"""流水线编排：把采集 -> 加工 -> 合并 -> 总结 -> 输出串成一条链。

CLI（`main.py`）和本地控制台（`web/server.py`）共用这一份实现，
保证「命令行跑出来的」和「页面上点出来的」结果完全一致。

设计原则：
    · 任何单条消息处理失败都不应中断整个流程，降级为 [处理失败] 并继续
    · 所有异常都有 try-catch，每一步都有日志
"""

from __future__ import annotations

import traceback
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from collector.chatlog_client import build_client_from_config
from collector.models import TYPE_ARTICLE, TYPE_IMAGE, TYPE_VOICE, Message
from core.config import get_leaders, leaders_as_text, load_prompt_template, resolve_path
from core.log import get_logger
from core.stats import summarize_messages
from output.html import write_html
from output.markdown import write_markdown
from processor import merge as merge_mod
from processor.article_fetch import build_fetcher_from_config
from processor.image_vision import build_vision_from_config
from processor.voice_asr import build_transcriber_from_config
from summarizer import build_summarizer

logger = get_logger("pipeline")

ProgressFn = Callable[[str, int, str], None]


class PipelineError(RuntimeError):
    """流水线遇到无法继续的致命错误时抛出。"""


@dataclass
class RunOptions:
    """一次运行的输入参数。

    Attributes:
        group: 目标群名。
        date: 日期，支持 `YYYY-MM-DD` / `today` / `yesterday`。
        leaders_only: 是否只保留领导发言进入总结。
        model: 总结后端，`file_output` 或 `doubao`。
        template: prompt 模板名。
        include_chat: 报告里是否附带聊天记录全文。
        formats: 输出格式列表，如 `["markdown", "html"]`。
        dry_run: 为 True 时只采集不写文件。
    """

    group: str
    date: str = "today"
    leaders_only: bool = False
    model: str = "default"
    template: str = "leader_brief"
    include_chat: bool = True
    formats: List[str] = field(default_factory=lambda: ["markdown"])
    dry_run: bool = False


@dataclass
class RunResult:
    """一次运行的产物与统计。"""

    group: str
    date: str
    messages: List[Message]
    stats: Dict[str, Any]
    summary: str = ""
    chat_content: str = ""
    markdown_path: Optional[Path] = None
    html_path: Optional[Path] = None
    pending_path: Optional[Path] = None
    warnings: List[str] = field(default_factory=list)
    elapsed: float = 0.0

    def as_dict(self) -> Dict[str, Any]:
        """转成可 JSON 序列化的字典（给控制台 API 用）。"""
        return {
            "group": self.group,
            "date": self.date,
            "total": self.stats.get("total", 0),
            "leader_count": self.stats.get("leader_count", 0),
            "summary": self.summary,
            "markdown_path": self.markdown_path.as_posix() if self.markdown_path else "",
            "html_path": self.html_path.as_posix() if self.html_path else "",
            "pending_path": self.pending_path.as_posix() if self.pending_path else "",
            "warnings": self.warnings,
            "elapsed": round(self.elapsed, 2),
        }


def parse_date(text: str) -> datetime:
    """把 `2026-10-22` / `today` / `yesterday` 解析成当天零点。

    Args:
        text: 日期表达式。

    Returns:
        当天 00:00:00 的 datetime。

    Raises:
        ValueError: 无法解析时抛出。
    """
    token = (text or "today").strip().lower()
    now = datetime.now()
    midnight = datetime(now.year, now.month, now.day)

    if token in ("today", "今天"):
        return midnight
    if token in ("yesterday", "昨天"):
        # 用 date 对象做减法是唯一的跨月/跨年安全写法；
        # 直接 day-1 在每月 1 号会得到当月 1 号，1 月 1 号更会抛 ValueError。
        return datetime.combine(midnight.date() - timedelta(days=1), datetime.min.time())
    if token in ("tomorrow", "明天"):
        return datetime.combine(midnight.date() + timedelta(days=1), datetime.min.time())

    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y%m%d"):
        try:
            return datetime.strptime(token, fmt)
        except ValueError:
            continue
    raise ValueError(f"无法解析日期：{text!r}（可用：2026-10-22 / today / yesterday）")


def day_range(day: datetime) -> tuple:
    """返回当天的起止 datetime（含）。"""
    start = datetime(day.year, day.month, day.day, 0, 0, 0)
    end = datetime(day.year, day.month, day.day, 23, 59, 59)
    return start, end


