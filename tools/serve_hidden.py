"""无窗口后台服务入口。

用 pythonw.exe 启动时没有控制台，`sys.stdout` / `sys.stderr` 是 None，
`print()` 会抛 AttributeError 直接把服务打死。这里先把输出重定向到
`logs/console.out`，再启动控制台，从而支持「关掉窗口也在跑」。

用法（由 后台启动.bat 调用）：
    pythonw.exe tools\\serve_hidden.py
"""

from __future__ import annotations

import os
import sys
import traceback
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG_PATH = ROOT / "logs" / "console.out"


def _open_log():
    """打开日志流；失败时退回 devnull，保证进程不因日志挂掉。"""
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        return open(LOG_PATH, "a", encoding="utf-8", buffering=1)
    except OSError:
        return open(os.devnull, "w", encoding="utf-8")


def main() -> int:
    """启动后台服务，返回退出码。"""
    stream = _open_log()
    sys.stdout = stream
    sys.stderr = stream

    # 固定工作目录：配置里的 ./runs、./logs 都是相对项目根解析的
    os.chdir(ROOT)
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))

    print(f"\n===== {datetime.now():%Y-%m-%d %H:%M:%S} 后台服务启动 =====")

    try:
        from core.config import ConfigError, load_settings

        try:
            cfg = load_settings()
        except ConfigError as exc:
            print(f"配置加载失败：{exc}")
            return 1

        # 后台模式不自动开浏览器，由启动脚本自己打开
        cfg.setdefault("web", {})["open_browser"] = False

        from web.server import run_server

        return run_server(cfg)
    except Exception:  # 兜底：任何异常都要落盘，否则无窗口下无从排查
        print("后台服务异常退出：")
        traceback.print_exc()
        return 1
    finally:
        try:
            stream.flush()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
