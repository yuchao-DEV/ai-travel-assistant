"""Embedding 客户端：外部向量服务 + 本地缓存 + 降级 + 维度守卫。

设计要点：

1. **失败即降级**：任何异常（未配置 Key、网络失败、服务不支持 embedding）
   都返回 ``None``，由上层退回纯 BM25 检索。绝不让"没有向量"变成"检索不可用"。
2. **维度守卫**：服务返回的向量长度与 :data:`app.config.EMBED_DIM` 不一致时
   **直接抛错并拒绝写入**。混入不同维度的向量会让余弦相似度完全失去意义，
   而且症状是"检索结果莫名其妙"，极难排查——宁可在写入时报错。
3. **本地缓存**：同一段文本的向量只请求一次，缓存落在 ``backed/cache/embed/``，
   与高德的 POI 缓存同样的做法（SHA-256 文件名 + JSON 内容）。
"""
import asyncio
import hashlib
import json
import logging
import math
import struct
from pathlib import Path
from typing import Dict, List, Optional, Sequence

import httpx

from app.config import (
    CACHE_DIR,
    EMBED_API_KEY,
    EMBED_BATCH,
    EMBED_BASE_URL,
    EMBED_DIM,
    EMBED_MODEL,
)

logger = logging.getLogger("app.rag.embedding")

_EMBED_CACHE_DIR = CACHE_DIR / "embed"
_TIMEOUT = httpx.Timeout(connect=10.0, read=60.0, write=30.0, pool=10.0)


class EmbeddingDimensionError(RuntimeError):
    """向量维度与配置不符。

    单独定义异常类型，是为了让调用方能区分"服务挂了"（可降级）与
    "维度不对"（必须人工介入：清空 embeddings 表后重建索引）。
    """


def embed_enabled() -> bool:
    """Embedding 是否具备可用配置。

    没有 Key 或没有服务地址时直接判为不可用，避免白白发一次必然失败的请求。

    注意：**配置存在不等于服务可用**。当前 .env 用的是 DeepSeek 官方（只有对话
    没有 embedding 接口），密钥与服务地址都会被继承下来，所以这里会返回 True，
    真正失败在第一次请求。因此还需要下面的熔断机制。
    """
    if _CIRCUIT_OPEN is not None:
        return False
    return bool(EMBED_API_KEY and EMBED_BASE_URL and EMBED_MODEL)


# ---------------------------------------------------------------- 熔断
#
# 为什么需要：批量导入时每篇文档都会走一次 embed_texts。若服务根本不提供该接口
# （404）或密钥无效（401），就会为每篇文档白白发一次注定失败的请求——
# 实测导入 12 城（约 1,080 篇）时，这 1,080 次 HTTP 往返把导入拖到十分钟以上。
# 配置类错误重试没有意义，所以第一次确定后就**本进程内不再尝试**。
#
# 只对 4xx（配置/权限问题）熔断；5xx 与超时属于临时故障，下次仍然重试。
_CIRCUIT_OPEN: Optional[str] = None
_CIRCUIT_STATUSES = (400, 401, 403, 404, 405, 422)


def _open_circuit(reason: str) -> None:
    global _CIRCUIT_OPEN
    if _CIRCUIT_OPEN is None:
        _CIRCUIT_OPEN = reason
        logger.warning("Embedding 已熔断，本进程内不再尝试（%s）；"
                       "检索将使用纯 BM25", reason)


def reset_circuit() -> None:
    """复位熔断（配置改好后或测试中调用）。"""
    global _CIRCUIT_OPEN
    _CIRCUIT_OPEN = None


def circuit_reason() -> Optional[str]:
    """熔断原因，None 表示未熔断。"""
    return _CIRCUIT_OPEN


# ---------------------------------------------------------------- 向量编解码

def pack(vec: Sequence[float]) -> bytes:
    """把向量打包成 float32 小端字节串（SQLite BLOB）。"""
    return struct.pack(f"<{len(vec)}f", *vec)


def unpack(blob: bytes) -> List[float]:
    """把 BLOB 还原成向量。"""
    if not blob:
        return []
    count = len(blob) // 4
    return list(struct.unpack(f"<{count}f", blob[: count * 4]))


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    """余弦相似度。长度不等或任一为零向量时返回 0.0（不抛异常）。

    刻意不在这里做维度校验：这个函数是纯数学工具，维度不一致属于调用方的
    错误，应该在写入阶段就被 :class:`EmbeddingDimensionError` 拦住。
    """
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na <= 0.0 or nb <= 0.0:
        return 0.0
    return dot / (math.sqrt(na) * math.sqrt(nb))


# ---------------------------------------------------------------- 本地缓存

def _cache_path(text: str) -> Path:
    digest = hashlib.sha256(
        f"{EMBED_MODEL}\x00{EMBED_DIM}\x00{text}".encode("utf-8")
    ).hexdigest()
    return _EMBED_CACHE_DIR / f"{digest}.json"