def _noop_progress(stage: str, percent: int, detail: str = "") -> None:
    """默认的空 progress 回调。"""


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #
def run_pipeline(
    cfg: Dict[str, Any],
    options: RunOptions,
    progress: Optional[ProgressFn] = None,
    groups_path: Optional[Path] = None,
) -> RunResult:
    """执行完整流水线。

    Args:
        cfg: 已加载的配置字典。
        options: 本次运行参数。
        progress: 进度回调 `fn(stage_label, percent, detail)`，用于 UI 与控制台进度条。
        groups_path: 可选的群配置路径（测试用）。

    Returns:
        RunResult，包含消息列表、统计、总结与产物路径。

    Raises:
        PipelineError: 致命错误（服务不可达、日期解析失败、provider 配置错误）。
    """
    started = datetime.now()
    emit = progress or _noop_progress
    warnings: List[str] = []

    # ---------- 1. 参数与时间范围 ---------- #
    emit("解析参数", 4, f"群={options.group} 日期={options.date}")
    try:
        day = parse_date(options.date)
        start_dt, end_dt = day_range(day)
    except ValueError as exc:
        raise PipelineError(str(exc)) from exc
    date_text = f"{day:%Y-%m-%d}"

    # 必须重建 summarizer 子字典再改写：cfg 只是浅拷贝，
    # 直接 `cfg["summarizer"][k] = v` 会把本次参数写回调用方持有的配置对象，
    # 让控制台里「换一次模板」永久污染后续所有运行。
    cfg = dict(cfg)
    summarizer_cfg = dict(cfg.get("summarizer") or {})
    if options.model.strip().lower() not in ("", "default"):
        summarizer_cfg["provider"] = options.model
    if options.template:
        summarizer_cfg["prompt_template"] = options.template
    cfg["summarizer"] = summarizer_cfg

    # ---------- 2. 连接数据源 ---------- #
    emit("连接数据源", 10, cfg.get("chatlog", {}).get("api_base", ""))
    client = build_client_from_config(cfg)
    if not client.health():
        data_dir = cfg.get("chatlog", {}).get("decrypted_dir", "")
        raise PipelineError(
            f"连不上数据源（解密目录：{data_dir or '未配置'}）。"
            "请确认：① 微信电脑版已登录；② 已在 .env 里配置 WX_DECRYPTED_DIR；"
            "③ 数据源已经导出好最新数据（双击「刷新数据.bat」可执行你配置的刷新脚本）。"
        )

    leaders = get_leaders(options.group, groups_path)
    if not leaders:
        warnings.append(f"群「{options.group}」还没设置关注的人，本次不会打「领导」标记（可在网页点「设置关注的人」添加）")

    # ---------- 3. 拉取消息 ---------- #
    emit("拉取群聊消息", 25, f"{date_text} 00:00 - 23:59")
    try:
        messages = client.get_messages(options.group, start_dt, end_dt, leaders=leaders)
    except Exception as exc:
        raise PipelineError(f"拉取聊天记录失败：{exc}") from exc

    if not messages:
        warnings.append(f"{date_text} 在群「{options.group}」没有拉到任何消息")

    # ---------- 4. 多模态加工 ---------- #
    total = max(len(messages), 1)
    transcriber = build_transcriber_from_config(cfg)
    vision = build_vision_from_config(cfg)
    fetcher = build_fetcher_from_config(cfg)

    for index, msg in enumerate(messages):
        percent = 35 + int(index / total * 38)
        try:
            if msg.msg_type == TYPE_VOICE:
                emit("语音转写", percent, f"{msg.time_text} {msg.sender_name}")
                transcriber.process_message(msg, _voice_fetcher(client))
            elif msg.msg_type == TYPE_IMAGE:
                emit("图片理解", percent, f"{msg.time_text} {msg.sender_name}")
                vision.process_message(msg, _image_fetcher(client, cfg))
            elif msg.msg_type == TYPE_ARTICLE:
                emit("抓取文章正文", percent, f"{msg.time_text} {msg.sender_name}")
                fetcher.process_message(msg)
        except Exception as exc:  # 单条失败必须降级，不能中断整批
            logger.warning("消息 %s 加工失败：%s", msg.msg_id, exc)
            logger.debug(traceback.format_exc())
            msg.content = "[处理失败]"
            msg.note = f"加工异常：{exc}"

    stats = summarize_messages(messages)
    stats["leaders"] = [l.get("name", "") for l in leaders]

    # ---------- 5. 合并 ---------- #
    emit("合并聊天文本", 76, f"{stats['total']} 条")
    chat_content = merge_mod.build_chat_content(
        messages,
        leaders_only=options.leaders_only,
        max_chars=int(cfg.get("summarizer", {}).get("max_input_chars", 0)) or None,
    )

    # ---------- 6. 总结 ---------- #
    emit("生成总结", 85, f"provider={cfg.get('summarizer', {}).get('provider')}")
    summarizer = build_summarizer(cfg)
    leaders_text = leaders_as_text(leaders)
    summarizer.last_meta.update(
        {
            "group_name": options.group,
            "date": date_text,
            "total": stats.get("total", 0),
            "leader_count": stats.get("leader_count", 0),
            "leaders_text": leaders_text,
        }
    )

    try:
        prompt_template = load_prompt_template(options.template)
    except Exception as exc:
        logger.warning("prompt 模板加载失败，使用内置兜底模板：%s", exc)
        prompt_template = "{chat_content}"

    try:
        summary = summarizer.summarize(chat_content, prompt_template)
    except Exception as exc:
        logger.error("总结环节失败：%s", exc)
        summary = (
            "## 总结\n\n> ⚠️ 总结环节失败：" + str(exc) + "\n>\n> 聊天记录已完整保留在下方。"
        )
        warnings.append(f"总结环节失败：{exc}")

    # ---------- 7. 输出 ---------- #
    emit("写入报告", 94, "runs/")
    result = RunResult(
        group=options.group,
        date=date_text,
        messages=messages,
        stats=stats,
        summary=summary,
        chat_content=chat_content,
        warnings=warnings,
        pending_path=summarizer.last_output_path,
    )

    if options.dry_run:
        emit("完成（dry-run）", 100, "未写入文件")
        result.elapsed = (datetime.now() - started).total_seconds()
        return result

    out_dir = resolve_path(cfg.get("output", {}).get("dir", "./runs"), mkdir=True)
    formats = [f.lower() for f in (options.formats or cfg.get("output", {}).get("format", ["markdown"]))]

    visible = merge_mod.filter_leaders_only(messages) if options.leaders_only else messages

    if "markdown" in formats or "md" in formats:
        try:
            result.markdown_path = write_markdown(
                summary,
                visible,
                options.group,
                date_text,
                output_dir=out_dir,
                stats=stats,
                include_meta=bool(cfg.get("output", {}).get("include_meta", True)),
                include_chat=options.include_chat,
            )
        except OSError as exc:
            raise PipelineError(f"Markdown 写入失败：{exc}") from exc

    if "html" in formats:
        try:
            result.html_path = write_html(
                summary,
                visible,
                options.group,
                date_text,
                output_dir=out_dir,
                stats=stats,
                theme=str(cfg.get("output", {}).get("html_theme", "light")),
            )
        except OSError as exc:
            warnings.append(f"HTML 写入失败：{exc}")

    result.elapsed = (datetime.now() - started).total_seconds()
    emit("完成", 100, f"{stats.get('total', 0)} 条消息")
    return result


