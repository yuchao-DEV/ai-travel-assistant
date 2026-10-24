"""导入线路/地点：把一段文字里的地点变成可加入行程的必去项。

**关键设计：每个地点都必须经过高德反查验证。**

大模型能从攻略文字里认出「老门东、先锋书店、颐和路」这类名字，但它无法保证这些地方
真实存在、坐标在哪。所以流程是：

    文字 →（LLM 抽取地点名）→ 逐个走高德 place/text 反查 → 真实 POI
                                                      ↘ 反查不到 → **明确列出**，绝不静默丢弃

这与本项目一直坚持的纪律一致：**界面声称的必须等于系统实际做的**。
导入功能最常见的坑就是"显示导入成功，实际一半地点没匹配上"，用户完全看不出来。

抽取环节沿用 ``/api/parse`` 的两道防线：LLM 抽取 + 规则兜底；
LLM 不可用时退回分隔符切分，并在 notes 里如实告知。
"""
import asyncio
import json
from pathlib import Path
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from app.amap.client import geocode, search_pois
from app.algorithms.candidate_selector import match_score
from app.algorithms.data_loader import load_cities
from app.config import CACHE_DIR
from app.llm import chat
from app.services.llm_json import extract_json

logger = logging.getLogger("app.importer")

# 一次最多导入多少个地点（避免一句长文抽出几十个点把行程塞爆）
MAX_PLACES = 12
# 地点名长度范围（中文地名极少短于 2 字或长于 20 字）
MIN_NAME_LEN, MAX_NAME_LEN = 2, 20
# 反查并发数（与灵感流一致，避免触发高德 QPS 限制）
LOOKUP_CONCURRENCY = 3

_SYSTEM_PROMPT = (
    "你是一个旅游地点抽取器。你只负责从用户给的文字里找出被提到的**具体地点名**，"
    "绝不能编造文字里没有出现的地点，也不要给出任何建议或推荐。"
)


def _build_prompt(text: str) -> str:
    """注意：**不要**用 str.format —— 提示词末尾的 JSON 字面量大括号会被当成占位符，
    实测会抛 KeyError: 'city'，导致每次都退回规则抽取。"""
    return (
        "从下面的文字里找出所有被提到的**具体地点**（景点、街区、博物馆、公园、餐厅、地标等）。\n\n"
        f"文字：\n{text}\n\n"
        "规则：\n"
        "- 只抽取文字里**明确出现过的**地点名，不要补充、不要联想、不要推荐\n"
        "- 每个地点名尽量与文字中一致，写完整名称\n"
        "- 不要把城市名、省份名、酒店品牌、交通枢纽当成地点\n"
        f"- 最多 {MAX_PLACES} 个\n\n"
        "只返回 JSON，不要任何解释、不要代码块标记：\n"
        '{"city": null, "places": []}'
    )

# 规则兜底用的分隔符：攻略里常见 "→"、"、""。"、"D1:" 这类写法。
# 句子级标点必须在内，否则"然后逛苏州博物馆。中午在平江路吃午饭。"会连成一段而无法识别。
_SPLIT_RE = re.compile(
    r"[、,，;；。．.！!？?~～\n\r\|/]+|->|→|—|·|(?:\bD\d\b[:：]?)|(?:\d+\s*[.、])"
)
# 规则兜底时剥掉的时间/动作前后缀。
# 实测不剥会出现"上午去拙政园""早上去虎丘"这类片段，反查自然全部失败。
_PREFIX_RE = re.compile(
    r"^(?:第[一二三四五六七八九十\d]+天|[Dd]\d|"
    r"上午|下午|晚上|早上|中午|傍晚|夜里|凌晨|"
    r"然后|接着|随后|再去|先去|最后|可以|建议|"
    r"去|到|逛|游|玩|吃|看|赏|打卡|前往|返回|住在|入住|途经|参观|在)+"
)
_SUFFIX_RE = re.compile(
    r"(?:吃午饭|吃晚饭|吃早餐|吃个饭|看夜景|看日出|看日落|打卡|拍照|逛逛|转转|"
    r"游玩|游览|参观|附近|一带|周边|然后|之后|出发|集合)+$"
)
# 中英文句读/空白，剥前后缀时一并去掉（否掉"苏州博物馆。"这种尾巴）
_TRIM_CHARS = " 　\t:：-·。．.！!？?；;，,、~～"
# 明显是标题/行程概述而非地点的片段
_TITLE_HINTS = ("攻略", "游记", "行程", "安排", "日游", "两日", "三日", "自由行", "路线")
# 明显不是地点名的词（城市/省份/交通/时间等），规则兜底时排除
_STOPWORDS = {
    "上午", "下午", "晚上", "中午", "早上", "傍晚", "第一天", "第二天", "第三天",
    "早餐", "午餐", "晚餐", "午饭", "晚饭", "住宿", "酒店", "民宿", "入住", "退房",
    "行程", "攻略", "打卡", "推荐", "必去", "免费", "门票", "交通", "地铁", "公交",
    "打车", "步行", "飞机", "高铁", "火车", "机场", "车站", "预算", "人均",
}


