"""极简 Markdown -> HTML 渲染器。

只覆盖「AI 生成总结」会用到的语法子集，目的是完全掌控输出标签结构，
好让样式精准落在「任务行」「表格」「代码块」这些语义单元上：

    # 标题          -> <h1>~<h4>
    ☐ / ☑ 任务行     -> <li class="task">（按 “ — ” 拆成 事项/负责人/截止）
    - 列表          -> <ul><li>
    1. 列表         -> <ol><li>
    > 引用          -> <blockquote>
    | 表格 |        -> <table>
    ``` 代码块       -> <pre><code>
    空行隔开的段落   -> <p>

刻意不引入 markdown 库：一是少一个依赖，二是标准库渲染出的标签不可控，
做不出带负责人/截止时间分列的任务卡。
"""

from __future__ import annotations

import html
import re
from typing import Dict, List, Tuple

_TASK_RE = re.compile(r"^(☐|☑|□|✅|-\s*\[[ xX]\])\s*(.*)$")
_TASK_SPLIT = re.compile(r"\s+—\s+|\s+--\s+")
_PRIORITY_RE = re.compile(r"^\[(紧急|重要|常规|高|中|低| P[0-3])\]\s*")


def escape(text: str) -> str:
    """HTML 转义，防 XSS（总结内容来自模型或网页，不可信）。"""
    return html.escape(text or "", quote=False)


