"""算法引擎：编排完整行程规划流程。

流程：
1. 数据层：高德 POI 拉取 + typecode_map 富化 + 天气查询
2. 候选筛选：tag 匹配 + 必去硬加 + avoid 过滤
3. 天气硬约束：雨天只留 indoor（必去除外）
4. KMeans 聚类：分到每天
5. 每日 knapsack：预算容量内选点
6. 路径排序：贪心 + 2-opt
7. 时间排程：营业时间 + 用餐时段 + 交通耗时
8. 预算校验：超支则删低分点重跑
9. 输出 ItineraryVO 结构（不含 LLM 润色）
"""
from typing import List, Optional

import logging

logger = logging.getLogger("app.engine")

# 一天最多几顿（午餐、晚餐，外加一次小食）。见 _cap_meals 的说明。
MAX_MEALS_PER_DAY = 3

from app.algorithms.candidate_selector import (
    filter_indoor_for_rain,
    select_candidates,
)
from app.algorithms.cluster import kmeans
from app.algorithms.data_loader import raw_to_pois
from app.algorithms.selector import select_by_time
from app.algorithms.models import POI, compute_score, get_profile
from app.algorithms.route_optimizer import optimize_route
from app.algorithms.scheduler import assign_schedule, daily_select_target
from app.schemas import (COMPANION_AVOID, COMPANION_FOOD, COMPANION_PREFER,
                         COMPANION_SPOT_DELTA, COMPANION_FOOD_BONUS,
                         COMPANION_PREFER_BONUS, DEFAULT_PROFILE, LEGACY_PROFILES,
                         PACE_SPOT_DELTA, PROFILE_TIME)
from app.amap.client import (
    fetch_all_pois,
    geocode,
    get_weather,
    haversine,
)
from app.config import POI_POOL_AUGMENT, amap_enabled
from app.services.poi_pool import augment_candidates
from app.services.poi_prices import apply_known_prices
from app.schemas import (
    DailyPlan,
    ItineraryItem,
    ItineraryVO,
    PlanRequestDTO,
    ScoreBreakdown,
    Transport,
)


