"""本地可视化控制台后端。

刻意使用 Python 标准库 `http.server` 实现，不引入 Flask / FastAPI：
    · 遵守「依赖最小化」约束，装 `requests + pyyaml + bs4` 就能跑
    · 控制台只是本地调试/阅读层，不需要生产级 Web 框架的全部能力
    · ThreadingHTTPServer 足够支撑单用户的流水线查询

所有 API 只做三件事：查状态、跑流水线、读产物。聊天数据处理全部复用
`core.pipeline.run_pipeline`，与 CLI 完全同源。
"""

from __future__ import annotations

import json
import mimetypes
import re
import threading
import time
import uuid
import webbrowser
from dataclasses import dataclass, field
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, unquote, urlparse

from collector.chatlog_client import build_client_from_config
from core.config import (
    ConfigError,
    load_groups,
    load_groups_raw,
    load_settings,
    resolve_path,
    save_group_leaders,
)
from core.log import get_logger
from core.pipeline import PipelineError, RunOptions, run_pipeline

logger = get_logger("web")

ASSETS_DIR = Path(__file__).resolve().parent / "assets"
MAX_TASKS = 30
MAX_LOGS = 200
MAX_BODY_BYTES = 1 << 20  # 请求体上限，防止恶意 Content-Length 把内存打爆
RUN_TIMEOUT_SEC = 15 * 60  # 单任务软超时，超时后在 UI 上标红而不是无限转圈

#: 从 Markdown 报告的元信息行里反解数字，用于列表卡片展示
_META_RE = {
    "total": re.compile(r"\*\*消息总数\*\*：(\d+)"),
    "leader": re.compile(r"\*\*领导发言数\*\*：(\d+)"),
    "group": re.compile(r"\*\*群聊\*\*：(.+)"),
    "date": re.compile(r"\*\*日期\*\*：(.+)"),
}


# --------------------------------------------------------------------------- #
# 任务管理
# --------------------------------------------------------------------------- #
@dataclass
class Task:
    """一次流水线运行的任务状态。"""

    id: str
    options: Dict[str, Any]
    status: str = "running"  # running / done / error
    stage: str = "排队中"
    percent: int = 0
    detail: str = ""
    logs: List[str] = field(default_factory=list)
    started: float = field(default_factory=time.time)
    finished: Optional[float] = None
    result: Optional[Dict[str, Any]] = None
    error: str = ""

    def to_dict(self, include_logs: bool = True) -> Dict[str, Any]:
        """转成 API 响应字典。

        跑太久的任务会被软标记为「超时」：流水线理论上每步都有网络超时，
        但外部进程挂钩之类的意外仍可能把线程挂住，这里给 UI 一个止损信号，
        免得前端进度条永远停在 99%。
        """
        elapsed = (self.finished or time.time()) - self.started
        status = self.status
        if status == "running" and elapsed > RUN_TIMEOUT_SEC:
            status = "timeout"
        payload: Dict[str, Any] = {
            "id": self.id,
            "status": status,
            "stage": self.stage,
            "percent": self.percent,
            "detail": self.detail,
            "elapsed": round(elapsed, 1),
            "result": self.result,
            "error": self.error or ("运行超时" if status == "timeout" else ""),
        }
        if include_logs:
            payload["logs"] = self.logs[-60:]
        return payload


