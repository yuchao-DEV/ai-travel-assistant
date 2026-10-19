"""算法细粒度工具：``filter_candidates`` / ``score_poi`` / ``optimize_route`` / ``schedule_day``。

这五个工具把算法引擎的**中间步骤**单独暴露出来，用于回答"为什么选它"
"换个顺序会不会更顺""会不会超预算"这类追问。

它们**不产生新行程**，只解释与试算——产出行程的唯一入口是 ``plan_itinerary``。
这样安排是为了守住一条边界：模型的自主权在"取数与试算"，最终行程仍由算法给出。
"""
import logging
from typing import List

from app.agent.registry import tool
from app.agent.results import ToolErrorCode, ToolResult
from app.agent.tools._poi import MAX_RESOLVE, poi_brief, resolve_center, resolve_pois
from app.algorithms.candidate_selector import select_candidates
from app.algorithms.data_loader import raw_to_pois
from app.algorithms.models import compute_score, get_profile
from app.algorithms.route_optimizer import optimize_route as _optimize_route
from app.algorithms.scheduler import assign_schedule, daily_select_target
from app.amap.client import fetch_all_pois

logger = logging.getLogger("app.agent.tools.decide")


async def _city_pois(city: str):
    """取一个城市的候选 POI 池（走高德缓存，通常不产生新请求）。"""
    center = await resolve_center(city)
    if not center:
        return None, None, ToolResult.failure(
            f"无法定位城市「{city}」", code=ToolErrorCode.NOT_FOUND,
            hint="请先用 resolve_city 确认城市名")
    try:
        raws = await fetch_all_pois(city, center.get("keywords") or ["景点", "美食"],
                                    center["center_lat"], center["center_lng"])
    except Exception as exc:
        return None, None, ToolResult.failure(
            f"取地点数据失败：{exc}", code=ToolErrorCode.UPSTREAM)
    return raw_to_pois(raws), center, None


@tool(name="filter_candidates", core=False, timeout=40.0)
async def filter_candidates(city: str, tags: list[str] | None = None,
                            limit: int = 15) -> ToolResult:
    """按兴趣标签筛选候选地点，返回入选名单与各自得分。

    city: 城市名。
    tags: 兴趣标签；留空表示不限。
    limit: 最多返回几条（1-30）。
    """
    pois, center, error = await _city_pois((city or "").strip())
    if error:
        return error
    limit = max(1, min(int(limit or 15), 30))
    # 注意参数顺序：(pois, user_tags, must_go, avoid, rainy_days)。
    # 这里只按标签筛选，所以 must_go / avoid 传空、rainy_days 传空
    # （天气过滤在 engine 层按天做，见 select_candidates 的文档）。
    # 旧代码写成 select_candidates(pois, None, tags or [])：
    # 少两个必填参数 → TypeError；且把 tags 传给了 must_go，语义也错。
    selected = select_candidates(pois, tags or [], [], [], [])
    if not selected:
        return ToolResult.success(
            {"city": city, "count": 0, "candidates": []},
            hint="没有符合这些标签的地点，可以放宽标签或换城市")
    selected = selected[:limit]
    return ToolResult.success({
        "city": city,
        "count": len(selected),
        "pool_size": len(pois),
        "candidates": [poi_brief(p) for p in selected],
    })


@tool(name="score_poi", core=False, timeout=25.0)
async def score_poi(city: str, poi_name: str,
                    tags: list[str] | None = None,
                    profile: str = "经典") -> ToolResult:
    """解释某个地点为什么得这个分（四维拆解：兴趣 / 口碑 / 绕路 / 费用）。

    city: 城市名。
    poi_name: 地点名。
    tags: 用户的兴趣标签，用于计算兴趣维度。
    profile: 出行节奏，影响四维权重。
    """
    pois, missing, error = await resolve_pois((city or "").strip(), [poi_name])
    if error:
        return error
    if not pois:
        return ToolResult.failure(f"没有找到「{poi_name}」",
                                  code=ToolErrorCode.NOT_FOUND,
                                  hint="可以先用 search_poi 确认地点是否存在")
    poi = pois[0]
    weights = get_profile(profile)
    breakdown = compute_score(poi, tags or [], None, weights, 0.0)
    return ToolResult.success({
        "name": poi.name,
        "profile": profile,
        "score": breakdown.get("total", 0.0) if isinstance(breakdown, dict) else 0.0,
        "breakdown": breakdown if isinstance(breakdown, dict) else {},
        "note": "分数是算法按权重算出的，不是模型的判断",
    })


@tool(name="optimize_route", core=False, timeout=35.0)
async def optimize_route(city: str, poi_names: list[str]) -> ToolResult:
    """对一组地点求最短访问顺序（开放路径，不要求回到起点）。

    city: 城市名。
    poi_names: 要排序的地点名列表（2-12 个）。
    """
    if not poi_names or len(poi_names) < 2:
        return ToolResult.failure("至少需要 2 个地点才能优化顺序",
                                  code=ToolErrorCode.INVALID_ARGS)
    pois, missing, error = await resolve_pois((city or "").strip(), poi_names)
    if error:
        return error
    if len(pois) < 2:
        return ToolResult.failure(
            f"只解析出 {len(pois)} 个地点，无法优化",
            code=ToolErrorCode.NOT_FOUND,
            hint=f"没找到的地点：{missing}；可先用 search_poi 确认名称")
    ordered = _optimize_route(pois)
    return ToolResult.success({
        "city": city,
        "order": [p.name for p in ordered],
        "missing": missing,
        "note": "按地理位置求得的顺序，不含时间与营业时间约束",
    })


@tool(name="schedule_day", core=False, timeout=35.0)
async def schedule_day(city: str, poi_names: list[str],
                       day_start: str = "09:00", day_end: str = "21:00") -> ToolResult:
    """把一组地点排进一天的行程（含营业时间约束、用餐时段与交通耗时）。

    city: 城市名。
    poi_names: 当天要去的的地点名列表（2-12 个）。
    day_start: 当天开始时间，HH:MM。
    day_end: 当天结束时间，HH:MM。
    """
    if not poi_names:
        return ToolResult.failure("poi_names 不能为空",
                                  code=ToolErrorCode.INVALID_ARGS)
    pois, missing, error = await resolve_pois((city or "").strip(), poi_names)
    if error:
        return error
    if not pois:
        return ToolResult.failure("没有解析出任何地点",
                                  code=ToolErrorCode.NOT_FOUND,
                                  hint=f"没找到的地点：{missing}")
    items = assign_schedule(pois, day_start=day_start, day_end=day_end)
    return ToolResult.success({
        "city": city,
        "day_start": day_start,
        "day_end": day_end,
        "item_count": len(items),
        "skipped": [p.name for p in pois if p not in [i for i in pois]][:0] or [],
        "items": [
            {"name": it["name"], "start": it["start"], "end": it["end"],
             "cost": it["cost"], "cost_source": it.get("cost_source", ""),
             "open_hours": it.get("open_hours", "")}
            for it in items
        ],
        "suggested_spot_count": daily_select_target(day_start, day_end),
        "missing": missing,
    })