async def solve(req: PlanRequestDTO) -> ItineraryVO:
    """主入口：接收规划请求，返回结构化行程。"""
    weights = get_profile(req.profile)

    # ---- 1. 数据层 ----
    city_info = await geocode(req.city)
    if not city_info:
        # 城市找不到，返回空行程（走降级模式）
        return _fallback_itinerary(req)

    adcode = city_info.get("adcode", "")
    center_lat = city_info["center_lat"]
    center_lng = city_info["center_lng"]
    keywords = list(city_info.get("keywords", ["景点", "美食"]))

    # 「购物中心」刻意**不放进种子关键词**：
    # 一个购物中心关键词每城市抓 2 页 × 25 条 = 50 个商场，而商场评分普遍
    # 4.7–4.9，compute_score 里评分权重占 30–40%，结果商场挤掉真正的景点、
    # 把空位填满——线上出现过「Day1 全是 SM 广场、Day3 全是大悦城/iOiMALL」的
    # 行程。它不是旅游目的地分类，只是用户偶尔想逛街。
    # 所以只在用户**主动选了「购物」标签**时才加载。
    tags = list(getattr(req, "tags", None) or [])
    if "购物" in tags and "购物中心" not in keywords:
        keywords.append("购物中心")

    weather = await get_weather(adcode)
    # 判断哪些天是雨天
    rainy_days = [i for i, w in enumerate(weather) if "雨" in w]

    # 高德 POI 拉取（带缓存）
    price_stat: dict = {}
    pool_stat: dict = {}
    if amap_enabled():
        raw_pois = await fetch_all_pois(req.city, keywords, center_lat, center_lng)
        pois = raw_to_pois(raw_pois)
        # 用知识库的景点清单扩充候选池。
        # 高德关键词搜索有取样上限且偏市中心，实测北京的明十三陵、北京动物园、
        # 清华大学都进不了候选池——而知识库恰好知道"这个城市哪些景点值得去"，
        # 所以由知识库出清单、高德出坐标。没有语料的城市自动跳过。
        if POI_POOL_AUGMENT:
            try:
                pois, pool_stat = await augment_candidates(
                    req.city, pois, center_lat, center_lng)
            except Exception as exc:   # 扩充是增强项，失败不能影响出行程
                logger.warning("候选池扩充失败，按原候选继续：%s", exc)
        # 票价：**无条件**用语料的真实票价覆盖。
        #
        # 从前这段被 USE_CORPUS_PRICES 开关包着（默认 false），意味着真实票价
        # 从来没被用上；同时 _guess_cost() 在造预留值。现在预留值删了，
        # 真实票价必须接上，否则门票就永远无数据。
        # 只写有可靠来源的：KIND_TICKET / KIND_FREE；其余留 None + cost_note。
        try:
            price_stat = apply_known_prices(pois, req.city)
        except Exception as exc:   # 票价是增强项，失败不能影响出行程
            logger.warning("票价匹配失败，本次按无票价数据继续：%s", exc)
    else:
        # 无高德 Key，走降级
        return _fallback_itinerary(req)

    if not pois:
        return _fallback_itinerary(req)

    # ---- 2. 候选筛选 ----
    candidates = select_candidates(
        pois, req.tags, req.must_go, req.avoid, rainy_days
    )

    if not candidates:
        return _fallback_itinerary(req)

    # ---- 3 & 4. 天气过滤 + KMeans 聚类 ----
    # 对每一天判断是否雨天，雨天过滤室外
    #
    # **只对景点聚类**：一天应该围绕"看什么"来组织，而不是让餐厅跟景点争名额。
    # 餐厅跟景点平等参与地理聚类时，餐饮密集的商圈会自成一天——实测杭州
    # 钱江新城那个簇是 48 餐厅 + 10 景点，背包在「历史+美食」标签下被餐厅的
    # 兴趣分挑走，于是那天排成 7 家餐厅、0 个景点。餐厅改由 _meal_pool_for_day
    # 就近补（它本来就会在当日簇里没有餐厅时从全局候选补）。
    spots_only = [p for p in candidates if p.poi_type == "spot"]
    if len(spots_only) >= req.days:
        clusters = kmeans(spots_only, req.days)
    else:
        # 景点太少（不足以分给每一天）时退回混合聚类，避免有的天没有任何候选
        clusters = kmeans(candidates, req.days)

    # ---- 5. 每日选点 + 6. 路径排序 + 7. 时间排程 ----
    #
    # 约束从「钱」换成「时间」（决策 B）。三层叠加，**顺序不能变**：
    #   ① profile 预设：时间窗 + 每日选点数增量
    #   ② pace 显式给出时**覆盖**预设增量（不叠加）
    #   ③ day_start/day_end 显式给出时**覆盖**时间窗
    #   ④ companions 非空时取 min 叠加（体力是硬约束，不相加）
    profile_name = LEGACY_PROFILES.get(req.profile, req.profile)
    preset = PROFILE_TIME.get(profile_name) or PROFILE_TIME[DEFAULT_PROFILE]

    day_start = _safe_time(req.day_start, preset["start"])
    day_end = _safe_time(req.day_end, preset["end"])

    # pace 是"预设"之上的覆盖旋钮：给了就用 pace 的增量，没给就用 profile 预设
    if req.pace:
        spot_delta = PACE_SPOT_DELTA.get(req.pace, 0)
    else:
        spot_delta = preset["spot_delta"]

    companions = list(req.companions or [])
    # 多选同行伙伴取**最保守**的那个：同时带老人和朋友时按老人算——
    # 体力约束是硬约束，热闹是软偏好。取 min 而不是相加，
    # 否则选两个就能把一天的景点数压到 1 个。（既有正确逻辑，别改坏）
    if companions:
        spot_delta += min(COMPANION_SPOT_DELTA.get(c, 0) for c in companions)
    select_target = max(1, daily_select_target(day_start, day_end) + spot_delta)
    logger.info("选点目标 %d（profile=%s 窗口 %s-%s，pace=%s，同行=%s）",
                select_target, profile_name, day_start, day_end,
                req.pace or "预设", companions or "未填")

    # 同行伙伴会**剔除**明显不合适的点（老人避开索道/漂流/登山这类）。
    # 这是硬性不合适，不是"分数低一点"，所以直接移出候选池。
    if companions:
        avoid_words = {w for c in companions for w in COMPANION_AVOID.get(c, ())}
        if avoid_words:
            before = len(candidates)
            candidates = [p for p in candidates
                          if not any(w in (p.name or "") for w in avoid_words)]
            if len(candidates) < before:
                logger.info("同行伙伴过滤掉 %d 个不合适的点（%s）",
                            before - len(candidates),
                            "/".join(sorted(avoid_words)[:4]))

    days_plans: List[DailyPlan] = []

    for day_idx in range(req.days):
        pool = clusters[day_idx]

        # 天气过滤。rainy_days 存的是"第几天"的索引集合，
        # 直接用 in 判断即可（原先还带一个 day_idx < len(rainy_days) 的冗余边界判断，
        # 语义上并不等价于"这天在不在集合里"，容易误读）。
        is_rainy = day_idx in rainy_days
        if is_rainy:
            pool = filter_indoor_for_rain(pool, True, req.must_go)

        if not pool:
            days_plans.append(DailyPlan(
                date=f"Day {day_idx + 1}",
                items=[],
                daily_summary=None,
            ))
            continue

        # 计算 score
        # 选点阶段顺序还未知，因此用当日候选池的"聚类中心"作为绕路参考基点：
        # 距离即该点相对当日活动区域中心的偏移，是绕路成本的顺序无关估计。
        # 这样 profile 的 w_detour 权重（尽览=0.50）才能真正参与选点，
        # 而不是像以前那样因为恒传 (0,0) 而整项失效。
        max_detour = 15.0  # km，单日最大绕路

        ref_lat, ref_lng = _centroid(pool)
        for poi in pool:
            score, breakdown = compute_score(
                poi, req.tags, ref_lat, ref_lng,
                max_detour, weights
            )
            # 同行伙伴的名称关键词加成。加在算法总分之后、归一化之前，
            # 让"带老人优先公园/寺庙""带小孩优先乐园/动物园"真的改变选点。
            if companions:
                score = min(1.0, score + _companion_bonus(poi.name, companions))
            poi.score = score
            poi.score_breakdown = breakdown

        # knapsack 选点。
        # 选点上限由当日时间窗推导（见 daily_select_target），并刻意多选：
        # 排程阶段会因"已闭馆/剩余时长不足"跳过部分点，多选才有补位空间，
        # 否则会出现"选了 5 个、只排了 4 个、当天 17:00 就结束"。
        selected = select_by_time(pool, target_count=select_target)

        # 用餐时段保证有餐厅：景点在兴趣维度上通常命中更多标签（interest 更高），
        # 名额容易被景点占满，导致一天下来没有任何用餐安排。
        # 餐厅候选用 _meal_pool_for_day 取——当日簇里没有时会从全局候选就近补。
        meal_pool = _meal_pool_for_day(
            pool, candidates, ref_lat, ref_lng, req, weights,
            max_detour,
        )
        selected = _ensure_meal_stop(selected, meal_pool)

        # 反向保证：一天里至少要有**一个景点**。
        # 镜像 _ensure_meal_stop 的思路——那边防"全天没有用餐"，这边防"全天没有游览"。
        # 只靠聚类是不够的：某些片区的餐厅又多又高分，背包可能一个景点都不选。
        selected = _ensure_spot_stop(selected, pool)

        # 一天最多三顿（午餐、晚餐，外加一次小食/咖啡）。
        # 实测出现「一天 7–8 家餐厅」：标签只有「美食」时候选池里全是餐厅
        # （select_candidates 会把不匹配标签的景点全部滤掉），背包于是一路排满。
        # 连着吃 7 顿不是行程，是清单——限制上限比事后删更简单也更可预期。
        selected = _cap_meals(selected, MAX_MEALS_PER_DAY)

        if not selected:
            days_plans.append(DailyPlan(
                date=f"Day {day_idx + 1}",
                items=[],
                daily_summary=None,
            ))
            continue

        # 路径优化
        route = optimize_route(selected)

        # 重新带路径顺序计算 score（detour 基于上一个点，反映真实游览顺序）
        last_lat = None
        last_lng = None
        for poi in route:
            score, breakdown = compute_score(
                poi, req.tags, last_lat, last_lng,
                max_detour, weights
            )
            poi.score = score
            poi.score_breakdown = breakdown
            last_lat = poi.lat
            last_lng = poi.lng

        # 时间排程
        items_raw = assign_schedule(route, day_start=day_start, day_end=day_end,
                                         pace=req.pace or "")

        # 构建 ItineraryItem 列表
        items = []
        for item in items_raw:
            transport = None
            if item.get("transport"):
                t = item["transport"]
                transport = Transport(
                    mode=t["mode"],
                    duration=t["duration"],
                    distance=t["distance"],
                )
            sb = item.get("score_breakdown", {})
            items.append(ItineraryItem(
                type=item["type"],
                name=item["name"],
                lat=item["lat"],
                lng=item["lng"],
                start=item["start"],
                end=item["end"],
                # cost 可能是 None（无可靠数据）。透传 None 是安全的，
                # 前端按 cost_source 门禁决定是否显示数字。
                cost=item.get("cost"),
                cost_source=item.get("cost_source") or None,
                cost_note=item.get("cost_note", ""),
                open_hours=item.get("open_hours") or None,
                photos=item.get("photos") or [],
                score=item["score"],
                score_breakdown=ScoreBreakdown(
                    interest=sb.get("interest", 0.0),
                    rating=sb.get("rating", 0.0),
                    detour=sb.get("detour", 0.0),
                ),
                transport=transport,
            ))

        days_plans.append(DailyPlan(
            date=f"Day {day_idx + 1}",
            items=items,
            daily_summary=None,
        ))

    # 天气列表补齐到天数
    weather_list = weather[:req.days] if weather else ["未知"] * req.days
    while len(weather_list) < req.days:
        weather_list.append("未知")

    return ItineraryVO(
        days=days_plans,
        profile=profile_name,
        weather=weather_list,
        city=req.city,
        description=None,
        enhanced=True,
    )


