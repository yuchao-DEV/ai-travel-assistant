"""高德开放平台 REST 客户端：封装 4 个核心接口。

接口列表：
1. geocode/geo        — 城市名 → adcode + 中心坐标
2. weather/weatherInfo — 城市 adcode → 未来 4 天天气
3. place/text          — POI 关键词搜索（景点/餐厅/酒店）
4. direction/transit   — 相邻景点间公交路线耗时

每个接口独立 try/except 降级，高德不可用或未配置 Key 时返回空结果，
engine 会据此走纯 LLM 降级模式（enhanced=False）。
"""
import asyncio
import json
import logging
import math
import re
from typing import Any, Dict, List, Optional

import httpx

from app.amap.cache import get_cache, set_cache
from app.config import AMAP_BASE, AMAP_KEY, POI_CACHE_TTL
from app.config import amap_enabled

logger = logging.getLogger("app.amap")


# ---------- 工具函数 ----------

def haversine(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """计算两点间球面距离（公里），用于交通矩阵初筛。"""
    r = 6371.0
    lat1_r = math.radians(lat1)
    lat2_r = math.radians(lat2)
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(lat1_r) * math.cos(lat2_r) * math.sin(dlng / 2) ** 2)
    return 2 * r * math.asin(math.sqrt(a))


def estimate_walk_minutes(distance_km: float) -> int:
    """步行估时：5km/h。"""
    return max(1, int(distance_km / 5.0 * 60))


# ---------- 接口 1: 地理编码 ----------

_ADDR_FIELDS = (
    "formatted_address", "province", "city", "district", "township", "street", "number",
)
# 地名后缀，用于「杭州市」→「杭州」这类归一
_PLACE_SUFFIX_RE = re.compile(r"(省|市|区|县|自治州|自治区|地区|盟|旗)$")


def geocode_matches(request: str, item: Dict[str, Any]) -> bool:
    """核对高德返回的地址是否真的对应请求的地名。

    **为什么必须核对**：高德 `geocode/geo` 对无法识别的输入会做模糊匹配并返回
    某个真实坐标（实测请求「不存在的城市xyz」也能拿到坐标）。若不核对，
    就会据此算出完全错误的两地距离，或让行程规划用错城市的 POI。

    判定规则：请求名（去掉省/市/区/县等后缀）必须出现在返回的行政区文本里。
    """
    request = (request or "").strip()
    if not request:
        return False
    haystack = "".join(str(item.get(field, "") or "") for field in _ADDR_FIELDS)
    if not haystack:
        return False
    candidates = {request, _PLACE_SUFFIX_RE.sub("", request)}
    return any(name and name in haystack for name in candidates)


async def geocode(city: str) -> Optional[Dict[str, Any]]:
    """城市名 → {adcode, center_lat, center_lng}。

    优先从 cities.json 读取（本地表），找不到再调高德 API；
    高德的结果会经过 :func:`geocode_matches` 核对，模糊匹配的一律视为找不到。
    """
    # 先查本地 cities.json
    from app.algorithms.data_loader import get_city_config
    cfg = get_city_config(city)
    if cfg:
        return {
            "adcode": cfg["adcode"],
            "center_lat": cfg["center_lat"],
            "center_lng": cfg["center_lng"],
            "keywords": cfg.get("keywords", ["景点", "美食"]),
        }

    if not amap_enabled():
        return None

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{AMAP_BASE}/geocode/geo", params={
                "key": AMAP_KEY,
                "address": city,
            })
            resp.raise_for_status()
            data = resp.json()
            geocodes = data.get("geocodes") or []
            if data.get("count") == "0" or not geocodes:
                return None
            first = geocodes[0]
            if not geocode_matches(city, first):
                # 高德的模糊匹配结果，不能当作命中
                print(f"[amap] geocode 未命中「{city}」，返回的是"
                      f"「{first.get('formatted_address', '')}」，已忽略")
                return None
            loc = first["location"].split(",")
            result = {
                "adcode": first.get("adcode", ""),
                "center_lat": float(loc[1]),
                "center_lng": float(loc[0]),
                # 回显高德实际解析到的地址：同名异地（实测「乌镇」会解析到陕西、
                # 「西湖」会解析到台湾苗栗）时，前端能把这次解析展示出来让用户发现
                "resolved_address": first.get("formatted_address", ""),
                "keywords": ["景点", "美食", "博物馆", "公园"],
            }
            # 记进本地扩展表：这个城市下次直接本地命中，省一次 API 调用。
            # 也让 nl_parse 的城市识别、importer 的城市校验随之认识它
            # —— 否则会出现"能规划但不能导入"这种自相矛盾。
            try:
                from app.algorithms.data_loader import remember_city
                remember_city(
                    city,
                    adcode=result["adcode"],
                    center_lat=result["center_lat"],
                    center_lng=result["center_lng"],
                    keywords=result["keywords"],
                )
            except Exception as exc:  # 缓存失败不影响本次结果
                print(f"[amap] 城市记入本地表失败（不影响本次规划）: {exc}")
            return result
    except Exception as e:
        print(f"[amap] geocode 降级: {e}")
        return None


