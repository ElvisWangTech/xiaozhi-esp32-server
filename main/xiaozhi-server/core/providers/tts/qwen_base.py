"""
Qwen 系列「非实时语音合成 + SSE 流式输出」公共实现。

阿里云百炼（Model Studio）的非实时 TTS 都是 HTTP 一次性提交完整文本，
通过请求头 ``X-DashScope-SSE: enable`` 让服务端把音频切成 base64 分片逐段返回，
最后一个分片的 ``output.audio.url`` 携带完整音频地址。

本模块把这套协议适配成小智的 TTS Provider 接口（SINGLE_STREAM：流式文本进、流式音频出）。

子类只需覆盖：
- ``DEFAULT_API_URL`` / ``DEFAULT_MODEL`` / ``DEFAULT_VOICE``
- ``SUPPORTS_SAMPLE_RATE`` / ``SUPPORTED_SAMPLE_RATES``
- ``_build_payload(text, sample_rate)``

已有实现：
- ``qwen_tts.py``       Qwen3-TTS（qwen3-tts-flash），固定 24 kHz
- ``qwen_audio_tts.py`` Qwen-Audio-TTS，可指定采样率/语速/音调/自然语言指令

两个关键协议事实（2026-11 实测）：
1. Qwen3-TTS 无论是否传 format 参数，SSE 返回的 ``audio.data`` 都是**带 44 字节
   RIFF 头的 WAV**（首个分片含头，后续分片是纯 PCM）。直接当 PCM 用会把头部
   44 字节当成 22 个全振幅样本——每段开头一声爆音。``WavHeaderStripper``
   负责识别并剥离；若上游将来返回裸 PCM 则自动直通。
2. 每个文本分段是一次独立 HTTP 请求，串行合成时段间有 TTFB 空档，
   设备播放队列排空后会出现不自然的停顿。因此文本线程只负责切段并
   提交后台合成（共享连接、最多并发预取下一段），由专用发射线程按
   提交顺序把 PCM 送入编码与播放流水线。
"""

import os
import re
import json
import time
import wave
import queue
import base64
import asyncio
import threading
import traceback

from collections import deque
from typing import Callable, Optional

import aiohttp
import numpy as np

from config.logger import setup_logging
from core.utils import textUtils, opus_encoder_utils
from core.utils.tts import MarkdownCleaner
from core.providers.tts.base import TTSProviderBase
from core.providers.tts.dto.dto import SentenceType, ContentType, InterfaceType

TAG = __name__
logger = setup_logging()


class WavHeaderStripper:
    """流式剥离 WAV/RIFF 头（DashScope Qwen3-TTS 返回的是 WAV 而非裸 PCM）。

    输入不以 ``RIFF`` 开头时永久直通（上游返回裸 PCM 的场景）。
    典型头部长度 44 字节：RIFF(12) + "fmt "(8+16) + "data"(8)；
    这里按块（chunk）遍历实现，兼容 fmt 18/40 字节、含 LIST 块等变体，
    且不依赖 data 块大小字段（流式场景该字段是占位值 0x7FFFFFBF）。
    """

    def __init__(self):
        self._buf = b""
        self._skip = 0
        self._decided = False
        self._passthrough = False

    def feed(self, pcm: bytes) -> bytes:
        """送入一段音频字节，返回剥离头部后的有效 PCM（可能为空）。"""
        if self._passthrough:
            return pcm
        if not pcm:
            return b""
        self._buf += pcm

        if not self._decided:
            if len(self._buf) < 12:
                return b""
            if self._buf[:4] != b"RIFF" or self._buf[8:12] != b"WAVE":
                # 不是 WAV：全部直通
                self._passthrough = True
                out, self._buf = self._buf, b""
                return out
            self._decided = True
            self._buf = self._buf[12:]  # 跳过 "RIFF<size>WAVE"

        # 按 chunk 遍历，找到 data 块后全部直通
        while True:
            if self._skip > 0:
                if len(self._buf) < self._skip:
                    return b""
                self._buf = self._buf[self._skip :]
                self._skip = 0
                continue
            if len(self._buf) < 8:
                return b""
            chunk_id = self._buf[:4]
            self._skip = int.from_bytes(self._buf[4:8], "little")
            self._buf = self._buf[8:]
            if chunk_id == b"data":
                # data 负载从这里开始，后续所有字节（含未来 feed）都是音频
                self._passthrough = True
                self._skip = 0
                out, self._buf = self._buf, b""
                return out


