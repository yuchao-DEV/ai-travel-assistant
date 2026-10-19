"""Agent 工具层离线单测（阶段 3 · 第一批）。

覆盖：事件 to_sse / ToolResult 契约 / 签名反射生成 schema / 四道执行守卫。

**全部离线**：外部服务（高德）用 monkeypatch 替换，不消耗配额。

运行：
    cd backed
    .venv\\Scripts\\python.exe tests\\test_agent_tools.py
"""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.agent import registry  # noqa: E402
from app.agent.events import (  # noqa: E402
    ChunkEvent, DoneEvent, ErrorEvent, KnowledgeEvent, StatusEvent,
    ThoughtEvent, ToolEndEvent, ToolStartEvent,
)
from app.agent.results import ToolErrorCode, ToolResult  # noqa: E402

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
print("\n1. 事件：SSE 序列化")
# ==================================================================
_sse = StatusEvent(text="正在检索", stage="search").to_sse()
check("以 data: 开头", _sse.startswith("data: "))
check("以两个换行结尾（SSE 规范）", _sse.endswith("\n\n"))
_body = json.loads(_sse[6:].strip())
check("带 type 字段", _body.get("type") == "status", str(_body))
check("业务字段被展平", _body.get("text") == "正在检索" and _body.get("stage") == "search")

check("ToolStartEvent 带 args 与 round",
      json.loads(ToolStartEvent(call_id="c1", name="search_poi",
                                args={"city": "杭州"}, round=2).to_sse()[6:].strip())
      == {"type": "tool_start", "call_id": "c1", "name": "search_poi",
          "args": {"city": "杭州"}, "round": 2})
check("ToolEndEvent 成功时不带 error",
      "error" not in json.loads(ToolEndEvent(call_id="c1", name="t", ok=True,
                                             elapsed_ms=12, summary="ok").to_sse()[6:].strip()))
check("ToolEndEvent 失败时带 error 与 code",
      json.loads(ToolEndEvent(call_id="c1", name="t", ok=False,
                              error="炸了", code="internal").to_sse()[6:].strip())
      .get("code") == "internal")
check("DoneEvent 带收敛原因", json.loads(
    DoneEvent(reason="fallback", rounds=3, tool_calls=5).to_sse()[6:].strip())["reason"] == "fallback")
check("中文不被转义（前端直接可读）", "正在检索" in StatusEvent(text="正在检索").to_sse())
for _ev in (ThoughtEvent(text="x"), ChunkEvent(text="y"), ErrorEvent(message="z"),
            KnowledgeEvent(sources=[{"chunk_id": 1}]), StatusEvent(text="s")):
    check(f"{type(_ev).__name__} 可独立解析", json.loads(_ev.to_sse()[6:].strip())["type"] == _ev.type)

# ==================================================================
print("\n2. ToolResult 契约（三条不变量）")
# ==================================================================
_ok = ToolResult.success({"a": 1})
check("成功：ok=True 且无 error", _ok.ok and _ok.error == "")
check("失败：ok=False 且有 error",
      not ToolResult.failure("坏了").ok and ToolResult.failure("坏了").error == "坏了")
for _bad, _desc in (
    (lambda: ToolResult(ok=True, error="不该有"), "成功带 error"),
    (lambda: ToolResult(ok=False, error=""), "失败无 error"),
    (lambda: ToolResult(ok=False, error="x", data={"y": 1}), "失败带 data"),
    (lambda: ToolResult(ok=False, error="x", is_itinerary=True), "失败标 is_itinerary"),
):
    try:
        _bad()
        check(f"不变量拦截：{_desc}", False, "没有抛异常")
    except ValueError:
        check(f"不变量拦截：{_desc}", True)

check("to_dict 成功含 data 不含 error",
      "data" in _ok.to_dict() and "error" not in _ok.to_dict())
check("to_dict 失败含 error 不含 data",
      "error" in ToolResult.failure("x").to_dict()
      and "data" not in ToolResult.failure("x").to_dict())
check("hint 会被带给模型", ToolResult.failure("x", hint="换个思路").to_dict()["hint"] == "换个思路")
_msg = _ok.to_message("call_9")
check("to_message 是 role=tool 且带 tool_call_id",
      _msg["role"] == "tool" and _msg["tool_call_id"] == "call_9")
