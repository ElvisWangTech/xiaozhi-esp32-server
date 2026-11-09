"""Provider 故障自动切换（ASR/LLM/TTS 通用，语音激活与正常对话共用）。

任一模块出现明确失败（异常 / 超时 / 连接失败）就立即切换，不做失败计数、
不设阈值、不设冷却。切换时全局扫描候选池：必填字段缺失、空密钥、模板
占位的直接跳过；剩下的按优先级逐个做带超时的连接验证，连不上就试下一个，
直到找到能用的为止。候选池来自三处：连接时快照、智能体/服务器配置、
以及管理端「同类型全部启用中的模型配置」——管理员在控制台新增或改用
某个 provider 后，下一次失败即可被选中。

三个 provider 都是连接级实例（conn.asr / conn.tts / conn.llm），可在
运行时热替换，无需重连设备：
- LLM：conn.chat / _ask_llm_json 调用失败时切换；
- TTS：语音激活用 TTSAudioProbe 探测静默死亡，正常对话由明确合成失败触发；
- ASR：建连失败或接口报错时由 receiveAudioHandle 触发（空识别不算失败）。

唯一保留的状态是「本轮已试过哪些候选」的遍历游标，用于保证一轮故障内
不回头、试完即停；它不是失败次数，也不跨轮次累积。
"""

import asyncio
import json
import time

TAG = __name__

# 各模块调用/建连超时（秒）。可用 conn.config["failover_timeout"][模块名] 覆盖。
FAILOVER_TIMEOUTS = {"LLM": 30.0, "TTS": 15.0, "ASR": 10.0}

# 切换本身包含实时配置刷新（HTTP）+ 实例重建 + 音频通道握手，单独给超时。
_SWAP_TIMEOUTS = {"LLM": 60.0, "TTS": 45.0, "ASR": 30.0}

# 候选自检（provider.probe_ready）超时。只有真正会建连的 provider 才有这一步。
_PROBE_TIMEOUT = 10.0


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
_LOCAL_URL_KEYS = ("base_url", "url", "api_url", "ws_url", "host")
_LOCAL_PATH_KEYS = ("model_dir",)

# 各 provider type 的必填字段（按 conf["type"] 匹配，TTS/ASR/LLM 通用）。
# 管理端模板会把整份字段写全（含空值），但手工配置/yaml 里可能整个字段缺失：
# 这类候选重建必然失败或握手必挂，必须在排序阶段就跳过，不浪费候选名额。
# 注意：本机 endpoint 的「凭证类」必填字段豁免（自部署服务不留 key 是常态）。
_PROVIDER_REQUIRED_KEYS = {
    # TTS / ASR 共用 type 的只写一份
    "huoshan_double_stream": ("appid", "access_token", "ws_url"),
    "doubao": ("appid", "access_token"),
    "doubao_stream": ("appid", "access_token"),
    "alibl_stream": ("appkey", "api_key", "ws_url"),
    "aliyunbl_stream": ("api_key",),
    "xunfei_stream": ("app_id", "api_key", "api_secret"),
    # group_id 不是必填：新版 t2a_v2 接口 GroupId 留空也能正常合成（已实测）
    "minimax_httpstream": ("api_key",),
    "aliyun_stream": ("token", "appkey"),
    "aliyun": ("appkey", "access_key_id", "access_key_secret"),
    "tencent": ("appid", "secret_id", "secret_key"),
    "baidu": ("app_id", "api_key", "secret_key"),
    "openai": ("api_key",),
    "siliconflow": ("access_token",),
    "cozecn": ("access_token",),
    "qwen_tts": ("api_key",),
    "qwen_audio_tts": ("api_key",),
    "qwen_base": ("api_key",),
    "qwen3_asr_flash": ("api_key",),
    "mimo": ("api_key",),
    "fishspeech": ("api_url",),
    "gpt_sovits_v2": ("url",),
    "gpt_sovits_v3": ("url",),
    "custom": ("url",),
    "index_stream": ("api_url",),
    "paddle_speech": ("url",),
    "vosk": ("model_path",),
    "fun_local": ("model_dir",),
    "fun_server": ("host",),
    "sherpa_onnx_local": ("model_dir",),
}