class PCMResampler:
    """轻量级流式 PCM 重采样器（16bit 单声道）。

    Qwen3-TTS 的采样率固定为 24 kHz，而设备 hello 消息可能声明 16 kHz，
    因此需要在推流过程中做重采样。这里用「均值抗混叠 + 线性插值」实现，
    并保留分片间的相位与历史样本，保证音频连续、无爆音。
    """

    def __init__(self, src_rate: int, dst_rate: int):
        self.src_rate = int(src_rate)
        self.dst_rate = int(dst_rate)
        self.step = self.src_rate / self.dst_rate
        # 降采样时用均值窗口做粗抗混叠；升采样时窗口为 1（不做滤波）
        self.window = max(1, int(round(self.step)))
        self._hist = np.zeros(max(0, self.window - 1), dtype=np.float32)
        self._buf = np.zeros(0, dtype=np.float32)
        self._pos = 0.0

    def reset(self):
        self._hist = np.zeros(max(0, self.window - 1), dtype=np.float32)
        self._buf = np.zeros(0, dtype=np.float32)
        self._pos = 0.0

    def _anti_alias(self, samples: np.ndarray) -> np.ndarray:
        if self.window <= 1:
            return samples
        merged = np.concatenate([self._hist, samples])
        self._hist = merged[-(self.window - 1) :]
        if merged.size < self.window:
            return np.zeros(0, dtype=np.float32)
        kernel = np.ones(self.window, dtype=np.float32) / self.window
        return np.convolve(merged, kernel, mode="valid")

    def process(self, pcm_bytes: bytes) -> bytes:
        """送入一段源采样率 PCM，返回可用的目标采样率 PCM（可能为空）。"""
        if not pcm_bytes:
            return b""
        samples = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32)
        samples = self._anti_alias(samples)
        if samples.size == 0:
            return b""

        self._buf = (
            np.concatenate([self._buf, samples]) if self._buf.size else samples
        )

        out = []
        pos = self._pos
        total = self._buf.size
        while pos + 1 < total:
            idx = int(pos)
            frac = pos - idx
            left = self._buf[idx]
            out.append(left + (self._buf[idx + 1] - left) * frac)
            pos += self.step

        consumed = int(pos)
        if consumed > 0:
            self._buf = self._buf[consumed:]
            pos -= consumed
        self._pos = pos

        if not out:
            return b""
        result = np.clip(np.round(np.asarray(out)), -32768, 32767).astype(np.int16)
        return result.tobytes()


