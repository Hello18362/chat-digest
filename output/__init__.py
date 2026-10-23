"""输出层：Markdown / HTML 报告。"""

from __future__ import annotations

from output.html import render_report, write_html
from output.markdown import build_markdown, safe_filename, write_markdown
from output.md_lite import extract_tasks, md_to_html

__all__ = [
    "write_markdown",
    "build_markdown",
    "safe_filename",
    "write_html",
    "render_report",
    "md_to_html",
    "extract_tasks",
]
