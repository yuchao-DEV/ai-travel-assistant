"""用知识库的景点清单**扩充高德候选池**。

**为什么需要它**：现在的候选池完全来自高德关键词搜索
（``fetch_all_pois(city, ["景点","博物馆","公园",…])``），而它有取样上限
（``max_per_keyword=50``）且结果偏向市中心。实测北京 5 个关键词只取到 228 个 POI，
导致**明十三陵、北京动物园、清华大学、红砖美术馆这些知名景点根本进不了候选池**
——高德直接搜名字明明搜得到。

后果不只是"少几个点"：一个城市最值得去的地方（远郊大景点）从原理上就进不了行程，
而行程只能在市中心的小点里打转。

**修法是把角色对调**：

- 谁决定"这个城市有哪些值得去的候选" → **知识库**（策展过的景点清单）
- 谁提供坐标、评分、营业时间等真实字段 → **高德**（不变）

知识库的景点名逐个反查高德，命中的真实 POI 合并进候选池。合并进来的点**不享受任何
评分优待**——它们和原有候选走完全相同的打分与排序，偏远景点照样吃绕路惩罚，
只是"以前看不见，现在看得见"。

**降级**：没有语料的城市直接返回原候选池；反查失败只记日志，不影响出行程。
"""
import asyncio
import logging
from typing import Any, Dict, List, Optional, Tuple

from app.algorithms.candidate_selector import match_score
from app.algorithms.data_loader import raw_to_pois
from app.amap.client import search_pois
from app.db.database import get_conn
from app.rag.tokenize import STOPWORDS  # noqa: F401  (保持与本项目其余模块一致的导入风格)

logger = logging.getLogger("app.poi_pool")

# 每城最多反查多少个知识库景点名。
#
# **这个值直接决定配额风险**：每个名字是一次高德地点搜索。
# 曾设成 120（约等于每城全部知识点位），实测把高德**每日搜索配额打爆**
# （返回 infocode 10002 SERVICE_NOT_AVAILABLE），后果不是"扩充失效"，
# 而是**整个规划功能瘫痪**——取不到任何 POI，所有城市都返回空行程。
#
# 所以压到 30：每城 30 次搜索，扩充收益仍然明显（能捞回明十三陵这类
# 远郊知名景点），但不会一次会话就把一天的额度吃光。
MAX_LOOKUPS_PER_CITY = 30
# 反查并发（与灵感流、导入功能一致，避免触发高德 QPS 限制）
LOOKUP_CONCURRENCY = 3
# 每个名字取几条候选结果来挑最佳匹配
LOOKUP_PAGE_SIZE = 5
# 命中判定所需的最低匹配分（2=开头匹配，3=完全相等；1=包含匹配也算）
MIN_MATCH_SCORE = 1


def knowledge_poi_names(city: str) -> List[str]:
    """该城市知识库里的景点名（来自 poi_prices，一行一个 POI）。"""
    if not city:
        return []
    try:
        with get_conn() as conn:
            rows = conn.execute(
                "SELECT DISTINCT name FROM poi_prices WHERE city = ?", (city,)
            ).fetchall()
    except Exception as exc:
        logger.warning("读取知识库景点名失败，跳过候选池扩充：%s", exc)
        return []
    return [r["name"] for r in rows if r["name"]]


def _norm(text: str) -> str:
    """轻量归一化，仅用于去重比较。"""
    return "".join(ch for ch in (text or "") if ch.isalnum() or "\u4e00" <= ch <= "\u9fff")


def _already_covered(name: str, existing_norms: set) -> bool:
    """候选池里是否已经有了这个景点（避免重复反查）。"""
    norm = _norm(name)
    if not norm:
        return True
    if norm in existing_norms:
        return True
    # 高德叫「杭州西湖风景名胜区-灵隐寺」而知识库叫「灵隐寺」：子串也算已覆盖
    return any(norm in other or other in norm
               for other in existing_norms if len(other) >= len(norm))


async def _lookup_one(name: str, city: str, center_lat: float, center_lng: float,
                      sem: asyncio.Semaphore) -> Optional[Dict[str, Any]]:
    """按名字反查高德，返回最佳匹配的原始 POI（或 None）。"""
    async with sem:
        try:
            results = await search_pois(city, name, center_lat, center_lng,
                                        page=1, page_size=LOOKUP_PAGE_SIZE)
        except Exception as exc:
            logger.warning("知识点位反查失败「%s」：%s", name, exc)
            return None
    if not results:
        return None
    best = max(results, key=lambda r: (match_score(r.get("name", ""), name),
                                       r.get("rating") or 0.0))
    if match_score(best.get("name", ""), name) < MIN_MATCH_SCORE:
        return None
    return best


async def augment_candidates(
    city: str,
    base_pois: List[Any],
    center_lat: float,
    center_lng: float,
) -> Tuple[List[Any], Dict[str, int]]:
    """用知识库景点名扩充候选池，返回 ``(合并后的候选, 统计)``。

    统计字段：``knowledge``（知识库景点数）/ ``looked_up``（实际反查数）/
    ``added``（新增候选数）/ ``missed``（高德查不到的）。
    """
    stat = {"knowledge": 0, "looked_up": 0, "added": 0, "missed": 0}
    names = knowledge_poi_names(city)
    stat["knowledge"] = len(names)
    if not names:
        return base_pois, stat

    existing = {_norm(getattr(p, "name", "")) for p in base_pois}
    existing.discard("")
    pending = [n for n in names if not _already_covered(n, existing)]
    pending = pending[:MAX_LOOKUPS_PER_CITY]
    if not pending:
        return base_pois, stat

    stat["looked_up"] = len(pending)
    sem = asyncio.Semaphore(LOOKUP_CONCURRENCY)
    found = await asyncio.gather(*[
        _lookup_one(n, city, center_lat, center_lng, sem) for n in pending
    ])

    extra_raw: List[Dict[str, Any]] = []
    seen = set(existing)
    for raw in found:
        if not raw:
            stat["missed"] += 1
            continue
        norm = _norm(raw.get("name", ""))
        if not norm or norm in seen:
            continue
        seen.add(norm)
        extra_raw.append(raw)

    if not extra_raw:
        return base_pois, stat

    extra_pois = raw_to_pois(extra_raw)
    stat["added"] = len(extra_pois)
    logger.info("候选池扩充：%s 知识库 %d 个景点，反查 %d 个，新增 %d 个候选",
                city, stat["knowledge"], stat["looked_up"], stat["added"])
    return base_pois + extra_pois, stat
