"""Agent SSE 路由服务：把 :class:`AgentEvent` 包成 ``data: {...}\\n\\n``。

单独一层的原因：路由函数只该负责 HTTP（状态码、头、依赖注入），
事件流的组装与异常兜底属于业务逻辑，放这里才测得动。
"""
import logging
from typing import Any, AsyncGenerator, Optional

from app.agent.context import RunContext
from app.agent.events import (ChunkEvent, DoneEvent, ErrorEvent, SessionEvent,
                              StatusEvent)
from app.agent.orchestrator import AgentOrchestrator
from app.services import agent_session

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
    message = (getattr(dto, "message", "") or "").strip()
    history = getattr(dto, "history", None)

    if not message:
        yield ErrorEvent(message="消息不能为空", code="empty_message").to_sse()
        yield DoneEvent(reason="error").to_sse()
        return

    # ---- 会话落库（仅登录用户）----
    #
    # 归属校验很重要：前端回传的 session_id 不可信，任何人猜到 id 就能
    # 往别人的会话里写内容。belongs_to() 只做一次轻量查询。
    session_id = (session_id or "").strip()
    if user_id:
        if not session_id or not agent_session.belongs_to(session_id, user_id):
            session_id = agent_session.create(user_id, message)
        agent_session.append_message(session_id, "user", message)
        # 先告诉前端会话 id，后续轮次带回来才能续上同一会话
        yield SessionEvent(session_id=session_id).to_sse()
    else:
        session_id = ""

    ctx = RunContext(user_id=user_id, session_id=session_id)
    orchestrator = AgentOrchestrator(ctx)

    # 前端传来的历史（多轮对话）先注入，让模型能看到上下文
    if history:
        for item in history[-10:]:
            role = "user" if getattr(item, "role", "") == "user" else "assistant"
            text = (getattr(item, "content", "") or "").strip()
            if text:
                orchestrator.messages.append({"role": role, "content": text})

    answer: list[str] = []
    try:
        async for event in orchestrator.run(message):
            if isinstance(event, ChunkEvent) and event.text:
                answer.append(event.text)
            yield event.to_sse()
    except Exception as exc:      # 兜底中的兜底
        logger.error("Agent 会话异常终止：%s", exc, exc_info=True)
        yield ErrorEvent(message=f"会话异常终止：{exc}", code="internal").to_sse()
        yield DoneEvent(reason="error").to_sse()
    finally:
        # 助手最终答复 + 刷新会话元信息。落库失败绝不能影响已经推给用户的内容。
        if user_id and session_id:
            try:
                text = "".join(answer).strip()
                if text:
                    agent_session.append_message(session_id, "assistant", text)
                agent_session.touch(session_id)
            except Exception as exc:
                logger.warning("会话落库失败（不影响对话）：%s", exc)
