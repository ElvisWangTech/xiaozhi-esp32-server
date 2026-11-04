"""Provider 故障自动切换（ASR/LLM/TTS 通用，语音激活与正常对话共用）。

任一模块访问失败或超时，自动切换到同模块下其他已配置的 provider
（不限于 mimoAI）。切换前实时重读最新配置：管理员新配/改配 provider
（含重新选定 selected_module）立即成为候选；已失败的 provider 若配置
被管理员修改过，也会重新尝试。代码层只做状态记录与实例重建。

三个 provider 都是连接级实例（conn.asr / conn.tts / conn.llm），可在
运行时热替换，无需重连设备：
- LLM：conn.chat / _ask_llm_json 调用失败时切换；
- TTS：语音激活用 TTSAudioProbe 探测静默死亡，正常对话由 TTS 文本
  线程按连续合成失败次数触发；
- ASR：建连失败由 provider 侧计数、receiveAudioHandle 看门狗触发。
"""

import asyncio
import json
import time

TAG = __name__

# 各模块调用/建连超时（秒）。可用 conn.config["failover_timeout"][模块名] 覆盖。
FAILOVER_TIMEOUTS = {"LLM": 30.0, "TTS": 15.0, "ASR": 10.0}

# 切换本身包含实时配置刷新（HTTP）+ 实例重建 + 音频通道握手，单独给超时。
_SWAP_TIMEOUTS = {"LLM": 60.0, "TTS": 45.0, "ASR": 30.0}


def _log(conn, level, message):
    logger = getattr(conn, "logger", None)
    if logger is None:
        return
    getattr(logger.bind(tag=TAG), level)(message)


def conn_config(conn):
    cfg = getattr(conn, "config", None)
    return cfg if isinstance(cfg, dict) else {}


def failover_timeout(conn, module_type):
    custom = conn_config(conn).get("failover_timeout") or {}
    try:
        return float(custom.get(module_type, FAILOVER_TIMEOUTS[module_type]))
    except Exception:
        return FAILOVER_TIMEOUTS[module_type]


def provider_conf_hash(conf):
    """配置指纹。管理员改过配置的 provider 允许重新尝试。"""
    try:
        return hash(json.dumps(conf, sort_keys=True, ensure_ascii=False, default=str))
    except Exception:
        return None


class TTSAudioProbe:
    """临时包一层 TTS 输出队列，统计是否真的产出了音频数据。

    TTS 文本工作线程会吞掉合成异常，只能靠“有没有音频产出”判断服务是否可用。
    仅在一次播报期间生效，结束后恢复原队列对象。
    """

    def __init__(self, tts):
        self.audio_puts = 0
        self._queue = getattr(tts, "tts_audio_queue", None)
        self._installed = False
        if self._queue is None:
            return
        original = self._queue
        outer = self

        class _ProbeQueue:
            def put(self, item, *args, **kwargs):
                try:
                    if isinstance(item, (tuple, list)) and len(item) >= 2 and item[1]:
                        outer.audio_puts += 1
                except Exception:
                    pass
                return original.put(item, *args, **kwargs)

            def __getattr__(self, name):
                return getattr(original, name)

        try:
            tts.tts_audio_queue = _ProbeQueue()
            self._installed = True
        except Exception:
            self._installed = False

    async def wait_first_audio(self, window):
        """等首帧音频；探测不可用时直接放行，不阻塞流程。"""
        if not self._installed:
            return True
        deadline = time.monotonic() + max(0.0, float(window))
        while time.monotonic() < deadline:
            if self.audio_puts > 0:
                return True
            await asyncio.sleep(0.1)
        return self.audio_puts > 0

    def restore(self, tts):
        if self._installed and tts is not None:
            try:
                tts.tts_audio_queue = self._queue
            except Exception:
                pass


