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
SKIP_SKILLS_WORDS = ("稍后再说", "以后再说", "先聊天", "跳过", "不用了", "算了", "取消")
SKILLS_COMMAND_WORDS = ("绑定技能", "配置技能", "技能绑定", "设置技能")


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


def is_skills_command(text):
    compact = compact_text(unwrap_user_text(text))
    return any(word in compact for word in SKILLS_COMMAND_WORDS)


def is_skip_skills(text):
    compact = compact_text(text)
    if not compact:
        return False
    # 「不要跳过」表示继续，不当作放弃
    if "不要跳过" in compact or "别跳过" in compact:
        return False
    return any(word in compact for word in SKIP_SKILLS_WORDS)


def spoken_digits(number):
    return "，".join(list(number))


async def start_skills_onboarding(conn, announce=None):
    """日常对话中主动说「绑定技能」时重新进入技能绑定。"""
    if getattr(conn, "voice_activation_handler", None) is not None:
        handler = conn.voice_activation_handler
        if getattr(handler, "stage", None) == "skills":
            await handler._bind_skills()
            return
        await handler._say("当前还在完成其他设置，请稍后再试。")
        return
    conn.voice_onboarding = "skills"
    conn.skills_pending = True
    conn.voice_activation_handler = VoiceActivationSession(conn, stage="skills")
    if announce:
        await conn.voice_activation_handler._say(announce)
    await conn.voice_activation_handler.start()


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
    RETRY_SKILLS = "retry_skills"
    ASK_AUTH_CODE = "ask_auth_code"
    ASK_REBOOT = "ask_reboot"

    # 方案 B：默认依次开通；瑞幸先于滴滴（滴滴常需实名/免密支付）
    # 不询问「要开通吗」；仅 waiting_code 时打断要验证码
    AUTH_QUEUE = (
        ("luckin", "瑞幸咖啡"),
        ("didi", "滴滴出行"),
    )

    def __init__(self, conn, stage="bind", announce_bind_success=False):
        self.conn = conn
        self.stage = stage
        if stage == "bind":
            self.state = self.ASK_PHONE
        elif stage == "nickname":
            self.state = self.ASK_NICKNAME
        else:
            self.state = self.RETRY_SKILLS
        self.phone = None
        self.phone_digits = ""
        self.nickname = None
        self.announce_bind_success = announce_bind_success
        self.auth_queue = []
        self.auth_service = None
        self.auth_label = None
        self.auth_task_id = None
        self.auth_done = []
        self.auth_failed = []
        self._busy = False
        self._phone_epoch = 0
        self._phone_wait_task = None

    async def start(self):
        if self.stage == "skills":
            await self._bind_skills()
            return
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
        elif self.state == self.RETRY_SKILLS:
            await self._on_retry_skills(text)
        elif self.state == self.ASK_AUTH_CODE:
            await self._on_auth_code(text)
        elif self.state == self.ASK_REBOOT:
            await self._on_ask_reboot(text)

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
        self.conn.voice_onboarding = "skills"
        self.stage = "skills"
        await self._say(f"好的，我是{self.nickname}。正在绑定技能。")
        await self._bind_skills()

    async def _bind_skills(self):
        try:
            await self._post(
                "/device/voice-activation/skills",
                {"macAddress": self._mac()},
            )
        except Exception as exc:
            self.conn.logger.bind(tag=TAG).warning(f"技能绑定失败: {exc}")
            self.state = self.RETRY_SKILLS
            self.conn.skills_pending = True
            self.conn.voice_onboarding = "skills"
            await self._say(api_error_text(exc) + "。请说确认重试，或者说稍后再说。")
            return
        self.conn.voice_onboarding = "skills"
        self.conn.skills_pending = True
        self.auth_queue = list(self.AUTH_QUEUE)
        self.auth_service = None
        self.auth_label = None
        self.auth_task_id = None
        self.auth_done = []
        self.auth_failed = []
        first_label = self.AUTH_QUEUE[0][1] if self.AUTH_QUEUE else "技能"
        await self._say(f"通用技能已绑定。正在开通{first_label}。")
        await self._run_next_auth()

    def _mark_auth_failed(self, label, reason=""):
        name = label or "技能"
        if name not in self.auth_failed:
            self.auth_failed.append(name)
        return reason

    def _auth_finish_prefix(self):
        """汇总开通结果，失败项必须口头提示。"""
        parts = []
        if self.auth_done:
            parts.append("、".join(self.auth_done) + "已开通")
        if self.auth_failed:
            failed = "、".join(self.auth_failed)
            tip = f"{failed}开通失败"
            if any("滴滴" in name for name in self.auth_failed):
                tip += "。滴滴出行可能需要先完成实名认证和免密支付后再试"
            tip += "。可以说绑定技能重新开通"
            parts.append(tip)
        if not parts:
            return "技能引导已结束。"
        return "。".join(parts) + "。"

    async def _run_next_auth(self):
        if not self.auth_queue:
            await self._finish_skills_and_ask_reboot(self._auth_finish_prefix())
            return
        service, label = self.auth_queue.pop(0)
        self.auth_service = service
        self.auth_label = label
        self.auth_task_id = None
        first_service = self.AUTH_QUEUE[0][0] if self.AUTH_QUEUE else None
        if service != first_service:
            await self._say(f"正在开通{label}。")
        try:
            result = await self._post(
                "/device/voice-activation/skills/auth",
                {"macAddress": self._mac(), "service": service},
            )
        except Exception as exc:
            self.conn.logger.bind(tag=TAG).warning(f"{label}授权启动失败: {exc}")
            reason = api_error_text(exc)
            self._mark_auth_failed(label, reason)
            await self._say(f"{label}开通失败：{reason}。")
            await self._run_next_auth()
            return
        await self._handle_auth_result(result or {})

    async def _handle_auth_result(self, result):
        status = str((result or {}).get("status") or "")
        task_id = (result or {}).get("taskId")
        message = (result or {}).get("message") or ""
        label = self.auth_label or "技能"
        if task_id:
            self.auth_task_id = task_id

        if status == "waiting_code":
            self.state = self.ASK_AUTH_CODE
            self.conn.last_activity_time = time.time() * 1000
            await self._say(f"请说出{label}短信里的验证码。不要可以说跳过。")
            return

        if status == "done":
            if label not in self.auth_done:
                self.auth_done.append(label)
            await self._say(f"{label}已开通。")
            await self._run_next_auth()
            return

        if status in ("failed", "skipped"):
            if status == "skipped":
                await self._say(f"好的，已跳过{label}。")
            else:
                reason = message.strip() if message else ""
                self._mark_auth_failed(label, reason)
                if reason:
                    await self._say(f"{label}开通失败：{reason}。")
                else:
                    await self._say(f"{label}开通失败。")
            await self._run_next_auth()
            return

        # pending：轮询至 waiting_code / done / failed
        for _ in range(60):
            await asyncio.sleep(1.5)
            if not self.auth_task_id:
                break
            try:
                polled = await self._post(
                    "/device/voice-activation/skills/auth/status",
                    {"macAddress": self._mac(), "taskId": self.auth_task_id},
                )
            except Exception as exc:
                self.conn.logger.bind(tag=TAG).warning(f"{label}授权状态查询失败: {exc}")
                reason = api_error_text(exc)
                self._mark_auth_failed(label, reason)
                await self._say(f"{label}开通失败：{reason}。")
                await self._run_next_auth()
                return
            polled_status = str((polled or {}).get("status") or "")
            if polled_status == "pending":
                continue
            await self._handle_auth_result(polled or {})
            return

        await self._cancel_auth_task()
        self._mark_auth_failed(label, "等待超时")
        await self._say(f"{label}开通失败：等待超时。")
        await self._run_next_auth()

    async def _cancel_auth_task(self):
        if not self.auth_task_id:
            return
        try:
            await self._post(
                "/device/voice-activation/skills/auth/cancel",
                {"macAddress": self._mac(), "taskId": self.auth_task_id},
            )
        except Exception as exc:
            self.conn.logger.bind(tag=TAG).info(f"取消授权任务失败: {exc}")
        self.auth_task_id = None

    async def _on_auth_code(self, text):
        label = self.auth_label or "技能"
        if is_skip_skills(text) or is_reject(text):
            await self._cancel_auth_task()
            await self._say(f"好的，已跳过{label}。")
            await self._run_next_auth()
            return
        if is_resend(text):
            await self._say("验证码由服务方发送，请留意短信。收到后直接说出验证码。")
            return
        code = extract_code(text)
        if not code:
            await self._say("没有听清验证码，请再说一遍。或者说跳过。")
            return
        if not self.auth_task_id:
            self._mark_auth_failed(label, "任务已失效")
            await self._say(f"{label}开通失败：任务已失效。")
            await self._run_next_auth()
            return
        try:
            result = await self._post(
                "/device/voice-activation/skills/auth/code",
                {
                    "macAddress": self._mac(),
                    "taskId": self.auth_task_id,
                    "code": code,
                },
            )
        except Exception as exc:
            self.conn.logger.bind(tag=TAG).warning(f"{label}提交验证码失败: {exc}")
            await self._say(api_error_text(exc) + "。请再说一遍验证码，或者说跳过。")
            return
        status = str((result or {}).get("status") or "")
        if status == "waiting_code":
            await self._say("验证码不正确或尚未生效，请再说一遍，或者说跳过。")
            return
        if status == "done":
            if label not in self.auth_done:
                self.auth_done.append(label)
            await self._say(f"{label}已开通。")
            await self._run_next_auth()
            return
        if status in ("failed", "skipped"):
            await self._handle_auth_result(result or {})
            return
        # pending：提交后继续轮询
        await self._say("验证码已提交，正在登录。")
        await self._handle_auth_result(result or {})

    async def _finish_skills_and_ask_reboot(self, prefix=""):
        try:
            await self._post(
                "/device/voice-activation/skills/complete",
                {"macAddress": self._mac()},
            )
        except Exception as exc:
            self.conn.logger.bind(tag=TAG).warning(f"完成技能引导失败: {exc}")
            # 即使 complete 失败，也允许进入重启询问，避免卡在引导
        self.conn.voice_onboarding = None
        self.conn.skills_pending = False
        self.state = self.ASK_REBOOT
        summary = (prefix or "").strip() or "技能引导已结束。"
        if not summary.endswith("。"):
            summary += "。"
        await self._say(summary + "是否重启设备？")

    async def _on_retry_skills(self, text):
        if is_skip_skills(text):
            self.conn.voice_activation_handler = None
            # 数据库仍是 skills，日常对话里可以说「绑定技能」再进来
            self.conn.voice_onboarding = None
            self.conn.skills_pending = True
            await self._say("好的。之后想绑定技能，可以说绑定技能。现在可以开始聊天了。")
            return
        compact = compact_text(text)
        if (
            is_confirm(text)
            or is_skills_command(text)
            or "重试" in compact
            or "再试" in compact
            or "不要跳过" in compact
        ):
            await self._bind_skills()
            return
        await self._say("请说确认重试，或者说稍后再说。")

    async def _on_ask_reboot(self, text):
        if is_reject(text):
            await self._finish_without_reboot()
            return
        compact = compact_text(text)
        if is_confirm(text) or "重启" in compact:
            await self._reboot_device()
            return
        await self._say("请说确认重启设备，或者说不要。")

    async def _finish_without_reboot(self):
        self.conn.voice_activation_handler = None
        self.conn.voice_onboarding = None
        self.conn.skills_pending = False
        await self._say("好的，我们可以开始聊天了。")

    async def _reboot_device(self):
        await self._say("好的，正在重启设备。")
        for _ in range(50):
            if not self._playback_active():
                break
            await asyncio.sleep(0.2)
        # 工具列表可能还在异步拉取，稍等再匹配
        tool_name = None
        for _ in range(20):
            tool_name = self._find_reboot_tool()
            if tool_name:
                break
            mcp_client = getattr(self.conn, "mcp_client", None)
            if mcp_client is None:
                break
            await asyncio.sleep(0.25)
        if not tool_name:
            available = list(
                (getattr(getattr(self.conn, "mcp_client", None), "name_mapping", None) or {}).values()
            )
            self.conn.logger.bind(tag=TAG).warning(
                f"未找到重启工具，当前设备 MCP 工具: {available}"
            )
            self.conn.voice_activation_handler = None
            self.conn.voice_onboarding = None
            self.conn.skills_pending = False
            await self._say("暂时无法自动重启，请手动重启设备。")
            return
        try:
            from core.providers.tools.device_mcp.mcp_handler import call_mcp_tool

            # 设备马上重启，不必久等结果
            await call_mcp_tool(self.conn, self.conn.mcp_client, tool_name, "{}", timeout=5)
        except Exception as exc:
            # 重启过程中连接断开也属正常
            self.conn.logger.bind(tag=TAG).info(f"已下发重启工具 {tool_name}: {exc}")
        self.conn.voice_activation_handler = None
        self.conn.voice_onboarding = None
        self.conn.skills_pending = False

    def _find_reboot_tool(self):
        """本设备固件重启工具是 self.qiniu.custom.restart，不是标准 self.reboot。"""
        mcp_client = getattr(self.conn, "mcp_client", None)
        if mcp_client is None:
            return None
        mapping = getattr(mcp_client, "name_mapping", None) or {}
        if not mapping:
            return None

        preferred = (
            "self_qiniu_custom_restart",
            "self.qiniu.custom.restart",
            "self_reboot",
            "self.reboot",
            "reboot",
            "restart",
        )
        for name in preferred:
            if name in mapping:
                return name
            for sanitized, original in mapping.items():
                if name == sanitized or name == original:
                    return sanitized

        for sanitized, original in mapping.items():
            haystack = f"{sanitized} {original}".lower()
            if "wifi" in haystack or "switch" in haystack:
                continue
            if (
                "reboot" in haystack
                or haystack.endswith("restart")
                or ".restart" in (original or "").lower()
                or "_restart" in sanitized
            ):
                return sanitized
        return None

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
        elif self.state == self.RETRY_SKILLS:
            await self._say("技能绑定还未完成，请说确认重试，或者说稍后再说。")
        elif self.state == self.ASK_AUTH_CODE:
            label = self.auth_label or "技能"
            await self._say(f"请说出{label}短信验证码。或者说跳过。")
        elif self.state == self.ASK_REBOOT:
            await self._say("技能已经绑定好了。是否重启设备？")

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
