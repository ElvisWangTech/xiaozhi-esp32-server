"""
Qwen-Audio-TTS 语音合成，接入方式：非实时 HTTP + SSE 流式输出。

官方文档：
- Python SDK 参考：https://help.aliyun.com/zh/model-studio/qwen-audio-tts-http-python-sdk
- HTTP API：https://help.aliyun.com/zh/model-studio/qwen-audio-tts-http-api
- 音色列表：https://help.aliyun.com/zh/model-studio/qwen-audio-tts-voice-list

与 Qwen3-TTS 的差异：
- 端点为 ``/api/v1/services/audio/tts/SpeechSynthesizer``；
- ``input`` 里可以直接指定 ``format`` / ``sample_rate`` / ``rate`` / ``pitch`` /
  ``volume`` / ``instruction`` / ``language_hints``；
- 采样率支持 8000 / 16000 / 22050 / 24000 / 44100 / 48000，因此器件侧声明的
  采样率可直接透传，无需重采样；
- 支持情感与富语言标签（如 ``[excited]``、``[laughing]``）直接写在文本里；
- 相对 Qwen3-TTS 表达力更强（音色更自然、支持指令与方言），首包延迟略高。
"""

from core.providers.tts.qwen_base import QwenTTSBase


class TTSProvider(QwenTTSBase):
    DEFAULT_API_URL = (
        "https://dashscope.aliyuncs.com/api/v1/services/audio/tts/SpeechSynthesizer"
    )
    DEFAULT_MODEL = "qwen-audio-3.1-tts-flash"
    DEFAULT_VOICE = "longanhuan_v3.1"
    NATIVE_SAMPLE_RATE = 24000
    SUPPORTS_SAMPLE_RATE = True
    SUPPORTED_SAMPLE_RATES = (8000, 16000, 22050, 24000, 44100, 48000)
    DEFAULT_MAX_CHARS = 600

    # 面板上的音量/语速/音调百分比 -> 接口参数
    TTS_PARAM_CONFIG = [
        ("ttsVolume", "volume", 0, 100, 50, lambda v: int(round(v))),
        ("ttsRate", "rate", 0.5, 2.0, 1.0, lambda v: round(float(v), 2)),
        ("ttsPitch", "pitch", 0.5, 2.0, 1.0, lambda v: round(float(v), 2)),
    ]

    def __init__(self, config, delete_audio_file):
        super().__init__(config, delete_audio_file)

        # 以下参数默认不下发，只有显式配置时才写入请求体，
        # 避免不同模型版本对未知字段的兼容性差异。
        # 管理页面表单未填时会传空字符串，这里统一按 None 处理。
        self.volume = self._parse_optional_number(config.get("volume"), int)
        self.rate = self._parse_optional_number(config.get("rate"), float)
        self.pitch = self._parse_optional_number(config.get("pitch"), float)

        self.instruction = config.get("instruction")
        self.language_hints = config.get("language_hints")

        # 面板百分比参数优先覆盖
        self._apply_percentage_params(config)

    @staticmethod
    def _parse_optional_number(value, caster):
        """空串/None/非法值返回 None（参数不下发），合法值转换为数字。"""
        if value is None:
            return None
        if isinstance(value, str) and not value.strip():
            return None
        try:
            return caster(value)
        except (TypeError, ValueError):
            return None

    def _build_payload(self, text: str, sample_rate: int) -> dict:
        input_payload = {
            "text": text,
            "voice": self.voice,
            # 固定请求裸 PCM：此处按 16bit/单声道裸流直接送 Opus 编码器
            "format": "pcm",
            "sample_rate": int(sample_rate),
        }

        if self.instruction:
            input_payload["instruction"] = self.instruction
        if self.language_hints:
            input_payload["language_hints"] = (
                [self.language_hints]
                if isinstance(self.language_hints, str)
                else list(self.language_hints)
            )
        if self.volume is not None:
            input_payload["volume"] = self.volume
        if self.rate is not None:
            input_payload["rate"] = self.rate
        if self.pitch is not None:
            input_payload["pitch"] = self.pitch

        return {"model": self.model, "input": input_payload}