def failover_state(conn):
    """每个 module 的切换状态：{"active": 当前provider名, "failed": {名: 配置指纹}}"""
    state = getattr(conn, "_failover_state", None)
    if state is None:
        state = conn._failover_state = {}
    return state


def active_provider_name(conn, module_type):
    state = failover_state(conn).get(module_type)
    if state and state.get("active"):
        return state["active"]
    return (conn_config(conn).get("selected_module") or {}).get(module_type)


def mark_current_failed(conn, module_type):
    """把当前正在用的 provider 记为失败（带配置指纹）。"""
    state = failover_state(conn).setdefault(module_type, {})
    active = state.get("active") or (
        conn_config(conn).get("selected_module") or {}
    ).get(module_type)
    if not active:
        return
    state["active"] = active
    conf = (conn_config(conn).get(module_type) or {}).get(active) or {}
    state.setdefault("failed", {})[active] = provider_conf_hash(conf)


async def refresh_module_providers(conn, module_type):
    """实时重读该模块的 provider 配置（管理员救场入口）。

    优先取设备绑定的 agent 配置；设备未绑定或获取失败时回退到服务器级
    配置。任一路失败都保持 conn.config 原状（旧配置仍可作为候选）。
    本地 config.yaml 模式没有实时来源，维持连接时的快照。
    """
    if not getattr(conn, "read_config_from_api", False):
        return
    if not isinstance(getattr(conn, "config", None), dict):
        return
    headers = getattr(conn, "headers", None) or {}
    device_id = headers.get("device-id")
    if not device_id:
        return
    fresh = None
    try:
        from config.config_loader import get_private_config_from_api

        fresh = await get_private_config_from_api(
            conn.config, device_id, headers.get("client-id", device_id)
        )
    except Exception as exc:
        # 设备未绑定等拿不到 agent 配置，语音激活场景下属正常。
        _log(conn, "debug", f"刷新{module_type}配置: agent配置不可用({exc})，改用服务器配置")
        fresh = None
    if not (fresh or {}).get(module_type):
        try:
            from config.manage_api_client import get_server_config

            fresh = (await get_server_config()) or {}
        except Exception as exc:
            _log(conn, "debug", f"刷新{module_type}配置: 服务器配置获取失败({exc})")
            return
    block = (fresh or {}).get(module_type)
    if not isinstance(block, dict) or not block:
        return
    conn.config[module_type] = block
    selected = ((fresh or {}).get("selected_module") or {}).get(module_type)
    if selected in block:
        conn.config.setdefault("selected_module", {})[module_type] = selected
    _log(conn, "info", f"{module_type} 实时配置已刷新，候选provider: {list(block.keys())}")


async def rebuild_provider(conn, module_type, provider_name):
    """按 provider 名重建实例；失败返回 None。"""
    if not isinstance(getattr(conn, "config", None), dict):
        return None
    import copy

    new_cfg = copy.deepcopy(conn.config)
    new_cfg.setdefault("selected_module", {})[module_type] = provider_name
    try:
        if module_type == "LLM":
            from core.utils import llm as llm_utils

            block = new_cfg["LLM"][provider_name]
            llm_type = provider_name if "type" not in block else block["type"]
            return llm_utils.create_instance(llm_type, block)
        if module_type == "TTS":
            from core.utils.modules_initialize import initialize_tts

            return initialize_tts(new_cfg)
        if module_type == "ASR":
            from core.utils.modules_initialize import initialize_asr

            return initialize_asr(new_cfg)
    except Exception as exc:
        _log(conn, "error", f"重建{module_type}备用实例({provider_name})失败: {exc}")
    return None