# ---------- 接口 2: 天气 ----------

async def get_weather(adcode: str) -> List[str]:
    """查询未来 4 天天气，返回简洁描述列表。

    返回格式: ["晴 25°/15°", "多云 23°/14°", ...]
    降级时返回空列表。
    """
    if not amap_enabled() or not adcode:
        return []

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{AMAP_BASE}/weather/weatherInfo", params={
                "key": AMAP_KEY,
                "city": adcode,
                "extensions": "all",
            })
            resp.raise_for_status()
            data = resp.json()
            casts = data.get("casts", [])
            result = []
            for c in casts[:4]:
                day_weather = c.get("dayweather", "未知")
                day_temp = c.get("daytemp", "?")
                night_temp = c.get("nighttemp", "?")
                result.append(f"{day_weather} {day_temp}°/{night_temp}°")
            return result
    except Exception as e:
        print(f"[amap] weather 降级: {e}")
        return []


# ---------- 接口 3: POI 搜索 ----------

# 缓存 schema 版本：提取字段发生变化时必须递增，否则会命中旧结构的缓存
# v2: 开始提取 biz_ext（评分/营业时间/人均消费）、category、business_area、photos
# v3: 图片地址统一归一为 https（v2 缓存里存的是 http，会触发混合内容拦截）
# v4: address / tel / business_area / category 统一走 _first_text 归一
#     （高德对部分 POI 返回空列表 [] 而非字符串，旧缓存里存的是 []）
# v5: 检索改走高德 **v5 place/text**（v3 被反爬封）。旧 v4 缓存是
#     ``place/around`` 降级时写下的"周边搜"结果——名字匹配很差
#     （「中山陵」那条缓存里全是中山植物园）。不递增版本会继续命中这些脏数据。
POI_CACHE_VERSION = "v5"


def _first_number(value: Any) -> float:
    """从高德字段里取出一个数字。

    高德的字段类型不稳定：`biz_ext.cost` 可能是 "172.00" 字符串、也可能是 []，
    `rating` 可能是 "4.7" 或 4.7。这里统一容错，取不到就返回 0.0。
    """
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return 0.0
    if isinstance(value, (list, tuple)):
        for item in value:
            num = _first_number(item)
            if num:
                return num
    return 0.0


def _first_text(value: Any) -> str:
    """从高德字段里取出一段非空文本（字段可能是 str、list 或空）。"""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (list, tuple)):
        for item in value:
            text = _first_text(item)
            if text:
                return text
    return ""


def _extract_photos(value: Any, limit: int = 3) -> List[str]:
    """高德 photos 是 [{url, title, provider}, ...]，取出前几张图片地址。

    统一把 `http://` 归一为 `https://`：高德返回的图片地址多为 http，
    部署到 https 站点后浏览器会以混合内容为由拦截。
    """
    if not isinstance(value, (list, tuple)):
        return []
    urls = []
    for item in value:
        if isinstance(item, dict):
            url = item.get("url")
            if isinstance(url, str) and url:
                urls.append(_https(url))
        if len(urls) >= limit:
            break
    return urls


def _https(url: str) -> str:
    """把 http 图片地址升级为 https（高德同一路径 https 实测可用）。"""
    if url.startswith("http://"):
        return "https://" + url[len("http://"):]
    return url


