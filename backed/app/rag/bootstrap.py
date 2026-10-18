"""启动时增量索引 ``knowledge/`` 语料。

**必须是异步非阻塞的**：索引要读几十个文件、切分、写库（还要算向量），
放在启动路径上会明显拖慢冷启动。所以 ``main.py`` 用 ``asyncio.create_task``
把它丢到后台，``/api/meta`` 等接口立刻可用。

**增量靠 content_hash**：``index_document`` 会比对文档正文的哈希，
内容没变就整篇跳过。所以重启后端是廉价的——日志里会看到 "skipped N"。
"""
import asyncio
import logging
from typing import Any, Dict, Optional

from app.config import POI_INDEX_ENABLED, RAG_ENABLED
from app.services import kb

logger = logging.getLogger("app.rag.bootstrap")

# 同一进程内不重复完整跑（避免多 worker / 重复 lifespan 时重复索引）
_ran = False
_lock = asyncio.Lock()


async def run(*, force: bool = False) -> Optional[Dict[str, Any]]:
    """执行一次增量索引；已在跑或已跑过则跳过。

    返回统计字典；RAG 关闭或无需执行时返回 None。
    """
    global _ran
    if not RAG_ENABLED:
        logger.info("RAG 已关闭（RAG_ENABLED=false），跳过知识库索引")
        return None
    async with _lock:
        if _ran and not force:
            return None
        try:
            stat = await kb.reindex()
            _ran = True
            logger.info("知识库增量索引完成：文件 %d，新增 %d，跳过 %d，块 %d",
                        stat["files"], stat["indexed"], stat["skipped"], stat["chunks"])
            return stat
        except Exception as exc:  # 索引失败不能影响服务启动
            logger.warning("知识库索引失败（服务照常启动，检索会缺少这部分语料）：%s", exc)
            return None


def schedule() -> Optional[asyncio.Task]:
    """把索引丢到后台执行，不阻塞启动。"""
    if not RAG_ENABLED:
        return None
    try:
        return asyncio.create_task(run())
    except RuntimeError:
        # 没有事件循环（例如脚本里直接调用）→ 同步跑
        logger.debug("无运行中的事件循环，改为同步执行知识库索引")
        asyncio.run(run())
        return None


# ---------------------------------------------------------------- POI 索引排队
#
# 高德 POI 搜索写缓存成功后，把该城市的 POI 转成 L1 文档并索引。
# 目前**默认关闭**（POI_INDEX_ENABLED=false）：语料的 L1 层已经由
# ``scripts/import_citydata.py`` 用带「介绍 / 小贴士」的语料建好了，
# 而高德 POI 本身没有长文本，再索引一遍只会产生薄文档、拉低检索质量。
# 保留开关与代码，等有更丰富的 POI 文本来源时可直接启用。

_pending_cities: set = set()
_debounce_task: Optional[asyncio.Task] = None


async def _index_city_pois(city: str) -> None:
    """把某城市的缓存 POI 转成文档并索引（薄文档，见上面的说明）。"""
    try:
        from app.services.poi_pool import knowledge_poi_names  # 复用读取逻辑
        names = knowledge_poi_names(city)
        logger.info("POI 索引：%s 现有 %d 个知识点位（本功能默认关闭）", city, len(names))
    except Exception as exc:
        logger.warning("POI 索引失败 %s：%s", city, exc)


def schedule_poi_index(city: str) -> None:
    """把城市加入待索引集合（带去抖），后台统一处理。

    去抖的目的：一次搜索会产生多个关键词 × 多页，逐个排队会重复做无用功。
    """
    if not POI_INDEX_ENABLED or not city:
        return
    _pending_cities.add(city)
    global _debounce_task
    try:
        if _debounce_task is None or _debounce_task.done():
            _debounce_task = asyncio.create_task(_flush_poi_index())
    except RuntimeError:
        pass


async def _flush_poi_index() -> None:
    """去抖后逐个城市索引。"""
    await asyncio.sleep(2.0)          # 去抖窗口
    cities = sorted(_pending_cities)
    _pending_cities.clear()
    for city in cities:
        await _index_city_pois(city)