class TaskRegistry:
    """内存态任务注册表（含简单容量回收）。"""

    def __init__(self, max_tasks: int = MAX_TASKS) -> None:
        self._tasks: Dict[str, Task] = {}
        self._lock = threading.Lock()
        self.max_tasks = max_tasks

    def create(self, options: Dict[str, Any]) -> Task:
        """登记一个新任务（必要时先回收旧任务腾位置）。"""
        with self._lock:
            self._evict_locked()
            task = Task(id=uuid.uuid4().hex[:10], options=options)
            self._tasks[task.id] = task
            return task

    def _evict_locked(self) -> None:
        """回收超量的已结束任务，按开始时间从旧到新淘汰。

        必须在持有 `self._lock` 时调用。原实现按字典插入顺序近似淘汰、
        且把「即将新建的那一个」漏算进配额，长期运行会让注册表一直超限。
        """
        overdue = len(self._tasks) - self.max_tasks + 1
        if overdue <= 0:
            return
        finished = sorted(
            (t for t in self._tasks.values() if t.status != "running"),
            key=lambda t: t.started,
        )
        for task in finished[:overdue]:
            self._tasks.pop(task.id, None)

    def get(self, task_id: str) -> Optional[Task]:
        """按 ID 取任务。"""
        with self._lock:
            return self._tasks.get(task_id)

    def running(self) -> Optional[Task]:
        """取当前正在跑的任务（用于阻止并发重复触发）。"""
        with self._lock:
            candidates = [t for t in self._tasks.values() if t.status == "running"]
            return max(candidates, key=lambda t: t.started) if candidates else None

    def latest(self) -> Optional[Task]:
        """取最近一次任务（用于刷新页面后恢复进度）。"""
        with self._lock:
            running = [t for t in self._tasks.values() if t.status == "running"]
            if running:
                return max(running, key=lambda t: t.started)
            if not self._tasks:
                return None
            return max(self._tasks.values(), key=lambda t: t.started)


TASKS = TaskRegistry()


# --------------------------------------------------------------------------- #
# chatlog 客户端复用
# --------------------------------------------------------------------------- #
_CLIENT_LOCK = threading.Lock()
_CLIENT: Optional[Any] = None
_CLIENT_KEY = ""


def shared_client(cfg: Dict[str, Any]):
    """返回复用的 chatlog 客户端。

    控制台每 1.5s 轮询一次 `/api/status`，若每次都 `build_client_from_config`
    现造一个实例，就等于每次新建一条 TCP 连接（外加一个 ConnectionPool）。
    长时间挂着会持续累积 TIME_WAIT 套接字与句柄 —— 这是典型的慢泄漏。
    配置变化时（改了 api_base 等）自动换掉旧实例。
    """
    global _CLIENT, _CLIENT_KEY

    key = json.dumps(cfg.get("chatlog") or {}, sort_keys=True, default=str)
    with _CLIENT_LOCK:
        if _CLIENT is not None and _CLIENT_KEY == key:
            return _CLIENT
        previous, _CLIENT = _CLIENT, build_client_from_config(cfg)
        _CLIENT_KEY = key

    if previous is not None:  # 释放旧实例持有的连接
        try:
            previous.close()
        except Exception:  # pragma: no cover
            pass
    return _CLIENT


def _run_worker(cfg: Dict[str, Any], task: Task, options: RunOptions) -> None:
    """后台线程：真正跑流水线并回写进度。"""

    def emit(stage: str, percent: int, detail: str = "") -> None:
        task.stage = stage
        task.percent = int(percent)
        task.detail = detail
        task.logs.append(f"{datetime.now():%H:%M:%S} {stage} · {detail}")
        if len(task.logs) > MAX_LOGS:
            task.logs[:] = task.logs[-MAX_LOGS:]

    try:
        emit("启动", 2, f"群={options.group}")
        result = run_pipeline(cfg, options, progress=emit)
        emit("完成", 100, f"{result.stats.get('total', 0)} 条消息")
        result_dict = result.as_dict()
        # 附件不可 JSON 化，只保留必要信息
        result_dict["stats"] = {
            k: v
            for k, v in result.stats.items()
            if k in ("total", "leader_count", "failed", "span_minutes", "counts", "chars")
        }
        task.result = result_dict
        task.status = "done"
    except PipelineError as exc:
        task.status = "error"
        task.error = str(exc)
        emit("失败", 100, str(exc)[:120])
        logger.error("控制台任务失败：%s", exc)
    except Exception as exc:  # 兜底，避免线程静默死亡
        task.status = "error"
        task.error = f"未预期的错误：{exc}"
        logger.exception("控制台任务异常")
    finally:
        task.finished = time.time()


