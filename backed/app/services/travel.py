"""【已下线】旧版行程推荐服务，对应 Java 版 TravelServiceImpl。

.. warning::

   本模块的 ``recommend()`` **不再对外暴露任何路由**（原
   ``POST /api/travel/recommend`` 已下线），保留目的只有一个：
   作为本项目「纯 LLM 直出 → 算法引擎决策」这段架构演进的对照物。

   它为什么被下线：``build_travel_prompt()`` 让大模型直接编造
   「每天上午/下午/晚上去哪个景点、门票多少、游览多久、预算怎么分」，
   产出**无法被校验**——实体校验（``polish._validate_entities``）需要一个
   基准名单，而这里的 LLM 输出本身就是行程，不存在 ground truth。
   这与项目「算法决策、LLM 只负责表达」的核心架构直接冲突。

   新代码请使用 ``app.services.plan.plan``（对应 ``POST /api/plan``）。
"""
import json
import re

from app.llm import chat
from app.schemas import RecommendVO


def build_travel_prompt(city: str, budget: float, days: int) -> str:
    return (
        "你是一个专业的旅游规划师。根据以下信息为用户生成旅游规划：\n"
        f"- 目的地城市：{city}\n"
        f"- 预算：{budget}元\n"
        f"- 旅行天数：{days}天\n\n"
        "要求：每天安排上午、下午、晚上三个时段的景点/活动，包含景点名称、游览时长、门票、交通方式、简短介绍；给出预算分配和注意事项。\n"
        "只返回如下 JSON 结构，不要任何多余文字或代码块标记：\n"
        "{\n"
        "\"success\": true,\n"
        "\"city\": \"城市名\",\n"
        "\"days\": 天数,\n"
        "\"totalBudget\": 总预算,\n"
        "\"dailyItinerary\": [\n"
        "  {\n"
        "    \"day\": 1,\n"
        "    \"date\": \"第1天\",\n"
        "    \"morning\": {\"spot\": \"景点名\", \"duration\": \"时长\", \"ticket\": \"门票\", \"transportation\": \"交通\", \"description\": \"介绍\"},\n"
        "    \"afternoon\": {\"spot\": \"景点名\", \"duration\": \"时长\", \"ticket\": \"门票\", \"transportation\": \"交通\", \"description\": \"介绍\"},\n"
        "    \"evening\": {\"spot\": \"活动名\", \"duration\": \"时长\", \"ticket\": \"费用\", \"transportation\": \"交通\", \"description\": \"介绍\"}\n"
        "  }\n"
        "],\n"
        "\"budgetBreakdown\": {\"accommodation\": \"住宿费用\", \"food\": \"餐饮费用\", \"transportation\": \"交通费用\", \"tickets\": \"门票费用\", \"other\": \"其他费用\"},\n"
        "\"tips\": [\"提示1\", \"提示2\"],\n"
        "\"warnings\": [\"注意事项1\", \"注意事项2\"]\n"
        "}\n"
    )


def extract_json(response: str) -> str | None:
    """从模型回复中提取 JSON 片段，对应 Java 版 extractJson。"""
    if not response:
        return None
    patterns = [r"```json\n([\s\S]*?)\n```", r"```\n([\s\S]*?)\n```"]
    for pattern in patterns:
        m = re.search(pattern, response)
        if m:
            return m.group(1)
    start = response.find("{")
    end = response.rfind("}")
    if start != -1 and end != -1 and end > start:
        return response[start:end + 1]
    return None


def parse_travel_response(response: str) -> RecommendVO:
    """解析模型回复为 RecommendVO，对应 Java 版 parseTravelResponse。"""
    try:
        json_content = extract_json(response)
        if json_content:
            data = json.loads(json_content)
            return RecommendVO(**data)
        return RecommendVO(success=False, error="未能从响应中提取JSON", rawResponse=response)
    except Exception as e:
        print(f"解析旅游响应失败: {e}")
        return RecommendVO(success=False, error="JSON解析失败", rawResponse=response)


async def recommend(city: str, budget: float, days: int) -> RecommendVO:
    prompt = build_travel_prompt(city, budget, days)
    try:
        response = await chat("", prompt)
        return parse_travel_response(response)
    except Exception as e:
        print(f"LLM API调用失败: {e}")
        return RecommendVO(success=False, error="LLM API调用失败")
