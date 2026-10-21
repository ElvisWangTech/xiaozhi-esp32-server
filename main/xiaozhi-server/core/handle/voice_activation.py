import asyncio
import re
import time
import uuid

from core.providers.tts.dto.dto import ContentType, TTSMessageDTO, SentenceType

TAG = __name__
# 报号中间常有停顿。这段静音内不播报，避免把用户后面的数字打断。
PHONE_PAUSE_SECONDS = 3
_CN_COUNT = ["零", "一", "两", "三", "四", "五", "六", "七", "八", "九", "十"]

CN_DIGITS = {
    "零": "0",
    "〇": "0",
    "一": "1",
    "幺": "1",
    "壹": "1",
    "二": "2",
    "两": "2",
    "贰": "2",
    "三": "3",
    "叁": "3",
    "四": "4",
    "肆": "4",
    "五": "5",
    "伍": "5",
    "六": "6",
    "陆": "6",
    "七": "7",
    "柒": "7",
    "八": "8",
    "捌": "8",
    "九": "9",
    "玖": "9",
}

CONFIRM_WORDS = {"对", "是", "好", "嗯", "行", "确认", "对的", "是的", "没错", "正确", "可以", "好的"}
REJECT_WORDS = ("不对", "不是", "不行", "错", "重说", "重来", "重新", "不要")


def compact_text(text):
    if not text:
        return ""
    return re.sub(r"[\s，。,.!！?？、]", "", str(text))


def digits_from_text(text):
    converted = []
    for char in str(text or ""):
        if char in CN_DIGITS:
            converted.append(CN_DIGITS[char])
        elif char.isdigit():
            converted.append(char)
    return "".join(converted)


def is_mobile_phone(digits):
    return bool(re.fullmatch(r"1\d{10}", digits or ""))


def extract_phone(text):
    digits = digits_from_text(text)
    if is_mobile_phone(digits):
        return digits
    for index in range(0, max(0, len(digits) - 10)):
        candidate = digits[index : index + 11]
        if is_mobile_phone(candidate):
            return candidate
    return None


def spoken_count(number):
    if 0 <= number < len(_CN_COUNT):
        return _CN_COUNT[number]
    return str(number)


def extract_code(text):
    digits = digits_from_text(text)
    if 4 <= len(digits) <= 8:
        return digits
    match = re.search(r"\d{6}", digits)
    if match:
        return match.group(0)
    match = re.search(r"\d{4,8}", digits)
    if match:
        return match.group(0)
    return None


def is_reject(text):
    compact = compact_text(text)
    return any(word in compact for word in REJECT_WORDS)


def is_confirm(text):
    compact = compact_text(text)
    if not compact or is_reject(compact):
        return False
    return compact in CONFIRM_WORDS


def is_resend(text):
    compact = compact_text(text)
    return any(word in compact for word in ("重发", "再发", "没收到", "重新发送"))


def spoken_digits(number):
    return "，".join(list(number))


def unwrap_user_text(text):
    raw = (text or "").strip()
    if raw.startswith("{") and raw.endswith("}"):
        try:
            import json

            data = json.loads(raw)
            if isinstance(data, dict) and data.get("content"):
                return str(data["content"])
        except Exception:
            return raw
    return raw


def speak(conn, text):
    # sentence_id 由 _say 先分配，并已先下发 tts start。这里不要再换编号。
    if not getattr(conn, "sentence_id", None):
        conn.sentence_id = uuid.uuid4().hex
    conn.tts.store_tts_text(conn.sentence_id, text)
    conn.tts.tts_text_queue.put(
        TTSMessageDTO(
            sentence_id=conn.sentence_id,
            sentence_type=SentenceType.FIRST,
            content_type=ContentType.ACTION,
        )
    )
    conn.tts.tts_one_sentence(conn, ContentType.TEXT, content_detail=text)
    conn.tts.tts_text_queue.put(
        TTSMessageDTO(
            sentence_id=conn.sentence_id,
            sentence_type=SentenceType.LAST,
            content_type=ContentType.ACTION,
        )
    )


