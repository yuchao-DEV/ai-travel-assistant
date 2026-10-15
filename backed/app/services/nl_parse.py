"""自然语言需求解析：把一句人话变成结构化的规划参数。

**严格边界（与 polish.py 同样的纪律）**：本模块只做「抽取 + 校验」，
不做任何规划决策——不选点、不定顺序、不算时间与费用。
LLM 在这里扮演的角色是"把自然语言翻译成 PlanRequestDTO"，
翻译完就交还给算法引擎。

三道防线保证 LLM 不能乱来：
1. **白名单**：tags 只能从 VALID_TAGS 取，profile 只能从 VALID_PROFILES 取；
2. **范围夹取**：天数夹到 `MIN_TRIP_DAYS`–`MAX_TRIP_DAYS`（默认 1–30，均由
   `config` 给出，不再硬编码），预算不低于 100，且把夹取动作回报给用户；
3. **本地规则兜底**：LLM 不可用或输出无法解析时，退回正则 + 城市表的
   确定性解析，弱网/断网也能用（与项目的降级哲学一致）。
"""
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from app.algorithms.data_loader import load_cities
from app.config import MAX_TRIP_DAYS, MIN_TRIP_DAYS
from app.errors import ApiError
from app.llm import chat
from app.schemas import (
    ParsedPlanVO,
    ParseRequestDTO,
    PlanRequestDTO,
    VALID_PROFILES,
    VALID_TAGS,
)
from app.services.llm_json import extract_json

logger = logging.getLogger("app.nl_parse")

DEFAULT_DAYS = 3
DEFAULT_PROFILE = "经典"   # 三档改名后「舒适型」→「经典」
# 天数上限统一取自 config，避免"后端 7 天 / 前端 7 天 / 旧接口 30 天"这种不一致
MIN_DAYS, MAX_DAYS = MIN_TRIP_DAYS, MAX_TRIP_DAYS
MAX_TAGS = 6
MAX_PLACES = 5          # must_go / avoid 各自的条数上限
PLACE_MIN_LEN = 2
PLACE_MAX_LEN = 20

# 字段中文名，用于给前端展示"识别到哪些 / 哪些是默认的"
FIELD_LABELS = {
    "city": "目的地",
    "days": "天数",
    "tags": "兴趣偏好",
    "profile": "出行节奏",
    "must_go": "必去景点",
    "avoid": "避开景点",
}

# ---------------------------------------------------------------- 规则解析

