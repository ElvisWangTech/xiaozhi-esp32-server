import asyncio
import json
import re
import time
import uuid

from core.providers.tts.dto.dto import ContentType, TTSMessageDTO, SentenceType

TAG = __name__
# 报号中间常有停顿。这段静音内不播报，避免把用户后面的数字打断。
PHONE_PAUSE_SECONDS = 3
# 提交验证码后最多等多久（秒）。超时按失败处理，播报后接着开下一项。
# 用户说完验证码就听不到任何反馈，不能让他干等两分钟。
AUTH_CODE_WAIT_SECONDS = 15
# 没有验证码环节时（纯服务端自动化登录）可以等更久。
AUTH_POLL_WAIT_SECONDS = 90
# 轮询间隔（秒）。等结果期间一直按这个间隔查状态接口。
AUTH_POLL_INTERVAL_SECONDS = 1.5
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

class _RepeatRequest:
    """模型判断：用户没听清，要设备把刚才的问题再说一遍。"""


REPEAT = _RepeatRequest()


class _SkipRequest:
    """模型判断：用户明确不想设置昵称，想跳过或用默认。"""


SKIP = _SkipRequest()


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


def spoken_digits_only(text):
    """整句只有数字。带了别的字就交给模型，不用词表判断。"""
    compact = compact_text(unwrap_user_text(text))
    if not compact:
        return False
    return all(char.isdigit() or char in CN_DIGITS for char in compact)


def isolated_code(text):
    """整句就是一串验证码。夹了别的意思时返回 None。"""
    code = extract_code(text)
    if not code or not spoken_digits_only(text):
        return None
    return code


_NICKNAME_SYSTEM_PROMPT = (
    "你在语音引导里，从用户原话中提取助手昵称。"
    "只输出一个 JSON 对象，不要解释，不要使用 markdown。"
    '{"intent":"answer","nickname":"豆豆"}。'
    "没听清、要你把问题再说一遍时输出 "
    '{"intent":"repeat","nickname":""}。'
    "用户明确不想设置昵称、想跳过或用默认时输出 "
    '{"intent":"skip","nickname":""}。\n'
    "规则：\n"
    "1. 用户可能直接说昵称，也可能说整句，例如「你的昵称是旺财」「我想叫你豆豆吧」「就叫小美」。只保留真实昵称。\n"
    "2. 昵称 1 到 12 个字，不要带语气词。\n"
    "3. 语音识别经常把名字听错，例如用户想叫「初五」可能被识别成「初期」。"
    "只要原话读起来像是一个名字（通常很短、没有句子结构），即使可能听错了，"
    "也把它原样填进 nickname 并输出 answer——下一步会向用户复述确认，说错了可以当场改口。"
    "不要因为拿不准就输出 unclear，那会让用户陷入反复重说。\n"
    "4. 只有整句是语气词（例如「嗯」「啊」）、纯确认或纯否定（例如「对」「确认」「不对」）、"
    "或与起名完全无关的话时，才输出 unclear，nickname 留空。\n"
    "5. 否定里如果带了新昵称，例如「不对，你的昵称是小美」，intent 为 answer，取出小美。\n"
    "6. 「没听清」「你再说一遍」「刚才说什么」是 repeat。用户自己要重说答案，例如「我重说」，是 unclear。\n"
    "7. 用户表达放弃设置的意图，例如不想起、算了、先跳过、用默认的，intent 为 skip，nickname 留空。"
)
_SPEECH_TAILS = (
    "可以吗",
    "好不好",
    "对不对",
    "行不行",
    "就行了",
    "就可以了",
    "就好了",
    "的话",
    "好吗",
    "行吗",
    "对吗",
    "就行",
    "就好",
    "就可以",
    "谢谢",
    "吧",
    "啊",
    "呀",
    "呢",
    "哦",
    "哈",
    "嘛",
    "了",
)
_OWNER_SYSTEM_PROMPT = (
    "你在语音引导里，从用户原话中提取助手该怎么称呼用户。"
    "只输出一个 JSON 对象，不要解释，不要使用 markdown。"
    '{"intent":"answer","title":"老板"}。'
    "没听清、要你把问题再说一遍时输出 "
    '{"intent":"repeat","title":""}。'
    "用户明确不想设置称呼、想跳过或用默认时输出 "
    '{"intent":"skip","title":""}。\n'
    "规则：\n"
    "1. 用户可能直接说称呼，也可能说整句，例如「叫我老板」「你可以称呼我为张先生」「我是小王」。只保留真实称呼。\n"
    "2. 称呼 1 到 12 个字，不要带语气词。\n"
    "3. 语音识别经常把称呼听错。只要原话读起来像一个称呼（通常很短、没有句子结构），"
    "即使可能听错了，也把它原样填进 title 并输出 answer——下一步会向用户复述确认，说错了可以当场改口。"
    "不要因为拿不准就输出 unclear，那会让用户陷入反复重说。\n"
    "4. 只有整句是语气词（例如「嗯」「啊」）、纯确认或纯否定（例如「对」「确认」「不对」）、"
    "反问（例如「我该怎么称呼您」）、或与称呼完全无关的话时，才输出 unclear，title 留空。\n"
    "5. 否定里如果带了新称呼，例如「不对，叫我妈妈」，intent 为 answer，取出妈妈。\n"
    "6. 「没听清」「你再说一遍」是 repeat。用户自己要重说，是 unclear。\n"
    "7. 用户表达放弃设置的意图，例如不想设置、算了、先跳过、用默认的，intent 为 skip，title 留空。"
)
_SUPPLEMENT_SYSTEM_PROMPT = (
    "用户在补充角色设定，常常一次只说一句，说完会示意结束。"
    "判断这一句是在继续补充、示意结束、要求重听，还是听不清。"
    "只输出一个 JSON 对象，不要解释，不要使用 markdown。"
    '{"action":"content","text":"说话温柔一点"}、{"action":"end","text":""}、'
    '{"action":"repeat","text":""} 或 {"action":"unclear","text":""}。\n'
    "content：这一句提出了新的具体要求。text 写这个要求本身，去掉「还有」「然后」「再补充」这类连接词。\n"
    "end：整句都在表示补充说完了、没有了。\n"
    "repeat：没听清，让你把刚才的问题再说一遍。\n"
    "unclear：语气词、纯确认、反问，或听不清在说什么。\n"
    "先说没有、后面又给了具体要求时，是 content，不是 end。不要因为出现「没有」「不用」「算了」就判成 end，"
    "要看整句是不是还在提要求。"
)
_SUPPLEMENT_ACTIONS = ("content", "end", "repeat", "unclear")
_SUPPLEMENT_CONFIRM_INTENTS = ("accept", "more", "restart", "repeat", "unclear")
_SUPPLEMENT_CONFIRM_PROMPT = (
    "你在语音引导里，刚把收集到的补充设定复述给用户，等用户确认。"
    "只输出一个 JSON 对象，不要解释，不要使用 markdown。"
    '{"intent":"accept","text":""}、{"intent":"more","text":""}、{"intent":"restart","text":""}、'
    '{"intent":"repeat","text":""} 或 {"intent":"unclear","text":""}。\n'
    "accept：同意，就按这些补充保存。\n"
    "more：还要继续补充。text 写这一句新的要求；用户只说「还有」「等一下」而没给内容时，text 留空。\n"
    "restart：不要这些补充，要重新说，或者说得不对要改。用户自己要重说，例如「重说」「我说错了」，也是 restart。\n"
    "repeat：没听清，让你把刚才复述的内容再说一遍。例如「你再说一遍」「没听清」。\n"
    "unclear：反问、犹豫，或在问原因。\n"
    "句子里出现复述过的词，不等于同意。"
)
_CONFIRM_INTENTS = {"accept", "decline", "repeat", "correct", "unclear"}
_CONFIRM_SYSTEM_PROMPT = (
    "你在语音引导里判断用户有没有同意刚才的确认。用户消息里会给出刚才的问题和用户原话。"
    "只输出一个 JSON 对象，不要解释，不要使用 markdown。"
    '{"intent":"accept","value":""}、{"intent":"decline","value":""}、{"intent":"repeat","value":""}、'
    '{"intent":"correct","value":""} 或 {"intent":"unclear","value":""}。\n'
    "accept：整句是在明确同意。例如「对」「是的」「没错」「确认」「就这样」「好，重启吧」。value 留空。\n"
    "decline：拒绝、推辞，或用户要自己重答。例如「不对」「不要」「先别」「能不重启吗」「不想重启」「不用了」「重说」「我重说」。value 留空。\n"
    "repeat：没听清刚才的话，让你再说一遍。例如「没听清」「你再说一遍」「刚才说什么」。这不是 decline。\n"
    "correct：改口并给出了新内容。value 只放新的昵称、称呼或模板名。\n"
    "unclear：反问、犹豫，或在问原因。例如「必须重启吗」「为什么要重启」「是吗」「再想想」。\n"
    "句子里出现问题里的词，不等于同意。否定和反问里提到同一个词，按否定或反问判断。"
)
_SKILL_REQUEST_PROMPT = (
    "判断用户是不是在要求现在绑定技能。"
    "只输出一个 JSON 对象，不要解释，不要使用 markdown。"
    '{"intent":"bind"}、{"intent":"rebind"} 或 {"intent":"none"}。\n'
    "bind：明确要现在绑定、配置或设置技能。\n"
    "rebind：明确要重新绑定、重新开通、再绑一次。\n"
    "none：提问、否定、闲聊或反问。例如「不要绑定技能」「绑定技能是什么意思」「能不绑定吗」「先别开通」。\n"
    "出现「绑定技能」「重新绑定」不等于要执行。"
)
_RETRY_PROMPT = (
    "技能绑定失败了。用户可以现在重试，也可以稍后再说。"
    "只输出一个 JSON 对象，不要解释，不要使用 markdown。"
    '{"intent":"retry","rebind":false}、{"intent":"skip","rebind":false}、{"intent":"repeat","rebind":false} 或 {"intent":"unclear","rebind":false}。\n'
    "retry：同意现在再试，或说不要跳过。明确要重新绑定、重新开通时，intent 仍是 retry，并把 rebind 设为 true。\n"
    "skip：稍后再说、先不用、算了、取消、先聊天，或不想再试。\n"
    "repeat：没听清，让你把问题再说一遍。\n"
    "「不要重试」「先别试了」「能不再试吗」不是 retry。「不要跳过」「先别跳过」是 retry。\n"
    "句子里出现「重试」「再试」「绑定」不等于同意。"
)
_SMS_PROMPT = (
    "用户被要求说出短信验证码。也可以要求重发，或表示手机号错了要重新报手机号。"
    "只输出一个 JSON 对象，不要解释，不要使用 markdown。"
    '{"intent":"code"}、{"intent":"resend"}、{"intent":"restart"}、{"intent":"repeat"} 或 {"intent":"unclear"}。\n'
    "code：这句是在报验证码。前面带了「不对」，只要给出了验证码，仍是 code。\n"
    "resend：没收到或要求重发，而且没有在报一串新的验证码。\n"
    "restart：手机号错了，要重新说手机号。用户说「重说」但没说手机号错了，不是 restart。\n"
    "repeat：没听清你刚才的话，让你再说一遍。不是 restart。\n"
    "unclear：听不清用户想做什么。\n"
    "不要因为出现「不对」「重说」就重新收集手机号，要看整句。"
)
_AUTH_PROMPT = (
    "用户正在开通一项技能，被要求说出短信验证码。也可以跳过、要求重发，或重新绑定。"
    "只输出一个 JSON 对象，不要解释，不要使用 markdown。"
    '{"intent":"code"}、{"intent":"skip"}、{"intent":"resend"}、{"intent":"rebind"}、{"intent":"repeat"} 或 {"intent":"unclear"}。\n'
    "code：在报验证码。前面带「不对」但给出了验证码，仍是 code。\n"
    "skip：明确放弃这次开通。例如「跳过」「不用了」「算了」。\n"
    "「不要跳过」「能不跳过吗」「先别跳过」不是 skip。\n"
    "resend：没收到或请重发，且没有报出验证码。又说没收到又报了验证码时是 code。\n"
    "rebind：要重新绑定或重新开通。\n"
    "repeat：没听清，让你把问题再说一遍。不是 skip。\n"
    "出现「跳过」「重发」「绑定」不等于要执行对应动作。"
)
_PHONE_ASIDE_PROMPT = (
    "用户正在报手机号，这句里没有数字。"
    "只输出一个 JSON 对象，不要解释，不要使用 markdown。"
    '{"intent":"restart"}、{"intent":"repeat"}、{"intent":"ignore"} 或 {"intent":"unclear"}。\n'
    "restart：明确说错了、要清空、重新报号码。\n"
    "repeat：没听清，让你把刚才的问题再说一遍。\n"
    "ignore：语气词、等一下，或「能不重说吗」这种并不是在要求清空。\n"
    "出现「重说」「不对」不等于一定要清空，要看整句是不是在要求重来。"
)
_SHORT_NAME_LIMIT = 12
_SUPPLEMENT_LIMIT = 80
# 补充可以分几句说。记下一句后先等用户继续，这段静音内没有新内容就当作说完了。
SUPPLEMENT_PAUSE_SECONDS = 6
# 跳过设置时的兜底昵称。后端 saveNickname 拒绝空值且负责推进 onboarding 阶段，
# 所以跳过也必须提交一个昵称；用中性的「助手」，与 _begin_owner 的既有兜底一致。
_NICKNAME_DEFAULT = "助手"
# 首次提问就给例子，减少用户不知道该说多短而反复失败。
_ASK_NICKNAME_PROMPT = "请说出助手的昵称，比如豆豆、小美。"
# 模型判 unclear 时逐级给出更具体的引导，不重复同一句话。
_NICKNAME_RETRY_HINTS = (
    "没听清。请只说一个简短的名字，比如豆豆、小美。",
    "还是没听清。请靠近一点，直接说名字，不要加别的字。",
    "一直没听清。也可以说不想设置了，先用默认昵称。",
)
# 跳过称呼设置时的兜底。owner_title 不单独提交，而是与性格模板一起在 /role
# 一次性提交，后端 normalizeOwnerTitle 拒绝空值，所以跳过也必须给出称呼；
# 「主人」是该类设备的通用默认称呼。
_OWNER_DEFAULT = "主人"
_ASK_OWNER_PROMPT = "我该怎么称呼您呢？比如老板、张先生。"
_OWNER_RETRY_HINTS = (
    "没听清。请直接说一个称呼，比如老板、张先生。",
    "还是没听清。请靠近一点，直接说称呼，不要加别的字。",
    "一直没听清。也可以说不想设置了，先用默认称呼。",
)


