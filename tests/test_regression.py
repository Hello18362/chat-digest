"""Chat Digest 回归测试（纯标准库，无需 pytest）。

运行方式::

    python tests/test_regression.py          # 或在项目根目录执行
    python -m unittest discover -s tests -v

这里覆盖的都是曾经真实咬到人的缺陷，每一条都对应一个具体的故障场景：
    · 月初 / 元旦跑 `--date yesterday` 得到错误日期甚至崩溃
    · 报告里出现两个「## 总结」标题（Markdown 与 HTML 两处渲染出口）
    · 控制台里换一次模板，把共享配置写脏，之后所有运行都串味
    · 媒体下载中断留下残缺文件，之后每次都命中这份坏缓存
    · 任务注册表回收算法写错，长期运行持续膨胀
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from collector.models import Message, TYPE_TEXT  # noqa: E402
from collector.chatlog_client import ChatlogClient  # noqa: E402
from core.pipeline import PipelineError, RunOptions, parse_date, run_pipeline  # noqa: E402
from output.html import render_report  # noqa: E402
from output.markdown import build_markdown, strip_summary_heading  # noqa: E402

REAL_DATETIME = datetime


def frozen(target: datetime):
    """返回一个替换 datetime 的类，让 `datetime.now()` 固定在指定时刻。"""

    class FrozenDatetime(REAL_DATETIME):
        @classmethod
        def now(cls, tz=None):  # noqa: D102
            return target if tz is None else target.astimezone(tz)

    return FrozenDatetime


# --------------------------------------------------------------------------- #
# 日期解析
# --------------------------------------------------------------------------- #
class TestParseDate(unittest.TestCase):
    """`parse_date` 的边界情况，重点是跨月 / 跨年。"""

    def test_today_is_midnight(self):
        mid = parse_date("today")
        self.assertEqual((mid.hour, mid.minute, mid.second, mid.microsecond), (0, 0, 0, 0))

    def test_yesterday_on_first_day_of_month(self):
        # 曾经的实现在每月 1 号会返回「当天」而不是上个月最后一天
        with mock.patch("core.pipeline.datetime", frozen(REAL_DATETIME(2026, 10, 1, 9, 30))):
            self.assertEqual(parse_date("yesterday"), REAL_DATETIME(2026, 9, 30))

    def test_yesterday_on_new_year_day(self):
        # 曾经的实现在这里会抛 ValueError: day is out of range for month
        with mock.patch("core.pipeline.datetime", frozen(REAL_DATETIME(2026, 1, 1, 0, 5))):
            self.assertEqual(parse_date("yesterday"), REAL_DATETIME(2025, 12, 31))

    def test_yesterday_after_leap_day(self):
        with mock.patch("core.pipeline.datetime", frozen(REAL_DATETIME(2024, 3, 1, 12, 0))):
            self.assertEqual(parse_date("yesterday"), REAL_DATETIME(2024, 2, 29))

    def test_explicit_formats(self):
        for text in ("2026-10-22", "2026/10/22", "20261022"):
            self.assertEqual(parse_date(text), REAL_DATETIME(2026, 10, 22))

    def test_invalid_raises(self):
        with self.assertRaises(ValueError):
            parse_date("下周三")


# --------------------------------------------------------------------------- #
# 总结标题去重
# --------------------------------------------------------------------------- #
class TestSummaryHeading(unittest.TestCase):
    """总结正文自带标题时，外层不能再补一个。"""

    def test_strips_leading_heading(self):
        raw = "## 总结\n\n正文内容"
        self.assertEqual(strip_summary_heading(raw), "正文内容")

    def test_strips_h1_and_trailing_colon(self):
        self.assertEqual(strip_summary_heading("# 总结：\n\n内容"), "内容")

    def test_keeps_inner_headings(self):
        raw = "## 总结\n\n## 决策事项\n\n- 一条决策"
        self.assertEqual(strip_summary_heading(raw), "## 决策事项\n\n- 一条决策")

    def test_empty_text_safe(self):
        self.assertEqual(strip_summary_heading(""), "")

    def test_markdown_has_single_summary_heading(self):
        summary = "## 总结\n\n> 本次使用 file_output 模式。"
        md = build_markdown(summary, [], "测试群", "2026-10-22")
        self.assertEqual(md.count("## 总结"), 1, "报告里出现了重复的总结标题")

    def test_html_has_single_summary_heading(self):
        summary = "## 总结\n\n> 本次使用 file_output 模式。"
        page = render_report(summary, [], "测试群", "2026-10-22")
        # 页面小节标题是 class="section-title"，正文里若再来一个就是重复渲染
        self.assertEqual(page.count("<h2>总结</h2>"), 0)


# --------------------------------------------------------------------------- #
# 配置隔离
# --------------------------------------------------------------------------- #
class TestConfigIsolation(unittest.TestCase):
    """pipeline 不得把本次运行的参数写回调用方持有的配置。"""

    def _make_config(self) -> dict:
        return {
            "chatlog": {"api_base": "http://127.0.0.1:5030"},
            "summarizer": {"provider": "file_output", "prompt_template": "leader_brief"},
            "output": {"dir": "./runs", "format": ["markdown"]},
        }

    def test_does_not_mutate_shared_config(self):
        cfg = self._make_config()
        before = {
            "provider": cfg["summarizer"]["provider"],
            "template": cfg["summarizer"]["prompt_template"],
        }
        # chatlog 连不上会在拿到 client 后立刻失败 —— 但配置改写发生在那之前，
        # 正好可以在这个断言点上检查有没有把 cfg 写脏。
        with self.assertRaises(PipelineError):
            run_pipeline(cfg, RunOptions(group="某群", model="default", template="todo_list"))

        self.assertEqual(cfg["summarizer"]["provider"], before["provider"])
        self.assertEqual(cfg["summarizer"]["prompt_template"], before["template"],
                         "本次运行的 template 泄漏进了共享配置")

    def test_applies_model_override_without_error(self):
        cfg = self._make_config()
        with self.assertRaises(PipelineError):
            run_pipeline(cfg, RunOptions(group="某群", model="doubao"))
        self.assertEqual(cfg["summarizer"]["provider"], "file_output")


# --------------------------------------------------------------------------- #
# 任务注册表
# --------------------------------------------------------------------------- #
class TestTaskRegistry(unittest.TestCase):
    """控制台长期运行时的任务回收。"""

    def setUp(self):
        try:
            from web.server import TaskRegistry
        except ImportError:  # pragma: no cover
            self.skipTest("web 依赖不可用")
        self.registry = TaskRegistry(max_tasks=5)

    def test_evicts_oldest_finished_tasks(self):
        for _ in range(5):
            task = self.registry.create({"group": "g"})
            task.status = "done"
        self.assertEqual(len(self.registry._tasks), 5)

        self.registry.create({"group": "g"})
        self.assertEqual(len(self.registry._tasks), 5, "注册表突破了容量上限")

    def test_never_evicts_running_tasks(self):
        for _ in range(5):
            task = self.registry.create({"group": "g"})
            task.status = "done"
        running = self.registry.create({"group": "g"})  # 保持 running
        for _ in range(5):
            task = self.registry.create({"group": "g"})
            task.status = "done"
        self.assertIsNotNone(self.registry.get(running.id), "运行中的任务不该被回收")

    def test_running_lookup(self):
        self.assertIsNone(self.registry.running())
        task = self.registry.create({"group": "g"})
        self.assertEqual(self.registry.running().id, task.id)
        task.status = "done"
        self.assertIsNone(self.registry.running())

    def test_soft_timeout_status(self):
        from web.server import RUN_TIMEOUT_SEC, Task

        task = Task(id="x", options={})
        task.started -= (RUN_TIMEOUT_SEC + 60)  # 倒拨时钟模拟挂住
        self.assertEqual(task.to_dict()["status"], "timeout")


# --------------------------------------------------------------------------- #
# 媒体下载落盘
# --------------------------------------------------------------------------- #
class TestMediaDownload(unittest.TestCase):
    """中断的下载不能留下会被后续误当作有效缓存的残缺文件。"""

    def test_interrupted_download_leaves_no_corrupt_cache(self):
        from collector.chatlog_client import ChatlogClient

        client = ChatlogClient.__new__(ChatlogClient)
        client.api_base = "http://127.0.0.1:5030"
        client.timeout = 5
        client.endpoints = ChatlogClient._default_endpoints()
        client.media_dir = Path(tempfile.mkdtemp())
        client._session = SimpleNamespace(get=self._fake_stream)
        target = client.media_dir / "voice_key123.mp3"

        result = client.get_voice_file("key123")
        self.assertIsNone(result, "下载中断时应返回 None")
        self.assertFalse(target.exists(), "残缺的目标文件会被下一次当作有效缓存命中")
        self.assertEqual(
            [p.name for p in client.media_dir.iterdir()], [], "临时 .part 文件应已清理"
        )

    def _fake_stream(self, url, **kwargs):  # 假装收到一半就断了
        class Resp:
            status_code = 200

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def iter_content(self, size=None, **kwargs):
                yield b"\x00" * 128
                raise IOError("connection reset with file still open")

        return Resp()

    def test_part_file_promoted_on_success(self):
        from collector.chatlog_client import ChatlogClient

        client = ChatlogClient.__new__(ChatlogClient)
        client.api_base = "http://127.0.0.1:5030"
        client.timeout = 5
        client.endpoints = ChatlogClient._default_endpoints()
        client.media_dir = Path(tempfile.mkdtemp())
        client._session = SimpleNamespace(get=self._fake_ok)
        result = client.get_voice_file("key456")
        self.assertIsNotNone(result)
        self.assertTrue(Path(result).exists())
        self.assertEqual(Path(result).suffix, ".mp3")

    def _fake_ok(self, url, **kwargs):
        class Resp:
            status_code = 200

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def iter_content(self, size=None, **kwargs):
                yield b"ID3\x03" * 64

        return Resp()


# --------------------------------------------------------------------------- #
# 端到端
# --------------------------------------------------------------------------- #
def _sample_messages(group: str) -> list:
    base = REAL_DATETIME(2026, 10, 22, 9, 0, 0)
    rows = [
        (0, "陈总", "这次压测指标明确到 P99 350ms。", True),
        (3, "王五", "会场页链路要不要一起压？", False),
        (6, "陈总", "要，去年就是这里出的问题。", True),
    ]
    return [
        Message(
            msg_id=f"m{i}",
            group_name=group,
            sender_name=name,
            sender_id=f"wxid_{i}",
            timestamp=base + timedelta(minutes=offset),
            msg_type=TYPE_TEXT,
            content=content,
            raw_content=content,
            is_leader=leader,
            seq=i,
        )
        for i, (offset, name, content, leader) in enumerate(rows)
    ]


class TestEndToEnd(unittest.TestCase):
    """完整跑一次流水线，校验产物自洽。"""

    def test_pipeline_produces_clean_report(self):
        from collector import chatlog_client as module

        messages = _sample_messages("端到端测试群")
        original = module.ChatlogClient.__dict__["health"]
        try:
            module.ChatlogClient.health = lambda self: True
            module.ChatlogClient.get_messages = lambda self, *a, **k: messages

            with tempfile.TemporaryDirectory() as tmp:
                cfg = {
                    "chatlog": {"api_base": "http://127.0.0.1:5030"},
                    "summarizer": {
                        "provider": "file_output",
                        "prompt_template": "leader_brief",
                        "pending_dir": str(Path(tmp) / "pending"),
                    },
                    "output": {"dir": str(Path(tmp) / "runs"), "format": ["markdown", "html"]},
                    "asr": {"enabled": False},
                    "vision": {"provider": "dummy"},
                    "article": {"enabled": False},
                    "logging": {"level": "ERROR"},
                }
                result = run_pipeline(
                    cfg, RunOptions(group="端到端测试群", date="2026-10-22", formats=["markdown", "html"])
                )

                self.assertEqual(result.stats["total"], 3)
                self.assertEqual(result.stats["leader_count"], 2)
                self.assertTrue(result.markdown_path and result.markdown_path.exists())
                self.assertTrue(result.html_path and result.html_path.exists())

                md = result.markdown_path.read_text(encoding="utf-8")
                self.assertEqual(md.count("## 总结"), 1, "Markdown 里总结标题重复")

                page = result.html_path.read_text(encoding="utf-8")
                self.assertEqual(page.count("<h2>总结</h2>"), 0, "HTML 里总结标题重复")
                self.assertIn("陈总", page)
        finally:
            module.ChatlogClient.health = original  # 还原类方法，避免污染其它用例


# --------------------------------------------------------------------------- #
# chatlog v4 真实 JSON 结构映射
# --------------------------------------------------------------------------- #
class TestChatlogV4Mapping(unittest.TestCase):
    """chatlog v4 把媒体 key / 链接信息放在 `contents` map 里，
    而不是顶层字段。这里锁死映射关系，防止将来改客户端时漏读。"""

    def setUp(self):
        self.client = ChatlogClient("http://127.0.0.1:5030")

    def _row(self, **kw):
        base = {
            "seq": 1,
            "time": "2026-10-22 14:30:00",
            "talker": "群",
            "talkerName": "测试群",
            "sender": "wxid_a",
            "senderName": "张三",
            "type": 1,
            "content": "",
        }
        base.update(kw)
        return base

    def test_image_key_from_contents_md5(self):
        m = self.client._to_message(
            self._row(type=3, contents={"md5": "abc123def456"}), "测试群", set(), set()
        )
        self.assertEqual(m.msg_type, "image")
        self.assertEqual(m.raw_content, "abc123def456")
        self.assertEqual(m.attachments[0]["key"], "abc123def456")

    def test_voice_key_from_contents_voice(self):
        m = self.client._to_message(
            self._row(type=34, contents={"voice": "voice_key_xyz"}), "测试群", set(), set()
        )
        self.assertEqual(m.msg_type, "voice")
        self.assertEqual(m.raw_content, "voice_key_xyz")
        self.assertEqual(m.attachments[0]["key"], "voice_key_xyz")

    def test_article_restores_url_and_title(self):
        m = self.client._to_message(
            self._row(
                type=49,
                subType=5,
                contents={"title": "一篇公众号", "url": "https://mp.weixin.qq.com/s/x", "desc": "摘要"},
            ),
            "测试群",
            set(),
            set(),
        )
        self.assertEqual(m.msg_type, "article")
        self.assertEqual(m.content, "https://mp.weixin.qq.com/s/x")
        self.assertIn("一篇公众号", m.note)

    def test_text_content_cleaned(self):
        m = self.client._to_message(self._row(type=1, content="<p>你好</p>"), "测试群", set(), set())
        self.assertEqual(m.msg_type, "text")
        self.assertEqual(m.content, "你好")


if __name__ == "__main__":
    unittest.main(verbosity=2)