# --------------------------------------------------------------------------- #
# 媒体取值器
# --------------------------------------------------------------------------- #
def _voice_fetcher(client) -> Callable[[Message], Optional[str]]:
    """构造「把语音消息换成本地音频路径」的可调用对象。"""

    def _fetch(msg: Message) -> Optional[str]:
        key = ""
        for att in msg.attachments:
            if att.get("kind") == "voice":
                key = str(att.get("key") or "")
        return client.get_voice_file(key or msg.raw_content or msg.msg_id)

    return _fetch


def _image_fetcher(client, cfg: Dict[str, Any]) -> Callable[[Message], Optional[str]]:
    """构造「把图片消息换成本地图片路径」的可调用对象。"""

    budget = int((cfg.get("vision", {}) or {}).get("max_images", 20) or 20)
    used = {"n": 0}

    def _fetch(msg: Message) -> Optional[str]:
        # dummy provider 不需要真的下载图，直接返回空以省流量
        if str((cfg.get("vision", {}) or {}).get("provider", "dummy")).lower() == "dummy":
            return None
        if used["n"] >= budget:
            return None
        used["n"] += 1
        for att in msg.attachments:
            if att.get("kind") == "image":
                return client.get_image_file(str(att.get("key") or ""))
        return client.get_image_file(msg.raw_content)

    return _fetch
