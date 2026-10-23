"""在「启动」文件夹写入开机自启项（GBK 编码，兼容 cmd 中文路径）。

删除自启：直接删掉 %APPDATA%\\Microsoft\\Windows\\Start Menu\\Programs\\Startup\\WeChatWorkHub.bat
或运行本脚本加 --remove 参数。
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT = str(Path(__file__).resolve().parents[1])
STARTUP_DIR = (
    Path.home()
    / "AppData"
    / "Roaming"
    / "Microsoft"
    / "Windows"
    / "Start Menu"
    / "Programs"
    / "Startup"
)
TARGET = STARTUP_DIR / "WeChatWorkHub.bat"

BODY = f"""@echo off
chcp 936 >nul
rem auto-start Chat Digest background service at logon
cd /d "{PROJECT}"
call "\u540e\u53f0\u542f\u52a8.bat"
"""


def main(argv: list[str]) -> int:
    """写入或删除自启项。"""
    if "--remove" in argv:
        if TARGET.exists():
            TARGET.unlink()
            print("已删除开机自启：", TARGET)
        else:
            print("没有找到自启项，无需删除")
        return 0

    if not STARTUP_DIR.exists():
        print("找不到「启动」文件夹：", STARTUP_DIR)
        return 1
    if not Path(PROJECT).exists():
        print("项目目录不存在：", PROJECT)
        return 1

    TARGET.write_text(BODY, encoding="gbk")
    print("已写入开机自启：", TARGET)
    print("内容：")
    print(TARGET.read_text(encoding="gbk"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