# --------------------------------------------------------------------------- #
# 产物读取
# --------------------------------------------------------------------------- #
def _safe_name(runs_dir: Path, name: str) -> Optional[Path]:
    """校验并拼出产物绝对路径，阻断 ../ 之类的路径穿越。

    Args:
        runs_dir: 允许访问的根目录。
        name: 请求里的文件名。

    Returns:
        安全时返回 Path，否则返回 None。
    """
    if not name or name.strip() in (".", ".."):
        return None
    candidate = (runs_dir / Path(unquote(name)).name).resolve()
    try:
        candidate.relative_to(runs_dir.resolve())
    except ValueError:
        return None
    return candidate


def _parse_meta(md_path: Path) -> Dict[str, Any]:
    """从 Markdown 报告头部的元信息行里抽统计数字。"""
    meta: Dict[str, Any] = {"group": "", "date": "", "total": 0, "leader": 0}
    try:
        head = md_path.read_text(encoding="utf-8", errors="replace")[:4000]
    except OSError:
        return meta
    for key, pattern in _META_RE.items():
        found = pattern.search(head)
        if not found:
            continue
        value = found.group(1).strip()
        if key in ("total", "leader"):
            try:
                meta[key] = int(value)
            except ValueError:
                pass
        else:
            meta[key] = value
    return meta


