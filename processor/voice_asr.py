"""语音转写（faster-whisper，本地运行）。

处理链路：

    chatlog /voice/<key>  ->  MP3（chatlog 已做 SILK 实时转码）
                          ->  _ensure_wav() 兜底转码（如遇原始 SILK）
                          ->  faster-whisper.transcribe()  ->  文本

设计要点：
    · faster-whisper 是可选依赖，未安装时不影响其余链路，只把语音降级成 [语音]
    · 模型懒加载：真正遇到语音才 import + load，避免启动就吃掉几秒
    · 单条失败不影响其他消息
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from collector.models import PLACEHOLDER_VOICE, TYPE_VOICE, Message
from core.log import get_logger

logger = get_logger("voice_asr")

SILK_MAGIC = b"#!SILK_V3"


class VoiceTranscriber:
    """把语音文件转写成文字。

    Args:
        model_name: faster-whisper 模型名，如 `large-v3`。
        device: `cpu` 或 `cuda`。
        compute_type: 量化方式，如 `int8` / `float16`。
        language: 识别语言，留空表示自动检测。
        beam_size: beam search 宽度，越大越准越慢。
        vad_filter: 是否启用静音过滤。
        silk_decoder: SILK 转码工具的可执行路径 / 命令名。
        cache_dir: 转码中间文件目录。
        enabled: 总开关，False 时直接返回占位符。
    """

    def __init__(
        self,
        model_name: str = "large-v3",
        device: str = "cpu",
        compute_type: str = "int8",
        language: str = "zh",
        beam_size: int = 5,
        vad_filter: bool = True,
        silk_decoder: str = "",
        cache_dir: Optional[Path] = None,
        enabled: bool = True,
    ) -> None:
        self.model_name = model_name
        self.device = device
        self.compute_type = compute_type
        self.language = language or None
        self.beam_size = beam_size
        self.vad_filter = vad_filter
        self.silk_decoder = silk_decoder.strip()
        self.cache_dir = Path(cache_dir or (Path(__file__).resolve().parents[1] / ".wechat_media"))
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.enabled = enabled

        self._model = None
        self._lock = threading.Lock()
        self._warned_unavailable = False
        self._cache: Dict[str, str] = {}
        self._stats = {"ok": 0, "failed": 0, "skipped": 0, "chars": 0}

    # -------------------------------------------------------------- #
    # 对外主入口
    # -------------------------------------------------------------- #
    def transcribe(self, audio_path: str) -> str:
        """把音频文件转成文字。

        Args:
            audio_path: 本地音频文件路径（mp3 / wav / silk）。

        Returns:
            转写文本；失败返回空字符串（由调用方决定降级文案）。
        """
        if not audio_path or not Path(audio_path).exists():
            logger.debug("音频文件不存在：%s", audio_path)
            return ""

        key = self._file_key(audio_path)
        if key in self._cache:
            return self._cache[key]

        if not self.enabled:
            logger.info("ASR 已禁用（asr.enabled=false），跳过转写")
            self._stats["skipped"] += 1
            return ""

        wav_path = self._ensure_wav(audio_path)
        if not wav_path:
            self._stats["failed"] += 1
            return ""

        model = self._load_model()
        if model is None:
            self._stats["skipped"] += 1
            return ""

        try:
            segments, info = model.transcribe(
                str(wav_path),
                language=self.language,
                beam_size=self.beam_size,
                vad_filter=self.vad_filter,
            )
            text = "".join(seg.text for seg in segments).strip()
        except Exception as exc:
            logger.warning("语音转写失败（%s）：%s", audio_path, exc)
            self._stats["failed"] += 1
            return ""

        if not text:
            logger.debug("转写结果为空：%s", audio_path)
            self._stats["failed"] += 1
            return ""

        self._stats["ok"] += 1
        self._stats["chars"] += len(text)
        self._cache[key] = text
        logger.info("语音转写成功（%d 字）：%s", len(text), Path(audio_path).name)
        return text

    def process_message(
        self, message: Message, fetcher: Callable[[Message], Optional[str]]
    ) -> bool:
        """就地处理一条语音消息，把结果写回 message.content。

        Args:
            message: 待处理的 Message（msg_type 必须是 voice）。
            fetcher: 负责把 message 换成可转写的本地文件路径。

        Returns:
            True 表示成功写入文本，False 表示降级为占位符。
        """
        if message.msg_type != TYPE_VOICE:
            return False

        audio_path: Optional[str] = None
        try:
            audio_path = fetcher(message)
        except Exception as exc:
            logger.warning("获取语音文件失败（msg=%s）：%s", message.msg_id, exc)

        if not audio_path:
            message.content = PLACEHOLDER_VOICE
            message.note = "未取到本地语音文件"
            return False

        text = self.transcribe(audio_path)
        if text:
            message.content = text
            message.raw_content = audio_path
            message.note = "已转写"
            return True

        message.content = PLACEHOLDER_VOICE
        message.note = "语音转写失败或未安装 faster-whisper"
        return False

    @property
    def stats(self) -> Dict[str, int]:
        """返回本次运行的转写统计。"""
        return dict(self._stats)

    # -------------------------------------------------------------- #
    # 内部实现
    # -------------------------------------------------------------- #
    def _load_model(self):
        """懒加载 faster-whisper 模型（线程安全，重复调用只加载一次）。"""
        with self._lock:
            if self._model is not None:
                return self._model
            try:
                from faster_whisper import WhisperModel  # 延迟导入，MVP 可不装
            except ImportError:
                if not self._warned_unavailable:
                    logger.warning(
                        "未安装 faster-whisper，语音将降级为 [语音]。"
                        "安装命令：pip install '.[asr]' 或 pip install faster-whisper"
                    )
                    self._warned_unavailable = True
                return None
            try:
                logger.info("加载 ASR 模型：%s（%s / %s）", self.model_name, self.device, self.compute_type)
                self._model = WhisperModel(
                    self.model_name, device=self.device, compute_type=self.compute_type
                )
                return self._model
            except Exception as exc:
                logger.error("ASR 模型加载失败：%s", exc)
                return None

    def _ensure_wav(self, audio_path: str) -> Optional[str]:
        """确保输入是 faster-whisper 能直接吃的格式。

        chatlog 返回的已经是 MP3，绝大多数情况直接原样返回；
        只有真的拿到 SILK 原始流时才触发转码。
        """
        path = Path(audio_path)
        if not path.exists() or path.stat().st_size == 0:
            return None

        try:
            head = path.open("rb").read(9)
        except OSError:
            return None

        if not head.startswith(SILK_MAGIC):
            return str(path)

        logger.debug("检测到 SILK 原始流，尝试转码：%s", path.name)
        wav = self._silk_to_wav(path)
        if wav:
            return wav
        logger.warning("SILK 转码失败，跳过该条语音：%s", path.name)
        return None

    def _silk_to_wav(self, silk_path: Path) -> Optional[str]:
        """把 SILK 转成 16k 单声道 WAV。

        优先用配置里的 `asr.silk_decoder`，其次 ffmpeg，都不可用返回 None。
        """
        target = self.cache_dir / f"{silk_path.stem}_{self._short_digest(silk_path)}.wav"
        if target.exists() and target.stat().st_size > 0:
            return str(target)

        cmds = []
        if self.silk_decoder:
            cmds.append([self.silk_decoder, str(silk_path), str(target)])
        cmds.append(
            ["ffmpeg", "-y", "-i", str(silk_path), "-ar", "16000", "-ac", "1", str(target)]
        )

        for cmd in cmds:
            try:
                proc = subprocess.run(
                    cmd, capture_output=True, timeout=60, text=True, errors="replace"
                )
                if proc.returncode == 0 and target.exists() and target.stat().st_size > 0:
                    return str(target)
                logger.debug("转码命令失败 %s：%s", cmd[0], (proc.stderr or "")[:200])
            except (OSError, subprocess.SubprocessError) as exc:
                logger.debug("转码工具不可用（%s）：%s", cmd[0], exc)

        return None

    @staticmethod
    def _file_key(path: str) -> str:
        """按路径 + 大小 + 修改时间做轻量缓存键。"""
        p = Path(path)
        try:
            stat = p.stat()
            raw = f"{p}|{stat.st_size}|{int(stat.st_mtime)}"
        except OSError:
            raw = str(p)
        return hashlib.md5(raw.encode("utf-8")).hexdigest()

    @staticmethod
    def _short_digest(path: Path) -> str:
        """给临时文件加短摘要，避免同名冲突。"""
        return hashlib.md5(str(path.resolve()).encode("utf-8")).hexdigest()[:8]


def build_transcriber_from_config(cfg: Dict[str, Any]) -> VoiceTranscriber:
    """从 settings.yaml 解析结果构造 VoiceTranscriber。"""
    asr_cfg = cfg.get("asr", {}) or {}
    return VoiceTranscriber(
        model_name=str(asr_cfg.get("model", "large-v3")),
        device=str(asr_cfg.get("device", "cpu")),
        compute_type=str(asr_cfg.get("compute_type", "int8")),
        language=str(asr_cfg.get("language", "zh") or ""),
        beam_size=int(asr_cfg.get("beam_size", 5)),
        vad_filter=bool(asr_cfg.get("vad_filter", True)),
        silk_decoder=str(asr_cfg.get("silk_decoder", "") or ""),
        enabled=bool(asr_cfg.get("enabled", True)),
    )