def _strip_affixes(name: str) -> str:
    """反复剥掉"上午去/xx/看夜景。"这类前后缀，得到干净的地点名。"""
    prev = None
    cur = (name or "").strip(_TRIM_CHARS)
    while prev != cur:
        prev = cur
        cur = _PREFIX_RE.sub("", cur).strip(_TRIM_CHARS)
        cur = _SUFFIX_RE.sub("", cur).strip(_TRIM_CHARS)
    return cur


def _looks_like_place(name: str) -> bool:
    """粗筛：长度合适、纯中文/字母/间隔号、且不是明显的非地点词或标题。"""
    name = (name or "").strip(" 　\t")
    if not (MIN_NAME_LEN <= len(name) <= MAX_NAME_LEN):
        return False
    if name in _STOPWORDS:
        return False
    if any(h in name for h in _TITLE_HINTS):
        return False
    if any(w in name for w in ("酒店", "民宿", "客栈")):
        return False
    return bool(re.fullmatch(r"[\u4e00-\u9fa5A-Za-z0-9·\-（）()]+", name))


def _dedup(names: List[str]) -> List[str]:
    """去重且保序。"""
    seen = set()
    out = []
    for n in names:
        key = n.strip()
        if key and key not in seen:
            seen.add(key)
            out.append(key)
    return out


def _rule_extract(text: str) -> List[str]:
    """规则兜底：按常见分隔符切分，保留像地名的片段。

    准确率远不如大模型，但它是**确定性且离线可用**的降级路径——
    比"大模型挂了就什么都不返回"要好。
    """
    parts = _SPLIT_RE.split(text or "")
    names = [_strip_affixes(p) for p in parts]
    return _dedup([n for n in names if _looks_like_place(n)])[:MAX_PLACES]


def _guess_city(text: str) -> Optional[str]:
    """从文字里找已知城市名（本地城市表，避免凭空猜城市）。

    本地表 = cities.json 的 12 个种子城市 + 用过高德解析的城市（自扩展）。
    所以"第一次提到某城市"可能识别不到，但该城市一旦被规划或导入过一次，
    就会记进本地表，之后都能识别——不需要维护一份几百城的静态清单。
    """
    cities = load_cities()
    for name in sorted(cities.keys(), key=len, reverse=True):
        if name and name in (text or ""):
            return name
    return None


async def extract_places(text: str) -> Tuple[Optional[str], List[str], str, List[str]]:
    """抽取地点名，返回 (city, places, source, notes)。"""
    text = (text or "").strip()
    if not text:
        return None, [], "rule", ["内容为空"]

    notes: List[str] = []
    data = None
    source = "llm"
    try:
        # 与 nl_parse 同理：地点抽取是结构化任务，温度压到 0.1 以减少发散
        response = await chat(_SYSTEM_PROMPT, _build_prompt(text[:4000]), temperature=0.1)
        data = extract_json(response)
        if data is None:
            raise ValueError("LLM 返回无法解析为 JSON")
    except Exception as exc:  # 大模型不可用 → 规则兜底，并如实告知
        logger.warning("地点抽取失败，退回规则切分：%s", exc)
        source = "rule"
        notes.append("大模型暂时不可用，已用分隔符切分地点，请核对结果")

    city = None
    places: List[str] = []
    if isinstance(data, dict):
        raw_city = data.get("city")
        if isinstance(raw_city, str):
            city = raw_city.strip() or None
        raw_places = data.get("places")
        if isinstance(raw_places, list):
            places = [p.strip() for p in raw_places if isinstance(p, str) and p.strip()]

    # 规则兜底：LLM 没给地点时用切分结果补上（城市同理）
    if not places:
        rule_places = _rule_extract(text)
        if rule_places:
            places = rule_places
            if source == "llm":
                notes.append("大模型未返回地点，已用分隔符切分，请核对结果")
    if not city:
        city = _guess_city(text)

    places = _dedup([p for p in places if _looks_like_place(p)])[:MAX_PLACES]
    return city, places, source, notes


