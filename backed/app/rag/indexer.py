"""双索引写入：FTS5 全文索引 + 向量索引。

**核心不变量**：``doc_chunks`` 与 ``doc_chunks_fts`` 的行数必须始终相等。
FTS5 外部内容表的同步靠触发器完成，一旦触发器写错（或漏了一种操作），
倒排索引就会与主表**静默**不一致——检索结果开始莫名其妙，但没有任何报错。
所以这里每次写完都断言一次行数相等，不等就抛错并回滚。

向量是**可选**的：Embedding 不可用时只写全文索引，检索退化为纯 BM25。
这不算失败，``index_document`` 会在返回值里如实标注 ``vector_ready``。
"""
import logging
from typing import Any, Dict, List, Optional, Tuple

from app.config import EMBED_DIM, EMBED_MODEL
from app.db.database import fts_available, fts_row_count, get_conn, transaction, utc_now_iso
from app.rag.embedding import embed_texts, embed_enabled, circuit_reason, pack
from app.rag.pipeline import Chunk, chunk_document, content_hash
from app.rag.tokenize import tokenize_zh

logger = logging.getLogger("app.rag.indexer")


class FtsSyncError(RuntimeError):
    """FTS 倒排索引与 doc_chunks 行数不一致。

    这属于必须人工介入的数据损坏：继续用下去只会得到无意义（但看起来正常）的检索结果。
    """


def _assert_fts_synced(conn) -> None:
    """断言 FTS 与主表行数一致，不一致立即抛错（调用方会回滚）。"""
    chunks = int(conn.execute("SELECT count(*) FROM doc_chunks").fetchone()[0])
    if not fts_available():
        return
    fts = fts_row_count(conn)
    if chunks != fts:
        raise FtsSyncError(
            f"FTS 索引与主表不一致：doc_chunks={chunks}, doc_chunks_fts={fts}。"
            f"请重建索引（删除该文档后重新 index_document）。"
        )


async def index_document(
    doc_key: str,
    title: str,
    text: str,
    *,
    kind: str = "general",
    city: Optional[str] = None,
    category: Optional[str] = None,
    source: Optional[str] = None,
    source_url: Optional[str] = None,
    doc_date: Optional[str] = None,
    with_vectors: bool = True,
) -> Dict[str, Any]:
    """把一篇文档写入知识库（幂等：同一 ``doc_key`` 会整体替换）。

    返回统计信息：``{doc_key, chunks, vectors, vector_ready, skipped}``。
    ``skipped=True`` 表示内容哈希未变、已跳过重建（增量索引）。
    """
    chunks: List[Chunk] = chunk_document(text, meta={"city": city, "kind": kind})
    digest = content_hash(text or "")

    # 增量判断：同 key 且内容哈希一致 → 直接跳过
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id, content_hash FROM documents WHERE doc_key = ?", (doc_key,)
        ).fetchone()
    if row and row["content_hash"] == digest:
        logger.info("知识库跳过未变文档：%s", doc_key)
        return {"doc_key": doc_key, "chunks": 0, "vectors": 0,
                "vector_ready": False, "skipped": True}

    if not chunks:
        logger.warning("文档切分为空，未写入：%s", doc_key)
        return {"doc_key": doc_key, "chunks": 0, "vectors": 0,
                "vector_ready": False, "skipped": False}

    # 向量先算好再进事务：网络请求不能占着数据库锁
    vectors: Optional[List[List[float]]] = None
    if with_vectors:
        try:
            vectors = await embed_texts([c.content for c in chunks])
        except Exception as exc:  # 维度错误等：不静默，但要写清楚
            logger.error("向量化失败，本次只写全文索引：%s", exc)
            vectors = None
    vector_ready = vectors is not None and len(vectors) == len(chunks)

    now = utc_now_iso()
    with transaction() as conn:
        # 整体替换：先删旧块（触发器会同步摘除 FTS 行）
        old = conn.execute("SELECT id FROM documents WHERE doc_key = ?", (doc_key,)).fetchone()
        if old:
            conn.execute("DELETE FROM documents WHERE id = ?", (old["id"],))
        conn.execute(
            """INSERT INTO documents
               (doc_key, kind, city, category, title, source, source_url, doc_date,
                content_hash, char_count, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (doc_key, kind, city, category, title, source, source_url, doc_date,
             digest, len(text or ""), now, now),
        )
        doc_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])

        for chunk in chunks:
            conn.execute(
                """INSERT INTO doc_chunks
                   (document_id, doc_key, chunk_index, heading_path, content, tokens,
                    char_count, city, kind)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (doc_id, doc_key, chunk.chunk_index, chunk.heading_path, chunk.content,
                 tokenize_zh(chunk.content), chunk.char_count, city, kind),
            )
            if vector_ready:
                chunk_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
                conn.execute(
                    """INSERT INTO embeddings (chunk_id, model, dim, vector, created_at)
                       VALUES (?,?,?,?,?)""",
                    (chunk_id, EMBED_MODEL, EMBED_DIM, pack(vectors[chunk.chunk_index]), now),
                )

        # 不变量：写完立刻断言两个索引一致（不一致会抛错 → 事务回滚）
        _assert_fts_synced(conn)

    logger.info("知识库写入：%s（%d 块，向量 %s）",
                doc_key, len(chunks), "已写入" if vector_ready else "未写入")
    return {"doc_key": doc_key, "chunks": len(chunks),
            "vectors": len(chunks) if vector_ready else 0,
            "vector_ready": vector_ready, "skipped": False}


