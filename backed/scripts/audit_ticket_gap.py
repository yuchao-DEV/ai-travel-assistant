"""统计"票价数据缺口"的规模（一次性分析脚本）。

回答两个问题：
1. 语料层面：1120 行里，多少行**有本景点自己的成人票价**，多少行只有别的东西的票？
2. 规划层面：真实规划时，候选景点里多少能拿到真实票价、多少只能落到"预留"？

用法：
    cd backed
    .venv\\Scripts\\python.exe scripts\\audit_ticket_gap.py
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
from app.services.poi_prices import apply_known_prices  # noqa: E402

CITIES = ["北京", "上海", "杭州", "南京", "苏州", "成都",
          "西安", "重庆", "厦门", "青岛", "大理", "丽江"]

KIND_LABEL = {
    "ticket": "有本景点成人票价",
    "free": "明确免费",
    "service_only": "只有付费服务（非门票）",
    "unknown": "无本景点票价",
}


def corpus_audit() -> None:
    """语料层面：按类别统计，并把 unknown 再细分。"""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT COALESCE(kind,'unknown') k, note, count(*) n "
            "FROM poi_prices GROUP BY k, note"
        ).fetchall()

    total = sum(r["n"] for r in rows)
    by_kind = Counter()
    unknown_split = Counter()
    for r in rows:
        by_kind[r["k"]] += r["n"]
        if r["k"] == "unknown":
            note = r["note"] or ""
            if "其他景点" in note:
                unknown_split["只有景区内其他景点的票（同鼓浪屿）"] += r["n"]
            elif "优待" in note or "学生" in note:
                unknown_split["只有优待/学生票，无成人票价"] += r["n"]
            elif note:
                unknown_split["文本无价格（如「以现场公示为主」）"] += r["n"]
            else:
                unknown_split["完全没有门票字段"] += r["n"]

    print("=" * 72)
    print("语料层面（1,120 行）")
    print("=" * 72)
    for kind in ("ticket", "free", "service_only", "unknown"):
        n = by_kind.get(kind, 0)
        print(f"  {KIND_LABEL[kind]:<26} {n:>5}  {n / total * 100:5.1f}%")
    print()
    print("  「无本景点票价」的细分：")
    for label, n in unknown_split.most_common():
        print(f"    {label:<34} {n:>5}")
    usable = by_kind.get("ticket", 0) + by_kind.get("free", 0)
    print(f"\n  → 真正可用（成人票价 + 明确免费）：{usable} / {total}"
          f" = {usable / total * 100:.1f}%")


async def plan_audit() -> None:
    """规划层面：真实候选里各来源的占比（这才是用户看得见的）。"""
    print()
    print("=" * 72)
    print("规划层面（真实高德候选景点）")
    print("=" * 72)
    print(f"  {'城市':<6} {'候选景点':>8} {'有真实票价':>10} {'落预留':>8} {'覆盖率':>8}")
    print("  " + "-" * 46)
    grand = Counter()
    for city in CITIES:
        geo = await geocode(city)
        if not geo:
            continue
        raws = await fetch_all_pois(city, ["景点", "博物馆", "公园"],
                                    geo["center_lat"], geo["center_lng"])
        pois = [p for p in raw_to_pois(raws) if p.poi_type == "spot"]
        apply_known_prices(pois, city)
        real = sum(1 for p in pois if p.cost_source == "third_party")
        reserved = len(pois) - real
        grand["total"] += len(pois)
        grand["real"] += real
        print(f"  {city:<6} {len(pois):>8} {real:>10} {reserved:>8} "
              f"{real / max(len(pois), 1) * 100:>7.0f}%")
    print("  " + "-" * 46)
    t, r = grand["total"], grand["real"]
    print(f"  {'合计':<6} {t:>8} {r:>10} {t - r:>8} {r / max(t, 1) * 100:>7.0f}%")


async def main() -> int:
    corpus_audit()
    await plan_audit()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
