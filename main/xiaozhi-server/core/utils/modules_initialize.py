from typing import Dict, Any
from config.logger import setup_logging
from core.utils import tts, llm, intent, memory, vad, asr

TAG = __name__
logger = setup_logging()


def initialize_modules(
    logger,
    config: Dict[str, Any],
    init_vad=False,
    init_asr=False,
    init_llm=False,
    init_tts=False,
    init_memory=False,
    init_intent=False,
) -> Dict[str, Any]:
    """
    初始化所有模块组件

    Args:
        config: 配置字典

    Returns:
        Dict[str, Any]: 包含所有初始化后的模块的字典
    """
    modules = {}

    # 每个模块独立隔离：选中的 ASR 缺必填配置时只丢 ASR，
    # 不能让一个坏配置把已初始化成功的 TTS/LLM 一起带走。
    def _init(key, name, factory):
        try:
            modules[key] = factory()
            logger.bind(tag=TAG).info(f"初始化组件: {key}成功 {name}")
        except Exception as e:
            logger.bind(tag=TAG).error(f"初始化组件: {key}失败 {name}: {e}")

    # 初始化TTS模块
    if init_tts:
        select_tts_module = config["selected_module"]["TTS"]
        _init("tts", select_tts_module, lambda: initialize_tts(config))

    # 初始化LLM模块
    if init_llm:
        select_llm_module = config["selected_module"]["LLM"]
        llm_type = (
            select_llm_module
            if "type" not in config["LLM"][select_llm_module]
            else config["LLM"][select_llm_module]["type"]
        )
        _init(
            "llm",
            select_llm_module,
            lambda: llm.create_instance(
                llm_type, config["LLM"][select_llm_module]
            ),
        )

    # 初始化Intent模块
    if init_intent:
        select_intent_module = config["selected_module"]["Intent"]
        intent_type = (
            select_intent_module
            if "type" not in config["Intent"][select_intent_module]
            else config["Intent"][select_intent_module]["type"]
        )
        _init(
            "intent",
            select_intent_module,
            lambda: intent.create_instance(
                intent_type, config["Intent"][select_intent_module]
            ),
        )

    # 初始化Memory模块
    if init_memory:
        select_memory_module = config["selected_module"]["Memory"]
        memory_type = (
            select_memory_module
            if "type" not in config["Memory"][select_memory_module]
            else config["Memory"][select_memory_module]["type"]
        )
        _init(
            "memory",
            select_memory_module,
            lambda: memory.create_instance(
                memory_type,
                config["Memory"][select_memory_module],
                config.get("summaryMemory", None),
            ),
        )

    # 初始化VAD模块
    if init_vad:
        select_vad_module = config["selected_module"]["VAD"]
        vad_type = (
            select_vad_module
            if "type" not in config["VAD"][select_vad_module]
            else config["VAD"][select_vad_module]["type"]
        )
        _init(
            "vad",
            select_vad_module,
            lambda: vad.create_instance(
                vad_type, config["VAD"][select_vad_module]
            ),
        )

    # 初始化ASR模块
    if init_asr:
        select_asr_module = config["selected_module"]["ASR"]
        _init("asr", select_asr_module, lambda: initialize_asr(config))
    return modules


def initialize_tts(config):
    select_tts_module = config["selected_module"]["TTS"]
    tts_config = config["TTS"][select_tts_module].copy()
    tts_config.setdefault("tts_timeout", config.get("tts_timeout", 15))
    tts_type = (
        select_tts_module
        if "type" not in tts_config
        else tts_config["type"]
    )
    new_tts = tts.create_instance(
        tts_type,
        tts_config,
        str(config.get("delete_audio", True)).lower() in ("true", "1", "yes"),
    )
    # 记录自身 provider 名：故障切换时实例报障要能准确记账，不能靠全局 active 猜
    try:
        new_tts._failover_name = select_tts_module
    except Exception:
        pass
    return new_tts


def initialize_asr(config):
    select_asr_module = config["selected_module"]["ASR"]
    asr_type = (
        select_asr_module
        if "type" not in config["ASR"][select_asr_module]
        else config["ASR"][select_asr_module]["type"]
    )
    new_asr = asr.create_instance(
        asr_type,
        config["ASR"][select_asr_module],
        str(config.get("delete_audio", True)).lower() in ("true", "1", "yes"),
    )
    logger.bind(tag=TAG).info("ASR模块初始化完成")
    return new_asr


def initialize_voiceprint(asr_instance, config):
    """初始化声纹识别功能"""
    voiceprint_config = config.get("voiceprint")
    if not voiceprint_config:
        return False  

    # 应用配置
    if not voiceprint_config.get("url") or not voiceprint_config.get("speakers"):
        logger.bind(tag=TAG).warning("声纹识别配置不完整")
        return False
        
    try:
        asr_instance.init_voiceprint(voiceprint_config)
        logger.bind(tag=TAG).info("ASR模块声纹识别功能已动态启用")
        logger.bind(tag=TAG).info(f"配置说话人数量: {len(voiceprint_config['speakers'])}")
        return True
    except Exception as e:
        logger.bind(tag=TAG).error(f"动态初始化声纹识别功能失败: {str(e)}")
        return False
