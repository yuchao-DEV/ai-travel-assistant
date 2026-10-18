"""混合检索：BM25（FTS5） + 向量余弦 + RRF 融合。

**为什么要混合**：两路召回的失败模式是互补的——
- BM25 强在精确词匹配（"灵隐寺要预约吗" → 命中含"灵隐寺"的段落），
  但用户换个说法（"那个飞来峰旁边的寺庙"）就完全失效；
- 向量强在语义相近，但会把"杭州"和"苏州"的交通段落混在一起。
融合两路，任一路挂掉都不至于让检索失效。

**RRF（Reciprocal Rank Fusion）** 的公式是 ``score += 1 / (k + rank)``，
只用**名次**不用分数，所以两路分数量纲不同也无所谓（BM25 是负值、余弦是 0~1）。
``k`` 默认 60，作用是压低头部名次的绝对优势、让多路都靠前的文档胜出。

**降级**：没有向量时只跑 BM25，结果里 ``vector_ready=False``，不抛异常。
"""
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from app.config import (
    RAG_RECALL_K,
    RAG_RRF_K,
    RAG_TOP_K,
    RAG_VECTOR_SCAN_LIMIT,
)
from app.db.database import fts_available, get_conn
from app.rag.embedding import cosine, embed_texts, embed_enabled, unpack
from app.rag.tokenize import escape_fts_query, query_terms, tokenize_zh

logger = logging.getLogger("app.rag.retriever")

# LIKE 兜底用的中文片段长度
_LIKE_SNIPPET = 6


@dataclass
class RetrievedChunk:
    """一条检索结果，带来源信息（Agent 引用与前端展示都需要）。"""
    chunk_id: int
    doc_key: str
    title: str
    content: str
    heading_path: str = ""
    city: Optional[str] = None
    kind: Optional[str] = None
    score: float = 0.0
    bm25_rank: Optional[int] = None
    vector_rank: Optional[int] = None
    vector_score: Optional[float] = None
    source: Optional[str] = None
    source_url: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "chunk_id": self.chunk_id,
            "doc_key": self.doc_key,
            "title": self.title,
            "content": self.content,
            "heading_path": self.heading_path,
            "city": self.city,
            "kind": self.kind,
            "score": round(self.score, 6),
            "bm25_rank": self.bm25_rank,
            "vector_rank": self.vector_rank,
            "vector_score": (round(self.vector_score, 4)
                             if self.vector_score is not None else None),
            "source": self.source,
            "source_url": self.source_url,
        }


def _row_to_candidate(row) -> Dict[str, Any]:
    return {
        "chunk_id": row["id"],
        "doc_key": row["doc_key"],
        "title": row["title"],
        "content": row["content"],
        "heading_path": row["heading_path"] or "",
        "city": row["city"],
        "kind": row["kind"],
        "source": row["source"],
        "source_url": row["source_url"],
    }


def _bm25_search(query: str, city: Optional[str], limit: int) -> List[Dict[str, Any]]:
    """FTS5 BM25 检索。

    ``bm25()`` 返回的是**负值**，越相关越负，所以排序必须是 ``ASC``——
    写成 DESC 会把最不相关的排到最前面，而且不会报错。
    """
    terms = query_terms(query)
    if not terms:
        return []
    match_expr = escape_fts_query(terms)
    if not match_expr:
        return []

    sql = """
        SELECT c.id, c.doc_key, c.heading_path, c.content, c.city, c.kind,
               d.title, d.source, d.source_url,
               bm25(doc_chunks_fts) AS rank_score
        FROM doc_chunks_fts
        JOIN doc_chunks c ON c.id = doc_chunks_fts.rowid
        JOIN documents  d ON d.id = c.document_id
        WHERE doc_chunks_fts MATCH ?
    """
    params: List[Any] = [f"tokens : ({match_expr})"]
    if city:
        # 城市过滤放宽到"该城市 + 通用/规则类"，否则通用经验（预算、预约）会被过滤掉
        sql += " AND (c.city = ? OR c.city IS NULL)"
        params.append(city)
    sql += " ORDER BY rank_score ASC LIMIT ?"
    params.append(limit)

    with get_conn() as conn:
        try:
            rows = conn.execute(sql, params).fetchall()
        except Exception as exc:
            logger.warning("FTS 检索失败，改用 LIKE 兜底：%s", exc)
            return _like_search(query, city, limit)
    return [_row_to_candidate(row) for row in rows]


def _like_search(query: str, city: Optional[str], limit: int) -> List[Dict[str, Any]]:
    """FTS5 不可用时的兜底：按查询的前几个字做 LIKE 匹配。

    精度远不如 BM25，但保证「检索永远有结果可返回」而不是直接报错。
    """
    snippet = re.sub(r"\s+", "", query or "")[:_LIKE_SNIPPET]
    if not snippet:
        return []
    sql = """
        SELECT c.id, c.doc_key, c.heading_path, c.content, c.city, c.kind,
               d.title, d.source, d.source_url
        FROM doc_chunks c JOIN documents d ON d.id = c.document_id
        WHERE c.content LIKE ?
    """
    params: List[Any] = [f"%{snippet}%"]
    if city:
        sql += " AND (c.city = ? OR c.city IS NULL)"
        params.append(city)
    sql += " LIMIT ?"
    params.append(limit)
    with get_conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [_row_to_candidate(row) for row in rows]


