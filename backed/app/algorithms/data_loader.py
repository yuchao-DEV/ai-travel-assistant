"""数据加载器：加载 typecode_map + cities，并富化 POI 列表。"""
import json
import logging
from functools import lru_cache
from typing import Any, Dict, List, Optional

from app.algorithms.models import POI
from app.config import CACHE_DIR, DATA_DIR

logger = logging.getLogger("app.data_loader")

# 自扩展的城市表：cities.json 里只有 12 个"种子城市"，其余城市由高德地理编码
# 实时解析。解析成功的城市会记到这里，下次就不用再花一次 API 调用。
# 这让本地表从"固定 12 城"变成"越用越全"，同时不改变 cities.json 的语义。
_EXTRA_CITIES_PATH = CACHE_DIR / "cities_extra.json"


@lru_cache(maxsize=1)
def load_typecode_map() -> Dict[str, Dict[str, Any]]:
    """加载 typecode → {tags, indoor} 映射表（带缓存）。"""
    fpath = DATA_DIR / "typecode_map.json"
    if not fpath.exists():
        return {}
    data = json.loads(fpath.read_text(encoding="utf-8"))
    # 过滤掉 _comment / _fallback_rules 等元数据 key
    return {k: v for k, v in data.items() if not k.startswith("_")}


@lru_cache(maxsize=1)
def load_seed_cities() -> Dict[str, Dict[str, Any]]:
    """cities.json 里的**种子城市**（12 个常用城市，带 adcode 与关键词模板）。"""
    fpath = DATA_DIR / "cities.json"
    if not fpath.exists():
        return {}
    data = json.loads(fpath.read_text(encoding="utf-8"))
    cities = data.get("cities", data) if isinstance(data, dict) else data
    return {c["name"]: c for c in cities}