check("to_message 内容是合法 JSON", isinstance(json.loads(_msg["content"]), dict))

# ==================================================================
print("\n3. 签名反射生成 JSON Schema")
# ==================================================================
registry.clear()


@registry.tool(name="demo", description="演示工具", core=True, timeout=3.0)
async def demo(city: str, days: int = 2, tags: list[str] | None = None,
               ratio: float = 1.0) -> ToolResult:
    """演示用。"""
    return ToolResult.success({"city": city, "days": days, "tags": tags, "ratio": ratio})


_spec = registry.get("demo")
check("工具已注册", _spec is not None)
check("description 取自参数", _spec.description == "演示工具")
check("core 标记生效", _spec.core is True)
check("timeout 生效", _spec.timeout == 3.0)
check("识别为异步函数", _spec.is_async is True)

_props = _spec.parameters["properties"]
check("str → string", _props["city"]["type"] == "string", str(_props["city"]))
check("int → integer", _props["days"]["type"] == "integer", str(_props["days"]))
check("float → number", _props["ratio"]["type"] == "number", str(_props["ratio"]))
check("list[str] → array of string",
      _props["tags"]["type"] == "array" and _props["tags"]["items"]["type"] == "string",
      str(_props["tags"]))
check("只有无默认值的参数是必填", _spec.parameters.get("required") == ["city"],
      str(_spec.parameters.get("required")))
check("schema 里没有 title 噪音", "title" not in _props["city"], str(_props["city"]))

_oa = _spec.openai_schema()
check("openai_schema 结构正确",
      _oa["type"] == "function" and _oa["function"]["name"] == "demo"
      and "parameters" in _oa["function"])
check("schemas() 只返回白名单内的",
      [s["function"]["name"] for s in registry.schemas(["demo", "不存在"])] == ["demo"])
check("schemas() 传 None 返回全部",
      len(registry.schemas()) == len(registry.all_tools()))
check("core_names 只有标记过的", registry.core_names() == ["demo"])

# 描述缺省时取 docstring 第一行
@registry.tool(name="doconly")
async def doconly(x: str) -> ToolResult:
    """从文档字符串取描述。

    第二行不该出现。
    """
    return ToolResult.success(x)


check("description 缺省时取 docstring 第一行",
      registry.get("doconly").description.startswith("从文档字符串取描述"),
      registry.get("doconly").description)


# ==================================================================
print("\n4. execute() 的四道守卫")
# ==================================================================
async def _run():
    results = {}

    # 守卫 1：白名单
    r = await registry.execute("不存在的工具", {})
    results["unknown"] = (r.ok is False and r.code == ToolErrorCode.UNKNOWN_TOOL, r)
    results["unknown_hint"] = bool(r.hint)

    # 守卫 2：参数校验
    r = await registry.execute("demo", {"days": 3})          # 缺必填 city
    results["missing"] = (r.ok is False and r.code == ToolErrorCode.INVALID_ARGS, r)
    r = await registry.execute("demo", {"city": "杭州", "days": "不是数字"})
    results["badtype"] = (r.ok is False and r.code == ToolErrorCode.INVALID_ARGS, r)

    # 正常执行
    r = await registry.execute("demo", {"city": "杭州", "days": 3, "tags": ["历史"]})
    results["ok"] = (r.ok is True and r.data["city"] == "杭州"
                     and r.data["days"] == 3 and r.data["tags"] == ["历史"], r)
    results["elapsed"] = r.elapsed_ms >= 0

    # 默认值生效
    r = await registry.execute("demo", {"city": "苏州"})
    results["default"] = (r.ok and r.data["days"] == 2 and r.data["ratio"] == 1.0, r)

    # 守卫 4：超时
    @registry.tool(name="slow", timeout=0.2)
    async def slow(x: str) -> ToolResult:
        """故意慢。"""
        await asyncio.sleep(3)
        return ToolResult.success(x)

    r = await registry.execute("slow", {"x": "a"})
    results["timeout"] = (r.ok is False and r.code == ToolErrorCode.TIMEOUT, r)

    # 工具内部抛异常 → 收成结果而不是往上抛
    @registry.tool(name="boom", timeout=2.0)
    async def boom(x: str) -> ToolResult:
        """故意炸。"""
        raise RuntimeError("内部炸了")

    r = await registry.execute("boom", {"x": "a"})
    results["exception"] = (r.ok is False and r.code == ToolErrorCode.INTERNAL
                            and "内部炸了" in r.error, r)

    # 同步工具也能跑（走线程）
    @registry.tool(name="sync_tool", timeout=2.0)
    def sync_tool(x: str) -> ToolResult:
        """同步工具。"""
        return ToolResult.success({"echo": x})

    r = await registry.execute("sync_tool", {"x": "hi"})
    results["sync"] = (r.ok and r.data["echo"] == "hi", r)

    # 忘记返回 ToolResult → 自动包装而不是崩
    @registry.tool(name="raw_return", timeout=2.0)
    async def raw_return(x: str):
        """返回裸值。"""
        return {"raw": x}

    r = await registry.execute("raw_return", {"x": "v"})
    results["raw"] = (r.ok and r.data == {"raw": "v"}, r)
    return results


