"""时间排程器：为每日路径分配具体时间。

考虑：
- **真实营业时间**（来自高德 biz_ext，解析为分钟区间）
- 用餐时段（午餐 11:30–13:30、晚餐 17:30–19:30）
- 景点间交通耗时
- 每种 POI 类型的典型游览时长

修复说明：旧实现只用一个统一的 09:00–21:00 活动窗口，
`DEFAULT_OPEN` / `DEFAULT_CLOSE` 两个常量定义了却从未被引用，
而高德其实返回了每个点的真实营业时间。
"""
from typing import Dict, List, Optional, Tuple

from app.algorithms.models import POI, open_window
from app.amap.client import haversine, estimate_walk_minutes

# POI 类型 → 默认游览时长（分钟）
DEFAULT_DURATIONS = {
    "spot": 120,        # 景点 2 小时
    "restaurant": 60,   # 餐厅 1 小时
    "hotel": 0,         # 酒店不排程
}

# 未知营业时间时的兜底：按"全天可游"处理，不与活动窗口冲突
UNKNOWN_OPEN_WINDOW: Optional[Tuple[int, int]] = None

# 访问一个点所需的最短停留时间，低于此值就跳过而不是硬塞
MIN_VISIT_MINUTES = 30

# 用餐时段
LUNCH_START = "11:30"
LUNCH_END = "13:30"
DINNER_START = "17:30"
DINNER_END = "19:30"
MEAL_MINUTES = 45

# 默认当日活动窗口（与 assign_schedule 的默认值保持一致）
DEFAULT_DAY_START = "09:00"
DEFAULT_DAY_END = "21:00"

# 相邻两点平均交通耗时（分钟），用于估算当日可游览点数
AVG_TRANSFER_MINUTES = 15

# 选点余量系数：排程阶段会因"已闭馆/剩余时长不足"跳过部分点，
# 因此选点必须多选一些留出补位空间，否则会出现
# "选了 5 个、实际只排了 4 个、当天 17:00 就结束"的情况。
SELECT_HEADROOM = 1.8


def _time_to_minutes(t: str) -> int:
    """HH:MM → 当日分钟数。"""
    parts = t.split(":")
    return int(parts[0]) * 60 + int(parts[1])


