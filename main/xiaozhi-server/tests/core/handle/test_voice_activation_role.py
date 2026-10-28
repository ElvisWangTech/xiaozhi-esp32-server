"""昵称抽取、主人称呼和性格模板选择。"""

import asyncio

from core.handle.voice_activation import (
    VoiceActivationSession,
    apply_persona_voice,
    clean_supplement,
    extract_nickname,
    extract_owner_title,
    is_no_supplement,
    match_persona,
    parse_nickname_reply,
    parse_persona_catalog,
    persona_menu,
    resolve_persona,
    supplement_text,
)


class _FakeLogger:
    def bind(self, **_kwargs):
        return self

    def info(self, *_args, **_kwargs):
        return None

    def warning(self, *_args, **_kwargs):
        return None


class _FakeConn:
    def __init__(self, reply):
        self.reply = reply
        self.calls = []
        self.logger = _FakeLogger()
        self.llm = self

    def response_no_stream(self, system_prompt, user_prompt):
        self.calls.append((system_prompt, user_prompt))
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply

TEMPLATES = ["湾湾小何", "星际游子", "英语老师", "好奇男孩", "汪汪队长"]


def test_parse_nickname_reply():
    assert parse_nickname_reply('{"nickname":"小智"}') == "小智"
    assert parse_nickname_reply('```json\n{"nickname":"豆豆"}\n```') == "豆豆"
    assert parse_nickname_reply('好的 {"nickname":"小美"}') == "小美"
    assert parse_nickname_reply('{"nickname":""}') is None
    assert parse_nickname_reply('{"nickname":"你的昵称是小智"}') is None
    assert parse_nickname_reply("小智") is None


def test_extract_nickname_uses_llm_for_sentences():
    conn = _FakeConn('{"nickname":"小智"}')
    assert asyncio.run(extract_nickname(conn, "你的昵称是小智")) == "小智"
    assert conn.calls and "你的昵称是小智" in conn.calls[0][1]

    corrected = _FakeConn('{"nickname":"小美"}')
    assert asyncio.run(extract_nickname(corrected, "不对，你的昵称是小美")) == "小美"


def test_extract_nickname_bare_name_skips_llm_and_rejects_noise():
    conn = _FakeConn('{"nickname":"不该调用"}')
    assert asyncio.run(extract_nickname(conn, "小智")) == "小智"
    assert conn.calls == []
    assert asyncio.run(extract_nickname(conn, "小智吧")) == "小智"
    assert asyncio.run(extract_nickname(conn, "确认")) is None
    assert asyncio.run(extract_nickname(conn, "不对")) is None
    assert conn.calls == []
    assert asyncio.run(extract_nickname(_FakeConn('{"nickname":""}'), "你的昵称是")) is None
    long_name = "一二三四五六七八九十一二三"
    assert asyncio.run(extract_nickname(_FakeConn('{"nickname":"' + long_name + '"}'), long_name)) is None
    failed = _FakeConn(RuntimeError("down"))
    assert asyncio.run(extract_nickname(failed, "就叫小美")) is None


def test_extract_owner_title():
    bare = _FakeConn('{"title":"不该调用"}')
    assert asyncio.run(extract_owner_title(bare, "老板")) == "老板"
    assert asyncio.run(extract_owner_title(bare, "嗯，老板")) == "老板"
    assert asyncio.run(extract_owner_title(bare, "确认")) is None
    assert bare.calls == []

    called = _FakeConn('{"title":"张先生"}')
    assert asyncio.run(extract_owner_title(called, "你可以称呼我为张先生")) == "张先生"
    assert called.calls and "你可以称呼我为张先生" in called.calls[0][1]

    corrected = _FakeConn('{"title":"妈妈"}')
    assert asyncio.run(extract_owner_title(corrected, "不对，叫我妈妈")) == "妈妈"
    assert asyncio.run(extract_owner_title(_FakeConn('{"title":""}'), "我该怎么称呼您呢")) is None
    assert asyncio.run(extract_owner_title(_FakeConn(RuntimeError("down")), "叫我老板")) is None