# 一次切换最多尝试几个候选；0 表示不限制（全局扫描所有已配置候选）。
# 未配置的 provider 在排序阶段就被跳过，实际需要建连的通常只有几个。
# 可用 conn.config["failover_max_candidates"] 覆盖。
DEFAULT_MAX_CANDIDATES = 0

# 「本轮已试过哪些候选」的游标生命周期。只用于决定下一次故障从哪开始扫，
# 不是冷却：任何时刻出现明确失败都会立即切换，不受这个窗口限制。
_TRY_ROUND_RESET = 30.0


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
    """必填字段齐全、没有占位值才算可用。

    三层检查：
    1. 任意字符串值里出现模板占位（"你的xxx"）→ 没配过；
    2. 按 conf["type"] 查必填字段注册表，缺失或为空 → 没配完；
    3. 云端 provider 的凭证字段（api_key 等）存在但为空 → 没配过。
       本机 endpoint 豁免第 3 层：自部署服务（FishSpeech/GPT-SoVITS 等）
       不需要凭证，api_key 留空是常态，不能因此被排除出候选池。
    """
    if not isinstance(conf, dict) or not conf:
        return False
    for text in _iter_strings(conf):
        if any(marker in text for marker in _PLACEHOLDER_MARKERS):
            return False
    if _missing_required_keys(conf):
        return False
    if _is_local_endpoint(conf):
        return True
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


def _missing_required_keys(conf):
    """按 type 返回缺失/为空的必填字段列表（本机 endpoint 豁免凭证类字段）。"""
    ptype = str(conf.get("type") or "").strip()
    required = _PROVIDER_REQUIRED_KEYS.get(ptype)
    if not required:
        return []
    local = _is_local_endpoint(conf)
    missing = []
    for key in required:
        if local and key in _CREDENTIAL_KEYS:
            continue
        value = conf.get(key)
        if value is None or str(value).strip().lower() in _EMPTY_MARKERS:
            missing.append(key)
    return missing


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
        # 0 表示不限制（全局扫描）；不能夹到 1，否则每轮只试一个候选。
        return max(0, int(conn_config(conn).get("failover_max_candidates", DEFAULT_MAX_CANDIDATES)))
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
    """每个 module 的切换状态：active + 本轮遍历游标 tried（无失败计数/黑名单）。"""
    state = getattr(conn, "_failover_state", None)
    if state is None:
        state = conn._failover_state = {}
    return state


def active_provider_name(conn, module_type):
    state = failover_state(conn).get(module_type)
    if state and state.get("active"):
        return state["active"]
    return (conn_config(conn).get("selected_module") or {}).get(module_type)


def mark_current_failed(conn, module_type, provider_name=None):
    """返回报障的 provider 名；不再记录失败、黑名单或时间戳。

    名字只用于 expect_active 并发去重：切换后 active 已变，后到的调用
    直接复用别人的切换结果，避免连环切换。
    """
    state = failover_state(conn).setdefault(module_type, {})
    active = provider_name or state.get("active") or (
        conn_config(conn).get("selected_module") or {}
    ).get(module_type)
    if not active:
        return None
    if not provider_name:
        state["active"] = active

    # 遍历游标：一轮故障内记住已经试过的 provider，保证「切到下一个」不会
    # 回头、试完就能停。超过窗口或候选耗尽即重置，下一轮重新全局扫描。
    now = time.monotonic()
    tried = state.get("tried")
    if not isinstance(tried, set):
        tried = state["tried"] = set()
    if now - state.get("tried_at", 0.0) > _TRY_ROUND_RESET:
        tried.clear()
    state["tried_at"] = now
    tried.add(active)
    return active


def _swap_lock(conn, module_type):
    """每个模块一把切换锁：TTS 文本线程、语音激活协程可能同时触发切换。"""
    locks = getattr(conn, "_failover_locks", None)
    if locks is None:
        locks = {}
        try:
            conn._failover_locks = locks
        except Exception:
            return None
    lock = locks.get(module_type)
    if lock is None:
        lock = asyncio.Lock()
        locks[module_type] = lock
    return lock


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