def list_runs(cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    """列出 runs/ 下的全部报告（按时间倒序）。"""
    runs_dir = resolve_path(cfg.get("output", {}).get("dir", "./runs"), mkdir=True)
    items: List[Dict[str, Any]] = []

    for md in sorted(runs_dir.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True):
        meta = _parse_meta(md)
        html = md.with_suffix(".html")
        items.append(
            {
                "md": md.name,
                "html": html.name if html.exists() else "",
                "group": meta["group"] or _guess_group(md.stem),
                "date": meta["date"] or _guess_date(md.stem),
                "total": meta["total"],
                "leader": meta["leader"],
                "size": md.stat().st_size,
                "mtime": int(md.stat().st_mtime),
            }
        )
    return items


def _guess_group(stem: str) -> str:
    """从文件名 `2026-10-22_XX项目工作群` 里猜群名。"""
    parts = stem.split("_", 1)
    return parts[1] if len(parts) > 1 else "未命名群"


def _guess_date(stem: str) -> str:
    """从文件名里取日期。"""
    return stem.split("_", 1)[0]


# --------------------------------------------------------------------------- #
# HTTP Handler
# --------------------------------------------------------------------------- #
class HubHandler(BaseHTTPRequestHandler):
    """控制台请求处理器。"""

    server_version = "WeChatWorkHub/0.1"
    # HTTP/1.1 + keep-alive：默认 HTTP/1.0 会在每次响应后关闭连接，
    # 前端每 1.5s 轮询一次状态，那样会稳定堆积上百条 TIME_WAIT 套接字。
    # 配合正确的 Content-Length，连接得以复用，客户端连接池才有意义。
    protocol_version = "HTTP/1.1"
    cfg: Dict[str, Any] = {}

    # ---------- 基础 ---------- #
    def log_message(self, fmt: str, *args: Any) -> None:
        """把访问日志接到项目 logger，而不是打到 stderr。"""
        logger.debug("%s %s", self.address_string(), fmt % args)

    def _send(
        self,
        body: bytes,
        status: int = 200,
        content_type: str = "text/plain; charset=utf-8",
        extra: Optional[Dict[str, str]] = None,
    ) -> None:
        """统一的响应写出。"""
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, data: Any, status: int = 200) -> None:
        """写出 JSON。"""
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self._send(body, status, "application/json; charset=utf-8")

    def _error(self, message: str, status: int = 400) -> None:
        """写出统一格式的错误。"""
        self._json({"ok": False, "error": message}, status)

    def _read_body(self) -> Dict[str, Any]:
        """读取并解析 JSON 请求体（超过 1MB 直接丢弃，不读进内存）。"""
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return {}
        if length <= 0:
            return {}
        if length > MAX_BODY_BYTES:
            logger.warning("请求体过大被拒绝：%d 字节", length)
            return {}
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8", "replace"))
        except ValueError:
            return {}
        return payload if isinstance(payload, dict) else {}

    def _guard(self, handler: Callable[[], None]) -> None:
        """包住每个请求的处理过程。

        必要性：`BaseHTTPRequestHandler` 里任一异常都会让该请求半途断连、
        浏览器只看到「连接已重置」，同时 traceback 直接刷到 stderr。
        这里统一转成 500 JSON，并对浏览器主动取消导致的断管保持静默。
        """
        try:
            handler()
        except (ConnectionResetError, BrokenPipeError):
            pass  # 用户刷新/关页面导致的正常现象，不该报警
        except Exception as exc:
            logger.exception("处理 %s 失败", self.path)
            try:
                self._error(f"服务内部错误：{exc}", 500)
            except Exception:  # pragma: no cover - socket 已断，无从写回
                pass

    # ---------- 路由 ---------- #
    def do_HEAD(self) -> None:  # noqa: N802
        """健康检查用：只回头部。"""
        self._guard(lambda: self._send(b"", 200))

    def do_GET(self) -> None:  # noqa: N802
        """处理所有 GET 请求。"""
        self._guard(self._handle_get)

    def do_POST(self) -> None:  # noqa: N802
        """处理 POST：跑流水线。"""
        self._guard(self._handle_post)

    def do_DELETE(self) -> None:  # noqa: N802
        """处理 DELETE：删除单份报告 / 清空全部。"""
        self._guard(self._handle_delete)

    def _handle_get(self) -> None:
        """GET 路由分发。"""
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        query = parse_qs(parsed.query)

        routes: Dict[str, Callable[[Dict[str, List[str]]], None]] = {
            "/": self._page,
            "/api/status": self._api_status,
            "/api/groups": self._api_groups,
            "/api/groups/config": self._api_groups_config,
            "/api/runs": self._api_runs,
            "/api/report": self._api_report,
            "/api/task": self._api_task,
        }

        if path.startswith("/reports/"):
            return self._serve_report(path[len("/reports/") :])
        if path.startswith("/assets/"):
            return self._serve_asset(path[len("/assets/") :])
        if path in routes:
            return routes[path](query)
        if path == "/favicon.ico":
            return self._send(b"", 404)
        self._error(f"未知路径：{path}", 404)

    def _handle_post(self) -> None:
        """POST 路由分发。"""
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")
        if path == "/api/run":
            self._api_run()
            return
        if path == "/api/groups/config":
            self._api_save_groups_config()
            return
        self._error("未知接口", 404)

    def _handle_delete(self) -> None:
        """DELETE 路由分发。"""
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        if parsed.path.rstrip("/") == "/api/report":
            self._api_delete_report(query)
            return
        if parsed.path.rstrip("/") == "/api/runs":
            self._api_delete_runs()
            return
        self._error("未知接口", 404)

    # ---------- 页面与静态资源 ---------- #
    def _page(self, query: Dict[str, List[str]]) -> None:
        """返回控制台首页。"""
        index = ASSETS_DIR / "index.html"
        if not index.exists():
            self._send(
                "<h1>缺少 web/assets/index.html</h1>".encode("utf-8"),
                500,
                "text/html; charset=utf-8",
            )
            return
        self._send(index.read_bytes(), 200, "text/html; charset=utf-8")

    def _serve_asset(self, name: str) -> None:
        """返回静态资源（css / js）。"""
        target = _safe_name(ASSETS_DIR, name)
        if not target or not target.exists():
            return self._error("资源不存在", 404)
        mime = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if mime.startswith("text/") or mime in ("application/javascript",):
            mime += "; charset=utf-8"
        self._send(target.read_bytes(), 200, mime)

    def _serve_report(self, name: str) -> None:
        """返回 runs/ 目录下的产物（HTML 报告可直接在浏览器打开）。"""
        runs_dir = resolve_path(self.cfg.get("output", {}).get("dir", "./runs"), mkdir=True)
        target = _safe_name(runs_dir, name)
        if not target or not target.exists():
            return self._error("报告不存在", 404)
        mime = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if mime.startswith("text/") or mime == "application/javascript":
            mime += "; charset=utf-8"
        self._send(target.read_bytes(), 200, mime)

    # ---------- API ---------- #
    def _api_status(self, query: Dict[str, List[str]]) -> None:
        """返回服务与 chatlog 的健康状态。"""
        client = shared_client(self.cfg)
        online = client.health()
        from core.config import list_prompt_templates

        self._json(
            {
                "ok": True,
                "chatlog_online": online,
                "api_base": self.cfg.get("chatlog", {}).get("api_base"),
                "today": datetime.now().strftime("%Y-%m-%d"),
                "templates": list_prompt_templates(),
                "version": "0.1.0",
            }
        )

    def _api_groups(self, query: Dict[str, List[str]]) -> None:
        """返回 chatlog 群列表，并标注是否已在 groups.yaml 中配置。"""
        client = shared_client(self.cfg)
        configured = {g["name"]: len(g.get("leaders", [])) for g in load_groups()}
        merged: List[Dict[str, Any]] = []

        if client.health():
            for group in client.get_groups():
                name = group.get("name", "")
                merged.append(
                    {
                        "name": name,
                        "configured": name in configured,
                        "leader_count": configured.get(name, 0),
                        "source": "chatlog",
                    }
                )
        # chatlog 不可用时，退化为展示配置里的群，保证页面仍能操作
        for name, count in configured.items():
            if not any(m["name"] == name for m in merged):
                merged.append(
                    {"name": name, "configured": True, "leader_count": count, "source": "config"}
                )

        self._json({"ok": True, "groups": merged})

    def _api_groups_config(self, query: Dict[str, List[str]]) -> None:
        """返回 groups.yaml 原始结构，供前端「领导设置」面板展示。"""
        self._json({"ok": True, **load_groups_raw()})

    def _api_save_groups_config(self) -> None:
        """保存某群的领导名单（写回 groups.yaml）。

        请求体：`{"group": "群名", "leaders": [{"name": "...", "id": ""}]}`。
        """
        payload = self._read_body()
        group = str(payload.get("group") or "").strip()
        if not group:
            return self._error("group 不能为空")
        leaders = payload.get("leaders")
        if not isinstance(leaders, list):
            return self._error("leaders 必须是数组")
        try:
            save_group_leaders(group, leaders)
        except ConfigError as exc:
            return self._error(f"保存失败：{exc}", 500)
        logger.info("控制台更新了群「%s」的领导名单（%d 人）", group, len(leaders))
        self._json({"ok": True, "group": group, "leader_count": len(leaders)})

    def _api_runs(self, query: Dict[str, List[str]]) -> None:
        """返回历史报告列表。"""
        self._json({"ok": True, "runs": list_runs(self.cfg)})

    def _api_report(self, query: Dict[str, List[str]]) -> None:
        """返回某份报告的 Markdown 全文。"""
        runs_dir = resolve_path(self.cfg.get("output", {}).get("dir", "./runs"), mkdir=True)
        name = (query.get("md") or [""])[0]
        target = _safe_name(runs_dir, name)
        if not target or not target.exists():
            return self._error("报告不存在", 404)
        html = target.with_suffix(".html")
        self._json(
            {
                "ok": True,
                "markdown": target.read_text(encoding="utf-8", errors="replace"),
                "html": html.name if html.exists() else "",
            }
        )

    def _api_task(self, query: Dict[str, List[str]]) -> None:
        """返回任务进度。"""
        task_id = (query.get("id") or [""])[0]
        task = TASKS.get(task_id) if task_id else TASKS.latest()
        if not task:
            return self._json({"ok": True, "task": None})
        self._json({"ok": True, "task": task.to_dict()})

    def _api_run(self) -> None:
        """启动一次流水线。"""
        payload = self._read_body()
        group = str(payload.get("group") or "").strip()
        if not group:
            return self._error("group 不能为空")

        # 同一时刻只允许一个任务在跑：重复点击按钮会并行拉同一批聊天记录，
        # 既浪费带宽又会让两份进度互相覆盖，前端根本看不出在跑哪一个。
        active = TASKS.running()
        if active:
            return self._json(
                {
                    "ok": False,
                    "task_id": active.id,
                    "busy": True,
                    "error": f"已有任务在运行中（{active.stage}），请等它结束",
                },
                409,
            )

        fmt = str(payload.get("format") or "markdown")
        options = RunOptions(
            group=group,
            date=str(payload.get("date") or "today"),
            leaders_only=bool(payload.get("leaders_only")),
            model=str(payload.get("model") or self.cfg.get("summarizer", {}).get("provider", "file_output")),
            template=str(payload.get("template") or "leader_brief"),
            formats={"markdown": ["markdown"], "html": ["html"], "both": ["markdown", "html"]}.get(
                fmt, ["markdown"]
            ),
        )

        task = TASKS.create(
            {
                "group": group,
                "date": options.date,
                "leaders_only": options.leaders_only,
                "format": fmt,
            }
        )
        thread = threading.Thread(
            target=_run_worker, args=(self.cfg, task, options), name=f"run-{task.id}", daemon=True
        )
        thread.start()
        logger.info("控制台启动任务 %s：%s", task.id, group)
        self._json({"ok": True, "task_id": task.id})

    def _api_delete_report(self, query: Dict[str, List[str]]) -> None:
        """删除单份报告（md 与其配套 html）。"""
        runs_dir = resolve_path(self.cfg.get("output", {}).get("dir", "./runs"), mkdir=True)
        target = _safe_name(runs_dir, (query.get("md") or [""])[0])
        if not target or not target.exists():
            return self._error("报告不存在", 404)
        try:
            target.unlink()
            html = target.with_suffix(".html")
            if html.exists():
                html.unlink()
        except OSError as exc:
            return self._error(f"删除失败：{exc}", 500)
        self._json({"ok": True})

    def _api_delete_runs(self) -> None:
        """清空 runs 目录下的全部报告（pending 目录保留免得丢待处理内容）。"""
        runs_dir = resolve_path(self.cfg.get("output", {}).get("dir", "./runs"), mkdir=True)
        removed = 0
        try:
            for target in list(runs_dir.glob("*.md")) + list(runs_dir.glob("*.html")):
                if target.parent == runs_dir:
                    target.unlink()
                    removed += 1
        except OSError as exc:
            return self._error(f"清理失败：{exc}", 500)
        logger.info("控制台清理了 %d 份报告", removed)
        self._json({"ok": True, "removed": removed})


