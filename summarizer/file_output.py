"""本地文件输出模式（MVP 默认总结后端）。

不直接调用 LLM，而是把「完整渲染好的提示词 + 聊天内容」写成一个自包含文件，
用户后续把这个文件丢给 Codex / Claude Code / WorkBuddy 即可拿到总结。

这样做的原因：MVP 阶段先把数据采集与加工链路打磨扎实，
总结这一步由外部 AI CLI 完成，零成本、零延迟、结果可控。
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from textwrap import dedent
from typing import Any, Dict, Optional

from core.log import get_logger
from summarizer.base import Summarizer

logger = get_logger("file_output")

_HOWTO = dedent(
    """
    > 把本文件中 `## 待处理内容` 以下的部分整段复制，粘贴给你的 AI 编程助手
    > （Codex / Claude Code / WorkBuddy），即可得到结构化总结。
    """
).strip()


class FileOutputSummarizer(Summarizer):
    """把待总结内容写成文件，等待外部 AI 处理。

    Args:
        pending_dir: 待处理文件落盘目录，默认 `runs/pending`。
        copy_ready: 是否额外生成一份「可直接复制」的 prompt-only 文件。
    """

    name = "file_output"

    def __init__(
        self,
        pending_dir: Path | str = "./runs/pending",
        copy_ready: bool = True,
        keep_recent: int = 20,
    ) -> None:
        super().__init__()
        self.pending_dir = Path(str(pending_dir)).expanduser()
        self.copy_ready = copy_ready
        # 只保留最近 N 组待处理文件，更旧的自动清理，避免长期无限堆积
        self.keep_recent = max(1, int(keep_recent))

    def summarize(self, chat_content: str, prompt_template: str) -> str:
        """渲染提示词并落盘，返回给用户看的下一步指引。

        Args:
            chat_content: 合并后的聊天文本。
            prompt_template: prompt 模板原文。

        Returns:
            一段说明性 Markdown，会写进最终报告的「总结」区块。
        """
        self.pending_dir.mkdir(parents=True, exist_ok=True)

        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        prompt = self.render_prompt(
            prompt_template,
            chat_content=chat_content,
            leaders_list=self.last_meta.get("leaders_text", "（未指定）"),
            group_name=self.last_meta.get("group_name", "未命名群"),
            date=self.last_meta.get("date", datetime.now().strftime("%Y-%m-%d")),
        )

        master_path = self.pending_dir / f"{stamp}_content.md"
        body = self._compose(prompt, chat_content)
        try:
            master_path.write_text(body, encoding="utf-8")
            self.last_output_path = master_path
        except OSError as exc:
            logger.error("待处理文件写入失败：%s", exc)
            return "## 总结\n\n⚠️ 待处理文件写入失败，请检查 runs/pending 目录权限。"

        prompt_path: Optional[Path] = None
        if self.copy_ready:
            prompt_path = self.pending_dir / f"{stamp}_prompt.txt"
            try:
                prompt_path.write_text(prompt, encoding="utf-8")
            except OSError:
                prompt_path = None

        self._prune_old()
        logger.info("待处理文件已生成：%s", master_path)

        lines = [
            "## 总结",
            "",
            "> 本次使用 `file_output` 模式：内容已整理完毕，等待外部 AI 处理。",
            "",
            f"- 待处理文件：`{master_path.as_posix()}`",
        ]
        if prompt_path:
            lines.append(f"- 可直接复制的完整提示词：`{prompt_path.as_posix()}`")
        lines += [
            "",
            "**下一步**：把上面那个文件的内容整段复制给任意 AI 助手（Codex / Claude Code /",
            "ChatGPT 等），把返回的总结替换掉本文件的这一节即可。",
            "",
            "若要自动化这一段，在 `.env` 里填上 `DEEPSEEK_API_KEY`，",
            "并把 `config/settings.yaml` 的 `summarizer.provider` 改成 `deepseek`。",
        ]
        return "\n".join(lines)

    # -------------------------------------------------------------- #
    def _prune_old(self) -> None:
        """清理过期待处理文件：每组按时间戳排序，只保留最近 keep_recent 组。"""
        try:
            stamps = sorted(
                {p.stem.split("_")[0] for p in self.pending_dir.glob("*_content.md")}
            )
        except OSError:
            return
        if len(stamps) <= self.keep_recent:
            return
        for old in stamps[: len(stamps) - self.keep_recent]:
            for suffix in ("_content.md", "_prompt.txt"):
                f = self.pending_dir / f"{old}{suffix}"
                try:
                    if f.exists():
                        f.unlink()
                except OSError:
                    pass

    def _compose(self, prompt: str, chat_content: str) -> str:
        """组装待处理文件的完整内容。"""
        meta = self.last_meta or {}
        header = [
            "# Chat Digest · 待处理内容",
            "",
            _HOWTO,
            "",
            "## 元信息",
            "",
            f"- 群聊：{meta.get('group_name', '未命名群')}",
            f"- 日期：{meta.get('date', datetime.now().strftime('%Y-%m-%d'))}",
            f"- 消息总数：{meta.get('total', 0)}",
            f"- 领导发言数：{meta.get('leader_count', 0)}",
            f"- 生成时间：{datetime.now():%Y-%m-%d %H:%M:%S}",
            "",
            "## 待处理内容",
            "",
            "```text",
            chat_content.strip() or "（本次没有采集到任何消息）",
            "```",
            "",
            "## 完整提示词（给 AI 用的）",
            "",
            "```text",
            prompt.strip(),
            "```",
            "",
        ]
        return "\n".join(header)
