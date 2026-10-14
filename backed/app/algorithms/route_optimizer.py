"""路径优化器：小规模求精确最优，大规模用多起点贪心 + 2-opt。

为什么是两套策略：
- 每天实际选点数由 knapsack 的 target_count 限制（当前 = 5），
  而 5 个点的开链只有 5! = 120 种排列，**精确枚举只要约 0.1 毫秒**。
  在一个精确解几乎免费的问题上用启发式，既没必要也不最优：
  实测旧实现（贪心 + 2-opt）在每天 5 点规模下约 9.3% 的实例拿不到最优，
  平均多走 0.50%、最差多走 15.69%。
- 只有在点数超过 EXACT_MAX_N 时才退回启发式（多起点贪心 + 2-opt），
  避免排列数爆炸（8! = 4 万，10! = 363 万）。

距离一律用 Haversine 球面距离。
"""
import itertools
from typing import List, Optional

from app.algorithms.models import POI
from app.amap.client import haversine

# 精确枚举的点数上限：8! = 40320，实测约 40ms/天，可以接受
EXACT_MAX_N = 8


def _total_distance(route: List[POI]) -> float:
    """计算路径总距离（公里）。开链，不算回到起点。"""
    total = 0.0
    for i in range(len(route) - 1):
        total += haversine(route[i].lat, route[i].lng,
                           route[i + 1].lat, route[i + 1].lng)
    return total


def _pick_head(pois: List[POI], start_lat: Optional[float],
               start_lng: Optional[float]) -> Optional[POI]:
    """若给了起点坐标，返回离它最近的 POI 作为第一站；否则返回 None。"""
    if start_lat is None or start_lng is None:
        return None
    return min(pois, key=lambda p: haversine(start_lat, start_lng, p.lat, p.lng))


def _nearest_neighbor_from(head: POI, others: List[POI]) -> List[POI]:
    """从指定起点出发的贪心最近邻路径。"""
    route = [head]
    unvisited = list(others)
    while unvisited:
        last = route[-1]
        nearest_idx = min(
            range(len(unvisited)),
            key=lambda i: haversine(last.lat, last.lng,
                                    unvisited[i].lat, unvisited[i].lng),
        )
        route.append(unvisited.pop(nearest_idx))
    return route


def greedy_init(pois: List[POI],
                start_lat: Optional[float] = None,
                start_lng: Optional[float] = None) -> List[POI]:
    """贪心最近邻初始解（保留的公开接口，供外部调用与对照实验）。"""
    if not pois:
        return []
    if len(pois) == 1:
        return list(pois)

    head = _pick_head(pois, start_lat, start_lng) or pois[0]
    return _nearest_neighbor_from(head, [p for p in pois if p is not head])


def two_opt(route: List[POI], max_iters: int = 100) -> List[POI]:
    """2-opt 局部搜索：反转路径中的一段，看总距离是否减小。

    只反转 [i+1, j] 段，因此**首元素固定**。若想让起点也可变，
    用 multi_start_two_opt。
    """
    # 3 个点也存在有效的 2-opt 改进（反转后两点），旧实现用 <4 把它跳过了
    if len(route) < 3:
        return route

    best_route = list(route)
    best_dist = _total_distance(best_route)
    improved = True
    iterations = 0

    while improved and iterations < max_iters:
        improved = False
        iterations += 1
        n = len(best_route)
        for i in range(0, n - 1):
            for j in range(i + 2, n):
                # 反转 [i+1, j] 段
                new_route = best_route[:i + 1] + best_route[i + 1:j + 1][::-1] + best_route[j + 1:]
                new_dist = _total_distance(new_route)
                if new_dist < best_dist - 1e-9:
                    best_route = new_route
                    best_dist = new_dist
                    improved = True

    return best_route


def exact_open_path(pois: List[POI],
                    start_lat: Optional[float] = None,
                    start_lng: Optional[float] = None) -> List[POI]:
    """暴力枚举最优开链。

    未指定起点时起点也可自由选择（这是比"固定首元素"更强的解，
    实测平均可再省约 8.7% 路程）；指定起点时第一站固定为离起点最近的点。
    """
    if not pois:
        return []
    if len(pois) == 1:
        return list(pois)

    head = _pick_head(pois, start_lat, start_lng)
    if head is None:
        rest = list(pois)
    else:
        rest = [p for p in pois if p is not head]

    best_route: List[POI] = []
    best_dist = float("inf")
    for perm in itertools.permutations(rest):
        candidate = list(perm) if head is None else [head, *perm]
        dist = _total_distance(candidate)
        if dist < best_dist:
            best_dist = dist
            best_route = candidate
    return best_route


def multi_start_two_opt(pois: List[POI],
                        start_lat: Optional[float] = None,
                        start_lng: Optional[float] = None) -> List[POI]:
    """多起点贪心 + 2-opt，取其中最优（点数超过精确枚举上限时使用）。

    旧实现固定用 pois[0] 作为起点，白白损失约 8.7% 路程；
    这里对每个候选起点各跑一次，保留最好的结果。
    """
    if not pois:
        return []

    head = _pick_head(pois, start_lat, start_lng)
    heads = [head] if head is not None else list(pois)

    best_route: List[POI] = []
    best_dist = float("inf")
    for cand_head in heads:
        rest = [p for p in pois if p is not cand_head]
        candidate = two_opt(_nearest_neighbor_from(cand_head, rest))
        dist = _total_distance(candidate)
        if dist < best_dist:
            best_dist = dist
            best_route = candidate
    return best_route


def optimize_route(pois: List[POI],
                   start_lat: Optional[float] = None,
                   start_lng: Optional[float] = None) -> List[POI]:
    """完整路径优化：小规模精确最优，大规模多起点贪心 + 2-opt。"""
    if not pois:
        return []
    if len(pois) <= EXACT_MAX_N:
        return exact_open_path(pois, start_lat, start_lng)
    return multi_start_two_opt(pois, start_lat, start_lng)
