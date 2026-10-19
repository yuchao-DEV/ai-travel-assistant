"""城际交通工具：``estimate_intercity``。

**只给距离与驾车耗时估算，绝不编造车次**。真实班次需要 12306 或 OTA 授权，
拿不到就不说——这是项目一贯的纪律（见 `app/services/intercity.py` 的说明）。
"""
import logging

from app.agent.registry import tool
from app.agent.results import ToolErrorCode, ToolResult
from app.services.intercity import intercity

logger = logging.getLogger("app.agent.tools.traffic")


@tool(name="estimate_intercity", core=False, timeout=25.0)
async def estimate_intercity(origin: str, dest: str) -> ToolResult:
    """估算两座城市之间的距离与驾车耗时（不含车次与票价）。

    origin: 出发城市。
    dest: 目的城市。

    结果是**估算值**：不要把它说成"高铁 2 小时"这类班次信息。
    """
    a = (origin or "").strip()
    b = (dest or "").strip()
    if not a or not b:
        return ToolResult.failure("origin 与 dest 都不能为空",
                                  code=ToolErrorCode.INVALID_ARGS)
    try:
        data = await intercity(a, b)
    except Exception as exc:
        logger.warning("城际估算失败：%s", exc)
        return ToolResult.failure(f"城际估算失败：{exc}",
                                  code=ToolErrorCode.UPSTREAM)
    if not data or not data.get("ok"):
        note = (data or {}).get("note") or "两地中至少有一个无法定位"
        return ToolResult.failure(f"无法估算「{a}」到「{b}」：{note}",
                                  code=ToolErrorCode.NOT_FOUND)
    return ToolResult.success({
        "origin": data.get("origin"),
        "dest": data.get("dest"),
        "distance_km": data.get("distance_km"),
        "drive_minutes": data.get("drive_minutes"),
        "estimated": True,
        "navigation_url": data.get("navigation_url", ""),
        "note": data.get("note") or "班次数据未接入，此处为距离与驾车耗时估算",
    })
