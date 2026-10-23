"""本地可视化控制台。

用 Python 标准库 http.server 实现，不引入任何额外 Web 依赖：

    python main.py --serve
"""

from __future__ import annotations

from web.server import build, run_server

__all__ = ["run_server", "build"]
