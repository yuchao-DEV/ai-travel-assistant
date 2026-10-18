"""把高德缓存的 POI 批量转成 L1 文档并索引。

.. note::

   **这个脚本在当前的语料策略下用处有限，默认不启用。**

   项目的 L1 文本层已经由 ``scripts/import_citydata.py`` 建好了
   （1,120 篇 / 2,069 块，含「介绍」「小贴士」「建议游玩时间」等长文本）。
   而高德 POI **没有长文本**——只有名称/类型/评分/营业时间这些结构化字段，
   转成文档后是"薄文档"，检索时既抢不到分、又会稀释真正有内容的语料。

   保留它有两个目的：一是遵守落地清单约定的接口，二是在将来接入更丰富的
   POI 文本来源（例如景区官网描述）时可以直接改用。

用法：
    cd backed
    .venv\\Scripts\\python.exe scripts\\build_poi_index.py --city 杭州
    .venv\\Scripts\\python.exe scripts\\build_poi_index.py --all
"""
import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

_BACKED = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKED))

from app.config import CACHE_DIR  # noqa: E402
from app.db.database import init_db  # noqa: E402
from app.rag.indexer import index_document, index_stats  # noqa: E402


def poi_to_text(poi: Dict[str, Any]) -> str:
    """把一条高德 POI 写成句子（不要写"字段：值"的堆砌，向量检索理解不了）。"""
    parts: List[str] = []
    name = poi.get("name") or ""
    if not name:
        return ""
    parts.append(name)
    area = poi.get("business_area") or poi.get("address") or ""
    if area:
        parts.append(f"，位于{area}")
    parts.append("。")
    if poi.get("category"):
        parts.append(f"类型：{poi['category']}。")
    if poi.get("rating"):
        parts.append(f"高德评分 {poi['rating']}。")
    if poi.get("cost") and poi.get("cost_source") == "real":
        parts.append(f"人均消费约 {poi['cost']:.0f} 元。")
    if poi.get("open_time"):
        parts.append(f"营业时间：{poi['open_time']}。")
    if poi.get("poi_type") == "restaurant":
        parts.append("可以在这里用餐。")
    return "".join(parts)


def find_cache_files(city: str) -> List[Path]:
    """找该城市的 POI 缓存文件（``cache/<城市>/pois_v*.json``）。"""
    city_dir = CACHE_DIR / city
    if not city_dir.exists():
        return []
    return sorted(city_dir.glob("pois_v*.json"))


def load_cached_pois(path: Path) -> List[Dict[str, Any]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    if isinstance(data, dict):
        for key in ("pois", "results", "data"):
            if isinstance(data.get(key), list):
                return data[key]
        return []
    return data if isinstance(data, list) else []


async def run(city: str | None, all_cities: bool) -> int:
    init_db()
    cities: List[str]
    if all_cities:
        cities = sorted(p.name for p in CACHE_DIR.iterdir() if p.is_dir())
    elif city:
        cities = [city]
    else:
        print("请用 --city 指定城市，或用 --all 处理全部缓存城市")
        return 2

    total_docs = 0
    for name in cities:
        files = find_cache_files(name)
        if not files:
            print(f"  {name}：无 POI 缓存，跳过")
            continue
        seen = set()
        count = 0
        for path in files:
            for poi in load_cached_pois(path):
                poi_name = poi.get("name") or ""
                if not poi_name or poi_name in seen:
                    continue
                seen.add(poi_name)
                text = poi_to_text(poi)
                if len(text) < 20:
                    continue
                result = await index_document(
                    f"poi/{name}/{poi_name}", f"{name}·{poi_name}", text,
                    kind="poi", city=name, category="poi", source="高德",
                    with_vectors=False,       # 薄文档不值得花向量配额
                )
                if not result["skipped"]:
                    count += 1
        total_docs += count
        print(f"  {name}：索引 {count} 篇")

    stats = index_stats()
    print(f"\n完成：本次写入 {total_docs} 篇")
    print(f"知识库现状：documents={stats['documents']} chunks={stats['chunks']} "
          f"vectors={stats['vectors']}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="把高德缓存 POI 转成 L1 文档并索引")
    parser.add_argument("--city", help="只处理该城市")
    parser.add_argument("--all", action="store_true", help="处理全部有缓存的城市")
    args = parser.parse_args()
    return asyncio.run(run(args.city, args.all))


if __name__ == "__main__":
    sys.exit(main())
