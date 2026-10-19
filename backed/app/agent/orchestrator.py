"""Agent 主循环：调用模型 → 执行工具 → 把结果回灌 → 直到产出答复或耗尽预算。

## 三个关键取舍

**① 循环内不做流式工具调用。** 主循环用非流式 ``chat_messages``，因为流式下
``tool_calls`` 的 JSON 参数是**分片到达**的，拼接与容错复杂度极高，而这些复杂度
换不来用户体验——用 ``status`` 事件 + 工具卡片填满"思考中"的空窗就够了。
只有**最终答复**用流式逐字输出。

**② 重复调用检测。** 模型卡住时最典型的表现是拿完全相同的参数反复调同一个工具。
第二次不执行，直接把上次结果重发并附 ``hint`` 引导它换思路；连续三次则中断走兜底。
不这样做的话，一次会话能把配额烧光却什么也没产出。

**③ 工具集选择性挂载。** 14 个工具的 schema 约 2,100 token，每轮都要重发。
首轮只挂 6 个 CORE（约 900 token），只有用户话里出现"调整/换顺序/太满/超预算/
便宜点/为什么"这类**追问意图**时才挂全量。

## 落库

每次工具调用都写 ``tool_calls`` 表（参数、成败、错误码、耗时、是否重复）。
排障时"模型到底查了什么"全靠它。
"""
import json
import logging
import time
import uuid
from typing import Any, AsyncIterator, Dict, List, Optional, Set

from app.agent.context import RunContext, compact_messages
from app.agent.events import (AgentEvent, ChunkEvent, DoneEvent, ErrorEvent,
                              ItineraryEvent, KnowledgeEvent, ProfileUpdateEvent,
                              StatusEvent, ThoughtEvent, ToolEndEvent, ToolStartEvent)
from app.agent.protocol import SYSTEM_PROMPT, validate_answer
from app.agent.registry import execute, schemas
from app.agent.results import ToolErrorCode, ToolResult
from app.config import LLM_TOOLS_ENABLED
from app.db.database import get_conn, utc_now_iso
from app.llm import chat_messages

# 导入工具包即完成注册（@tool 装饰器在导入时执行）。
# **必须在这里 import**：orchestrator 是工具的唯一消费者，有它就一定有工具。
# 早先漏了这一步，导致路由起来了但注册表是空的——兜底里 plan_itinerary
# 报 unknown_tool，整条兜底路径失效（端到端验证抓到的 bug）。
import app.agent.tools  # noqa: E402,F401  isort:skip

logger = logging.getLogger("app.agent.orchestrator")

CORE_TOOLS = ["resolve_city", "search_knowledge", "get_weather",
              "search_poi", "plan_itinerary", "recall_history"]
ADVANCED_TOOLS = ["optimize_route", "schedule_day",
                  "filter_candidates", "score_poi", "estimate_intercity",
                  "get_poi_detail", "get_city_intro", "recall_preferences"]

# 出现这些信号时说明用户在追问/要求调整 → 需要全量工具
_ADVANCED_SIGNALS = (
    "调整", "换顺序", "换个顺序", "重排", "顺序", "太满", "太赶", "太累",
    "超预算", "便宜", "省钱", "贵了", "为什么", "怎么选", "解释", "对比",
    "优化", "路线", "详细", "细节", "预算够", "够不够",
)

# 结果摘要里单个字符串的最大长度（落库用，避免把整个行程塞进去）
_SUMMARY_LIMIT = 300


def wants_advanced_tools(message: str) -> bool:
    """用户这句话是否暗示需要细粒度工具。"""
    text = message or ""
    return any(sig in text for sig in _ADVANCED_SIGNALS)


def tools_enabled() -> bool:
    """工具调用是否可用。``off`` 时主循环不该被启动。"""
    mode = (LLM_TOOLS_ENABLED or "auto").lower()
    return mode != "off"


def _summarize(result: ToolResult) -> str:
    """把工具结果压成一句话，用于落库与前端卡片。"""
    if not result.ok:
        return (result.error or "")[:_SUMMARY_LIMIT]
    data = result.data
    if isinstance(data, dict):
        if "count" in data:
            return f"命中 {data.get('count')} 条"
        if "plan" in data:
            days = len(data.get("plan") or [])
            return f"{data.get('city', '')} {days} 天行程，共 {sum(len(d.get('items') or []) for d in (data.get('plan') or []))} 个安排"
        if "name" in data:
            return str(data.get("name"))[:_SUMMARY_LIMIT]
    return "执行成功"


