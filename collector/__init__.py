"""采集层导出。

复用方式：`from collector import Message, normalize_type`
"""

from __future__ import annotations

from collector.chatlog_client import ChatlogClient, ChatlogError
from collector.models import (
    TYPE_ARTICLE,
    TYPE_FILE,
    TYPE_IMAGE,
    TYPE_SYSTEM,
    TYPE_TEXT,
    TYPE_UNKNOWN,
    TYPE_VIDEO,
    TYPE_VOICE,
    TYPE_LABEL,
    PLACEHOLDER_FAIL,
    PLACEHOLDER_IMAGE,
    PLACEHOLDER_VOICE,
    Message,
    normalize_type,
)

__all__ = [
    "Message",
    "normalize_type",
    "TYPE_TEXT",
    "TYPE_VOICE",
    "TYPE_IMAGE",
    "TYPE_ARTICLE",
    "TYPE_VIDEO",
    "TYPE_FILE",
    "TYPE_SYSTEM",
    "TYPE_UNKNOWN",
    "TYPE_LABEL",
    "PLACEHOLDER_FAIL",
    "PLACEHOLDER_IMAGE",
    "PLACEHOLDER_VOICE",
    "ChatlogClient",
    "ChatlogError",
]
