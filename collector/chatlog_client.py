"""chatlog HTTP API 客户端。

对接 chatlog（github.com/sjzar/chatlog）的本地 HTTP 服务，默认 ::

    GET /api/v1/chatlog?time=2026-10-22~2026-10-22&talker=<群名>&limit=500&offset=0&format=json
    GET /api/v1/chatroom
    GET /api/v1/contact
    GET /api/v1/session
    GET /voice/<key>          # 直接返回 SILK 实时转码后的 MP3
    GET /image/<key>          # 302 跳转到解密后的图片
    GET /health

设计要点：
    · chatlog 版本间字段名不一致（驼峰 / 下划线），这里做多别名兜底读取
    · 分页拉全量，带最大页数上限，防止数据量大时无限翻页
    · 所有网络异常收敛为 ChatlogError，由上层决定降级还是中断
"""

from __future__ import annotations

import re
import subprocess
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import quote

import requests
from requests.adapters import HTTPAdapter

from collector.models import (
    TYPE_ARTICLE,
    TYPE_IMAGE,
    TYPE_VOICE,
    Message,
    normalize_type,
)
from core.log import get_logger

logger = get_logger("chatlog_client")

# XML-encoded content 里的公众号链接 / 图片 / 语音模板会用到这些标签
_XML_TAG_APPMSG = ("appmsg",)
_MD5_RE = re.compile(r"[0-9a-fA-F]{32}")


class ChatlogError(RuntimeError):
    """与 chatlog 通信失败时抛出。"""


# --------------------------------------------------------------------------- #
# 字段读取工具：chatlog 各版本返回字段名不完全一致
# --------------------------------------------------------------------------- #
_FIELD_ALIASES: Dict[str, Tuple[str, ...]] = {
    "id": ("id", "msgId", "msg_id", "localId", "serverId", "sortSeq", "seq"),
    "seq": ("seq", "sortSeq", "localId", "serverId"),
    "time": ("time", "timestamp", "createTime", "createdAt", "TimeStamp"),
    "talker": ("talker", "chatroom", "roomId", "roomid", "Talker"),
    "talker_name": ("talkerName", "talker_name", "roomName", "chatroomName", "nickName"),
    "sender": ("sender", "senderId", "wxid", "userName", "Sender"),
    "sender_name": (
        "senderName",
        "sender_name",
        "nickname",
        "nickName",
        "displayName",
        "remark",
        "Remark",
    ),
    "content": ("content", "message", "msg", "strContent", "Content"),
    "type": ("type", "msgType", "MsgType", "typeName"),
    "sub_type": ("subType", "sub_type", "SubType"),
    "is_self": ("isSelf", "is_self"),
    "media_key": ("mediaKey", "media_key", "md5", "MD5"),
}


def _pick(data: Dict[str, Any], logical: str, default: Any = None) -> Any:
    """按逻辑名读取字段，依次尝试所有已知别名。"""
    for key in _FIELD_ALIASES.get(logical, (logical,)):
        if key in data and data[key] not in (None, ""):
            return data[key]
    return default


def _parse_time(value: Any) -> Optional[datetime]:
    """把各种时间表示统一解析为 datetime。

    支持：`2026-10-22 14:30:00` / ISO8601 / Unix 秒或毫秒时间戳。
    """
    if value is None or value == "":
        return None

    # 数值型：秒 / 毫秒
    if isinstance(value, (int, float)):
        num = float(value)
        if num > 1e11:  # 毫秒
            num /= 1000.0
        try:
            return datetime.fromtimestamp(num)
        except (OSError, OverflowError, ValueError):
            return None

    text = str(value).strip().replace("T", " ")
    candidates = (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d",
        "%Y/%m/%d %H:%M:%S",
        "%Y年%m月%d日 %H:%M",
    )
    for fmt in candidates:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    try:  # 标准 ISO 兜底
        return datetime.fromisoformat(text)
    except ValueError:
        logger.debug("无法解析时间字段：%r", value)
        return None


