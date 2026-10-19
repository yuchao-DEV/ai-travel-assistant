"""Agent 主循环离线单测（阶段 3 · 第三批）。

**用假 LLM 脚本化返回**，不调用任何真实模型。覆盖清单要求的 6 种情形。

运行：
    cd backed
    .venv\\Scripts\\python.exe tests\\test_agent_loop.py
"""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.agent import registry  # noqa: E402
from app.agent.context import RunBudget, RunContext  # noqa: E402
from app.agent.orchestrator import (  # noqa: E402
    AgentOrchestrator, tools_enabled, wants_advanced_tools,
)
from app.agent.results import ToolErrorCode, ToolResult  # noqa: E402
from app.db.database import get_conn, init_db  # noqa: E402
from app.llm import LLMResponse, ToolCall  # noqa: E402

PASSED = 0
FAILED = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if condition:
        PASSED += 1
        print(f"  [PASS] {name}")
    else:
        FAILED += 1
        print(f"  [FAIL] {name}" + (f"  → {detail}" if detail else ""))


# ==================================================================
# 假 LLM：按脚本依次返回预设响应，并记录每次调用收到的参数
# ==================================================================
class FakeLLM:
    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def __call__(self, messages, *, model=None, temperature=None, tools=None,
                 tool_choice=None, response_format=None, timeout=None,
                 max_tokens=None):
        self.calls.append({"messages": [dict(m) for m in messages],
                           "tools": tools, "model": model})
        item = self.script.pop(0) if self.script else _text("（脚本已用尽）")
        return _await(item)


def _await(value):
    async def _inner():
        return value
    return _inner()


def _text(content):
    return LLMResponse(content=content)


def _call(name, args, call_id="c1", content=""):
    return LLMResponse(
        content=content,
        tool_calls=[ToolCall(id=call_id, name=name, arguments=args,
                             raw_arguments=json.dumps(args, ensure_ascii=False))],
    )


# ==================================================================
# 测试替身工具
# ==================================================================
def _register_fakes():
    registry.clear()

    @registry.tool(name="ok_tool", core=True, timeout=5.0)
    async def ok_tool(q: str) -> ToolResult:
        """总是成功。"""
        return ToolResult.success({"echo": q, "count": 3})

    @registry.tool(name="explode", core=True, timeout=5.0)
    async def explode(q: str) -> ToolResult:
        """总是抛异常。"""
        raise RuntimeError("工具内部炸了")

    @registry.tool(name="plan_itinerary", core=True, timeout=5.0)
    async def plan_itinerary(city: str, days: int = 2, budget: float = 1500.0,
                             tags: list[str] | None = None,
                             profile: str = "舒适型",
                             must_go: list[str] | None = None,
                             avoid: list[str] | None = None) -> ToolResult:
        """假的规划工具（离线，不碰真实服务）。"""
        return ToolResult.success(
            {"city": city, "total_cost": 600.0, "budget": budget,
             "budget_note": "", "profile": profile,
             "plan": [{"date": "第 1 天", "daily_cost": 600.0, "items": [
                 {"name": f"{city}某景点", "type": "spot", "start": "09:00",
                  "end": "11:00", "cost": 45.0, "cost_source": "third_party",
                  "cost_note": "", "open_hours": "09:00-17:00",
                  "score": 0.5, "lat": 30.0, "lng": 120.0}]}] * days},
            is_itinerary=True)


async def _collect(orchestrator, message):
    events = []
    async for event in orchestrator.run(message):
        events.append(event)
    return events


def _types(events):
    return [e.type for e in events]


# ==================================================================
print("\n1. 选择性挂载与开关判定")
# ==================================================================
check("默认信号词判定：普通请求不挂全量",
      wants_advanced_tools("帮我规划杭州 3 天") is False)
for _sig in ("调整", "换顺序", "太满", "超预算", "便宜点", "为什么", "优化"):
    check(f"信号词「{_sig}」触发全量挂载", wants_advanced_tools(f"能不能{_sig}一下") is True)
