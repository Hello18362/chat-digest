"""Markdown 报告输出。

产物样例：`runs/2026-10-22_XX项目工作群.md`

结构为「元信息 -> 总结 -> 聊天记录」，元信息里包含验收标准要求的
群名 / 日期 / 消息总数 / 领导发言数。
"""

from __future__ import annotations

import re
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from collector.models import Message
from core.log import get_logger
from processor.merge import merge_messages

logger = get_logger("output_markdown")

_ILLEGAL_RE = re.compile(r'[\\/:*?"<>|\r\n\t]')

#: 总结正文里若自带了顶层标题（`## 总结` / `# 总结`），外层就不该再补一个，
#: 否则会出现连续两个同名标题 —— file_output 后端返回的正文就自带 `## 总结`。
_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s*(总结|摘要|小结|今日总结)\s*[:：]?\s*$")


def strip_summary_heading(text: str) -> str:
    """去掉总结正文开头的「总结」标题行，避免渲染出两个重复标题。

    Args:
        text: 总结区块的 Markdown 原文。

    Returns:
        已剥离首行标题的文本；有多个时也一并剥掉，且不影响正文中后出现的标题。
    """
    lines = (text or "").splitlines()
    index = 0
    while index < len(lines) and not lines[index].strip():
        index += 1
    while index < len(lines) and _HEADING_RE.match(lines[index]):
        index += 1
        # 标题后紧跟的空行一并吃干净
        while index < len(lines) and not lines[index].strip():
            index += 1
    if index == 0:
        return text or ""
    return "\n".join(lines[index:])


def safe_filename(name: str, max_len: int = 60) -> str:
    """把群名清洗成可安全用作文件名的字符串。

    Args:
        name: 原始群名。
        max_len: 最大长度（按字符计，避免中文被截断成乱码）。

    Returns:
        清洗后的名字；全被清洗掉时返回 `未命名群`。
    """
    cleaned = unicodedata.normalize("NFKC", name or "")
    cleaned = _ILLEGAL_RE.sub("_", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ._")
    if not cleaned:
        cleaned = "未命名群"
    return cleaned[:max_len]


def build_markdown(
    summary_text: str,
    messages: Iterable[Message],
    group_name: str,
    date: str,
    stats: Optional[Dict[str, Any]] = None,
    include_meta: bool = True,
    include_chat: bool = True,
) -> str:
    """拼装完整 Markdown 报告文本。

    Args:
        summary_text: 总结区块内容（Markdown）。
        messages: 已加工的消息列表。
        group_name: 群名。
        date: 日期字符串 `YYYY-MM-DD`。
        stats: core.stats.summarize_messages 的输出。
        include_meta: 是否在文件头写入元信息。
        include_chat: 是否附带聊天记录全文。

    Returns:
        完整 Markdown 文本。
    """
    stats = stats or {}
    parts: List[str] = []

    parts.append(f"# {group_name} · {date} 工作简报")
    parts.append("")

    if include_meta:
        counts = stats.get("counts", {}) or {}
        parts += [
            "> 由 Chat Digest 本地生成 · 数据未出本机",
            ">",
            f"> **群聊**：{group_name}",
            f"> **日期**：{date}",
            f"> **消息总数**：{stats.get('total', 0)}",
            f"> **领导发言数**：{stats.get('leader_count', 0)}",
            ">",
            "> 分布："
            + " / ".join(
                f"{label} {counts.get(key, 0)}"
                for key, label in (
                    ("text", "文字"),
                    ("voice", "语音"),
                    ("image", "图片"),
                    ("article", "链接"),
                )
            ),
            "",
        ]

    parts.append("## 总结")
    parts.append("")
    body = strip_summary_heading(summary_text or "")
    parts.append(body.strip() or "_本次没有生成总结。_")
    parts.append("")

    if include_chat:
        parts.append("## 聊天记录")
        parts.append("")
        parts.append(merge_messages(messages))
        parts.append("")

    return "\n".join(parts)


def write_markdown(
    summary_text: str,
    messages: Iterable[Message],
    group_name: str,
    date: str,
    output_dir: Path | str = "./runs",
    stats: Optional[Dict[str, Any]] = None,
    include_meta: bool = True,
    include_chat: bool = True,
    suffix: str = "",
) -> Path:
    """写入 Markdown 报告并返回文件路径。

    Args:
        summary_text: 总结区块内容。
        messages: 消息列表。
        group_name: 群名。
        date: 日期字符串。
        output_dir: 输出目录，不存在时自动创建。
        stats: 统计信息。
        include_meta: 是否写入元信息。
        include_chat: 是否附带聊天记录。
        suffix: 文件名后缀（用于区分同一天的多次运行）。

    Returns:
        写入成功的 Path。

    Raises:
        OSError: 目录不可写时抛出（属于致命错误，不应静默吞掉）。
    """
    directory = Path(str(output_dir)).expanduser()
    directory.mkdir(parents=True, exist_ok=True)

    content = build_markdown(
        summary_text,
        messages,
        group_name,
        date,
        stats=stats,
        include_meta=include_meta,
        include_chat=include_chat,
    )

    stamp = datetime.now().strftime("%H%M%S") if suffix == "auto" else suffix
    filename = f"{date}_{safe_filename(group_name)}{('_' + stamp) if stamp else ''}.md"
    target = directory / filename
    target.write_text(content, encoding="utf-8")
    logger.info("Markdown 报告已写入：%s", target)
    return target
