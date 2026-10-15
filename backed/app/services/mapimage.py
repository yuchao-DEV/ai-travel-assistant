"""行程路线缩略图服务（高德静态地图）。

用在行程总览卡里：Bottom Sheet 拉到全屏时地图被盖住，缩略图让用户仍能看到路线全貌。

关于高德静态地图 API 的几个**实测结论**（都踩过坑，记下来避免重蹈）：
1. `markers` 的尺寸取值是 `small` / `mid` / `large`——**不是 `medium`**，写 medium 会返回 UNKNOWN_ERROR；
2. `paths` 必须给 **5 个字段**：`weight,color,transparency,fillcolor,fillTransparency:lng,lat;...`，少给字段会失败；
3. **多组标注/路径不能用 `|` 拼接**（无论字面量还是 %7C 都会 UNKNOWN_ERROR），
   正确做法是**重复同名参数**：`markers=A&markers=B`；
4. 该接口有 QPS 限制（超出返回 `CUQPS_HAS_EXCEEDED_THE_LIMIT`），因此这里加了进程内缓存。

另外：静态地图用的是后端 Web 服务 Key，Key 不会暴露到前端。
"""
import hashlib
import logging
import math
import time
from typing import Dict, List, Optional, Tuple

import httpx

from app.config import AMAP_KEY, AMAP_BASE

logger = logging.getLogger("app.mapimage")

# 与前端地图一致的每日配色
DAY_COLORS = ["0x4A7DFF", "0x7B68EE", "0xF0A020", "0x4A8C5E", "0xD65D4A", "0x08979C", "0xC41D7F"]

MAX_POINTS = 60          # 超过这个点数缩略图已经看不清，直接截断
CACHE_TTL = 3600         # 秒
_cache: Dict[str, Tuple[float, bytes]] = {}


def parse_point_groups(points: str, day_sizes: str = "") -> List[List[Tuple[float, float]]]:
    """解析前端传来的点位串。

    格式：`points=lat,lng;lat,lng;lat,lng`，`day_sizes=2,1` 表示前 2 个为第 1 天、后 1 个为第 2 天。
    非法坐标直接跳过（容错优先，宁可少画一个点也不要 500）。
    """
    flat: List[Tuple[float, float]] = []
    for chunk in (points or "").split(";"):
        chunk = chunk.strip()
        if not chunk:
            continue
        parts = chunk.split(",")
        if len(parts) != 2:
            continue
        try:
            lat, lng = float(parts[0]), float(parts[1])
        except ValueError:
            continue
        if not (-90 <= lat <= 90 and -180 <= lng <= 180):
            continue
        flat.append((lat, lng))
        if len(flat) >= MAX_POINTS:
            break

    if not flat:
        return []

    sizes: List[int] = []
    for piece in (day_sizes or "").split(","):
        piece = piece.strip()
        if piece.isdigit() and int(piece) > 0:
            sizes.append(int(piece))

    if not sizes:
        return [flat]

    groups: List[List[Tuple[float, float]]] = []
    cursor = 0
    for size in sizes:
        if cursor >= len(flat):
            break
        groups.append(flat[cursor:cursor + size])
        cursor += size
    if cursor < len(flat):           # 剩余点归到最后一天，避免丢点
        if groups:
            groups[-1].extend(flat[cursor:])
        else:
            groups.append(flat[cursor:])
    return [g for g in groups if g]


def _zoom_for(span_deg: float, size_px: int) -> int:
    """按经纬跨度与图片像素宽度估算合适的缩放级别。

    静态地图在缩放 z 下，360° 经度对应 256·2^z 像素：
        span / size_px = 360 / (256 · 2^z)  →  z = log2(360 · size_px / (256 · span))

    传入的 span 已经带了边距，因此这里直接下取整即可，
    不要再额外减档——否则路线会缩成画面中间的一小块。
    """
    if span_deg <= 0:
        return 14
    zoom = math.log2(360.0 * size_px / (256.0 * span_deg))
    return max(3, min(16, int(math.floor(zoom))))


def _build_params(groups: List[List[Tuple[float, float]]], width: int, height: int) -> List[Tuple[str, str]]:
    """构造静态地图请求参数（重复同名参数表示多组）。"""
    lats = [p[0] for g in groups for p in g]
    lngs = [p[1] for g in groups for p in g]
    center_lat = (min(lats) + max(lats)) / 2
    center_lng = (min(lngs) + max(lngs)) / 2
    # 留边距，避免端点贴边被裁掉。
    # 纬度方向在墨卡托投影下会被拉长 1/cos(φ)，高纬度城市需要额外余量。
    lat_span = (max(lats) - min(lats))
    lng_span = (max(lngs) - min(lngs))
    mercator_lat_span = lat_span / max(math.cos(math.radians(center_lat)), 0.3)
    span = max(mercator_lat_span, lng_span) * 1.5

    params: List[Tuple[str, str]] = [
        ("location", f"{round(center_lng, 6)},{round(center_lat, 6)}"),
        ("zoom", str(_zoom_for(span, width))),
        ("size", f"{width}*{height}"),
        ("scale", "2"),
    ]
    for index, group in enumerate(groups):
        color = DAY_COLORS[index % len(DAY_COLORS)]
        coord = ";".join(f"{round(lng, 6)},{round(lat, 6)}" for lat, lng in group)
        # 多组必须用重复参数，不能用 | 拼接
        params.append(("markers", f"mid,{color},{index + 1}:{coord}"))
        if len(group) > 1:
            params.append(("paths", f"5,{color},1,{color},0.2:{coord}"))
    return params


async def route_thumbnail(groups: List[List[Tuple[float, float]]],
                          width: int = 750, height: int = 420) -> Optional[bytes]:
    """请求静态地图，返回 PNG 字节；失败返回 None（调用方隐藏缩略图即可）。"""
    if not groups or not AMAP_KEY:
        return None

    width = max(200, min(1024, width))
    height = max(120, min(1024, height))
    params = _build_params(groups, width, height)

    cache_key = hashlib.sha256(
        ("|".join(f"{k}={v}" for k, v in params)).encode("utf-8")
    ).hexdigest()
    hit = _cache.get(cache_key)
    if hit and time.time() - hit[0] < CACHE_TTL:
        return hit[1]

    url = f"{AMAP_BASE}/staticmap"
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.get(url, params=[*params, ("key", AMAP_KEY)])
        content_type = resp.headers.get("content-type", "")
        if resp.status_code != 200 or not content_type.startswith("image/"):
            logger.warning("静态地图返回非图片：HTTP %s %s", resp.status_code, resp.text[:160])
            return None
        _cache[cache_key] = (time.time(), resp.content)
        # 简单清理，避免长期运行后无限增长
        if len(_cache) > 200:
            oldest = sorted(_cache.items(), key=lambda kv: kv[1][0])[:100]
            for key, _ in oldest:
                _cache.pop(key, None)
        return resp.content
    except Exception as exc:
        logger.warning("静态地图请求失败：%s", exc)
        return None