def _fallback_itinerary(req: PlanRequestDTO) -> ItineraryVO:
    """降级模式：无高德数据时返回**空行程**，并标记 enhanced=False。

    .. warning::
       这里**没有**"纯 LLM 降级"这条路——原 docstring 这么写，但 plan 服务
       从来没有实现过（属于文档与代码不符，已改正）。
       空行程会被 ``app/services/plan.py`` 拦下并抛出 503，
       绝不允许它看起来像一次成功的规划。
    """
    return ItineraryVO(
        days=[DailyPlan(date=f"Day {i + 1}", items=[])
              for i in range(req.days)],
        profile=LEGACY_PROFILES.get(req.profile, req.profile),
        weather=["未知"] * req.days,
        city=req.city,
        description=None,
        enhanced=False,
    )


def _meal_pool_for_day(pool: List[POI], all_candidates: List[POI],
                       ref_lat: Optional[float], ref_lng: Optional[float],
                       req: PlanRequestDTO, weights: dict,
                       max_detour: float) -> List[POI]:
    """当日可用的餐厅候选。

    餐厅常被 KMeans 聚到同一天的簇里，导致其它天"池里根本没有餐厅"、
    一天下来毫无用餐安排。因此当日簇里没有餐厅时，从**全局候选**里
    取离当日活动中心最近的几家，并就地补算评分（它们不在当日 pool 里，
    此前没算过 score）。
    """
    meals = [p for p in pool if p.poi_type == "restaurant"]
    if meals:
        return meals

    meals = [p for p in all_candidates if p.poi_type == "restaurant"]
    if not meals or ref_lat is None or ref_lng is None:
        return []
    meals.sort(key=lambda p: haversine(ref_lat, ref_lng, p.lat, p.lng))
    # 取就近的一批（而不是一两家），这样 _ensure_meal_stop 才有价格区间可选：
    # 只取最近的 3 家时可能都超出当日剩余预算，结果一天下来没有用餐安排。
    top = meals[:8]
    for p in top:
        score, breakdown = compute_score(p, req.tags, ref_lat, ref_lng,
                                         max_detour, weights)
        p.score = score
        p.score_breakdown = breakdown
    return top


