"""知识库检索工具：``search_knowledge``。

这是 RAG 与 Agent 的接合点：模型的"常识"不可靠（预约规则、闭馆日、票价口径
这些必须来自真实语料），所以凡是涉及"要不要预约""周一开不开""多少钱"的问题，
都应该走这个工具，而不是靠模型记忆回答。

返回值里带 ``chunk_id`` 与来源标题，主循环据此发 ``KnowledgeEvent``，
让用户能看到答案的依据。
"""
import logging

from app.agent.registry import tool
from app.agent.results import ToolErrorCode, ToolResult
from app.rag.retriever import hybrid_search

logger = logging.getLogger("app.agent.tools.knowledge")


@tool(name="search_knowledge", core=True, timeout=20.0)
async def search_knowledge(query: str, city: str = "", top_k: int = 5) -> ToolResult:
    """检索本地知识库（城市攻略、预约规则、费用口径、系统能力边界等）。

    query: 要查的问题，用自然语言，如「灵隐寺要预约吗」。
    city: 可选，限定城市；通用规则类内容不受城市限制。
    top_k: 返回条数（1-10）。

    凡涉及预约、闭馆、票价、注意事项的问题都应先用它查证——
    **不要凭记忆回答这些**。
    """
    q = (query or "").strip()
    if not q:
        return ToolResult.failure("query 不能为空", code=ToolErrorCode.INVALID_ARGS)
    top_k = max(1, min(int(top_k or 5), 10))
    try:
        result = await hybrid_search(q, city=(city or "").strip() or None, top_k=top_k)
    except Exception as exc:
        logger.warning("知识库检索失败：%s", exc)
        return ToolResult.failure(f"知识库检索失败：{exc}",
                                  code=ToolErrorCode.INTERNAL)

    chunks = result.get("results") or []
    if not chunks:
        return ToolResult.success(
            {"query": q, "city": city, "count": 0, "chunks": []},
            hint="知识库里没有相关内容；不要凭记忆编造，可以如实告诉用户暂时查不到",
        )

    return ToolResult.success({
        "query": q,
        "city": city,
        "count": len(chunks),
        # 只给模型必要字段，photos 之类的重字段不透传
        "chunks": [
            {
                "chunk_id": c["chunk_id"],
                "title": c["title"],
                "heading": c.get("heading_path", ""),
                "content": c["content"],
                "city": c.get("city"),
            }
            for c in chunks
        ],
        # 向量检索是否可用要如实带出去：纯 BM25 时召回可能偏窄
        "vector_ready": result.get("vector_ready", False),
        "note": result.get("note", ""),
    })