def _cache_get(text: str) -> Optional[List[float]]:
    path = _cache_path(text)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        vec = data.get("vector")
        if isinstance(vec, list) and len(vec) == EMBED_DIM:
            return [float(x) for x in vec]
    except Exception:  # 缓存损坏不该影响主流程
        return None
    return None


def _cache_put(text: str, vec: List[float]) -> None:
    try:
        _EMBED_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        _cache_path(text).write_text(
            json.dumps({"model": EMBED_MODEL, "dim": EMBED_DIM, "vector": vec},
                       ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception as exc:  # 缓存写失败只记日志
        logger.debug("embedding 缓存写入失败：%s", exc)


# ---------------------------------------------------------------- 远程调用

def _parse_vectors(payload: dict) -> List[List[float]]:
    """从 OpenAI 兼容响应里取出向量，按 index 排序。"""
    rows = payload.get("data")
    if not isinstance(rows, list):
        raise RuntimeError(f"embedding 响应缺少 data 字段：{str(payload)[:200]}")
    ordered = sorted(rows, key=lambda r: r.get("index", 0))
    return [[float(x) for x in (r.get("embedding") or [])] for r in ordered]


def _guard_dim(vectors: List[List[float]]) -> None:
    """维度守卫：长度不符直接抛错。"""
    for i, vec in enumerate(vectors):
        if len(vec) != EMBED_DIM:
            raise EmbeddingDimensionError(
                f"向量维度不符：第 {i} 条返回 {len(vec)} 维，"
                f"配置 EMBED_DIM={EMBED_DIM}（模型 {EMBED_MODEL}）。"
                f"请清空 embeddings 表后重建索引，或把 EMBED_DIM 改成正确值。"
            )


async def _fetch_batch(texts: List[str]) -> List[List[float]]:
    url = EMBED_BASE_URL.rstrip("/") + "/v1/embeddings"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {EMBED_API_KEY}",
    }
    body = {"model": EMBED_MODEL, "input": texts}
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.post(url, json=body, headers=headers)
        resp.raise_for_status()
        vectors = _parse_vectors(resp.json())
    _guard_dim(vectors)
    return vectors


async def embed_texts(texts: Sequence[str]) -> Optional[List[List[float]]]:
    """批量取向量。**任何失败都返回 None**（调用方据此退回 BM25 检索）。

    只有一种情况会抛异常：服务正常响应但**维度不符**（:class:`EmbeddingDimensionError`）
    ——那属于配置/数据损坏，静默降级会把问题藏起来。
    """
    items = [t or "" for t in texts]
    if not items:
        return []
    if not embed_enabled():
        logger.debug("embedding 不可用（未配置或已熔断），跳过向量化")
        return None

    # 先查缓存，只把未命中的送去请求
    result: List[Optional[List[float]]] = [None] * len(items)
    pending: List[int] = []
    for i, text in enumerate(items):
        hit = _cache_get(text)
        if hit is not None:
            result[i] = hit
        else:
            pending.append(i)

    if pending:
        batch_size = max(1, EMBED_BATCH)
        for start in range(0, len(pending), batch_size):
            idxs = pending[start:start + batch_size]
            try:
                vectors = await _fetch_batch([items[i] for i in idxs])
            except EmbeddingDimensionError:
                raise  # 维度问题是配置错误，必须让调用方看见
            except Exception as exc:
                status = getattr(getattr(exc, "response", None), "status_code", None)
                if status in _CIRCUIT_STATUSES:
                    _open_circuit(f"服务返回 {status}（接口不存在或密钥无效）")
                logger.warning("embedding 请求失败，本次退回纯 BM25 检索：%s", exc)
                return None
            if len(vectors) != len(idxs):
                logger.warning("embedding 返回条数(%d)与请求(%d)不符，退回 BM25",
                               len(vectors), len(idxs))
                return None
            for pos, vec in zip(idxs, vectors):
                result[pos] = vec
                _cache_put(items[pos], vec)

    if any(v is None for v in result):
        return None
    vectors = [v for v in result if v is not None]
    # 在**公开入口**再守一次维度：_fetch_batch 内部也有守卫，但那是实现细节，
    # 谁替换或包一层取向量逻辑都可能绕过它。embed_texts 是唯一对外契约，
    # 它返回的向量必须永远是合法维度。
    _guard_dim(vectors)
    return vectors


def embed_texts_sync(texts: Sequence[str]) -> Optional[List[List[float]]]:
    """:func:`embed_texts` 的同步包装，给脚本与非异步上下文用。"""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(embed_texts(texts))
    # 已在事件循环里（例如从 FastAPI 路由调用）：交给独立线程跑一个新循环
    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(lambda: asyncio.run(embed_texts(texts))).result()