# 明显属于"附属设施"而非游览点的词。命中即拒绝，除非名字与查询完全相等。
_NOT_VISITABLE_WORDS = (
    "通道", "出入口", "停车场", "售票处", "售票厅", "检票口",
    "公交站", "公交车站", "地铁站", "地铁口", "站台", "候车",
    "充电站", "加气站", "加油站", "公厕", "卫生间", "垃圾",
    "管理处", "派出所", "办事处", "工地", "围挡", "仓库",
)


# 查询词是自然地貌时，POI 名里出现这些"设施"词，说明它是地貌旁边的建筑而非地貌本身。
# 例：「黄厝沙滩」→「黄厝沙滩会展中心」必须拒掉（用户要的是沙滩）。
_NATURE_HINTS = ("沙滩", "海滩", "山", "湖", "岛", "公园", "森林", "湿地",
                 "溪", "瀑", "温泉", "草原")
_FACILITY_HINTS = ("会展", "酒店", "大厦", "中心", "广场", "商场", "公寓",
                   "写字楼", "影城", "银行",
                   # 商业经营场所：实测「颐海度假别墅(环岛路黄厝沙滩店)」
                   # 会被当成「黄厝沙滩」返回，名字里就写着"度假别墅"。
                   "别墅", "度假", "民宿", "饭店", "菜馆", "商店",
                   "分店", "旗舰", "门市", "专营")


# ------------------------------------------------------------------
# 本地 POI 缓存兜底
# ------------------------------------------------------------------

def _iter_cache_pois(city: str):
    """遍历 cache/<city>/*.json 里的 POI。

    缓存文件结构是 ``{"_ts": ..., "payload": [poi, ...]}``——
    注意是 payload 不是 pois，早期调试时按 pois 取会永远是 0 条。
    """
    if not city:
        return
    city_dir = Path(CACHE_DIR) / city
    if not city_dir.is_dir():
        return
    for f in city_dir.glob("*.json"):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        payload = data.get("payload") if isinstance(data, dict) else data
        if not isinstance(payload, list):
            continue
        for poi in payload:
            if isinstance(poi, dict) and poi.get("name"):
                yield poi


def _search_local_cache(name: str, city: str) -> Optional[Dict[str, Any]]:
    """在本城缓存里找名字最匹配的 POI；找不到返回 None。

    只接受 ``match_score >= 1``（POI 名包含查询词）且通过 ``_is_visitable``
    的结果——否则缓存里的「中山陵音乐台」会把「中山陵」抢走却给错坐标。
    """
    best = None
    best_key = (-2, 0.0)
    for poi in _iter_cache_pois(city):
        poi_name = str(poi.get("name") or "")
        score = match_score(poi_name, name)
        if score < 1:
            continue
        if not _is_visitable(poi_name, str(poi.get("typecode") or ""), name):
            continue
        # 优先分档高、其次评分高、再其次名字短的（"中山陵景区"优于"…音乐台"）
        key = (score, -(poi.get("rating") or 0.0), -len(poi_name))
        if key > best_key:
            best_key = key
            best = poi
    if not best:
        return None
    logger.info("本地缓存命中：%s → %s", name, best.get("name"))
    return {
        "query": name,
        "matched": True,
        "source": "cache",
        "reason": "高德暂时查不到，已在本地已抓取的地点数据里找到",
        "poi": {
            "name": best.get("name", ""),
            "lat": best.get("lat"),
            "lng": best.get("lng"),
            "typecode": best.get("typecode", ""),
            "rating": best.get("rating", 0.0),
            "address": best.get("address", ""),
            "business_area": best.get("business_area", ""),
            "photos": (best.get("photos") or [])[:1],
        },
    }