check("空消息不触发", wants_advanced_tools("") is False)
check("默认开启工具调用", tools_enabled() is True)

# ==================================================================
print("\n2. 三轮正常收敛")
# ==================================================================
import app.agent.orchestrator as orch  # noqa: E402

init_db()
# 清掉本测试用过的会话留痕：测试必须可重复运行，不能依赖"库里本来是空的"
with get_conn() as _cc:
    _cc.execute("DELETE FROM tool_calls WHERE session_id LIKE 't-%'")

_register_fakes()
_fake = FakeLLM([
    _call("ok_tool", {"q": "第一次"}, "c1", content="我先查一下。"),
    _call("ok_tool", {"q": "第二次"}, "c2"),
    _text("查到了，结论是这样。"),
])
orch.chat_messages = _fake
_ctx = RunContext(session_id="t-converge")
_events = asyncio.run(_collect(AgentOrchestrator(_ctx), "帮我查点东西"))
_t = _types(_events)

check("事件流以 status 开头", _t[0] == "status", str(_t))
check("有两条工具开始事件", _t.count("tool_start") == 2, str(_t))
check("有两条工具结束事件", _t.count("tool_end") == 2, str(_t))
check("最后是 done", _t[-1] == "done", str(_t))
check("有流式文本分片", _t.count("chunk") >= 1, str(_t))
check("无 error 事件", "error" not in _t, str(_t))
_done = _events[-1]
check("done.reason=completed", _done.reason == "completed", _done.reason)
check("done 记录 3 轮", _done.rounds == 3, str(_done.rounds))
check("done 记录 2 次工具调用", _done.tool_calls == 2, str(_done.tool_calls))
check("assistant 文字以 thought 事件下发",
      any(e.type == "thought" and "我先查一下" in e.text for e in _events))
check("模型收到了工具返回（role=tool 消息）",
      any(m.get("role") == "tool" for m in _fake.calls[-1]["messages"]))
check("用户消息只出现一次（不重复 append）",
      sum(1 for m in _fake.calls[-1]["messages"]
          if m.get("role") == "user" and m.get("content") == "帮我查点东西") == 1,
      str([m.get("role") for m in _fake.calls[-1]["messages"]]))
# 挂载的是 **CORE_TOOLS 白名单 ∩ 已注册**——白名单来自清单，不是"所有 core 标记的工具"
_mounted = {t["function"]["name"] for t in (_fake.calls[0]["tools"] or [])}
check("首轮只挂 CORE 白名单里已注册的工具",
      _mounted == {"plan_itinerary"}, str(_mounted))
check("首轮未挂 ADVANCED 工具", "get_poi_detail" not in _mounted, str(_mounted))

# ==================================================================
print("\n3. 相同调用重复 → 第二次不执行")
# ==================================================================
_register_fakes()
_fake = FakeLLM([
    _call("ok_tool", {"q": "同样的"}, "c1"),
    _call("ok_tool", {"q": "同样的"}, "c2"),
    _text("好了。"),
])
orch.chat_messages = _fake
_ctx = RunContext(session_id="t-repeat")
_events = asyncio.run(_collect(AgentOrchestrator(_ctx), "查一下"))
_ends = [e for e in _events if e.type == "tool_end"]
check("两次 tool_end 都有", len(_ends) == 2, str(len(_ends)))
check("第二次被识别为重复复用",
      "重复" in (_ends[1].summary or ""), _ends[1].summary)
check("第二次未真正执行（echo 结果来自第一次）",
      _ends[1].ok is True)
with get_conn() as _c:
    _rows = _c.execute(
        "SELECT tool_name, is_repeat, result_ok FROM tool_calls "
        "WHERE session_id = ? ORDER BY id", ("t-repeat",)).fetchall()
