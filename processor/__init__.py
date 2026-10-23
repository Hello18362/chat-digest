"""多模态加工层：把原始消息加工成「可读文本」。

三个加工器彼此独立，任一环节失败都只影响单条消息本身，
由 pipeline 统一汇总降级结果。
"""

from __future__ import annotations

from processor.article_fetch import ArticleFetcher, extract_urls, is_wechat_article
from processor.image_vision import (
    DoubaoVisionProvider,
    DummyVisionProvider,
    VisionProvider,
)
from processor.merge import build_chat_content, filter_leaders_only, merge_messages
from processor.voice_asr import VoiceTranscriber

__all__ = [
    "ArticleFetcher",
    "extract_urls",
    "is_wechat_article",
    "VisionProvider",
    "DummyVisionProvider",
    "DoubaoVisionProvider",
    "VoiceTranscriber",
    "merge_messages",
    "filter_leaders_only",
    "build_chat_content",
]