def _strip_speech_tails(text):
    tails = sorted(_SPEECH_TAILS, key=len, reverse=True)
    changed = True
    while changed and text:
        changed = False
        for tail in tails:
            if len(text) > len(tail) and text.endswith(tail):
                text = text[: -len(tail)]
                changed = True
                break
    return text


def _valid_short_name(name, limit=_SHORT_NAME_LIMIT):
    if not name or name in set("吧啊呀呢哦哈嘛了"):
        return False
    return 1 <= len(name) <= limit


def _strip_bare_leads(compact):
    changed = True
    while changed and compact:
        changed = False
        for lead in ("嗯", "啊", "呃", "哎"):
            if compact.startswith(lead) and len(compact) > len(lead):
                compact = compact[len(lead) :]
                changed = True
                break
    return compact


def _plain_name(compact):
    return _strip_speech_tails(_strip_bare_leads(compact)).replace("「", "").replace("」", "")


def _log_guide(conn, level, message):
    logger = getattr(conn, "logger", None)
    if logger is None:
        return
    getattr(logger.bind(tag=TAG), level)(message)


def _intent_of(data, allowed):
    if not data:
        return None
    intent = str(data.get("intent") or "").strip()
    if intent not in allowed:
        return None
    return intent


def _flag_true(value):
    return value is True or str(value).lower() == "true"


async def confirm_turn(conn, text, subject):
    """同意、拒绝、改口，或让设备把问题再说一遍。模型失败时不当成同意。"""
    raw = unwrap_user_text(text).strip()
    if not compact_text(raw):
        return "unclear", ""
    asked = f"刚才的问题：{subject}\n用户原话：{raw}"
    data = await _ask_llm_json(conn, _CONFIRM_SYSTEM_PROMPT, asked, "确认意图")
    intent = _intent_of(data, _CONFIRM_INTENTS) or "unclear"
    value = _plain_name(compact_text((data or {}).get("value") or ""))
    if not _valid_short_name(value):
        value = ""
    if intent == "correct" and not value:
        intent = "unclear"
    _log_guide(conn, "info", f"确认意图: {raw} -> {intent} {value}")
    return intent, value


