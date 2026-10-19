"""地点检索工具：``search_poi`` / ``get_poi_detail``。

``search_poi`` 是 Agent 用得最多的工具——模型拿它把"西湖周边有什么"变成
具体候选；``get_poi_detail`` 用于追问某个地点的门票、营业时间、照片。
"""
import logging

from app.agent.registry import tool
from app.agent.results import ToolErrorCode, ToolResult
from app.agent.tools._poi import poi_brief, resolve_center, resolve_pois
from app.algorithms.data_loader import raw_to_pois
from app.amap.client import search_pois

logger = logging.getLogger("app.agent.tools.search")


@tool(core=True, timeout=20.0)
async def search_poi(city: str, keyword: str, limit: int = 10) -> ToolResult:
    """在城市范围内按关键词搜索真实地点（返回名称、坐标、评分、费用、营业时间）。

    city: 城市名。
    keyword: 检索词，如「博物馆」「西湖」「小吃」。
    limit: 最多返回几条（1-20）。
    """
    name = (city or "").strip()
    kw = (keyword or "").strip()
    if not name or not kw:
        return ToolResult.failure("city 与 keyword 都不能为空",
                                  code=ToolErrorCode.INVALID_ARGS)
    limit = max(1, min(int(limit or 10), 20))
    center = await resolve_center(name)
    if not center:
        return ToolResult.failure(f"无法定位城市「{city}」",
                                  code=ToolErrorCode.NOT_FOUND,
                                  hint="请先用 resolve_city 确认城市名")
    try:
        raws = await search_pois(name, kw, center["center_lat"],
                                 center["center_lng"], page=1,
                                 page_size=min(25, limit * 2))
    except Exception as exc:
        return ToolResult.failure(f"地点检索失败：{exc}",
                                  code=ToolErrorCode.UPSTREAM,
                                  hint="可以换一个关键词重试")
    if not raws:
        return ToolResult.success(
            {"city": name, "keyword": kw, "count": 0, "pois": []},
            hint="该关键词没有结果，可以换更通用的词（如「公园」「博物馆」）再试")
    pois = raw_to_pois(raws)[:limit]
    return ToolResult.success({
        "city": name,
        "keyword": kw,
        "count": len(pois),
        "pois": [poi_brief(p) for p in pois],
    })


@tool(core=False, timeout=20.0)
async def get_poi_detail(city: str, poi_name: str) -> ToolResult:
    """查一个具体地点的详情（门票、营业时间、地址、商圈、照片）。

    city: 城市名。
    poi_name: 地点名。查不到时返回失败，**不要用相近的名字顶替**。
    """
    name = (city or "").strip()
    target = (poi_name or "").strip()
    if not name or not target:
        return ToolResult.failure("city 与 poi_name 都不能为空",
                                  code=ToolErrorCode.INVALID_ARGS)
    pois, missing, error = await resolve_pois(name, [target])
    if error:
        return error
    if not pois:
        return ToolResult.failure(
            f"在「{name}」没有找到「{target}」",
            code=ToolErrorCode.NOT_FOUND,
            hint="可以先用 search_poi 确认这个地点是否存在",
        )
    poi = pois[0]
    detail = poi_brief(poi)
    detail["photos"] = list(poi.photos)[:3]
    detail["open_ranges"] = poi.open_ranges
    detail["scenic_level"] = poi.scenic_level or ""
    detail["category"] = poi.category or ""
    detail["cost_note"] = poi.cost_note or ""
    return ToolResult.success(detail)