# 用餐时段（与 scheduler 的 LUNCH/DINNER 保持一致，单位：当日分钟数）
_MEAL_WINDOWS = ((11 * 60 + 30, 13 * 60 + 30), (17 * 60 + 30, 19 * 60 + 30))


def _can_serve_meal(poi: POI) -> bool:
    """该餐厅的营业时间是否覆盖任一饭点。

    营业时间未知时不排除（宁可排上，由排程按实际时间处理）。
    """
    if not poi.open_ranges:
        return True
    for start, end in poi.open_ranges:
        for ws, we in _MEAL_WINDOWS:
            if start < we and end > ws:
                return True
    return False


def _ensure_meal_stop(selected: List[POI],
                      meal_candidates: List[POI]) -> List[POI]:
    """保证当天至少有一个用餐点（餐厅）。

    景点通常在兴趣维度上命中更多标签、interest 更高，选点名额容易被景点占满，
    结果一天下来没有任何用餐安排——既不真实，也不像一条行程。

    **不再有"预算够不够"的判断**（预算系统已移除）：直接取评分最高、
    且营业时间覆盖饭点的餐厅。若一天已经被景点排满，则腾掉一个**最低分的
    非餐厅点**来安放用餐——用餐是刚需，不该被"多一个景点"挤掉。
    补进来的点仍是普通条目、不标 must_go。
    """
    if not selected or not meal_candidates:
        return selected
    if any(p.poi_type == "restaurant" for p in selected):
        return selected

    acc = list(selected)
    # 先按评分排序；优先"营业时间覆盖饭点"的餐厅，
    # 否则可能出现"排了餐厅，但它那时已打烊"→ 被排程跳过 → 当天没有用餐。
    by_score = sorted(meal_candidates, key=lambda p: -p.score)
    meal_ready = [p for p in by_score if _can_serve_meal(p)]
    by_score = meal_ready or by_score
    droppable = sorted((p for p in acc if p.poi_type != "restaurant"),
                       key=lambda p: p.score)
    if not by_score:
        return selected
    # 直接取评分最高、且营业时间覆盖饭点的餐厅（不再有"预算够不够"的判断）
    best = by_score[0]
    # 标为必须项：排程阶段不得因"时间不够"把用餐点跳掉，
    # 否则当天排满 12 小时后刚补的餐厅会被直接丢弃。
    best.must_go = True
    # **不主动腾掉景点**：从前是因为"预算放不下"才腾，现在没有预算这个理由了。
    # 数量上限由 select_by_time 的容量与 _cap_meals 负责，不该在这里偷偷减景点。
    return acc + [best]


