#!/usr/bin/env python3
"""Chat Digest 命令行入口。

典型用法::

    python main.py --group "XX项目工作群"
    python main.py --group "XX项目工作群" --date today --format both --open
    python main.py --list-groups                  # 看看 chatlog 里都有哪些群
    python main.py --serve                        # 启动本地可视化控制台
    python main.py --group "XX项目工作群" --dry-run  # 只跑链路不落盘
"""

from __future__ import annotations

import argparse
import sys
import unicodedata
import webbrowser
from pathlib import Path
from typing import List, Optional

# 让 `python main.py` 与 `python -m main` 都能正常 import 同目录包
sys.path.insert(0, str(Path(__file__).resolve().parent))

from core.config import ConfigError, load_groups, load_settings  # noqa: E402
from core.log import force_utf8_console, get_logger, setup_logging  # noqa: E402
from core.pipeline import PipelineError, RunOptions, run_pipeline  # noqa: E402
from core.stats import format_duration  # noqa: E402

DIM = "\033[2m"
BOLD = "\033[1m"
CYAN = "\033[36m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
RESET = "\033[0m"


def _display_width(text: str) -> int:
    """按终端显示宽度计算字符数（中日韩全角字符占两列）。"""
    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in text)


def banner(version: str = "0.1.0") -> str:
    """返回启动横幅；边框按实际显示宽度自适应，避免中英混排时错位。"""
    title = "Chat Digest  ·  本地微信群聊多模态 AI 工作助手"
    inner = _display_width(title) + 4  # 左右各留 2 个空格
    top = "  ┌" + "─" * inner + "┐"
    bottom = "  └" + "─" * inner + "┘"
    return f"""{CYAN}
{top}
  │  {title}  │
{bottom}{RESET}
  {DIM}v{version} · 数据全程留在本机{RESET}
"""


# --------------------------------------------------------------------------- #
# 控制台进度条
# --------------------------------------------------------------------------- #
class ProgressBar:
    """轻量命令行进度条（信息密度参考「按钮变进度条」这一交互模式）。

    非 TTY 环境下自动退化为不打印任何东西，避免污染重定向输出。
    """

    WIDTH = 26

    def __init__(self, enabled: bool = True) -> None:
        self.enabled = enabled
        self._last_len = 0

    def __call__(self, stage: str, percent: int, detail: str = "") -> None:
        """渲染一行进度。"""
        if not self.enabled:
            return
        filled = int(self.WIDTH * max(0, min(100, percent)) / 100)
        bar = "█" * filled + "░" * (self.WIDTH - filled)
        line = f"  {CYAN}{bar}{RESET} {percent:>3}%  {BOLD}{stage:<10}{RESET} {DIM}{detail}{RESET}"
        pad = " " * max(0, self._last_len - len(line) + len(DIM) * 3)
        sys.stdout.write("\r" + line + pad)
        sys.stdout.flush()
        self._last_len = len(line) - len(DIM) * 3 - len(CYAN) - len(BOLD) - len(RESET) * 3
        if percent >= 100:
            sys.stdout.write("\n")
            sys.stdout.flush()


# --------------------------------------------------------------------------- #
# 参数解析
# --------------------------------------------------------------------------- #
def build_parser() -> argparse.ArgumentParser:
    """构造命令行解析器。"""
    parser = argparse.ArgumentParser(
        prog="main.py",
        description="Chat Digest：从微信群采集 -> 多模态加工 -> 生成工作任务清单",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例：\n"
            '  python main.py --group "XX项目工作群"\n'
            '  python main.py --group "XX项目工作群" --date yesterday --format both --open\n'
            "  python main.py --list-groups\n"
            "  python main.py --serve\n"
        ),
    )
    parser.add_argument("--group", "-g", help="目标群聊名称（必须与 chatlog 里的群名一致）")
    parser.add_argument("--date", "-d", default="today", help="日期：2026-10-22 / today / yesterday")
    parser.add_argument("--leaders-only", action="store_true", help="只让领导发言进入总结")
    parser.add_argument("--model", default="default", help="总结后端：default（跟随配置）/ deepseek / doubao / file_output")
    parser.add_argument(
        "--template", default="leader_brief", help="prompt 模板名（config/prompts 下的文件名）"
    )
    parser.add_argument(
        "--format",
        "-f",
        default="markdown",
        choices=["markdown", "html", "both", "none"],
        help="输出格式，默认 markdown",
    )
    parser.add_argument("--no-chat", action="store_true", help="报告里不附带聊天记录全文")
    parser.add_argument("--dry-run", action="store_true", help="跑完链路但不写文件")
    parser.add_argument("--open", action="store_true", help="生成后自动打开 HTML 报告")
    parser.add_argument("--list-groups", action="store_true", help="列出 chatlog 里的全部群聊")
    parser.add_argument("--serve", action="store_true", help="启动本地可视化控制台")
    parser.add_argument("--no-progress", action="store_true", help="关闭进度条")
    parser.add_argument("--verbose", "-v", action="store_true", help="输出 DEBUG 级日志")
    return parser


def pick_formats(value: str) -> List[str]:
    """把 --format 参数翻译成 pipeline 认识的列表。"""
    return {
        "markdown": ["markdown"],
        "html": ["html"],
        "both": ["markdown", "html"],
        "none": [],
    }.get(value, ["markdown"])


