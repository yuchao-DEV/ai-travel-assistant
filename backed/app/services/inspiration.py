"""旅行灵感流：把目的地的真实 POI 变成"可以加入行程"的卡片。

**用途有两个**：
1. 行程生成要 20–40 秒（LLM 润色 + 路径优化 + 时间排程），这段空窗期用真实地点把它填住，
   而不是让用户盯着一个转圈动画。因此本接口**不调用大模型、不做路线计算**，
   只做"拉 POI + 富化"，通常 1–3 秒返回（POI 层有 24h 缓存，二次访问接近瞬时）。
2. 行程页「旅行灵感」Tab 展示"值得去"的地点，用户点「+」即可加入必去。

**只输出真实数据**：名称、真实照片、真实评分、真实人均消费、商圈、营业时间。
刻意不做新闻式标题——高德不提供 UGC/编辑文案，本项目也没有内容源，
硬编"XX 节目单焕新"这类时效性标题就是编造。卡片标题用「地点名」，
副信息用真实分类，语义不会失真。

分类也只用高德真实分类推导，不臆造「新开业」这种无数据支撑的类目。
"""
import logging
from collections import Counter
from typing import Any, Dict, List

from app.algorithms.data_loader import get_city_config, raw_to_pois
from app.amap.client import fetch_all_pois, geocode
from app.config import amap_enabled

logger = logging.getLogger("app.inspiration")

# 灵感流每个关键词最多取多少条。规划时取 50，这里取小一些以保证接口够快。
MAX_PER_KEYWORD = 15

# 卡片总数上限。取值要足够大以便**把分类池基本取完**：
# 否则会出现"标签写美食 25、点进去只有 6 张"这种自相矛盾的展示——
# 标签数字必须等于点进去能看到的张数。
MAX_ITEMS = 120

# 分类展示顺序（"全部"由前端处理，这里只给真实类目）
CATEGORY_ORDER = ["风景", "博物馆", "展览", "美食", "购物", "住宿", "其他"]


def card_category(poi) -> str:
    """由高德真实分类推导卡片所属类目。

    只用 `poi_type` 与中文分类 `category`，不引入任何外部假设。
    """
    cat = poi.category or ""
    if poi.poi_type == "restaurant":
        return "美食"
    if poi.poi_type == "hotel":
        return "住宿"
    if "博物馆" in cat:
        return "博物馆"
    if any(k in cat for k in ("美术馆", "展览馆", "会展中心", "科技馆", "文化宫")):
        return "展览"
    if any(k in cat for k in ("购物中心", "商场", "步行街", "商业街", "专卖店")):
        return "购物"
    if any(k in cat for k in ("公园", "风景", "广场", "游乐", "景区", "寺", "塔", "古镇")):
        return "风景"
    return "其他"


def category_counts(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """统计**返回卡片**里的类目分布（按类目固定顺序输出）。

    这是灵感流唯一的计数口径，也是 /api/inspiration 的 ``categories`` 的来源。
    必须只统计返回的卡片，不能统计池子总数——否则标签数字会大于点进去能看到的张数。
    """
    counter = Counter(card.get("category", "其他") for card in items)
    return [
        {"key": key, "count": counter[key]}
        for key in CATEGORY_ORDER
        if counter.get(key)
    ]


def _to_card(poi) -> Dict[str, Any]:
    """POI → 卡片。照片只取 1 张，控制响应体积。"""
    return {
        "name": poi.name,
        "category": card_category(poi),
        "type": poi.poi_type,
        "rating": round(poi.rating, 1),
        "photos": list(poi.photos[:1]),
        "business_area": poi.business_area,
        "address": poi.address,
        "cost": poi.cost,
        "cost_source": poi.cost_source,
        "open_hours": poi.open_hours or None,
        "scenic_level": poi.scenic_level,
        "tags": list(poi.tags),
    }


def _interleave(buckets: Dict[str, List[Dict[str, Any]]], limit: int) -> List[Dict[str, Any]]:
    """按类目轮转取卡片，避免高分餐厅把整条流占满。"""
    result: List[Dict[str, Any]] = []
    # 游标按固定类目顺序初始化：某个类目没有数据时也要有游标，否则会 KeyError
    cursors = {key: 0 for key in CATEGORY_ORDER}
    while len(result) < limit:
        progressed = False
        for key in CATEGORY_ORDER:
            bucket = buckets.get(key) or []
            idx = cursors[key]
            if idx < len(bucket):
                result.append(bucket[idx])
                cursors[key] = idx + 1
                progressed = True
                if len(result) >= limit:
                    break
        if not progressed:
            break
    return result


async def build(city: str, limit: int = 24) -> Dict[str, Any]:
    """构建灵感卡片流。

    返回 {city, enhanced, total, categories, items}；
    高德不可用或无数据时 items 为空，由前端隐藏该模块（不影响主流程）。
    """
    empty = {"city": city, "enhanced": False, "total": 0, "categories": [], "items": []}
    if not city or not amap_enabled():
        return empty

    try:
        geo = await geocode(city)
        if not geo:
            logger.info("灵感流：城市未识别 %s", city)
            return empty
        # 复用城市配置里的关键词，与规划阶段共享 POI 缓存，避免重复消耗配额
        cfg = get_city_config(city) or {}
        keywords = cfg.get("keywords") or ["景点", "美食", "博物馆", "公园"]
        raw = await fetch_all_pois(
            city, keywords, geo["center_lat"], geo["center_lng"],
            max_per_keyword=MAX_PER_KEYWORD,
            # 并发拉取：灵感流要在行程生成的空窗期尽快出现内容，
            # 串行 5 个关键词实测要 8 秒级，并发后降到 3 秒级
            concurrency=3,
        )
        pois = [p for p in raw_to_pois(raw) if p.name]
    except Exception as exc:
        logger.warning("灵感流构建失败：%s", exc)
        return empty

    if not pois:
        return empty

    # 按类目分桶后各自按评分降序，再轮转输出
    buckets: Dict[str, List[Dict[str, Any]]] = {}
    for poi in pois:
        buckets.setdefault(card_category(poi), []).append(_to_card(poi))
    for cards in buckets.values():
        cards.sort(key=lambda c: (-c["rating"], not c["photos"]))

    items = _interleave(buckets, max(1, min(limit, MAX_ITEMS)))
    # 计数只统计真正返回的卡片，保证"标签数字 == 点进去能看到的张数"
    return {
        "city": city,
        "enhanced": True,
        "total": len(items),
        "categories": category_counts(items),
        "items": items,
    }