def test_persona_catalog_keeps_voice_codes():
    names, voices = parse_persona_catalog(
        [
            {"name": "湾湾小何", "voice": "zh_female_xiaohe_uranus_bigtts"},
            {"name": "星际游子", "voice": "zh_male_m191_uranus_bigtts"},
            {"name": "英语老师", "voice": "zh_female_vv_uranus_bigtts"},
        ]
    )
    assert names == ["湾湾小何", "星际游子", "英语老师"]
    assert voices["英语老师"] == "zh_female_vv_uranus_bigtts"
    plain, empty = parse_persona_catalog(["湾湾小何"])
    assert plain == ["湾湾小何"]
    assert empty == {}


def test_asr_keeps_speech_until_reconnect():
    from core.providers.asr.doubao_stream import merge_replay, stash_unsent

    buffer = []
    for index in range(40):
        stash_unsent(buffer, index, speaking=False)
    assert buffer == list(range(10, 40))
    for index in range(40, 45):
        stash_unsent(buffer, index, speaking=True)
    assert buffer[0] == 10
    assert buffer[-1] == 44

    replay = merge_replay([1, 2, 3], [4])
    assert replay == [1, 2, 3, 4]
    assert merge_replay([], [9]) == [9]


def test_utterance_end_keeps_asr_socket():
    from core.providers.asr.doubao_stream import ASRProvider, playback_blocks_upload

    provider = ASRProvider(
        {"appid": "1", "access_token": "token", "sample_rate": 16000},
        True,
    )
    socket = object()
    provider.asr_ws = socket
    provider.is_processing = True
    provider._is_stopping = False
    provider._recent = [b"a"]
    provider._unsent = [b"b"]
    provider.stop_ws_connection()
    assert provider.asr_ws is socket
    assert provider.is_processing is True
    assert provider._recent == []
    assert provider._unsent == []

    blocked, resume_at = playback_blocks_upload(True, False, False, 10.0, 0.0, True)
    assert blocked is True
    assert resume_at == 11.0
    blocked, resume_at = playback_blocks_upload(False, False, False, 10.5, resume_at, True)
    assert blocked is True
    blocked, _ = playback_blocks_upload(False, False, False, 11.0, resume_at, True)
    assert blocked is False
    blocked, _ = playback_blocks_upload(True, True, False, 10.0, 0.0, True)
    assert blocked is False
    blocked, resume_at = playback_blocks_upload(True, False, False, 10.0, 0.0, False)
    assert blocked is True
    assert resume_at == 10.0
    blocked, _ = playback_blocks_upload(False, False, False, 10.1, resume_at, False)
    assert blocked is False


def test_apply_persona_voice_updates_tts():
    class _TTS:
        voice = "old"

    tts = _TTS()
    assert apply_persona_voice(tts, "zh_male_m191_uranus_bigtts") is True
    assert tts.voice == "zh_male_m191_uranus_bigtts"
    assert apply_persona_voice(tts, "") is False


def test_match_persona_by_name_or_index():
    assert match_persona("英语老师", TEMPLATES) == "英语老师"
    assert match_persona("我选汪汪队长", TEMPLATES) == "汪汪队长"
    assert match_persona("二", TEMPLATES) == "星际游子"
    assert match_persona("第二个", TEMPLATES) == "星际游子"
    assert match_persona("第3个", TEMPLATES) == "英语老师"
    assert match_persona("就用第五个", TEMPLATES) == "汪汪队长"
    assert match_persona("确认", TEMPLATES) is None
    assert match_persona("不要英语老师", TEMPLATES) is None
    assert match_persona("十", TEMPLATES) is None


def test_resolve_persona_uses_llm_for_description():
    local = _FakeConn('{"template":"不该调用"}')
    assert asyncio.run(resolve_persona(local, "英语老师", TEMPLATES)) == "英语老师"
    assert asyncio.run(resolve_persona(local, "第二个", TEMPLATES)) == "星际游子"
    assert local.calls == []

    described = _FakeConn('{"template":"湾湾小何"}')
    assert asyncio.run(resolve_persona(described, "我想要那个台湾女生", TEMPLATES)) == "湾湾小何"
    assert described.calls
    assert asyncio.run(resolve_persona(_FakeConn('{"template":"外星人不存在"}'), "随便来一个", TEMPLATES)) is None


