"""统一消息数据结构。

整个项目里只有一种消息中间表示（IR）：任何来源（chatlog HTTP API、未来可能的
本地数据库）都先映射到这个 dataclass，再由 processor 逐条加工。
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any, Dict, Optional

# --------------------------------------------------------------------------- #
# 消息类型常量（对外统一用这套字符串，内部各来源的数值类型都会映射过来）
# --------------------------------------------------------------------------- #
TYPE_TEXT = "text"
TYPE_VOICE = "voice"
TYPE_IMAGE = "image"
TYPE_ARTICLE = "article"
TYPE_VIDEO = "video"
TYPE_FILE = "file"
TYPE_SYSTEM = "system"
TYPE_UNKNOWN = "unknown"

# 微信原始 MsgType -> 本项目类型
WX_TYPE_MAP: Dict[int, str] = {
    1: TYPE_TEXT,
    3: TYPE_IMAGE,
    34: TYPE_VOICE,
    43: TYPE_VIDEO,
    47: TYPE_UNKNOWN,      # 表情包 / 大表情
    49: TYPE_ARTICLE,      # 复合消息（公众号链接、文件、引用等），按 subType 细分
    10000: TYPE_SYSTEM,
}

# 各类型在报告 / 合并文本里的展示标签
TYPE_LABEL: Dict[str, str] = {
    TYPE_TEXT: "文字",
    TYPE_VOICE: "语音",
    TYPE_IMAGE: "图片",
    TYPE_ARTICLE: "链接",
    TYPE_VIDEO: "视频",
    TYPE_FILE: "文件",
    TYPE_SYSTEM: "系统",
    TYPE_UNKNOWN: "其他",
}

PLACEHOLDER_FAIL = "[处理失败]"
PLACEHOLDER_VOICE = "[语音]"
PLACEHOLDER_IMAGE = "[图片]"


@dataclass
class Message:
    """一条群聊消息的统一表示。

    Attributes:
        msg_id: 消息唯一 ID（chatlog 返回的 id / seq）。
        group_name: 所属群聊名称。
        sender_name: 发送人昵称 / 备注名。
        sender_id: 发送人 wxid，用于精确匹配领导名单。
        timestamp: 消息发生的本地时间。
        msg_type: 见本模块的 TYPE_* 常量。
        content: **处理后**的可读文字（语音转写结果、图片描述、文章正文等）。
        raw_content: 原始内容（语音文件路径、图片 md5、文章 URL 等）。
        is_leader: 是否命中领导名单，为 True 时合并文本里会打 [领导] 标记。
        attachments: 附加信息列表（字典形式，如 {"kind": "image", "key": "..."}）。
        seq: 同一秒内多条消息时的稳定排序键。
        note: 加工过程中的备注（降级原因、失败原因等），用于排障。
    """

    msg_id: str
    group_name: str
    sender_name: str
    sender_id: str
    timestamp: datetime
    msg_type: str = TYPE_TEXT
    content: str = ""
    raw_content: str = ""
    is_leader: bool = False
    attachments: list = field(default_factory=list)
    seq: int = 0
    note: str = ""

    # ----------------------- 派生属性 ----------------------- #
    @property
    def type_label(self) -> str:
        """返回中文类型标签，未知类型回落到「其他」。"""
        return TYPE_LABEL.get(self.msg_type, TYPE_LABEL[TYPE_UNKNOWN])

    @property
    def sort_key(self) -> tuple:
        """稳定排序键：先按时间，再按 seq，最后按 id 保证确定性。"""
        return (self.timestamp, self.seq, self.msg_id)

    @property
    def time_text(self) -> str:
        """`14:30` 形式的短时间，用于聊天流展示。"""
        return self.timestamp.strftime("%H:%M") if self.timestamp else "--:--"

    @property
    def full_time_text(self) -> str:
        """`2026-10-22 14:30` 形式的完整时间。"""
        return self.timestamp.strftime("%Y-%m-%d %H:%M") if self.timestamp else "未知时间"

    @property
    def failed(self) -> bool:
        """本条消息在加工环节是否失败过。"""
        return PLACEHOLDER_FAIL in (self.content or "")

    def add_attachment(self, kind: str, **fields: Any) -> None:
        """登记一个附件信息到 attachments 列表。"""
        payload = {"kind": kind}
        payload.update(fields)
        self.attachments.append(payload)

    def to_dict(self) -> Dict[str, Any]:
        """转成可 JSON 序列化的字典（时间转 ISO 字符串）。"""
        data = asdict(self)
        if isinstance(self.timestamp, datetime):
            data["timestamp"] = self.timestamp.isoformat(timespec="seconds")
        return data

    def __str__(self) -> str:  # pragma: no cover - 便于调试
        flag = " [领导]" if self.is_leader else ""
        preview = (self.content or "").replace("\n", " ")[:40]
        return f"[{self.full_time_text}] {self.sender_name}{flag}: {preview}"


def normalize_type(value: Any, sub_type: Any = None) -> str:
    """把来源侧的类型（数值或字符串）归一化为本项目的 TYPE_* 常量。

    Args:
        value: 如 1 / 3 / 34 / 49 / 10000，或 "text" / "图片" / "语音"。
        sub_type: 微信 subType，用于区分 type=49 下的具体形态。

    Returns:
        归一化后的类型字符串，无法识别时返回 TYPE_UNKNOWN。
    """
    if value is None:
        return TYPE_UNKNOWN

    # 数值型
    try:
        numeric = int(value)
        mapped = WX_TYPE_MAP.get(numeric)
        if mapped == TYPE_ARTICLE:
            return _refine_appmsg(sub_type)
        if mapped:
            return mapped
    except (TypeError, ValueError):
        pass

    # 字符串型
    text = str(value).strip().lower()
    if not text:
        return TYPE_UNKNOWN
    table = [
        (("text", "文本", "文字", "textmsg"), TYPE_TEXT),
        (("image", "图片", "img", "picture"), TYPE_IMAGE),
        (("voice", "语音", "audio", "silk"), TYPE_VOICE),
        (("video", "视频", "mp4"), TYPE_VIDEO),
        (("file", "文件"), TYPE_FILE),
        (("system", "系统", "sysmsg", "revoke"), TYPE_SYSTEM),
        (("article", "链接", "分享", "share", "url", "appmsg", "公众号"), TYPE_ARTICLE),
    ]
    for keywords, result in table:
        if any(k in text for k in keywords):
            return result
    return TYPE_UNKNOWN


def _refine_appmsg(sub_type: Any) -> str:
    """细分 type=49 的复合消息。

    微信 subType：5=公众号链接/网页，19=聊天记录合并转发，51=视频号，
    6/8=文件，57=引用消息，63=转账。这里只挑出本项目关心的三类。

    Args:
        sub_type: 原始 subType 值。

    Returns:
        TYPE_ARTICLE / TYPE_FILE / TYPE_UNKNOWN。
    """
    try:
        numeric = int(sub_type)
    except (TypeError, ValueError):
        return TYPE_ARTICLE
    if numeric == 5:
        return TYPE_ARTICLE
    if numeric in (6, 8):
        return TYPE_FILE
    return TYPE_UNKNOWN
