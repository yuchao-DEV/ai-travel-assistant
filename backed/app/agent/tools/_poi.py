"""工具之间共享的 POI 解析辅助。

算法类工具（`optimize_route` / `schedule_day` …）都需要把
"一串地点名"变成真实 POI 对象。让模型直接传完整 POI 结构既不现实也不安全，
所以统一在这里按名字反查高德，**只接受高置信度匹配**（复用候选筛选的匹配分档），
查不到的如实回报——绝不拿一个名字相近的别的地点顶替。
"""
import asyncio
import logging
from typing import Any, Dict, List, Optional, Tuple

from app.algorithms.candidate_selector import match_score
from app.algorithms.data_loader import raw_to_pois
from app.algorithms.models import POI
from app.amap.client import geocode, search_pois
from app.agent.results import ToolErrorCode, ToolResult

logger = logging.getLogger("app.agent.tools.poi")

# 一次最多解析多少个地点（防模型一次要几十个把配额打光）
MAX_RESOLVE = 12
_RESOLVE_CONCURRENCY = 3


async def resolve_center(city: str) -> Optional[Dict[str, Any]]:
    """取城市中心与 adcode；解析不到返回 None。"""
    name = (city or "").strip()
    if not name:
        return None
    return await geocode(name)


async def _one(name: str, city: str, lat: float, lng: float,
               sem: asyncio.Semaphore) -> Optional[POI]:
    async with sem:
        try:
            results = await search_pois(city, name, lat, lng, page=1, page_size=5)
        except Exception as exc:
            logger.warning("POI 解析失败「%s」：%s", name, exc)
            return None
    if not results:
        return None
    best = max(results, key=lambda r: (match_score(r.get("name", ""), name),
                                       r.get("rating") or 0.0))
    if match_score(best.get("name", ""), name) < 1:
        return None
    pois = raw_to_pois([best])
    return pois[0] if pois else None


async def resolve_pois(
    city: str, names: List[str]
) -> Tuple[List[POI], List[str], Optional[ToolResult]]:
    """把地点名列表解析成 POI。

    返回 ``(pois, missing_names, error)``。``error`` 非空时调用方应直接返回它
    （通常是城市解析失败）。
    """
    clean: List[str] = []
    for raw in names or []:
        token = (raw or "").strip()
        if token and token not in clean:
            clean.append(token)
    if not clean:
        return [], [], None
    if len(clean) > MAX_RESOLVE:
        clean = clean[:MAX_RESOLVE]

    center = await resolve_center(city)
    if not center:
        return [], [], ToolResult.failure(
            f"无法定位城市「{city}」", code=ToolErrorCode.NOT_FOUND,
            hint="请先用 resolve_city 确认城市名")

    sem = asyncio.Semaphore(_RESOLVE_CONCURRENCY)
    found = await asyncio.gather(*[
        _one(n, city, center["center_lat"], center["center_lng"], sem) for n in clean
    ])
    pois = [p for p in found if p is not None]
    matched_names = {p.name for p in pois}
    missing = [n for n in clean
               if not any(n in m or m in n for m in matched_names)]
    return pois, missing, None


def poi_brief(poi: POI) -> Dict[str, Any]:
    """POI 的简要表示（给模型看的字段，不含内部评分细节）。"""
    return {
        "name": poi.name,
        "type": poi.poi_type,
        "lat": poi.lat,
        "lng": poi.lng,
        "rating": poi.rating,
        "cost": poi.cost,
        "cost_source": poi.cost_source,
        "open_hours": poi.open_hours or "",
        "address": poi.address or "",
        "business_area": poi.business_area or "",
    }