check("落库 2 条", len(_rows) == 2, str(len(_rows)))
check("第二条标记为重复（未执行）", _rows[1]["is_repeat"] == 1, str(dict(_rows[1])))

# ==================================================================
print("\n4. 连续三次重复 → 中断走兜底")
# ==================================================================
_register_fakes()
_fake = FakeLLM([
    _call("ok_tool", {"q": "卡住"}, "c1"),
    _call("ok_tool", {"q": "卡住"}, "c2"),
    _call("ok_tool", {"q": "卡住"}, "c3"),
    _call("ok_tool", {"q": "卡住"}, "c4"),
    _text("不该走到这里。"),
])
orch.chat_messages = _fake
_ctx = RunContext(session_id="t-loop")
_events = asyncio.run(_collect(AgentOrchestrator(_ctx), "规划杭州 2 天"))
_t = _types(_events)
check("最终以 done 结束", _t[-1] == "done", str(_t))
check("触发了兜底（done.reason 标明）",
      _events[-1].reason in ("repeat_loop", "fallback"), _events[-1].reason)
check("兜底产出了行程卡", "itinerary" in _t, str(_t))

# ==================================================================
print("\n5. 工具抛异常 → 循环继续")
# ==================================================================
_register_fakes()
_fake = FakeLLM([
    _call("explode", {"q": "炸"}, "c1"),
    _text("那个工具没成功，我换个说法回答你。"),
])
orch.chat_messages = _fake
_ctx = RunContext(session_id="t-explode")
_events = asyncio.run(_collect(AgentOrchestrator(_ctx), "试试会炸的工具"))
_t = _types(_events)
check("异常被收成 tool_end(ok=False)", any(
    e.type == "tool_end" and e.ok is False for e in _events), str(_t))
check("循环没有被异常打断，仍产出了答复",
      _t[-1] == "done" and "chunk" in _t, str(_t))
check("没有会话级 error 事件（工具失败不等于会话失败）",
      "error" not in _t, str(_t))

# ==================================================================
print("\n6. 模型编造工具名 → 白名单拒绝")
# ==================================================================
_register_fakes()
_fake = FakeLLM([
    _call("不存在的工具", {"x": 1}, "c1"),
    _text("我换用正确的工具。"),
])
orch.chat_messages = _fake
_ctx = RunContext(session_id="t-unknown")
_events = asyncio.run(_collect(AgentOrchestrator(_ctx), "随便"))
_end = [e for e in _events if e.type == "tool_end"][0]
check("未知工具被拒（ok=False, code=unknown_tool）",
      _end.ok is False and _end.code == ToolErrorCode.UNKNOWN_TOOL, str(_end))
check("拒绝后循环继续并收敛", _events[-1].type == "done", str(_types(_events)))
with get_conn() as _c:
    _r = _c.execute("SELECT code FROM tool_calls WHERE session_id = ?",
                    ("t-unknown",)).fetchone()
check("落库记录了错误码", _r["code"] == ToolErrorCode.UNKNOWN_TOOL, str(dict(_r)))

# ==================================================================
print("\n7. 超 max_rounds → 触发兜底且事件流完整")
# ==================================================================
_register_fakes()
_fake = FakeLLM([_call("ok_tool", {"q": f"第{i}次"}, f"c{i}") for i in range(20)])
orch.chat_messages = _fake
_ctx = RunContext(session_id="t-maxrounds")
_ctx.budget = RunBudget(max_rounds=3, max_tool_calls=50, max_seconds=50,
                        max_tokens=999999)
_events = asyncio.run(_collect(AgentOrchestrator(_ctx), "规划杭州 2 天"))
_t = _types(_events)
check("触发上限后仍以 done 结束", _t[-1] == "done", str(_t))
check("done.reason 标明上限原因",
      _events[-1].reason == "max_rounds", _events[-1].reason)
check("兜底产出完整事件流（tool_start→tool_end→itinerary→chunk→done）",
      "tool_start" in _t and "itinerary" in _t and "chunk" in _t, str(_t))
