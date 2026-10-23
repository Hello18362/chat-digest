"""日志基础设施：同时输出到控制台与 logs/ 目录。

控制台走彩色 + 分级简写，方便肉眼跟踪流水线；
文件走完整格式（时间 / 级别 / 模块 / 行号），方便事后复盘。
"""

from __future__ import annotations

import logging
import logging.handlers
import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional

# ANSI 颜色（Windows 终端 / macOS Terminal 均支持）
_LEVEL_COLOR: Dict[str, str] = {
    "DEBUG": "\033[90m",     # 灰
    "INFO": "\033[36m",      # 青
    "WARNING": "\033[33m",   # 黄
    "ERROR": "\033[31m",     # 红
    "CRITICAL": "\033[35m",  # 品红
}
_RESET = "\033[0m"
_DIM = "\033[2m"


def _supports_color() -> bool:
    """非 TTY（如重定向到文件）时自动关闭颜色，避免控制字符污染输出。"""
    try:
        return bool(sys.stdout) and sys.stdout.isatty()
    except Exception:  # pragma: no cover
        return False


class ConsoleFormatter(logging.Formatter):
    """控制台格式：`HH:MM:SS INFO  模块名  消息`。"""

    def __init__(self, color: bool = True) -> None:
        super().__init__()
        self.color = color

    def format(self, record: logging.LogRecord) -> str:
        level = record.levelname
        ts = self.formatTime(record, "%H:%M:%S")
        name = record.name.split(".")[-1][:12].ljust(12)
        msg = record.getMessage()
        if record.exc_info:  # 异常堆栈完整保留
            msg = f"{msg}\n{self.formatException(record.exc_info)}"
        if self.color:
            color = _LEVEL_COLOR.get(level, "")
            return f"{_DIM}{ts}{_RESET} {color}{level:<8}{_RESET} {_DIM}{name}{_RESET} {msg}"
        return f"{ts} {level:<8} {name} {msg}"


class FileFormatter(logging.Formatter):
    """文件格式：带日期、模块和行号，便于长期留存与排错。"""

    def __init__(self) -> None:
        super().__init__(
            fmt="%(asctime)s | %(levelname)-8s | %(name)-24s:%(lineno)d | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )


_HUB_LOGGER = "chatdigest"
_configured = False


def force_utf8_console() -> None:
    """把 stdout / stderr 切到 UTF-8，避免非中文代码页的 Windows 上中文输出直接崩。

    英文版 Windows 的控制台代码页默认是 cp1252 / cp437，`print("中文")` 会抛
    `UnicodeEncodeError` 并中断程序。切成 utf-8 且 `errors="replace"` 后，
    最坏情况只是显示成问号，不会让进程挂掉。
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except (AttributeError, ValueError, OSError):  # pragma: no cover - 老版本/特殊流
            pass


def setup_logging(cfg: Dict[str, Any]) -> logging.Logger:
    """依据配置初始化 hub logger（幂等，重复调用不会叠加 handler）。"""
    global _configured

    force_utf8_console()

    logger = logging.getLogger(_HUB_LOGGER)
    level = str(dig(cfg, "logging.level", "INFO")).upper()
    logger.setLevel(getattr(logging, level, logging.INFO))
    logger.propagate = False

    if _configured:
        return logger

    log_dir = Path(str(dig(cfg, "logging.dir", "./logs"))).expanduser()
    if not log_dir.is_absolute():
        # 打包成 exe 后日志也要落在 exe 旁边，而不是 _internal 里；
        # 这里不复用 core.config.PROJECT_ROOT，避免 config <-> log 循环导入。
        base = (
            Path(sys.executable).resolve().parent
            if getattr(sys, "frozen", False)
            else Path(__file__).resolve().parents[1]
        )
        log_dir = base / log_dir
    log_dir.mkdir(parents=True, exist_ok=True)

    file_name = str(dig(cfg, "logging.file", "hub.log"))
    max_bytes = int(dig(cfg, "logging.max_bytes", 5242880) or 5242880)
    backup = int(dig(cfg, "logging.backup_count", 5) or 5)

    # 控制台。注意：PyInstaller --windowed（pythonw）下 sys.stdout / sys.stderr
    # 可能为 None，直接传给 StreamHandler 会在 emit 时 AttributeError，这里兜底到 devnull。
    _stream = sys.stdout or sys.stderr
    if _stream is None:
        _stream = open(os.devnull, "w", encoding="utf-8")
    console = logging.StreamHandler(_stream)
    console.setFormatter(ConsoleFormatter(color=_supports_color()))
    console.setLevel(logger.level)
    logger.addHandler(console)

    # 文件（滚动）
    try:
        file_handler = logging.handlers.RotatingFileHandler(
            log_dir / file_name,
            maxBytes=max_bytes,
            backupCount=backup,
            encoding="utf-8",
        )
        file_handler.setFormatter(FileFormatter())
        file_handler.setLevel(logger.level)
        logger.addHandler(file_handler)
    except OSError as exc:  # 磁盘不可写不应拖垮主流程
        logger.warning("日志文件无法创建（%s），仅输出到控制台", exc)

    _configured = True
    return logger


def get_logger(module: Optional[str] = None) -> logging.Logger:
    """获取模块级 logger：`get_logger(__name__)`。"""
    return logging.getLogger(_HUB_LOGGER).getChild(module or "root")


def dig(data: Any, dotted: str, default: Any = None) -> Any:
    """本地轻量取值，避免 core.log 反向依赖 core.config 造成循环导入。"""
    node: Any = data
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    return node if node is not None else default
