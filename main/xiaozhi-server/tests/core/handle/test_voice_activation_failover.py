"""Provider 故障自动切换：通用备用切换、逐候选链式重试与管理员实时救场。

语音激活与正常对话共用 core/utils/provider_failover.py，这里同时覆盖两条链路。
"""

import asyncio
import queue as thread_queue
import threading
import unittest.mock as mock

import core.utils.provider_failover as pf
import core.handle.voice_activation as va
from core.handle.voice_activation import (
    VoiceActivationSession,
    _ask_llm_json,
)


class _FakeLogger:
    def bind(self, **_kwargs):
        return self

    def debug(self, *_args, **_kwargs):
        return None

    def info(self, *_args, **_kwargs):
        return None

    def warning(self, *_args, **_kwargs):
        return None

    def error(self, *_args, **_kwargs):
        return None


class _FakeLLM:
    def __init__(self, reply=None, delay=0.0):
        self.reply = reply if reply is not None else RuntimeError("服务不可用")
        self.delay = delay

    def response_no_stream(self, system_prompt, user_prompt):
        if self.delay:
            import time

            time.sleep(self.delay)
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


class _FakeASR:
    """流式 ASR：ensure_connected 失败，模拟握手不可用。"""

    def __init__(self, error=None):
        self.error = error or RuntimeError("ASR 连接失败")
        self.closed = False

    async def ensure_connected(self, conn):
        raise self.error

    async def open_audio_channels(self, conn):
        pass

    async def close(self):
        self.closed = True


class _FakeNonStreamASR:
    """非流式 ASR：没有 ensure_connected，按需识别。"""

    def __init__(self):
        self.closed = False

    async def open_audio_channels(self, conn):
        pass

    async def close(self):
        self.closed = True


class _FakeTTS:
    def __init__(self, error=None, produce_audio=True):
        self.error = error
        self.produce_audio = produce_audio
        self.texts = []
        self.tts_text_queue = thread_queue.Queue()
        self.tts_audio_queue = thread_queue.Queue()
        self.closed = False

    def store_tts_text(self, sentence_id, text):
        self.texts.append((sentence_id, text))

    def tts_one_sentence(self, conn, content_type, content_detail=None):
        if self.error:
            raise self.error
        # 真实 provider 在合成成功时会把音频元组推进 tts_audio_queue；
        # produce_audio=False 模拟“接受文本但静默失败”的服务。
        if self.produce_audio:
            self.tts_audio_queue.put((1, b"\x00\x01" * 8, None, None))

    async def open_audio_channels(self, conn):
        pass

    async def close(self):
        self.closed = True


def _llm_config():
    return {
        "LLM": {
            "DeepSeekLLM": {"type": "deepseek", "api_key": "k1"},
            "SimpleHttpLLM": {"type": "simple_http", "api_key": "k2"},
            "MimoLLM": {"type": "mimo", "api_key": "k3"},
        },
        "selected_module": {"LLM": "DeepSeekLLM"},
    }


class _FakeConn:
    def __init__(self, config, llm, asr=None, tts=None, read_from_api=False):
        self.config = config
        self.logger = _FakeLogger()
        self.read_config_from_api = read_from_api
        self.headers = {"device-id": "aa:bb:cc:dd:ee:ff"} if read_from_api else {}
        self.llm = llm
        self.asr = asr
        self.tts = tts
        self.sentence_id = None
        self.client_abort = False
        self.client_is_speaking = False


def _session(conn):
    return VoiceActivationSession(conn, stage="bind")


def test_llm_failure_swaps_to_any_configured_provider():
    conn = _FakeConn(_llm_config(), _FakeLLM(RuntimeError("500")))

    async def rebuild(_conn, _module, name):
        return _FakeLLM('{"intent":"answer"}')

    with mock.patch.object(pf, "rebuild_provider", rebuild):
        data = asyncio.run(_ask_llm_json(conn, "sys", "用户", "提取昵称"))

    assert data == {"intent": "answer"}
    state = conn._failover_state["LLM"]
    # 不是按名字挑 mimo，而是配置里除当前外的第一个候选。
    assert state["active"] == "SimpleHttpLLM"
    assert isinstance(conn.llm, _FakeLLM)


def test_llm_chains_through_candidates_until_one_works():
    conn = _FakeConn(_llm_config(), _FakeLLM(RuntimeError("500")))

    async def rebuild(_conn, _module, name):
        if name == "SimpleHttpLLM":
            return _FakeLLM(RuntimeError("也挂了"))
        return _FakeLLM('{"intent":"answer"}')

    with mock.patch.object(pf, "rebuild_provider", rebuild):
        data = asyncio.run(_ask_llm_json(conn, "sys", "用户", "提取昵称"))

    assert data == {"intent": "answer"}
    state = conn._failover_state["LLM"]
    assert state["active"] == "MimoLLM"
    assert "failed" not in state  # 不再维护失败黑名单
    assert "DeepSeekLLM" in state.get("tried", set())