def test_supplement_decline_uses_llm_for_sentence():
    assert asyncio.run(supplement_text(_FakeConn('{"decline":false}'), "没有")) == ""
    direct = _FakeConn('{"decline":true}')
    assert asyncio.run(supplement_text(direct, "说话温柔一点")) == "说话温柔一点"
    assert direct.calls == []
    declined = _FakeConn('{"decline":true}')
    assert asyncio.run(supplement_text(declined, "没什么要补充的")) == ""
    assert declined.calls


def test_supplement_and_menu():
    assert is_no_supplement("没有")
    assert is_no_supplement("没有了。")
    assert is_no_supplement("不用补充")
    assert not is_no_supplement("说话温柔一点")
    assert clean_supplement("  说话温柔一点。 ") == "说话温柔一点"
    menu = persona_menu(TEMPLATES)
    assert menu.startswith("接下来选一个性格。一，湾湾小何。二，星际游子。")
    assert "五，汪汪队长" in menu


def test_phone_prompt_connects_asr_as_prompt_ends():
    order = []

    class _ASR:
        async def ensure_connected(self, conn):
            order.append("connect")

        def finish_prompt_guard(self):
            order.append("listen")

    class _Conn:
        def __init__(self):
            self.asr = _ASR()
            self.logger = _FakeLogger()
            self.client_is_speaking = False

    session = VoiceActivationSession(_Conn(), stage="bind")

    async def _say(text):
        order.append(text)

    session._say = _say

    async def _run():
        await session.start()
        await session._asr_ready_task
        session._idle_task.cancel()
        try:
            await session._idle_task
        except asyncio.CancelledError:
            pass

    asyncio.run(_run())
    assert order == ["请说出您的手机号码，一共十一位。", "connect", "listen"]


def test_silence_packet_only_after_audio_gap():
    from core.providers.asr import doubao_stream

    sent = []
    provider = doubao_stream.ASRProvider(
        {"appid": "1", "access_token": "token", "sample_rate": 16000},
        True,
    )
    ws = object()
    provider.asr_ws = ws
    provider._is_stopping = False
    provider._last_audio_at = 0
    provider._silence_keepalive_logged = False

    async def _send(_ws, frame, remember=True):
        sent.append(frame)
        provider.asr_ws = None

    provider._send_pcm = _send
    original_sleep = doubao_stream.asyncio.sleep

    async def _fast_sleep(_seconds):
        return None

    async def _run():
        doubao_stream.asyncio.sleep = _fast_sleep
        try:
            await provider._silence_before_packet_timeout(ws)
        finally:
            doubao_stream.asyncio.sleep = original_sleep

    asyncio.run(_run())
    assert len(sent) == 1
    assert len(sent[0]) == 6400


def test_long_silence_stops_asr_then_prompts_sleep():
    order = []

    class _ASR:
        def stop_ws_connection(self):
            order.append("stop")

    class _Conn:
        def __init__(self):
            self.asr = _ASR()
            self.logger = _FakeLogger()
            self.client_is_speaking = False
            self.close_after_chat = False
            self.vad_last_voice_time = 0
            self.config = {"close_connection_no_voice_time": 1}
            self.voice_activation_handler = None

    session = VoiceActivationSession(_Conn(), stage="bind")
    session.conn.voice_activation_handler = session

    async def _say(text):
        order.append(text)

    session._say = _say

    async def _run():
        session._start_idle_watch()
        await asyncio.wait_for(session._idle_task, timeout=3)

    asyncio.run(_run())
    assert order[0] == "stop"
    assert "休息" in order[1]
    assert session.conn.close_after_chat is True


