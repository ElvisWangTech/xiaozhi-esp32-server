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
    assert sorted(state["failed"]) == ["DeepSeekLLM", "SimpleHttpLLM"]


def test_llm_all_providers_exhausted_returns_none():
    conn = _FakeConn(_llm_config(), _FakeLLM(RuntimeError("500")))

    async def rebuild(_conn, _module, name):
        return _FakeLLM(RuntimeError(f"{name} 也挂了"))

    with mock.patch.object(pf, "rebuild_provider", rebuild):
        data = asyncio.run(_ask_llm_json(conn, "sys", "用户", "提取昵称"))

    assert data is None
    state = conn._failover_state["LLM"]
    assert sorted(state["failed"]) == sorted(_llm_config()["LLM"])


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
        assert name == "RescueLLM"
        return _FakeLLM('{"intent":"answer"}')

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
        assert sorted(state["failed"]) == ["DeepSeekLLM", "MimoLLM", "SimpleHttpLLM"]

    # 管理员修好了 SimpleHttpLLM 的配置（换 api_key）。
    fresh = _llm_config()
    fresh["LLM"]["SimpleHttpLLM"]["api_key"] = "fixed-key"

    async def rebuild2(_conn, _module, name):
        assert name == "SimpleHttpLLM"
        return _FakeLLM('{"intent":"answer"}')

    with mock.patch.object(pf, "rebuild_provider", rebuild2), mock.patch.object(
        cl, "get_private_config_from_api", _rescue_async(lambda: fresh)
    ):
        data = asyncio.run(_ask_llm_json(conn, "sys", "用户", "提取昵称"))

    assert data == {"intent": "answer"}
    assert conn._failover_state["LLM"]["active"] == "SimpleHttpLLM"


def test_asr_failure_swaps_to_non_stream_provider():
    config = {
        "ASR": {
            "DoubaoStreamASR": {"type": "doubao_stream"},
            "MimoASR": {"type": "mimo"},
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
            "DoubaoTTS": {"type": "doubao_stream"},
            "MimoTTS": {"type": "mimo"},
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
            "DoubaoTTS": {"type": "doubao_stream"},
            "MimoTTS": {"type": "mimo"},
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
    assert sorted(state["failed"]) == ["DoubaoTTS", "MimoTTS"]


def test_tts_silent_dead_service_is_detected_by_no_audio():
    """TTS 线程吞异常：接受文本但不出音频，也要判为失败并切换。"""
    import sys

    config = {
        "TTS": {
            "DoubaoTTS": {"type": "doubao_stream"},
            "MimoTTS": {"type": "mimo"},
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
        "TTS": {"DoubaoTTS": {"type": "doubao_stream"}, "MimoTTS": {"type": "mimo"}},
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

    def fake_failover(conn_, module):
        assert module == "TTS"
        pf.mark_current_failed(conn_, module)
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