def test_llm_all_providers_exhausted_returns_none():
    conn = _FakeConn(_llm_config(), _FakeLLM(RuntimeError("500")))

    async def rebuild(_conn, _module, name):
        return _FakeLLM(RuntimeError(f"{name} 也挂了"))

    with mock.patch.object(pf, "rebuild_provider", rebuild):
        data = asyncio.run(_ask_llm_json(conn, "sys", "用户", "提取昵称"))

    assert data is None
    state = conn._failover_state["LLM"]
    assert "failed" not in state  # 无黑名单
    assert state.get("tried") == set()  # 一轮走完，游标清空


def test_llm_timeout_triggers_failover():
    config = _llm_config()
    config["failover_timeout"] = {"LLM": 0.2}
    conn = _FakeConn(config, _FakeLLM(delay=2.0))

    async def rebuild(_conn, _module, name):
        return _FakeLLM('{"intent":"answer"}')

    with mock.patch.object(pf, "rebuild_provider", rebuild):
        data = asyncio.run(_ask_llm_json(conn, "sys", "用户", "提取昵称"))

    assert data == {"intent": "answer"}
    assert conn._failover_state["LLM"]["active"] == "SimpleHttpLLM"


def _rescue_async(fresh_config):
    async def _fetch(*_args, **_kwargs):
        return fresh_config()

    return _fetch


def test_admin_new_provider_rescues_session():
    import config.config_loader as cl

    fresh = _llm_config()
    conn = _FakeConn(_llm_config(), _FakeLLM(RuntimeError("500")), read_from_api=True)

    async def rebuild(_conn, _module, name):
        # 管理员加入前，新名字不会被请求到；这里对未知名字给坏实例即可。
        return _FakeLLM(RuntimeError(f"{name} 挂了"))

    with mock.patch.object(pf, "rebuild_provider", rebuild), mock.patch.object(
        cl, "get_private_config_from_api", _rescue_async(lambda: fresh)
    ):
        assert asyncio.run(_ask_llm_json(conn, "sys", "用户", "提取昵称")) is None

    # 管理员在管理端新配了一个 provider 并选为默认。
    fresh = {
        "LLM": {
            "DeepSeekLLM": {"type": "deepseek", "api_key": "k1"},
            "SimpleHttpLLM": {"type": "simple_http", "api_key": "k2"},
            "RescueLLM": {"type": "rescue", "api_key": "k4"},
        },
        "selected_module": {"LLM": "RescueLLM"},
    }

    async def rebuild2(_conn, _module, name):
        # 新逻辑会重新全局扫描，可能先试回旧 provider，连不上再往后试
        if name == "RescueLLM":
            return _FakeLLM('{"intent":"answer"}')
        return _FakeLLM(RuntimeError(f"{name} 挂了"))

    with mock.patch.object(pf, "rebuild_provider", rebuild2), mock.patch.object(
        cl, "get_private_config_from_api", _rescue_async(lambda: fresh)
    ):
        data = asyncio.run(_ask_llm_json(conn, "sys", "用户", "提取昵称"))

    assert data == {"intent": "answer"}
    assert conn._failover_state["LLM"]["active"] == "RescueLLM"


def test_admin_fixed_provider_config_is_retried():
    import config.config_loader as cl

    fresh = _llm_config()
    conn = _FakeConn(_llm_config(), _FakeLLM(RuntimeError("500")), read_from_api=True)

    async def rebuild(_conn, _module, name):
        return _FakeLLM(RuntimeError(f"{name} 挂了"))

    with mock.patch.object(pf, "rebuild_provider", rebuild), mock.patch.object(
        cl, "get_private_config_from_api", _rescue_async(lambda: fresh)
    ):
        assert asyncio.run(_ask_llm_json(conn, "sys", "用户", "提取昵称")) is None
        state = conn._failover_state["LLM"]
        assert "failed" not in state
        assert state.get("tried") == set()

    # 管理员修好了 SimpleHttpLLM 的配置（换 api_key）。
    fresh = _llm_config()
    fresh["LLM"]["SimpleHttpLLM"]["api_key"] = "fixed-key"

    async def rebuild2(_conn, _module, name):
        # 重新全局扫描时会按顺序试其它候选，修好的那个最终会被选中
        if name == "SimpleHttpLLM":
            return _FakeLLM('{"intent":"answer"}')
        return _FakeLLM(RuntimeError(f"{name} 仍不可用"))

    with mock.patch.object(pf, "rebuild_provider", rebuild2), mock.patch.object(
        cl, "get_private_config_from_api", _rescue_async(lambda: fresh)
    ):
        data = asyncio.run(_ask_llm_json(conn, "sys", "用户", "提取昵称"))

    assert data == {"intent": "answer"}
    assert conn._failover_state["LLM"]["active"] == "SimpleHttpLLM"


