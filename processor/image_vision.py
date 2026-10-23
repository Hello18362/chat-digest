"""图片理解（抽象接口 + 火山方舟豆包 / OpenAI 兼容实现 + dummy 兜底）。

`VisionProvider` 是抽象基类，`DummyVisionProvider`（MVP 默认，零成本）和
`DoubaoVisionProvider`（真实调用）实现同一接口，通过 settings.yaml 的
`vision.provider` 切换，业务代码只依赖基类。
"""

from __future__ import annotations

import base64
import mimetypes
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Callable, Dict, Optional

import requests

from collector.models import TYPE_IMAGE, Message
from core.log import get_logger

logger = get_logger("image_vision")

# 视觉描述的系统提示词：约束输出风格，让它能直接塞进聊天流
VISION_SYSTEM_PROMPT = (
    "你是群聊助手。用一句话客观描述这张图片的内容，40 字以内。"
    "如果是聊天截图或报表截图，说明它记录和展示的主题；"
    "如果是文档/表格，说清核心数据；不要臆测未在图中出现的信息，不要写“这是一张图片”这种废话。"
)


class VisionProvider(ABC):
    """图片理解服务抽象基类。"""

    #: 实现类可读名称，用于日志与 UI 展示
    name: str = "base"

    @abstractmethod
    def describe_image(self, image_path: str) -> str:
        """输入本地图片路径，返回一句自然语言描述。

        Args:
            image_path: 本地图片文件路径。

        Returns:
            描述文本；失败时返回空字符串。
        """

    # -------------------------------------------------------------- #
    # 通用辅助
    # -------------------------------------------------------------- #
    @property
    def available(self) -> bool:
        """服务是否可用（未配置密钥 / 未安装依赖时应返回 False）。"""
        return True

    def process_message(self, message: Message, fetcher: Callable[[Message], Optional[str]]) -> bool:
        """就地处理一条图片消息，把描述写回 message.content。

        Args:
            message: msg_type 为 image 的消息。
            fetcher: 把 message 换成可读取的本地图片路径。

        Returns:
            True 表示成功生成描述，False 表示降级。
        """
        if message.msg_type != TYPE_IMAGE:
            return False

        fallback = message.raw_content or "图片"
        image_path: Optional[str] = None
        try:
            image_path = fetcher(message)
        except Exception as exc:
            logger.warning("获取图片文件失败（msg=%s）：%s", message.msg_id, exc)

        if not image_path or not Path(image_path).exists():
            message.content = f"[图片：{fallback}]"
            message.note = "无可用图片文件"
            return False

        try:
            desc = self.describe_image(image_path)
        except Exception as exc:
            logger.warning("图片理解失败（%s）：%s", image_path, exc)
            desc = ""

        if desc:
            message.content = f"[图片：{desc}]"
            message.note = f"已理解（{self.name}）"
            return True

        message.content = f"[图片：{fallback}]"
        message.note = "图片理解失败或 provider 不可用"
        return False


class DummyVisionProvider(VisionProvider):
    """MVP 默认实现：返回占位符，不联网、不花钱、不报错。"""

    name = "dummy"

    def __init__(self, fallback_text: str = "图片") -> None:
        self.fallback_text = fallback_text

    def describe_image(self, image_path: str) -> str:
        """直接从文件名猜一个可读描述，完全不联网。"""
        stem = Path(image_path).stem
        hint = stem.replace("_", " ").strip()
        return f"{self.fallback_text}（{hint[:24]}）" if hint else self.fallback_text

    @property
    def available(self) -> bool:
        """永远可用：它的职责就是兜底。"""
        return True


class DoubaoVisionProvider(VisionProvider):
    """调用火山方舟 / OpenAI 兼容的视觉多模态接口。

    Args:
        api_key: API Key，建议从 .env 读取。
        base_url: 服务地址，如 `https://ark.cn-beijing.volces.com/api/v3`。
        model: 视觉模型接入点 ID。
        timeout: 单次请求超时秒数。
        system_prompt: 视觉描述提示词。
    """

    name = "doubao"

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://ark.cn-beijing.volces.com/api/v3",
        model: str = "doubao-vision-pro",
        timeout: int = 30,
        system_prompt: str = VISION_SYSTEM_PROMPT,
    ) -> None:
        self.api_key = api_key.strip()
        self.base_url = str(base_url).rstrip("/")
        self.model = model
        self.timeout = timeout
        self.system_prompt = system_prompt
        self._session = requests.Session()
        self._session.trust_env = False

    @property
    def available(self) -> bool:
        """有密钥才可用。"""
        return bool(self.api_key)

    def describe_image(self, image_path: str) -> str:
        """把图片转 base64 后调用多模态接口拿到描述。"""
        if not self.available:
            logger.warning("未配置 DOUBAO_API_KEY，视觉理解降级")
            return ""

        data_url = self._to_data_url(image_path)
        if not data_url:
            return ""

        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": self.system_prompt},
                        {"type": "image_url", "image_url": {"url": data_url}},
                    ],
                }
            ],
            "temperature": 0.2,
            "max_tokens": 300,
        }

        try:
            resp = self._session.post(
                f"{self.base_url}/chat/completions",
                json=payload,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                timeout=self.timeout,
            )
            if resp.status_code != 200:
                logger.warning("视觉接口返回 %s：%s", resp.status_code, resp.text[:200])
                return ""
            body = resp.json()
            choices = body.get("choices") or []
            if not choices:
                return ""
            return str(choices[0].get("message", {}).get("content", "")).strip()
        except requests.exceptions.RequestException as exc:
            logger.warning("视觉接口请求失败：%s", exc)
            return ""
        except ValueError:
            logger.warning("视觉接口返回非法 JSON")
            return ""

    @staticmethod
    def _to_data_url(image_path: str) -> str:
        """把本地图片编码为 base64 data URL。"""
        path = Path(image_path)
        try:
            raw = path.read_bytes()
        except OSError as exc:
            logger.warning("读取图片失败：%s", exc)
            return ""
        if not raw:
            return ""
        mime = mimetypes.guess_type(path.name)[0] or "image/jpeg"
        encoded = base64.b64encode(raw).decode("ascii")
        return f"data:{mime};base64,{encoded}"


def build_vision_from_config(cfg: Dict[str, Any]) -> VisionProvider:
    """按 settings.yaml 的 `vision.provider` 构造对应的 provider。"""
    vision_cfg = cfg.get("vision", {}) or {}
    env = cfg.get("_env", {}) or {}
    provider = str(vision_cfg.get("provider", "dummy")).lower()

    if provider in ("doubao", "openai", "api"):
        api_key = str(env.get("api_key") or "")
        base_url = str(env.get("base_url") or "https://ark.cn-beijing.volces.com/api/v3")
        model = str(vision_cfg.get("model") or env.get("vision_model") or "doubao-vision-pro")
        real = DoubaoVisionProvider(api_key=api_key, base_url=base_url, model=model)
        if real.available:
            logger.info("视觉 provider = doubao（model=%s）", model)
            return real
        logger.warning(
            "vision.provider=doubao 但没读到 DOUBAO_API_KEY，自动回落到 dummy。"
            "复制 .env.example 为 .env 并填入密钥即可启用。"
        )
    logger.info("视觉 provider = dummy")
    return DummyVisionProvider(fallback_text=str(vision_cfg.get("fallback_text", "图片")))
