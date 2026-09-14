import base64

import requests

from config.logger import setup_logging
from core.providers.tts.base import TTSProviderBase
from core.utils.util import check_model_key

TAG = __name__
logger = setup_logging()


def _chat_completions_url(base_url: str) -> str:
    url = (base_url or "").strip().rstrip("/")
    if url.endswith("/chat/completions"):
        return url
    return f"{url}/chat/completions"


def _extract_audio_b64(payload: dict) -> str:
    choices = payload.get("choices") or []
    if not choices:
        return ""
    message = choices[0].get("message") or {}
    audio = message.get("audio")
    if isinstance(audio, dict):
        return audio.get("data") or ""
    if isinstance(audio, str):
        return audio
    return ""


class TTSProvider(TTSProviderBase):
    """小米 MiMo TTS（Chat Completions + audio，非 /v1/audio/speech）。"""

    def __init__(self, config, delete_audio_file):
        super().__init__(config, delete_audio_file)
        self.api_key = config.get("api_key")
        self.base_url = config.get("base_url", "https://api.xiaomimimo.com/v1")
        self.model = config.get("model", "mimo-v2.5-tts")
        if config.get("private_voice"):
            self.voice = config.get("private_voice")
        else:
            self.voice = config.get("voice", "冰糖")
        self.audio_file_type = config.get("format", "wav") or "wav"
        self.style_prompt = config.get("style_prompt", "")
        self.timeout = int(config.get("timeout", 60))

        model_key_msg = check_model_key("TTS", self.api_key or "")
        if model_key_msg:
            logger.bind(tag=TAG).error(model_key_msg)

    async def text_to_speak(self, text, output_file):
        messages = []
        if self.style_prompt:
            messages.append({"role": "user", "content": self.style_prompt})
        messages.append({"role": "assistant", "content": text})

        payload = {
            "model": self.model,
            "messages": messages,
            "audio": {
                "format": self.audio_file_type,
                "voice": self.voice,
            },
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "api-key": self.api_key,
            "Content-Type": "application/json",
        }
        response = requests.post(
            _chat_completions_url(self.base_url),
            json=payload,
            headers=headers,
            timeout=self.timeout,
        )
        if response.status_code != 200:
            raise Exception(
                f"MiMo TTS请求失败: {response.status_code} - {response.text}"
            )

        audio_b64 = _extract_audio_b64(response.json())
        if not audio_b64:
            raise Exception("MiMo TTS 响应中未找到音频数据")

        audio_bytes = base64.b64decode(audio_b64)
        if output_file:
            with open(output_file, "wb") as audio_file:
                audio_file.write(audio_bytes)
            return None
        return audio_bytes