def _is_visitable(poi_name: str, typecode: str, query: str) -> bool:
    """高德返回的这个 POI，是不是一个**真能作为行程站点**的地方？

    这是导入功能的精度闸门。没有它，``place/around``（关键字周边搜）
    会把"植物园地下通道""八市阿杰五香"这类附属设施当成景点返回，
    界面就会宣称"导入 4 个地点"而实际只有 1 个能用。
    """
    name = (poi_name or "").strip()
    code = (typecode or "").strip()
    want = (query or "").strip()

    # 名字与查询完全相等 → 用户要的就是它，无条件接受
    if name and want and name == want:
        return True

    if any(w in name for w in _NOT_VISITABLE_WORDS):
        return False

    # 自然地貌 vs 附属建筑：用户要沙滩，不能给沙滩会展中心
    if any(n in want for n in _NATURE_HINTS) and any(f in name for f in _FACILITY_HINTS):
        return False

    # 高德 typecode 一级分类：15=交通设施服务，19=地名地址信息
    if code[:2] in ("15", "19"):
        return False

    # 市井美食地：菜市场 / 夜市 / 美食街本身就是旅游目的地。
    # 高德把它们归到「购物服务/餐饮服务」(05/06)，若按下面的严格规则会被误杀。
    # 典型案例：查询「八市」，高德返回「第八市场(八市)」——名字的括号里就是
    # 用户说的那个名字，字面却不完全相等（score=1），旧规则直接拒掉，
    # 界面上显示成「不是可游览的地点」。而八市是厦门最有名的市井美食地，
    # 正是"美食为主"行程该去的地方。
    _MARKET_HINTS = ("市场", "菜市", "农贸", "夜市", "美食街", "小吃街",
                     "食街", "大排档", "海鲜广场")
    if any(h in name for h in _MARKET_HINTS):
        # 仍然要求查询与名字有包含关系，避免把无关的"XX市场"塞进来
        if match_score(name, want) >= 1:
            return True

    # 05=餐饮服务，06=购物服务：只有当名字比查询"多出内容"时才拒绝。
    # "八市阿杰五香"(05xxxx) 对查询 "八市" 属于沾边，拒掉；
    # 但用户若直接说要"八市阿杰五香"，名字==查询，前面已放行。
    if code[:2] in ("05", "06"):
        # 只有**完全相等**才收（score 3）。
        # 「八市阿杰五香」对查询「八市」是 startswith(score 2)，必须拒掉。
        if match_score(name, want) < 3:
            return False

    return True


async def _lookup_one(name: str, city: str, center_lat: float,
                      center_lng: float, sem: asyncio.Semaphore) -> Dict[str, Any]:
    """把单个地点名反查成高德真实 POI；查不到则返回未匹配（含原因）。"""
    async with sem:
        try:
            results = await search_pois(city, name, center_lat, center_lng,
                                        page=1, page_size=10)
        except Exception as exc:
            logger.warning("地点反查失败 %s：%s", name, exc)
            return {"query": name, "matched": False, "reason": "高德查询失败，请稍后重试"}

    if not results:
        # 高德偶发空结果（同一个词一次 10 条、一次 0 条）。先重试一次，
        # 再退回本地缓存，最后才如实说"未找到"。
        async with sem:
            try:
                results = await search_pois(city, name, center_lat, center_lng,
                                            page=1, page_size=10)
            except Exception:
                results = []
        if not results:
            cached = _search_local_cache(name, city)
            if cached:
                return cached
            return {"query": name, "matched": False, "reason": "高德未找到该地点"}

    # 先按"是不是一个真能去的地方"过滤，**再**按名字匹配度挑选。
    #
    # 顺序不能反。原实现直接 max() 挑最高分，于是：
    #   "植物园" → "植物园地下通道"（match_score=1，包含即通过）
    #   "八市"   → "八市阿杰五香"  （startswith，score=2）
    #   "黄厝沙滩" → "黄厝沙滩会展中心"
    # 三个都被判为"匹配成功"，界面报"导入 4 个"，实际只有 1 个能用。
    plausible = [r for r in results if _is_visitable(r.get("name", ""),
                                                     r.get("typecode", ""), name)]
    if not plausible:
        # around 的中文分词会把「中山陵」拆成「中山」→ 返回中山植物园，
        # 真正的地点一条都不在结果里。这种情况退回本地缓存。
        cached = _search_local_cache(name, city)
        if cached:
            return cached
        found = "、".join(f"「{r.get('name', '')}」" for r in results[:2])
        return {"query": name, "matched": False,
                "reason": f"高德只找到 {found}，不是可游览的地点"}

    # 用同样的分档规则挑最佳匹配，避免"拙政园"匹配到"片仔癀(拙政园店)"这类
    best = max(plausible, key=lambda r: (match_score(r.get("name", ""), name),
                                         r.get("rating") or 0.0))
    score = match_score(best.get("name", ""), name)
    if score < 0:
        # 高德返回的都不沾边（典型：around 把「中山陵」拆成「中山」，
        # 返回的全是中山植物园）。退回本地缓存再找一次，
        # 而不是直接告诉用户"查不到"——我们自己的数据里就有中山陵。
        cached = _search_local_cache(name, city)
        if cached:
            return cached
        return {"query": name, "matched": False,
                "reason": f"高德只找到「{best.get('name', '')}」，与输入不符"}
    return {
        "query": name,
        "matched": True,
        "poi": {
            "name": best.get("name", ""),
            "lat": best.get("lat"),
            "lng": best.get("lng"),
            "typecode": best.get("typecode", ""),
            "rating": best.get("rating", 0.0),
            "address": best.get("address", ""),
            "business_area": best.get("business_area", ""),
            "photos": (best.get("photos") or [])[:1],
        },
    }


