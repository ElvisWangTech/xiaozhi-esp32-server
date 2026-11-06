"""
Qwen3-TTS（qwen3-tts-flash）语音合成，接入方式：非实时 HTTP + SSE 流式输出。

官方文档：
- 非实时语音合成 Qwen-TTS API：https://help.aliyun.com/zh/model-studio/qwen-tts-api
- 音色列表：https://help.aliyun.com/zh/model-studio/qwen-tts-voice-list

协议要点：
- 端点为 DashScope 多模态生成接口，``X-DashScope-SSE: enable`` 开启流式；
- 请求体为 ``{"model": ..., "input": {"text", "voice", "language_type"}}``；
- 响应每个分片的 ``output.audio.data`` 是 base64 音频片，最后一个分片
  ``audio.data`` 为空、``audio.url`` 携带完整音频地址（有效期 24 小时）；
- **实测（2026-11）无论是否传 format 参数，返回的都是带 44 字节 RIFF 头的
  WAV（24kHz/16bit/单声道）**，首个分片含头、后续分片为纯 PCM；
  头部由 ``qwen_base.WavHeaderStripper`` 剥离，直接当裸 PCM 用会造成段首爆音；
- 单次请求文本上限 600 字符；采样率不可配置，设备声明的采样率不是 24 kHz
  时由 provider 侧重采样。
"""

from core.providers.tts.qwen_base import QwenTTSBase


class TTSProvider(QwenTTSBase):
    DEFAULT_API_URL = (
        "https://dashscope.aliyuncs.com/api/v1/services/aigc/"
        "multimodal-generation/generation"
    )
    DEFAULT_MODEL = "qwen3-tts-flash"
    DEFAULT_VOICE = "Cherry"
    NATIVE_SAMPLE_RATE = 24000
    SUPPORTS_SAMPLE_RATE = False
    DEFAULT_MAX_CHARS = 600

    def _build_payload(self, text: str, sample_rate: int) -> dict:
        payload = {
            "model": self.model,
            "input": {
                "text": text,
                "voice": self.voice,
            },
        }
        if self.language_type:
            # Auto / Chinese / English / German / Italian / Portuguese /
            # Spanish / Japanese / Korean / French / Russian
            payload["input"]["language_type"] = self.language_type
        return payload