def test_asr_failure_swaps_to_non_stream_provider():
    config = {
        "ASR": {
            "DoubaoStreamASR": {"type": "doubao_stream", "appid": "a1", "access_token": "t1"},
            "MimoASR": {"type": "mimo", "api_key": "k1"},
        },
        "selected_module": {"ASR": "DoubaoStreamASR"},
    }
    old_asr = _FakeASR()
    conn = _FakeConn(config, _FakeLLM(), asr=old_asr)

    async def rebuild(_conn, _module, name):
        assert name == "MimoASR"
        return _FakeNonStreamASR()

    with mock.patch.object(pf, "rebuild_provider", rebuild):
        asyncio.run(_session(conn)._connect_asr_before_prompt())

    assert old_asr.closed
    assert isinstance(conn.asr, _FakeNonStreamASR)
    assert conn._failover_state["ASR"]["active"] == "MimoASR"


def _fake_send_audio_module():
    """预注入假的 sendAudioHandle，避免真实导入触发 manager-api 请求。"""
    import sys
    import types

    module = types.ModuleType("core.handle.sendAudioHandle")

    async def send_tts_message(_conn, _msg):
        pass

    module.send_tts_message = send_tts_message
    return module


def test_tts_failure_swaps_provider():
    import sys

    config = {
        "TTS": {
            "DoubaoTTS": {"type": "doubao_stream", "appid": "a1", "access_token": "t1"},
            "MimoTTS": {"type": "mimo", "api_key": "k1"},
        },
        "selected_module": {"TTS": "DoubaoTTS"},
        "failover_timeout": {"TTS": 0.3},
    }
    old_tts = _FakeTTS(error=RuntimeError("合成失败"))
    conn = _FakeConn(config, _FakeLLM(), tts=old_tts)
    new_tts = _FakeTTS()

    async def rebuild(_conn, _module, name):
        assert name == "MimoTTS"
        return new_tts

    fake_module = _fake_send_audio_module()
    with mock.patch.object(pf, "rebuild_provider", rebuild), mock.patch.dict(
        sys.modules, {"core.handle.sendAudioHandle": fake_module}
    ):
        asyncio.run(_session(conn)._say("请告诉我你的手机号"))

    assert old_tts.closed
    assert conn.tts is new_tts
    assert new_tts.texts and new_tts.texts[0][1] == "请告诉我你的手机号"
    assert conn._failover_state["TTS"]["active"] == "MimoTTS"


def test_tts_all_fail_falls_back_to_subtitle():
    import sys

    config = {
        "TTS": {
            "DoubaoTTS": {"type": "doubao_stream", "appid": "a1", "access_token": "t1"},
            "MimoTTS": {"type": "mimo", "api_key": "k1"},
        },
        "selected_module": {"TTS": "DoubaoTTS"},
        "failover_timeout": {"TTS": 0.3},
    }
    conn = _FakeConn(config, _FakeLLM(), tts=_FakeTTS(error=RuntimeError("合成失败")))

    async def rebuild(_conn, _module, name):
        return _FakeTTS(error=RuntimeError(f"{name} 也失败"))

    fake_module = _fake_send_audio_module()
    with mock.patch.object(pf, "rebuild_provider", rebuild), mock.patch.dict(
        sys.modules, {"core.handle.sendAudioHandle": fake_module}
    ):
        asyncio.run(_session(conn)._say("请告诉我你的手机号"))

    # 兜底：即使全部失败，也要把文本推到当前 TTS 的字幕队列。
    assert conn.tts.texts and conn.tts.texts[0][1] == "请告诉我你的手机号"
    state = conn._failover_state["TTS"]
    assert "failed" not in state
    assert state.get("tried") == set()