async def confirm_intent(conn, text, subject):
    intent, _value = await confirm_turn(conn, text, subject)
    return intent


async def resolve_skills_request(conn, text):
    """每句话都问模型是不是在要求绑定。问不出来就不绑定。"""
    raw = unwrap_user_text(text).strip()
    if not compact_text(raw):
        return None
    data = await _ask_llm_json(conn, _SKILL_REQUEST_PROMPT, raw, "技能指令")
    intent = _intent_of(data, {"bind", "rebind", "none"})
    _log_guide(conn, "info", f"技能指令: {raw} -> {intent or ''}")
    if intent in ("bind", "rebind"):
        return intent
    return None


async def resolve_skill_retry(conn, text):
    """重试、稍后再说，或让设备把问题再说一遍。"""
    raw = unwrap_user_text(text).strip()
    if not compact_text(raw):
        return "unclear", False
    data = await _ask_llm_json(conn, _RETRY_PROMPT, raw, "重试意图")
    intent = _intent_of(data, {"retry", "skip", "repeat", "unclear"}) or "unclear"
    rebind = bool(data) and _flag_true(data.get("rebind"))
    if rebind and intent == "unclear":
        intent = "retry"
    _log_guide(conn, "info", f"重试意图: {raw} -> {intent} rebind={rebind}")
    return intent, rebind


async def resolve_sms_reply(conn, text):
    """纯数字验证码直接收下。其余问模型。"""
    code = isolated_code(text)
    if code:
        return "code", code
    raw = unwrap_user_text(text).strip()
    if not compact_text(raw):
        return "unclear", None
    data = await _ask_llm_json(conn, _SMS_PROMPT, raw, "验证码意图")
    intent = _intent_of(data, {"code", "resend", "restart", "repeat", "unclear"}) or "unclear"
    _log_guide(conn, "info", f"验证码意图: {raw} -> {intent}")
    if intent == "code":
        return "code", extract_code(text)
    return intent, None


async def resolve_auth_reply(conn, text):
    """纯数字验证码直接收下。跳过、重发、重听都问模型。"""
    code = isolated_code(text)
    if code:
        return "code", code
    raw = unwrap_user_text(text).strip()
    if not compact_text(raw):
        return "unclear", None
    data = await _ask_llm_json(conn, _AUTH_PROMPT, raw, "授权验证码意图")
    intent = _intent_of(data, {"code", "skip", "resend", "rebind", "repeat", "unclear"}) or "unclear"
    _log_guide(conn, "info", f"授权验证码意图: {raw} -> {intent}")
    if intent == "code":
        return "code", extract_code(text)
    return intent, None


async def resolve_phone_aside(conn, text):
    """报号时这句没有数字。清空、重听还是忽略，都问模型。"""
    raw = unwrap_user_text(text).strip()
    if not compact_text(raw):
        return "ignore"
    data = await _ask_llm_json(conn, _PHONE_ASIDE_PROMPT, raw, "报号意图")
    intent = _intent_of(data, {"restart", "repeat", "ignore", "unclear"}) or "unclear"
    _log_guide(conn, "info", f"报号意图: {raw} -> {intent}")
    return intent


