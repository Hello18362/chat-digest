"""SQLite 直读采集器 —— 从本机导出的微信 4.x 数据库读取群聊消息。

本模块不负责「导出」或「解密」数据库，只读取已经落到本地目录的明文
SQLite 文件，向流水线提供与 ChatlogClient 完全相同的接口
（health / get_groups / get_messages）。

目录结构约定：
    <decrypted_dir>/message/message_0.db   消息（每会话一张 Msg_<md5> 表）
    <decrypted_dir>/contact/contact.db     联系人（昵称 / 备注 / 群信息）

映射规则（已在真实数据上验证）：
    表名 = "Msg_" + md5(会话 wxid)
    发送者 = real_sender_id → Name2Id.rowid → user_name → contact 昵称
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from collector.models import (
    TYPE_ARTICLE,
    TYPE_IMAGE,
    TYPE_SYSTEM,
    TYPE_TEXT,
    TYPE_VIDEO,
    TYPE_VOICE,
    Message,
)

_TYPE_MAP = {
    1: TYPE_TEXT,
    3: TYPE_IMAGE,
    34: TYPE_VOICE,
    43: TYPE_VIDEO,
    47: TYPE_TEXT,      # 表情，按文本占位
    49: TYPE_ARTICLE,   # 复合消息（链接 / 文件 / 转发）
    57: TYPE_TEXT,      # 引用消息，取 <title> 作为正文
    10000: TYPE_SYSTEM,
    10002: TYPE_SYSTEM,
    51: TYPE_TEXT,      # 状态消息
}

_MEDIA_RE = re.compile(r"([0-9a-f]{32})", re.I)
_ZSTD_MAGIC = b"\x28\xb5\x2f\xfd"


def _decode_content(content: Any) -> str:
    """WCDB 压缩内容解码：zstd 魔数解压 + UTF-8；失败返回空串。"""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    raw = bytes(content)
    if raw[:4] == _ZSTD_MAGIC:
        try:
            import zstandard as _zstd

            raw = _zstd.ZstdDecompressor().decompressobj().decompress(raw)
        except Exception:
            return ""
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return ""


class SqliteClient:
    """解密 SQLite 直读客户端，接口与 ChatlogClient 对齐。"""

    def __init__(self, decrypted_dir: str | Path) -> None:
        """Args: decrypted_dir — 已导出的数据库目录（含 message/ contact/ 子目录）。"""
        self.base = Path(decrypted_dir)
        self._contact_cache: Optional[Dict[str, Dict[str, str]]] = None

    # ------------------------------------------------------------------ #
    # 基础
    # ------------------------------------------------------------------ #
    def _open(self, sub: str) -> Optional[sqlite3.Connection]:
        """打开子数据库，失败返回 None。"""
        path = self.base / sub
        if not path.exists():
            return None
        try:
            conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
            conn.row_factory = sqlite3.Row
            return conn
        except sqlite3.Error:
            return None

    def health(self) -> bool:
        """message_0.db 存在且可打开即视为在线。"""
        conn = self._open("message/message_0.db")
        if conn is None:
            return False
        try:
            conn.execute("SELECT 1").fetchone()
            return True
        except sqlite3.Error:
            return False
        finally:
            conn.close()

    # ------------------------------------------------------------------ #
    # 联系人
    # ------------------------------------------------------------------ #
    def _contacts(self) -> Dict[str, Dict[str, str]]:
        """wxid → {nick, remark, alias}。只取一次，进程内缓存。"""
        if self._contact_cache is not None:
            return self._contact_cache
        cache: Dict[str, Dict[str, str]] = {}
        conn = self._open("contact/contact.db")
        if conn is not None:
            try:
                for row in conn.execute(
                    "SELECT username, nick_name, remark, alias FROM contact"
                ):
                    cache[row["username"]] = {
                        "nick": (row["nick_name"] or "").strip(),
                        "remark": (row["remark"] or "").strip(),
                        "alias": (row["alias"] or "").strip(),
                    }
            except sqlite3.Error:
                pass
            finally:
                conn.close()
        self._contact_cache = cache
        return cache

    @staticmethod
    def _display(wxid: str, info: Optional[Dict[str, str]]) -> str:
        """挑最友好的显示名：备注 > 昵称 > 别名 > wxid。"""
        if info:
            for key in ("remark", "nick", "alias"):
                if info.get(key):
                    return info[key]
        return wxid

    # ------------------------------------------------------------------ #
    # 群列表
    # ------------------------------------------------------------------ #
    def get_groups(self) -> List[Dict[str, Any]]:
        """列出有消息记录的群聊：[{name, id}]，按名称排序。"""
        conn = self._open("message/message_0.db")
        if conn is None:
            return []
        contacts = self._contacts()
        tables = {
            r["name"]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE name LIKE 'Msg_%'"
            )
        }
        groups: List[Dict[str, Any]] = []
        try:
            for row in conn.execute(
                "SELECT user_name FROM Name2Id WHERE user_name LIKE '%@chatroom'"
            ):
                wxid = row["user_name"]
                if f"Msg_{hashlib.md5(wxid.encode()).hexdigest()}" not in tables:
                    continue
                groups.append(
                    {"name": self._display(wxid, contacts.get(wxid)), "id": wxid}
                )
        except sqlite3.Error:
            pass
        finally:
            conn.close()
        groups.sort(key=lambda g: g["name"])
        return groups

    # ------------------------------------------------------------------ #
    # 消息
    # ------------------------------------------------------------------ #
    def _resolve_wxid(self, group_name: str) -> Optional[str]:
        """把群名称（或 wxid）解析成 wxid。"""
        contacts = self._contacts()
        target = group_name.strip().lower()
        if target.endswith("@chatroom"):
            return group_name.strip()
        for wxid, info in contacts.items():
            if not wxid.endswith("@chatroom"):
                continue
            for v in info.values():
                if v and v.lower() == target:
                    return wxid
        return None

    def get_messages(
        self,
        group_name: str,
        start_time: datetime,
        end_time: datetime,
        leaders: Optional[Iterable[Dict[str, Any]]] = None,
        keyword: Optional[str] = None,
    ) -> List[Message]:
        """拉取指定群在 [start_time, end_time] 内的消息，按时间升序。"""
        wxid = self._resolve_wxid(group_name)
        if wxid is None:
            return []
        table = f"Msg_{hashlib.md5(wxid.encode()).hexdigest()}"

        conn = self._open("message/message_0.db")
        if conn is None:
            return []
        try:
            if not conn.execute(
                "SELECT 1 FROM sqlite_master WHERE name = ?", (table,)
            ).fetchone():
                return []

            id2name = {
                r["rowid"]: r["user_name"]
                for r in conn.execute("SELECT rowid, user_name FROM Name2Id")
            }
            contacts = self._contacts()
            leader_ids = {
                str(x.get("name", "")).strip().lower() for x in (leaders or []) if x
            }

            rows = conn.execute(
                f"SELECT local_id, local_type, sort_seq, real_sender_id, "
                f"create_time, message_content FROM {table} "
                f"WHERE create_time >= ? AND create_time <= ? "
                f"ORDER BY sort_seq ASC",
                (int(start_time.timestamp()), int(end_time.timestamp())),
            ).fetchall()

            messages: List[Message] = []
            for row in rows:
                raw_lt = int(row["local_type"] or 0)
                ltype = raw_lt & 0xFFFF
                # 微信 4.x 的 local_type 是 64 位：高 32 位是真正的消息子类型。
                # 高位 0x3E = 拍一拍（"A 拍了拍 B"），属无意义互动，直接跳过，
                # 避免它混进总结当成任务/链接。
                high = raw_lt >> 32
                if high == 0x3E:
                    continue
                msg_type = _TYPE_MAP.get(ltype, TYPE_SYSTEM)
                raw = _decode_content(row["message_content"]).strip()
                # 双保险：即使高位对不上，内容本身含「拍了拍」也跳过
                if "拍了拍" in raw:
                    continue
                sender_wxid = id2name.get(int(row["real_sender_id"] or 0), "")
                info = contacts.get(sender_wxid)
                sender_name = self._display(sender_wxid, info) if sender_wxid else "未知"
                is_leader = bool(sender_name) and sender_name.lower() in leader_ids

                content, note = self._render(msg_type, raw)
                if keyword and keyword.lower() not in (content + note).lower():
                    continue

                attachments: List[Dict[str, str]] = []
                if msg_type == TYPE_IMAGE:
                    key = _MEDIA_RE.search(raw)
                    if key:
                        attachments.append({"kind": "image", "key": key.group(0)})
                elif msg_type == TYPE_ARTICLE and content:
                    attachments.append({"kind": "url", "key": content})

                messages.append(
                    Message(
                        msg_id=f"{wxid}_{row['local_id']}",
                        group_name=group_name,
                        sender_name=sender_name,
                        sender_id=sender_wxid,
                        timestamp=datetime.fromtimestamp(int(row["create_time"])),
                        msg_type=msg_type,
                        content=content,
                        raw_content=raw[:2000],
                        is_leader=is_leader,
                        attachments=attachments,
                        seq=int(row["sort_seq"] or 0),
                        note=note,
                    )
                )
            return messages
        except sqlite3.Error:
            return []
        finally:
            conn.close()

    @staticmethod
    def _render(msg_type: str, raw: str) -> tuple[str, str]:
        """把原始内容渲染成 (content, note)。"""
        if msg_type == TYPE_TEXT:
            # 引用消息（49/57 XML）：用户回复文本在 <title>
            m = re.search(r"<title>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>", raw, re.S)
            if raw.lstrip().startswith("<") and m:
                return m.group(1).strip(), "[引用消息]"
            return raw, ""
        if msg_type == TYPE_IMAGE:
            key = _MEDIA_RE.search(raw)
            return "", f"[图片] {key.group(0) if key else ''}".strip()
        if msg_type == TYPE_VOICE:
            return "", "[语音]"
        if msg_type == TYPE_VIDEO:
            return "", "[视频]"
        if msg_type == TYPE_ARTICLE:
            title = re.search(r"<title>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>", raw, re.S)
            url = re.search(r"<url>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</url>", raw, re.S)
            note = f"链接标题：{title.group(1)}" if title else ""
            return (url.group(1) if url else ""), note
        return raw[:500], ""


def build_sqlite_client_from_config(cfg: Dict[str, Any]) -> Optional[SqliteClient]:
    """按 settings.yaml 的 chatlog.decrypted_dir 构造；未配置返回 None。"""
    chatlog_cfg = cfg.get("chatlog", {}) or {}
    data_dir = chatlog_cfg.get("decrypted_dir") or ""
    if not data_dir:
        return None
    return SqliteClient(data_dir)