def inline(text: str) -> str:
    """渲染行内语法：粗体 / 斜体 / 行内代码 / 链接。"""
    text = escape(text)
    # 链接：必须在转义之后处理，因为 []() 不受 escape 影响
    text = re.sub(
        r"\[([^\]]{1,120})\]\((https?://[^\s)]{1,300}|#[^\s)]{0,80})\)",
        r'<a href="\2" target="_blank" rel="noopener">\1</a>',
        text,
    )
    text = re.sub(r"`([^`\n]{1,300})`", r"<code>\1</code>", text)
    text = re.sub(r"\*\*([^*\n]{1,300})\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"(?<!\*)\*([^*\n]{1,300})\*(?!\*)", r"<em>\1</em>", text)
    return text


def _parse_table_row(row: str) -> List[str]:
    """拆分一行 Markdown 表格。"""
    return [c.strip() for c in row.strip().strip("|").split("|")]


def _is_table_separator(line: str) -> bool:
    """判断是否为 `|---|---|` 分隔行。"""
    return bool(re.match(r"^\s*\|?[\s:|-]+\|[\s:|-]*$", line)) and "-" in line


def _render_task(body: str, done: bool) -> str:
    """渲染 `☐ [紧急] 事项 — 负责人 — 截止时间` 形式的任务行。"""
    checked = " checked" if done else ""
    priority = ""
    match = _PRIORITY_RE.match(body)
    if match:
        priority = match.group(1).strip()
        body = body[match.end() :]

    parts = [p.strip() for p in _TASK_SPLIT.split(body) if p.strip()]
    title = inline(parts[0]) if parts else ""
    owner = inline(parts[1]) if len(parts) > 1 else ""
    due = inline(parts[2]) if len(parts) > 2 else (inline(parts[1]) if owner == "" and len(parts) > 1 else "")

    chips = ""
    if priority:
        chips += f'<span class="chip chip-pri" data-pri="{escape(priority)}">{escape(priority)}</span>'
    if owner:
        chips += f'<span class="chip chip-owner">{owner}</span>'
    if due and due != owner:
        chips += f'<span class="chip chip-due">{due}</span>'

    return (
        f'<li class="task{" is-done" if done else ""}"{checked}>'
        f'<span class="task-box" aria-hidden="true"></span>'
        f'<span class="task-title">{title}</span>'
        f'<span class="task-meta">{chips}</span>'
        "</li>"
    )


def parse_task_line(body: str, done: bool = False) -> Dict[str, str]:
    """把一条任务行的正文解析成结构化字段。

    支持的格式（用「 — 」或「 -- 」分隔，列数可变）：
        [优先级] 事项 — 负责人 — 截止时间 — 来源(发言人 HH:MM)
        [优先级] 事项 — 负责人 — 截止时间
        事项 — 负责人

    Returns:
        `{title, priority, owner, due, source, done}`，缺失字段为空串。
    """
    priority = ""
    match = _PRIORITY_RE.match(body)
    if match:
        priority = match.group(1).strip()
        body = body[match.end() :]

    parts = [p.strip() for p in _TASK_SPLIT.split(body) if p.strip()]
    title = parts[0] if parts else ""
    owner = parts[1] if len(parts) > 1 else ""
    due = parts[2] if len(parts) > 2 else ""
    source = parts[3] if len(parts) > 3 else ""

    # 来源常写成「来源(张三 14:00)」，把包裹去掉只留可读文本
    if source:
        source = re.sub(r"^来源[（(]\s*", "", source)
        source = re.sub(r"\s*[)）]$", "", source)

    return {
        "title": title,
        "priority": priority,
        "owner": owner,
        "due": due,
        "source": source,
        "done": done,
    }


def extract_task_cards(markdown_text: str) -> List[Dict[str, str]]:
    """从总结 Markdown 里抽取所有任务行，解析成结构化对象列表。

    Args:
        markdown_text: 总结 Markdown 原文。

    Returns:
        `[{"title": ..., "priority": ..., "owner": ..., "due": ..., "source": ..., "done": ...}]`
    """
    result: List[Dict[str, str]] = []
    for line in (markdown_text or "").splitlines():
        task = _TASK_RE.match(line.strip())
        if not task:
            continue
        done = task.group(1) in ("☑", "✅") or bool(re.match(r"-\s*\[[xX]\]", task.group(1)))
        result.append(parse_task_line(task.group(2).strip(), done))
    return result


def md_to_html(markdown_text: str, skip_tasks: bool = False) -> str:
    """把 Markdown 文本渲染成 HTML 片段。

    Args:
        markdown_text: 原始 Markdown。
        skip_tasks: 为 True 时跳过 `☐` 任务行（用于卡片网格已单独承载任务、避免重复）。

    Returns:
        HTML 字符串（不含外层容器，可直接塞进 <article>）。
    """
    if not markdown_text or not markdown_text.strip():
        return '<p class="empty">本次没有可展示的总结内容。</p>'

    lines = markdown_text.replace("\r\n", "\n").split("\n")
    out: List[str] = []
    index = 0
    total = len(lines)
    in_list: str = ""
    in_quote: bool = False
    paragraph: List[str] = []

    def _close_list() -> None:
        nonlocal in_list
        if in_list:
            out.append(f"</{in_list}>")
            in_list = ""

    def _close_quote() -> None:
        nonlocal in_quote
        if in_quote:
            out.append("</blockquote>")
            in_quote = False

    def _flush_paragraph() -> None:
        nonlocal paragraph
        if paragraph:
            out.append(f"<p>{inline(' '.join(paragraph))}</p>")
            paragraph = []

    while index < total:
        line = lines[index].rstrip()
        stripped = line.strip()

        # ---- 代码块 ---- #
        if stripped.startswith("```"):
            _close_list()
            _close_quote()
            _flush_paragraph()
            lang = stripped[3:].strip()[:20]
            buffer: List[str] = []
            index += 1
            while index < total and not lines[index].strip().startswith("```"):
                buffer.append(lines[index])
                index += 1
            index += 1
            out.append(
                f'<pre class="code" data-lang="{escape(lang)}"><code>{escape(chr(10).join(buffer))}</code></pre>'
            )
            continue

        # ---- 空行：结束块级上下文 ---- #
        if not stripped:
            _close_list()
            _close_quote()
            _flush_paragraph()
            index += 1
            continue

        # ---- 标题 ---- #
        heading = re.match(r"^(#{1,4})\s+(.*)$", stripped)
        if heading:
            _close_list()
            _close_quote()
            _flush_paragraph()
            level = len(heading.group(1)) + 1  # 报告里 # 已经是文档标题，降一级
            level = min(level, 6)
            out.append(f'<h{level} class="md-h">{inline(heading.group(2))}</h{level}>')
            index += 1
            continue

        # ---- 分隔线 ---- #
        if re.match(r"^(-{3,}|\*{3,}|_{3,})$", stripped):
            _close_list()
            _close_quote()
            _flush_paragraph()
            out.append('<hr class="md-hr">')
            index += 1
            continue

        # ---- 引用 ---- #
        if stripped.startswith(">"):
            _close_list()
            _flush_paragraph()
            content = stripped.lstrip(">").strip()
            if not in_quote:
                out.append('<blockquote class="md-quote">')
                in_quote = True
            out.append(f"<p>{inline(content)}</p>")
            index += 1
            continue

        # ---- 表格 ---- #
        if stripped.startswith("|") and index + 1 < total and _is_table_separator(lines[index + 1]):
            _close_list()
            _close_quote()
            _flush_paragraph()
            header = _parse_table_row(stripped)
            index += 2
            rows: List[List[str]] = []
            while index < total and lines[index].strip().startswith("|"):
                rows.append(_parse_table_row(lines[index]))
                index += 1
            thead = "".join(f"<th>{inline(c)}</th>" for c in header)
            tbody = "".join(
                "<tr>" + "".join(f"<td>{inline(c)}</td>" for c in row) + "</tr>" for row in rows
            )
            out.append(
                f'<div class="table-wrap"><table class="md-table"><thead><tr>{thead}</tr></thead>'
                f"<tbody>{tbody}</tbody></table></div>"
            )
            continue

        # ---- 任务行 ---- #
        task = _TASK_RE.match(stripped)
        if task:
            if skip_tasks:
                index += 1
                continue
            _close_quote()
            _flush_paragraph()
            done = task.group(1) in ("☑", "✅") or re.match(r"-\s*\[[xX]\]", task.group(1))
            if in_list != "ul":
                _close_list()
                out.append('<ul class="task-list">')
                in_list = "ul"
            out.append(_render_task(task.group(2), done))
            index += 1
            continue

        # ---- 列表 ---- #
        bullet = re.match(r"^[-*+]\s+(.*)$", stripped)
        ordered = re.match(r"^\d+[.)]\s+(.*)$", stripped)
        if bullet or ordered:
            _close_quote()
            _flush_paragraph()
            want = "ul" if bullet else "ol"
            body = (bullet or ordered).group(1)
            if in_list != want:
                _close_list()
                out.append(f'<{want} class="md-list">')
                in_list = want
            out.append(f"<li>{inline(body)}</li>")
            index += 1
            continue

        # ---- 普通段落（可能多行） ---- #
        paragraph.append(stripped)
        index += 1

    _close_list()
    _close_quote()
    _flush_paragraph()
    return "\n".join(out)


def extract_tasks(markdown_text: str) -> List[Tuple[str, bool]]:
    """抽取出总结里的任务行，用于报告头部的「任务数」统计。

    Args:
        markdown_text: Markdown 原文。

    Returns:
        `[(任务原文, 是否已完成), ...]`
    """
    result: List[Tuple[str, bool]] = []
    for line in (markdown_text or "").splitlines():
        task = _TASK_RE.match(line.strip())
        if task:
            done = task.group(1) in ("☑", "✅")
            result.append((task.group(2).strip(), done))
    return result