async def swap_to_fallback(conn, module_type):
    """切换到该模块下其他已配置 provider，逐个尝试。

    切换前先实时刷新配置。候选顺序：管理员最新选定的 provider 优先，
    其余按配置顺序。已失败且配置未变的 provider 跳过；管理员改过配置的
    会重新纳入。返回切换成功的 provider 名，全部不可用返回 None。
    """
    await refresh_module_providers(conn, module_type)
    cfg = conn_config(conn)
    block = cfg.get(module_type) or {}
    active = active_provider_name(conn, module_type)
    state = failover_state(conn).setdefault(module_type, {})
    state["active"] = active
    failed = state.setdefault("failed", {})

    def _eligible(name):
        conf = block.get(name)
        if not isinstance(conf, dict) or not conf:
            return False
        if name == active:
            return False
        old = failed.get(name)
        # 没试过，或试过但管理员改过配置 → 可尝试
        return old is None or old != provider_conf_hash(conf)

    candidates = []
    selected = (cfg.get("selected_module") or {}).get(module_type)
    if selected and _eligible(selected):
        candidates.append(selected)
    for name in block:
        if _eligible(name) and name not in candidates:
            candidates.append(name)

    if not candidates:
        _log(
            conn, "warning",
            f"{module_type} 无可用备用provider（当前={active}，已试过且配置未变={sorted(failed)}）",
        )
        return None

    for name in candidates:
        provider = await rebuild_provider(conn, module_type, name)
        if provider is None:
            failed[name] = provider_conf_hash(block.get(name))
            continue
        if module_type == "LLM":
            old = getattr(conn, "llm", None)
            conn.llm = provider
            # 记忆/意图模块若持有同一个旧实例，一并换新
            for holder_name in ("memory", "intent"):
                holder = getattr(conn, holder_name, None)
                if holder is not None and getattr(holder, "llm", None) is old:
                    try:
                        holder.llm = provider
                    except Exception:
                        pass
        else:
            # ASR/TTS 需要打开音频通道再挂载；失败则关掉试下一个。
            try:
                await provider.open_audio_channels(conn)
            except Exception as exc:
                _log(conn, "error", f"{module_type} 备用provider({name})打开音频通道失败: {exc}")
                try:
                    if hasattr(provider, "close"):
                        await provider.close()
                except Exception:
                    pass
                failed[name] = provider_conf_hash(block.get(name))
                continue
            old = getattr(conn, module_type.lower(), None)
            try:
                if old is not None and hasattr(old, "close"):
                    await old.close()
            except Exception:
                pass
            setattr(conn, module_type.lower(), provider)
        state["active"] = name
        failed.pop(name, None)
        _log(
            conn, "warning",
            f"{module_type} 服务不可用，已自动切换到备用provider: {name}",
        )
        return name

    _log(conn, "warning", f"{module_type} 候选provider均不可用: {candidates}")
    return None


def swap_from_thread(conn, module_type, timeout=None):
    """非事件循环线程（chat 执行线程、TTS 文本线程等）用的同步切换入口。

    通过 run_coroutine_threadsafe 回到连接的事件循环执行，阻塞等待结果。
    返回切换成功的 provider 名，失败返回 None。
    """
    loop = getattr(conn, "loop", None)
    if loop is None or loop.is_closed():
        _log(conn, "warning", f"{module_type} 切换失败: 连接事件循环不可用")
        return None
    if timeout is None:
        timeout = _SWAP_TIMEOUTS.get(module_type, 60.0)
    future = asyncio.run_coroutine_threadsafe(swap_to_fallback(conn, module_type), loop)
    try:
        return future.result(timeout)
    except Exception as exc:
        _log(conn, "error", f"{module_type} 切换备用provider失败: {exc}")
        return None


def failover_module_sync(conn, module_type, timeout=None):
    """工作线程版：把当前 provider 记为失败并切换。返回新 provider 名或 None。"""
    mark_current_failed(conn, module_type)
    return swap_from_thread(conn, module_type, timeout=timeout)


async def failover_module(conn, module_type):
    """事件循环协程版：把当前 provider 记为失败并切换。返回新 provider 名或 None。"""
    mark_current_failed(conn, module_type)
    return await swap_to_fallback(conn, module_type)
