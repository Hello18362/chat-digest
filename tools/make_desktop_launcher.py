"""把 启动.bat 放到桌面（GBK 编码，兼容 cmd 中文路径）。

桌面这份是自包含的：自己 cd 到项目目录，再调用项目里的 启动.bat，
所以无论从桌面双击还是在项目里双击，行为完全一致。
"""

from __future__ import annotations

from pathlib import Path

PROJECT = str(Path(__file__).resolve().parents[1])
DESKTOP_DIR = Path.home() / "Desktop"
TARGET = DESKTOP_DIR / "启动.bat"
OLD_LAUNCHER = DESKTOP_DIR / "微信群工作报告.bat"

BODY = f"""@echo off
chcp 936 >nul
cd /d "{PROJECT}"
call "\u542f\u52a8.bat"
"""


def main() -> int:
    """写出桌面启动脚本，并清理旧的命名版本。"""
    if not Path(PROJECT).exists():
        print("项目目录不存在：", PROJECT)
        return 1

    TARGET.write_text(BODY, encoding="gbk")
    print("已放到桌面：", TARGET)

    if OLD_LAUNCHER.exists():
        OLD_LAUNCHER.unlink()
        print("已删除旧的桌面启动文件：", OLD_LAUNCHER.name)

    print("\n内容：")
    print(TARGET.read_text(encoding="gbk"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