def _clean_text(text: str) -> str:
    """去掉 XML 噪音与控制字符，得到干净文本。"""
    if not text:
        return ""
    cleaned = re.sub(r"<[^>]{1,200}>", "", text)          # 残留标签
    cleaned = cleaned.replace("\u2005", " ").replace("‍", "")
    cleaned = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", cleaned)
    return cleaned.strip()


def parse_appmsg_xml(xml_text: str) -> Dict[str, str]:
    """解析 type=49 复合消息的 XML，抽取标题 / 链接 / 图片等。

    Args:
        xml_text: 形如 `<msg><appmsg><title>..</title><url>..</url></appmsg></msg>`。

    Returns:
        至少包含 title / url / app_name / md5 的字典，解析失败时返回空字典。
    """
    if not xml_text or "<" not in xml_text:
        return {}
    try:
        root = ET.fromstring(xml_text.strip())
    except ET.ParseError:
        # chatlog 偶尔返回未闭合的片段，补一层根节点再试
        try:
            root = ET.fromstring(f"<root>{xml_text.strip()}</root>")
        except ET.ParseError:
            return {}

    def _text(tag: str) -> str:
        node = root.find(f".//{tag}")
        return (node.text or "").strip() if node is not None and node.text else ""

    md5 = _text("md5") or _text("cdnthumbaeskey")
    found = _MD5_RE.search(xml_text)
    if not md5 and found:
        md5 = found.group(0)

    return {
        "title": _text("title") or _text("des")[:40],
        "url": _text("url"),
        "app_name": _text("appname") or _text("sourcedisplayname"),
        "md5": md5,
        "type_label": "链接",
    }