_res = asyncio.run(_run())
check("守卫1 未知工具被拒", _res["unknown"][0], str(_res["unknown"][1]))
check("守卫1 拒绝时给出可用工具清单", _res["unknown_hint"])
check("守卫2 缺必填参数被拒", _res["missing"][0], str(_res["missing"][1]))
check("守卫2 类型不符被拒", _res["badtype"][0], str(_res["badtype"][1]))
check("参数合法时正常执行", _res["ok"][0], str(_res["ok"][1]))
check("记录了耗时", _res["elapsed"])
check("默认值生效", _res["default"][0], str(_res["default"][1]))
check("守卫4 超时返回错误而非抛出", _res["timeout"][0], str(_res["timeout"][1]))
check("工具抛异常被收成失败结果", _res["exception"][0], str(_res["exception"][1]))
check("同步工具可用（自动走线程）", _res["sync"][0], str(_res["sync"][1]))
check("裸返回值被包装成成功结果", _res["raw"][0], str(_res["raw"][1]))

# 守卫 3：身份（requires_user）
registry.clear()


@registry.tool(name="private", requires_user=True)
async def private(x: str) -> ToolResult:
    """需要登录。"""
    return ToolResult.success({"x": x})


async def _identity():
    a = await registry.execute("private", {"x": "1"}, user_id=None)
    b = await registry.execute("private", {"x": "1"}, user_id=7)
    return a, b


_anon, _logged = asyncio.run(_identity())
check("守卫3 未登录时被拒", _anon.ok is False and _anon.code == ToolErrorCode.NEEDS_LOGIN,
      str(_anon))
check("守卫3 已登录时放行", _logged.ok is True, str(_logged))

# ==================================================================
print("\n5. 真实工具注册情况（geo / weather）")
# ==================================================================
registry.clear()
import app.agent.tools  # noqa: E402,F401  （导入即注册）

_names = sorted(registry.all_tools())
check("geo 与 weather 工具已注册", "resolve_city" in _names and "get_weather" in _names,
      str(_names))
check("get_city_intro 也已注册", "get_city_intro" in _names, str(_names))
check("geo 的两个工具已注册（CORE 归属在 §6 全量审计里断言）",
      {"resolve_city", "get_city_intro"} <= set(_names), str(_names))

# 离线验证：monkeypatch 掉高德
import app.agent.tools.geo as _geo  # noqa: E402
import app.agent.tools.weather as _weather  # noqa: E402


async def _fake_geocode(city):
    if city == "杭州":
        return {"adcode": "330100", "center_lat": 30.27, "center_lng": 120.15,
                "resolved_address": "浙江省杭州市", "keywords": ["景点"]}
    return None


async def _fake_weather(adcode):
    return ["晴 25°/15°", "小雨 22°/14°"]


_geo.geocode = _fake_geocode
_weather.geocode = _fake_geocode
_weather._amap_weather = _fake_weather


async def _tools():
    r1 = await registry.execute("resolve_city", {"city": "杭州"})
    r2 = await registry.execute("resolve_city", {"city": "杭州市"})     # 去后缀
    r3 = await registry.execute("resolve_city", {"city": "不存在城"})
    r4 = await registry.execute("get_weather", {"city": "杭州"})
    r5 = await registry.execute("get_weather", {"city": "不存在城"})
    return r1, r2, r3, r4, r5