# --------------------------------------------------------------------------- #
# 启动
# --------------------------------------------------------------------------- #
def build(
    cfg: Dict[str, Any], host: Optional[str] = None, port: Optional[int] = None
) -> Tuple[ThreadingHTTPServer, str]:
    """构造服务器实例（不阻塞，便于测试与嵌入）。

    Returns:
        `(server, url)`
    """
    web_cfg = cfg.get("web", {}) or {}
    bind_host = host or str(web_cfg.get("host", "127.0.0.1"))
    bind_port = int(port or web_cfg.get("port", 5710))

    handler = type("BoundHubHandler", (HubHandler,), {"cfg": cfg})
    for candidate in range(bind_port, bind_port + 12):
        try:
            server = ThreadingHTTPServer((bind_host, candidate), handler)
            break
        except OSError:
            continue
    else:
        raise PipelineError(f"端口 {bind_port}-{bind_port + 11} 都被占用了，请在 settings.yaml 里换一个")

    url = f"http://{bind_host}:{server.server_address[1]}"
    return server, url


def run_server(cfg: Optional[Dict[str, Any]] = None) -> int:
    """启动并阻塞运行控制台。

    Returns:
        退出码。
    """
    try:
        cfg = cfg or load_settings()
    except ConfigError as exc:
        print(f"配置加载失败：{exc}")
        return 1

    get_logger("web")
    try:
        server, url = build(cfg)
    except (PipelineError, OSError) as exc:
        print(f"控制台启动失败：{exc}")
        return 1

    print(f"\n  本地控制台已就绪：{url}")
    print("  Ctrl+C 退出\n")
    if cfg.get("web", {}).get("open_browser", True):
        try:
            webbrowser.open(url)
        except Exception:  # 打不开浏览器不该影响服务
            pass

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  控制台已关闭")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(run_server())
