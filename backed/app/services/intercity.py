"""城际交通估算服务。

**为什么是"估算"而不是班次**：真实火车/航班班次属于 12306 与 OTA 的数据，
高德 Web 服务不提供；第三方要么付费、要么有合规风险。
因此这里只做**能力边界内**的事：

1. 用高德地理编码解析出发地与目的地（真实坐标，不是编的）；
2. 计算两地直线距离（Haversine）；
3. 按经验平均车速给出驾车耗时**估算**，并明确标注 `estimated=true`。

前端据此展示「距离 + 驾车预估」并给出高德导航深链，
而不是伪造一个车次号与时刻表——那样在答辩现场被追问"车次哪来的"会很难看。
"""
import logging
from typing import Any, Dict

from app.amap.client import geocode, haversine

logger = logging.getLogger("app.intercity")

# 城际驾车平均速度（km/h）：含高速与两端市区道路的经验值
AVG_DRIVE_KMH = 75.0
# 距离过近时不建议走城际（比如同城），低于此值直接返回步行口径之外的提示
MIN_INTERCITY_KM = 5.0

NOTE = "班次数据未接入（铁路/航班需 12306 或 OTA 授权），此处为两地距离与驾车耗时估算"


def _estimate_drive_minutes(distance_km: float) -> int:
    """按经验平均车速估算驾车耗时（分钟）。"""
    return max(1, int(round(distance_km / AVG_DRIVE_KMH * 60)))


def build_navigation_url(origin: Dict[str, Any], dest: Dict[str, Any]) -> str:
    """高德 URI 导航深链（手机端可直接唤起高德 App）。"""
    return (
        "https://uri.amap.com/navigation"
        f"?from={origin['lng']},{origin['lat']},{origin['name']}"
        f"&to={dest['lng']},{dest['lat']},{dest['name']}"
        "&mode=car&policy=1&src=travel-assistant&coordinate=gaode"
    )


async def intercity(origin_name: str, dest_name: str) -> Dict[str, Any]:
    """估算两地城际距离与驾车耗时。

    返回结构：
    {
      "ok": bool,                     # 是否成功解析出两地坐标
      "origin": {name, lat, lng} | None,
      "dest":   {name, lat, lng} | None,
      "distance_km": float | None,
      "drive_minutes": int | None,
      "estimated": True,
      "mode": "drive",
      "navigation_url": str | None,
      "note": str
    }
    """
    result: Dict[str, Any] = {
        "ok": False,
        "origin": None,
        "dest": None,
        "distance_km": None,
        "drive_minutes": None,
        "estimated": True,
        "mode": "drive",
        "navigation_url": None,
        "note": NOTE,
    }

    origin_name = (origin_name or "").strip()
    dest_name = (dest_name or "").strip()
    if not origin_name or not dest_name:
        result["note"] = "缺少出发地或目的地，无法估算城际交通"
        return result

    try:
        origin = await geocode(origin_name)
        dest = await geocode(dest_name)
    except Exception as exc:  # 地理编码异常不应让整个页面失败
        logger.warning("城际地理编码失败：%s", exc)
        result["note"] = "地理编码服务暂不可用，无法估算城际交通"
        return result

    if not origin or not dest:
        missing = []
        if not origin:
            missing.append(origin_name)
        if not dest:
            missing.append(dest_name)
        result["note"] = f"未能识别：{'、'.join(missing)}（可换用更明确的城市名）"
        return result

    origin_info = {
        "name": origin_name,
        "lat": origin["center_lat"],
        "lng": origin["center_lng"],
        # 高德实际解析到的地址：同名异地时用户能一眼看出解析错了
        "resolved_address": origin.get("resolved_address", ""),
    }
    dest_info = {
        "name": dest_name,
        "lat": dest["center_lat"],
        "lng": dest["center_lng"],
        "resolved_address": dest.get("resolved_address", ""),
    }
    distance = haversine(
        origin_info["lat"], origin_info["lng"], dest_info["lat"], dest_info["lng"]
    )

    result.update({
        "ok": distance >= MIN_INTERCITY_KM,
        "origin": origin_info,
        "dest": dest_info,
        "distance_km": round(distance, 1),
        "drive_minutes": _estimate_drive_minutes(distance),
        "navigation_url": build_navigation_url(origin_info, dest_info),
    })
    if distance < MIN_INTERCITY_KM:
        result["note"] = "两地距离很近，无需城际交通安排"
    return result