def _ranked_candidates(conn, module_type, block, active, state=None):
    """全局扫描：只留下配置齐全的 provider，按优先级排序。

    排序：failover_priority 显式指定 > 云端地址 > 本机地址，同分按配置顺序。
    跳过：当前 active、必填字段缺失/空密钥/模板占位的 provider。
    不做失败黑名单、不做 TTL、默认不限制数量——每个留下的候选都会在
    切换流程里做一次带超时的连接验证，连不上就试下一个。
    """
    priority = (conn_config(conn).get("failover_priority") or {}).get(module_type) or []
    if not isinstance(priority, (list, tuple)):
        priority = []
    prio_index = {name: i for i, name in enumerate(priority) if isinstance(name, str)}
    unordered = len(prio_index) + len(block) + 1

    tried = (state or {}).get("tried") or set()
    unconfigured = []
    scored = []
    for order, (name, conf) in enumerate(block.items()):
        if not isinstance(conf, dict) or not conf:
            continue
        if name == active or name in tried:
            continue  # 当前实例、本轮已经试过且连不上的，直接跳过
        if not provider_is_configured(conf):
            missing = _missing_required_keys(conf)
            unconfigured.append(f"{name}({','.join(missing) or '缺密钥'})")
            continue
        score = (prio_index.get(name, unordered), 1 if _is_local_endpoint(conf) else 0, order)
        scored.append((score, name))
    if unconfigured:
        _log(
            conn, "debug",
            f"{module_type} 跳过未配置的候选: {unconfigured}",
        )
    scored.sort(key=lambda item: item[0])
    ranked = [name for _, name in scored]
    limit = _max_candidates(conn)
    return ranked if limit <= 0 else ranked[:limit]


async def _probe_candidate(conn, module_type, provider, name):
    """对候选做一次带超时的连接验证；失败或超时立即返回 False。

    open_audio_channels 只起本地线程，任何配置（哪怕 ws_url 是 404）都会
    「成功」。这里补两步真实连接：provider 自带的 probe_ready() 握手，
    以及流式 ASR 的 ensure_connected() 建连。
    """
    ready = getattr(provider, "probe_ready", None)
    if ready is not None:
        try:
            if not await asyncio.wait_for(ready(), timeout=_PROBE_TIMEOUT):
                _log(conn, "warning", f"{module_type} 候选({name})自检未通过")
                return False
        except Exception as exc:
            _log(conn, "warning", f"{module_type} 候选({name})自检超时或失败: {exc}")
            return False

    if module_type == "ASR":
        ensure = getattr(provider, "ensure_connected", None)
        if ensure is not None:
            try:
                if await asyncio.wait_for(ensure(conn), timeout=_PROBE_TIMEOUT) is False:
                    _log(conn, "warning", f"{module_type} 候选({name})建连失败")
                    return False
            except Exception as exc:
                _log(conn, "warning", f"{module_type} 候选({name})建连超时或失败: {exc}")
                return False
            # ensure_connected 是「提示音快结束时建连」的专用入口，会把实例
            # 置成忽略识别结果(_ignore_results=True)，只有提示音播完后的
            # finish_prompt_guard() 才复位。failover 场景没有提示音，必须
            # 在这里立刻复位，否则切换后的 ASR 会把用户每句话都丢掉，
            # 日志表现为「ASR 已连接但零识别结果」。
            finish = getattr(provider, "finish_prompt_guard", None)
            if finish is not None:
                finish()
    return True


async def swap_to_fallback(conn, module_type, expect_active=None):
    """切换到该模块下其他可用 provider，逐个尝试。

    切换前先实时刷新配置。候选顺序：failover_priority 显式指定优先，
    其余云端 provider 在前、本机地址在后，同分按配置顺序。已失败且配置
    未变的 provider 跳过；管理员改过配置的会重新纳入。
    返回切换成功的 provider 名，全部不可用返回 None。

    expect_active 是调用方认为「已经被自己标记失败」的 provider。并发触发时
    后到的调用发现 active 已变，直接复用别人的结果，避免连环切换。
    """
    lock = _swap_lock(conn, module_type)
    if lock is not None:
        async with lock:
            return await _swap_locked(conn, module_type, expect_active)
    return await _swap_locked(conn, module_type, expect_active)


