"""总结模型的抽象接口。

MVP 阶段默认走 `FileOutputSummarizer`（不联网、不花钱，产出待处理文件交给外部 CLI），
后续可在 `summarizer.doubao_api` 里直接切到在线模型，业务代码不需要改动。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict, Optional

DEFAULT_TEMPLATE = """你是我的工作助手。请阅读以下微信群聊记录，提炼出明确的工作任务清单。

群聊记录：
{chat_content}
"""


class _SafeDict(dict):
    """format_map 用的缺省映射：缺失的键返回空串，不抛 KeyError。"""

    def __missing__(self, key: str) -> str:
        return ""


class Summarizer(ABC):
    """总结器基类。

    子类只需要实现 `summarize()`；提示词渲染是共享能力，
    定义在基类里以保证「同一份模板、不同后端」输出一致。
    """

    #: 实现类名称，会写进报告元信息与日志
    name: str = "base"

    def __init__(self) -> None:
        self.last_output_path: Optional[Path] = None
        self.last_meta: Dict[str, Any] = {}

    # -------------------------------------------------------------- #
    # 子类必须实现
    # -------------------------------------------------------------- #
    @abstractmethod
    def summarize(self, chat_content: str, prompt_template: str) -> str:
        """输入聊天内容 + Prompt 模板，返回总结结果。

        Args:
            chat_content: 已合并的聊天文本流。
            prompt_template: 模板原始内容（含 `{chat_content}` 等占位符）。

        Returns:
            总结结果文本（Markdown）。
        """

    # -------------------------------------------------------------- #
    # 共享能力
    # -------------------------------------------------------------- #
    def render_prompt(self, prompt_template: str, **variables: Any) -> str:
        """把模板里的 `{变量}` 占位符替换成实际内容。

        模板里未提供的变量会被替换为空串（而非抛 KeyError），
        这样即使模板升级了、代码还没跟上，也不会崩溃。

        Args:
            prompt_template: 模板文本。
            **variables: 要注入的变量，如 chat_content / leaders_list。

        Returns:
            渲染后的完整提示词。
        """
        if not prompt_template:
            prompt_template = DEFAULT_TEMPLATE
        # 用 str.format_map 做替换，模板里的占位符是 {chat_content} 这种花括号形式。
        # 用一个「缺省返回空串」的映射兜底，未提供的变量不会导致 KeyError。
        return prompt_template.format_map(_SafeDict(**variables))

    @property
    def available(self) -> bool:
        """后端是否可用（缺密钥时返回 False，由工厂决定回落策略）。"""
        return True

    def describe(self) -> str:
        """一行描述，用于日志与 UI 展示。"""
        return f"总结后端：{self.name}"