# 中文数字 → 阿拉伯数字（只覆盖天数这种小数字场景）
_CN_NUM = {"一": 1, "两": 2, "二": 2, "三": 3, "四": 4, "五": 5,
           "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}

# 兴趣口语词 → 官方 tag
_TAG_KEYWORDS: Dict[str, List[str]] = {
    "历史": ["历史", "古迹", "古都", "文化", "博物馆", "文物", "遗址", "民国", "古镇"],
    "自然": ["自然", "风景", "山水", "看海", "看湖", "爬山", "公园", "户外", "湿地", "徒步"],
    "美食": ["美食", "小吃", "餐厅", "夜市", "特色菜", "探店", "吃"],
    "购物": ["购物", "逛街", "商场", "免税", "步行街", "买点"],
    "艺术": ["艺术", "美术", "展览", "剧院", "演出", "音乐会", "设计"],
    "夜生活": ["夜生活", "酒吧", "夜游", "夜景", "深夜"],
}

# 出行节奏口语词 → profile
_PROFILE_KEYWORDS: Dict[str, List[str]] = {
    "尽览": ["尽览", "紧凑", "效率", "尽量多", "行程满", "特种兵", "多逛几个"],
    "悠闲": ["悠闲", "轻松", "不累", "慢节奏", "带爸妈", "带孩子", "亲子",
             "老人", "休闲", "别太累"],
    "经典": ["经典", "口碑", "高评分", "值得去", "必看", "舒适"],
}

# 「省钱」类词不再映射到任何 profile —— 预算系统移除后按预算选点已不存在。
# 但**不能静默忽略**：用户说了想省钱，要如实告诉他当前版本不按预算规划。
_BUDGET_WORDS = ("省钱", "穷游", "便宜", "实惠", "性价比", "预算紧", "预算有限")

_PLACE_RE = r"[\u4e00-\u9fa5A-Za-z0-9·]{2,20}"
# 景点名之间的连接词（"必去总统府和中山陵" 要拆成两个）
_PLACE_SPLIT_RE = re.compile(r"以及|[和与及、,，]")


def _split_places(raw_places: List[str]) -> List[str]:
    """把"总统府和中山陵"这类连写串拆成多个景点名。"""
    places: List[str] = []
    for item in raw_places:
        for part in _PLACE_SPLIT_RE.split(item):
            part = part.strip()
            if part:
                places.append(part)
    return places


def _rule_parse(text: str) -> Dict[str, Any]:
    """纯规则抽取（LLM 不可用时的兜底，结果确定可复现）。"""
    result: Dict[str, Any] = {}

    # 目的地：匹配城市表里的名字，长名优先避免短名误配
    for name in sorted(load_cities().keys(), key=len, reverse=True):
        if name in text:
            result["city"] = name
            break

    # 天数：3天 / 三天 / 玩3日
    match = re.search(r"(\d+)\s*[天日]", text)
    if match:
        result["days"] = int(match.group(1))
    else:
        match = re.search(r"([一两二三四五六七八九十])\s*[天日]", text)
        if match:
            result["days"] = _CN_NUM.get(match.group(1))

    # 预算：2000元 / 预算2000 / 一千五
    match = re.search(r"(\d+(?:\.\d+)?)\s*(?:元|块|人民币|RMB|rmb)", text)
    if match:
        result["budget"] = float(match.group(1))
    else:
        match = re.search(r"预算\s*(\d+(?:\.\d+)?)", text)
        if match:
            result["budget"] = float(match.group(1))

    # 兴趣标签
    tags = [tag for tag in VALID_TAGS
            if any(kw in text for kw in _TAG_KEYWORDS.get(tag, []))]
    if tags:
        result["tags"] = tags

    # 出行节奏
    for profile in ("尽览", "悠闲", "经典"):
        if any(kw in text for kw in _PROFILE_KEYWORDS[profile]):
            result["profile"] = profile
            break

    # 必去 / 避开
    must_go = _split_places(re.findall(r"必去(?:了)?\s*(" + _PLACE_RE + ")", text))
    if must_go:
        result["must_go"] = must_go
    avoid = _split_places(re.findall(r"(?:不要去|避开|不想去|排除)\s*(" + _PLACE_RE + ")", text))
    if avoid:
        result["avoid"] = avoid

    return result


# ---------------------------------------------------------------- LLM 抽取

_SYSTEM_PROMPT = (
    "你是一个旅游需求结构化抽取器。你只负责从用户的话里抽取字段，"
    "绝不能编造用户没提到的信息，也不要给出任何行程建议或景点推荐。"
)


def _build_prompt(text: str) -> str:
    return (
        "从下面这段话里抽取旅行规划参数，输出严格 JSON。\n\n"
        f"用户的话：{text}\n\n"
        "字段规则（没提到的字段一律填 null 或空数组，不要猜）：\n"
        "- city: 目的地城市名，只写城市名（如「南京」）\n"
        f"- days: 旅行天数，整数 {MIN_DAYS}-{MAX_DAYS}\n"
            f"- tags: 兴趣标签数组，只能从 {VALID_TAGS} 里选\n"
        f"- profile: 出行节奏，只能从 {VALID_PROFILES} 里选\n"
        "- must_go: 用户明确说必去的景点名数组\n"
        "- avoid: 用户明确说要避开的景点名数组\n\n"
        "只返回 JSON，不要任何解释、不要代码块标记：\n"
        '{"city": null, "days": null, "tags": [], '
        '"profile": null, "must_go": [], "avoid": []}'
    )


async def _extract(text: str) -> Tuple[Dict[str, Any], str, List[str]]:
    """抽取字段，返回 (raw_fields, source, notes)。

    抽取是**结构化任务**，不是创作：温度压到 0.1，让同一句话说两次得到
    基本一致的结果（可解释性与可复现性都依赖这一点）。
    """
    try:
        response = await chat(_SYSTEM_PROMPT, _build_prompt(text), temperature=0.1)
    except Exception as exc:
        logger.warning("LLM 解析失败，退回规则解析：%s", exc)
        return _rule_parse(text), "rule", ["大模型暂时不可用，已用本地规则解析，请核对表单"]

    data = extract_json(response)
    if data is None:
        logger.warning("LLM 返回无法解析为 JSON，退回规则解析")
        return _rule_parse(text), "rule", ["大模型返回内容无法解析，已用本地规则解析，请核对表单"]

    # LLM 与规则结果取并集，LLM 优先：规则能补上 LLM 漏掉的城市/天数等显式信息
    rule = _rule_parse(text)
    for key, value in rule.items():
        if not data.get(key):
            data[key] = value
    return data, "llm", []


# ---------------------------------------------------------------- 字段清洗

def _clean_city(value: Any) -> Optional[str]:
    """城市名清洗：只接受 2-10 个中文/字母/间隔号组成的短名称。"""
    if not isinstance(value, str):
        return None
    city = value.strip()
    # 「北京市」→「北京」，但不动「大理」这类本身以市结尾的地名之外的情况
    if len(city) > 2 and city.endswith("市"):
        city = city[:-1]
    if not city or len(city) > 10:
        return None
    if not re.fullmatch(r"[\u4e00-\u9fa5A-Za-z·]+", city):
        return None
    return city


def _coerce_days(value: Any) -> Tuple[Optional[int], List[str]]:
    """天数：越界夹到 MIN_TRIP_DAYS-MAX_TRIP_DAYS 并把动作回报给用户。"""
    if value is None:
        return None, []
    try:
        days = int(float(value))
    except (TypeError, ValueError):
        return None, []
    if days < MIN_DAYS:
        return MIN_DAYS, [f"天数「{value}」小于 {MIN_DAYS} 天，已按 {MIN_DAYS} 天处理"]
    if days > MAX_DAYS:
        return MAX_DAYS, [f"天数「{value}」超出上限，已按 {MAX_DAYS} 天处理"]
    return days, []



def _clean_tags(value: Any) -> List[str]:
    """标签白名单过滤 + 去重 + 截断。"""
    if not isinstance(value, (list, tuple)):
        return []
    seen: List[str] = []
    for item in value:
        if isinstance(item, str):
            tag = item.strip()
            if tag in VALID_TAGS and tag not in seen:
                seen.append(tag)
    return seen[:MAX_TAGS]


def _clean_profile(value: Any) -> Optional[str]:
    """偏好白名单过滤。"""
    if isinstance(value, str) and value.strip() in VALID_PROFILES:
        return value.strip()
    return None


def _clean_places(value: Any) -> List[str]:
    """景点名列表清洗：过滤空串/超长/异常字符，去重并截断。"""
    if not isinstance(value, (list, tuple)):
        return []
    places: List[str] = []
    for item in value:
        if not isinstance(item, str):
            continue
        name = item.strip()
        if PLACE_MIN_LEN <= len(name) <= PLACE_MAX_LEN and name not in places:
            places.append(name)
    return places[:MAX_PLACES]


# ---------------------------------------------------------------- 对外入口

async def parse_request(dto: ParseRequestDTO) -> ParsedPlanVO:
    """把自然语言需求解析成 PlanRequestDTO。"""
    text = (dto.text or "").strip()
    if not text:
        raise ApiError("请先描述一下你的旅行需求")

    defaults = dto.defaults
    raw, source, notes = await _extract(text)

    inferred: List[str] = []
    assumed: List[str] = []

    # ---- 目的地 ----
    city = _clean_city(raw.get("city"))
    if city:
        inferred.append(FIELD_LABELS["city"])
    else:
        city = (defaults.city if defaults else "") or ""
        assumed.append(FIELD_LABELS["city"])
        notes.append("没有识别出目的地城市，请手动选择")

    # ---- 天数 ----
    days, day_notes = _coerce_days(raw.get("days"))
    notes.extend(day_notes)
    if days is None:
        days = defaults.days if defaults else DEFAULT_DAYS
        assumed.append(FIELD_LABELS["days"])
    else:
        inferred.append(FIELD_LABELS["days"])

    # ---- 预算 ----
    # 已删除：预算系统整体移除（见《移除预算系统与重构偏好体系-执行规格》）。
    # 用户口述"预算 3000"时不再抽取，也不会进入请求。若需要提示用户，
    # 应走 notes（"当前版本不再按预算选点"），而不是静默忽略。

    # ---- 兴趣标签 ----
    tags = _clean_tags(raw.get("tags"))
    if tags:
        inferred.append(FIELD_LABELS["tags"])
    elif defaults and defaults.tags:
        tags = list(defaults.tags)
        assumed.append(FIELD_LABELS["tags"])
    else:
        assumed.append(FIELD_LABELS["tags"])

    # ---- 出行节奏 ----
    profile = _clean_profile(raw.get("profile"))
    if profile:
        inferred.append(FIELD_LABELS["profile"])
    else:
        profile = (defaults.profile if defaults and defaults.profile else DEFAULT_PROFILE)
        assumed.append(FIELD_LABELS["profile"])

    # ---- 必去 / 避开 ----
    must_go = _clean_places(raw.get("must_go"))
    if must_go:
        inferred.append(FIELD_LABELS["must_go"])
    elif defaults:
        must_go = list(defaults.must_go)

    avoid = _clean_places(raw.get("avoid"))
    if avoid:
        inferred.append(FIELD_LABELS["avoid"])
    elif defaults:
        avoid = list(defaults.avoid)

    request = PlanRequestDTO(
        city=city,
        days=days,
            tags=tags,
        must_go=must_go,
        avoid=avoid,
        profile=profile,
    )

    logger.info(
        "自然语言解析完成 source=%s 识别=%s 兜底=%s", source, inferred, assumed
    )
    return ParsedPlanVO(
        request=request,
        inferred=inferred,
        assumed=assumed,
        notes=notes,
        source=source,
        raw_text=text,
    )
