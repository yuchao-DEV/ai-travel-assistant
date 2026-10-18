"""导出所有"语料票价匹配"案例，供人工核对假阳性（一次性分析脚本）。

用法：
    cd backed
    .venv\\Scripts\\python.exe scripts\\review_ticket_matches.py
输出：
    docs/ticket-match-review.md
"""
import asyncio
import re
import sys
from pathlib import Path

_BACKED = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKED))

from app.algorithms.data_loader import raw_to_pois  # noqa: E402
from app.amap.client import fetch_all_pois, geocode  # noqa: E402
from app.services.poi_prices import apply_known_prices, load_city_prices, match_price  # noqa: E402

CITIES = ["北京", "上海", "杭州", "南京", "苏州", "成都",
          "西安", "重庆", "厦门", "青岛", "大理", "丽江"]

# 常见"本来就免费"的地名特征。**只是提请注意，不是结论**——
# 最终真假由人工核对，脚本不做判断。
FREE_HINT = re.compile(
    r"(公园|广场|步行街|街|桥|纪念馆|博物馆|美术馆|图书馆|展览馆|"
    r"湿地|湖|江|海|山|村|镇|巷|里|道|路)"
)
OUT = _BACKED.parent / "docs" / "ticket-match-review.md"


async def main() -> int:
    rows = []
    for city in CITIES:
        geo = await geocode(city)
        if not geo:
            print(f"  跳过（无法定位）：{city}")
            continue
        price_map = load_city_prices(city)
        raws = await fetch_all_pois(city, ["景点", "博物馆", "公园"],
                                    geo["center_lat"], geo["center_lng"])
        pois = raw_to_pois(raws)
        apply_known_prices(pois, city)
        for poi in pois:
            if poi.cost_source != "third_party":
                continue
            hit = match_price(poi.name, price_map) or {}
            rows.append({
                "city": city,
                "poi": poi.name,
                "corpus": hit.get("name", ""),
                "price": poi.cost,
                "raw": " ".join((hit.get("price_text") or "").split()),
                "likely_free": bool(FREE_HINT.search(poi.name)),
                "is_free_flag": bool(hit.get("is_free")),
            })

    lines = [
        "# 语料票价匹配结果（供人工核对）",
        "",
        f"共 **{len(rows)}** 条匹配。请逐条核对，在最后一列标注：",
        "",
        "- 留空 / ✓ = 价格正确，保留",
        "- ❌ = 价格错误（该地点实际免费或票价不符），我会把它改成「未知 / 门票预留」",
        "",
        "「⚠️」列表示**高德名称看起来像免费场所**（公园/广场/步行街/博物馆/湖/山 等），"
        "只是提请注意，不代表一定错——请以你的判断为准。",
        "",
        "| # | 城市 | 高德地点 | 语料名 | 取到的价 | 名称像免费 | 语料门票原文 | 核对 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    ordered = sorted(rows, key=lambda r: (not r["likely_free"], r["city"], r["poi"]))
    for i, r in enumerate(ordered, start=1):
        raw = r["raw"][:180].replace("|", "/")
        flag = "⚠️" if r["likely_free"] else ""
        line = (f"| {i} | {r['city']} | {r['poi']} | {r['corpus']} | "
                f"¥{r['price']:g} | {flag} | {raw} | |")
        lines.append(line)
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")

    free_like = [r for r in ordered if r["likely_free"]]
    print(f"  共 {len(rows)} 条匹配 → 已写入 {OUT}")
    print(f"  其中名称像免费场所的 {len(free_like)} 条（建议优先核对）")
    print()
    print("  === 名称像免费场所的（优先核对）===")
    for r in free_like:
        print(f"    {r['city']:<4} {r['poi'][:32]:34} ¥{r['price']:<8g} 语料名={r['corpus'][:16]}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
