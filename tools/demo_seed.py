"""生成演示报告：用合成数据跑完整链路，不碰任何真实聊天记录。

两种模式：

    python tools/demo_seed.py                  # 离线 file_output 模式，产物是待处理文件
    python tools/demo_seed.py --sample-summary # 额外塞一份示例总结，任务卡片/决策都能看到

第二种模式里的总结文案是**手写的示例**，不是真的模型输出，只为演示渲染效果。
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from collector.chatlog_client import ChatlogClient  # noqa: E402
from collector.models import TYPE_ARTICLE, TYPE_IMAGE, TYPE_TEXT, TYPE_VOICE, Message  # noqa: E402
from core.config import load_settings  # noqa: E402
from core.pipeline import RunOptions, run_pipeline  # noqa: E402

# (分钟偏移, 发言人, wxid, 内容, 类型, 是否领导)
SCENES = [
    (
        "2026-10-22",
        "XX项目工作群",
        [
            (0, "张总", "wxid_zhang", "接口联调这周必须闭环，别拖到下周。", TYPE_TEXT, True),
            (4, "王五", "wxid_wang", "还差两个字段没对齐，今天下午补完。", TYPE_TEXT, False),
            (27, "李四", "wxid_lisi", "[语音]", TYPE_VOICE, False),
            (31, "王五", "wxid_wang", "这是目前的联调报错截图，[图片：异常堆栈截屏]", TYPE_IMAGE, False),
            (75, "张总", "wxid_zhang", "看到群里的方案文档了，按第二版推进。", TYPE_TEXT, True),
            (131, "李四", "wxid_lisi", "把压测报告发群里了，[链接：https://mp.weixin.qq.com/s/demo-perf]", TYPE_ARTICLE, False),
            (188, "张总", "wxid_zhang", "周三上午十点过一下方案，相关同学都参加。", TYPE_TEXT, True),
            (203, "王五", "wxid_wang", "收到，我先把冒烟用例补齐。", TYPE_TEXT, False),
        ],
    ),
    (
        "2026-10-22",
        "部门周会群",
        [
            (0, "陈总", "wxid_chen", "这周的周会改到下午三点，会议室 B。", TYPE_TEXT, True),
            (18, "赵敏", "wxid_zhao", "上周的复盘文档已经归档了。", TYPE_TEXT, False),
            (44, "陈总", "wxid_chen", "Q4 的人力缺口今天下班前给我一个数字。", TYPE_TEXT, True),
            (96, "赵敏", "wxid_zhao", "[图片：人力盘点表]", TYPE_IMAGE, False),
        ],
    ),
    (
        "2026-10-21",
        "XX项目工作群",
        [
            (0, "王五", "wxid_wang", "灰度环境的配置已经更新。", TYPE_TEXT, False),
            (22, "张总", "wxid_zhang", "压测目标明确到 P99 350ms，不要含糊。", TYPE_TEXT, True),
            (40, "李四", "wxid_lisi", "会场页那条链路要不要一起压？", TYPE_TEXT, False),
            (41, "张总", "wxid_zhang", "要，去年就是这里出的问题。", TYPE_TEXT, True),
            (88, "李四", "wxid_lisi", "[语音]", TYPE_VOICE, False),
        ],
    ),
    (
        "2026-10-21",
        "部门周会群",
        [
            (0, "陈总", "wxid_chen", "上季度的问题清单再过一遍。", TYPE_TEXT, True),
            (15, "赵敏", "wxid_zhao", "[链接：https://mp.weixin.qq.com/s/demo-retro]", TYPE_ARTICLE, False),
            (36, "陈总", "wxid_chen", "归档完同步到知识库。", TYPE_TEXT, True),
        ],
    ),
]


# 示例总结（手写，不是真实模型输出）：用于在没配 API Key 的机器上
# 也能看到任务卡片、领导决策这些渲染效果。
SAMPLE_SUMMARIES = {
    "XX项目工作群": """## 今日工作任务

☐ [紧急] 完成接口联调并输出联调结论 — 王五 — 本周五 18:00 — 来源(张总 09:00)
☐ [重要] 补齐两个未对齐字段的联调用例 — 王五 — 今天 18:00 — 来源(王五 09:04)
☐ [重要] 组织方案第二版评审会 — 未指定 — 周三 10:00 — 来源(张总 12:08)

## 领导决策

- 方案按第二版推进 —— 张总确认，第一版不再讨论
- 联调必须本周闭环 —— 不允许拖到下周，直接影响压测排期

## 需我跟进

- 核对压测报告里 P99 的统计口径 — 今天下班前
- 确认方案评审会参会名单 — 周二""",
    "部门周会群": """## 今日工作任务

☐ [重要] 给出 Q4 人力缺口的具体数字 — 未指定 — 今天 18:00 — 来源(陈总 10:00)
☐ [常规] 更新周会时间并通知全员 — 未指定 — 无明确截止 — 来源(陈总 09:00)

## 领导决策

- 周会改到下午三点，会议室 B —— 陈总通知

## 需我跟进

- 人力盘点表补齐后同步到知识库 — 明天""",
}


class _DemoSummarizer:
    """把示例总结原样返回，替代真实模型，用于离线演示。"""

    def __init__(self, summary: str) -> None:
        self._summary = summary
        self.last_meta: dict = {}
        self.last_output_path = None

    def summarize(self, chat_content: str, prompt_template: str = "") -> str:
        """返回预置的示例总结。"""
        return self._summary


def build(day: str, group: str, rows) -> list:
    base = datetime.strptime(day, "%Y-%m-%d").replace(hour=9, minute=0)
    return [
        Message(
            msg_id=f"{day.replace('-', '')}{i:03d}",
            group_name=group,
            sender_name=who,
            sender_id=sid,
            timestamp=base + timedelta(minutes=offset),
            msg_type=kind,
            content=content,
            raw_content=content,
            is_leader=leader,
            seq=i,
        )
        for i, (offset, who, sid, content, kind, leader) in enumerate(rows)
    ]


def main(argv: list | None = None) -> int:
    """生成演示报告；`--sample-summary` 时改用示例总结。"""
    args = list(sys.argv[1:] if argv is None else argv)
    sample = "--sample-summary" in args

    cfg = load_settings()
    ChatlogClient.health = lambda self: True

    pipeline_mod = None
    if sample:
        import core.pipeline as pipeline_mod

        print("  [i] 使用示例总结（手写文案，非真实模型输出）\n")

    for day, group, rows in SCENES:
        messages = build(day, group, rows)
        ChatlogClient.get_messages = lambda self, *a, _m=messages, **k: _m
        if sample and pipeline_mod is not None:
            pipeline_mod.build_summarizer = lambda _cfg, g=group: _DemoSummarizer(  # type: ignore[assignment]
                SAMPLE_SUMMARIES.get(g, "")
            )
        result = run_pipeline(
            cfg,
            RunOptions(group=group, date=day, formats=["markdown", "html"]),
            progress=lambda s, p, d: None,
        )
        md = result.markdown_path.read_text(encoding="utf-8")
        page = result.html_path.read_text(encoding="utf-8")
        assert md.count("## 总结") == 1, f"{group} 重复标题"
        assert "<h2>总结</h2>" not in page, f"{group} HTML 重复标题"
        print(
            f"  {group} {day}: {result.stats['total']} 条 "
            f"(领导 {result.stats['leader_count']}) -> {result.html_path.name}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
