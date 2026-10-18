"""审计知识库景点与高德候选景点的重合度（一次性分析脚本）。

回答：知识库里的 POI，有多少能在规划时被高德候选命中？反过来呢？

三种匹配口径（从严到宽）：
- exact   归一化后完全相等
- prefix  高德名以知识库名开头（或反过来）
- contain 一方完整包含另一方（长度 ≥ 2）

用法：
    cd backed
    .venv\\Scripts\\python.exe scripts\\audit_poi_overlap.py
"""
import asyncio
import sys
from collections import Counter
from pathlib import Path

_BACKED = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKED))

from app.algorithms.data_loader import raw_to_pois  # noqa: E402
from app.amap.client import fetch_all_pois, geocode  # noqa: E402
from app.db.database import get_conn  # noqa: E402
from app.services.poi_prices import normalize_name  # noqa: E402

CITIES = ["北京", "上海", "杭州", "南京", "苏州", "成都",
          "西安", "重庆", "厦门", "青岛", "大理", "丽江"]


def corpus_names(city: str) -> set:
    """知识库中该城市的 POI 名（归一化后）。"""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT name FROM poi_prices WHERE city = ?", (city,)
        ).fetchall()
    return {normalize_name(r["name"]) for r in rows if normalize_name(r["name"])}


def match_level(amap_norm: str, corpus_norm: str) -> str:
    """返回匹配档位；不匹配返回空串。"""
    if not amap_norm or not corpus_norm:
        return ""
    if amap_norm == corpus_norm:
        return "exact"
    if amap_norm.startswith(corpus_norm) or corpus_norm.startswith(amap_norm):
        return "prefix"
    if len(corpus_norm) >= 2 and (corpus_norm in amap_norm or amap_norm in corpus_norm):
        return "contain"
    return ""


async def main() -> int:
    print("=" * 90)
    print("知识库景点 ←→ 高德候选景点 重合度")
    print("=" * 90)
    header = (f"  {'城市':<6} {'知识库':>6} {'高德候选':>8} "
              f"{'完全相等':>8} {'开头匹配':>8} {'包含匹配':>8} {'命中合计':>8} {'重合率':>7}")
    print(header)
    print("  " + "-" * 84)

    grand = Counter()
    unmatched_corpus_samples = []
    for city in CITIES:
        c_names = corpus_names(city)
        geo = await geocode(city)
        if not geo:
            continue
        raws = await fetch_all_pois(city, ["景点", "博物馆", "公园"],
                                    geo["center_lat"], geo["center_lng"])
        a_names = {normalize_name(p.name)
                   for p in raw_to_pois(raws) if p.poi_type == "spot"}
        a_names.discard("")

        exact = prefix = contain = 0
        hit_corpus = set()
        for c in c_names:
            best = ""
            for a in a_names:
                level = match_level(a, c)
                if level == "exact":
                    best = "exact"
                    break
                if level and not best:
                    best = level
            if best == "exact":
                exact += 1
                hit_corpus.add(c)
            elif best == "prefix":
                prefix += 1
                hit_corpus.add(c)
            elif best == "contain":
                contain += 1
                hit_corpus.add(c)
            elif len(unmatched_corpus_samples) < 12:
                unmatched_corpus_samples.append(f"{city}/{c}")

        hit = len(hit_corpus)
        grand["corpus"] += len(c_names)
        grand["amap"] += len(a_names)
        grand["hit"] += hit
        rate = hit / max(len(c_names), 1) * 100
        print(f"  {city:<6} {len(c_names):>6} {len(a_names):>8} "
              f"{exact:>8} {prefix:>8} {contain:>8} {hit:>8} {rate:>6.0f}%")

    print("  " + "-" * 84)
    c, a, h = grand["corpus"], grand["amap"], grand["hit"]
    print(f"  {'合计':<6} {c:>6} {a:>8} {'':>8} {'':>8} {'':>8} {h:>8} {h / max(c, 1) * 100:>6.0f}%")
    print()
    print(f"  知识库 {c} 个景点中，{h} 个能被高德候选命中（{h / max(c, 1) * 100:.0f}%），"
          f"{c - h} 个命中不了")
    print(f"  高德候选 {a} 个景点中，约 {h} 个能在知识库里找到对应（{h / max(a, 1) * 100:.0f}%）")
    print()
    print("  知识库里『高德候选没命中』的样例（前 12 个）：")
    for s in unmatched_corpus_samples:
        print(f"    {s}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
