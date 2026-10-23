"""桌面软件入口。

双击运行（源码 `python app.py`）或打包后的 exe 会：
  1) 在本机 127.0.0.1 起控制台服务；
  2) 用原生窗口（PyWebView）打开界面，像软件一样使用；
  3) 若未安装 pywebview 或缺少 WebView2，自动降级为默认浏览器打开。

打包成 exe：双击 build_exe.bat，详见「桌面软件说明.md」。
"""
from __future__ import annotations

import os
import socket
import sys
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

# ---- 源码运行 / 打包运行都能 import 到包 ----
if getattr(sys, "frozen", False):
    BASE = Path(sys.executable).resolve().parent
else:
    BASE = Path(__file__).resolve().parent
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

from core.config import ConfigError, load_settings  # noqa: E402
from core.log import get_logger, setup_logging  # noqa: E402
from web.server import build  # noqa: E402

APP_TITLE = "微信群工作助手"
logger = get_logger("desktop")


def _ensure_std_streams() -> None:
    """保证 sys.stdout / sys.stderr 非 None。

    PyInstaller --windowed（pythonw）下这两个流是 None，任何 print 或
    logging.StreamHandler 都会 AttributeError 把进程打死。这里重定向到
    logs/console.out（拿不到就 devnull），必须在 setup_logging 之前调用。
    """
    if sys.stdout is not None and sys.stderr is not None:
        return
    try:
        base = (
            Path(sys.executable).resolve().parent
            if getattr(sys, "frozen", False)
            else Path(__file__).resolve().parent
        )
        log_dir = base / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        stream = open(log_dir / "console.out", "a", encoding="utf-8", buffering=1)
    except Exception:
        stream = open(os.devnull, "w", encoding="utf-8")
    if sys.stdout is None:
        sys.stdout = stream
    if sys.stderr is None:
        sys.stderr = stream


def _start_server(cfg):
    """起本地服务并等待端口可用，返回 (server, url)。"""
    server, url = build(cfg, host="127.0.0.1")
    threading.Thread(target=server.serve_forever, daemon=True).start()

    parsed = urlparse(url)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or 5710
    for _ in range(40):  # 最多等 4 秒
        try:
            with socket.create_connection((host, port), timeout=0.5):
                return server, url
        except OSError:
            time.sleep(0.1)
    return server, url


def _run_native_window(url: str) -> bool:
    """用 PyWebView 打开原生窗口；不可用时返回 False（交给浏览器降级）。"""
    try:
        import webview  # type: ignore
    except Exception as exc:
        logger.info("未安装 pywebview，将降级为浏览器：%s", exc)
        return False
    try:
        webview.create_window(
            APP_TITLE, url,
            width=1320, height=880, min_size=(980, 640),
            text_select=True,
        )
        webview.start()  # 阻塞，直到用户关闭窗口
        return True
    except Exception as exc:  # 例如缺少 WebView2 运行时
        logger.warning("原生窗口启动失败，降级为浏览器：%s", exc)
        return False


def _run_browser_fallback(url: str) -> None:
    import webbrowser
    webbrowser.open(url)
    print(f"[提示] 已用默认浏览器打开：{url}")
    print("       保持本窗口开启即可使用；关闭本窗口或按 Ctrl+C 结束。")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass


def main() -> int:
    _ensure_std_streams()
    try:
        cfg = load_settings()
    except ConfigError as exc:
        _fatal(f"配置加载失败：\n{exc}")
        return 1

    setup_logging(cfg)  # 日志写入 logs/hub.log（打包后无控制台也能查）

    server, url = _start_server(cfg)
    logger.info("控制台已就绪：%s", url)

    try:
        if not _run_native_window(url):
            _run_browser_fallback(url)
    finally:
        try:
            server.shutdown()
            server.server_close()
        except Exception:
            pass
    return 0


def _fatal(msg: str) -> None:
    """打包为 --windowed 后没有控制台，用系统弹窗提示致命错误。"""
    print(msg)
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, msg, APP_TITLE, 0x10)
    except Exception:
        pass


if __name__ == "__main__":
    raise SystemExit(main())