async def _swap_locked(conn, module_type, expect_active):
    state = failover_state(conn).setdefault(module_type, {})
    current = active_provider_name(conn, module_type)
    if expect_active and current and current != expect_active:
        _log(
            conn, "info",
            f"{module_type} 已由其他线程切换到 {current}，跳过本次切换",
        )
        return current

    await refresh_module_providers(conn, module_type)
    cfg = conn_config(conn)
    block = cfg.get(module_type) or {}
    active = active_provider_name(conn, module_type)
    state["active"] = active

    candidates = _ranked_candidates(conn, module_type, block, active, state)
    if not candidates:
        # 一轮走完：清空游标，下一次新的故障重新全局扫描
        state["tried"] = set()
        _log(
            conn, "warning",
            f"{module_type} 没有可再试的备用provider（当前={active}，"
            f"全部候选={list(block)}；未配置的与本轮试过的已跳过）",
        )
        return None

    tried = state.setdefault("tried", set())
    for name in candidates:
        # 无论实例化/建连成功与否，本轮都算「试过」，避免下一轮又回头试它
        tried.add(name)
        provider = await rebuild_provider(conn, module_type, name)
        if provider is None:
            _log(conn, "warning", f"{module_type} 候选({name})实例化失败，试下一个")
            continue
        if module_type != "LLM":
            # 打开音频通道 + 带超时的连接验证；连不上就立刻试下一个。
            try:
                await provider.open_audio_channels(conn)
            except Exception as exc:
                _log(conn, "warning", f"{module_type} 候选({name})打开音频通道失败: {exc}")
                await _dispose_provider(provider)
                continue
            if not await _probe_candidate(conn, module_type, provider, name):
                await _dispose_provider(provider)
                continue

        # 标记来源，便于实例自己报障时认出自己（旧实例不会误标新 active）
        try:
            provider._failover_name = name
            provider._from_failover = True
        except Exception:
            pass

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
            old = getattr(conn, module_type.lower(), None)
            setattr(conn, module_type.lower(), provider)
            # 先让旧实例退役（工作线程退出），再关连接：否则新旧实例会同时
            # 往设备推音频，字幕/时序都会错乱。
            await _dispose_provider(old)
        state["active"] = name
        _log(
            conn, "warning",
            f"{module_type} 服务不可用，已自动切换到备用provider: {name}",
        )
        return name

    state["tried"] = set()
    _log(conn, "warning", f"{module_type} 已配置候选全部连不上: {candidates}")
    return None


async def _dispose_provider(provider):
    """让一个不再使用的实例彻底停掉并释放连接。

    shutdown() 负责停工作线程（base 提供）；close() 只关连接。
    顺序不能反：close 之后线程可能仍在跑，会继续往队列推音频。
    """
    if provider is None:
        return
    shutdown = getattr(provider, "shutdown", None)
    if shutdown is not None:
        try:
            shutdown()
        except Exception:
            pass
    try:
        close = getattr(provider, "close", None)
        if close is not None:
            await close()
    except Exception:
        pass


def swap_from_thread(conn, module_type, timeout=None, expect_active=None):
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
    future = asyncio.run_coroutine_threadsafe(
        swap_to_fallback(conn, module_type, expect_active=expect_active), loop
    )
    try:
        return future.result(timeout)
    except Exception as exc:
        _log(conn, "error", f"{module_type} 切换备用provider失败: {exc}")
        return None


def failover_module_sync(conn, module_type, timeout=None, provider_name=None):
    """工作线程版：把当前 provider 记为失败并切换。返回新 provider 名或 None。"""
    marked = mark_current_failed(conn, module_type, provider_name=provider_name)
    return swap_from_thread(
        conn, module_type, timeout=timeout, expect_active=marked
    )


async def failover_module(conn, module_type, provider_name=None):
    """事件循环协程版：把当前 provider 记为失败并切换。返回新 provider 名或 None。"""
    marked = mark_current_failed(conn, module_type, provider_name=provider_name)
    return await swap_to_fallback(conn, module_type, expect_active=marked)


async def failover_missing_module(conn, module_type):
    """关键组件初始化失败时的救场切换（在连接事件循环里执行）。

    与 failover_module 的区别只在日志语义：这里没有「曾经可用」的实例，
    选中的 provider 连实例都建不起来（缺必填配置/依赖缺失）。连接层
    发现 ASR/TTS/LLM 请求初始化了却拿不到实例时调用，成功后 ASR/TTS
    的音频通道由切换流程负责打开。
    """
    name = await failover_module(conn, module_type)
    if not name:
        active = active_provider_name(conn, module_type) or "未配置"
        _log(
            conn, "error",
            f"{module_type} 初始化失败（选中={active}）且无可用备用provider，"
            f"该连接无法处理{module_type}，请在管理端检查模型配置",
        )
    return name