def parse_json_object(text):
    raw = (text or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?", "", raw).strip()
        raw = re.sub(r"```$", "", raw).strip()
    try:
        data = json.loads(raw)
    except Exception:
        match = re.search(r"\{.*\}", raw, re.S)
        if not match:
            return None
        try:
            data = json.loads(match.group(0))
        except Exception:
            return None
    if not isinstance(data, dict):
        return None
    return data


def parse_nickname_reply(text):
    data = parse_json_object(text)
    if not data:
        return None
    name = _plain_name(compact_text(data.get("nickname") or ""))
    if not _valid_short_name(name):
        return None
    if any(token in name for token in ("昵称", "名字", "叫你", "叫做", "就叫", "你的", "您的")):
        return None
    return name


async def _ask_llm_json(conn, system_prompt, user_text, label):
    llm = getattr(conn, "llm", None)
    if llm is None or not hasattr(llm, "response_no_stream"):
        _log_guide(conn, "warning", f"{label}时没有可用的对话模型")
        return None
    try:
        reply = await asyncio.to_thread(llm.response_no_stream, system_prompt, user_text)
    except Exception as exc:
        _log_guide(conn, "warning", f"{label}失败: {exc}")
        return None
    return parse_json_object(reply)


async def extract_nickname(conn, text):
    """每句都问模型。返回昵称、REPEAT、SKIP，或 None。"""
    raw = unwrap_user_text(text).strip()
    if not compact_text(raw):
        return None
    data = await _ask_llm_json(conn, _NICKNAME_SYSTEM_PROMPT, raw, "提取昵称")
    intent = _intent_of(data, {"answer", "repeat", "unclear", "skip"})
    if intent == "repeat":
        return REPEAT
    if intent == "skip":
        _log_guide(conn, "info", f"昵称识别: {raw} -> 跳过")
        return SKIP
    nickname = parse_nickname_reply(json.dumps(data, ensure_ascii=False) if data else "")
    _log_guide(conn, "info", f"昵称识别: {raw} -> {nickname or ''}")
    return nickname


def parse_owner_reply(text):
    data = parse_json_object(text)
    if not data:
        return None
    title = _plain_name(compact_text(data.get("title") or ""))
    if not _valid_short_name(title):
        return None
    if any(token in title for token in ("称呼", "叫我", "喊我", "你的", "您的", "怎么", "什么")):
        return None
    return title


async def extract_owner_title(conn, text):
    """每句都问模型。返回称呼、REPEAT、SKIP，或 None。"""
    raw = unwrap_user_text(text).strip()
    if not compact_text(raw):
        return None
    data = await _ask_llm_json(conn, _OWNER_SYSTEM_PROMPT, raw, "提取称呼")
    intent = _intent_of(data, {"answer", "repeat", "unclear", "skip"})
    if intent == "repeat":
        return REPEAT
    if intent == "skip":
        _log_guide(conn, "info", f"称呼识别: {raw} -> 跳过")
        return SKIP
    title = parse_owner_reply(json.dumps(data, ensure_ascii=False) if data else "")
    _log_guide(conn, "info", f"称呼识别: {raw} -> {title or ''}")
    return title


def clean_supplement(text):
    raw = unwrap_user_text(text).strip()
    return raw.strip("，。,.!！?？、 \n\t")


def _persona_system_prompt(names):
    listed = "、".join(names)
    return (
        "你在语音引导里，根据用户原话从给定性格模板中选一个。"
        f"可选模板只有这些：{listed}。"
        "只输出一个 JSON 对象，不要解释，不要使用 markdown。"
        '{"intent":"answer","template":"英语老师"}。'
        "没听清、让你再说一遍时输出 "
        '{"intent":"repeat","template":""}。'
        "没选中时输出 "
        '{"intent":"unclear","template":""}。\n'
        "规则：\n"
        "1. template 必须是列表中的原文，不能自造。\n"
        "2. 用户说序号、名字或描述，都要对应到列表中的一个，intent 为 answer。\n"
        "3. 只是确认，或只是否定而没有改选，intent 为 unclear，template 留空。\n"
        "4. 否定里改选了另一个，例如「不要英语老师，我要星际游子」，intent 为 answer，选出星际游子。\n"
        "5. 「没听清」「你再说一遍」是 repeat。听不清或对不上任何一个，是 unclear。"
    )


def parse_persona_reply(text, names):
    data = parse_json_object(text)
    if not data:
        return None
    chosen = str(data.get("template") or "").strip()
    for name in names:
        if chosen == name or compact_text(chosen) == compact_text(name):
            return name
    return None


async def resolve_persona(conn, text, names):
    """每句都问模型，从给定模板里选。返回模板名、REPEAT，或 None。"""
    raw = unwrap_user_text(text).strip()
    if not names or not compact_text(raw):
        return None
    data = await _ask_llm_json(conn, _persona_system_prompt(names), raw, "选择性格")
    if _intent_of(data, {"answer", "repeat", "unclear"}) == "repeat":
        chosen = parse_persona_reply(json.dumps(data, ensure_ascii=False) if data else "", names)
        if not chosen:
            _log_guide(conn, "info", f"性格识别: {raw} -> repeat")
            return REPEAT
    chosen = parse_persona_reply(json.dumps(data, ensure_ascii=False) if data else "", names)
    _log_guide(conn, "info", f"性格识别: {raw} -> {chosen or ''}")
    return chosen


async def resolve_supplement_turn(conn, text, collected=""):
    """判断这一句是继续补充、示意结束、要求重听，还是听不清。

    返回 (action, content)。action 为 content 时 content 是这一句的要求。
    """
    raw = unwrap_user_text(text).strip()
    if not compact_text(raw):
        return "unclear", ""
    asked = f"已经记下的补充：{collected or '暂无'}\n用户这一句：{raw}"
    data = await _ask_llm_json(conn, _SUPPLEMENT_SYSTEM_PROMPT, asked, "判断补充")
    action = str((data or {}).get("action") or "").strip()
    if action not in _SUPPLEMENT_ACTIONS:
        return "unclear", ""
    content = ""
    if action == "content":
        content = clean_supplement((data or {}).get("text") or "") or clean_supplement(raw)
    _log_guide(conn, "info", f"补充识别: {raw} -> {action} {content}")
    return action, content


async def resolve_supplement_confirm(conn, text, collected):
    """复述完补充后等确认。同意、还要补充、重说、重听，或听不清。"""
    raw = unwrap_user_text(text).strip()
    if not compact_text(raw):
        return "unclear", ""
    asked = f"刚才复述的补充：{collected}\n用户原话：{raw}"
    data = await _ask_llm_json(conn, _SUPPLEMENT_CONFIRM_PROMPT, asked, "确认补充")
    intent = str((data or {}).get("intent") or "").strip()
    if intent not in _SUPPLEMENT_CONFIRM_INTENTS:
        return "unclear", ""
    content = clean_supplement((data or {}).get("text") or "")
    _log_guide(conn, "info", f"确认补充: {raw} -> {intent} {content}")
    return intent, content


def parse_persona_catalog(payload):
    """模板接口返回名称列表，或带音色编码的对象列表。"""
    names = []
    voices = {}
    for item in payload or []:
        if isinstance(item, str) and item.strip():
            names.append(item.strip())
            continue
        if not isinstance(item, dict) or not item.get("name"):
            continue
        name = str(item["name"]).strip()
        if not name:
            continue
        names.append(name)
        voice = str(item.get("voice") or "").strip()
        if voice:
            voices[name] = voice
    return names, voices


def apply_persona_voice(tts, voice):
    """下一句合成改用这个音色。火山会话按句启动，改 voice 即可。"""
    if not voice or tts is None or not hasattr(tts, "voice"):
        return False
    tts.voice = voice
    return True


def spoken_ordinal(number):
    """序号用「二」，不用「两」。"""
    if number == 2:
        return "二"
    return spoken_count(number)


def persona_menu(names):
    parts = [f"{spoken_ordinal(index)}，{name}" for index, name in enumerate(names, 1)]
    return "接下来选一个性格。" + "。".join(parts) + "。请说序号，或者说名字。"


def spoken_digits(number):
    return "，".join(list(number))


async def start_skills_onboarding(conn, announce=None, rebind=False):
    """日常对话中主动说「绑定技能」时进入授权绑定。已开通的跳过，除非用户说重新绑定。"""
    if getattr(conn, "voice_activation_handler", None) is not None:
        handler = conn.voice_activation_handler
        if getattr(handler, "stage", None) == "skills":
            handler.include_auth = True
            if rebind:
                handler.rebind_auth = True
            handler._announce_skill_failure = True
            conn.speak_skill_failure = True
            conn.voice_onboarding = "skills"
            if announce:
                await handler._say(announce)
            if not handler._skill_bind_inflight:
                await handler._bind_skills()
            return
        await handler._say("当前还在完成其他设置，请稍后再试。")
        return
    conn.voice_onboarding = "skills"
    conn.skills_pending = True
    conn.voice_activation_handler = VoiceActivationSession(
        conn, stage="skills", include_auth=True, rebind_auth=rebind
    )
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
    ASK_OWNER = "ask_owner"
    CONFIRM_OWNER = "confirm_owner"
    ASK_PERSONA = "ask_persona"
    CONFIRM_PERSONA = "confirm_persona"
    ASK_SUPPLEMENT = "ask_supplement"
    CONFIRM_SUPPLEMENT = "confirm_supplement"
    RETRY_SKILLS = "retry_skills"
    ASK_AUTH_CODE = "ask_auth_code"
    # 配置完毕到重启下发之间的终态：不再接收用户输入，避免误触发上一步的提问。
    REBOOTING = "rebooting"

    # 方案 B：默认依次开通；瑞幸先于滴滴（滴滴常需实名/免密支付）
    # 不询问「要开通吗」；仅 waiting_code 时打断要验证码。
    # 自动开通只在第一次进入 skills 时发生；之后必须用户主动说「绑定技能」。
    AUTH_QUEUE = (
        ("luckin", "瑞幸咖啡"),
        ("didi", "滴滴出行"),
    )

    def __init__(self, conn, stage="bind", announce_bind_success=False, include_auth=False, rebind_auth=False):
        self.conn = conn
        self.stage = stage
        if stage == "bind":
            self.state = self.ASK_PHONE
        elif stage == "nickname":
            self.state = self.ASK_NICKNAME
        elif stage == "role":
            self.state = self.ASK_OWNER
        else:
            self.state = self.RETRY_SKILLS
        self.phone = None
        self.phone_digits = ""
        self.nickname = None
        # 模型连续判 unclear 的次数。用于切换引导话术，避免重复同一句提示。
        self._nickname_misses = 0
        self._owner_misses = 0
        self.owner_title = None
        self.persona_name = None
        self.persona_templates = []
        self.persona_voices = {}
        # 补充设定可以分几句说，逐句记下来，最后一起提交。
        self.supplement_parts = []
        self._supplement_epoch = 0
        self._supplement_wait_task = None
        self._supplement_nudges = 0
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
        self._asr_ready_task = None
        self._idle_task = None
        self._giving_up = False
        self._skill_bind_inflight = False
        self._announce_skill_failure = False
        self._auth_polling = False
        # 只有第一次技能引导，或用户主动说「绑定技能」，才自动跑瑞幸/滴滴。
        self.include_auth = include_auth
        # 用户说「重新绑定」时，已开通的授权技能也再走一遍。
        self.rebind_auth = rebind_auth

    async def start(self):
        self._start_idle_watch()
        if self.stage == "skills":
            await self._bind_skills()
            return
        if self.state == self.ASK_NICKNAME:
            prefix = "绑定成功。" if self.announce_bind_success else ""
            await self._say(prefix + _ASK_NICKNAME_PROMPT)
            return
        if self.state == self.ASK_OWNER:
            await self._say("我该怎么称呼您呢？")
            return
        await self._ask_for_phone("请说出您的手机号码，一共十一位。")

    def _allow_barge_in(self):
        return self.state not in (self.ASK_PHONE, self.ASK_CODE, self.ASK_AUTH_CODE)

    async def handle_text(self, text):
        if self._busy:
            return
        # 已播报「即将重启」，等待下发重启工具期间不再处理任何输入。
        if self.state == self.REBOOTING:
            return
        # 报号、报验证码时不接收，避免提示音和中间停顿被当成新的一句话。
        # 确认类问题（对不对、有没有补充）允许打断，否则「对」会在播报尾巴里被丢掉。
        if self._playback_active() or getattr(self.conn, "client_is_speaking", False):
            if not self._allow_barge_in():
                return
            from core.handle.abortHandle import handleAbortMessage

            await handleAbortMessage(self.conn)
        content = unwrap_user_text(text)
        if not content:
            return
        await self._send_recognized_text(content)
        if await self._speak_if_user_requests_skills(content):
            return
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
        elif self.state == self.ASK_OWNER:
            await self._on_owner(text)
        elif self.state == self.CONFIRM_OWNER:
            await self._on_confirm_owner(text)
        elif self.state == self.ASK_PERSONA:
            await self._on_persona(text)
        elif self.state == self.CONFIRM_PERSONA:
            await self._on_confirm_persona(text)
        elif self.state == self.ASK_SUPPLEMENT:
            await self._on_supplement(text)
        elif self.state == self.CONFIRM_SUPPLEMENT:
            await self._on_confirm_supplement(text)
        elif self.state == self.RETRY_SKILLS:
            await self._on_retry_skills(text)
        elif self.state == self.ASK_AUTH_CODE:
            await self._on_auth_code(text)

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
        if epoch != self._phone_epoch or self.state != self.ASK_PHONE or self._giving_up:
            return
        if not self.phone_digits:
            await self._ask_for_phone("没有听清手机号，请按顺序说出十一位手机号。")
            return
        missing = 11 - len(self.phone_digits)
        await self._ask_for_phone(f"还差{spoken_count(missing)}位，请继续说。")

    async def _accept_phone(self, phone):
        self._invalidate_phone_wait()
        self.phone_digits = ""
        self.phone = phone
        self.state = self.CONFIRM_PHONE
        await self._say(f"您的手机号是，{spoken_digits(phone)}。对吗？")

    async def _on_phone(self, text):
        self._invalidate_phone_wait()
        digits = digits_from_text(text)
        if not digits:
            aside = await resolve_phone_aside(self.conn, text)
            if aside == "repeat":
                await self._repeat_prompt()
                return
            if aside == "restart":
                self.phone_digits = ""
                await self._ask_for_phone("好的，请重新按顺序说出十一位手机号。")
                return
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
            await self._ask_for_phone("位数超过十一位了，请重新按顺序说出手机号。")
            return
        if len(self.phone_digits) < 11:
            self._schedule_phone_wait()
            return
        if not is_mobile_phone(self.phone_digits):
            self.phone_digits = ""
            await self._ask_for_phone("手机号需要是以1开头的十一位数字，请重新说。")
            return
        await self._accept_phone(self.phone_digits)

    async def _on_confirm_phone(self, text):
        echoed = digits_from_text(text)
        if echoed and self.phone and self.phone.startswith(echoed) and spoken_digits_only(text):
            return
        corrected = extract_phone(text)
        if corrected and corrected != self.phone:
            self.phone = corrected
            await self._say(f"您的手机号是，{spoken_digits(self.phone)}。对吗？")
            return
        intent = await confirm_intent(self.conn, text, "刚才报的手机号对不对")
        if intent == "repeat":
            await self._repeat_prompt()
            return
        if intent == "decline":
            self.phone = None
            self.phone_digits = ""
            self.state = self.ASK_PHONE
            await self._ask_for_phone("好的，请重新按顺序说出十一位手机号。")
            return
        if intent != "accept":
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
        action, code = await resolve_sms_reply(self.conn, text)
        if action == "repeat":
            await self._repeat_prompt()
            return
        if action == "resend":
            self.state = self.CONFIRM_PHONE
            await self._send_sms()
            return
        if action == "restart":
            self.phone = None
            self.phone_digits = ""
            self.state = self.ASK_PHONE
            await self._ask_for_phone("好的，请重新按顺序说出十一位手机号。")
            return
        if action != "code" or not code:
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
        nickname = await extract_nickname(self.conn, text)
        if nickname is REPEAT:
            await self._repeat_prompt()
            return
        if nickname is SKIP:
            await self._skip_nickname()
            return
        if not nickname:
            self._nickname_misses += 1
            await self._say(self._nickname_retry_hint())
            return
        self._nickname_misses = 0
        self.nickname = nickname
        self.state = self.CONFIRM_NICKNAME
        await self._say(f"助手的昵称是{nickname}，对吗？")

    def _nickname_retry_hint(self):
        index = min(self._nickname_misses, len(_NICKNAME_RETRY_HINTS)) - 1
        return _NICKNAME_RETRY_HINTS[max(index, 0)]

    async def _skip_nickname(self):
        """用户明确放弃设置时给一个出口：默认昵称走确认，仍可当场改口。

        后端靠 saveNickname 把 onboarding 从 nickname 推进到 role，
        所以必须走确认再提交，不能直接切到下一步，否则重连会退回本步。
        """
        self._nickname_misses = 0
        self.nickname = _NICKNAME_DEFAULT
        self.state = self.CONFIRM_NICKNAME
        await self._say(f"好的，昵称先用{self.nickname}。对吗？说确认继续，或者说新的名字。")

    async def _on_confirm_nickname(self, text):
        intent, nickname = await confirm_turn(self.conn, text, f"助手昵称是不是{self.nickname}")
        if intent == "repeat":
            await self._repeat_prompt()
            return
        if nickname and nickname != self.nickname:
            self.nickname = nickname
            await self._say(f"助手的昵称是{self.nickname}，对吗？")
            return
        if intent == "accept" or nickname == self.nickname:
            await self._commit_nickname()
            return
        if intent == "decline":
            self.nickname = None
            self._nickname_misses = 0
            self.state = self.ASK_NICKNAME
            await self._say("好的，请重新说出助手昵称，比如豆豆、小美。")
            return
        await self._say("请说确认，或者说重说。")

    async def _commit_nickname(self):
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
        await self._begin_owner()

    async def _begin_owner(self):
        self.stage = "role"
        self.state = self.ASK_OWNER
        self.conn.voice_onboarding = "role"
        name = self.nickname or "助手"
        await self._say(f"好的，我是{name}。{_ASK_OWNER_PROMPT}")

    async def _on_owner(self, text):
        title = await extract_owner_title(self.conn, text)
        if title is REPEAT:
            await self._repeat_prompt()
            return
        if title is SKIP:
            await self._skip_owner()
            return
        if not title:
            self._owner_misses += 1
            await self._say(self._owner_retry_hint())
            return
        self._owner_misses = 0
        self.owner_title = title
        self.state = self.CONFIRM_OWNER
        await self._say(f"我称呼您为{title}，对吗？")

    def _owner_retry_hint(self):
        index = min(self._owner_misses, len(_OWNER_RETRY_HINTS)) - 1
        return _OWNER_RETRY_HINTS[max(index, 0)]

    async def _skip_owner(self):
        """用户明确放弃设置时给一个出口：默认称呼走确认，仍可当场改口。

        owner_title 与性格模板一起在 /role 提交，后端拒绝空值，
        所以跳过也必须有称呼，不能留空跳到下一步。
        """
        self._owner_misses = 0
        self.owner_title = _OWNER_DEFAULT
        self.state = self.CONFIRM_OWNER
        await self._say(f"好的，先称呼您为{self.owner_title}。对吗？说确认继续，或者说新的称呼。")

    async def _on_confirm_owner(self, text):
        intent, title = await confirm_turn(self.conn, text, f"是不是称呼用户为{self.owner_title}")
        if intent == "repeat":
            await self._repeat_prompt()
            return
        if title and title != self.owner_title:
            self.owner_title = title
            await self._say(f"我称呼您为{self.owner_title}，对吗？")
            return
        if intent == "accept" or title == self.owner_title:
            await self._begin_persona()
            return
        if intent == "decline":
            self.owner_title = None
            self._owner_misses = 0
            self.state = self.ASK_OWNER
            await self._say(f"好的。{_ASK_OWNER_PROMPT}")
            return
        await self._say("请说确认，或者说重说。")

    async def _begin_persona(self):
        self.persona_name = None
        self.state = self.ASK_PERSONA
        if not await self._load_persona_templates():
            return
        await self._say(self._persona_menu())

    async def _load_persona_templates(self):
        try:
            payload = await self._post(
                "/device/voice-activation/role/templates",
                {"macAddress": self._mac()},
            )
        except Exception as exc:
            self.conn.logger.bind(tag=TAG).warning(f"读取性格模板失败: {exc}")
            await self._say(api_error_text(exc) + "。请再说一次。")
            return False
        names, voices = parse_persona_catalog(payload)
        if not names:
            await self._say("还没有可选的性格模板。请再说一次。")
            return False
        self.persona_templates = names
        self.persona_voices = voices
        return True

    def _persona_menu(self):
        return persona_menu(self.persona_templates)

    def _use_persona_voice(self, name):
        voice = (self.persona_voices or {}).get(name)
        if not apply_persona_voice(getattr(self.conn, "tts", None), voice):
            return
        self.conn.logger.bind(tag=TAG).info(f"性格模板 {name} 切换音色: {voice}")

    async def _on_persona(self, text):
        if not self.persona_templates:
            if not await self._load_persona_templates():
                return
        matched = await resolve_persona(self.conn, text, self.persona_templates)
        if matched is REPEAT:
            await self._repeat_prompt()
            return
        if matched:
            self.persona_name = matched
            self.state = self.CONFIRM_PERSONA
            self._use_persona_voice(matched)
            await self._say(f"性格用{matched}，对吗？")
            return
        await self._say("没有听清。请说序号，或者说模板名字。")

    async def _on_confirm_persona(self, text):
        listed = "、".join(self.persona_templates)
        intent, value = await confirm_turn(
            self.conn,
            text,
            f"性格是不是用{self.persona_name}。可选模板：{listed}",
        )
        if intent == "repeat":
            await self._repeat_prompt()
            return
        chosen = None
        for name in self.persona_templates:
            if value and (value == name or compact_text(value) == compact_text(name)):
                chosen = name
                break
        if chosen and chosen != self.persona_name:
            self.persona_name = chosen
            self._use_persona_voice(chosen)
            await self._say(f"性格用{chosen}，对吗？")
            return
        if intent == "accept" or chosen == self.persona_name:
            await self._ask_supplement()
            return
        if intent == "decline":
            await self._reask_persona()
            return
        await self._say("请说确认，或者说重说。")

    async def _reask_persona(self):
        self.persona_name = None
        self.state = self.ASK_PERSONA
        await self._say("好的。" + self._persona_menu())

    async def _ask_supplement(self):
        """补充常常不止一句。先说清结束方式：说完请说「没有了」。"""
        self.state = self.ASK_SUPPLEMENT
        self.supplement_parts = []
        self._supplement_nudges = 0
        await self._say(
            "还有要补充的吗？可以一句一句说，比如说话温柔一点、少讲道理。"
            "说完请说没有了。不想补充也说没有。"
        )
        self._schedule_supplement_wait()

    def _supplement_text(self):
        return "。".join(self.supplement_parts)

    def _add_supplement(self, content):
        """后端最多收八十个字，超了就不记这一句。"""
        if not content or len("。".join(self.supplement_parts + [content])) > _SUPPLEMENT_LIMIT:
            return False
        self.supplement_parts.append(content)
        self._supplement_nudges = 0
        return True

    def _invalidate_supplement_wait(self):
        self._supplement_epoch += 1
        task = self._supplement_wait_task
        self._supplement_wait_task = None
        if task is not None and not task.done():
            task.cancel()

    def _schedule_supplement_wait(self):
        self._invalidate_supplement_wait()
        epoch = self._supplement_epoch
        self._supplement_wait_task = asyncio.create_task(self._supplement_wait(epoch))

    def _voice_active(self):
        """麦克风这边还有人声。自己的提示音不算。"""
        if getattr(self.conn, "client_have_voice", False):
            return True
        last_voice_ms = getattr(self.conn, "vad_last_voice_time", 0) or 0
        return bool(last_voice_ms and time.time() * 1000 - last_voice_ms < 1000)

    async def _supplement_wait(self, epoch):
        """记下一句后等用户继续。安静够久没有新内容，就当作说完了。"""
        try:
            for _ in range(120):
                if epoch != self._supplement_epoch or self.state != self.ASK_SUPPLEMENT:
                    return
                if not self._playback_active() and not getattr(self.conn, "client_is_speaking", False):
                    break
                await asyncio.sleep(0.1)
            quiet_since = None
            while True:
                await asyncio.sleep(0.2)
                if epoch != self._supplement_epoch or self.state != self.ASK_SUPPLEMENT:
                    return
                if self._voice_active():
                    quiet_since = None
                    continue
                now = time.monotonic()
                if quiet_since is None:
                    quiet_since = now
                    continue
                if now - quiet_since >= SUPPLEMENT_PAUSE_SECONDS:
                    break
        except asyncio.CancelledError:
            return
        if epoch != self._supplement_epoch or self.state != self.ASK_SUPPLEMENT or self._giving_up:
            return
        self._supplement_wait_task = None
        await self._supplement_timeout()

    async def _supplement_timeout(self):
        """沉默也算说完。第一次先再问一遍，还是没回应就按已记下的提交。"""
        if self._voice_active():
            self._schedule_supplement_wait()
            return
        if self._supplement_nudges < 1:
            self._supplement_nudges += 1
            if self.supplement_parts:
                await self._say("还有要补充的吗？没有的话请说没有了。")
            else:
                await self._say("还有要补充的吗？直接说就行，没有的话请说没有。")
            self._schedule_supplement_wait()
            return
        self._invalidate_supplement_wait()
        await self._save_role(self._supplement_text())

    def _supplement_hint(self):
        if self.supplement_parts:
            return "没有听清。请继续补充，说完请说没有了。"
        return "没有听清。请说出要补充的设定，没有的话请说没有。"

    async def _on_supplement(self, text):
        self._invalidate_supplement_wait()
        action, content = await resolve_supplement_turn(self.conn, text, self._supplement_text())
        if action == "repeat":
            await self._repeat_prompt()
            self._schedule_supplement_wait()
            return
        if action == "end":
            await self._finish_supplement()
            return
        if action != "content" or not content:
            await self._say(self._supplement_hint())
            self._schedule_supplement_wait()
            return
        if not self._add_supplement(content):
            await self._say("补充已经到八十个字的上限了，先记这些。请说没有了，结束补充。")
            self._schedule_supplement_wait()
            return
        await self._say("好的，记下了。还有吗？说完请说没有了。")
        self._schedule_supplement_wait()

    async def _finish_supplement(self):
        """用户示意说完了。没记到内容就直接提交，记到了先复述确认。"""
        self._invalidate_supplement_wait()
        if not self.supplement_parts:
            await self._save_role("")
            return
        self.state = self.CONFIRM_SUPPLEMENT
        await self._say(f"记下的补充是：{self._supplement_text()}。对吗？")

    async def _on_confirm_supplement(self, text):
        intent, content = await resolve_supplement_confirm(self.conn, text, self._supplement_text())
        if intent == "repeat":
            await self._repeat_prompt()
            return
        if intent == "accept":
            await self._save_role(self._supplement_text())
            return
        if intent == "restart":
            self.supplement_parts = []
            self.state = self.ASK_SUPPLEMENT
            await self._say("好的，请重新说要补充的设定。说完请说没有了。")
            self._schedule_supplement_wait()
            return
        if intent == "more":
            self.state = self.ASK_SUPPLEMENT
            if not content:
                await self._say("好的，请继续补充。说完请说没有了。")
            elif not self._add_supplement(content):
                await self._say("补充已经到八十个字的上限了，先记这些。请说没有了，结束补充。")
            else:
                await self._say("好的，记下了。还有吗？说完请说没有了。")
            self._schedule_supplement_wait()
            return
        await self._say("请说确认，或者说不对重新补充。")

    async def _save_role(self, supplement):
        try:
            await self._post(
                "/device/voice-activation/role",
                {
                    "macAddress": self._mac(),
                    "ownerTitle": self.owner_title,
                    "templateName": self.persona_name,
                    "supplement": supplement,
                },
            )
        except Exception as exc:
            self.conn.logger.bind(tag=TAG).warning(f"保存角色设定失败: {exc}")
            await self._say(api_error_text(exc))
            return
        await self.conn.refresh_agent_prompt()
        await self._say("角色性格绑定成功。")
        await self._wait_spoken()
        self.conn.voice_onboarding = "skills"
        self.stage = "skills"
        self.include_auth = True
        await self._say("接下来绑定技能。")
        await self._wait_spoken()
        await self._bind_skills()

    def _skills_quiet(self):
        """用户已经跳过一次。唤醒后的自动重试失败不再出声。用户主动说绑定技能时要播报。"""
        if self._announce_skill_failure or getattr(self.conn, "speak_skill_failure", False):
            return False
        return getattr(self.conn, "voice_onboarding", None) == "skills_quiet"

    async def _speak_if_user_requests_skills(self, text):
        """静默重试进行中，用户又说了绑定技能：这次失败要说出来。"""
        if self.stage != "skills":
            return False
        request = await resolve_skills_request(self.conn, text)
        if not request:
            return False
        if getattr(self.conn, "voice_onboarding", None) != "skills_quiet":
            return False
        self.include_auth = True
        if request == "rebind":
            self.rebind_auth = True
        self._announce_skill_failure = True
        self.conn.speak_skill_failure = True
        self.conn.voice_onboarding = "skills"
        await self._say("好的，开始绑定技能。")
        if self._skill_bind_inflight:
            return True
        await self._bind_skills()
        return True

    async def _release_quiet_skills(self):
        """静默重试失败：留在聊天里，下次唤醒再试。"""
        self.conn.skills_pending = True
        self.conn.voice_onboarding = None
        self.conn.voice_activation_handler = None
        task = self._idle_task
        if task is not None and not task.done():
            task.cancel()

    async def _bind_skills(self):
        self._skill_bind_inflight = True
        try:
            await self._post(
                "/device/voice-activation/skills",
                {"macAddress": self._mac()},
            )
        except Exception as exc:
            self.conn.logger.bind(tag=TAG).warning(f"技能绑定失败: {exc}")
            if self._skills_quiet():
                await self._release_quiet_skills()
                return
            self.state = self.RETRY_SKILLS
            self.conn.skills_pending = True
            self.conn.voice_onboarding = "skills"
            await self._say(api_error_text(exc) + "。请说确认重试，或者说稍后再说。")
            return
        finally:
            self._skill_bind_inflight = False
        if not self.include_auth:
            await self._finish_general_skills()
            return
        self.conn.voice_onboarding = "skills"
        self.conn.skills_pending = True
        self.auth_queue = list(self.AUTH_QUEUE)
        self.auth_service = None
        self.auth_label = None
        self.auth_task_id = None
        self.auth_done = []
        self.auth_failed = []
        await self._say("通用技能已绑定。")
        await self._run_next_auth()

    async def _finish_general_skills(self):
        """通用绑定已完成，但这次不自动开通授权技能。"""
        try:
            await self._post(
                "/device/voice-activation/skills/complete",
                {"macAddress": self._mac()},
            )
        except Exception as exc:
            self.conn.logger.bind(tag=TAG).warning(f"结束通用技能绑定失败: {exc}")
        self.conn.voice_onboarding = None
        self.conn.skills_pending = False
        self.conn.voice_activation_handler = None
        task = self._idle_task
        if task is not None and not task.done():
            task.cancel()
        await self._say("通用技能已绑定。开通瑞幸或滴滴可以说绑定技能。")

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
            tip += "。要重新开通可以说重新绑定"
            parts.append(tip)
        if not parts:
            return "技能引导已结束。"
        return "。".join(parts) + "。"

    async def _run_next_auth(self):
        if not self.auth_queue:
            await self._finish_skills_and_reboot(self._auth_finish_prefix())
            return
        service, label = self.auth_queue.pop(0)
        self.auth_service = service
        self.auth_label = label
        self.auth_task_id = None
        try:
            result = await self._post(
                "/device/voice-activation/skills/auth",
                {
                    "macAddress": self._mac(),
                    "service": service,
                    "rebind": bool(self.rebind_auth),
                },
            )
        except Exception as exc:
            self.conn.logger.bind(tag=TAG).warning(f"{label}授权启动失败: {exc}")
            reason = api_error_text(exc)
            self._mark_auth_failed(label, reason)
            await self._say(f"{label}开通失败：{reason}。")
            await self._wait_spoken()
            await self._run_next_auth()
            return
        if str((result or {}).get("status") or "") == "already_bound":
            await self._say(f"{label}已经开通。")
            await self._wait_spoken()
            await self._run_next_auth()
            return
        await self._say(f"正在开通{label}。")
        await self._handle_auth_result(result or {})

    def _auth_code_rejected(self, result):
        message = str((result or {}).get("message") or "")
        return any(word in message for word in ("不正确", "重新输入", "验证码错误", "验证码有误"))

    async def _poll_auth(self, after_code=False):
        """登录往往还停在 waiting_code。提交验证码后要继续查，直到成功、失败或验证码被拒。"""
        label = self.auth_label or "技能"
        self._auth_polling = True
        wait_seconds = AUTH_CODE_WAIT_SECONDS if after_code else AUTH_POLL_WAIT_SECONDS
        rounds = max(1, int(wait_seconds / AUTH_POLL_INTERVAL_SECONDS))
        try:
            for _ in range(rounds):
                await asyncio.sleep(AUTH_POLL_INTERVAL_SECONDS)
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
                    await self._wait_spoken()
                    await self._run_next_auth()
                    return
                polled_status = str((polled or {}).get("status") or "")
                if polled_status == "pending":
                    continue
                if (
                    polled_status == "waiting_code"
                    and after_code
                    and not self._auth_code_rejected(polled)
                ):
                    continue
                await self._handle_auth_result(polled or {}, after_code=after_code)
                return
        finally:
            self._auth_polling = False

        # 超时：先把失败播报完，再切下一项，否则「正在开通滴滴出行」会盖掉这句。
        await self._cancel_auth_task()
        self._mark_auth_failed(label, "等待超时")
        await self._say(f"{label}开通失败，没有收到授权结果。")
        await self._wait_spoken()
        await self._run_next_auth()

    async def _wait_spoken(self):
        """等这一句开始播完，避免下一句把「已开通」盖掉。"""
        if getattr(self.conn, "tts", None) is None and not getattr(self.conn, "client_is_speaking", False):
            return
        for _ in range(20):
            if self._playback_active() or getattr(self.conn, "client_is_speaking", False):
                break
            await asyncio.sleep(0.05)
        for _ in range(80):
            if not self._playback_active() and not getattr(self.conn, "client_is_speaking", False):
                return
            await asyncio.sleep(0.1)

    async def _handle_auth_result(self, result, after_code=False):
        status = str((result or {}).get("status") or "")
        task_id = (result or {}).get("taskId")
        message = (result or {}).get("message") or ""
        label = self.auth_label or "技能"
        if task_id:
            self.auth_task_id = task_id

        if status == "waiting_code":
            if after_code and not self._auth_code_rejected(result):
                await self._poll_auth(after_code=True)
                return
            self.state = self.ASK_AUTH_CODE
            self.conn.last_activity_time = time.time() * 1000
            if after_code:
                await self._say("验证码不正确或尚未生效，请再说一遍。")
            else:
                await self._say(f"请说出{label}短信里的验证码。")
            return

        if status == "done":
            if label not in self.auth_done:
                self.auth_done.append(label)
            await self._say(f"{label}已开通。")
            await self._wait_spoken()
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
            # 等这句播完再开下一项，否则失败提示会被「正在开通…」盖掉。
            await self._wait_spoken()
            await self._run_next_auth()
            return

        await self._poll_auth(after_code=after_code)

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
        action, code = await resolve_auth_reply(self.conn, text)
        if action == "repeat":
            await self._repeat_prompt()
            return
        if action == "rebind":
            self.rebind_auth = True
            await self._cancel_auth_task()
            self.auth_queue = list(self.AUTH_QUEUE)
            await self._say("好的，重新绑定授权技能。")
            await self._wait_spoken()
            await self._run_next_auth()
            return
        if action == "skip":
            await self._cancel_auth_task()
            await self._say(f"好的，已跳过{label}。")
            await self._wait_spoken()
            await self._run_next_auth()
            return
        if action == "resend":
            await self._say("验证码由服务方发送，请留意短信。收到后直接说出验证码。")
            return
        if action != "code" or not code:
            await self._say("没有听清验证码，请再说一遍。")
            return
        if not self.auth_task_id:
            self._mark_auth_failed(label, "任务已失效")
            await self._say(f"{label}开通失败：任务已失效。")
            await self._wait_spoken()
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
            await self._say(api_error_text(exc) + "。请再说一遍验证码。")
            return
        status = str((result or {}).get("status") or "")
        if status == "done":
            await self._handle_auth_result(result or {})
            return
        if status == "waiting_code" and self._auth_code_rejected(result):
            self.state = self.ASK_AUTH_CODE
            await self._say("验证码不正确或尚未生效，请再说一遍。")
            return
        if status in ("failed", "skipped"):
            await self._handle_auth_result(result or {})
            return
        # 验证码已收下。登录完成前状态常常仍是 waiting_code，不能再把要验证码的那句说一遍。
        await self._say("验证码已提交，正在登录。")
        await self._handle_auth_result(result or {}, after_code=True)

    async def _finish_skills_and_reboot(self, prefix=""):
        """配置完毕不再询问：不重启设备 MCP 工具无法加载，播报后直接重启。"""
        try:
            await self._post(
                "/device/voice-activation/skills/complete",
                {"macAddress": self._mac()},
            )
        except Exception as exc:
            self.conn.logger.bind(tag=TAG).warning(f"完成技能引导失败: {exc}")
            # complete 失败也要继续重启，否则配置不生效、引导卡住
        self.conn.voice_onboarding = None
        self.conn.skills_pending = False
        self.state = self.REBOOTING
        summary = (prefix or "").strip() or "技能引导已结束。"
        if not summary.endswith("。"):
            summary += "。"
        await self._say(summary + "配置完毕，设备即将重启。")
        await self._reboot_device()

    async def _on_retry_skills(self, text):
        if self._skills_quiet():
            return
        intent, rebind = await resolve_skill_retry(self.conn, text)
        if intent == "repeat":
            await self._repeat_prompt()
            return
        if intent == "skip":
            self.conn.voice_activation_handler = None
            # 记下已经跳过。下次唤醒只重试通用绑定，授权技能要用户再说「绑定技能」。
            self.conn.voice_onboarding = None
            self.conn.skills_pending = True
            try:
                await self._post(
                    "/device/voice-activation/skills/defer",
                    {"macAddress": self._mac()},
                )
            except Exception as exc:
                self.conn.logger.bind(tag=TAG).warning(f"记录跳过技能绑定失败: {exc}")
            await self._say("好的。之后想绑定技能，可以说绑定技能。现在可以开始聊天了。")
            return
        if intent == "retry":
            if rebind:
                self.rebind_auth = True
            await self._bind_skills()
            return
        await self._say("请说确认重试，或者说稍后再说。")

    async def _reboot_device(self):
        # 等「配置完毕，设备即将重启」播完再下发，别把提示音掐掉。
        await self._wait_spoken()
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
            await self._say("自动重启没成功，请手动重启设备，否则技能用不了。")
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
                await self._ask_for_phone(f"还差{spoken_count(missing)}位，请继续说。")
            else:
                await self._ask_for_phone("请按顺序说出十一位手机号。")
        elif self.state == self.CONFIRM_PHONE:
            await self._say(f"您的手机号是，{spoken_digits(self.phone or '')}。对吗？")
        elif self.state == self.ASK_CODE:
            await self._say("请说出短信验证码。")
        elif self.state == self.ASK_NICKNAME:
            await self._say(_ASK_NICKNAME_PROMPT)
        elif self.state == self.CONFIRM_NICKNAME:
            await self._say(f"助手的昵称是{self.nickname}，对吗？")
        elif self.state == self.ASK_OWNER:
            await self._say(_ASK_OWNER_PROMPT)
        elif self.state == self.CONFIRM_OWNER:
            await self._say(f"我称呼您为{self.owner_title}，对吗？")
        elif self.state == self.ASK_PERSONA:
            if self.persona_templates:
                await self._say(self._persona_menu())
            else:
                await self._say("请说出性格模板的序号或名字。")
        elif self.state == self.CONFIRM_PERSONA:
            await self._say(f"性格用{self.persona_name}，对吗？")
        elif self.state == self.ASK_SUPPLEMENT:
            if self.supplement_parts:
                await self._say(
                    f"已经记下的补充是：{self._supplement_text()}。还有要补充的吗？说完请说没有了。"
                )
            else:
                await self._say("还有要补充的吗？可以一句一句说。说完请说没有了，不想补充也说没有。")
        elif self.state == self.CONFIRM_SUPPLEMENT:
            await self._say(f"记下的补充是：{self._supplement_text()}。对吗？")
        elif self.state == self.RETRY_SKILLS:
            if self._skills_quiet():
                return
            await self._say("技能绑定还未完成，请说确认重试，或者说稍后再说。")
        elif self.state == self.ASK_AUTH_CODE:
            label = self.auth_label or "技能"
            await self._say(f"请说出{label}短信验证码。")

    async def _send_recognized_text(self, text):
        """把识别文本回给设备。不发 tts start，避免还在收号时设备退出录音。"""
        from core.handle.sendAudioHandle import send_display_message
        from core.utils import textUtils

        display = textUtils.get_string_no_punctuation_or_emoji(text) or text
        try:
            await send_display_message(self.conn, display)
        except Exception as exc:
            self.conn.logger.bind(tag=TAG).warning(f"回传识别文本失败: {exc}")

    def _start_idle_watch(self):
        task = self._idle_task
        if task is not None and not task.done():
            return
        self._idle_task = asyncio.create_task(self._watch_idle_then_sleep())

    def _idle_limit_seconds(self):
        config = getattr(self.conn, "config", None) or {}
        try:
            base = int(config.get("close_connection_no_voice_time", 120))
        except (TypeError, ValueError):
            base = 120
        if base <= 0:
            base = 120
        if self.state == self.ASK_CODE:
            return max(base, 300)
        return base

    async def _watch_idle_then_sleep(self):
        """用户没说话才计时。我们自己的提示音不算。到点断开识别并让设备休眠。"""
        accumulated = 0.0
        last = time.monotonic()
        try:
            while True:
                await asyncio.sleep(0.5)
                if self._giving_up or getattr(self.conn, "voice_activation_handler", None) is not self:
                    return
                now = time.monotonic()
                delta = now - last
                last = now
                if self._auth_polling:
                    accumulated = 0.0
                    continue
                if self._playback_active() or getattr(self.conn, "client_is_speaking", False):
                    continue
                last_voice_ms = getattr(self.conn, "vad_last_voice_time", 0) or 0
                if last_voice_ms and time.time() * 1000 - last_voice_ms < 1000:
                    accumulated = 0.0
                    continue
                accumulated += delta
                if accumulated >= self._idle_limit_seconds():
                    await self.give_up_and_sleep()
                    return
        except asyncio.CancelledError:
            return

    async def give_up_and_sleep(self):
        """长时间没人说话：停掉计费识别，说一声，再断开连接。

        设备回到空闲后会自己休眠。下次唤醒会重连，语音激活按当前进度重新开始。
        """
        if self._giving_up:
            return
        self._giving_up = True
        self.conn.close_after_chat = True
        self._invalidate_phone_wait()
        self._invalidate_supplement_wait()
        ready = self._asr_ready_task
        if ready is not None and not ready.done():
            ready.cancel()
        asr = getattr(self.conn, "asr", None)
        stop = getattr(asr, "disconnect", None)
        if stop is None:
            stop = getattr(asr, "stop_ws_connection", None)
        if stop is not None:
            stop()
        self.conn.logger.bind(tag=TAG).info("语音激活长时间无回应，断开ASR并让设备休眠")
        await self._say("没有听到回应，我先休息了。唤醒我之后，我们再继续。")

    async def _ask_for_phone(self, text):
        """先播提示，等提示音快说完再连 ASR。不在整段提示音期间占着计费连接。"""
        if self._giving_up:
            return
        await self._say(text)
        self._schedule_asr_ready()

    def _schedule_asr_ready(self):
        task = self._asr_ready_task
        self._asr_ready_task = None
        if task is not None and not task.done():
            task.cancel()
        self._asr_ready_task = asyncio.create_task(self._connect_when_prompt_ending())

    async def _connect_when_prompt_ending(self):
        try:
            await self._wait_until_prompt_tail()
            if self._giving_up:
                return
            await self._connect_asr_before_prompt()
            while self._playback_active():
                await asyncio.sleep(0.05)
            asr = getattr(self.conn, "asr", None)
            finish = getattr(asr, "finish_prompt_guard", None)
            if finish is not None:
                finish()
        except asyncio.CancelledError:
            return

    async def _wait_until_prompt_tail(self):
        """等语音真正开始播放，并且服务端只剩大约 1 秒时再握手。

        设备端还有播放缓冲，这 1 秒够连上，又不会把整段提示音的时长算进识别费用。
        """
        saw_audio = False
        for _ in range(200):
            controller = getattr(self.conn, "audio_rate_controller", None)
            queued_ms = None
            if controller is not None and (len(controller.queue) > 0 or controller.play_position > 0):
                saw_audio = True
                queued_ms = len(controller.queue) * controller.interval_ms
            if saw_audio and not self._tts_still_generating() and queued_ms is not None and queued_ms <= 1000:
                return
            if saw_audio and not self._playback_active():
                return
            if not saw_audio and not self._playback_active() and not getattr(self.conn, "client_is_speaking", False):
                return
            await asyncio.sleep(0.05)

    def _tts_still_generating(self):
        tts = getattr(self.conn, "tts", None)
        if tts is None:
            return False
        return not tts.tts_text_queue.empty() or not tts.tts_audio_queue.empty()

    async def _connect_asr_before_prompt(self):
        asr = getattr(self.conn, "asr", None)
        ensure = getattr(asr, "ensure_connected", None)
        if ensure is None:
            return
        try:
            await ensure(self.conn)
        except Exception as exc:
            logger = getattr(self.conn, "logger", None)
            if logger is not None:
                logger.bind(tag=TAG).warning(f"询问手机号前连接ASR失败: {exc}")

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

    async def _post(self, path, payload):
        from config.manage_api_client import ManageApiClient

        if ManageApiClient._instance is None:
            raise Exception("API返回错误: 语音服务未连接管理后台")
        return await ManageApiClient._instance._execute_async_request(
            "POST", path, json=payload
        )
