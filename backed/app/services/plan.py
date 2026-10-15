"""行程规划编排服务：串联算法引擎 + LLM 润色。

流程：PlanRequestDTO → engine.solve() → polish.polish_itinerary() → ItineraryVO
"""
from app.algorithms.engine import solve as engine_solve
from app.errors import ApiError
from app.schemas import ItineraryVO, PlanRequestDTO
from app.services.polish import polish_itinerary


async def plan(req: PlanRequestDTO) -> ItineraryVO:
    """完整规划流程：算法决策 → LLM 表达。"""
    # 1. 算法引擎做决策（选点/排序/排程/预算）
    itinerary = await engine_solve(req)

    # 2. LLM 只做润色（顶层描述 + 每日导语），带实体校验
    if itinerary.enhanced and itinerary.days:
        itinerary = await polish_itinerary(itinerary)

    # 3. **空行程绝不能当正常结果返回。**
    #
    # 实测踩过：高德地点搜索配额用尽时（infocode 10002），引擎走兜底返回
    # "3 天 × 0 个安排、总花费 ¥0"，而这段代码把它原样放行，
    # 界面显示成「3 天 / 0 个安排 / ¥0」+ 三个「未知」标签。
    # 用户会以为产品坏了，却看不到任何原因。
    #
    # 宁可明确报错，也不给一个看起来"成功"的空结果——
    # 这与项目一贯的「绝不编造、标注估算或省略」是同一条原则。
    if not any(day.items for day in (itinerary.days or [])):
        # 注意 ApiError 的签名是 (message, code=400, status_code=None)——
        # 第一个参数是消息，不是状态码。
        raise ApiError(
            "暂时取不到该城市的地点数据，无法生成行程。"
            "常见原因是地图服务额度用尽或服务波动，请稍后重试或换一个城市。",
            code=503,
        )

    return itinerary
