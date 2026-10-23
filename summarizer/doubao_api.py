"""豆包 / OpenAI 兼容 API 直连总结（预留，用于自动化阶段）。

默认走火山方舟的 OpenAI 兼容接口：

    POST {base_url}/chat/completions
    Authorization: Bearer ${DOUBAO_API_KEY}

没有密钥时 `available` 返回 False，工厂会自动回落到 file_output，不会报错。
"""

from __future__ import annotations

from typing import Any, Dict

import requests

from core.log import get_logger
from summarizer.base import Summarizer

logger = get_logger("doubao_api")


class DoubaoSummarizer(Summarizer):
    """调用在线大模型完成总结。

    Args:
        api_key: API Key（来自 .env）。
        base_url: 服务地址，形如 `https://ark.cn-beijing.volces.com/api/v3`。
        model: 模型接入点 ID 或模型名。
        timeout: 单次请求超时秒数（总结通常较慢，默认给到 120s）。
        temperature: 采样温度，总结任务建议压低以保证稳定。
        max_tokens: 单次最大输出 token。
    """

    name = "doubao"

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://ark.cn-beijing.volces.com/api/v3",
        model: str = "doubao-seed-1904",
        timeout: int = 120,
        temperature: float = 0.3,
        max_tokens: int = 3000,
    ) -> None:
        super().__init__()
        self.api_key = (api_key or "").strip()
        self.base_url = str(base_url).rstrip("/")
        self.model = model
        self.timeout = timeout
        self.temperature = temperature
        self.max_tokens = max_tokens
        self._session = requests.Session()
        self._session.trust_env = False

    @property
    def available(self) -> bool:
        """有密钥才算可用。"""
        return bool(self.api_key)

    def summarize(self, chat_content: str, prompt_template: str) -> str:
        """调用模型生成总结。

        Args:
            chat_content: 合并后的聊天文本。
            prompt_template: prompt 模板原文。

        Returns:
            模型返回的 Markdown 总结；失败时返回带错误说明的 Markdown。
        """
        if not self.available:
            self.last_meta["error"] = "missing_api_key"
            return self._error_block("未配置 DOUBAO_API_KEY，已跳过在线总结。")

        prompt = self.render_prompt(
            prompt_template,
            chat_content=chat_content,
            leaders_list=self.last_meta.get("leaders_text", "（未指定）"),
            group_name=self.last_meta.get("group_name", "未命名群"),
            date=self.last_meta.get("date", ""),
        )

        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": "你是严谨的工作助手，只依据给定聊天记录作答，不臆造信息。",
                },
                {"role": "user", "content": prompt},
            ],
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }

        try:
            logger.info("调用在线总结模型：%s", self.model)
            resp = self._session.post(
                f"{self.base_url}/chat/completions",
                json=payload,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                timeout=self.timeout,
            )
        except requests.exceptions.RequestException as exc:
            logger.error("在线总结请求失败：%s", exc)
            self.last_meta["error"] = "request_failed"
            return self._error_block(f"在线总结请求失败：{exc}")

        if resp.status_code != 200:
            logger.error("在线总结返回 %s：%s", resp.status_code, resp.text[:300])
            self.last_meta["error"] = f"http_{resp.status_code}"
            return self._error_block(f"在线总结失败（HTTP {resp.status_code}）：{resp.text[:200]}")

        try:
            body = resp.json()
            choices = body.get("choices") or []
            message = choices[0].get("message", {}) if choices else {}
            content = str(message.get("content") or "").strip()
            # 容错：部分模型（如开启思维链的 deepseek-flash）会把回答放在
            # reasoning_content，而 content 为空。此时降级用 reasoning_content，
            # 避免「模型返回空内容」的误判。
            if not content:
                content = str(message.get("reasoning_content") or "").strip()
            usage = body.get("usage") or {}
            self.last_meta["tokens"] = usage
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            logger.error("在线总结响应解析失败：%s", exc)
            self.last_meta["error"] = "bad_response"
            return self._error_block("在线总结响应解析失败，请查看日志。")

        if not content:
            self.last_meta["error"] = "empty_response"
            return self._error_block("模型返回了空内容。")

        logger.info("在线总结完成（%d 字）", len(content))
        return content

    @staticmethod
    def _error_block(reason: str) -> str:
        """统一的失败占位区块，保证报告结构不被破坏。"""
        return (
            "## 总结\n\n"
            f"> ⚠️ {reason}\n>\n"
            "> 聊天记录已完整保留在下方，可改用 `file_output` 模式离线处理。"
        )