# ---------------------------------------------------------------- 城市探测

# 探测只取前 N 个地点名：再多也只是重复验证，而每次都多一次高德请求
DETECT_SAMPLE = 3
# 至少这么多个地点指向同一城市才采信，否则视为"探测不到"（绝不猜）
DETECT_MIN_VOTES = 2


def _normalize_city_name(raw: str) -> str:
    """「北京市」→「北京」，「广西壮族自治区」→ 原样（不做激进裁剪）。"""
    city = (raw or "").strip()
    if len(city) > 2 and city.endswith("市"):
        city = city[:-1]
    return city


async def detect_city_from_places(places: List[str],
                                  min_votes: int = DETECT_MIN_VOTES
                                  ) -> Tuple[Optional[str], Dict[str, int]]:
    """用地点名反推攻略内容属于哪个城市。

    做法：取前 ``DETECT_SAMPLE`` 个地点名，做**不带城市限定**的全国检索，
    取每个结果第一名的 ``cityname`` 投票。**至少 2 票指向同一城市**才采信。

    为什么要这个下限：像「人民公园」「中山路」这种名字全国几十个城市都有，
    单票众数毫无意义。宁可返回 None（让调用方退回 hint 行为）也不猜。

    返回 ``(city, votes)``；city 为 None 表示探测不到。
    """
    from app.amap.client import search_nationwide

    names = [str(x).strip() for x in (places or []) if str(x).strip()]
    names = names[:DETECT_SAMPLE]
    if len(names) < min_votes:
        # 枚举数不够构成"多数"，不猜
        return None, {}

    votes: Dict[str, int] = {}
    for name in names:
        try:
            hits = await search_nationwide(name, page_size=5)
        except Exception as exc:      # 网络抖动不该让整个导入失败
            logger.warning("城市探测请求失败 %s：%s", name, exc)
            continue
        if not hits:
            continue
        city = _normalize_city_name(hits[0].get("cityname") or "")
        if city:
            votes[city] = votes.get(city, 0) + 1

    if not votes:
        return None, {}
    city, n = max(votes.items(), key=lambda kv: kv[1])
    if n < min_votes:
        logger.info("城市探测未达多数（%s），不采信", votes)
        return None, votes
    logger.info("城市探测命中：%s（投票 %s）", city, votes)
    return city, votes



