"""运行指标计算。

把「原始消息列表」折算成报告头部要展示的一组数字。
所有函数都是纯函数（输入不变、无副作用），方便单测与复用。
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional

from collector.models import Message

PLACEHOLDER_FAIL = "[处理失败]"


def summarize_messages(messages: Iterable[Message]) -> Dict[str, Any]:
    """汇总一条消息列表的统计指标。

    Args:
        messages: 任意可迭代的 Message。

    Returns:
        包含总数、类型分布、领导发言数、发言人 Top、时间跨度等的字典。
        空列表返回结构一致的全零结果（调用方不必判空）。
    """
    items: List[Message] = list(messages)

    total = len(items)
    failed = sum(1 for m in items if PLACEHOLDER_FAIL in (m.content or ""))
    voice = sum(1 for m in items if m.msg_type == "voice")
    image = sum(1 for m in items if m.msg_type == "image")
    article = sum(1 for m in items if m.msg_type == "article")
    text = sum(1 for m in items if m.msg_type == "text")
    leaders = [m for m in items if m.is_leader]

    type_dist: Counter = Counter(m.msg_type for m in items)
    speaker_dist: Counter = Counter(m.sender_name or "未知" for m in items)

    stamps: List[datetime] = [m.timestamp for m in items if m.timestamp]
    first_at = min(stamps) if stamps else None
    last_at = max(stamps) if stamps else None

    chars = sum(len(m.content or "") for m in items)

    return {
        "total": total,
        "leader_count": len(leaders),
        "leader_ratio": round(len(leaders) / total, 4) if total else 0.0,
        "failed": failed,
        "success_rate": round((total - failed) / total, 4) if total else 1.0,
        "type_dist": dict(type_dist),
        "counts": {
            "text": text,
            "voice": voice,
            "image": image,
            "article": article,
        },
        "top_speakers": [
            {"name": name, "count": cnt} for name, cnt in speaker_dist.most_common(8)
        ],
        "first_at": first_at,
        "last_at": last_at,
        "span_minutes": _span_minutes(first_at, last_at),
        "chars": chars,
    }


def _span_minutes(first: Optional[datetime], last: Optional[datetime]) -> int:
    """计算消息首尾时间跨度（分钟），缺失时返回 0。"""
    if not first or not last:
        return 0
    return max(0, int((last - first).total_seconds() // 60))


def leader_names(messages: Iterable[Message]) -> List[str]:
    """按发言量倒序返回本次实际发言的领导名字。"""
    counter = Counter(m.sender_name for m in messages if m.is_leader and m.sender_name)
    return [name for name, _ in counter.most_common()]


def format_duration(minutes: int) -> str:
    """把跨度分钟数格式化成人类可读文本：`128 -> 2小时8分钟`。"""
    if minutes <= 0:
        return "—"
    hours, mins = divmod(minutes, 60)
    if hours and mins:
        return f"{hours}小时{mins}分钟"
    if hours:
        return f"{hours}小时"
    return f"{mins}分钟"