async def search_pois(city: str, keyword: str, center_lat: float,
                      center_lng: float, page: int = 1,
                      page_size: int = 25) -> List[Dict[str, Any]]:
    """按关键词搜索 POI，返回标准化 POI 列表。

    高德 place/text 在 extensions=all 时返回 40+ 个字段，本项目只取算法与前端
    真正要用的那部分。**注意 `rating` 与营业时间都在 `biz_ext` 里**，
    顶层 `rating` 实测在 150 条抽样中全部为 null，读顶层会永远拿到 0。

    每条 POI 字段见下方 result.append。
    带缓存（24h TTL，缓存键含 schema 版本）。

    **同键请求合并**：行程规划与旅行灵感流会并发拉同一批关键词（两者用同一套
    城市关键词配置）。没有合并时两边各打一次高德，实测把灵感流的首屏从 2 秒
    拖到 13 秒。这里对「同一城市 + 同一关键词 + 同一页」的并发请求只发一次，
    其余调用等待同一个结果。
    """
    cache_key = f"pois_{POI_CACHE_VERSION}_{keyword}_p{page}"
    cached = get_cache(city, cache_key, POI_CACHE_TTL)
    if cached is not None:
        return cached

    if not amap_enabled():
        return []

    inflight_key = f"{city}|{cache_key}"
    task = _inflight.get(inflight_key)
    if task is None:
        task = asyncio.ensure_future(
            _search_pois_remote(city, keyword, center_lat, center_lng, page, page_size, cache_key)
        )
        _inflight[inflight_key] = task
        task.add_done_callback(lambda _t, k=inflight_key: _inflight.pop(k, None))
    # shield：某个等待者被取消时不应连带取消正在进行的高德请求
    return await asyncio.shield(task)


# 进行中的 POI 拉取任务：(城市|缓存键) → Task
_inflight: Dict[str, "asyncio.Future[List[Dict[str, Any]]]"] = {}


def _parse_amap_json(text: str, endpoint: str) -> Optional[Dict[str, Any]]:
    """解析高德响应；不是 JSON 时返回 None 并记日志。

    高德的**反爬处罚页**返回的是 HTML（``bixi.alicdn.com/punish/...``），
    直接 ``resp.json()`` 会抛 ``Expecting value`` 这种难懂的错误。
    这里显式识别，日志里写清"被反爬拦截"，而不是让上层看到一个 JSON 解析错。
    """
    body = (text or "").lstrip()
    if not body.startswith("{"):
        preview = body[:80].replace("\n", " ")
        logger.warning("%s 返回的不是 JSON（疑似反爬处罚页）：%s", endpoint, preview)
        return None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        logger.warning("%s 返回的 JSON 无法解析", endpoint)
        return None
    if str(data.get("status")) != "1":
        # status=0 表示业务失败（如 10002 SERVICE_NOT_AVAILABLE）
        logger.warning("%s 业务失败：infocode=%s info=%s",
                       endpoint, data.get("infocode"), data.get("info"))
        return None
    return data


async def _search_around(client: httpx.AsyncClient, keyword: str,
                         center_lat: float, center_lng: float,
                         page: int, page_size: int) -> Optional[Dict[str, Any]]:
    """``place/around``：按中心点 + 半径检索。

    半径取 50km（高德上限），足以覆盖一个城市的绝大多数景点。
    与 ``place/text`` 的差别是它**偏向中心点**——远端景点可能进不来，
    但"能查到"远比"查得全"重要：查不到就等于整个功能不可用。
    """
    try:
        resp = await client.get(f"{AMAP_BASE}/place/around", params={
            "key": AMAP_KEY,
            "keywords": keyword,
            "location": f"{center_lng},{center_lat}",
            "radius": 50000,
            "offset": page_size,
            "page": page,
            "extensions": "all",
        })
        resp.raise_for_status()
        return _parse_amap_json(resp.text, "place/around")
    except httpx.HTTPError as exc:
        logger.warning("place/around 检索失败：%s", exc)
        return None


async def _search_text_v5(client: httpx.AsyncClient, city: str, keyword: str,
                          page: int, page_size: int) -> Optional[Dict[str, Any]]:
    """高德 **v5** 关键字检索。返回 v5 原始 JSON（含 pois），失败返回 None。

    为什么不用 v3：v3 ``/v3/place/text`` 目前稳定返回反爬处罚页（HTML），
    而同名的 v5 接口正常。这是目前"按名字查地点"唯一可用的路。
    """
    params = {
        "key": AMAP_KEY,
        "keywords": keyword,
        "page_size": min(max(page_size, 1), 25),
        "page_num": max(page, 1),
        # 拿营业时间与照片（v5 放在 business / photos 字段里）
        "show_fields": "business,photos",
    }
    if city:
        params["region"] = city
        params["city_limit"] = "true"
    try:
        resp = await client.get("https://restapi.amap.com/v5/place/text",
                                params=params)
    except Exception as exc:
        logger.warning("v5 place/text 请求失败：%s", exc)
        return None
    data = _parse_amap_json(resp.text, "v5/place/text")
    if not data or str(data.get("status")) != "1":
        return None
    return data


