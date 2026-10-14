"""自实现 KMeans 聚类：按经纬度将 POI 分到每天。

使用 Lloyd 算法 + KMeans++ 初始化，纯 Python 实现，不依赖 numpy/scikit-learn。
"""
import math
import random
from typing import List, Tuple

from app.algorithms.models import POI


def _dist(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """欧氏距离（经纬度空间，足够做聚类初筛）。"""
    return math.sqrt((lat1 - lat2) ** 2 + (lng1 - lng2) ** 2)


def _kmeans_plus_plus_init(points: List[Tuple[float, float]],
                           k: int, rng: random.Random) -> List[Tuple[float, float]]:
    """KMeans++ 初始化：选择相互距离最远的 k 个点做初始中心。"""
    n = len(points)
    if n <= k:
        return list(points)

    # 随机选第一个中心
    centers = [points[rng.randint(0, n - 1)]]

    for _ in range(1, k):
        # 每个点到最近中心的距离
        dists = []
        for p in points:
            d = min(_dist(p[0], p[1], c[0], c[1]) for c in centers)
            dists.append(d ** 2)
        total = sum(dists)
        if total == 0:
            # 所有点重合，随机选
            centers.append(points[rng.randint(0, n - 1)])
        else:
            # 轮盘赌选下一个中心
            r = rng.random() * total
            acc = 0.0
            for i, d in enumerate(dists):
                acc += d
                if acc >= r:
                    centers.append(points[i])
                    break
            else:
                centers.append(points[-1])

    return centers


def kmeans(pois: List[POI], k: int, max_iters: int = 50,
           seed: int = 42) -> List[List[POI]]:
    """将 POI 聚成 k 个簇（对应 k 天）。

    返回 List[List[POI]]，长度=k，每个子列表是当天的 POI 候选池。
    """
    n = len(pois)
    if n == 0:
        return [[] for _ in range(k)]
    if n <= k:
        # POI 数少于天数，尽量均匀分配
        clusters = [[] for _ in range(k)]
        for i, poi in enumerate(pois):
            clusters[i % k].append(poi)
        return clusters

    points = [(p.lat, p.lng) for p in pois]
    rng = random.Random(seed)

    # KMeans++ 初始化
    centers = _kmeans_plus_plus_init(points, k, rng)

    # Lloyd 迭代
    for _ in range(max_iters):
        # 分配
        clusters_idx = [[] for _ in range(k)]
        for i, (lat, lng) in enumerate(points):
            best_c = 0
            best_d = float("inf")
            for ci, (clat, clng) in enumerate(centers):
                d = _dist(lat, lng, clat, clng)
                if d < best_d:
                    best_d = d
                    best_c = ci
            clusters_idx[best_c].append(i)

        # 更新中心
        new_centers = []
        for ci, idxs in enumerate(clusters_idx):
            if not idxs:
                new_centers.append(centers[ci])
                continue
            avg_lat = sum(points[i][0] for i in idxs) / len(idxs)
            avg_lng = sum(points[i][1] for i in idxs) / len(idxs)
            new_centers.append((avg_lat, avg_lng))

        # 收敛判断
        moved = sum(_dist(centers[i][0], centers[i][1],
                          new_centers[i][0], new_centers[i][1])
                    for i in range(k))
        centers = new_centers
        if moved < 1e-8:
            break

    # 构建结果
    clusters = [[] for _ in range(k)]
    for i, poi in enumerate(pois):
        best_c = 0
        best_d = float("inf")
        for ci, (clat, clng) in enumerate(centers):
            d = _dist(poi.lat, poi.lng, clat, clng)
            if d < best_d:
                best_d = d
                best_c = ci
        clusters[best_c].append(poi)

    # 处理空簇：从最大的簇借一个点
    for ci in range(k):
        if not clusters[ci]:
            # 找最大的簇
            sizes = [len(c) for c in clusters]
            biggest = sizes.index(max(sizes))
            if biggest != ci and len(clusters[biggest]) > 1:
                # 借最远离中心的点
                center = centers[ci]
                far_idx = max(range(len(clusters[biggest])),
                              key=lambda j: _dist(
                                  clusters[biggest][j].lat,
                                  clusters[biggest][j].lng,
                                  center[0], center[1]))
                clusters[ci].append(clusters[biggest].pop(far_idx))

    return _rebalance(clusters, centers, k)


# 容量再平衡的上界：允许最大簇到均值的这么多倍
_REBALANCE_UPPER = 1.35


def _rebalance(clusters: List[List[POI]], centers: List[tuple],
               k: int) -> List[List[POI]]:
    """容量再平衡：把过大的簇让一些点给过小的簇。

    vanilla KMeans 在"市中心密集 + 郊区稀疏"的数据上会产出极不均衡的簇
    （实测苏州 3 天出现 **7 / 124 / 12**）。对旅行分天而言这是有害的：
    候选池被抽干的那一天只能由少数几个点硬凑，既排不满时间、也没得挑餐厅。

    做法：反复把"最大簇里最应该属于最小簇"的那个点搬过去——
    即 (到本簇中心距离 - 到目标簇中心距离) 最大的点。中心随之更新。
    只在超出上下界时搬，避免破坏正常的地理聚类。
    """
    n = sum(len(c) for c in clusters)
    if k <= 1 or n < k * 2:
        return clusters
    target = n / k
    upper = target * _REBALANCE_UPPER

    # 最多搬 n 次，保证一定终止
    for _ in range(n):
        sizes = [len(c) for c in clusters]
        big = sizes.index(max(sizes))
        small = sizes.index(min(sizes))
        # 只约束上界：把过大的簇削到均值附近即可。
        # （早先版本还要求"最小簇低于下界"才继续，结果最小簇一达标就提前退出，
        #   大簇仍留着 95/143。）
        if sizes[big] <= upper or sizes[big] - sizes[small] <= 1:
            break
        bcenter = centers[big]
        scenter = centers[small]
        # 选"最不属于本簇、最属于目标簇"的点
        move_idx = max(
            range(len(clusters[big])),
            key=lambda j: (_dist(clusters[big][j].lat, clusters[big][j].lng,
                                 bcenter[0], bcenter[1])
                           - _dist(clusters[big][j].lat, clusters[big][j].lng,
                                   scenter[0], scenter[1])),
        )
        if move_idx is None:
            break
        moved_poi = clusters[big].pop(move_idx)
        clusters[small].append(moved_poi)
        # 更新两簇中心
        for ci in (big, small):
            c = clusters[ci]
            if c:
                centers[ci] = (sum(p.lat for p in c) / len(c),
                               sum(p.lng for p in c) / len(c))
    return clusters