_t1, _t2, _t3, _t4, _t5 = asyncio.run(_tools())
check("resolve_city 成功返回坐标与 adcode",
      _t1.ok and _t1.data["adcode"] == "330100" and _t1.data["center_lat"] == 30.27, str(_t1))
check("resolve_city 去掉「市」后缀再解析", _t2.ok and _t2.data["city"] == "杭州", str(_t2))
check("resolve_city 解析不到时失败且给提示",
      _t3.ok is False and _t3.code == ToolErrorCode.NOT_FOUND and bool(_t3.hint), str(_t3))
check("get_weather 返回预报并识别雨天",
      _t4.ok and _t4.data["has_rain"] is True and _t4.data["rainy_days"] == ["小雨 22°/14°"],
      str(_t4))
check("get_weather 定位失败时不编造天气", _t5.ok is False, str(_t5))

# 天气为空的降级路径
async def _no_weather(adcode):
    return []


_weather._amap_weather = _no_weather
_r = asyncio.run(registry.execute("get_weather", {"city": "杭州"}))
check("拿不到天气时返回失败并提示不要编造",
      _r.ok is False and "编造" in _r.hint, str(_r))

# ==================================================================
print("\n6. 14 个工具全量审计（schema / 必填项 / 类型）")
# ==================================================================
EXPECTED_TOOLS = {
    "resolve_city", "get_city_intro", "get_weather", "search_poi",
    "get_poi_detail", "plan_itinerary", "filter_candidates", "score_poi",
    "optimize_route", "schedule_day", "search_knowledge",
    "estimate_intercity", "recall_history", "recall_preferences",
}
_expected_core = {"resolve_city", "search_knowledge", "get_weather",
                  "search_poi", "plan_itinerary", "recall_history"}

_all = registry.all_tools()
check("14 个工具全部注册（预算移除后删掉 check_budget）", set(_all) == EXPECTED_TOOLS,
      f"缺 {EXPECTED_TOOLS - set(_all)}，多 {set(_all) - EXPECTED_TOOLS}")
check("CORE 恰好是清单里的 6 个", set(registry.core_names()) == _expected_core,
      str(sorted(registry.core_names())))

for _name, _spec in sorted(_all.items()):
    _p = _spec.parameters
    check(f"{_name}: schema 是 object",
          _p.get("type") == "object" and isinstance(_p.get("properties"), dict),
          str(_p)[:80])
    _req = _p.get("required") or []
    check(f"{_name}: required 都有对应 property",
          all(r in _p["properties"] for r in _req), str(_req))
    check(f"{_name}: 每个 property 有 type/enum",
          all(("type" in v or "enum" in v or "anyOf" in v)
              for v in _p["properties"].values()), str(_p["properties"]))
    check(f"{_name}: 有非空 description", bool(_spec.description.strip()))
    check(f"{_name}: schema 里没有 user_id（越权防线）",
          "user_id" not in _p.get("properties", {}))

check("plan_itinerary 必填 city/days（budget 已随预算系统移除）",
      set(_all["plan_itinerary"].parameters["required"]) == {"city", "days"},
      str(_all["plan_itinerary"].parameters["required"]))
_pl = _all["plan_itinerary"].parameters["properties"]
check("plan_itinerary: days → integer", _pl["days"]["type"] == "integer", str(_pl["days"]))
check("plan_itinerary: tags → array of string",
      _pl["tags"]["type"] == "array" and _pl["tags"]["items"]["type"] == "string",
      str(_pl["tags"]))
check("plan_itinerary: profile 有默认值（经典）", _pl["profile"].get("default") == "经典",
      str(_pl["profile"].get("default")))
check("search_poi 必填 city/keyword",
      set(_all["search_poi"].parameters["required"]) == {"city", "keyword"})
check("optimize_route: poi_names → array",
      _all["optimize_route"].parameters["properties"]["poi_names"]["type"] == "array")
check("recall_history 无必填参数",
      (_all["recall_history"].parameters.get("required") or []) == [],
      str(_all["recall_history"].parameters.get("required")))
