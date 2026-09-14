import base64
import os
import time
from typing import List, Optional, Tuple

import requests

from config.logger import setup_logging
from core.providers.asr.base import ASRProviderBase
from core.providers.asr.dto.dto import InterfaceType
from core.utils.util import check_model_key

TAG = __name__
logger = setup_logging()


def _chat_completions_url(base_url: str) -> str:
    url = (base_url or "").strip().rstrip("/")
    if url.endswith("/chat/completions"):
        return url
    return f"{url}/chat/completions"


def _extract_asr_text(payload: dict) -> str:
    choices = payload.get("choices") or []
    if not choices:
        return ""
    message = choices[0].get("message") or {}
    content = message.get("content")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text") or item.get("content")
                if text:
                    parts.append(str(text))
        return "".join(parts).strip()
    return ""


class ASRProvider(ASRProviderBase):
    """小米 MiMo ASR（Chat Completions + input_audio，非 Whisper transcriptions）。"""

    def __init__(self, config: dict, delete_audio_file: bool):
        super().__init__()
        self.interface_type = InterfaceType.NON_STREAM
        self.api_key = config.get("api_key")
        self.base_url = config.get("base_url", "https://api.xiaomimimo.com/v1")
        self.model = config.get("model_name", "mimo-v2.5-asr")
        self.language = config.get("language", "zh")
        self.output_dir = config.get("output_dir", "tmp/")
        self.delete_audio_file = delete_audio_file
        self.timeout = int(config.get("timeout", 60))
        os.makedirs(self.output_dir, exist_ok=True)

        model_key_msg = check_model_key("ASR", self.api_key or "")
        if model_key_msg:
            logger.bind(tag=TAG).error(model_key_msg)

    async def speech_to_text(
        self,
        opus_data: List[bytes],
        session_id: str,
        audio_format="opus",
        artifacts=None,
    ) -> Tuple[Optional[str], Optional[str]]:
        file_path = None
        try:
            if artifacts is None or not artifacts.pcm_bytes:
                return "", None
            file_path = artifacts.file_path

            wav_bytes = self._pcm_to_wav(artifacts.pcm_bytes)
            if not wav_bytes:
                logger.bind(tag=TAG).error("PCM 转 WAV 失败，无法调用 MiMo ASR")
                return "", file_path

            audio_b64 = base64.b64encode(wav_bytes).decode("utf-8")
            payload = {
                "model": self.model,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "input_audio",
                                "input_audio": {
                                    "data": f"data:audio/wav;base64,{audio_b64}"
                                },
                            }
                        ],
                    }
                ],
                "asr_options": {
                    "language": self.language or "auto",
                },
            }
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "api-key": self.api_key,
                "Content-Type": "application/json",
            }
            api_url = _chat_completions_url(self.base_url)
            start_time = time.time()
            response = requests.post(
                api_url,
                json=payload,
                headers=headers,
                timeout=self.timeout,
            )
            elapsed = time.time() - start_time
            if response.status_code != 200:
                raise Exception(
                    f"API请求失败: {response.status_code} - {response.text}"
                )

            result = response.json()
            text = _extract_asr_text(result)
            logger.bind(tag=TAG).debug(
                f"MiMo ASR 耗时: {elapsed:.3f}s | 结果: {text}"
            )
            return text, file_path
        except Exception as e:
            logger.bind(tag=TAG).error(f"语音识别失败: {e}")
            return "", file_path