check("恰好跑了 max_rounds 轮", _ctx.budget.rounds == 3, str(_ctx.budget.rounds))

# ==================================================================
print("\n8. LLM_TOOLS_ENABLED=off → 直接走兜底（最重要的一条）")
# ==================================================================
_register_fakes()
_fake = FakeLLM([_text("不该被调用")])
orch.chat_messages = _fake
_orig_mode = orch.LLM_TOOLS_ENABLED
orch.LLM_TOOLS_ENABLED = "off"
try:
    check("off 时 tools_enabled() 为假", tools_enabled() is False)
    _ctx = RunContext(session_id="t-off")
    _events = asyncio.run(_collect(AgentOrchestrator(_ctx), "帮我规划杭州 3 天，预算 3000"))
    _t = _types(_events)
    check("完全没有调用 LLM", len(_fake.calls) == 0, str(len(_fake.calls)))
    check("产出了行程卡", "itinerary" in _t, str(_t))
    check("以 done 结束", _t[-1] == "done", str(_t))
    _itin = [e for e in _events if e.type == "itinerary"][0].itinerary
    check("行程城市正确", _itin["city"] == "杭州", str(_itin.get("city")))
    check("天数取自规则解析（3 天）", len(_itin["plan"]) == 3, str(len(_itin["plan"])))
    check("预算取自规则解析", _itin["budget"] == 3000.0, str(_itin["budget"]))
    check("有给用户的文字说明", any(e.type == "chunk" for e in _events), str(_t))

    # 认不出城市时如实报错，不编一个城市
    _ctx2 = RunContext(session_id="t-off-nocity")
    _events2 = asyncio.run(_collect(AgentOrchestrator(_ctx2), "你好呀"))
    check("认不出城市时给出 error 而非编造行程",
          any(e.type == "error" for e in _events2), str(_types(_events2)))
    check("未产出 itinerary", not any(e.type == "itinerary" for e in _events2))
finally:
    orch.LLM_TOOLS_ENABLED = _orig_mode

# ==================================================================
print("\n9. LLM 调用失败 → 兜底")
# ==================================================================
_register_fakes()


def _boom(*args, **kwargs):
    async def _inner():
        raise RuntimeError("模型服务挂了")
    return _inner()


orch.chat_messages = _boom
_ctx = RunContext(session_id="t-llmboom")
_events = asyncio.run(_collect(AgentOrchestrator(_ctx), "规划杭州 2 天"))
_t = _types(_events)
check("发出 error 事件说明模型失败", "error" in _t, str(_t))
check("随后走兜底并产出行程", "itinerary" in _t and _t[-1] == "done", str(_t))

# ==================================================================
print("\n10. 产出即校验：F1 剥离编造数值")
# ==================================================================
_register_fakes()
_fake = FakeLLM([
    _call("plan_itinerary", {"city": "杭州", "days": 1, "budget": 1500}, "c1"),
    _text("第一天门票 45 元。另外还要准备 888 元现金。"),
])
orch.chat_messages = _fake
_ctx = RunContext(session_id="t-f1")
_events = asyncio.run(_collect(AgentOrchestrator(_ctx), "规划杭州 1 天"))
_text = "".join(e.text for e in _events if e.type == "chunk")
check("有出处的金额保留（45 元来自行程）", "45" in _text, _text)
check("编造的金额被剥离（888 元）", "888" not in _text, _text)
check("行程事件正常发出", any(e.type == "itinerary" for e in _events))

# 恢复真实的 orchestrator.chat_messages，避免影响其他测试
import importlib  # noqa: E402
import app.agent.orchestrator as _o  # noqa: E402
importlib.reload(_o)

print(f"\n{'=' * 60}")
print(f"通过 {PASSED} 项，失败 {FAILED} 项")
print(f"{'=' * 60}")
sys.exit(1 if FAILED else 0)