def test_tts_silent_dead_service_is_detected_by_no_audio():
    """TTS 线程吞异常：接受文本但不出音频，也要判为失败并切换。"""
    import sys

    config = {
        "TTS": {
            "DoubaoTTS": {"type": "doubao_stream", "appid": "a1", "access_token": "t1"},
            "MimoTTS": {"type": "mimo", "api_key": "k1"},
        },
        "selected_module": {"TTS": "DoubaoTTS"},
        "failover_timeout": {"TTS": 0.3},
    }
    silent_tts = _FakeTTS(produce_audio=False)
    conn = _FakeConn(config, _FakeLLM(), tts=silent_tts)
    healthy_tts = _FakeTTS()

    async def rebuild(_conn, _module, name):
        assert name == "MimoTTS"
        return healthy_tts

    fake_module = _fake_send_audio_module()
    with mock.patch.object(pf, "rebuild_provider", rebuild), mock.patch.dict(
        sys.modules, {"core.handle.sendAudioHandle": fake_module}
    ):
        asyncio.run(_session(conn)._say("请告诉我你的手机号"))

    assert silent_tts.closed
    assert conn.tts is healthy_tts
    assert conn._failover_state["TTS"]["active"] == "MimoTTS"
    # 探测用的代理队列已恢复成原对象，不影响后续播放线程。
    assert isinstance(healthy_tts.tts_audio_queue, thread_queue.Queue)


def _patch_candidates(providers):
    """让刷新走管理端模型候选接口（不真的发 HTTP）。"""
    import config.manage_api_client as mac

    pf._candidate_cache.clear()

    async def _fetch(model_type):
        assert model_type == "LLM"
        return {"modelType": model_type, "providers": providers}

    return mock.patch.object(mac, "get_model_candidates", _fetch)


def test_model_candidates_endpoint_supplies_extra_providers():
    """agent 配置只给一个 provider 时，候选池由模型配置列表补齐。"""
    import config.config_loader as cl

    config = {
        "LLM": {"LLM_DeepSeekLLM": {"type": "openai", "api_key": "k1"}},
        "selected_module": {"LLM": "LLM_DeepSeekLLM"},
    }
    conn = _FakeConn(config, _FakeLLM(RuntimeError("404")), read_from_api=True)
    primary = {"LLM": {"LLM_DeepSeekLLM": {"type": "openai", "api_key": "k1"}}}

    async def rebuild(_conn, _module, name):
        assert name == "LLM_MimoLLM"
        return _FakeLLM('{"intent":"answer"}')

    with mock.patch.object(pf, "rebuild_provider", rebuild), mock.patch.object(
        cl, "get_private_config_from_api", _rescue_async(lambda: primary)
    ), _patch_candidates(
        {
            "LLM_DeepSeekLLM": {"type": "openai", "api_key": "k1"},
            "LLM_MimoLLM": {"type": "openai", "api_key": "k2"},
        }
    ):
        data = asyncio.run(_ask_llm_json(conn, "sys", "用户", "提取昵称"))

    assert data == {"intent": "answer"}
    assert conn._failover_state["LLM"]["active"] == "LLM_MimoLLM"
    assert "LLM_MimoLLM" in conn.config["LLM"]


def test_unconfigured_provider_is_skipped():
    """密钥还是模板占位值（你的api_key）的 provider 不作为候选。"""
    config = {
        "LLM": {
            "DeepSeekLLM": {"type": "openai", "api_key": "k1"},
            "AliLLM": {"type": "openai", "api_key": "你的api_key"},
            "MimoLLM": {"type": "openai", "api_key": "k3"},
        },
        "selected_module": {"LLM": "DeepSeekLLM"},
    }
    conn = _FakeConn(config, _FakeLLM(RuntimeError("404")))
    tried = []

    async def rebuild(_conn, _module, name):
        tried.append(name)
        return _FakeLLM('{"intent":"answer"}')

    with mock.patch.object(pf, "rebuild_provider", rebuild):
        asyncio.run(_ask_llm_json(conn, "sys", "用户", "提取昵称"))

    assert tried == ["MimoLLM"], tried
    assert conn._failover_state["LLM"]["active"] == "MimoLLM"


def test_local_provider_ranked_after_cloud_provider():
    """本机地址（Ollama/LM Studio）排到云端备用之后。"""
    config = {
        "LLM": {
            "DeepSeekLLM": {"type": "openai", "api_key": "k1"},
            "OllamaLLM": {"type": "ollama", "base_url": "http://localhost:11434"},
            "MimoLLM": {"type": "openai", "api_key": "k3"},
        },
        "selected_module": {"LLM": "DeepSeekLLM"},
    }
    conn = _FakeConn(config, _FakeLLM(RuntimeError("404")))
    tried = []

    async def rebuild(_conn, _module, name):
        tried.append(name)
        return _FakeLLM('{"intent":"answer"}')

    with mock.patch.object(pf, "rebuild_provider", rebuild):
        asyncio.run(_ask_llm_json(conn, "sys", "用户", "提取昵称"))

    assert tried == ["MimoLLM"], tried