# --------------------------------------------------------------------------- #
# 客户端
# --------------------------------------------------------------------------- #
class ChatlogClient:
    """chatlog HTTP API 的轻量封装。

    Args:
        api_base: chatlog 服务根地址，如 `http://127.0.0.1:5030`。        timeout: 单次请求超时秒数。
        page_size: 每页消息条数。
        max_pages: 最多翻页数，防止异常数据导致死循环。
        endpoints: 各接口路径模板，用于兼容不同版本。
        media_dir: 语音 / 图片等媒体文件的本地缓存目录。

    Example:
        >>> client = ChatlogClient("http://127.0.0.1:5030")
        >>> client.get_groups()
        [{'name': 'XX项目工作群', 'id': '...'}]
    """

    #: 健康检查专用超时，远短于业务请求超时（详见 `health`）
    _HEALTH_TIMEOUT = 2.5

    def __init__(
        self,
        api_base: str = "http://127.0.0.1:5030",
        timeout: int = 15,
        page_size: int = 500,
        max_pages: int = 20,
        endpoints: Optional[Dict[str, str]] = None,
        media_dir: Optional[Path] = None,
    ) -> None:
        self.api_base = str(api_base).rstrip("/")
        self.timeout = timeout
        self.page_size = max(1, int(page_size))
        self.max_pages = max(1, int(max_pages))
        self.endpoints: Dict[str, str] = dict(endpoints or self._default_endpoints())
        self.media_dir = Path(media_dir or (Path(__file__).resolve().parents[1] / ".wechat_media"))
        self.media_dir.mkdir(parents=True, exist_ok=True)
        self._session = requests.Session()
        self._session.trust_env = False  # 本地服务不经代理，避免被系统代理拦截
        # 限定连接池规模：控制台会高频轮询 /api/status，
        # 若不设上限，每次请求都可能新建 TCP 连接并堆积 TIME_WAIT 套接字。
        adapter = HTTPAdapter(pool_connections=4, pool_maxsize=8, max_retries=0)
        self._session.mount("http://", adapter)
        self._session.mount("https://", adapter)

    def close(self) -> None:
        """释放底层连接（长期运行的进程退出时调用）。"""
        try:
            self._session.close()
        except Exception:  # pragma: no cover - 关闭失败无需中断调用方
            pass

    # ------------------------------ 工具 ------------------------------ #
    @staticmethod
    def _default_endpoints() -> Dict[str, str]:
        """默认接口路径模板（与 chatlog v4 对齐）。"""
        return {
            "chatlog": "/api/v1/chatlog",
            "chatroom": "/api/v1/chatroom",
            "contact": "/api/v1/contact",
            "session": "/api/v1/session",
            "health": "/health",
            "voice": "/voice/{key}",
            "image": "/image/{key}",
            "video": "/video/{key}",
            "file": "/file/{key}",
            "data": "/data/{path}",
        }

    def _url(self, logical: str, **parts: Any) -> str:
        """根据逻辑名拼出完整 URL。"""
        template = self.endpoints.get(logical) or self._default_endpoints().get(logical, "")
        return f"{self.api_base}{template.format(**parts)}"

    def _request(
        self,
        logical: str,
        params: Optional[Dict[str, Any]] = None,
        timeout: Optional[float] = None,
        **parts: Any,
    ) -> requests.Response:
        """发起 GET 请求并把网络错误统一包装成 ChatlogError。

        Args:
            logical: 逻辑端点名，如 `chatlog` / `health`。
            params: 查询参数。
            timeout: 覆盖默认超时；健康检查等高频调用应传更短的值。
        """
        url = self._url(logical, **parts)
        try:
            return self._session.get(url, params=params, timeout=timeout or self.timeout)
        except requests.exceptions.ConnectionError as exc:
            raise ChatlogError(f"连不上 chatlog 服务 {self.api_base}，请确认已启动 HTTP 服务：{exc}")
        except requests.exceptions.Timeout:
            raise ChatlogError(f"chatlog 请求超时（{timeout or self.timeout}s）：{url}")
        except requests.exceptions.RequestException as exc:
            raise ChatlogError(f"chatlog 请求失败：{exc}")

    # ------------------------------ 健康检查 ------------------------------ #
    def health(self) -> bool:
        """探测服务是否可用，失败返回 False 而不是抛错（供 UI 层做状态点）。

        刻意用远短于业务请求的超时：控制台每 1.5s 轮询一次状态，
        若沿用 15s 的业务超时，chatlog 半死不活时工作线程会被逐个挂住。
        """
        try:
            resp = self._request("health", timeout=self._HEALTH_TIMEOUT)
            return resp.status_code == 200
        except ChatlogError:
            return False

    # ------------------------------ 群聊列表 ------------------------------ #
    def get_groups(self) -> List[Dict[str, Any]]:
        """获取所有群聊列表。

        Returns:
            `[{"name": "群名", "id": "chatroom id", "member_count": 12}, ...]`
            解析失败时返回空列表，而不是抛错中断主流程。
        """
        try:
            resp = self._request("chatroom", {"format": "json", "limit": 500})
        except ChatlogError as exc:
            logger.warning("获取群聊列表失败：%s", exc)
            return []

        if resp.status_code != 200:
            logger.warning("群聊列表接口返回 %s", resp.status_code)
            return []

        try:
            payload = resp.json()
        except ValueError:
            logger.warning("群聊列表不是合法 JSON")
            return []

        rows = payload if isinstance(payload, list) else self._unwrap(payload)
        result: List[Dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            name = _pick(row, "talker_name") or _pick(row, "sender_name") or row.get("name")
            if not name:
                continue
            result.append(
                {
                    "name": str(name),
                    "id": str(_pick(row, "talker") or row.get("id") or ""),
                    "member_count": int(row.get("memberCount") or row.get("member_count") or 0),
                }
            )
        return result

    # ------------------------------ 消息拉取 ------------------------------ #
    def get_messages(
        self,
        group_name: str,
        start_time: datetime,
        end_time: datetime,
        leaders: Optional[Iterable[Dict[str, Any]]] = None,
        keyword: Optional[str] = None,
    ) -> List[Message]:
        """拉取指定群在 [start_time, end_time] 区间内的全部消息。

        Args:
            group_name: 群聊名称（与 chatlog 里的群名一致）。
            start_time: 区间起点（含）。
            end_time: 区间终点（含）。
            leaders: 领导名单，命中后把 Message.is_leader 置 True。
            keyword: 可选关键词过滤，由服务端完成。

        Returns:
            按时间升序排列的 Message 列表。任何单点失败都会被跳过而不是中断。

        Raises:
            ChatlogError: 网络层不可用时抛出（属于致命错误，需由调用方决定是否继续）。
        """
        leader_ids = {str(l.get("id") or "").strip() for l in (leaders or []) if l.get("id")}
        leader_names = {str(l.get("name") or "").strip() for l in (leaders or []) if l.get("name")}

        raw_rows: List[Dict[str, Any]] = []
        offset = 0
        for page in range(self.max_pages):
            params: Dict[str, Any] = {
                "time": f"{start_time:%Y-%m-%d}~{end_time:%Y-%m-%d}",
                "talker": group_name,
                "limit": self.page_size,
                "offset": offset,
                "format": "json",
            }
            if keyword:
                params["keyword"] = keyword

            resp = self._request("chatlog", params)
            if resp.status_code != 200:
                if page == 0:
                    raise ChatlogError(f"拉取聊天记录失败，HTTP {resp.status_code}：{resp.text[:200]}")
                logger.warning("第 %d 页返回 %s，停止翻页", page + 1, resp.status_code)
                break

            try:
                payload = resp.json()
            except ValueError:
                logger.warning("聊天记录不是合法 JSON（第 %d 页）", page + 1)
                break

            rows = payload if isinstance(payload, list) else self._unwrap(payload)
            if not rows:
                break

            raw_rows.extend([r for r in rows if isinstance(r, dict)])
            if len(rows) < self.page_size:
                break
            offset += self.page_size

        logger.info("chatlog 返回 %d 条原始记录（群=%s）", len(raw_rows), group_name)

        messages: List[Message] = []
        for index, row in enumerate(raw_rows):
            try:
                msg = self._to_message(row, group_name, leader_ids, leader_names)
            except Exception as exc:  # 单条解析失败必须跳过，不能拖垮整批
                logger.warning("第 %d 条消息解析失败，已跳过：%s", index, exc)
                continue
            if not msg:
                continue
            if msg.timestamp and not (start_time <= msg.timestamp <= end_time):
                continue
            messages.append(msg)

        messages.sort(key=lambda m: m.sort_key)
        logger.info("过滤后剩余 %d 条有效消息", len(messages))
        return messages

    # ------------------------------ 媒体文件 ------------------------------ #
    def get_voice_file(self, msg_id: str) -> Optional[str]:
        """下载语音文件（chatlog 会把 SILK 实时转成 MP3 直接返回）。

        Args:
            msg_id: 消息 ID，用作 media key。

        Returns:
            本地缓存文件路径；下载失败返回 None。
        """
        return self._download_media("voice", msg_id, suffix=".mp3")

    def get_image_file(self, key: str) -> Optional[str]:
        """下载图片到本地缓存。

        Args:
            key: 图片 media key / md5。

        Returns:
            本地缓存文件路径；失败返回 None。
        """
        if not key:
            return None
        return self._download_media("image", key, suffix=".jpg")

    def _download_media(self, kind: str, key: str, suffix: str) -> Optional[str]:
        """通用媒体下载：先查缓存，未命中再走网络。

        落盘策略是先写 `.part` 临时文件、写完再原子改名。这一点很关键：
        若直接写目标文件，一旦中途断流就会留下一个「非空但残缺」的文件，
        下次调用会被 `size > 0` 判定为有效缓存并直接返回损坏数据，
        而且这类故障不会自愈，用户会一直拿到同一份坏文件。
        """
        if not key:
            return None

        safe_key = re.sub(r"[^0-9A-Za-z_.-]", "", str(key))[:64] or "media"
        target = self.media_dir / f"{kind}_{safe_key}{suffix}"
        try:
            if target.exists() and target.stat().st_size > 0:
                return str(target)
        except OSError:
            return None

        part = target.with_name(target.name + ".part")
        url = self._url(kind, key=quote(str(key), safe=""))
        try:
            with self._session.get(
                url, timeout=self.timeout * 3, stream=True, allow_redirects=True
            ) as resp:
                if resp.status_code != 200:
                    logger.debug("%s 媒体下载失败：HTTP %s（url=%s）", kind, resp.status_code, url)
                    return None
                with open(part, "wb") as fh:
                    for chunk in resp.iter_content(chunk_size=65536):
                        if chunk:
                            fh.write(chunk)
        except requests.exceptions.RequestException as exc:
            logger.debug("%s 媒体下载异常：%s", kind, exc)
            _silent_unlink(part)
            return None
        except OSError as exc:
            logger.warning("%s 媒体写入失败：%s", kind, exc)
            _silent_unlink(part)
            return None

        try:
            if part.stat().st_size <= 0:
                _silent_unlink(part)
                return None
            part.replace(target)  # 原子替换，避免半写文件被并发读到
        except OSError as exc:
            logger.warning("%s 媒体落盘失败：%s", kind, exc)
            _silent_unlink(part)
            return None

        return str(target)

    # ------------------------------ 内部转换 ------------------------------ #
    @staticmethod
    def _unwrap(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
        """从 `{data: [...]}` / `{items: [...]}` / `{messages: [...]}` 里取出列表本体。"""
        for key in ("data", "items", "messages", "records", "list", "rows", "results"):
            value = payload.get(key)
            if isinstance(value, list):
                return [r for r in value if isinstance(r, dict)]
        return []

    def _to_message(
        self,
        row: Dict[str, Any],
        group_name: str,
        leader_ids: set,
        leader_names: set,
    ) -> Optional[Message]:
        """把一行 API 数据转成 Message，时间缺失则丢弃该条。"""
        timestamp = _parse_time(_pick(row, "time"))
        if not timestamp:
            return None

        msg_id = str(_pick(row, "id") or _pick(row, "seq") or timestamp.strftime("%Y%m%d%H%M%S"))
        try:
            seq = int(_pick(row, "seq") or 0)
        except (TypeError, ValueError):
            seq = 0

        sender_id = str(_pick(row, "sender") or "")
        sender_name = str(
            _pick(row, "sender_name") or _pick(row, "talker_name") or sender_id or "未知"
        )

        raw_content = str(_pick(row, "content") or "")
        msg_type = normalize_type(_pick(row, "type"), _pick(row, "sub_type"))
        content = raw_content
        note = ""

        # ---- 媒体 key 优先从 contents map 里取（chatlog v4 把图片/语音的
        #      md5 放在 `contents.md5`，而不是顶层字段；漏读会导致拿不到媒体）---- #
        contents = row.get("contents")
        media_key = ""
        voice_key = ""
        if isinstance(contents, dict):
            # 图片/视频的 key 在 contents.md5 / rawmd5；语音的 key 在 contents.voice
            voice_key = str(contents.get("voice") or "")
            for key in ("md5", "rawmd5", "mediaKey", "media_key", "cdnUrl", "key"):
                value = contents.get(key)
                if value:
                    media_key = str(value)
                    break
        if not media_key:
            media_key = str(_pick(row, "media_key") or "")

        # ---- 复合消息：拆 XML 拿标题 / 链接 ---- #
        if msg_type == TYPE_ARTICLE and raw_content.lstrip().startswith("<"):
            parsed = parse_appmsg_xml(raw_content)
            if parsed.get("url"):
                content = parsed.get("url") or ""
                title = parsed.get("title") or ""
                raw_content = parsed.get("url") or raw_content
                note = f"链接标题：{title}" if title else ""
            else:
                content = _clean_text(raw_content)
        elif msg_type == TYPE_ARTICLE:
            # chatlog v4 把公众号/链接的 title/url/desc 放在 contents map 里，
            # 而不是 content 字段；优先从 contents 恢复链接信息。
            link_title = ""
            link_url = ""
            link_desc = ""
            if isinstance(contents, dict):
                link_title = str(contents.get("title") or "").strip()
                link_url = str(contents.get("url") or "").strip()
                link_desc = str(contents.get("desc") or "").strip()
            if link_url:
                content = link_url
                raw_content = link_url
                note = f"链接标题：{link_title}" if link_title else ""
            elif link_title:
                content = link_title
                raw_content = link_title
                note = f"链接：{link_title}"
            else:
                content = _clean_text(raw_content)
        elif msg_type == TYPE_IMAGE:
            key = media_key
            md5_match = _MD5_RE.search(raw_content)
            if not key and md5_match:
                key = md5_match.group(0)
            content = ""
            raw_content = key or _clean_text(raw_content)
        elif msg_type == TYPE_VOICE:
            content = ""
            key = voice_key or media_key
            raw_content = key or msg_id
        else:
            content = _clean_text(raw_content)

        is_leader = bool((sender_id and sender_id in leader_ids) or (sender_name in leader_names))

        message = Message(
            msg_id=msg_id,
            group_name=str(group_name),
            sender_name=sender_name,
            sender_id=sender_id,
            timestamp=timestamp,
            msg_type=msg_type,
            content=content or "",
            raw_content=str(raw_content or ""),
            is_leader=is_leader,
            seq=seq,
            note=note,
        )
        if msg_type == TYPE_IMAGE and raw_content:
            message.add_attachment("image", key=str(raw_content))
        if msg_type == TYPE_VOICE:
            message.add_attachment("voice", key=str(raw_content))
        return message


# --------------------------------------------------------------------------- #
# 工厂：从配置字典构造客户端
# --------------------------------------------------------------------------- #
def _silent_unlink(path: Path) -> None:
    """删除文件但不抛错（清理临时文件用，失败不影响主流程）。"""
    try:
        path.unlink()
    except OSError:
        pass


def build_client_from_config(cfg: Dict[str, Any]):
    """按配置构造采集客户端。

    优先级：settings.yaml 里 `chatlog.decrypted_dir` 存在且指向有效的
    已导出数据库目录时，使用 SQLite 直读后端（SqliteClient）；否则回落到
    chatlog HTTP 服务。
    """
    from collector.sqlite_client import build_sqlite_client_from_config

    sqlite_client = build_sqlite_client_from_config(cfg)
    if sqlite_client is not None and sqlite_client.health():
        return sqlite_client

    endpoints = dict(ChatlogClient._default_endpoints())
    endpoints.update((cfg.get("chatlog", {}) or {}).get("endpoints", {}) or {})
    return ChatlogClient(
        api_base=(cfg.get("chatlog", {}) or {}).get("api_base", "http://127.0.0.1:5030"),
        timeout=int((cfg.get("chatlog", {}) or {}).get("timeout", 15)),
        page_size=int((cfg.get("chatlog", {}) or {}).get("page_size", 500)),
        max_pages=int((cfg.get("chatlog", {}) or {}).get("max_pages", 20)),
        endpoints=endpoints,
    )


def ffmpeg_available() -> bool:
    """检测系统是否可用 ffmpeg（仅用于兜底转码，非必需）。"""
    try:
        proc = subprocess.run(
            ["ffmpeg", "-version"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
        return proc.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False
