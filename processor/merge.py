"""把加工好的消息列表合并成一段给 LLM 看的文本流。

合并结果同时被三处复用：
    1. summarizer 的输入（prompt 里的 {chat_content}）
    2. Markdown / HTML 报告的「聊天记录」区块
    3. file_output 落盘的待处理文件
"""

from __future__ import annotations

from typing import Iterable, List, Optional

from collector.models import TYPE_SYSTEM, Message

LEADER_TAG = "[领导]"


def merge_messages(
    messages: Iterable[Message],
    include_system: bool = False,
    show_leader_tag: bool = True,
) -> str:
    """把消息列表渲染成带时间戳的对话文本。

    Args:
        messages: 已加工、已排序的消息列表。
        include_system: 是否保留系统消息（撤回提示、群公告等）。
        show_leader_tag: 是否在领导名字后追加 `[领导]` 标记。

    Returns:
        一段纯文本；空列表返回 `（本次没有采集到任何消息）`。

    Example:
        [2026-10-22 14:30] 张总 [领导]:
        明天下午3点开会，大家把Q3数据准备一下。
    """
    blocks: List[str] = []
    for msg in messages:
        if msg.msg_type == TYPE_SYSTEM and not include_system:
            continue
        blocks.append(_render_one(msg, show_leader_tag))

    if not blocks:
        return "（本次没有采集到任何消息）"

    return "\n\n".join(blocks)


def _render_one(msg: Message, show_leader_tag: bool) -> str:
    """渲染单条消息。"""
    tag = f" {LEADER_TAG}" if (msg.is_leader and show_leader_tag) else ""
    header = f"[{msg.full_time_text}] {msg.sender_name}{tag}:"

    content = (msg.content or "").strip()
    if not content:
        content = {
            "voice": "[语音：未能转写]",
            "image": "[图片：未能识别]",
            "article": "[链接：未能解析]",
        }.get(msg.msg_type, "[无文字内容]")

    return f"{header}\n{content}"


def filter_leaders_only(messages: Iterable[Message]) -> List[Message]:
    """只保留领导发言（用于 `--leaders-only` 与报告的「只看重点」视图）。"""
    return [m for m in messages if m.is_leader]


def build_chat_content(
    messages: Iterable[Message],
    leaders_only: bool = False,
    max_chars: Optional[int] = None,
) -> str:
    """生成喂给 LLM 的最终聊天内容。

    Args:
        messages: 全部消息。
        leaders_only: 为 True 时只保留领导发言。
        max_chars: 超过则从头截断（保留最新的内容，丢弃最早的）。

    Returns:
        合并后的文本；若命中截断会在开头插入提示行。
    """
    items = list(messages)
    if leaders_only:
        items = filter_leaders_only(items)

    text = merge_messages(items)
    if max_chars and len(text) > max_chars:
        dropped_note = (
            "（注意：聊天内容过长，已按配置截断最早的一部分，\n"
            f"原始长度 {len(text)} 字，本次输入 {max_chars} 字。）\n\n"
        )
        text = dropped_note + text[-max_chars:]
    return text