def test_failover_priority_overrides_default_order():
    """failover_priority 显式指定时按配置顺序优先。"""
    config = {
        "LLM": {
            "DeepSeekLLM": {"type": "openai", "api_key": "k1"},
            "MimoLLM": {"type": "openai", "api_key": "k3"},
            "OllamaLLM": {"type": "ollama", "base_url": "http://localhost:11434"},
        },
        "selected_module": {"LLM": "DeepSeekLLM"},
        "failover_priority": {"LLM": ["OllamaLLM", "MimoLLM"]},
    }
    conn = _FakeConn(config, _FakeLLM(RuntimeError("404")))

    async def rebuild(_conn, _module, name):
        assert name == "OllamaLLM"
        return _FakeLLM('{"intent":"answer"}')

    with mock.patch.object(pf, "rebuild_provider", rebuild):
        asyncio.run(_ask_llm_json(conn, "sys", "用户", "提取昵称"))

    assert conn._failover_state["LLM"]["active"] == "OllamaLLM"


class _ProbeTTS(_FakeTTS):
    """带可用性自检的候选：probe_ready 失败说明配置错误（如 ws_url 404）。"""

    def __init__(self, ready=True):
        super().__init__()
        self.ready = ready

    async def probe_ready(self):
        return self.ready


def test_tts_unreachable_candidate_is_skipped_during_swap():
    """备用候选自检不过（握手被拒）时不能算切换成功，要继续试下一个。"""
    config = {
        "TTS": {
            "FishTTS": {"type": "fish", "api_key": "k0"},
            "HSDSTTS": {"type": "huoshan_double_stream", "appid": "a2", "access_token": "k1", "ws_url": "wss://t.example/tts"},
            "MimoTTS": {"type": "mimo", "api_key": "k2"},
        },
        "selected_module": {"TTS": "FishTTS"},
    }
    conn = _FakeConn(config, _FakeLLM(), tts=_FakeTTS(error=RuntimeError("合成失败")))
    tried = []
    healthy = _ProbeTTS()

    async def rebuild(_conn, _module, name):
        tried.append(name)
        if name == "HSDSTTS":
            return _ProbeTTS(ready=False)
        return healthy

    with mock.patch.object(pf, "rebuild_provider", rebuild):
        asyncio.run(pf.swap_to_fallback(conn, "TTS"))

    assert tried == ["HSDSTTS", "MimoTTS"], tried
    assert conn.tts is healthy
    assert conn._failover_state["TTS"]["active"] == "MimoTTS"
    # 自检失败的候选被记账，下一轮不会再被优先选中
    assert "HSDSTTS" in conn._failover_state["TTS"].get("tried", set())


def test_tts_missing_websocket_raises_instead_of_silent_success():
    """发送文本时没有连接必须抛错：静默 return 会被线程当成合成成功。"""
    from core.providers.tts.huoshan_double_stream import TTSProvider as HSDPTTS

    tts = HSDPTTS(
        {
            "appid": "app",
            "access_token": "token",
            "cluster": "volcano_tts",
            "resource_id": "volc.service_type.10029",
            "ws_url": "ws://127.0.0.1:1/tts",
            "speaker": "voice",
        },
        True,
    )
    try:
        asyncio.run(tts.text_to_speak("你好", None))
    except ConnectionError as exc:
        assert "WebSocket" in str(exc)
    else:
        raise AssertionError("ws 缺失时必须抛 ConnectionError，而不是静默返回")


def test_swap_from_thread_runs_on_conn_loop():
    """正常对话：chat/TTS 线程经 failover_module_sync 回连接事件循环执行切换。"""
    conn = _FakeConn(_llm_config(), _FakeLLM(RuntimeError("500")))
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    conn.loop = loop

    async def rebuild(_conn, _module, name):
        return _FakeLLM('{"intent":"answer"}')

    try:
        with mock.patch.object(pf, "rebuild_provider", rebuild):
            name = pf.failover_module_sync(conn, "LLM")
        assert name == "SimpleHttpLLM"
        assert conn._failover_state["LLM"]["active"] == "SimpleHttpLLM"
        assert isinstance(conn.llm, _FakeLLM)
    finally:
        loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout=5)
        loop.close()