check("只有记忆类工具需要登录（recall_history / recall_preferences）",
      sorted(n for n, s in _all.items() if s.requires_user)
      == ["recall_history", "recall_preferences"],
      str([n for n, s in _all.items() if s.requires_user]))

_oa = registry.schemas()
check("schemas() 返回 14 条", len(_oa) == 14, str(len(_oa)))
check("每条都是 function 类型", all(x["type"] == "function" for x in _oa))
check("schemas(names) 可选择性挂载（CORE 6 个）",
      len(registry.schemas(sorted(_expected_core))) == 6)

# ==================================================================
print("\n7. 运行上下文：身份 contextvar / 预算计数器 / 消息压缩")
# ==================================================================
from app.agent.context import (  # noqa: E402
    RunBudget, RunContext, compact_messages, current_user_id,
    reset_user_id, set_user_id,
)

check("默认无登录用户", current_user_id() is None)
_tok = set_user_id(42)
check("设置后能读到", current_user_id() == 42)
reset_user_id(_tok)
check("复原后回到 None", current_user_id() is None)

_b = RunBudget(max_rounds=3, max_tool_calls=5, max_seconds=50, max_tokens=1000)
check("初始未耗尽", _b.exhausted() is None)
_b.rounds = 3
check("轮数达上限即耗尽", _b.exhausted() == "max_rounds")
_b.rounds = 0
_b.tool_calls = 5
check("工具调用达上限即耗尽", _b.exhausted() == "max_tool_calls")
_b.tool_calls = 0
_b.add_usage({"prompt_tokens": 600, "completion_tokens": 500})
check("usage 累加正确", _b.total_tokens == 1100 and _b.prompt_tokens == 600)
check("token 达上限即耗尽", _b.exhausted() == "max_tokens")
_b2 = RunBudget(max_rounds=1, max_tool_calls=1, max_seconds=1, max_tokens=1)
_b2.add_usage(None)
check("usage 为 None 时安全", _b2.total_tokens == 0)
check("to_dict 字段完整",
      set(_b.to_dict()) == {"rounds", "tool_calls", "prompt_tokens",
                            "completion_tokens", "total_tokens"})

_ctx = RunContext(user_id=7, session_id="s1")
check("RunContext 自动建预算", _ctx.budget is not None and _ctx.budget.max_rounds > 0)
check("RunContext 默认用 Agent 模型", bool(_ctx.model))
_ctx.note_facts(city="杭州", days=3, tags=[], budget=None)
check("关键事实被记录（空值不入库）", _ctx.facts == {"city": "杭州", "days": 3},
      str(_ctx.facts))
_ctx.note_facts(city="苏州")
check("关键事实可更新", _ctx.facts["city"] == "苏州")

_msgs = [{"role": "system", "content": "系统提示"},
         {"role": "user", "content": "帮我规划杭州 3 天"},
         {"role": "assistant", "content": "好的"},
         {"role": "tool", "tool_call_id": "c1", "content": "x" * 5000}] + \
        [{"role": "user", "content": f"第{i}条"} for i in range(10)]
_c = compact_messages(_msgs, facts={"city": "杭州", "days": 3}, keep_recent=4)
check("压缩后 system 提示保留",
      any(m["role"] == "system" and m["content"] == "系统提示" for m in _c))
check("压缩后带上关键事实备忘",
      any("已确认的事实" in m.get("content", "") and "杭州" in m.get("content", "")
          for m in _c), str([m.get("content", "")[:30] for m in _c]))
check("压缩后长度显著减少", len(_c) < len(_msgs), f"{len(_c)} vs {len(_msgs)}")
check("最近消息原样保留", _c[-1]["content"] == "第9条", _c[-1]["content"])
check("大段工具返回被丢弃", not any(len(m.get("content", "")) > 3000 for m in _c))
check("短会话不压缩",
      len(compact_messages([{"role": "user", "content": "hi"}], keep_recent=4)) == 1)
check("无 facts 时不加备忘",
      not any("已确认的事实" in m.get("content", "")
              for m in compact_messages(_msgs, facts=None, keep_recent=2)))

print(f"\n{'=' * 60}")
print(f"通过 {PASSED} 项，失败 {FAILED} 项")
print(f"{'=' * 60}")
sys.exit(1 if FAILED else 0)
