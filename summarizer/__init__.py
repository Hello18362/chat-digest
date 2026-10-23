"""总结层导出与工厂。"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from core.config import resolve_path
from core.log import get_logger
from summarizer.base import Summarizer
from summarizer.doubao_api import DoubaoSummarizer
from summarizer.file_output import FileOutputSummarizer

logger = get_logger("summarizer_factory")

__all__ = ["Summarizer", "FileOutputSummarizer", "DoubaoSummarizer", "build_summarizer"]


def build_summarizer(cfg: Dict[str, Any]) -> Summarizer:
    """按 settings.yaml 的 `summarizer.provider` 构造总结器。

    Args:
        cfg: 完整配置字典。

    Returns:
        可用的 Summarizer 实例；配置不可用时会安全地回落到 file_output。

    Raises:
        ValueError: provider 名无法识别时抛出（属于配置错误，应该被用户看到）。
    """
    s_cfg = cfg.get("summarizer", {}) or {}
    env = cfg.get("_env", {}) or {}
    provider = str(s_cfg.get("provider", "file_output")).lower()

    # `doubao` / `openai` 都走同一套 OpenAI 兼容客户端（DoubaoSummarizer）。
    # 这是刻意的：豆包、DeepSeek、通义、Kimi 等都提供 `POST /chat/completions`，
    # 差异只在 base_url / model，通过 .env 与 settings.yaml 注入即可，无需改代码。
    if provider in ("doubao", "openai", "deepseek"):
        api_key = str(env.get("api_key") or "")
        base_url = str(env.get("base_url") or "https://api.deepseek.com")
        model = str(s_cfg.get("model") or env.get("text_model") or "deepseek-chat")
        candidate = DoubaoSummarizer(
            api_key=api_key,
            base_url=base_url.rstrip("/"),
            model=model,
        )
        if candidate.available:
            logger.info("总结 provider = %s（model=%s base=%s）", provider, candidate.model, candidate.base_url)
            return candidate
        # 无 Key 时安全回落到 file_output，不要继续往下走到 raise
        logger.warning(
            "summarizer.provider=%s 但未检测到 API Key（.env 里的 DEEPSEEK_API_KEY / DOUBAO_API_KEY / OPENAI_API_KEY），自动回落到 file_output。",
            provider,
        )
        return FileOutputSummarizer(
            pending_dir=resolve_path(s_cfg.get("pending_dir", "./runs/pending"), mkdir=True)
        )

    if provider not in ("file_output", "local", "none"):
        raise ValueError(
            f"未知的 summarizer.provider：{provider!r}（可选：file_output / doubao / deepseek / openai）"
        )

    pending_dir = resolve_path(s_cfg.get("pending_dir", "./runs/pending"), mkdir=True)
    logger.info("总结 provider = file_output（待处理目录=%s）", pending_dir)
    return FileOutputSummarizer(pending_dir=pending_dir)