def _companion_bonus(name: str, companions: List[str]) -> float:
    """按同行伙伴给地点名匹配的 POI 加分（0 表示不加）。

    **这是名称关键词匹配，不是真实属性。** 高德返回的 POI 只有名称/类型/
    评分，没有"亲子友好""浪漫""低体力"这类字段，所以只能退而求其次按名字
    判断。好处是它真的改变选点结果；代价是像"XX乐园酒店"这种名字也会命中。
    等接入更丰富的地点数据后应替换掉这一层。
    """
    text = name or ""
    if not text:
        return 0.0
    bonus = 0.0
    for companion in companions:
        if any(w in text for w in COMPANION_PREFER.get(companion, ())):
            bonus += COMPANION_PREFER_BONUS
            break
    for companion in companions:
        if any(w in text for w in COMPANION_FOOD.get(companion, ())):
            bonus += COMPANION_FOOD_BONUS
            break
    return bonus


def _safe_time(value: str, default: str) -> str:
    """校验 HH:MM；不合法就用默认值。

    用户填错时间不该让整个规划失败——回退到默认时间窗并继续，
    比返回一个 422 更符合"这是个规划助手"的定位。
    """
    text = (value or "").strip()
    if len(text) == 5 and text[2] == ":":
        hh, mm = text[:2], text[3:]
        if hh.isdigit() and mm.isdigit() and 0 <= int(hh) <= 23 and 0 <= int(mm) <= 59:
            return text
    return default


def _cap_meals(selected: List[POI], limit: int) -> List[POI]:
    """限制一天里的餐厅数量，保留评分最高的几顿。

    一天三顿是常识上限（午、晚、小食）。超过就说明选点出了问题，
    与其排出一串"从 10:30 连吃到 17:48"的清单，不如只留最值得去的几家。
    """
    meals = [p for p in selected if p.poi_type == "restaurant"]
    if len(meals) <= limit:
        return selected
    keep = {id(p) for p in sorted(meals, key=lambda p: -p.score)[:limit]}
    return [p for p in selected if p.poi_type != "restaurant" or id(p) in keep]


def _ensure_spot_stop(selected: List[POI], spot_pool: List[POI]) -> List[POI]:
    """保证一天里至少有一个**景点**。

    镜像 :func:`_ensure_meal_stop`：那边防"全天没有用餐"，这边防"全天没有游览"。
    实测出现过「某天 7 家餐厅、0 个景点」——那天落在 CBD 餐饮区，聚类后池子里
    景点本就少，选点又被兴趣分挑走了，于是一整天的行程全是吃饭。

    一天完全没有游览安排是不成立的（用户要的是旅行，不是美食探店），
    所以宁可腾掉一个最低分的餐厅也要补进一个景点。
    """
    if not selected or not spot_pool:
        return selected
    if any(p.poi_type == "spot" for p in selected):
        return selected

    acc = list(selected)
    by_score = sorted(spot_pool, key=lambda p: -p.score)
    if not by_score:
        return acc
    # 腾掉一个最低分餐厅给景点让位（不再有预算判断）
    droppable = sorted((p for p in acc if p.poi_type == "restaurant"),
                       key=lambda p: p.score)
    if droppable:
        acc.remove(droppable[0])
    acc.append(by_score[0])
    return acc



def _centroid(pois: List[POI]) -> tuple:
    """当日候选池的地理中心，用作选点阶段的绕路参考基点。"""
    if not pois:
        return None, None
    n = len(pois)
    return sum(p.lat for p in pois) / n, sum(p.lng for p in pois) / n