def test_tts_worker_consecutive_failures_trigger_swap_and_replay():
    """正常对话：TTS 文本线程连续合成失败后切换备用，并把未播文本移交新实例。"""
    import time

    from core.providers.tts.base import TTSProviderBase
    from core.providers.tts.dto.dto import ContentType, SentenceType, TTSMessageDTO

    class _FailingBaseTTS(TTSProviderBase):
        def __init__(self, config=None, error=None):
            super().__init__(config or {"output_dir": "tmp/"}, True)
            self.error = error

        async def text_to_speak(self, text, output_file):
            if self.error:
                raise self.error
            return b"\x00\x01" * 64

    config = {
        "TTS": {"DoubaoTTS": {"type": "doubao_stream", "appid": "a1", "access_token": "t1"}, "MimoTTS": {"type": "mimo", "api_key": "k1"}},
        "selected_module": {"TTS": "DoubaoTTS"},
    }
    stop = threading.Event()
    conn = _FakeConn(config, _FakeLLM())
    conn.stop_event = stop
    sid = "sentence-1"
    conn.sentence_id = sid
    old_tts = _FailingBaseTTS(error=RuntimeError("合成服务不可用"))
    old_tts.conn = conn
    new_tts = _FakeTTS()

    def fake_failover(conn_, module, provider_name=None, **_kwargs):
        assert module == "TTS"
        pf.mark_current_failed(conn_, module, provider_name=provider_name)
        pf.failover_state(conn_).setdefault(module, {})["active"] = "MimoTTS"
        conn_.tts = new_tts
        return "MimoTTS"

    worker = threading.Thread(target=old_tts.tts_text_priority_thread, daemon=True)
    with mock.patch.object(pf, "failover_module_sync", fake_failover):
        worker.start()
        old_tts.tts_text_queue.put(
            TTSMessageDTO(
                sentence_id=sid, sentence_type=SentenceType.FIRST,
                content_type=ContentType.ACTION,
            )
        )
        old_tts.tts_text_queue.put(
            TTSMessageDTO(
                sentence_id=sid, sentence_type=SentenceType.MIDDLE,
                content_type=ContentType.TEXT, content_detail="你好，",
            )
        )
        old_tts.tts_text_queue.put(
            TTSMessageDTO(
                sentence_id=sid, sentence_type=SentenceType.MIDDLE,
                content_type=ContentType.TEXT, content_detail="世界。",
            )
        )
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if conn.tts is new_tts and new_tts.tts_text_queue.qsize() >= 2:
                break
            time.sleep(0.05)
    stop.set()
    worker.join(timeout=5)

    assert conn.tts is new_tts
    msgs = list(new_tts.tts_text_queue.queue)
    assert msgs[0].sentence_type == SentenceType.FIRST
    replayed = "".join(m.content_detail for m in msgs if m.content_type == ContentType.TEXT)
    assert "你好" in replayed and "世界" in replayed
    assert conn._failover_state["TTS"]["active"] == "MimoTTS"


def test_missing_required_keys_skips_candidate_immediately():
    """必填字段缺失/为空的候选在排序阶段就被跳过，不占尝试名额。"""
    conn = _FakeConn(
        {
            "LLM": {
                "DeepSeekLLM": {"type": "deepseek", "api_key": "k1"},
                "NoKeyMimo": {"type": "mimo"},  # api_key 整个缺失
                "EmptyVosk": {"type": "vosk", "model_path": ""},  # 必填为空
                "SimpleHttpLLM": {"type": "simple_http", "api_key": "k2"},
            },
            "selected_module": {"LLM": "DeepSeekLLM"},
        },
        _FakeLLM(RuntimeError("500")),
    )

    async def rebuild(_conn, _module, name):
        assert name == "SimpleHttpLLM"
        return _FakeLLM('{"intent":"answer"}')

    with mock.patch.object(pf, "rebuild_provider", rebuild):
        data = asyncio.run(_ask_llm_json(conn, "sys", "用户", "提取昵称"))

    assert data == {"intent": "answer"}
    state = conn._failover_state["LLM"]
    assert state["active"] == "SimpleHttpLLM"
    # 缺配置的被过滤，不会进入 failed（没浪费一次重建/探测）
    assert "NoKeyMimo" not in state.get("tried", set())
    assert "EmptyVosk" not in state.get("tried", set())
    assert "failed" not in state


def test_local_provider_without_credentials_is_candidate():
    """本机 endpoint 豁免凭证检查：FishSpeech 等自部署服务 api_key 留空是常态。"""
    fish = {"type": "fishspeech", "api_key": "", "api_url": "http://127.0.0.1:8080/v1/tts"}
    assert pf.provider_is_configured(fish)
    # 云端同样留空则不允许
    cloud = {"type": "mimo", "api_key": "", "base_url": "https://api.example.com/v1"}
    assert not pf.provider_is_configured(cloud)
    # 但本机的必填非凭证字段（如 url）仍然要求非空
    assert not pf.provider_is_configured(
        {"type": "fishspeech", "api_key": "", "api_url": ""}
    )