async def _vector_search(query: str, city: Optional[str], limit: int) -> Optional[List[Dict[str, Any]]]:
    """向量检索：全表算余弦后取 top。返回 None 表示"没有向量可用"（降级信号）。"""
    if not embed_enabled():
        return None
    try:
        vectors = await embed_texts([query])
    except Exception as exc:
        logger.warning("查询向量化失败，退化为纯 BM25：%s", exc)
        return None
    if not vectors:
        return None
    query_vec = vectors[0]

    sql = """
        SELECT c.id, c.doc_key, c.heading_path, c.content, c.city, c.kind,
               d.title, d.source, d.source_url, e.vector
        FROM embeddings e
        JOIN doc_chunks c ON c.id = e.chunk_id
        JOIN documents  d ON d.id = c.document_id
    """
    params: List[Any] = []
    if city:
        sql += " WHERE (c.city = ? OR c.city IS NULL)"
        params.append(city)
    sql += " ORDER BY e.chunk_id LIMIT ?"
    params.append(RAG_VECTOR_SCAN_LIMIT)

    with get_conn() as conn:
        rows = conn.execute(sql, params).fetchall()

    scored: List[tuple] = []
    for row in rows:
        vec = unpack(row["vector"])
        if len(vec) != len(query_vec):
            # 历史遗留的异维向量：跳过而不是算出一个无意义的分数
            continue
        scored.append((cosine(query_vec, vec), row))
    scored.sort(key=lambda item: item[0], reverse=True)

    out: List[Dict[str, Any]] = []
    for score, row in scored[:limit]:
        item = _row_to_candidate(row)
        item["vector_score"] = score
        out.append(item)
    return out


def _rrf_fuse(
    bm25_hits: List[Dict[str, Any]],
    vector_hits: Optional[List[Dict[str, Any]]],
    rrf_k: int,
) -> List[RetrievedChunk]:
    """RRF 融合两路结果。

    只用名次，不用分数——BM25 是负值、余弦是 0~1，量纲不同无法直接相加。
    """
    bucket: Dict[int, RetrievedChunk] = {}

    def ensure(item: Dict[str, Any]) -> RetrievedChunk:
        chunk_id = item["chunk_id"]
        if chunk_id not in bucket:
            bucket[chunk_id] = RetrievedChunk(
                chunk_id=chunk_id,
                doc_key=item["doc_key"],
                title=item["title"],
                content=item["content"],
                heading_path=item.get("heading_path", ""),
                city=item.get("city"),
                kind=item.get("kind"),
                source=item.get("source"),
                source_url=item.get("source_url"),
            )
        return bucket[chunk_id]

    for rank, item in enumerate(bm25_hits, start=1):
        node = ensure(item)
        node.bm25_rank = rank
        node.score += 1.0 / (rrf_k + rank)

    if vector_hits:
        for rank, item in enumerate(vector_hits, start=1):
            node = ensure(item)
            node.vector_rank = rank
            node.vector_score = item.get("vector_score")
            node.score += 1.0 / (rrf_k + rank)

    return sorted(bucket.values(), key=lambda n: n.score, reverse=True)


async def hybrid_search(
    query: str,
    city: Optional[str] = None,
    top_k: int = RAG_TOP_K,
    *,
    recall_k: int = RAG_RECALL_K,
) -> Dict[str, Any]:
    """混合检索入口。

    返回 ``{results, vector_ready, bm25_count, vector_count, query, city, note}``。
    ``vector_ready=False`` 是**正常降级**，不是错误——调用方应把它当作"本次只有
    关键词检索"来表述（例如告诉用户"知识库暂无向量检索能力"），而不是报错。
    """
    query = (query or "").strip()
    if not query:
        return {"results": [], "vector_ready": False, "bm25_count": 0,
                "vector_count": 0, "query": query, "city": city,
                "note": "查询为空"}

    bm25_hits = _bm25_search(query, city, recall_k) if fts_available() else \
        _like_search(query, city, recall_k)
    vector_hits = await _vector_search(query, city, recall_k)
    fused = _rrf_fuse(bm25_hits, vector_hits, RAG_RRF_K)

    note = ""
    if vector_hits is None:
        note = "向量检索不可用（未配置 Embedding 服务或调用失败），本次仅使用关键词检索"
    elif not vector_hits:
        note = "向量索引为空（可能尚未写入向量），本次仅使用关键词检索"

    return {
        "results": [node.to_dict() for node in fused[:max(1, top_k)]],
        "vector_ready": bool(vector_hits),
        "bm25_count": len(bm25_hits),
        "vector_count": len(vector_hits or []),
        "query": query,
        "city": city,
        "note": note,
    }