def load_extra_cities() -> Dict[str, Dict[str, Any]]:
    """自扩展城市表（高德解析成功后写入）。"""
    if not _EXTRA_CITIES_PATH.exists():
        return {}
    try:
        data = json.loads(_EXTRA_CITIES_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception as exc:
        logger.warning("扩展城市表读取失败，忽略：%s", exc)
        return {}


@lru_cache(maxsize=1)
def load_cities() -> Dict[str, Dict[str, Any]]:
    """加载城市配置表 = 种子城市 + 自扩展城市，返回 {城市名: 配置}。

    **这不是能力白名单**：真正的可用城市由 :func:`app.amap.client.geocode` 决定
    （本地表未命中就调高德）。本表只是"已知城市"的快速路径，被
    ``nl_parse`` 的城市识别与 ``importer`` 的城市校验复用。
    """
    merged: Dict[str, Dict[str, Any]] = dict(load_seed_cities())
    for name, cfg in load_extra_cities().items():
        merged.setdefault(name, cfg)
    return merged


def get_city_config(city: str) -> Optional[Dict[str, Any]]:
    """获取单个城市配置（种子表 + 扩展表）。"""
    return load_cities().get(city)


def remember_city(name: str, *, adcode: str = "", center_lat: float = 0.0,
                  center_lng: float = 0.0,
                  keywords: Optional[List[str]] = None) -> bool:
    """把高德解析成功的城市记进扩展表（幂等）。返回是否新增。

    这样「第一次用广州要调一次高德，之后就是本地命中」，
    城市识别（nl_parse）与导入校验（importer）也随之自动认识它。
    """
    name = (name or "").strip()
    if not name or not center_lat or not center_lng:
        return False
    if name in load_seed_cities() or name in load_extra_cities():
        return False
    extra = load_extra_cities()
    extra[name] = {
        "name": name,
        "adcode": adcode,
        "center_lat": center_lat,
        "center_lng": center_lng,
        "keywords": keywords or ["景点", "美食", "博物馆", "公园"],
        "_source": "amap-geocode",   # 标明来源：这批坐标是高德解析出来的
    }
    try:
        _EXTRA_CITIES_PATH.parent.mkdir(parents=True, exist_ok=True)
        _EXTRA_CITIES_PATH.write_text(
            json.dumps(extra, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception as exc:
        logger.warning("扩展城市表写入失败：%s", exc)
        return False
    load_cities.cache_clear()
    logger.info("城市已记入本地扩展表：%s（下次免一次高德调用）", name)
    return True


# ---------- 关键词兜底 ----------

_INDOOR_KEYWORDS = [
    "博物馆", "美术馆", "商场", "购物中心", "剧院", "影院",
    "图书馆", "展览馆", "科技馆", "水族馆", "海洋馆", "室内",
    "纪念馆", "大剧院", "音乐厅",
]
_OUTDOOR_KEYWORDS = [
    "公园", "广场", "山", "湖", "海滩", "海滨", "湿地",
    "景区", "风景区", "古迹", "遗址", "寺", "塔", "桥",
    "古镇", "老街", "步行街", "植物园", "动物园",
]

# 关键词 → tag 的兜底映射
_KEYWORD_TAG_MAP = {
    "博物馆": ["历史", "艺术"], "美术馆": ["艺术"], "纪念馆": ["历史"],
    "展览馆": ["艺术"], "科技馆": ["艺术"],
    "公园": ["自然"], "广场": ["自然"], "山": ["自然"], "湖": ["自然"],
    "海滩": ["自然"], "海滨": ["自然"], "湿地": ["自然"],
    "景区": ["自然", "历史"], "风景区": ["自然"], "古迹": ["历史"],
    "遗址": ["历史"], "寺": ["历史"], "塔": ["历史"],
    "古镇": ["历史"], "老街": ["历史", "美食"],
    "植物园": ["自然"], "动物园": ["自然"],
    "商场": ["购物"], "购物中心": ["购物"], "步行街": ["购物"],
    "酒吧": ["夜生活"], "KTV": ["夜生活"],
    "餐厅": ["美食"], "小吃": ["美食"], "美食": ["美食"],
}


# 高德 typecode 前缀 → 行程条目类型
# 05xxxx 餐饮服务 / 10xxxx 住宿服务 / 其余（11xxxx 风景名胜、14xxxx 科教文化等）按景点
_POI_TYPE_BY_PREFIX = (
    ("05", "restaurant"),
    ("10", "hotel"),
)

# 高德 category 末级里的等级词（注意：这是**高德的分类等级**，
# 与文旅部的 5A/4A 评级不完全等同，对外表述时不要混用）
_SCENIC_LEVEL_KEYWORDS = ("世界级", "国家级", "省级", "市级", "区县级")


def poi_type_from_typecode(typecode: str) -> str:
    """由 typecode 前缀判定条目类型。

    此前 `poi_type` 从未被赋值，恒为默认的 "spot"，
    导致前端的餐饮/住宿卡片样式从不触发、排程里餐厅/酒店的分支也从不生效。
    """
    tc = (typecode or "").strip()
    for prefix, poi_type in _POI_TYPE_BY_PREFIX:
        if tc.startswith(prefix):
            return poi_type
    return "spot"


def scenic_level_from_category(category: str) -> str:
    """从高德中文三级分类里提取景区等级词。

    实测形如 "风景名胜;风景名胜;国家级景点" → 取末级并确认含等级词。
    """
    if not category:
        return ""
    last = category.split(";")[-1].strip()
    if any(kw in last for kw in _SCENIC_LEVEL_KEYWORDS):
        return last
    return ""


def enrich_poi(poi: POI) -> POI:
    """富化单个 POI：补 poi_type / scenic_level / tags / indoor。

    步骤：
    1. 由 typecode 判定条目类型（餐厅 / 酒店 / 景点）
    2. 由中文分类提取景区等级
    3. typecode 精确匹配补 tags + indoor
    4. 关键词兜底
    5. 未知 → 空 tags + 默认 outdoor
    """
    poi.poi_type = poi_type_from_typecode(poi.typecode)
    poi.scenic_level = scenic_level_from_category(poi.category)

    tc_map = load_typecode_map()

    # 3. typecode 精确匹配
    if poi.typecode:
        # 高德 typecode 是 6 位层级码，如 110101 = 1101(二级) + 01(三级)
        # 映射表用的是二级码：110100、110200 等
        # 尝试顺序：精确 → 三级截00 → 二级截00 → 一级截0000
        tc = poi.typecode
        candidates = []
        if len(tc) >= 6:
            candidates.append(tc)                    # 精确 110101
            candidates.append(tc[:4] + "00")          # 二级 110100
        if len(tc) >= 4:
            candidates.append(tc[:2] + "0000")       # 一级 110000
        for tc_key in candidates:
            if tc_key in tc_map:
                entry = tc_map[tc_key]
                poi.tags = list(entry.get("tags", []))
                poi.indoor = entry.get("indoor", False)
                return poi

    # 4. 关键词兜底
    name = poi.name
    for kw, tags in _KEYWORD_TAG_MAP.items():
        if kw in name:
            poi.tags = list(tags)
            poi.indoor = any(k in name for k in _INDOOR_KEYWORDS)
            return poi

    # 5. 未知
    poi.tags = []
    poi.indoor = False
    return poi


def enrich_pois(pois: List[POI]) -> List[POI]:
    """批量富化 POI 列表。"""
    return [enrich_poi(p) for p in pois]


def raw_to_pois(raw_list: List[Dict[str, Any]]) -> List[POI]:
    """高德 API 原始结果 → POI 列表 + 富化。"""
    pois = [POI.from_amap(r) for r in raw_list]
    return enrich_pois(pois)