def test_no_blacklist_next_round_rescans_all_candidates():
    """没有失败黑名单/TTL：一轮耗尽后，下一次故障立即重新全局扫描。"""
    config = {
        "LLM": {
            "DeepSeekLLM": {"type": "deepseek", "api_key": "k1"},
            "SimpleHttpLLM": {"type": "simple_http", "api_key": "k2"},
        },
        "selected_module": {"LLM": "DeepSeekLLM"},
    }
    conn = _FakeConn(config, _FakeLLM(RuntimeError("500")))

    async def all_bad(_conn, _module, name):
        return _FakeLLM(RuntimeError(f"{name} 也挂了"))

    with mock.patch.object(pf, "rebuild_provider", all_bad):
        assert asyncio.run(_ask_llm_json(conn, "sys", "用户", "提取昵称")) is None
    state = conn._failover_state["LLM"]
    assert "failed" not in state
    assert state.get("tried") == set()  # 一轮走完，游标清空

    # 没有任何 TTL / 冷却：紧接着再故障一次，候选立即重新入池
    async def all_good(_conn, _module, name):
        return _FakeLLM('{"intent":"answer"}')

    with mock.patch.object(pf, "rebuild_provider", all_good):
        data = asyncio.run(_ask_llm_json(conn, "sys", "用户", "提取昵称"))
    assert data == {"intent": "answer"}


def test_asr_empty_result_does_not_failover_but_hard_error_does():
    """空识别（软结果）不切换；接口异常（硬失败）立即触发一次切换。"""
    import core.providers.asr.base as asr_base

    class _ASR(asr_base.ASRProviderBase):
        def __init__(self, mode):
            self.mode = mode
            self.output_dir = "/tmp"
            self.delete_audio_file = True

        def requires_file(self):
            return False

        def prefers_temp_file(self):
            return False

        def stop_ws_connection(self):
            pass

        async def speech_to_text(self, opus_data, session_id, artifacts=None):
            if self.mode == "hard":
                raise RuntimeError("ASR 服务不可用")
            return "", None

    conn = _FakeConn(
        {"ASR": {"MimoASR": {"type": "mimo", "api_key": "k1"}}},
        _FakeLLM(),
        asr=_ASR("empty"),
    )
    conn.session_id = "s1"
    conn.voiceprint_provider = None
    conn.asr_audio = []

    watchdog_calls = []

    async def fake_watchdog(conn_):
        watchdog_calls.append(1)

    async def fake_start_to_chat(_conn, _text):
        raise AssertionError("没有识别出文本不应进入对话")

    pcm = [b"\x00\x00" * 960] * 20
    with mock.patch.object(asr_base, "_maybe_failover_asr", fake_watchdog), \
            mock.patch.object(asr_base, "startToChat", fake_start_to_chat):
        for _ in range(3):
            asyncio.run(conn.asr.handle_voice_stop(conn, pcm))
        # 三次「没听清」都不该触发切换
        assert watchdog_calls == [], watchdog_calls

        conn.asr = _ASR("hard")
        asyncio.run(conn.asr.handle_voice_stop(conn, pcm))

    assert watchdog_calls == [1], watchdog_calls


def test_initialize_modules_isolates_asr_failure():
    """选中的 ASR 缺必填配置时只丢 ASR，TTS/LLM 等已成功的组件要保留。

    之前 initialize_modules 整体抛错，connection 把整个 modules dict 丢弃，
    一个坏 ASR 配置把 TTS/LLM/memory 全部拖死，连接哑到超时。
    """
    import core.utils.modules_initialize as mi

    config = {
        "TTS": {"MimoTTS": {"type": "mimo", "api_key": "k1"}},
        "ASR": {"XunfeiStream": {"type": "xunfei_stream", "app_id": ""}},
        "selected_module": {"TTS": "MimoTTS", "ASR": "XunfeiStream"},
    }
    sentinel_tts = object()
    with mock.patch.object(mi, "initialize_tts", return_value=sentinel_tts), \
            mock.patch.object(
                mi, "initialize_asr",
                side_effect=Exception("必须提供app_id、api_key和api_secret"),
            ):
        modules = mi.initialize_modules(
            _FakeLogger(), config, init_tts=True, init_asr=True
        )
    assert modules["tts"] is sentinel_tts
    assert "asr" not in modules