def api_error_text(exc):
    message = str(exc or "")
    prefix = "API返回错误: "
    if message.startswith(prefix):
        message = message[len(prefix) :]
    message = message.strip()
    if not message or len(message) > 40:
        return "操作失败，请再说一次"
    return message


class VoiceActivationSession:
    ASK_PHONE = "ask_phone"
    CONFIRM_PHONE = "confirm_phone"
    ASK_CODE = "ask_code"
    ASK_NICKNAME = "ask_nickname"
    CONFIRM_NICKNAME = "confirm_nickname"

    def __init__(self, conn, stage="bind", announce_bind_success=False):
        self.conn = conn
        self.stage = stage
        self.state = self.ASK_PHONE if stage == "bind" else self.ASK_NICKNAME
        self.phone = None
        self.phone_digits = ""
        self.nickname = None
        self.announce_bind_success = announce_bind_success
        self._busy = False
        self._phone_epoch = 0
        self._phone_wait_task = None

    async def start(self):
        if self.state == self.ASK_NICKNAME:
            prefix = "绑定成功。" if self.announce_bind_success else ""
            await self._say(prefix + "请说出助手的昵称。")
            return
        await self._say("请说出您的手机号码，一共十一位。")

    async def handle_text(self, text):
        if self._busy:
            return
        # 正在播报时不接收，避免号码中间的停顿被当成新的一句话。
        if self._playback_active():
            return
        content = unwrap_user_text(text)
        if not content:
            return
        await self._send_recognized_text(content)
        wakeup_words = self.conn.config.get("wakeup_words") or []
        if content in wakeup_words:
            await self._repeat_prompt()
            return
        self._busy = True
        try:
            await self._dispatch(content)
        finally:
            self._busy = False

    async def _dispatch(self, text):
        if self.state == self.ASK_PHONE:
            await self._on_phone(text)
        elif self.state == self.CONFIRM_PHONE:
            await self._on_confirm_phone(text)
        elif self.state == self.ASK_CODE:
            await self._on_code(text)
        elif self.state == self.ASK_NICKNAME:
            await self._on_nickname(text)
        elif self.state == self.CONFIRM_NICKNAME:
            await self._on_confirm_nickname(text)

    def _invalidate_phone_wait(self):
        self._phone_epoch += 1
        task = self._phone_wait_task
        self._phone_wait_task = None
        if task is not None and not task.done():
            task.cancel()

    def _schedule_phone_wait(self):
        self._invalidate_phone_wait()
        epoch = self._phone_epoch
        self._phone_wait_task = asyncio.create_task(self._phone_wait(epoch))

    async def _phone_wait(self, epoch):
        """按麦克风静音计时。用户还在说，或只是换气，就不播报。"""
        quiet_since = time.monotonic()
        try:
            while True:
                await asyncio.sleep(0.2)
                if epoch != self._phone_epoch or self.state != self.ASK_PHONE:
                    return
                speaking = bool(getattr(self.conn, "client_have_voice", False))
                last_voice = getattr(self.conn, "vad_last_voice_time", 0) or 0
                if speaking or last_voice > 0:
                    quiet_since = None
                    continue
                if quiet_since is None:
                    quiet_since = time.monotonic()
                if time.monotonic() - quiet_since >= PHONE_PAUSE_SECONDS:
                    break
        except asyncio.CancelledError:
            return
        if epoch != self._phone_epoch or self.state != self.ASK_PHONE:
            return
        if not self.phone_digits:
            await self._say("没有听清手机号，请按顺序说出十一位手机号。")
            return
        missing = 11 - len(self.phone_digits)
        await self._say(f"还差{spoken_count(missing)}位，请继续说。")

    async def _accept_phone(self, phone):
        self._invalidate_phone_wait()
        self.phone_digits = ""
        self.phone = phone
        self.state = self.CONFIRM_PHONE
        await self._say(f"您的手机号是，{spoken_digits(phone)}。对吗？对的话请说确认，不对请说重说。")

    async def _on_phone(self, text):
        self._invalidate_phone_wait()
        digits = digits_from_text(text)
        if not digits and is_reject(text):
            self.phone_digits = ""
            await self._say("好的，请重新按顺序说出十一位手机号。")
            return
        if not digits:
            self._schedule_phone_wait()
            return

        # 某一句里已经包含完整号码时，直接取这个号码，不和前面的片段拼在一起。
        if self.phone_digits and len(digits) >= 11:
            phone = extract_phone(text)
            if phone:
                await self._accept_phone(phone)
                return

        self.phone_digits += digits
        if len(self.phone_digits) > 11:
            self.phone_digits = ""
            await self._say("位数超过十一位了，请重新按顺序说出手机号。")
            return
        if len(self.phone_digits) < 11:
            self._schedule_phone_wait()
            return
        if not is_mobile_phone(self.phone_digits):
            self.phone_digits = ""
            await self._say("手机号需要是以1开头的十一位数字，请重新说。")
            return
        await self._accept_phone(self.phone_digits)

    async def _on_confirm_phone(self, text):
        echoed = digits_from_text(text)
        if (
            echoed
            and self.phone
            and self.phone.startswith(echoed)
            and not is_confirm(text)
            and not is_reject(text)
        ):
            return
        corrected = extract_phone(text)
        if corrected and corrected != self.phone:
            self.phone = corrected
            await self._say(f"您的手机号是，{spoken_digits(self.phone)}。对吗？")
            return
        if is_reject(text):
            self.phone = None
            self.phone_digits = ""
            self.state = self.ASK_PHONE
            await self._say("好的，请重新按顺序说出十一位手机号。")
            return
        if not is_confirm(text):
            await self._say("请说确认，或者说重说。")
            return
        await self._send_sms()

    async def _send_sms(self):
        try:
            await self._post(
                "/device/voice-activation/sms",
                {"macAddress": self._mac(), "phone": self.phone},
            )
        except Exception as exc:
            self.conn.logger.bind(tag=TAG).warning(f"发送验证码失败: {exc}")
            await self._say(api_error_text(exc) + "。确认后我会再发一次。")
            return
        self.state = self.ASK_CODE
        self.conn.last_activity_time = time.time() * 1000
        await self._say("验证码已发送。请说出短信里的验证码。没收到可以说重发。")

    async def _on_code(self, text):
        if is_resend(text):
            self.state = self.CONFIRM_PHONE
            await self._send_sms()
            return
        if is_reject(text):
            self.phone = None
            self.phone_digits = ""
            self.state = self.ASK_PHONE
            await self._say("好的，请重新按顺序说出十一位手机号。")
            return
        code = extract_code(text)
        if not code:
            await self._say("没有听清验证码，请再说一遍。")
            return
        try:
            await self._post(
                "/device/voice-activation/bind",
                {"macAddress": self._mac(), "phone": self.phone, "code": code},
            )
        except Exception as exc:
            self.conn.logger.bind(tag=TAG).warning(f"语音绑定失败: {exc}")
            await self._say(api_error_text(exc) + "。请再说一遍验证码。")
            return
        try:
            ready = await self.conn.complete_voice_bind_setup()
        except Exception as exc:
            self.conn.logger.bind(tag=TAG).warning(f"绑定后加载配置失败: {exc}")
            ready = False
        if not ready:
            self.conn.voice_activation_handler = None
            await self._say("绑定已经成功。请断开后重新连接，继续设置助手昵称。")
            return
        self.stage = "nickname"
        self.state = self.ASK_NICKNAME
        self.announce_bind_success = True
        await self.start()

    async def _on_nickname(self, text):
        nickname = self._clean_nickname(text)
        if not nickname:
            await self._say("没有听清昵称，请再说一次。")
            return
        self.nickname = nickname
        self.state = self.CONFIRM_NICKNAME
        await self._say(f"助手的昵称是{nickname}，对吗？")

    async def _on_confirm_nickname(self, text):
        if is_reject(text):
            self.nickname = None
            self.state = self.ASK_NICKNAME
            await self._say("好的，请重新说出助手昵称。")
            return
        corrected = self._clean_nickname(text)
        if corrected and not is_confirm(text) and corrected != self.nickname:
            self.nickname = corrected
            await self._say(f"助手的昵称是{self.nickname}，对吗？")
            return
        if not is_confirm(text):
            await self._say("请说确认，或者说重说。")
            return
        try:
            await self._post(
                "/device/voice-activation/nickname",
                {"macAddress": self._mac(), "nickname": self.nickname},
            )
        except Exception as exc:
            self.conn.logger.bind(tag=TAG).warning(f"设置昵称失败: {exc}")
            await self._say(api_error_text(exc))
            return
        await self.conn.refresh_agent_prompt()
        self.conn.voice_activation_handler = None
        self.conn.voice_onboarding = None
        await self._say(f"好的，我是{self.nickname}。设置完成，我们可以开始聊天了。")

    def _playback_active(self):
        tts = getattr(self.conn, "tts", None)
        if tts is not None and (
            not tts.tts_text_queue.empty() or not tts.tts_audio_queue.empty()
        ):
            return True
        controller = getattr(self.conn, "audio_rate_controller", None)
        if controller is None:
            return False
        if not controller.queue_empty_event.is_set():
            return True
        last_empty = getattr(controller, "_last_queue_empty_time", 0) or 0
        return last_empty > 0 and time.monotonic() - last_empty < 0.6

    async def _repeat_prompt(self):
        if self.state == self.ASK_PHONE:
            if self.phone_digits:
                missing = 11 - len(self.phone_digits)
                await self._say(f"还差{spoken_count(missing)}位，请继续说。")
            else:
                await self._say("请按顺序说出十一位手机号。")
        elif self.state == self.CONFIRM_PHONE:
            await self._say(f"您的手机号是，{spoken_digits(self.phone or '')}。对吗？")
        elif self.state == self.ASK_CODE:
            await self._say("请说出短信验证码。")
        elif self.state == self.ASK_NICKNAME:
            await self._say("请说出助手的昵称。")
        elif self.state == self.CONFIRM_NICKNAME:
            await self._say(f"助手的昵称是{self.nickname}，对吗？")

    async def _send_recognized_text(self, text):
        """把识别文本回给设备。不发 tts start，避免还在收号时设备退出录音。"""
        from core.handle.sendAudioHandle import send_display_message
        from core.utils import textUtils

        display = textUtils.get_string_no_punctuation_or_emoji(text) or text
        try:
            await send_display_message(self.conn, display)
        except Exception as exc:
            self.conn.logger.bind(tag=TAG).warning(f"回传识别文本失败: {exc}")

    async def _say(self, text):
        from core.handle.sendAudioHandle import send_tts_message

        self.conn.sentence_id = uuid.uuid4().hex
        self.conn.client_abort = False
        try:
            await send_tts_message(self.conn, "start")
        except Exception as exc:
            self.conn.logger.bind(tag=TAG).warning(f"发送语音开始失败: {exc}")
        self.conn.client_is_speaking = True
        speak(self.conn, text)

    def _mac(self):
        return self.conn.headers.get("device-id")

    @staticmethod
    def _clean_nickname(text):
        nickname = compact_text(unwrap_user_text(text))
        if not nickname or is_confirm(nickname) or is_reject(nickname):
            return None
        if len(nickname) > 12:
            return None
        return nickname

    async def _post(self, path, payload):
        from config.manage_api_client import ManageApiClient

        if ManageApiClient._instance is None:
            raise Exception("API返回错误: 语音服务未连接管理后台")
        return await ManageApiClient._instance._execute_async_request(
            "POST", path, json=payload
        )
