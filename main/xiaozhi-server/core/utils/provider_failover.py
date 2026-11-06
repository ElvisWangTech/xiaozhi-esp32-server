"""Provider 故障自动切换（ASR/LLM/TTS 通用，语音激活与正常对话共用）。

任一模块访问失败或超时，自动切换到同模块下其他可用 provider
（不限于 mimoAI）。候选池来自三处：连接时快照、智能体/服务器配置、
以及管理端「同类型全部启用中的模型配置」——所以管理员在控制台新增
或改用某个 provider 后，下一次失败即可被选中；已失败的 provider 若
配置被改过也会重新尝试。代码层只做状态记录与实例重建。

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


# 密钥类字段：任一为空或仍是模板占位符，说明这个 provider 没真正配过，不作为候选。
# 管理端新建模型时默认写入 "你的api_key" 这类占位值，直接拿去切换只会白等一次失败。
_CREDENTIAL_KEYS = (
    "api_key",
    "api_password",
    "access_token",
    "personal_access_token",
    "token",
    "appid",
    "app_key",
    "appkey",
    "app_secret",
    "bot_id",
    "access_key_id",
    "access_key_secret",
    "secret",
    "secret_key",
    "authorization",
)
_PLACEHOLDER_MARKERS = ("你的", "请填写", "xxx")
_EMPTY_MARKERS = ("", "none", "null", "nil")

# 本机地址（Ollama / LM Studio / 本地 TTS 服务、本地模型目录等）排到最后，
# 避免它们占用有限的尝试次数把真正的云端备用挤掉（本地模型加载还特别慢）。
_LOCAL_HOSTS = ("localhost", "127.0.0.1", "0.0.0.0", "::1")
_LOCAL_URL_KEYS = ("base_url", "url", "api_url", "ws_url")
_LOCAL_PATH_KEYS = ("model_dir",)

# 一次切换最多尝试几个候选：切 provider 要建连/握手，逐个试太慢。
DEFAULT_MAX_CANDIDATES = 3


def _iter_strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _iter_strings(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _iter_strings(item)


def provider_is_configured(conf):
    """密钥类字段都填了、且没有模板占位值才算可用（没有这类字段的 provider 视为可用）。"""
    if not isinstance(conf, dict) or not conf:
        return False
    for text in _iter_strings(conf):
        if any(marker in text for marker in _PLACEHOLDER_MARKERS):
            return False
    for key in _CREDENTIAL_KEYS:
        if key not in conf:
            continue
        value = conf.get(key)
        text = "" if value is None else str(value).strip()
        if text.rstrip(";").strip().lower() == "bearer":
            return False
        if text.strip().lower() in _EMPTY_MARKERS:
            return False
    return True


def _is_local_endpoint(conf):
    for key in _LOCAL_URL_KEYS:
        value = str(conf.get(key) or "").lower()
        if any(host in value for host in _LOCAL_HOSTS):
            return True
    for key in _LOCAL_PATH_KEYS:
        if conf.get(key):
            return True
    return False


def _max_candidates(conn):
    try:
        return max(1, int(conn_config(conn).get("failover_max_candidates", DEFAULT_MAX_CANDIDATES)))
    except Exception:
        return DEFAULT_MAX_CANDIDATES



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


# 候选池缓存：主用 provider 挂掉时所有连接会同时触发刷新，
# 短 TTL 足以挡住这次风暴，又不影响管理员改配置后很快生效。
_CANDIDATE_TTL = 20.0
_candidate_cache = {}  # module_type -> (expire_at, providers)

# 配置刷新是尽力而为：拿不到就沿用现有快照，不能把切换本身拖死
_REFRESH_TIMEOUT = 5.0


async def _fetch_model_candidates(conn, module_type):
    """从管理端拉同类型所有启用中的模型配置，作为候选池。

    管理端 /config/agent-models 只返回智能体选中的那一个 provider，
    单靠它永远只有一个候选；候选池必须来自模型配置列表。
    """
    now = time.monotonic()
    cached = _candidate_cache.get(module_type)
    if cached is not None and cached[0] > now:
        return cached[1]
    try:
        from config.manage_api_client import get_model_candidates

        data = await get_model_candidates(module_type)
    except Exception as exc:
        _log(conn, "debug", f"获取{module_type}候选模型失败: {exc}")
        return {}
    providers = data.get("providers") if isinstance(data, dict) else None
    providers = providers if isinstance(providers, dict) else {}
    if providers:
        _candidate_cache[module_type] = (now + _CANDIDATE_TTL, providers)
    return providers


async def refresh_module_providers(conn, module_type):
    """实时重读该模块的 provider 配置（管理员救场入口）。

    合并三路来源，后写覆盖先写，但都不删除已有条目：
    1. conn.config 里已有的 provider（连接建立时的快照、上一轮切换结果）；
    2. 智能体/服务器配置里的主用 provider（管理员刚改过的配置）；
    3. 管理端同类型全部启用中的模型配置（新增的备用 provider 立即成为候选）。

    任一路失败都保持已有内容（旧配置仍可作为候选）。本地 config.yaml 模式
    没有实时来源，维持连接时的快照。
    """
    if not getattr(conn, "read_config_from_api", False):
        return
    if not isinstance(getattr(conn, "config", None), dict):
        return

    cfg = conn.config
    block = cfg.get(module_type)
    merged = dict(block) if isinstance(block, dict) else {}

    primary = None
    try:
        primary = await asyncio.wait_for(
            _fetch_primary_block(conn, module_type), timeout=_REFRESH_TIMEOUT
        )
    except Exception as exc:
        _log(conn, "debug", f"刷新{module_type}主用配置超时或失败: {exc}")
    if isinstance(primary, dict) and primary:
        merged.update(primary)

    try:
        candidates = await asyncio.wait_for(
            _fetch_model_candidates(conn, module_type), timeout=_REFRESH_TIMEOUT
        )
    except Exception:
        candidates = {}
    added = [name for name in candidates if name not in merged]
    for name in added:
        merged[name] = candidates[name]

    if not merged:
        return
    cfg[module_type] = merged
    _log(
        conn, "info",
        f"{module_type} 候选provider已刷新: 共{len(merged)}个"
        f"（新增{len(added)}个），当前={active_provider_name(conn, module_type)}",
    )


async def _fetch_primary_block(conn, module_type):
    """智能体配置优先；设备未绑定（语音激活常态）回退服务器级配置。"""
    headers = getattr(conn, "headers", None) or {}
    device_id = headers.get("device-id")
    fresh = None
    if device_id:
        try:
            from config.config_loader import get_private_config_from_api

            fresh = await get_private_config_from_api(
                conn.config, device_id, headers.get("client-id", device_id)
            )
        except Exception as exc:
            # 设备未绑定等拿不到 agent 配置，语音激活场景下属正常。
            _log(conn, "debug", f"刷新{module_type}配置: agent配置不可用({exc})，改用服务器配置")
            fresh = None
    block = (fresh or {}).get(module_type)
    if isinstance(block, dict) and block:
        return block
    try:
        from config.manage_api_client import get_server_config

        server = (await get_server_config()) or {}
    except Exception as exc:
        _log(conn, "debug", f"刷新{module_type}配置: 服务器配置获取失败({exc})")
        return None
    return (server or {}).get(module_type)


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


def _ranked_candidates(conn, module_type, block, active, failed):
    """该模块下可尝试的备用 provider，按优先级排序并限制数量。

    排序：failover_priority 显式指定 > 云端地址 > 本机地址，同分按配置顺序。
    跳过：当前 active、已试过且配置未变、密钥未配置的 provider。
    """
    priority = (conn_config(conn).get("failover_priority") or {}).get(module_type) or []
    if not isinstance(priority, (list, tuple)):
        priority = []
    prio_index = {name: i for i, name in enumerate(priority) if isinstance(name, str)}
    unordered = len(prio_index) + len(block) + 1

    scored = []
    for order, (name, conf) in enumerate(block.items()):
        if not isinstance(conf, dict) or not conf:
            continue
        if name == active:
            continue
        old = failed.get(name)
        if old is not None and old == provider_conf_hash(conf):
            continue  # 试过且配置没变
        if not provider_is_configured(conf):
            continue  # 没填密钥，切过去只会再失败一次
        score = (prio_index.get(name, unordered), 1 if _is_local_endpoint(conf) else 0, order)
        scored.append((score, name))
    scored.sort(key=lambda item: item[0])
    return [name for _, name in scored][: _max_candidates(conn)]


async def swap_to_fallback(conn, module_type):
    """切换到该模块下其他可用 provider，逐个尝试。

    切换前先实时刷新配置。候选顺序：failover_priority 显式指定优先，
    其余云端 provider 在前、本机地址在后，同分按配置顺序。已失败且配置
    未变的 provider 跳过；管理员改过配置的会重新纳入。
    返回切换成功的 provider 名，全部不可用返回 None。
    """
    await refresh_module_providers(conn, module_type)
    cfg = conn_config(conn)
    block = cfg.get(module_type) or {}
    active = active_provider_name(conn, module_type)
    state = failover_state(conn).setdefault(module_type, {})
    state["active"] = active
    failed = state.setdefault("failed", {})

    candidates = _ranked_candidates(conn, module_type, block, active, failed)
    if not candidates:
        _log(
            conn, "warning",
            f"{module_type} 无可用备用provider（当前={active}，已配置={list(block)}，"
            f"已试过且配置未变={sorted(failed)}，未配置密钥的已排除）",
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