def test_failover_missing_module_rescues_broken_selected_asr():
    """连接初始化时选中的 ASR 实例化失败：救场切换到可用的备用 provider。"""
    conn = _FakeConn(
        {
            "ASR": {
                "XunfeiStream": {
                    "type": "xunfei_stream", "app_id": "", "api_key": "k", "api_secret": "s",
                },
                "DoubaoStream": {
                    "type": "doubao_stream", "appid": "a1", "access_token": "t1",
                },
            },
            "selected_module": {"ASR": "XunfeiStream"},
        },
        _FakeLLM(),
        asr=None,
    )
    rescue = _FakeNonStreamASR()

    async def rebuild(_conn, module, name):
        assert module == "ASR"
        assert name == "DoubaoStream"  # 缺必填配置的被跳过、active 被排除
        return rescue

    with mock.patch.object(pf, "rebuild_provider", rebuild):
        name = asyncio.run(pf.failover_missing_module(conn, "ASR"))

    assert name == "DoubaoStream"
    assert conn.asr is rescue
    state = conn._failover_state["ASR"]
    assert state["active"] == "DoubaoStream"
    assert "XunfeiStream" in state.get("tried", set())
    assert "failed" not in state


def test_minimax_tts_failure_counts_and_triggers_failover():
    """主用 MinimaxTTS 重写了文本线程，之前完全没接失败计数/切换触发。

    text_to_speak 内部会吞掉 HTTP/业务错误后正常返回，旧实现把「没抛
    异常」当成成功，还照样打『语音生成成功』——服务挂了也不会切换。
    """
    import core.providers.tts.minimax_httpstream as mm

    calls = {"fail": 0, "maybe": 0}

    class _P(mm.TTSProvider):
        def __init__(self, ok):
            self._ok = ok
            self._correct_words_pattern = None
            self.correct_words = {}

        async def text_to_speak(self, text, is_last):
            return self._ok

        def _note_tts_failure(self, text=None):
            calls["fail"] += 1

        def _maybe_failover_tts(self, last_pending=False):
            calls["maybe"] += 1

    _P(False).to_tts_single_stream("给你讲个短的")
    assert calls == {"fail": 1, "maybe": 1}
    _P(True).to_tts_single_stream("给你讲个短的")
    assert calls == {"fail": 1, "maybe": 1}  # 成功不计数、不切换


def test_minimax_http_200_without_audio_is_failure():
    """HTTP 200 但一个音频块都没有必须算失败，不能清零失败计数。"""
    import queue
    import types
    import core.providers.tts.minimax_httpstream as mm

    class _Resp:
        status = 200

        async def text(self):
            return ""

        class content:
            @staticmethod
            def iter_any():
                async def gen():
                    if False:
                        yield b""
                return gen()

    class _CM:
        async def __aenter__(self):
            return _Resp()

        async def __aexit__(self, *args):
            return False

    class _Session:
        def post(self, *args, **kwargs):
            return _CM()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

    p = object.__new__(mm.TTSProvider)
    p.model = "speech-01-turbo"
    p.voice_setting = {}
    p.pronunciation_dict = {}
    p.audio_setting = {}
    p.timber_weights = []
    p.api_url = "https://api.minimaxi.com/v1/t2a_v2?GroupId="
    p.header = {}
    p.tts_timeout = 5
    p.tts_audio_queue = queue.Queue()
    p.pcm_buffer = bytearray()
    p.opus_encoder = types.SimpleNamespace(
        sample_rate=16000, channels=1, frame_size_ms=60
    )
    p._note_tts_success = lambda: (_ for _ in ()).throw(
        AssertionError("没有音频块不该记为成功")
    )

    with mock.patch.object(mm.aiohttp, "ClientSession", return_value=_Session()):
        ok = asyncio.run(p.text_to_speak("你好", False))

    assert ok is False


def test_asr_probe_resets_prompt_guard():
    """failover 的连接探测不能把 ASR 留在「忽略识别结果」状态。

    doubao_stream.ensure_connected() 是提示音场景专用入口，会置
    _ignore_results=True；只有 finish_prompt_guard() 才复位。切换场景
    没有提示音，探测完必须立刻复位，否则用户每句话都被静默丢弃。
    """

    class _GuardedASR:
        def __init__(self):
            self._ignore_results = False
            self._awaiting_listen = False

        async def ensure_connected(self, conn):
            self._ignore_results = True
            self._awaiting_listen = True

        def finish_prompt_guard(self):
            self._ignore_results = False

    provider = _GuardedASR()
    with mock.patch.object(pf, "_log", lambda *args, **kwargs: None):
        ok = asyncio.run(pf._probe_candidate(None, "ASR", provider, "ASR_X"))

    assert ok is True
    assert provider._ignore_results is False