def _v5_poi_to_dict(p: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """把 v5 的 poi 归一成项目内部用的扁平结构（与 v3 路径保持一致）。"""
    loc = str(p.get("location") or "").split(",")
    if len(loc) != 2:
        return None
    try:
        lng, lat = float(loc[0]), float(loc[1])
    except ValueError:
        return None

    biz = p.get("business") if isinstance(p.get("business"), dict) else {}
    open_text = _first_text(biz.get("opentime_week"))
    return {
        "name": p.get("name", ""),
        "lat": lat,
        "lng": lng,
        "typecode": (p.get("typecode") or "").split(";")[0].strip(),
        "rating": _first_number(biz.get("rating")) or _first_number(p.get("rating")),
        "address": _first_text(p.get("address")),
        "tel": _first_text(biz.get("tel")),
        "category": _first_text(p.get("type")),
        "business_area": _first_text(p.get("business_area")),
        # v5 的 opentime_week 常是「08:30-17:00(周一闭馆)」这种，直接当展示文本用；
        # 结构化解析由下游 open_hours 解析器处理
        "open_time": open_text,
        "open_time_text": open_text,
        "biz_cost": _first_number(biz.get("cost")),
        "photos": _extract_photos(p.get("photos")),
        "entr_location": p.get("entr_location", ""),
        "cityname": _first_text(p.get("cityname")),
        "adname": _first_text(p.get("adname")),
    }


async def _search_pois_remote(city: str, keyword: str, center_lat: float,
                              center_lng: float, page: int, page_size: int,
                              cache_key: str) -> List[Dict[str, Any]]:
    """真正发起高德请求并写缓存（由 search_pois 去重后调用）。

    两条路：

    1. ``place/text``（按城市名检索，结果最贴切）——**首选**
    2. ``place/around``（按中心点 + 半径检索）——``place/text`` 不可用时自动降级

    为什么要这条降级路：实测 ``place/text`` 会被高德的反爬机制拦下，
    返回一个**HTML 处罚页**而不是 JSON（``bixi.alicdn.com/punish/...``）。
    这时 ``resp.json()`` 会抛 ``Expecting value``，如果不处理，
    整个地点检索就全废了——而 ``place/around`` 在同一时刻是正常的。
    """
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            # ---- 首选：v5 place/text（v3 被反爬封，v5 正常）----
            v5 = await _search_text_v5(client, city, keyword, page, page_size)
            if v5 is not None:
                result = []
                for p in v5.get("pois") or []:
                    item = _v5_poi_to_dict(p)
                    if item:
                        result.append(item)
                if result:
                    set_cache(city, cache_key, result)
                return result

            # ---- 次选：v3 place/text（万一 v5 也不可用）----
            text_params = {
                "key": AMAP_KEY,
                "keywords": keyword,
                "city": city,
                "citylimit": "true",
                "offset": page_size,
                "page": page,
                "extensions": "all",
            }
            resp = await client.get(f"{AMAP_BASE}/place/text", params=text_params)
            resp.raise_for_status()
            data = _parse_amap_json(resp.text, "place/text")

            if data is None:
                # 两个 text 接口都不行 → 最后才退到 place/around（名字匹配很差）
                logger.warning(
                    "v3/v5 place/text 都不可用，改用 place/around 检索「%s」",
                    keyword)
                data = await _search_around(client, keyword, center_lat,
                                            center_lng, page, page_size)

            pois_raw = (data or {}).get("pois", [])
            result = []
            for p in pois_raw:
                loc = p.get("location", "").split(",")
                if len(loc) != 2:
                    continue
                try:
                    lat = float(loc[1])
                    lng = float(loc[0])
                except ValueError:
                    continue
                typecode = p.get("typecode", "")
                # typecode 可能是多个，取第一个
                tc = typecode.split(";")[0].strip() if typecode else ""
                biz = p.get("biz_ext") if isinstance(p.get("biz_ext"), dict) else {}

                # 评分：优先 biz_ext.rating（实测 150/150 有值），顶层 rating 兜底
                rating = _first_number(biz.get("rating")) or _first_number(p.get("rating"))
                # 营业时间：open_time 更规整（可解析），opentime2 更详细（含"周一闭馆"等，供展示）
                open_time = _first_text(biz.get("open_time"))
                open_time_text = _first_text(biz.get("opentime2"))

                result.append({
                    "name": p.get("name", ""),
                    "lat": lat,
                    "lng": lng,
                    "typecode": tc,
                    "rating": rating,
                    # 注意：address / business_area 实测都可能是空列表 [] 而不是字符串，
                    # 必须归一，否则消费方会渲染出 "[]" 这种字面量
                    "address": _first_text(p.get("address")),
                    "tel": _first_text(p.get("tel")),
                    # ---- 以下字段此前被丢弃，实际都可直接使用 ----
                    "category": _first_text(p.get("type")),          # "风景名胜;风景名胜;国家级景点"
                    "business_area": _first_text(p.get("business_area")),  # 商圈，酒店选址要用
                    "open_time": open_time,                           # "09:00-16:30" / "24小时营业"
                    "open_time_text": open_time_text or open_time,    # 展示用完整文本
                    "biz_cost": _first_number(biz.get("cost")),       # 人均消费，餐饮类实测 24/25 有值
                    "photos": _extract_photos(p.get("photos")),
                    "entr_location": p.get("entr_location", ""),      # 景区出入口坐标
                })
            # 只缓存非空结果，避免失败结果被缓存 24h
            if result:
                set_cache(city, cache_key, result)
            return result
    except Exception as e:
        print(f"[amap] search_pois 降级: {e}")
        return []


async def _fetch_keyword(city: str, keyword: str, center_lat: float,
                         center_lng: float, max_per_keyword: int) -> List[Dict[str, Any]]:
    """拉取单个关键词（分页），返回该关键词下的 POI。"""
    collected: List[Dict[str, Any]] = []
    page = 1
    while len(collected) < max_per_keyword:
        batch = await search_pois(city, keyword, center_lat, center_lng, page=page)
        if not batch:
            break
        collected.extend(batch)
        if len(batch) < 25:
            break
        page += 1
    return collected


async def fetch_all_pois(city: str, keywords: List[str],
                         center_lat: float, center_lng: float,
                         max_per_keyword: int = 50,
                         concurrency: int = 1) -> List[Dict[str, Any]]:
    """拉取多个关键词的 POI，去重合并。

    `concurrency > 1` 时并发拉取各关键词（信号量限流，避免触发高德 QPS 限制）。
    - 规划流程保持默认 `concurrency=1`（串行），行为与之前完全一致；
    - 旅行灵感流用并发，把首屏等待从 8 秒级压到 3 秒级——它要在行程生成的
      空窗期里尽快出现内容，慢一步价值就打折。
    无论串行还是并发，结果都按 `keywords` 顺序合并，保持确定性。
    """
    if concurrency <= 1:
        batches = []
        for kw in keywords:
            batches.append(await _fetch_keyword(city, kw, center_lat, center_lng,
                                                max_per_keyword))
    else:
        sem = asyncio.Semaphore(max(2, concurrency))

        async def one(kw: str) -> List[Dict[str, Any]]:
            async with sem:
                return await _fetch_keyword(city, kw, center_lat, center_lng,
                                            max_per_keyword)

        batches = await asyncio.gather(*[one(kw) for kw in keywords],
                                       return_exceptions=False)

    # 去重合并：先按关键词顺序，再按批次内顺序 → 给定输入必然得到相同输出
    seen = set()
    all_pois: List[Dict[str, Any]] = []
    for batch in batches:
        for p in batch:
            key = (p["name"], round(p["lat"], 4), round(p["lng"], 4))
            if key in seen:
                continue
            seen.add(key)
            all_pois.append(p)
            if len(all_pois) >= max_per_keyword * len(keywords):
                return all_pois
    return all_pois


# ---------- 接口 4: 公交路径规划 ----------

async def transit_route(origin_lat: float, origin_lng: float,
                        dest_lat: float, dest_lng: float,
                        city: str) -> Optional[Dict[str, Any]]:
    """查询两点间公交路线，返回 {duration_min, distance_km}。

    降级时用 Haversine + 步行估时。
    """
    if not amap_enabled():
        dist = haversine(origin_lat, origin_lng, dest_lat, dest_lng)
        return {"duration_min": estimate_walk_minutes(dist),
                "distance_km": round(dist, 2), "mode": "walk"}

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(f"{AMAP_BASE}/direction/transit/integrated", params={
                "key": AMAP_KEY,
                "origin": f"{origin_lng},{origin_lat}",
                "destination": f"{dest_lng},{dest_lat}",
                "city": city,
            })
            resp.raise_for_status()
            data = resp.json()
            transit = data.get("route", {}).get("transits", [])
            if not transit:
                dist = haversine(origin_lat, origin_lng, dest_lat, dest_lng)
                return {"duration_min": estimate_walk_minutes(dist),
                        "distance_km": round(dist, 2), "mode": "walk"}
            best = transit[0]
            duration = int(best.get("duration", 0)) // 60
            distance = round(int(best.get("distance", 0)) / 1000, 2)
            return {"duration_min": duration, "distance_km": distance,
                    "mode": "bus"}
    except Exception as e:
        print(f"[amap] transit_route 降级: {e}")
        dist = haversine(origin_lat, origin_lng, dest_lat, dest_lng)
        return {"duration_min": estimate_walk_minutes(dist),
                "distance_km": round(dist, 2), "mode": "walk"}
