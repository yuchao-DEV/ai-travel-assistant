"""确定性兜底：**不依赖 LLM 也能产出完整行程**。

这是整个 Agent 最重要的一条退路（清单里出口验收把它标成"最重要的一条"）。
触发场景：

- ``LLM_TOOLS_ENABLED=off``（显式关闭工具调用）
- LLM 服务不可用 / 超时 / 连续报错
- 主循环触发了轮数/token/重复调用上限

做法是复用项目**已有的规则式解析器**（`nl_parse._rule_parse`）从用户原话里
抽出城市、天数、预算、标签、节奏，然后直接调用 ``plan_itinerary`` 工具。
全程是确定性代码，没有一次 LLM 调用。

**能用规则解析兜底，是因为项目从一开始就把"解析"和"生成"分开了**——
`nl_parse` 的规则路径本来就能独立工作（LLM 只是它的增强）。
"""
import logging
from typing import Any, AsyncIterator, Dict, List, Optional

from app.agent.events import (ChunkEvent, DoneEvent, ErrorEvent, ItineraryEvent,
                              StatusEvent, ToolEndEvent, ToolStartEvent)
from app.agent.context import RunContext
from app.agent.results import ToolResult

logger = logging.getLogger("app.agent.fallback")

MIN_FALLBACK_DAYS = 1
DEFAULT_DAYS = 2
DEFAULT_BUDGET = 1500.0


def extract_params(message: str) -> Dict[str, Any]:
    """用规则解析器从原话里抽参数（无 LLM）。"""
    from app.services.nl_parse import _rule_parse

    try:
        parsed = _rule_parse(message or "") or {}
    except Exception as exc:      # 规则解析不该失败，但兜底里更不能崩
        logger.warning("规则解析失败，用空参数兜底：%s", exc)
        parsed = {}
    return parsed


def can_plan(params: Dict[str, Any]) -> bool:
    """参数是否够规划一条行程（至少要有城市）。"""
    return bool((params.get("city") or "").strip())


async def run_fallback(
    message: str,
    ctx: RunContext,
    *,
    reason: str = "fallback",
    notify: bool = True,
) -> AsyncIterator[Any]:
    """确定性兜底的事件流。

    ``notify=True`` 时先说明"智能模式不可用，已改用基础规划"——
    用户有权知道当前走的是哪条路。
    """
    if notify:
        yield StatusEvent(text="正在用基础模式为你规划…", stage="fallback")

    params = extract_params(message)
    city = (params.get("city") or "").strip()
    if not can_plan(params):
        yield ErrorEvent(
            message="没能从你的描述里认出目的地城市。请告诉我城市名，"
                    "例如「帮我规划杭州 3 天，预算 3000」。",
            code="fallback_no_city",
        )
        yield DoneEvent(reason="fallback_no_city", rounds=0, tool_calls=0)
        return

    days = int(params.get("days") or DEFAULT_DAYS)
    days = max(MIN_FALLBACK_DAYS, days)
    budget = float(params.get("budget") or DEFAULT_BUDGET)
    tags: List[str] = list(params.get("tags") or [])
    profile = params.get("profile") or "经典"

    args = {"city": city, "days": days, "budget": budget, "tags": tags,
            "profile": profile,
            "must_go": list(params.get("must_go") or []),
            "avoid": list(params.get("avoid") or [])}

    yield ToolStartEvent(call_id="fallback_plan", name="plan_itinerary",
                         args=args, round=0)
    from app.agent.registry import execute
    result: ToolResult = await execute("plan_itinerary", args,
                                       user_id=ctx.user_id)
    if result.ok:
        yield ToolEndEvent(call_id="fallback_plan", name="plan_itinerary",
                           ok=True, elapsed_ms=result.elapsed_ms,
                           summary=f"{city} {days} 天行程")
        yield ItineraryEvent(itinerary=result.data)
        yield ChunkEvent(text=(
            f"已为你生成 {city} 的 {days} 天行程（基础模式）。"
            f"共 {len(result.data.get('plan') or [])} 天安排，"
        ))
    else:
        yield ToolEndEvent(call_id="fallback_plan", name="plan_itinerary",
                           ok=False, elapsed_ms=result.elapsed_ms,
                           error=result.error, code=result.code)
        yield ErrorEvent(message=f"规划失败：{result.error}", code=result.code)
    yield DoneEvent(reason=reason, rounds=0, tool_calls=1,
                    usage=ctx.budget.to_dict() if ctx.budget else {})