def test_first_skill_skip_is_spoken_later_failure_is_silent():
    said = []
    posts = []

    class _Conn:
        def __init__(self):
            self.logger = _FakeLogger()
            self.headers = {"device-id": "aa:bb"}
            self.voice_onboarding = "skills"
            self.voice_activation_handler = None
            self.skills_pending = False

    conn = _Conn()
    session = VoiceActivationSession(conn, stage="skills")
    conn.voice_activation_handler = session

    async def _post(path, payload):
        posts.append(path)
        if path.endswith("/skills"):
            raise RuntimeError("API返回错误: 技能服务暂不可用")

    async def _say(text):
        said.append(text)

    session._post = _post
    session._say = _say

    async def _first():
        await session._bind_skills()
        await session._on_retry_skills("稍后再说")

    asyncio.run(_first())
    assert any("稍后再说" in text for text in said)
    assert "/device/voice-activation/skills/defer" in posts
    assert any("绑定技能" in text for text in said)

    said.clear()
    conn.voice_onboarding = "skills_quiet"
    conn.voice_activation_handler = session
    conn.skills_pending = False

    async def _later():
        await session._bind_skills()

    asyncio.run(_later())
    assert said == []
    assert conn.voice_activation_handler is None
    assert conn.skills_pending is True
    assert conn.voice_onboarding is None


def test_user_asked_skill_bind_announces_failure_during_quiet_retry():
    said = []

    class _Conn:
        def __init__(self):
            self.logger = _FakeLogger()
            self.headers = {"device-id": "aa:bb"}
            self.voice_onboarding = "skills_quiet"
            self.voice_activation_handler = None
            self.skills_pending = False
            self.speak_skill_failure = False
            self.config = {}

    conn = _Conn()
    session = VoiceActivationSession(conn, stage="skills")
    conn.voice_activation_handler = session
    started = asyncio.Event()
    release = asyncio.Event()

    async def _post(path, payload):
        started.set()
        await release.wait()
        raise RuntimeError("API返回错误: 技能绑定失败")

    async def _say(text):
        said.append(text)

    async def _send_recognized_text(text):
        return None

    session._post = _post
    session._say = _say
    session._send_recognized_text = _send_recognized_text

    async def _run():
        task = asyncio.create_task(session._bind_skills())
        await started.wait()
        await session.handle_text("绑定技能。")
        release.set()
        await task

    asyncio.run(_run())
    assert any("开始绑定技能" in text for text in said)
    assert any("技能绑定失败" in text for text in said)
    assert conn.voice_activation_handler is session


def _skill_conn(onboarding="skills"):
    class _Conn:
        def __init__(self):
            self.logger = _FakeLogger()
            self.headers = {"device-id": "aa:bb"}
            self.voice_onboarding = onboarding
            self.voice_activation_handler = None
            self.skills_pending = False

    return _Conn()


def test_later_skill_bind_skips_authorization():
    said = []
    posts = []
    conn = _skill_conn("skills_quiet")
    session = VoiceActivationSession(conn, stage="skills", include_auth=False)
    conn.voice_activation_handler = session

    async def _post(path, payload):
        posts.append(path)

    async def _say(text):
        said.append(text)

    session._post = _post
    session._say = _say

    asyncio.run(session._bind_skills())
    assert posts == [
        "/device/voice-activation/skills",
        "/device/voice-activation/skills/complete",
    ]
    assert said == ["通用技能已绑定。开通瑞幸或滴滴可以说绑定技能。"]
    assert conn.voice_activation_handler is None
    assert conn.voice_onboarding is None


def test_first_skill_bind_starts_authorization():
    said = []
    posts = []
    auth_started = []
    conn = _skill_conn("skills")
    session = VoiceActivationSession(conn, stage="skills", include_auth=True)
    conn.voice_activation_handler = session

    async def _post(path, payload):
        posts.append(path)

    async def _say(text):
        said.append(text)

    async def _run_next_auth():
        auth_started.append(True)

    session._post = _post
    session._say = _say
    session._run_next_auth = _run_next_auth

    asyncio.run(session._bind_skills())
    assert posts == ["/device/voice-activation/skills"]
    assert auth_started == [True]
    assert any(text == "通用技能已绑定。" for text in said)
    assert conn.voice_activation_handler is session


