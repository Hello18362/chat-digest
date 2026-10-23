"""Chat Digest · 基础设施层（配置、日志、统计）。

刻意不在 __init__ 里 import 子模块，避免 main / subcommand 之间的循环依赖：
调用方按需 `from core.config import load_settings` 即可。
"""

from __future__ import annotations

__all__ = ["config", "log", "stats"]
__version__ = "0.1.0"
