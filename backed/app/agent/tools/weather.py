"""天气工具：``get_weather``。

行程排程里雨天会触发"只留室内 POI"的硬约束，所以这是规划前值得先查一次的信息。
"""
import logging

from app.agent.registry import tool
from app.agent.results import ToolErrorCode, ToolResult
from app.amap.client import geocode, get_weather as _amap_weather

logger = logging.getLogger("app.agent.tools.weather")


@tool(core=True, timeout=15.0)
async def get_weather(city: str) -> ToolResult:
    """查询城市未来几天的天气（用于判断是否需要安排室内备选）。

    city: 城市名。拿不到天气时返回 ok=false，**不要臆测天气**——
    行程里写"预计晴天"而实际下雨，比不写天气更糟。
    """
    name = (city or "").strip()
    if not name:
        return ToolResult.failure("城市名为空", code=ToolErrorCode.INVALID_ARGS,
                                  hint="请先向用户确认目的地城市")
    geo = await geocode(name)
    if not geo:
        return ToolResult.failure(f"无法定位城市「{city}」",
                                  code=ToolErrorCode.NOT_FOUND)
    weather = await _amap_weather(geo.get("adcode", ""))
    if not weather:
        return ToolResult.failure(
            f"暂时拿不到「{name}」的天气",
            code=ToolErrorCode.UPSTREAM,
            hint="不要编造天气；行程里可以不提天气，或提醒用户自行查看",
        )
    rainy = [w for w in weather if "雨" in w]
    return ToolResult.success({
        "city": name,
        "forecast": weather,
        "has_rain": bool(rainy),
        "rainy_days": rainy,
        "note": "有雨时排程会自动只保留室内地点",
    })
