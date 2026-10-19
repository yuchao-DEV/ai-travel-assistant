"""地理与城市类工具：``resolve_city`` / ``get_city_intro``。

Agent 的第一步几乎总是"把用户说的地名变成确定的城市"——用户会说"杭州""杭城"
"浙江省会"，而后续所有工具都需要一个确定的城市名与中心坐标。
"""
import logging
from typing import Any, Dict

from app.agent.registry import tool
from app.agent.results import ToolErrorCode, ToolResult
from app.amap.client import geocode
from app.services.city_registry import list_cities

logger = logging.getLogger("app.agent.tools.geo")


@tool(core=True, timeout=15.0)
async def resolve_city(city: str) -> ToolResult:
    """把用户口中的地名解析成确定的城市（含中心坐标与行政区划码）。

    city: 城市名，如「杭州」。用户可能说别称或加后缀（「杭州市」），
    本工具会去掉常见后缀再解析。解析不到时不要臆测，改用其他方式询问用户。
    """
    name = (city or "").strip()
    for suffix in ("市", "县", "区"):
        if len(name) > 2 and name.endswith(suffix):
            name = name[:-1]
            break
    if not name:
        return ToolResult.failure("城市名为空", code=ToolErrorCode.INVALID_ARGS,
                                  hint="请向用户确认具体目的地城市")
    geo = await geocode(name)
    if not geo:
        known = list_cities().get("hot") or []
        return ToolResult.failure(
            f"无法定位城市「{city}」",
            code=ToolErrorCode.NOT_FOUND,
            hint=f"请让用户确认城市名；系统常用的城市有：{'、'.join(known[:12])}",
        )
    return ToolResult.success({
        "city": name,
        "adcode": geo.get("adcode", ""),
        "center_lat": geo.get("center_lat"),
        "center_lng": geo.get("center_lng"),
        # 回显高德实际解析到的地址：同名异地时用户能立刻发现
        "resolved_address": geo.get("resolved_address", ""),
        "keywords": geo.get("keywords", []),
    })


@tool(core=False, timeout=15.0)
async def get_city_intro(city: str) -> ToolResult:
    """获取一个城市的基本介绍与可选关键词（用于判断该城市适不适合某类行程）。

    city: 已确认的城市名。
    """
    name = (city or "").strip()
    if not name:
        return ToolResult.failure("城市名为空", code=ToolErrorCode.INVALID_ARGS)
    geo = await geocode(name)
    if not geo:
        return ToolResult.failure(f"无法定位城市「{city}」",
                                  code=ToolErrorCode.NOT_FOUND)
    registry = list_cities()
    return ToolResult.success({
        "city": name,
        "resolved_address": geo.get("resolved_address", ""),
        "keywords": geo.get("keywords", []),
        "is_hot": name in (registry.get("hot") or []),
        "known": name in (registry.get("all") or []),
        "note": "关键词用于向地图服务检索候选地点，不代表本系统只支持这些类型",
    })