async def import_text(text: str, city_hint: Optional[str] = None,
                    force_city: Optional[str] = None) -> Dict[str, Any]:
    """导入主流程：抽取地点 → 判定城市 → 高德反查 → 返回匹配/未匹配两部分。

    ## 城市优先级（这是这一版的核心）

        force_city  >  内容识别出的城市  >  地点名反查出的城市  >  city_hint

    旧代码是 ``city_hint or city``——**表单城市无条件压过内容里的城市**。
    用户表单选南京、上传北京攻略图时，北京景点全部被拿去南京搜，
    结果是「全部未匹配」，而界面上给出的理由是「不是可游览的地点」，
    完全误导。

    ``force_city`` 只由用户显式选择时传入（冲突弹窗里点「仍在南京核对」）。
    """
    content_city, places, source, notes = await extract_places(text)
    content_city = _normalize_city_name(content_city or "") or None
    hint = _normalize_city_name(city_hint or "") or None
    forced = _normalize_city_name(force_city or "") or None

    # 内容里没写城市时，用地点名反推一次。
    #
    # 阈值随「有没有用户城市」变化：
    #   - 用户已选城市 → 要 2 票才敢覆盖（避免「人民公园」这类同名地误判）
    #   - 用户没选城市 → 1 票就够。没有可比对的对象，探测结果就是唯一线索；
    #     坚持 2 票会让"只写了一个地名的攻略图"直接失败（实测 city=None）。
    detected_city: Optional[str] = None
    city_votes: Dict[str, int] = {}
    if not content_city and not forced and places:
        need = DETECT_MIN_VOTES if hint else 1
        detected_city, city_votes = await detect_city_from_places(
            places, min_votes=need)

    if forced:
        city = forced
        city_source = "forced"
    elif content_city:
        city = content_city
        city_source = "content"
    elif detected_city:
        city = detected_city
        city_source = "detected"
    else:
        city = hint or ""
        city_source = "hint" if hint else "none"

    # 冲突 = 表单城市与最终城市不一致（且不是用户自己选的 forced）
    city_conflict = bool(not forced and hint and city and hint != city)

    result: Dict[str, Any] = {
        "city": city or None,
        # 前端据此决定是否弹「攻略图是北京，你在南京」的确认框
        "city_hint": hint,
        "city_conflict": city_conflict,
        "city_source": city_source,
        "city_votes": city_votes,
        "source": source,
        "notes": notes,
        "places": [],
        "matched_count": 0,
        "unmatched_count": 0,
    }
    if not places:
        result["notes"] = notes + ["没有从内容里识别出地点，可尝试粘贴更完整的行程文字"]
        return result
    if not city:
        # 没有城市就无法做"限定城市"的反查，反查结果会跨城漂移
        result["places"] = [{"query": p, "matched": False,
                             "reason": "未识别出城市，请先选择目的地"} for p in places]
        result["unmatched_count"] = len(places)
        result["notes"] = notes + ["未识别出城市，无法校验地点"]
        return result

    # 用 geocode 而不是本地城市表来判定"城市是否可用"。
    # 旧实现读本地表（只有 12 个种子城市），导致「广州」在规划里完全正常、
    # 在导入里却被判"暂不支持城市"——同一个城市两个功能给出矛盾结论。
    # geocode 会先查本地表、未命中再调高德，与规划走的是同一条判定路径。
    geo = await geocode(city)
    if not geo:
        result["places"] = [{"query": p, "matched": False,
                             "reason": f"无法定位城市「{city}」，请检查城市名是否正确"} for p in places]
        result["unmatched_count"] = len(places)
        result["notes"] = notes + [f"高德无法定位城市「{city}」，未做地点校验"]
        return result

    sem = asyncio.Semaphore(LOOKUP_CONCURRENCY)
    resolved = await asyncio.gather(*[
        _lookup_one(p, city, geo["center_lat"], geo["center_lng"], sem) for p in places
    ])

    # 保序：按"匹配到的排前面"输出，但保持各自原始顺序
    matched = sum(1 for r in resolved if r["matched"])

    # ---- 零命中回补：一个都没匹配上时，重新怀疑城市 ----
    #
    # 为什么需要这一步：预判阶段要求"至少 2 个地点指向同一城市"才采信，
    # 而攻略图常常只写 1 个地名（"八达岭长城"）。此时探测不到城市，
    # 代码会退回用户城市继续核对 → 全部未匹配，界面上只显示
    # "不是可游览的地点"，用户完全不知道是城市搞错了。
    #
    # 触发条件很窄：**用当前城市一个都没匹配上** + 用户没强制指定城市。
    # 此时把探测阈值放宽到 1 票，只要高德认为它属于别的城市，
    # 就用那个城市重查一遍，并把冲突标记出来让用户定夺。
    if (matched == 0 and not forced and city and places
            and city == (hint or city)):
        alt_city, alt_votes = await detect_city_from_places(places, min_votes=1)
        if alt_city and alt_city != city:
            alt_geo = await geocode(alt_city)
            if alt_geo:
                logger.info("城市回补：%s 在 %s 全部未命中，改按 %s 重查",
                            places[:3], city, alt_city)
                resolved = await asyncio.gather(*[
                    _lookup_one(p2, alt_city, alt_geo["center_lat"],
                                alt_geo["center_lng"], sem) for p2 in places
                ])
                matched = sum(1 for r in resolved if r["matched"])
                if matched:
                    # 只有"换个城市真能匹配上"才改判，否则保持原结果
                    city = alt_city
                    city_source = "detected"
                    city_conflict = bool(hint and hint != alt_city)
                    city_votes = alt_votes
                    result.update({
                        "city": city,
                        "city_conflict": city_conflict,
                        "city_source": city_source,
                        "city_votes": city_votes,
                    })

    result["places"] = list(resolved)
    result["matched_count"] = matched
    result["unmatched_count"] = len(resolved) - matched
    if result["unmatched_count"]:
        result["notes"] = notes + [
            f"有 {result['unmatched_count']} 个地点未能在高德匹配到，已单独列出，"
            "不会加入行程"
        ]
    return result
