"""记忆工具：``recall_history``。

让 Agent 知道用户以前规划过什么（"上次去的苏州那条线路"）。**必须登录**
（`requires_user=True`）——匿名用户没有可回忆的历史。

阶段 4 会在这里再加 ``recall_preferences``（长期偏好画像）。本阶段只做历史行程。
"""
import logging

from app.agent.registry import tool
from app.agent.results import ToolErrorCode, ToolResult
from app.services.history import list_itineraries

logger = logging.getLogger("app.agent.tools.memory")


@tool(name="recall_preferences", core=False, requires_user=True, timeout=15.0)
async def recall_preferences() -> ToolResult:
    """读取当前用户的长期偏好画像（节奏倾向、预算档位、同行人、饮食禁忌）。

    **只能作为参考**：与用户本次明确说的要求冲突时，一律以本次为准。
    置信度低（< 0.3）时不要按画像推测，应主动向用户确认。
    """
    from app.agent.context import current_user_id
    from app.agent.memory import load_profile

    user_id = current_user_id()
    if not user_id:
        return ToolResult.failure("尚未登录，无法读取偏好画像",
                                  code=ToolErrorCode.NEEDS_LOGIN)
    profile = load_profile(user_id)
    if not profile:
        return ToolResult.success(
            {"has_profile": False},
            hint="该用户还没有画像；请通过对话了解偏好，不要凭空假设")
    return ToolResult.success({
        "has_profile": True,
        "pace": profile.get("pace"),
        "budget_band": profile.get("budget_band"),
        "companions": profile.get("companions") or [],
        "dietary": profile.get("dietary") or [],
        "interests": profile.get("interests") or {},
        "confidence": profile.get("confidence"),
        "sample_size": profile.get("sample_size"),
        "note": "这是历史推断，仅供参考；与用户本次明确要求冲突时以本次为准",
    })


@tool(name="recall_history", core=True, requires_user=True, timeout=15.0)
async def recall_history(limit: int = 5) -> ToolResult:
    """读取当前用户保存过的历史行程（用于回答"我上次那条线路"这类问题）。

    limit: 最多返回几条（1-10）。

    只能读取**当前登录用户自己**的行程；未登录时本工具不可用。
    """
    count = max(1, min(int(limit or 5), 10))
    # user_id 由主循环注入（registry 执行时通过闭包参数传入），
    # 这里通过 contextvar 读取，避免把内部参数暴露进 JSON Schema。
    from app.agent.context import current_user_id

    user_id = current_user_id()
    if not user_id:
        return ToolResult.failure("尚未登录，无法读取历史行程",
                                  code=ToolErrorCode.NEEDS_LOGIN)
    try:
        page = list_itineraries(user_id, page=1, page_size=count)
    except Exception as exc:
        logger.warning("读取历史行程失败：%s", exc)
        return ToolResult.failure(f"读取历史行程失败：{exc}",
                                  code=ToolErrorCode.INTERNAL)
    items = getattr(page, "items", None) or []
    if not items:
        return ToolResult.success(
            {"count": 0, "trips": []},
            hint="该用户还没有保存过行程，可以正常为他规划一条新的",
        )
    return ToolResult.success({
        "count": len(items),
        "trips": [
            {
                "title": getattr(it, "title", ""),
                "city": getattr(it, "city", ""),
                "days": getattr(it, "days", 0),
                "budget": getattr(it, "budget", 0),
                "created_at": getattr(it, "created_at", ""),
            }
            for it in items
        ],
    })