def daily_select_target(day_start: str = DEFAULT_DAY_START,
                        day_end: str = DEFAULT_DAY_END) -> int:
    """按当日时间窗推算"选点上限"。

    注意这里算的是**选点数量**，不是最终行程条目数：
    真正每天排几个点由 :func:`assign_schedule` 按营业时间与剩余时长决定。
    之所以要多选（乘 ``SELECT_HEADROOM``），是因为排程会跳过闭馆或时间不够的点，
    若不预留下限就会出现"当天远早于 21:00 就结束、预算也花不掉"。

    推导：可用时长 = 窗口 - 两餐用时；单点占用 = 景点时长 + 平均交通。
    """
    window = _time_to_minutes(day_end) - _time_to_minutes(day_start)
    usable = window - MEAL_MINUTES * 2
    per_item = DEFAULT_DURATIONS["spot"] + AVG_TRANSFER_MINUTES
    capacity = max(1, usable // per_item)
    return max(3, int(round(capacity * SELECT_HEADROOM)))


def _minutes_to_time(m: int) -> str:
    """分钟 → HH:MM（跨天时按 24 小时取模，避免出现 25:30 这种时间）。"""
    m = max(0, min(m, 24 * 60 - 1))
    return f"{m // 60:02d}:{m % 60:02d}"


def _is_meal_time(t: str) -> Optional[str]:
    """判断时间是否落在用餐时段，返回 "lunch"/"dinner"/None。"""
    m = _time_to_minutes(t)
    if _time_to_minutes(LUNCH_START) <= m <= _time_to_minutes(LUNCH_END):
        return "lunch"
    if _time_to_minutes(DINNER_START) <= m <= _time_to_minutes(DINNER_END):
        return "dinner"
    return None


# 行程节奏 → 每站停留时长的缩放。
#
# 「紧凑」的真实语义是**每站待得短一点**，而不是"多选几个"——默认时间窗
# 09:00–21:00 已经吃满 12 小时上限，多选的点根本排不进去，只加数量会让
# "紧凑"变成一个选了没反应的开关（实测就是这样）。缩短停留才能真正排进更多点。
# 「宽松」则相反：多留些时间，宁可少去几个。
PACE_DURATION_SCALE = {"紧凑": 0.7, "适中": 1.0, "宽松": 1.3}


def _get_duration(poi: POI, pace: str = "") -> int:
    """获取 POI 游览时长（分钟），并按行程节奏缩放。"""
    base = DEFAULT_DURATIONS.get(poi.poi_type, 90)
    scale = PACE_DURATION_SCALE.get(pace or "", 1.0)
    return max(MIN_VISIT_MINUTES, int(round(base * scale)))


def _travel_minutes(from_lat: float, from_lng: float, poi: POI) -> Tuple[int, str, float]:
    """估算到下一个点的交通耗时，返回 (分钟, 方式, 距离km)。"""
    dist = haversine(from_lat, from_lng, poi.lat, poi.lng)
    if dist < 1.0:
        return estimate_walk_minutes(dist), "walk", dist
    if dist < 3.0:
        return int(dist / 20.0 * 60), "bus", dist      # 公交 20km/h
    return int(dist / 35.0 * 60), "subway", dist       # 地铁 35km/h


def assign_schedule(route: List[POI], day_start: str = "09:00",
                    day_end: str = "21:00", pace: str = "") -> List[Dict]:
    """为排序后的路径分配时间，并保证必须项不被时间挤掉。

    两段式：
    1. :func:`_schedule_once` 按顺序排程，时间/营业时间不够的点被跳过；
    2. 若**必须项**（``poi.must_go``，含用户指定的必去与每日用餐点）仍被跳过，
       就从已排的点里去掉分数最低的非必须项，重新排一次——直到必须项都排上
       或没有可让的位置。

    没有这一步时会出现：当日已被普通景点排满 09:00–21:00，
    刚补进来的用餐点或用户指定的必去点被直接跳过，
    于是"必去"名存实亡。
    """
    working = list(route)
    dropped: List[POI] = []
    items: List[Dict] = []
    settled = False
    for _ in range(len(route) + 1):
        items = _schedule_once(working, day_start, day_end, pace)
        kept = {it["name"] for it in items}
        missing_must = [p for p in working
                        if getattr(p, "must_go", False) and p.name not in kept]
        if not missing_must:
            settled = True
            break
        # 腾位置：去掉一个"非必须且分数最低"的已排点
        droppable = sorted([it for it in items if not it.get("must_go")],
                           key=lambda it: it.get("score", 0.0))
        if not droppable:
            return items
        drop_name = droppable[0]["name"]
        for p in working:
            if p.name == drop_name:
                dropped.append(p)
                break
        working = [p for p in working if p.name != drop_name]

    if not settled:
        return items

    # 回填：腾位只是为了让必须项排进去，不该让当天提前结束。
    # 按分数从高到低尝试把删掉的点加回来，只要仍不挤掉任何必须项就保留。
    for p in sorted(dropped, key=lambda x: -x.score):
        trial = working + [p]
        trial_items = _schedule_once(trial, day_start, day_end, pace)
        trial_kept = {it["name"] for it in trial_items}
        if (all(q.name in trial_kept for q in trial if getattr(q, "must_go", False))
                and len(trial_items) > len(items)):
            working = trial
            items = trial_items
    return items


def _schedule_once(route: List[POI], day_start: str = "09:00",
                   day_end: str = "21:00", pace: str = "") -> List[Dict]:
    """为排序后的路径分配时间。

    对每个点依次判断：
    1. 到达时间早于其开门时间 → 等到开门再游览；
    2. 到达时间已晚于其关门时间，或剩余可游览时长不足 `MIN_VISIT_MINUTES`
       → **跳过该点**（不排程），继续尝试后面的点；
    3. 否则按默认时长游览，超出当日窗口或关门时间时截断。

    返回 List[Dict]，每个 dict 含：
    {type, name, lat, lng, start, end, cost, cost_source, open_hours,
     score, score_breakdown, transport}
    """
    if not route:
        return []

    current_min = _time_to_minutes(day_start)
    day_end_min = _time_to_minutes(day_end)
    items: List[Dict] = []
    last_lat: Optional[float] = None
    last_lng: Optional[float] = None

    for poi in route:
        # ---- 交通耗时（仅在该点确实会被游览时才记录下来）----
        travel_min = 0
        mode = "walk"
        dist = 0.0
        if last_lat is not None:
            travel_min, mode, dist = _travel_minutes(last_lat, last_lng, poi)

        arrive = current_min + travel_min

        # ---- 用餐时段：若到达时刻落在饭点且这不是餐厅，先留出用餐时间 ----
        # 必须在"等待开门"之前处理：否则会出现"等到 19:00 开门后又加 45 分钟用餐、
        # 结果 19:45 才进去"的荒谬结果——等待时间本就应该吸收掉用餐。
        meal_type = _is_meal_time(_minutes_to_time(arrive))
        if meal_type and poi.poi_type != "restaurant":
            arrive += MEAL_MINUTES

        # ---- 营业时间约束 ----
        window = open_window(poi.open_ranges) if poi.open_ranges else UNKNOWN_OPEN_WINDOW
        if window is not None:
            open_min, close_min = window
            if arrive < open_min:
                # 到了还没开门 → 等待开门
                arrive = open_min
            if arrive >= close_min:
                # 已经关门，今天去不了 → 跳过该点
                continue
            latest_end = min(close_min, day_end_min)
        else:
            latest_end = day_end_min

        # ---- 当日窗口检查 ----
        if arrive >= day_end_min:
            break

        duration = _get_duration(poi, pace)
        end_min = arrive + duration
        if end_min > latest_end:
            # 剩余时间不够走完默认时长
            duration = latest_end - arrive
        if duration < MIN_VISIT_MINUTES:
            # 剩余时间太少，不值得塞进去
            continue
        end_min = arrive + duration

        items.append({
            "type": poi.poi_type,
            "name": poi.name,
            "lat": poi.lat,
            "lng": poi.lng,
            "start": _minutes_to_time(arrive),
            "end": _minutes_to_time(end_min),
            "cost": poi.cost,
            "cost_source": poi.cost_source,
            "cost_note": poi.cost_note,
            "open_hours": poi.open_hours,
            "photos": list(poi.photos),
            "must_go": bool(getattr(poi, "must_go", False)),
            "score": poi.score,
            "score_breakdown": poi.score_breakdown,
            "transport": ({
                "mode": mode,
                "duration": travel_min,
                "distance": round(dist, 2),
            } if last_lat is not None else None),
        })

        current_min = end_min
        last_lat = poi.lat
        last_lng = poi.lng

    return items