def test_user_request_during_quiet_bind_starts_authorization():
    said = []
    auth_started = []
    conn = _skill_conn("skills_quiet")
    session = VoiceActivationSession(conn, stage="skills", include_auth=False)
    conn.voice_activation_handler = session
    started = asyncio.Event()
    release = asyncio.Event()

    async def _post(path, payload):
        started.set()
        await release.wait()

    async def _say(text):
        said.append(text)

    async def _send_recognized_text(text):
        return None

    async def _run_next_auth():
        auth_started.append(True)

    session._post = _post
    session._say = _say
    session._send_recognized_text = _send_recognized_text
    session._run_next_auth = _run_next_auth

    async def _run():
        task = asyncio.create_task(session._bind_skills())
        await started.wait()
        await session.handle_text("绑定技能。")
        release.set()
        await task

    asyncio.run(_run())
    assert auth_started == [True]
    assert any(text == "通用技能已绑定。" for text in said)


def test_bound_auth_skill_is_skipped_unless_rebind():
    said = []
    payloads = []
    conn = _skill_conn()
    session = VoiceActivationSession(conn, stage="skills", include_auth=True)
    session.auth_queue = [("luckin", "瑞幸咖啡"), ("didi", "滴滴出行")]

    async def _post(path, payload):
        payloads.append(payload)
        service = payload.get("service")
        if service == "luckin":
            return {"status": "already_bound", "service": "luckin"}
        return {"status": "waiting_code", "service": "didi", "taskId": "t2"}

    async def _say(text):
        said.append(text)

    async def _handle_auth_result(result):
        said.append("handle:" + str((result or {}).get("status")))

    session._post = _post
    session._say = _say
    session._handle_auth_result = _handle_auth_result

    asyncio.run(session._run_next_auth())
    assert payloads[0]["rebind"] is False
    assert "瑞幸咖啡已经开通。" in said
    assert "正在开通瑞幸咖啡。" not in said
    assert "正在开通滴滴出行。" in said
    assert "handle:waiting_code" in said

    said.clear()
    payloads.clear()
    session.rebind_auth = True
    session.auth_queue = [("luckin", "瑞幸咖啡")]

    async def _post_rebind(path, payload):
        payloads.append(payload)
        return {"status": "waiting_code", "service": "luckin", "taskId": "t3"}

    session._post = _post_rebind
    asyncio.run(session._run_next_auth())
    assert payloads[0]["rebind"] is True
    assert "正在开通瑞幸咖啡。" in said
    assert "瑞幸咖啡已经开通。" not in said


def test_submitted_code_does_not_repeat_prompt_and_announces_success():
    import core.handle.voice_activation as voice_activation

    said = []
    conn = _skill_conn()
    session = VoiceActivationSession(conn, stage="skills")
    session.auth_label = "瑞幸咖啡"
    session.auth_task_id = "t1"
    session.state = session.ASK_AUTH_CODE
    replies = [
        {"status": "pending", "taskId": "t1"},
        {"status": "waiting_code", "taskId": "t1", "message": "已向手机发送验证码"},
        {"status": "done", "taskId": "t1"},
    ]

    async def _post(path, payload):
        return replies.pop(0)

    async def _say(text):
        said.append(text)

    async def _run_next_auth():
        return None

    session._post = _post
    session._say = _say
    session._run_next_auth = _run_next_auth
    original_sleep = voice_activation.asyncio.sleep

    async def _sleep(_seconds):
        return None

    voice_activation.asyncio.sleep = _sleep
    try:
        asyncio.run(session._on_auth_code("327124"))
    finally:
        voice_activation.asyncio.sleep = original_sleep

    spoken = "".join(said)
    assert "请说出瑞幸咖啡短信里的验证码" not in spoken
    assert "验证码已提交，正在登录。" in spoken
    assert "瑞幸咖啡已开通。" in spoken
    assert replies == []
