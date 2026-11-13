import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from core.handle import voice_activation as voice


class VoiceActivationFlowTest(unittest.IsolatedAsyncioTestCase):
    def make_session(self, stage="bind", server=None):
        conn = SimpleNamespace(
            config={},
            headers={"device-id": "test-device"},
            logger=Mock(),
            server=server,
        )
        session = voice.VoiceActivationSession(conn, stage=stage)
        session._say = AsyncMock()
        session._post = AsyncMock()
        session._wait_spoken = AsyncMock()
        session._schedule_phone_wait = Mock()
        return session

    async def test_onboarding_progresses_from_greeting_to_reboot(self):
        session = self.make_session()
        session._start_idle_watch = Mock()
        session._schedule_asr_ready = Mock()
        session._send_recognized_text = AsyncMock()
        session._reboot_device = AsyncMock()
        session.conn.complete_voice_bind_setup = AsyncMock(return_value=True)
        session.conn.refresh_agent_prompt = AsyncMock()
        session._post.side_effect = [
            None, None, None, ["可爱女生", "星际游子"], None, None,
            {"status": "waiting_code", "taskId": "luckin-test"},
            {"status": "done"}, {"status": "waiting_code", "taskId": "didi-test"},
            None, None,
        ]
        turns = [
            ("叫我老周", {"intent": "answer", "title": "老周"}, session.CONFIRM_GREET),
            ("对", {"intent": "accept"}, session.ASK_PHONE),
            ("我的手机号是123然后456789，13", {"intent": "phone", "phone": "12345678913"}, session.CONFIRM_PHONE),
            ("对", {"intent": "accept"}, session.ASK_CODE),
            ("验证码是123456", {"intent": "code", "slot": 1}, session.ASK_NICKNAME),
            ("你叫小七", {"intent": "answer", "nickname": "小七"}, session.CONFIRM_NICKNAME),
            ("对", {"intent": "accept"}, session.ASK_PERSONA),
            ("第二个", {"intent": "answer", "ordinal": 2}, session.CONFIRM_PERSONA),
            ("就这个", {"intent": "accept"}, session.ASK_SUPPLEMENT),
            ("说话温柔一点", {"action": "content", "text": "说话温柔一点"}, session.ASK_SUPPLEMENT),
            ("就这样吧", {"action": "end"}, session.CONFIRM_SUPPLEMENT),
            ("对", {"intent": "accept"}, session.ASK_AUTH_CONSENT),
            ("两项都开", {"intent": "accept", "services": ["luckin", "didi"]}, session.ASK_AUTH_CODE),
            ("123456", {"intent": "code", "slot": 1}, session.ASK_AUTH_CODE),
            ("滴滴先不用", {"intent": "skip"}, session.REBOOTING),
        ]
        model = AsyncMock(side_effect=[reply for _, reply, _ in turns])
        await session.start()
        with patch.object(voice, "_ask_llm_json", model):
            for utterance, _, expected_state in turns:
                await session.handle_text(utterance)
                self.assertEqual(session.state, expected_state, utterance)
        self.assertEqual(session.auth_done, ["瑞幸咖啡"])
        self.assertEqual(session.auth_skipped, ["特斯拉", "滴滴出行"])
        role_call = session._post.await_args_list[4]
        self.assertEqual(role_call.args[1]["ownerTitle"], "老周")
        self.assertEqual(role_call.args[1]["templateName"], "星际游子")
        self.assertEqual(role_call.args[1]["supplement"], "说话温柔一点")
        self.assertEqual(session._post.await_args.args[0], "/device/voice-activation/skills/complete")
        session._reboot_device.assert_awaited_once()

    async def test_opening_skip_or_phone_does_not_bypass_name(self):
        session = self.make_session()
        model = AsyncMock(side_effect=[
            {"intent": "skip"}, {"intent": "phone", "phone": "13800138000"},
        ])
        with patch.object(voice, "_ask_llm_json", model):
            await session._on_greet("先跳过")
            await session._on_greet("13800138000")
        self.assertEqual(session.state, session.GREET)
        session._post.assert_not_awaited()

    async def test_first_authorization_decline_completes_without_sending_codes(self):
        session = self.make_session("skills")
        session.include_auth = True
        session.ask_auth_consent = True
        session._reboot_device = AsyncMock()
        await session._bind_skills()
        self.assertEqual(session.state, session.ASK_AUTH_CONSENT)
        self.assertEqual(session._post.await_count, 1)
        with patch.object(voice, "_ask_llm_json", AsyncMock(return_value={"intent": "decline"})):
            await session._dispatch("以后再说")
        self.assertEqual([call.args[0] for call in session._post.await_args_list], [
            "/device/voice-activation/skills", "/device/voice-activation/skills/complete",
        ])
        self.assertEqual(session.state, session.REBOOTING)

    async def test_first_authorization_can_choose_only_one_service(self):
        session = self.make_session("skills")
        session.include_auth = True
        session.ask_auth_consent = True
        session._post.side_effect = [None, {"status": "waiting_code", "taskId": "didi"}]
        await session._bind_skills()
        with patch.object(voice, "_ask_llm_json", AsyncMock(
            return_value={"intent": "accept", "services": ["didi"]}
        )):
            await session._dispatch("瑞幸先不用，只开滴滴")
        self.assertEqual(session.auth_skipped, ["瑞幸咖啡", "特斯拉"])
        self.assertEqual(session.state, session.ASK_AUTH_CODE)
        self.assertEqual(session._post.await_count, 2)
        self.assertEqual(session._post.await_args.args[1]["service"], "didi")

    async def test_active_authorization_starts_without_consent_and_prompts_before_request(self):
        session = self.make_session("skills")
        session.include_auth = True

        async def post(path, payload):
            if path.endswith("/auth"):
                self.assertIn("请准备接收短信验证码", session._say.await_args.args[0])
                self.assertEqual(payload["service"], "luckin")
                return {"status": "waiting_code", "taskId": "luckin"}
            return None

        session._post.side_effect = post
        await session._bind_skills()
        self.assertEqual(session.state, session.ASK_AUTH_CODE)

    async def test_role_resume_uses_saved_owner_without_asking_again(self):
        session = self.make_session("role")
        session.conn.config["prompt"] = "默认角色\n[主人称呼]\n请称呼用户为「老周」。"
        resumed = voice.VoiceActivationSession(session.conn, stage="role")
        resumed._start_idle_watch = Mock()
        resumed._post = AsyncMock(return_value=["可爱女生", "星际游子"])
        resumed._say = AsyncMock()
        await resumed.start()
        self.assertEqual(resumed.owner_title, "老周")
        self.assertEqual(resumed.state, resumed.ASK_PERSONA)
        self.assertIn("星际游子", resumed._say.await_args.args[0])

    async def test_sms_cooldown_missing_seconds_uses_last_successful_send(self):
        session = self.make_session()
        session.phone = "13800138000"
        cooldown = RuntimeError("cooldown")
        cooldown.code = voice.VOICE_SMS_COOLDOWN
        session._post.side_effect = [None, cooldown]
        with patch.object(voice.time, "monotonic", return_value=100):
            await session._send_sms()
        with patch.object(voice.time, "monotonic", return_value=120):
            await session._send_sms(resending=True)
            self.assertIn("还要等40秒", session._say.await_args.args[0])
            self.assertEqual(session._sms_cooldown_remaining({"retryAfterSeconds": "12"}), 12)
            session.phone = "13900139000"
            self.assertIsNone(session._sms_cooldown_remaining(None))

    async def test_code_stage_help_answers_without_submitting_or_resending(self):
        for stage, utterance, expected in (
            ("bind", "你好，犀牛，继续吧。", "我在。收到短信后，把验证码念给我就好。"),
            ("skills", "还在吗？在吗？", "我在。收到滴滴出行的短信后，把验证码念给我就好。"),
        ):
            session = self.make_session(stage)
            session.state = session.ASK_CODE if stage == "bind" else session.ASK_AUTH_CODE
            session.auth_label = "滴滴出行"
            session._send_recognized_text = AsyncMock()
            model = AsyncMock(side_effect=[{"intent": "help"}, {"intent": "background"}])
            with patch.object(voice, "_ask_llm_json", model):
                await session.handle_text(utterance)
                await session.handle_text("他没有休眠，灯还亮着。")
            session._say.assert_awaited_once_with(expected)
            session._post.assert_not_awaited()
            self.assertEqual(session.state, session.ASK_CODE if stage == "bind" else session.ASK_AUTH_CODE)

    async def test_help_is_available_at_each_interactive_step(self):
        session = self.make_session()
        session._send_recognized_text = AsyncMock()
        session._help_current_step = AsyncMock()
        states = (
            session.GREET, session.CONFIRM_GREET, session.ASK_PHONE, session.CONFIRM_PHONE,
            session.RETRY_SMS, session.ASK_CODE, session.ASK_NICKNAME, session.CONFIRM_NICKNAME,
            session.ASK_PERSONA, session.CONFIRM_PERSONA, session.ASK_SUPPLEMENT,
            session.CONFIRM_SUPPLEMENT, session.RETRY_SKILLS, session.ASK_AUTH_CONSENT,
            session.ASK_AUTH_CODE, session.ASK_AUTH_OAUTH, session.RETRY_AUTH_SUMMARY,
            session.RETRY_SKILLS_COMPLETE,
        )
        session.persona_templates = ["可爱女生"]
        model = AsyncMock(return_value={"intent": "help"})
        with patch.object(voice, "_ask_llm_json", model):
            for state in states:
                session.state = state
                await session.handle_text("还在吗？现在要做什么？")
                self.assertEqual(session.state, state)
        self.assertEqual(session._help_current_step.await_count, len(states))
        session._post.assert_not_awaited()

    async def test_first_sms_cooldown_requires_explicit_resend(self):
        session = self.make_session()
        session.phone = "13800138000"
        cooldown = RuntimeError("cooldown")
        cooldown.code = voice.VOICE_SMS_COOLDOWN
        cooldown.data = {"retryAfterSeconds": 12}
        session._post.side_effect = [cooldown, None]
        await session._send_sms()
        self.assertEqual(session.state, session.RETRY_SMS)
        self.assertIn("12秒", session._say.await_args.args[0])
        model = AsyncMock(side_effect=[{"intent": "accept"}, {"intent": "resend"}])
        with patch.object(voice, "_ask_llm_json", model):
            await session._on_retry_sms("好")
            self.assertEqual(session._post.await_count, 1)
            await session._on_retry_sms("再发一条")
        self.assertEqual(session.state, session.ASK_CODE)
        self.assertEqual(session._post.await_count, 2)

    async def test_phone_ignores_unrelated_digits_and_applies_correction(self):
        session = self.make_session()
        session.state = session.ASK_PHONE
        model = AsyncMock(side_effect=[
            {"intent": "phone", "phone": "138"},
            {"intent": "unclear"},
            {"intent": "phone", "phone": "13900139000"},
        ])
        with patch.object(voice, "_ask_llm_json", model):
            await session._on_phone("138")
            await session._on_phone("周五吧")
            self.assertEqual(session.phone_digits, "138")
            await session._on_phone("13800138000，不对，是13900139000")
        model_input = model.await_args.args[2]
        self.assertEqual(model_input, "13800138000，不对，是13900139000")
        self.assertEqual(session.phone, "13900139000")
        self.assertEqual(session.state, session.CONFIRM_PHONE)

    async def test_phone_from_one_utterance_with_several_groups_is_read_back(self):
        session = self.make_session()
        session.state = session.ASK_PHONE
        text = "我的手机号是123然后456789，13"
        model = AsyncMock(return_value={"intent": "phone", "phone": "12345678913"})
        with patch.object(voice, "_ask_llm_json", model):
            await session._on_phone(text)
        self.assertEqual(model.await_args.args[2], text)
        self.assertEqual(session.phone, "12345678913")
        self.assertEqual(session.state, session.CONFIRM_PHONE)
        self.assertIn(voice.spoken_digits(session.phone), session._say.await_args.args[0])
        session._post.assert_not_awaited()

    async def test_sms_resend_cooldown_stays_in_code_state(self):
        session = self.make_session()
        session.phone = "13800138000"
        session.state = session.ASK_CODE
        cooldown = RuntimeError("provider message can change")
        cooldown.code = voice.VOICE_SMS_COOLDOWN
        cooldown.data = {"retryAfterSeconds": 12}
        session._post.side_effect = [cooldown, None]
        model = AsyncMock(return_value={"intent": "resend"})
        with patch.object(voice, "_ask_llm_json", model):
            await session._on_code("重发")
            self.assertEqual(session.state, session.ASK_CODE)
            self.assertIn("12秒", session._say.await_args.args[0])
            model.return_value = {"intent": "accept"}
            await session._on_code("好")
            self.assertEqual(session._post.await_count, 1)
            self.assertNotIn("刚才还询问", model.await_args.args[1])
            model.return_value = {"intent": "resend"}
            await session._on_code("重新发送验证码")
        self.assertEqual(session.state, session.ASK_CODE)
        self.assertEqual(session._post.await_args.args[1]["phone"], session.phone)

    async def test_wait_and_phone_input_use_one_intent_call_each(self):
        session = self.make_session()
        session.state = session.ASK_PHONE
        session.phone_digits = "138"
        session._send_recognized_text = AsyncMock()
        session._schedule_asr_ready = Mock()
        model = AsyncMock(side_effect=[
            {"intent": "wait"},
            {"intent": "phone", "phone": "00138000"},
        ])
        with patch.object(voice, "_ask_llm_json", model):
            await session.handle_text("先留在这一步，我找一下")
            self.assertEqual(model.await_count, 1)
            self.assertEqual(session._say.await_args.args[0], "好，准备好了接着说。")
            self.assertEqual(session.state, session.ASK_PHONE)
            self.assertEqual(session.phone_digits, "138")
            await session.handle_text("00138000")
        self.assertEqual(model.await_count, 2)
        self.assertEqual(session.state, session.CONFIRM_PHONE)
        self.assertEqual(session.phone, "13800138000")

    async def test_invalid_sms_uses_error_code_without_interpreting_message(self):
        session = self.make_session()
        session.state = session.ASK_CODE
        session.phone = "13800138000"
        error = RuntimeError("expired text is irrelevant")
        error.code = voice.SMS_CODE_ERROR
        session._post.side_effect = error
        with patch.object(voice, "_ask_llm_json", AsyncMock(return_value={"intent": "code", "slot": 1})):
            await session._on_code("123456")
        self.assertIn("核对验证码", session._say.await_args.args[0])
        self.assertIsNone(session._sms_retry_action)

    async def test_persona_out_of_range_keeps_candidate(self):
        session = self.make_session("role")
        session.persona_templates = ["甲", "乙", "丙"]
        session.persona_name = "乙"
        session.state = session.CONFIRM_PERSONA
        model = AsyncMock(return_value={"intent": "answer", "ordinal": 5})
        with patch.object(voice, "_ask_llm_json", model):
            await session._on_confirm_persona("不是第二个，还是第五个吧")
        self.assertEqual(model.await_args.args[2], "不是第二个，还是第五个吧")
        self.assertIn("三个角色", session._say.await_args.args[0])
        self.assertEqual(session.state, session.CONFIRM_PERSONA)
        self.assertEqual(session.persona_name, "乙")

    async def test_persona_applies_only_the_models_final_choice(self):
        session = self.make_session("role")
        session.persona_templates = ["甲", "乙", "丙"]
        session.persona_name = "乙"
        session.state = session.CONFIRM_PERSONA
        model = AsyncMock(side_effect=[
            {"intent": "unclear", "ordinal": 3},
            {"intent": "answer", "ordinal": 3},
        ])
        with patch.object(voice, "_ask_llm_json", model):
            await session._on_confirm_persona("第三个是什么来着？")
            self.assertEqual(session.persona_name, "乙")
            await session._on_confirm_persona("不选第二个了，换第三个")
        self.assertEqual(session.persona_name, "丙")
        self.assertEqual(session.state, session.CONFIRM_PERSONA)

    async def test_supplement_silence_restores_unsubmitted_draft(self):
        server = SimpleNamespace()
        session = self.make_session("role", server)
        session.state = session.ASK_SUPPLEMENT
        session.supplement_parts = ["说话温柔一点"]
        await session.give_up_and_sleep()
        session._post.assert_not_awaited()
        resumed = self.make_session("role", server)
        resumed._start_idle_watch = Mock()
        await resumed.start()
        self.assertEqual(resumed.state, resumed.ASK_SUPPLEMENT)
        self.assertEqual(resumed.supplement_parts, ["说话温柔一点"])

    async def test_supplement_waits_for_end_and_confirmation(self):
        for supplement in ([], ["说话温柔一点"]):
            with self.subTest(supplement=supplement):
                session = self.make_session("role")
                session.state = session.ASK_SUPPLEMENT
                session.supplement_parts = supplement.copy()
                session._send_recognized_text = AsyncMock()
                session._save_role = AsyncMock()
                model = AsyncMock(side_effect=[
                    {"intent": "wait"}, {"action": "end"}, {"intent": "accept"},
                ])
                with patch.object(voice, "_ask_llm_json", model):
                    await session.handle_text("我想一下")
                    self.assertEqual(session.state, session.ASK_SUPPLEMENT)
                    self.assertEqual(session.supplement_parts, supplement)
                    await session.handle_text("没有，要补充的。")
                    self.assertEqual(session.state, session.CONFIRM_SUPPLEMENT)
                    session._save_role.assert_not_awaited()
                    await session.handle_text("可以")
                session._save_role.assert_awaited_once_with("。".join(supplement))

    async def test_stalled_playback_continues_after_legacy_wait(self):
        session = self.make_session("skills")
        session.conn.client_is_speaking = True
        advance = AsyncMock()
        del session._wait_spoken
        sleep = AsyncMock()
        with patch.object(voice.asyncio, "sleep", sleep):
            await session._advance_after_skill_prompt("接下来开通瑞幸咖啡。", advance)
        self.assertEqual(sleep.await_count, 80)
        session._say.assert_awaited_once_with("接下来开通瑞幸咖啡。")
        advance.assert_awaited_once()

    async def test_role_confirmation_uses_requested_supplement_prompt(self):
        session = self.make_session("role")
        await session._ask_supplement()
        self.assertEqual(session.state, session.ASK_SUPPLEMENT)
        session._say.assert_awaited_once_with(
            "接下来可以补充角色性格，比如，说话温柔一点，不要讲大道理"
        )

    async def test_new_sms_code_replaces_the_previous_resend_offer(self):
        session = self.make_session()
        session.state = session.ASK_CODE
        session.phone = "13800138000"
        session._sms_retry_action = "resend"
        error = RuntimeError("invalid")
        error.code = voice.SMS_CODE_ERROR
        session._post.side_effect = error
        model = AsyncMock(side_effect=[{"intent": "code", "slot": 1}, {"intent": "accept"}])
        with patch.object(voice, "_ask_llm_json", model):
            await session._on_code("123456")
            self.assertIsNone(session._sms_retry_action)
            await session._on_code("好")
        self.assertEqual(session._post.await_count, 1)

    async def test_role_save_error_uses_user_facing_prompt(self):
        session = self.make_session("role")
        session.state = session.CONFIRM_SUPPLEMENT
        session._post.side_effect = RuntimeError("API 500: upstream exception")
        await session._save_role("说话温柔一点")
        self.assertEqual(session.state, session.CONFIRM_SUPPLEMENT)
        session._say.assert_awaited_once_with("刚才没能保存角色设定，要我再试一次吗？")

    async def test_skill_code_pending_acknowledges_receipt_before_polling(self):
        session = self.make_session("skills")
        session.state = session.ASK_AUTH_CODE
        session.auth_task_id = "test-task"
        session._post.return_value = {"status": "pending"}
        session._poll_auth = AsyncMock()
        with patch.object(voice, "resolve_auth_reply", AsyncMock(return_value=("code", "123456"))):
            await session._on_auth_code("123456")
        session._say.assert_awaited_once_with("验证码已经收到了，我正在确认开通结果，稍等一下。")
        session._poll_auth.assert_awaited_once_with(after_code=True)

    async def test_skill_code_submission_error_uses_user_facing_prompt(self):
        session = self.make_session("skills")
        session.state = session.ASK_AUTH_CODE
        session.auth_task_id = "test-task"
        session._post.side_effect = RuntimeError("HTTP 503: upstream unavailable")
        with patch.object(voice, "resolve_auth_reply", AsyncMock(return_value=("code", "123456"))):
            await session._on_auth_code("123456")
        self.assertEqual(session.state, session.ASK_AUTH_CODE)
        session._say.assert_awaited_once_with("刚才没能提交验证码，请再念一遍。")

    async def test_luckin_failure_gives_safe_order_history_guidance(self):
        session = self.make_session("skills")
        session.auth_service = "luckin"
        session.auth_label = "瑞幸咖啡"
        session._run_next_auth = AsyncMock()
        for error_code, expected in (
            ("PROVIDER_502", "瑞幸咖啡暂时没能开通，授权服务没有提供具体原因。"
             "如果这个账号还没在瑞幸小程序下过单，可以先下单，再试一次。"),
            ("ORDER_HISTORY_REQUIRED", "瑞幸咖啡暂时没能开通，这个账号需要先在瑞幸小程序下过单。"),
        ):
            session._say.reset_mock()
            await session._handle_auth_result({
                "status": "failed", "errorCode": error_code, "message": "HTTP 502 from upstream",
            })
            session._say.assert_awaited_once_with(expected)

    async def test_tesla_oauth_prompts_for_sms_link(self):
        session = self.make_session("skills")
        session.auth_service = "tesla"
        session.auth_label = "特斯拉"
        session._start_oauth_poll = Mock()
        await session._handle_auth_result({
            "status": "waiting_oauth",
            "authUrl": "https://auth.tesla.cn/oauth2/v3/authorize",
        })
        self.assertEqual(session.state, session.ASK_AUTH_OAUTH)
        session._say.assert_awaited_once_with(
            "授权链接已发到您手机，请打开短信完成特斯拉登录，完成后跟我说一声。"
        )
        session._start_oauth_poll.assert_called_once()

    async def test_tesla_sms_send_failure_is_announced(self):
        session = self.make_session("skills")
        session.auth_service = "tesla"
        session.auth_label = "特斯拉"
        session._run_next_auth = AsyncMock()
        await session._handle_auth_result({"status": "failed", "errorCode": "SMS_SEND_FAILED"})
        session._say.assert_awaited_once_with("特斯拉暂时没能开通，授权链接短信没发出去。")
        session._run_next_auth.assert_awaited_once()

    async def test_authorization_success_is_announced_for_both_skills(self):
        for service, label in voice.VoiceActivationSession.AUTH_QUEUE:
            session = self.make_session("skills")
            session.auth_service = service
            session.auth_label = label
            session._run_next_auth = AsyncMock()
            await session._handle_auth_result({"status": "done"})
            session._say.assert_awaited_once_with(f"{label}已开通。")
            session._run_next_auth.assert_awaited_once()

    async def test_auth_status_error_keeps_the_existing_task(self):
        session = self.make_session("skills")
        session.auth_task_id = "existing"
        session.auth_service = "luckin"
        session.auth_label = "瑞幸咖啡"
        session._post.side_effect = [RuntimeError("network"), {"status": "waiting_code"}]
        session._run_next_auth = AsyncMock()
        await session._resume_auth()
        self.assertEqual(session.auth_task_id, "existing")
        self.assertIs(session._auth_status_after_code, False)
        session._run_next_auth.assert_not_awaited()
        with patch.object(voice, "resolve_skill_retry", AsyncMock(return_value=("retry", False))):
            await session._dispatch("再查一次")
        self.assertEqual(session.auth_task_id, "existing")
        self.assertIsNone(session._auth_status_after_code)
        self.assertEqual(session._post.await_count, 2)

    async def test_skills_completion_retries_only_the_failed_save(self):
        session = self.make_session("skills")
        session.conn.voice_onboarding = "skills"
        session._reboot_device = AsyncMock()
        session._bind_skills = AsyncMock()
        session._post.side_effect = [RuntimeError("temporary failure"), None]
        await session._finish_skills_and_reboot("瑞幸咖啡已开通。")
        session._reboot_device.assert_not_awaited()
        self.assertEqual(session.state, session.RETRY_SKILLS_COMPLETE)
        self.assertEqual(session.conn.voice_onboarding, "skills")
        with patch.object(voice, "resolve_skill_retry", AsyncMock(return_value=("retry", False))):
            await session._dispatch("再试一次")
        self.assertEqual(session._post.await_count, 2)
        session._bind_skills.assert_not_awaited()
        session._reboot_device.assert_awaited_once()
        self.assertIn("瑞幸咖啡已开通", session._say.await_args.args[0])

    async def test_failed_skill_waits_for_choice_and_retries_only_failed_skill(self):
        session = self.make_session("skills")
        session.auth_done = ["瑞幸咖啡"]
        session.auth_failed = ["滴滴出行"]
        session._finish_skills_and_reboot = AsyncMock()
        await session._run_next_auth()
        self.assertEqual(session.state, session.RETRY_AUTH_SUMMARY)
        session._finish_skills_and_reboot.assert_not_awaited()
        session._run_next_auth = AsyncMock()
        with patch.object(voice, "_ask_llm_json", AsyncMock(return_value={"intent": "retry", "services": ["didi", "unknown"]})):
            await session._on_auth_summary("再试一下滴滴")
        self.assertEqual(session.auth_queue, [("didi", "滴滴出行")])
        self.assertEqual(session.auth_done, ["瑞幸咖啡"])

    async def test_code_uses_only_the_selected_masked_slot(self):
        model = AsyncMock(side_effect=[
            {"intent": "unclear"},
            {"intent": "code", "slot": 2},
            {"intent": "code", "slot": 3},
        ])
        with patch.object(voice, "_ask_llm_json", model):
            conn = self.make_session().conn
            self.assertEqual(await voice.resolve_sms_reply(conn, "验证码是旧的123456，别用"), ("unclear", None))
            self.assertEqual(await voice.resolve_sms_reply(conn, "别用123456，换成654321"), ("code", "654321"))
            self.assertEqual(await voice.resolve_sms_reply(conn, "654321"), ("code", None))
        for call in model.await_args_list:
            self.assertNotIn("123456", call.args[2])
            self.assertNotIn("654321", call.args[2])

    def test_invalid_or_ambiguous_code_is_not_truncated(self):
        self.assertIsNone(voice.isolated_code("123456789"))
        self.assertIsNone(voice.extract_code("123456或者654321"))
        self.assertEqual(voice.extract_code("验证码是123456"), "123456")


if __name__ == "__main__":
    unittest.main()
