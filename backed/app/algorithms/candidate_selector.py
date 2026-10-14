"""候选筛选器：tag 匹配 + 必去硬加 + avoid 过滤 + 天气硬约束。"""
from typing import Dict, List, Tuple

from app.algorithms.models import POI, interest_match

# 无论用户选了什么兴趣标签，都保留这么多家餐厅作为候选（供每天安排用餐）
MAX_MEAL_CANDIDATES = 20


def match_score(poi_name: str, query: str) -> int:
    """必去名称与 POI 名称的匹配得分；-1 表示不匹配。

    分级而不是简单的子串包含，是为了让"只保留最匹配那一档"成为可能：
      3 = 完全相等（用户说的就是它）
      2 = POI 名以查询开头（"虎丘" → "虎丘山风景区"）
      1 = POI 名包含查询（"拙政园" → "片仔癀(拙政园店)"，多半不是用户想要的地方）
      0 = 查询包含 POI 名（用户说得比 POI 名更细）
    """
    name = (poi_name or "").lower().strip()
    want = (query or "").lower().strip()
    if not name or not want:
        return -1
    if name == want:
        return 3
    if name.startswith(want):
        return 2
    if want in name:
        return 1
    if name in want:
        return 0
    return -1


def select_candidates(
    pois: List[POI],
    user_tags: List[str],
    must_go: List[str],
    avoid: List[str],
    rainy_days: List[int],
) -> List[POI]:
    """从全部 POI 中筛选候选集。

    规则：
    1. avoid 中的名称直接过滤
    2. must_go 中的名称强制加入（即使 tag 不匹配）
    3. tag 匹配度 > 0 的加入候选
    4. 天气硬约束在 engine 层按天过滤（此处不处理，留给 engine）

    rainy_days 参数保留用于未来扩展，当前在 engine 层处理。
    """
    avoid_lower = [a.lower().strip() for a in avoid if a.strip()]
    must_go_lower = [m.lower().strip() for m in must_go if m.strip()]

    # ---- 必去：每个名称只保留"匹配最好那一档"的点 ----
    # 旧实现是任意子串命中就全部标为必去，于是：
    #   "拙政园" → 连"片仔癀(拙政园店)"（一家药店）也被强制排进行程
    #   "虎丘"   → 虎丘塔、虎丘湿地公园一起被强制加入，行程被塞满
    # 现在按得分分档，只取该名称能达到的最高档；同档内全部保留
    # （这样"西湖"仍可对应西湖景区内的多个点，而明显更差的匹配会被丢掉）。
    best_tier: Dict[str, int] = {}
    for poi in pois:
        name_lower = poi.name.lower().strip()
        if any(av in name_lower for av in avoid_lower):
            continue
        for want in must_go_lower:
            score = match_score(name_lower, want)
            if score > best_tier.get(want, -1):
                best_tier[want] = score

    must_go_pois: List[POI] = []
    must_go_names = set()
    for poi in pois:
        name_lower = poi.name.lower().strip()
        if any(av in name_lower for av in avoid_lower):
            continue
        for want in must_go_lower:
            score = match_score(name_lower, want)
            if score >= 0 and score == best_tier.get(want):
                poi.must_go = True     # 硬约束标记：后端选点阶段不得因预算/评分丢弃
                # 注意：**不要**改写 poi.poi_type。旧实现在这里把它强制设成 "spot"，
                # 会把第一层刚判定好的餐厅/酒店类型抹掉。
                must_go_pois.append(poi)
                must_go_names.add(poi.name)
                break

    # ---- 其余候选：tag 匹配 ----
    candidates: List[POI] = []
    for poi in pois:
        name_lower = poi.name.lower().strip()
        if any(av in name_lower for av in avoid_lower):
            continue
        if poi.name in must_go_names:
            continue
        if user_tags and interest_match(poi.tags, user_tags) > 0:
            candidates.append(poi)
        elif not user_tags:
            # 用户未选 tag，全部纳入候选
            candidates.append(poi)

    # 合并必去 POI（去重）
    seen_names = {p.name for p in candidates}
    for p in must_go_pois:
        if p.name not in seen_names:
            candidates.append(p)

    # ---- 餐厅：用餐是刚需，不按兴趣标签过滤 ----
    # 旧行为下用户只选「历史/自然」时，餐厅因 interest_match=0 被整体剔除，
    # 于是行程里一天都没有用餐安排，餐饮消费也完全不进预算。
    # 这里始终保留评分较高的一批餐厅作为候选（engine 会据此保证每天有用餐点）。
    if user_tags:
        seen_names = {p.name for p in candidates}
        meals = [
            p for p in pois
            if p.poi_type == "restaurant"
            and p.name not in seen_names
            and not any(av in p.name.lower().strip() for av in avoid_lower)
        ]
        meals.sort(key=lambda p: (-p.rating, p.name))
        candidates.extend(meals[:MAX_MEAL_CANDIDATES])

    return candidates


def filter_indoor_for_rain(candidates: List[POI], is_rainy: bool,
                            must_go_names: List[str]) -> List[POI]:
    """雨天过滤：只留室内 POI，但 must_go 即使室外也保留。

    **这是雨天约束唯一生效的实现**（engine 调用它）。
    历史遗留的 ``filter_by_weather()`` 已删除：它的条件因运算符优先级实际等价于
    ``p.indoor``，既没有 must_go 保护、又和本函数重名同义，留着只会让 Agent
    误调一个语义为假的函数。
    """
    if not is_rainy:
        return candidates
    must_lower = [m.lower().strip() for m in must_go_names]
    result = []
    for p in candidates:
        if p.indoor:
            result.append(p)
        elif any(m in p.name.lower() for m in must_lower):
            result.append(p)  # 必去景点即使室外雨天也保留
    return result
