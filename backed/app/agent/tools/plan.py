"""核心工具：``plan_itinerary``。

**这是整个 Agent 里最重要、也最容易做错的一个工具。**

它一次调用就产出完整行程（选点 → 聚类 → 背包 → 路径 → 排程 → 预算），
内部完全是算法引擎在工作，模型只负责给参数。这样安排的理由：

- **决策权留在算法里**。如果让模型自己挑景点、自己排顺序，就回到了"LLM 编造
  行程"的老路，本项目从一开始就在避免这件事（见 `travel.py` 的下线记录）。
- **一次调用比十次拼装更省 token**，也更不容易中途出错。
- 输出带 ``is_itinerary=True``，主循环据此发出 ``ItineraryEvent``，
  前端直接渲染行程卡。

模型拿不到"再调一次改一个点"的能力是刻意的：要改就走这里重新规划
（``must_go`` / ``avoid`` / ``profile`` 都是入参），而不是让它手工拼行程。
"""
import logging

from app.agent.registry import tool
from app.agent.results import ToolErrorCode, ToolResult
from app.config import MAX_TRIP_DAYS, MIN_TRIP_DAYS
from app.schemas import VALID_PROFILES, VALID_TAGS, PlanRequestDTO
from app.services.plan import plan

logger = logging.getLogger("app.agent.tools.plan")


def _itinerary_payload(vo, request: dict | None = None) -> dict:
    """把 ItineraryVO 压成给模型/前端用的结构。

    带上 ``cost_note`` 与 ``cost_source``——门票四档口径必须一起传出去，
    否则模型在写文案时会把"预留"当成真实票价说出来。

    ``request`` 里带回**这次的规划入参**（出发地/兴趣/必去/避开）。
    为什么必须带：前端「在时间线中查看」会跳 `/timeline` 并**重新规划**，
    而 Timeline 的入参全部来自 URL query。不带回去的话，用助手说
    "从北京出发去亳州三天"，点进去就变成「当涂出发」、兴趣标签也丢了，
    行程和助手刚给出的那份可能不一样——这是实测到的问题。
    """
    return {
        "city": vo.city,
        "origin": (request or {}).get("origin") or "",
        "tags": list((request or {}).get("tags") or []),
        "must_go": list((request or {}).get("must_go") or []),
        "avoid": list((request or {}).get("avoid") or []),
        "days": vo.days and len(vo.days) or 0,
        "enhanced": vo.enhanced,
        "profile": vo.profile,
        "weather": list(vo.weather or []),
        "description": vo.description or "",
        "plan": [
            {
                "date": day.date,
                "daily_summary": day.daily_summary or "",
                "items": [
                    {
                        "name": it.name,
                        "type": it.type,
                        "start": it.start,
                        "end": it.end,
                        "cost": it.cost,
                        "cost_source": it.cost_source,
                        "cost_note": it.cost_note or "",
                        "open_hours": it.open_hours or "",
                        "score": it.score,
                        "lat": it.lat,
                        "lng": it.lng,
                    }
                    for it in day.items
                ],
            }
            for day in (vo.days or [])
        ],
    }


@tool(name="plan_itinerary", core=True, timeout=45.0)
async def plan_itinerary(
    city: str,
    days: int,
    tags: list[str] | None = None,
    profile: str = "经典",
    must_go: list[str] | None = None,
    avoid: list[str] | None = None,
    origin: str = "",
) -> ToolResult:
    """用算法引擎生成一份完整行程（这是产出行程的唯一方式）。

    city: 目的地城市。
    days: 天数。
    tags: 兴趣标签，只能从 历史/自然/美食/购物/艺术/夜生活 里选。
    profile: 出行风格，悠闲 / 经典 / 尽览。
    must_go: 用户明确要求必去的景点名；在算法里是**硬约束**，不会被挤掉。
    avoid: 用户明确要求避开的地点。
    origin: 出发地城市。用户说了「从 X 出发」就**必须**传——否则前端行程页
        会退回默认的「当涂」，城际距离与交通全按错误起点算。
    """
    name = (city or "").strip()
    if not name:
        return ToolResult.failure("city 不能为空", code=ToolErrorCode.INVALID_ARGS)

    clean_tags = [t for t in (tags or []) if t in VALID_TAGS]
    dropped_tags = [t for t in (tags or []) if t not in VALID_TAGS]
    profile_clean = profile if profile in VALID_PROFILES else "经典"

    try:
        req = PlanRequestDTO(
            city=name,
            days=int(days),
            tags=clean_tags,
            profile=profile_clean,
            must_go=[m for m in (must_go or []) if (m or "").strip()],
            avoid=[a for a in (avoid or []) if (a or "").strip()],
        )
    except Exception as exc:
        return ToolResult.failure(
            f"规划参数不合法：{exc}", code=ToolErrorCode.INVALID_ARGS,
            hint=f"天数需在 {MIN_TRIP_DAYS}-{MAX_TRIP_DAYS} 之间，预算不低于 100 元")

    try:
        vo = await plan(req)
    except Exception as exc:
        logger.warning("plan_itinerary 失败：%s", exc, exc_info=True)
        return ToolResult.failure(f"规划失败：{exc}", code=ToolErrorCode.INTERNAL,
                                  hint="可以换一组参数重试，或先只回答用户的其他问题")

    payload = _itinerary_payload(vo, {
        "origin": (origin or "").strip(),
        "tags": clean_tags,
        "must_go": list(req.must_go or []),
        "avoid": list(req.avoid or []),
    })
    hint = ""
    if dropped_tags:
        hint = (f"以下标签不在允许范围内、已被忽略：{dropped_tags}；"
                f"可用标签：{'、'.join(VALID_TAGS)}")
    if not payload["plan"]:
        return ToolResult.failure(
            "没有生成出任何行程",
            code=ToolErrorCode.UPSTREAM,
            hint="可能是该城市地点数据不足或地图服务不可用，请如实告诉用户")
    return ToolResult.success(
        payload, is_itinerary=True, hint=hint,
        # 前端「在时间线中查看」直接用这份完整行程渲染，不再重新规划
        itinerary_full=vo.model_dump(),
    )
