"""RAG（检索增强生成）骨架。

本包只做**检索**，不含任何 Agent 逻辑。分四层：

- :mod:`app.rag.tokenize`  —— 中文 bigram 分词（FTS5 的中文检索基础）
- :mod:`app.rag.embedding` —— 外部 Embedding 客户端（含缓存与降级）
- :mod:`app.rag.pipeline`  —— 文档解析 → 清洗 → 切分 → 元数据
- :mod:`app.rag.indexer`   —— 双索引写入（FTS5 全文 + 向量）
- :mod:`app.rag.retriever` —— 混合检索（BM25 + 向量 + RRF 融合）

**降级是这条链路的一等公民**：没有配置 Embedding 服务时，检索自动退化为纯
BM25，功能不中断、不抛异常，只在结果里标注 ``vector_ready=False``。
这与项目「高德不可用就降级、短信通道未接入也能跑」的做法一致。
"""
from app.rag.embedding import cosine, embed_texts, pack, unpack
from app.rag.pipeline import Chunk, chunk_document
from app.rag.retriever import RetrievedChunk, hybrid_search
from app.rag.tokenize import STOPWORDS, tokenize_zh

__all__ = [
    "STOPWORDS",
    "Chunk",
    "RetrievedChunk",
    "chunk_document",
    "cosine",
    "embed_texts",
    "hybrid_search",
    "pack",
    "tokenize_zh",
    "unpack",
]
