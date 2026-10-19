"""会话 CRUD（阶段 4）。

**越权隔离**：查会话时必须带上 user_id 一起过滤，查不到就返回 None
（路由层再转成 404）。**不能先按 id 查到记录再比较 user_id**——
那样即使最终拒绝，也已经在代码路径里把"这个 id 存在"泄露给了调用方。
"""
import json
import logging
import uuid
from typing import Any, Dict, List, Optional

from app.db.database import get_conn, utc_now_iso

logger = logging.getLogger("app.services.agent_session")

TITLE_LIMIT = 24
DEFAULT_PAGE_SIZE = 20


def create(user_id: Optional[int], first_message: str = "") -> str:
    """新建会话，返回 session_id。"""
    session_id = uuid.uuid4().hex[:16]
    title = (" ".join((first_message or "").split()))[:TITLE_LIMIT] or "新会话"
    now = utc_now_iso()
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO agent_sessions
               (id, user_id, title, created_at, updated_at, message_count)
               VALUES (?,?,?,?,?,0)""",
            (session_id, user_id, title, now, now),
        )
    return session_id


def touch(session_id: str) -> None:
    """更新会话时间戳与消息计数。"""
    if not session_id:
        return
    with get_conn() as conn:
        conn.execute(
            """UPDATE agent_sessions
               SET updated_at = ?,
                   message_count = (SELECT count(*) FROM agent_messages
                                    WHERE session_id = ?)
               WHERE id = ?""",
            (utc_now_iso(), session_id, session_id),
        )


def append_message(session_id: str, role: str, content: str) -> None:
    """追加一条消息。"""
    if not session_id or not (content or "").strip():
        return
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO agent_messages (session_id, role, content, created_at) "
            "VALUES (?,?,?,?)",
            (session_id, role, content, utc_now_iso()),
        )


def get(session_id: str, user_id: Optional[int]) -> Optional[Dict[str, Any]]:
    """取会话详情（含消息与工具调用时间线）。

    匿名会话（user_id 为 NULL）只有匿名调用方能读；登录用户只能读自己的。
    """
    if not session_id:
        return None
    with get_conn() as conn:
        # **把 user_id 放进 WHERE**，而不是查到以后再比
        if user_id:
            row = conn.execute(
                "SELECT * FROM agent_sessions WHERE id = ? AND user_id = ?",
                (session_id, user_id)).fetchone()
        else:
            row = conn.execute(
                "SELECT * FROM agent_sessions WHERE id = ? AND user_id IS NULL",
                (session_id,)).fetchone()
        if not row:
            return None
        messages = [dict(m) for m in conn.execute(
            "SELECT role, content, created_at FROM agent_messages "
            "WHERE session_id = ? ORDER BY id", (session_id,)).fetchall()]
        calls = [dict(c) for c in conn.execute(
            "SELECT round, tool_name, arguments, result_ok, result_summary, "
            "error, code, is_repeat, elapsed_ms, created_at FROM tool_calls "
            "WHERE session_id = ? ORDER BY id", (session_id,)).fetchall()]
    return {
        "session_id": row["id"],
        "title": row["title"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "message_count": row["message_count"],
        "messages": messages,
        "tool_calls": calls,
    }


def list_sessions(user_id: Optional[int], page: int = 1,
                  page_size: int = DEFAULT_PAGE_SIZE) -> Dict[str, Any]:
    """列出会话（只列自己的）。"""
    page = max(1, int(page))
    size = max(1, min(int(page_size), 50))
    offset = (page - 1) * size
    with get_conn() as conn:
        if user_id:
            where, params = "user_id = ?", (user_id,)
        else:
            where, params = "user_id IS NULL", ()
        total = int(conn.execute(
            f"SELECT count(*) FROM agent_sessions WHERE {where}", params).fetchone()[0])
        rows = conn.execute(
            f"""SELECT id, title, created_at, updated_at, message_count
                FROM agent_sessions WHERE {where}
                ORDER BY updated_at DESC LIMIT ? OFFSET ?""",
            (*params, size, offset)).fetchall()
    return {"total": total, "page": page, "page_size": size,
            "items": [dict(r) for r in rows]}


def delete(session_id: str, user_id: Optional[int]) -> bool:
    """删除会话及其消息与工具调用记录。返回是否确实删除了。"""
    if not session_id:
        return False
    with get_conn() as conn:
        if user_id:
            cur = conn.execute(
                "DELETE FROM agent_sessions WHERE id = ? AND user_id = ?",
                (session_id, user_id))
        else:
            cur = conn.execute(
                "DELETE FROM agent_sessions WHERE id = ? AND user_id IS NULL",
                (session_id,))
        if cur.rowcount <= 0:
            return False
        conn.execute("DELETE FROM agent_messages WHERE session_id = ?", (session_id,))
        conn.execute("DELETE FROM tool_calls WHERE session_id = ?", (session_id,))
    return True
