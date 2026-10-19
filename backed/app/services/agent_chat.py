"""Agent SSE 路由服务：把 :class:`AgentEvent` 包成 ``data: {...}\\n\\n``。

单独一层的原因：路由函数只该负责 HTTP（状态码、头、依赖注入），
事件流的组装与异常兜底属于业务逻辑，放这里才测得动。
"""
import logging
from typing import Any, AsyncGenerator, Optional

from app.agent.context import RunContext
from app.agent.events import DoneEvent, ErrorEvent, StatusEvent
from app.agent.orchestrator import AgentOrchestrator

logger = logging.getLogger("app.services.agent_chat")


async def agent_chat_stream(
    dto: Any,
    *,
    user_id: Optional[int] = None,
    session_id: str = "",
) -> AsyncGenerator[str, None]:
    """产出 SSE 文本流。

    **异常也要收成事件**：SSE 一旦开始推送就无法改 HTTP 状态码了，
    如果直接抛异常，前端只会看到连接莫名中断、拿不到任何原因。
    所以这里统一捕获并转成 ``error`` + ``done`` 两个事件。
    """
    ctx = RunContext(user_id=user_id, session_id=session_id or "")
    orchestrator = AgentOrchestrator(ctx)
    message = (getattr(dto, "message", "") or "").strip()
    history = getattr(dto, "history", None)

    if not message:
        yield ErrorEvent(message="消息不能为空", code="empty_message").to_sse()
        yield DoneEvent(reason="error").to_sse()
        return

    # 前端传来的历史（多轮对话）先注入，让模型能看到上下文
    if history:
        for item in history[-10:]:
            role = "user" if getattr(item, "role", "") == "user" else "assistant"
            text = (getattr(item, "content", "") or "").strip()
            if text:
                orchestrator.messages.append({"role": role, "content": text})

    try:
        async for event in orchestrator.run(message):
            yield event.to_sse()
    except Exception as exc:      # 兜底中的兜底
        logger.error("Agent 会话异常终止：%s", exc, exc_info=True)
        yield ErrorEvent(message=f"会话异常终止：{exc}", code="internal").to_sse()
        yield DoneEvent(reason="error").to_sse()