class QwenTTSBase(TTSProviderBase):
    """Qwen 非实时 TTS（HTTP + SSE 流式）公共实现。"""

    # 子类必须覆盖
    DEFAULT_API_URL = ""
    DEFAULT_MODEL = ""
    DEFAULT_VOICE = ""
    NATIVE_SAMPLE_RATE = 24000
    # 是否支持在请求中指定采样率（Qwen-Audio-TTS 支持，Qwen3-TTS 不支持）
    SUPPORTS_SAMPLE_RATE = False
    SUPPORTED_SAMPLE_RATES = (24000,)
    # 单次请求文本上限（字符）
    DEFAULT_MAX_CHARS = 600

    def __init__(self, config, delete_audio_file):
        super().__init__(config, delete_audio_file)

        self.interface_type = InterfaceType.SINGLE_STREAM
        self.audio_file_type = "pcm"

        self.api_key = (config.get("api_key") or "").strip()
        if not self.api_key:
            raise ValueError(
                f"{self.__class__.__module__.split('.')[-1]} 需要配置 api_key（阿里云百炼 API Key）"
            )

        # Qwen TTS 走 DashScope 原生协议（X-DashScope-SSE + input 结构），
        # 不支持 OpenAI compatible-mode 端点。管理页面若误填 OpenAI 兼容地址
        # （如 xxx/compatible-mode/v1）或其他非原生地址，POST 会得到 404。
        # 这里校验：仅当包含 DashScope 原生路径特征，或为本地地址
        # （本地测试/私有化网关）时才采用，否则回退默认端点。
        api_url = (config.get("api_url") or "").strip()
        if api_url and "/api/v1/services/" not in api_url:
            is_local = "://localhost" in api_url or "://127.0.0.1" in api_url
            if not is_local:
                logger.bind(tag=TAG).warning(
                    f"配置的 api_url（{api_url}）不是 DashScope 原生端点"
                    f"（Qwen TTS 不支持 OpenAI 兼容地址），已回退默认端点: {self.DEFAULT_API_URL}"
                )
                api_url = ""
        self.api_url = api_url or self.DEFAULT_API_URL
        self.model = (config.get("model") or self.DEFAULT_MODEL).strip()
        self.voice = (
            config.get("private_voice") or config.get("voice") or self.DEFAULT_VOICE
        )
        self.language_type = config.get("language_type")

        try:
            self.max_chars = int(config.get("max_chars", self.DEFAULT_MAX_CHARS))
        except (TypeError, ValueError):
            self.max_chars = self.DEFAULT_MAX_CHARS
        if self.max_chars <= 0:
            self.max_chars = self.DEFAULT_MAX_CHARS

        self._headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        # PCM 分帧缓冲与重采样器（按源采样率缓存）
        self.pcm_buffer = bytearray()
        # 段首淡入待施加标志（每个文本分段开始时置位）
        self._fade_in_pending = True
        self._resampler: Optional[PCMResampler] = None
        self._resampler_key = None

        # ---- 流水线状态：后台事件循环（共享连接）+ 并发预取 + 顺序发射 ----
        self._bg_loop: Optional[asyncio.AbstractEventLoop] = None
        self._bg_ready = threading.Event()
        self._bg_stop_event: Optional[asyncio.Event] = None
        self._session: Optional[aiohttp.ClientSession] = None
        # 待发射分段（deque 的 append/popleft 为 GIL 原子操作）
        self._pending_pieces = deque()
        # LAST 已到但仍有分段在途：排空后补发 stop
        self._stop_after_drain = False
        # 发射线程当前分段的 sentence_id（防串轮次），None 时回退 current_sentence_id
        self._emit_sentence_id = None
        self._emitter_thread: Optional[threading.Thread] = None

        # 首次出声耗时统计，便于排查链路延迟
        self._last_ttfb = 0.0

    # ------------------------------------------------------------------ 配置
    def _output_sample_rate(self) -> int:
        """本次请求向服务端索取的采样率。"""
        conn_rate = int(getattr(self.conn, "sample_rate", 0) or 0)
        if self.SUPPORTS_SAMPLE_RATE and conn_rate in self.SUPPORTED_SAMPLE_RATES:
            return conn_rate
        return self.NATIVE_SAMPLE_RATE

    def _ensure_resampler(self, src_rate: int) -> Optional[PCMResampler]:
        dst_rate = int(getattr(self.conn, "sample_rate", 0) or 0) or src_rate
        key = (src_rate, dst_rate)
        if self._resampler_key != key:
            self._resampler = (
                PCMResampler(src_rate, dst_rate) if src_rate != dst_rate else None
            )
            self._resampler_key = key
        return self._resampler

    @property
    def _frame_bytes(self) -> int:
        """一帧（frame_size_ms）PCM 的字节数。"""
        encoder = getattr(self, "opus_encoder", None)
        sample_rate = int(
            getattr(encoder, "sample_rate", 0)
            or getattr(self.conn, "sample_rate", 0)
            or self.NATIVE_SAMPLE_RATE
        )
        frame_size_ms = int(getattr(encoder, "frame_size_ms", 60) or 60)
        channels = int(getattr(encoder, "channels", 1) or 1)
        return int(sample_rate * channels * frame_size_ms / 1000 * 2)

    # ------------------------------------------------------------------ 文本切分
    def _split_text(self, text: str) -> list:
        """按句末标点切成不超过 max_chars 的片段，超长句子再硬切。"""
        text = (text or "").strip()
        if not text:
            return []
        if len(text) <= self.max_chars:
            return [text]

        pieces = [p for p in re.split(r"(?<=[。！？!?；;\.\n])", text) if p.strip()]
        chunks, buf = [], ""
        for piece in pieces:
            while len(piece) > self.max_chars:
                if buf:
                    chunks.append(buf)
                    buf = ""
                chunks.append(piece[: self.max_chars])
                piece = piece[self.max_chars :]
            if len(buf) + len(piece) > self.max_chars:
                chunks.append(buf)
                buf = piece
            else:
                buf += piece
        if buf.strip():
            chunks.append(buf)
        return [c for c in chunks if c.strip()]

    # ------------------------------------------------------------------ 请求
    def _build_payload(self, text: str, sample_rate: int) -> dict:
        raise NotImplementedError

    @staticmethod
    def _check_error(data: dict) -> Optional[str]:
        code = (data.get("code") or "").strip()
        status = data.get("status_code", 200)
        if code or (isinstance(status, int) and status >= 400):
            return f"code={code or status}, message={data.get('message') or '未知错误'}"
        output = data.get("output") or {}
        if isinstance(output.get("finish_reason"), str) and output.get(
            "finish_reason"
        ) in ("error", "failed"):
            return f"finish_reason={output.get('finish_reason')}"
        return None

    def _handle_event(
        self,
        event: bytes,
        on_pcm: Callable[[bytes], None],
        started_at: float,
        stats: dict,
    ) -> bool:
        """解析一个 SSE 事件块，返回 True 表示收到 finish_reason=stop。"""
        for line in event.split(b"\n"):
            line = line.strip()
            if not line or not line.startswith(b"data:"):
                continue
            chunk = line[5:].strip()
            if not chunk or chunk == b"[DONE]":
                continue
            try:
                data = json.loads(chunk.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                logger.bind(tag=TAG).debug(f"忽略无法解析的 SSE 分片: {chunk[:80]}")
                continue

            error = self._check_error(data)
            if error:
                raise RuntimeError(error)

            output = data.get("output") or {}
            audio = output.get("audio") or {}
            audio_b64 = audio.get("data")
            if audio_b64:
                try:
                    pcm = base64.b64decode(audio_b64)
                except Exception as e:  # noqa: BLE001
                    raise RuntimeError(f"音频分片 base64 解码失败: {e}") from e
                if pcm:
                    if not stats["received"]:
                        stats["received"] = True
                        self._last_ttfb = time.time() - started_at
                    on_pcm(pcm)

            if output.get("finish_reason") == "stop":
                return True
        return False

    async def _request_stream(
        self,
        text: str,
        sample_rate: int,
        on_pcm: Callable[[bytes], None],
        session: Optional[aiohttp.ClientSession] = None,
    ):
        """发起一次 SSE 请求，每收到一段音频就回调 on_pcm(纯 PCM)。

        - 音频流先经 ``WavHeaderStripper``：Qwen3-TTS 返回带 RIFF 头的 WAV，
          头部 44 字节若混入 PCM 会成为段首爆音；
        - ``session`` 传入共享会话（流水线模式，复用 TCP/TLS 连接），
          缺省时自建一次性会话（文件模式 / to_tts 测试入口）。

        注意：这里按 ``\\n\\n`` 手动切事件，而不是按行迭代 ``resp.content``，
        因为 base64 音频片可能超过 aiohttp 默认的 64KB 行长度限制。
        """
        payload = self._build_payload(text, sample_rate)
        headers = dict(self._headers)
        headers["X-DashScope-SSE"] = "enable"
        timeout = aiohttp.ClientTimeout(total=max(self.tts_timeout * 4, 30))

        stripper = WavHeaderStripper()

        def _on_pcm(pcm: bytes):
            pcm = stripper.feed(pcm)
            if pcm:
                on_pcm(pcm)

        started_at = time.time()
        stats = {"received": False}
        finished = False
        buffer = b""

        owns_session = session is None
        if owns_session:
            session = aiohttp.ClientSession(timeout=timeout)
        try:
            async with session.post(
                self.api_url,
                headers=headers,
                data=json.dumps(payload, ensure_ascii=False),
                timeout=timeout,
            ) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    raise RuntimeError(f"HTTP {resp.status}, {body[:300]}")

                async for raw in resp.content.iter_any():
                    if not raw:
                        continue
                    buffer += raw
                    while not finished:
                        index = buffer.find(b"\n\n")
                        if index < 0:
                            break
                        event, buffer = buffer[:index], buffer[index + 2 :]
                        finished = self._handle_event(
                            event, _on_pcm, started_at, stats
                        )
                        if finished:
                            break
                    if finished:
                        break

            # 兜底处理没有以空行结尾的残留事件
            if not finished and buffer.strip():
                self._handle_event(buffer, _on_pcm, started_at, stats)
        finally:
            if owns_session:
                await session.close()

        if not stats["received"]:
            raise RuntimeError("服务端未返回任何音频数据")

    # ------------------------------------------------------------------ 音频编码
    # 段尾淡出/段首淡入时长（毫秒）：每个文本分段是一次独立请求，
    # 段尾波形未必归零，与下一段或静音填充衔接会产生阶跃（爆破音），
    # 因此保留最后一帧待 flush 时施加淡出后再编码。
    FADE_OUT_MS = 10
    FADE_IN_MS = 5

    def _encoder_audio_params(self) -> tuple:
        """(采样率, 单帧毫秒数)，与 self._frame_bytes 的口径保持一致。"""
        encoder = getattr(self, "opus_encoder", None)
        sample_rate = int(
            getattr(encoder, "sample_rate", 0)
            or getattr(self.conn, "sample_rate", 0)
            or self.NATIVE_SAMPLE_RATE
        )
        frame_size_ms = int(getattr(encoder, "frame_size_ms", 60) or 60)
        return sample_rate, frame_size_ms

    @staticmethod
    def _apply_fade(samples: np.ndarray, fade_n: int, fade_in: bool) -> np.ndarray:
        """对 int16 波形施加线性淡入或淡出（原地 float 计算后回写）。"""
        n = len(samples)
        if fade_n <= 0 or n == 0:
            return samples
        fade_n = min(fade_n, n)
        ramp = (
            np.linspace(0.0, 1.0, fade_n, dtype=np.float32)
            if fade_in
            else np.linspace(1.0, 0.0, fade_n, dtype=np.float32)
        )
        out = samples.astype(np.float32)
        if fade_in:
            out[:fade_n] *= ramp
        else:
            out[n - fade_n :] *= ramp
        return np.clip(np.round(out), -32768, 32767).astype(np.int16)

    def _feed_pcm(self, pcm: bytes):
        if not pcm:
            return
        self.pcm_buffer.extend(pcm)
        frame_bytes = self._frame_bytes
        if frame_bytes <= 0:
            return
        # 保留最后 1 个完整帧不编码：段尾需要对其施加淡出，
        # 一旦编码发出就无法再修正段间波形跳变
        while len(self.pcm_buffer) >= frame_bytes * 2:
            frame = bytes(self.pcm_buffer[:frame_bytes])
            del self.pcm_buffer[:frame_bytes]
            self._encode_frame(frame)

    def _encode_frame(self, frame: bytes, end_of_stream: bool = False):
        if self._fade_in_pending:
            self._fade_in_pending = False
            sample_rate, _ = self._encoder_audio_params()
            fade = int(sample_rate * self.FADE_IN_MS / 1000)
            frame = self._apply_fade(
                np.frombuffer(frame, dtype=np.int16).copy(), fade, fade_in=True
            ).tobytes()
        self.opus_encoder.encode_pcm_to_opus_stream(
            frame, end_of_stream=end_of_stream, callback=self.handle_opus
        )

    def _flush_pcm(self):
        if not self.pcm_buffer:
            return
        tail = bytes(self.pcm_buffer)
        self.pcm_buffer.clear()
        # 段尾淡出：消除“段尾非零波形 -> 静音/下一段”的阶跃（爆破音）
        sample_rate, _ = self._encoder_audio_params()
        fade = int(sample_rate * self.FADE_OUT_MS / 1000)
        samples = np.frombuffer(tail, dtype=np.int16).copy()
        samples = self._apply_fade(samples, fade, fade_in=False)
        self._encode_frame(samples.tobytes(), end_of_stream=True)

    @staticmethod
    def _write_wav(path: str, pcm: bytes, sample_rate: int):
        directory = os.path.dirname(path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        with wave.open(path, "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(int(sample_rate))
            wav_file.writeframes(pcm)

    # ------------------------------------------------------------------ 后台事件循环
    def _ensure_bg_loop(self):
        """启动后台事件循环线程：承载共享 aiohttp 会话与并发分段合成。

        共享会话复用 TCP/TLS 连接，免去每个分段一次完整的 TLS 握手；
        各分段协程在此循环上并发执行（预取下一段时与当前段并行）。
        """
        if self._bg_loop is not None and self._bg_loop.is_running():
            return
        self._bg_ready.clear()
        self._bg_loop = asyncio.new_event_loop()
        thread = threading.Thread(
            target=self._run_bg_loop, daemon=True, name="qwen-tts-bg-loop"
        )
        thread.start()
        if not self._bg_ready.wait(timeout=10):
            raise RuntimeError("Qwen TTS 后台事件循环启动超时")

    def _run_bg_loop(self):
        loop = self._bg_loop
        asyncio.set_event_loop(loop)

        async def _main():
            self._bg_stop_event = asyncio.Event()
            # 会话级不设总超时，超时由每次请求自行指定
            self._session = aiohttp.ClientSession()
            self._bg_ready.set()
            await self._bg_stop_event.wait()
            await self._session.close()

        try:
            loop.run_until_complete(_main())
        finally:
            loop.close()

    # ------------------------------------------------------------------ 流水线：提交与发射
    def _submit_segment(self, text: str, is_last: bool):
        """把一个 LLM 分段切分后提交到后台合成（预取），不阻塞等待完成。"""
        text = MarkdownCleaner.clean_markdown(text)
        if self._correct_words_pattern:
            text = self._correct_words_pattern.sub(
                lambda m: self.correct_words[m.group(0)], text
            )
        if not text.strip():
            if is_last:
                self._process_before_stop_play_files()
            return

        pieces = self._split_text(text)
        sample_rate = self._output_sample_rate()
        for index, piece in enumerate(pieces):
            # 只有最后一片才允许触发收尾（推送 LAST），否则会提前结束播放
            piece_is_last = is_last and index == len(pieces) - 1
            # 预取上限：发射线程正在消费 1 段，队列里最多再排 1 段，
            # 即同时在途的 HTTP 请求最多 2 个
            while (
                len(self._pending_pieces) >= 1
                and not self.conn.client_abort
                and not self.conn.stop_event.is_set()
            ):
                time.sleep(0.02)
            if self.conn.client_abort or self.conn.stop_event.is_set():
                return
            record = {
                "queue": queue.Queue(),
                "text": piece,
                "is_last": piece_is_last,
                "sentence_id": getattr(self, "current_sentence_id", None),
                "future": None,
            }
            record["future"] = asyncio.run_coroutine_threadsafe(
                self._stream_piece(piece, sample_rate, record["queue"]),
                self._bg_loop,
            )
            self._pending_pieces.append(record)

    async def _stream_piece(self, piece: str, sample_rate: int, out_queue: queue.Queue):
        """在后台循环上合成一个分段，PCM 逐块送入 out_queue，结束放哨兵 None。"""
        try:
            await self._request_stream(
                piece, sample_rate, out_queue.put, session=self._session
            )
            logger.bind(tag=TAG).info(
                f"语音生成成功: {piece[:50]}，首包耗时: {self._last_ttfb:.2f}s"
            )
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            logger.bind(tag=TAG).error(
                f"Qwen TTS 合成失败: {piece[:50]}，错误: {e}"
            )
        finally:
            out_queue.put(None)

    def _drain_queue(self, out_queue: queue.Queue):
        while True:
            try:
                out_queue.get_nowait()
            except queue.Empty:
                return

    def _drop_pending(self):
        """丢弃所有在途分段（打断 / 新回合），并复位编码链路状态。"""
        while self._pending_pieces:
            record = self._pending_pieces.popleft()
            try:
                record["future"].cancel()
            except Exception:  # noqa: BLE001
                pass
            self._drain_queue(record["queue"])
        self._stop_after_drain = False
        self.pcm_buffer.clear()
        self._fade_in_pending = True
        if self._resampler:
            self._resampler.reset()

    def _audio_emitter_thread(self):
        """发射线程：按提交顺序把各分段 PCM 送入编码与播放流水线。

        合成是并发的（预取），但编码、淡入淡出、opus 编码与队列推送
        全部集中在本线程串行执行，天然保序且无共享状态竞争。
        """
        while not self.conn.stop_event.is_set():
            record = None
            if self._pending_pieces:
                record = self._pending_pieces.popleft()
            if record is None:
                time.sleep(0.02)
                continue
            try:
                self._emit_piece(record)
            except Exception as e:  # noqa: BLE001
                logger.bind(tag=TAG).error(
                    f"发射分段音频失败: {e}, 堆栈: {traceback.format_exc()}"
                )
                try:
                    record["future"].cancel()
                except Exception:  # noqa: BLE001
                    pass
            # 全部排空且 LAST 已到：补发 stop
            if not self._pending_pieces and self._stop_after_drain:
                self._stop_after_drain = False
                self._process_before_stop_play_files()

    def _emit_piece(self, record: dict):
        out_queue = record["queue"]
        piece = record["text"]
        sentence_id = record.get("sentence_id")

        resampler = self._ensure_resampler(self._output_sample_rate())
        if resampler:
            resampler.reset()
        self.pcm_buffer.clear()
        self._fade_in_pending = True
        self._emit_sentence_id = sentence_id
        first_audio_sent = False

        try:
            while True:
                if self.conn.client_abort:
                    # 丢弃本段剩余数据（句柄由 handle_opus 的 abort 守卫兜底）
                    try:
                        record["future"].cancel()
                    except Exception:  # noqa: BLE001
                        pass
                    self._drain_queue(out_queue)
                    return
                try:
                    chunk = out_queue.get(timeout=0.2)
                except queue.Empty:
                    continue
                if chunk is None:
                    break
                if not first_audio_sent:
                    first_audio_sent = True
                    self.tts_audio_queue.put(
                        (SentenceType.FIRST, [], piece, sentence_id)
                    )
                if resampler:
                    chunk = resampler.process(chunk)
                    if not chunk:
                        continue
                self._feed_pcm(chunk)

            # 段尾淡出并编码残留样本
            self._flush_pcm()
        finally:
            self._emit_sentence_id = None

        if record.get("is_last"):
            self._stop_after_drain = False
            self._process_before_stop_play_files()

    # ------------------------------------------------------------------ 文本线程
    def tts_text_priority_thread(self):
        """流式文本处理线程：切段后立即提交后台合成（预取下一段）。"""
        while not self._closed and not self.conn.stop_event.is_set():
            try:
                message = self.tts_text_queue.get(timeout=1)

                if self.conn.client_abort:
                    logger.bind(tag=TAG).info("收到打断信息，终止TTS文本处理线程")
                    self.tts_text_buff = []
                    self.processed_chars = 0
                    self._drop_pending()
                    continue

                if (
                    message.sentence_id
                    and self.conn.sentence_id
                    and message.sentence_id != self.conn.sentence_id
                ):
                    continue

                if message.sentence_type == SentenceType.FIRST:
                    self.current_sentence_id = message.sentence_id
                    self.tts_stop_request = False
                    self.processed_chars = 0
                    self.tts_text_buff = []
                    self.is_first_sentence = True
                    self.tts_audio_first_sentence = True
                    self.before_stop_play_files.clear()
                    self.reset_stream_state()
                    self._drop_pending()

                elif ContentType.TEXT == message.content_type:
                    self.tts_text_buff.append(message.content_detail)
                    segment_text = self._get_segment_text()
                    if segment_text:
                        self._submit_segment(segment_text, is_last=False)

                elif ContentType.FILE == message.content_type:
                    logger.bind(tag=TAG).info(
                        f"添加音频文件到待播放列表: {message.content_file}"
                    )
                    if message.content_file and os.path.exists(message.content_file):
                        self._process_audio_file_stream(
                            message.content_file,
                            callback=lambda audio_data: self.handle_audio_file(
                                audio_data, message.content_detail
                            ),
                        )

                if message.sentence_type == SentenceType.LAST:
                    self._process_remaining_text_stream()

            except queue.Empty:
                continue
            except Exception as e:  # noqa: BLE001
                logger.bind(tag=TAG).error(
                    f"处理TTS文本失败: {str(e)}, 类型: {type(e).__name__}, 堆栈: {traceback.format_exc()}"
                )

    def _process_remaining_text_stream(self):
        """把缓冲里剩下的文本合成完；LAST 到达时确保 stop 在全部音频后发出。"""
        full_text = "".join(self.tts_text_buff)
        remaining_text = full_text[self.processed_chars :]
        segment_text = (
            textUtils.get_string_no_punctuation_or_emoji(remaining_text)
            if remaining_text
            else ""
        )
        if segment_text:
            self._submit_segment(segment_text, is_last=True)
            self.processed_chars += len(full_text)
        else:
            # 没有新文本，但仍有分段在途：等发射线程排空后再发 stop，
            # 否则 stop 会抢在下一段音频之前发出
            if self._pending_pieces:
                self._stop_after_drain = True
            else:
                self._process_before_stop_play_files()

    # ------------------------------------------------------------------ 生命周期
    async def open_audio_channels(self, conn):
        await super().open_audio_channels(conn)
        # 后台事件循环（共享连接）+ 顺序发射线程
        self._ensure_bg_loop()
        self._emitter_thread = threading.Thread(
            target=self._audio_emitter_thread,
            daemon=True,
            name="qwen-tts-emitter",
        )
        self._emitter_thread.start()

    async def close(self):
        await super().close()
        # 停掉后台事件循环（共享会话随循环关闭）
        loop = self._bg_loop
        if loop is not None and loop.is_running():
            event = self._bg_stop_event
            if event is not None:
                loop.call_soon_threadsafe(event.set)

    def handle_opus(self, opus_data: bytes):
        """流水线发射出的帧归属提交时捕获的 sentence_id，避免串到新回合。"""
        if self._emit_sentence_id is not None:
            conn = getattr(self, "conn", None)
            if conn is not None and getattr(conn, "client_abort", False):
                return
            self.tts_audio_queue.put(
                (SentenceType.MIDDLE, opus_data, None, self._emit_sentence_id)
            )
            return
        super().handle_opus(opus_data)

    # ------------------------------------------------------------------ 单句合成
    async def text_to_speak(self, text, is_last=True, output_file=None):
        """同步合成一段文本（写文件模式 / 测试入口）。

        正常对话走流水线路径（``tts_text_priority_thread`` -> ``_submit_segment``），
        不经过本方法；本方法保留给：
        - 写文件模式（USB 回放 / 预生成音频），第二参数传字符串路径；
        - 测试直接调用验证编码链路，第二参数传布尔值。

        兼容父类抽象签名。
        """
        if isinstance(is_last, str):
            output_file, is_last = is_last, True

        text = (text or "").strip()
        to_file = bool(output_file)
        if not text:
            if is_last and not to_file:
                self._process_before_stop_play_files()
            return True

        sample_rate = self._output_sample_rate()
        resampler = None if to_file else self._ensure_resampler(sample_rate)
        if resampler:
            resampler.reset()

        collected = bytearray() if to_file else None
        self.pcm_buffer.clear()
        # 段首淡入：新分段的第一帧开头施加短淡入，消除与上一段尾的波形跳变
        self._fade_in_pending = not to_file
        first_audio_sent = False

        def on_pcm(pcm: bytes):
            nonlocal first_audio_sent
            if to_file:
                collected.extend(pcm)
                return
            if resampler:
                pcm = resampler.process(pcm)
                if not pcm:
                    return
            if not first_audio_sent:
                first_audio_sent = True
                self.tts_audio_queue.put(
                    (
                        SentenceType.FIRST,
                        [],
                        text,
                        getattr(self, "current_sentence_id", None),
                    )
                )
            self._feed_pcm(pcm)

        ok = True
        try:
            await self._request_stream(text, sample_rate, on_pcm)
        except Exception as e:  # noqa: BLE001
            ok = False
            logger.bind(tag=TAG).error(f"Qwen TTS 合成失败: {text[:50]}，错误: {e}")

        if to_file:
            if collected:
                self._write_wav(output_file, bytes(collected), sample_rate)
            return ok

        if ok:
            self._flush_pcm()
        if is_last:
            self._process_before_stop_play_files()
        return ok

    # ------------------------------------------------------------------ 测试入口
    def to_tts(self, text: str) -> list:
        """非流式入口（测试/预生成音频用），返回 opus 帧列表。"""
        text = MarkdownCleaner.clean_markdown(text or "")
        if self._correct_words_pattern:
            text = self._correct_words_pattern.sub(
                lambda m: self.correct_words[m.group(0)], text
            )
        if not text.strip():
            return []

        sample_rate = self._output_sample_rate()
        pcm = bytearray()

        async def _collect():
            for piece in self._split_text(text):
                await self._request_stream(piece, sample_rate, pcm.extend)

        start_time = time.time()
        try:
            asyncio.run(_collect())
        except Exception as e:  # noqa: BLE001
            logger.bind(tag=TAG).error(f"TTS请求异常: {e}")
            return []
        if not pcm:
            return []

        target_rate = int(getattr(self.conn, "sample_rate", 0) or 0) or sample_rate
        raw = bytes(pcm)
        if target_rate != sample_rate:
            resampler = PCMResampler(sample_rate, target_rate)
            raw = resampler.process(raw)

        encoder = opus_encoder_utils.OpusEncoderUtils(
            sample_rate=target_rate, channels=1, frame_size_ms=60
        )
        opus_datas = []
        frame_bytes = int(target_rate * 60 / 1000) * 2
        try:
            for offset in range(0, len(raw), frame_bytes):
                frame = raw[offset : offset + frame_bytes]
                is_last = offset + frame_bytes >= len(raw)
                if len(frame) < frame_bytes:
                    frame = frame + b"\x00" * (frame_bytes - len(frame))
                encoder.encode_pcm_to_opus_stream(
                    frame,
                    end_of_stream=is_last,
                    callback=lambda opus: opus_datas.append(opus),
                )
        finally:
            encoder.close()

        logger.bind(tag=TAG).info(
            f"TTS请求成功: {text}，耗时: {time.time() - start_time:.2f}秒"
        )
        return opus_datas

    async def close(self):
        await super().close()