# --------------------------------------------------------------------------- #
# 子命令
# --------------------------------------------------------------------------- #
def list_groups(cfg) -> int:
    """列出 chatlog 里的群聊，并标注哪些已在 groups.yaml 中配置。"""
    from collector.chatlog_client import build_client_from_config

    client = build_client_from_config(cfg)
    if not client.health():
        api_base = cfg.get("chatlog", {}).get("api_base")
        print(f"{RED}✗ 连不上数据源{RESET}（后端：{type(client).__name__}）")
        print(f"{DIM}  1) SQLite 直读：在 .env 里设置 WX_DECRYPTED_DIR=你的解密目录{RESET}")
        print(f"{DIM}  2) chatlog HTTP：{api_base}（已停更，仅兼容保留）{RESET}")
        print(f"{DIM}  3) 只想先看效果：python tools/demo_seed.py{RESET}\n")
        return 1

    configured = {g["name"] for g in load_groups()}
    groups = client.get_groups()
    if not groups:
        print(f"{YELLOW}没有查到任何群聊。{RESET}")
        return 0

    print(f"\n{BOLD}chatlog 中的群聊（{len(groups)} 个）：{RESET}\n")
    for group in groups:
        mark = f"{GREEN}● 已配置{RESET}" if group["name"] in configured else f"{DIM}○ 未配置{RESET}"
        count = f"{DIM}{group['member_count']} 人{RESET}" if group.get("member_count") else ""
        print(f"  {mark}  {group['name']}   {count}")
    print(f"\n{DIM}把群名填进 config/groups.yaml 的 target_groups 即可开始采集。{RESET}\n")
    return 0


def serve(cfg) -> int:
    """启动本地控制台。"""
    try:
        from web.server import run_server
    except ImportError as exc:
        print(f"{RED}✗ 控制台模块加载失败：{exc}{RESET}")
        return 1
    return run_server(cfg)


def report(result) -> None:
    """把运行结果打印成一行易读的汇总。"""
    stats = result.stats
    counts = stats.get("counts", {}) or {}
    print()
    print(f"{BOLD}── 运行结果 ──────────────────────────────────{RESET}")
    print(f"  群聊          {result.group}")
    print(f"  日期          {result.date}")
    print(f"  消息总数      {GREEN}{stats.get('total', 0)}{RESET}")
    print(f"  领导发言      {stats.get('leader_count', 0)}")
    print(
        "  类型分布      "
        + " / ".join(
            f"{label} {counts.get(key, 0)}"
            for key, label in (
                ("text", "文字"),
                ("voice", "语音"),
                ("image", "图片"),
                ("article", "链接"),
            )
        )
    )
    if stats.get("failed"):
        print(f"  {YELLOW}处理失败      {stats['failed']} 条（已降级为 [处理失败]）{RESET}")
    print(f"  时间跨度      {format_duration(stats.get('span_minutes', 0))}")
    print(f"  耗时          {result.elapsed:.1f}s")
    for warn in result.warnings:
        print(f"  {YELLOW}! {warn}{RESET}")
    if result.markdown_path:
        print(f"\n  {GREEN}Markdown{RESET}  {result.markdown_path}")
    if result.html_path:
        print(f"  {GREEN}HTML{RESET}      {result.html_path}")
    if result.pending_path:
        print(f"  {CYAN}待处理{RESET}    {result.pending_path}")
        print(f"  {DIM}把它整段丢给 Codex / Claude Code / WorkBuddy 就能拿到总结。{RESET}")
    print()


# --------------------------------------------------------------------------- #
# 主函数
# --------------------------------------------------------------------------- #
def main(argv: Optional[List[str]] = None) -> int:
    """程序入口。

    Returns:
        退出码：0 成功，1 运行失败，2 参数错误。
    """
    force_utf8_console()  # 必须在打印横幅之前：横幅里全是中文
    parser = build_parser()
    args = parser.parse_args(argv)

    print(banner())

    # ---- 加载配置 ---- #
    try:
        cfg = load_settings()
    except ConfigError as exc:
        print(f"{RED}✗ 配置加载失败：{exc}{RESET}")
        return 1

    if args.verbose:
        cfg.setdefault("logging", {})["level"] = "DEBUG"
    logger = setup_logging(cfg)
    logger.debug("配置加载完成")

    # ---- 子命令：列出群聊 / 启动控制台 ---- #
    if args.list_groups:
        return list_groups(cfg)
    if args.serve:
        return serve(cfg)

    # ---- 主流程 ---- #
    if not args.group:
        parser.error("必须提供 --group；或用 --list-groups 查看可用群聊、--serve 启动控制台")

    options = RunOptions(
        group=args.group,
        date=args.date,
        leaders_only=args.leaders_only,
        model=args.model,
        template=args.template,
        include_chat=not args.no_chat,
        formats=pick_formats(args.format),
        dry_run=args.dry_run,
    )

    progress = ProgressBar(enabled=not args.no_progress and sys.stdout.isatty())

    try:
        result = run_pipeline(cfg, options, progress=progress)
    except PipelineError as exc:
        print(f"\n{RED}✗ {exc}{RESET}\n")
        logger.error("流水线终止：%s", exc)
        return 1
    except KeyboardInterrupt:
        # 130 是 shell 约定的「被 SIGINT 终止」，脚本外部的调用方能据此区分
        # 「用户主动取消」与「程序真的失败了」，不要混用 1。
        print(f"\n{YELLOW}已取消。{RESET}")
        return 130
    except Exception as exc:  # 兜底：任何未预期异常都要给出可诊断信息
        logger.exception("未预期的异常")
        print(f"\n{RED}✗ 未预期的错误：{exc}{RESET}")
        print(f"{DIM}完整堆栈已写入 logs/hub.log{RESET}\n")
        return 1

    report(result)

    if args.open and result.html_path:
        webbrowser.open(Path(result.html_path).resolve().as_uri())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
