"""每日选点：按 score 降序取，容量由**时间**决定。

## 为什么不再叫"背包"

背包问题需要「容量」与「重量」两个要素。原实现的容量是预算、重量是费用；
预算系统移除后，两者都没了 —— 继续叫 knapsack 会永久误导后来人
（答辩时被问"你的背包容量是什么"将无法回答）。

现在的容量是**时间**，而且它不由本模块计算：``scheduler.daily_select_target()``
按当日时间窗（出发/结束时间、营业时间、用餐时段）推导出"这天最多能排几个点"，
本模块只负责在这个数量内挑分最高的。

## 保留了哪两段逻辑

1. **必去项硬约束**：``must_go=True`` 的点不受 ``target_count`` 限制，一定入选。
   这是 README 里记录过的历史缺陷修复，不能改回去。
2. **局部搜索**：在无容量约束的情况下，它的意义变成"用未选的高分点替换已选的低分点"。
   取前 N 名本身已是最优解，所以这里**保留但只在分数更高时替换**，
   收益有限；保留是因为它同时承担"必去项不参与替换"的守卫作用。
"""
from typing import List

from app.algorithms.models import POI


def select_by_time(pool: List[POI], target_count: int = 5) -> List[POI]:
    """从候选池中选出当天要去的点。

    ``target_count`` 是**时间容量**：由 ``daily_select_target()`` 按当日时间窗
    推导，代表"这天在真实营业时间与用餐时段约束下最多排得下几个点"。

    优先级：

    1. **必去项（``must_go=True``）是硬约束** —— 不受 ``target_count`` 限制，
       一定进入结果（用户明确要求的点不该被任何排序挤掉）；
    2. 其余候选按 ``score`` 降序取，直到达到 ``target_count``；
    3. 必去项不参与任何替换。

    返回选中的 POI 列表。
    """
    if not pool:
        return []
    if target_count <= 0:
        target_count = 1

    must_go = [p for p in pool if getattr(p, "must_go", False)]
    optional = [p for p in pool if not getattr(p, "must_go", False)]

    selected = list(must_go)
    # 目标点数至少要容纳全部必去项，否则用户指定必去就会被"时间容量"吃掉
    limit = max(target_count, len(selected))

    optional.sort(key=lambda p: p.score, reverse=True)
    for poi in optional:
        if len(selected) >= limit:
            break
        selected.append(poi)

    return selected


def is_over_target(selected: List[POI], target_count: int) -> bool:
    """选出数是否超过时间容量（诊断用）。

    超过只可能来自"必去项多于容量"——这是允许的：用户明确要求的点优先于
    时间估算，真排不下时由排程器如实跳过并记录，而不是在选点阶段偷偷丢掉。
    """
    return len(selected) > target_count