def delete_document(doc_key: str) -> bool:
    """删除文档及其全部块（触发器会同步清理 FTS）。"""
    with transaction() as conn:
        cur = conn.execute("DELETE FROM documents WHERE doc_key = ?", (doc_key,))
        deleted = cur.rowcount > 0
        if deleted:
            _assert_fts_synced(conn)
    return deleted


def reindex_city(city: str) -> int:
    """删除某城市已索引的**文档型**内容（L1 POI 索引用同一个 key 前缀）。

    返回删除的文档数。用于「重建某城市索引」。
    """
    with transaction() as conn:
        rows = conn.execute(
            "SELECT doc_key FROM documents WHERE city = ? AND kind != 'poi'", (city,)
        ).fetchall()
        for row in rows:
            conn.execute("DELETE FROM documents WHERE doc_key = ?", (row["doc_key"],))
        if rows:
            _assert_fts_synced(conn)
    return len(rows)


def index_stats() -> Dict[str, Any]:
    """知识库统计，供 /api/kb/stats 与验收使用。"""
    with get_conn() as conn:
        docs = int(conn.execute("SELECT count(*) FROM documents").fetchone()[0])
        chunks = int(conn.execute("SELECT count(*) FROM doc_chunks").fetchone()[0])
        vectors = int(conn.execute("SELECT count(*) FROM embeddings").fetchone()[0])
        dims = [r["dim"] for r in conn.execute(
            "SELECT DISTINCT dim FROM embeddings").fetchall()]
        by_kind = {r["kind"]: r["n"] for r in conn.execute(
            "SELECT kind, count(*) AS n FROM documents GROUP BY kind").fetchall()}
        by_city = {r["city"]: r["n"] for r in conn.execute(
            "SELECT city, count(*) AS n FROM documents WHERE city IS NOT NULL "
            "GROUP BY city ORDER BY n DESC").fetchall()}
        fts = fts_row_count(conn) if fts_available() else None
    return {
        "documents": docs,
        "chunks": chunks,
        "vectors": vectors,
        "vector_dims": dims,
        "fts_rows": fts,
        "fts_synced": (fts == chunks) if fts is not None else False,
        "by_kind": by_kind,
        "by_city": by_city,
        "embed_enabled": embed_enabled(),
        "embed_model": EMBED_MODEL,
        "embed_circuit": circuit_reason(),
        "vector_ready": vectors > 0,
    }


def list_documents(limit: int = 100) -> List[Dict[str, Any]]:
    """列出已索引的文档（调试与验收用）。"""
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT d.doc_key, d.kind, d.city, d.category, d.title, d.source,
                      d.char_count, d.updated_at,
                      (SELECT count(*) FROM doc_chunks c WHERE c.document_id = d.id) AS chunks
               FROM documents d ORDER BY d.updated_at DESC LIMIT ?""",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]
