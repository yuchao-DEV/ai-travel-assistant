"""城市清单的唯一真源，供 ``/api/meta`` 下发给前端。

**为什么需要它**：原先前端把 18 个城市**硬编码在两个文件里**（移动端 + 桌面端），
而后端能规划的城市远不止这些——``geocode()`` 可以解析任何高德认得的城市。
两边一旦不同步，就会出现"下拉框里有但规划不了"或者反过来"能规划却选不到"。

这里把清单合成一处，前端只负责渲染：

- **热门**：``cities.json`` 的种子城市（12 个），带本地坐标与关键词模板，零延迟；
- **全部**：热门 + 自扩展表（用过高德解析的城市）+ 知识库语料目录里出现过的城市。

注意「可选」不等于「有语料」：没有语料的城市照样能规划（POI 来自高德），
只是少了 RAG 检索内容——这是刻意的优雅降级，不是故障。
"""
import logging
from pathlib import Path
from typing import Any, Dict, List

from app.algorithms.data_loader import load_cities, load_seed_cities

logger = logging.getLogger("app.city_registry")

# 知识库语料目录（仓库根/data/citydata）。存在时把里面的城市一并列出来，
# 让"有语料的城市"在界面上就能选到。
_CORPUS_DIR = Path(__file__).resolve().parents[3] / "data" / "citydata"


def corpus_cities() -> List[str]:
    """语料目录里出现过的城市名（目录不存在时返回空）。

    只读文件名，不解析 CSV——启动路径上不能做重活。
    """
    try:
        if not _CORPUS_DIR.exists():
            return []
        return sorted(p.stem for p in _CORPUS_DIR.glob("*.csv") if p.stem)
    except Exception as exc:
        logger.warning("扫描语料城市失败，忽略：%s", exc)
        return []


def list_cities() -> Dict[str, Any]:
    """返回下发给前端的城市清单。

    ``hot`` 放最前面（前端做快捷入口），``all`` 是去重排序后的完整列表。
    """
    seed = list(load_seed_cities().keys())
    known = list(load_cities().keys())
    corpus = corpus_cities()

    hot = list(seed)
    merged: List[str] = []
    seen = set()

    # 顺序：热门 → 已缓存城市 → 语料城市 → 其余
    for group in (hot, known, corpus):
        for name in group:
            if name and name not in seen:
                seen.add(name)
                merged.append(name)

    return {
        "hot": hot,
        "all": merged,
        "total": len(merged),
        "cached": max(0, len(known) - len(seed)),   # 由高德解析后缓存下来的城市数
        "with_corpus": len(corpus),
    }


def is_supported(city: str) -> bool:
    """城市是否在已知清单里。

    **只用于做提示，不作为拒绝依据**——真正的可用性由能否地理编码决定。
    """
    return (city or "").strip() in set(load_cities().keys()) | set(corpus_cities())