class AgentOrchestrator:
    """一次 Agent 会话的执行器。"""

    def __init__(self, ctx: Optional[RunContext] = None):
        self.ctx = ctx or RunContext()
        if not self.ctx.session_id:
            self.ctx.session_id = uuid.uuid4().hex[:16]
        self.messages: List[Dict[str, Any]] = []
        self._last_signature: Optional[str] = None
        self._repeat_streak = 0
        self._last_tool_result: Optional[ToolResult] = None
        self._last_tool_name: str = ""
        self._known_chunk_ids: Set[int] = set()
        self._itinerary: Optional[Dict[str, Any]] = None
        self._final_text: str = ""
        # 中断标志：_run_tool 里走兜底后必须让主循环**真的停下**。
        # （早先只 yield 了兜底事件却没中断，主循环会继续调模型，
        #   结果兜底行程和正常答复一起推给用户——测试抓到的 bug）
        self._aborted: bool = False
        # 本轮用户消息：收尾时画像观察要用（run() 的入参在 _finish 里拿不到）
        self._user_message: str = ""

    # ------------------------------------------------------------ 重复调用检测
    def _signature(self, name: str, args: Dict[str, Any]) -> str:
        return f"{name}|{json.dumps(args, sort_keys=True, ensure_ascii=False)}"

    def _is_repeat(self, name: str, args: Dict[str, Any]) -> bool:
        """同一工具 + 完全相同的参数，与**上一次**调用相同即判定重复。"""
        return self._signature(name, args) == self._last_signature

    # ------------------------------------------------------------ 落库
    def _record(self, *, round_no: int, name: str, args: Dict[str, Any],
                result: ToolResult, is_repeat: bool = False) -> None:
        try:
            with get_conn() as conn:
                conn.execute(
                    """INSERT INTO tool_calls
                       (session_id, user_id, round, tool_name, arguments,
                        result_ok, result_summary, error, code, is_repeat,
                        elapsed_ms, created_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (self.ctx.session_id, self.ctx.user_id, round_no, name,
                     json.dumps(args, ensure_ascii=False)[:2000],
                     1 if result.ok else 0, _summarize(result),
                     result.error or None, result.code or None,
                     1 if is_repeat else 0, result.elapsed_ms, utc_now_iso()),
                )
        except Exception as exc:      # 留痕失败不能影响会话
            logger.warning("工具调用落库失败：%s", exc)

    # ------------------------------------------------------------ 工具集
    def _tool_names(self, user_message: str, round_no: int) -> List[str]:
        if round_no == 0 and not wants_advanced_tools(user_message):
            return list(CORE_TOOLS)
        return list(CORE_TOOLS) + list(ADVANCED_TOOLS)

    # ------------------------------------------------------------ 主循环
    async def run(self, user_message: str) -> AsyncIterator[AgentEvent]:
        """执行一次会话，产出事件流。"""
        started = time.monotonic()
        self._user_message = user_message or ""

        if not tools_enabled():
            logger.info("LLM_TOOLS_ENABLED=off，直接走确定性兜底")
            from app.agent.fallback import run_fallback
            async for event in run_fallback(user_message, self.ctx,
                                            reason="tools_disabled"):
                yield event
            return

        # 载入长期画像并注入 system。
        # **关键纪律**：画像只作建议，与本次显式输入冲突时以本次为准——
        # 这靠 render_profile_prompt 的并列写法保证，不是靠代码判断。
        from app.agent.memory import load_profile, render_profile_prompt
        profile = load_profile(self.ctx.user_id)
        profile_block = render_profile_prompt(profile, self.ctx.facts)
        system_prompt = SYSTEM_PROMPT
        if profile_block:
            system_prompt = SYSTEM_PROMPT + "\n\n" + profile_block
            yield StatusEvent(text="已载入你的历史偏好（仅作参考）", stage="memory")

        # 保留调用方**注入的历史**（多轮上下文）。
        #
        # 旧代码直接 `self.messages = [system, user]` 把整个列表覆盖掉，
        # 而 `agent_chat_stream` 在这之前刚刚把前端传来的 history
        # append 进 self.messages —— 于是历史 100% 被丢弃，
        # 表现就是用户说了「成都两天」，下一句「我想逛逛博物馆」时
        # 助手又问「你在哪个城市？大概想逛几天？」。
        #
        # 这里只保留 user / assistant 两类（system 由本函数自己给最新的），
        # 并且丢掉 tool 相关消息——历史里不该有工具调用，那属于本轮内部状态。
        prior = [m for m in (self.messages or [])
                 if m.get("role") in ("user", "assistant") and m.get("content")]
        self.messages = ([{"role": "system", "content": system_prompt}]
                         + prior
                         + [{"role": "user", "content": user_message}])
        yield StatusEvent(text="正在理解你的需求…", stage="understand")

        while True:
            reason = self.ctx.budget.exhausted()
            if reason:
                yield StatusEvent(text="已到本次会话的处理上限，改用基础模式…",
                                  stage="fallback")
                async for event in self._fallback(user_message, reason):
                    yield event
                return
            if time.monotonic() - started > self.ctx.budget.max_seconds:
                async for event in self._fallback(user_message, "max_seconds"):
                    yield event
                return

            # 只做压缩，**不再追加用户消息**——它在循环外已经加过一次了。
            # （早先写在这里会导致每轮都重复 append，模型会看到好几遍同一句话）
            self.messages = compact_messages(self.messages, facts=self.ctx.facts)

            names = self._tool_names(user_message, self.ctx.budget.rounds)
            try:
                response = await chat_messages(
                    self.messages,
                    model=self.ctx.model,
                    tools=schemas(names) if names else None,
                )
            except Exception as exc:
                logger.warning("LLM 调用失败，走兜底：%s", exc)
                # httpx 的 str(HTTPStatusError) 只有状态码，没有响应体，
                # 400 到底为什么被拒（模型名/消息结构/参数越界）完全看不出来。
                # 这里把响应体前 300 字也带上，方便定位。
                detail = str(exc)
                resp = getattr(exc, "response", None)
                if resp is not None:
                    try:
                        detail = f"{detail} | {resp.text[:300]}"
                    except Exception:
                        pass
                yield ErrorEvent(message=f"模型调用失败：{detail}", code="llm_error")
                async for event in self._fallback(user_message, "llm_error"):
                    yield event
                return

            self.ctx.budget.rounds += 1
            self.ctx.budget.add_usage(response.usage)

            # ---- 没有工具调用 → 这是最终答复
            if not response.has_tool_calls:
                async for event in self._finish(response.content):
                    yield event
                return

            if response.content:
                yield ThoughtEvent(text=response.content)

            self.messages.append({
                "role": "assistant",
                "content": response.content or "",
                "tool_calls": [
                    {"id": tc.id, "type": "function",
                     "function": {"name": tc.name,
                                  "arguments": tc.raw_arguments or
                                  json.dumps(tc.arguments, ensure_ascii=False)}}
                    for tc in response.tool_calls
                ],
            })

            for call in response.tool_calls:
                if self.ctx.budget.tool_calls >= self.ctx.budget.max_tool_calls:
                    break
                async for event in self._run_tool(call, user_message):
                    yield event
                if self._aborted:
                    return

    # ------------------------------------------------------------ 单次工具
    async def _run_tool(self, call, user_message: str) -> AsyncIterator[AgentEvent]:
        args = call.arguments
        yield ToolStartEvent(call_id=call.id, name=call.name, args=args,
                             round=self.ctx.budget.rounds)

        is_repeat = self._is_repeat(call.name, args)
        if is_repeat:
            self._repeat_streak += 1
            if self._repeat_streak >= 3:
                # 连续三次相同调用 → 模型确实卡住了，中断走兜底
                logger.warning("检测到连续 3 次重复调用 %s，中断循环", call.name)
                yield ToolEndEvent(call_id=call.id, name=call.name, ok=False,
                                   summary="重复调用，已跳过",
                                   error="连续重复调用", code="repeat")
                self._aborted = True
                async for event in self._fallback(user_message, "repeat_loop"):
                    yield event
                return
            result = self._last_tool_result or ToolResult.failure(
                "重复调用", code=ToolErrorCode.INTERNAL)
            result = ToolResult(
                ok=result.ok, data=result.data, error=result.error,
                code=result.code,
                hint="你刚才已经调用过完全相同的参数，请基于已有结果继续或换个思路",
            )
            self._record(round_no=self.ctx.budget.rounds, name=call.name,
                         args=args, result=result, is_repeat=True)
            yield ToolEndEvent(call_id=call.id, name=call.name, ok=result.ok,
                               summary="重复调用，复用上次结果", error=result.error,
                               code=result.code)
            self.messages.append(result.to_message(call.id))
            return

        self._repeat_streak = 0
        self.ctx.budget.tool_calls += 1
        self.ctx.note_facts(**args)

        from app.agent.context import set_user_id, reset_user_id
        token = set_user_id(self.ctx.user_id)
        try:
            result = await execute(call.name, args, user_id=self.ctx.user_id)
        finally:
            reset_user_id(token)

        self._last_signature = self._signature(call.name, args)
        self._last_tool_result = result
        self._last_tool_name = call.name
        self._record(round_no=self.ctx.budget.rounds, name=call.name,
                     args=args, result=result)

        yield ToolEndEvent(call_id=call.id, name=call.name, ok=result.ok,
                           elapsed_ms=result.elapsed_ms,
                           summary=_summarize(result),
                           error=result.error, code=result.code)

        # 知识来源单独发一个事件，前端可展示引用
        if result.ok and call.name == "search_knowledge":
            chunks = (result.data or {}).get("chunks") or []
            for chunk in chunks:
                if chunk.get("chunk_id") is not None:
                    self._known_chunk_ids.add(int(chunk["chunk_id"]))
            yield KnowledgeEvent(
                sources=[{"chunk_id": c.get("chunk_id"), "title": c.get("title"),
                          "heading": c.get("heading", "")} for c in chunks],
                vector_ready=(result.data or {}).get("vector_ready", False),
            )
        if result.ok and result.is_itinerary:
            self._itinerary = result.data
            yield ItineraryEvent(itinerary=result.data)

        self.messages.append(result.to_message(call.id))

    # ------------------------------------------------------------ 收尾
    async def _finish(self, content: str) -> AsyncIterator[AgentEvent]:
        """最终答复：先做 F1/F3 校验，再流式输出。"""
        report = validate_answer(content, itinerary=self._itinerary,
                                 known_chunk_ids=self._known_chunk_ids or None)
        if report.removed_sentences:
            logger.warning("F1 剥离了 %d 句编造数值：%s", len(report.removed_sentences),
                           report.removed_sentences)
        if report.removed_citations:
            logger.warning("F3 剥离了不存在的引用：%s", report.removed_citations)

        text = report.text
        if not text and self._itinerary:
            text = "行程已经生成好了，你可以看看下面的安排。"
        self._final_text = text

        # 逐字分片输出（这里不调 LLM——文本已经拿到了，再流式一次是浪费一次调用）
        for index in range(0, len(text), 24):
            yield ChunkEvent(text=text[index:index + 24])

        # 会话结束：观察本轮并更新长期画像（匿名用户内部会直接跳过）。
        # 放在这里而不是中途——中途更新会让本轮后续工具用上刚写进去的画像，
        # 而本轮还没结束，容易自己影响自己。
        if self.ctx.user_id:
            try:
                from app.agent.memory import observe_and_update, load_profile
                before = load_profile(self.ctx.user_id) or {}
                after = observe_and_update(
                    self.ctx.user_id, message=self._user_message,
                    itinerary=self._itinerary)
                # 画像有实质变化才通知前端，避免每轮都弹一次
                if after and after.get("confidence") != before.get("confidence"):
                    yield ProfileUpdateEvent(profile={
                        "pace": after.get("pace"),
                        "budget_band": after.get("budget_band"),
                        "companions": after.get("companions") or [],
                        "dietary": after.get("dietary") or [],
                        "confidence": after.get("confidence"),
                    })
            except Exception as exc:
                logger.warning("画像更新失败（不影响本轮结果）：%s", exc)

        yield DoneEvent(reason="completed", rounds=self.ctx.budget.rounds,
                        tool_calls=self.ctx.budget.tool_calls,
                        usage=self.ctx.budget.to_dict())

    async def _fallback(self, user_message: str, reason: str) -> AsyncIterator[AgentEvent]:
        from app.agent.fallback import run_fallback
        async for event in run_fallback(user_message, self.ctx, reason=reason):
            yield event
