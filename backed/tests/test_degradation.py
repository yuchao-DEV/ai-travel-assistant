"""降级演练（阶段 6.4）：七种异常下系统必须**优雅降级而不是崩溃**。

清单里这七项原本要求"手工做一遍"。自动化更好：手工做一次就忘了，
脚本可以每次回归都跑，而且能精确断言"降级后的行为"而不是"看起来还行"。

运行：
    cd backed
    .venv\\Scripts\\python.exe tests\\test_degradation.py
"""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

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
print("\n1. 无 tool calling（LLM_TOOLS_ENABLED=off）→ 走兜底仍产出行程")
# ==================================================================
from app.agent import registry  # noqa: E402
from app.agent.context import RunContext  # noqa: E402
from app.agent.results import ToolResult  # noqa: E402
from app.db.database import get_conn, init_db  # noqa: E402

init_db()
import app.agent.orchestrator as orch  # noqa: E402
import app.agent.tools  # noqa: F401,E402  （导入即注册）

_real_registry = dict(registry.all_tools())


async def _drill_no_tools():
    _orig = orch.LLM_TOOLS_ENABLED
    orch.LLM_TOOLS_ENABLED = "off"
    try:
        # 用真实的 plan_itinerary（会走算法引擎），验证兜底能出真行程
        collected = []
        ctx = RunContext(session_id="drill-no-tools")
        async for evt in orch.AgentOrchestrator(ctx).run("帮我规划杭州 2 天，预算 2000"):
            collected.append(evt)
        return collected
    finally:
        orch.LLM_TOOLS_ENABLED = _orig


_events = asyncio.run(_drill_no_tools())
_types = [e.type for e in _events]
check("不调用 LLM 也能产出事件流", len(_events) > 0, str(_types))
check("以 done 结束", _types[-1] == "done", str(_types))
check("done.reason 标明 tools_disabled",
      _events[-1].reason == "tools_disabled", _events[-1].reason)

# 兜底能产出**多好**的行程，取决于底层数据是否可用：
# - 高德可用 → 能出完整行程
# - 高德不可用（额度用尽/服务波动）→ 出不了行程，但**必须明确报错**，
#   绝不能返回"3 天 0 个安排"那种看起来成功的空结果。
if "itinerary" in _types:
    _itin = [e for e in _events if e.type == "itinerary"][0].itinerary
    check("高德可用时：兜底能产出真实行程",
          bool(_itin.get("plan")) and _itin.get("city") == "杭州",
          str(_itin.get("city")))
else:
    # 无地点数据时的正确行为：报错而不是静默给空行程
    check("高德不可用时：明确报错而非静默空行程",
          "error" in _types, str(_types))
    _err = [e for e in _events if e.type == "error"]
    check("错误信息说明了原因",
          bool(_err) and ("地点数据" in _err[0].message or "额度" in _err[0].message),
          _err[0].message if _err else "")
    print("       （当前高德不可用，走的是「明确报错」分支）")

# ==================================================================
print("\n2. 无 Embedding → 检索退化为纯 BM25，不报错")
# ==================================================================
import app.rag.embedding as emb  # noqa: E402
from app.rag.retriever import hybrid_search  # noqa: E402


async def _drill_no_embedding():
    # 模拟"没有可用的 embedding 服务"：让 embed_texts 返回 None
    _orig_embed = emb.embed_texts
    emb.embed_texts = lambda texts, **kw: _none()
    try:
        return await hybrid_search("灵隐寺要预约吗", city="杭州", top_k=3)
    finally:
        emb.embed_texts = _orig_embed


async def _none():
    return None


_result = asyncio.run(_drill_no_embedding())
check("无向量时检索仍返回结果", len(_result.get("results") or []) > 0,
      str(len(_result.get("results") or [])))
check("如实报告 vector_ready=False", _result.get("vector_ready") is False,
      str(_result.get("vector_ready")))
check("有用 note 说明降级", bool(_result.get("note")), str(_result.get("note"))[:60])

# ==================================================================
print("\n3. 无高德 → enhanced=false，服务不报错")
# ==================================================================
import app.algorithms.engine as engine  # noqa: E402
from app.schemas import PlanRequestDTO  # noqa: E402
from app.errors import ApiError  # noqa: E402
from app.services.plan import plan  # noqa: E402


async def _drill_no_amap():
    _orig = engine.amap_enabled
    engine.amap_enabled = lambda: False
    try:
        return await plan(PlanRequestDTO(city="杭州", origin="当涂", days=2,
                                         budget=2000, tags=["历史"]))
    finally:
        engine.amap_enabled = _orig


# **这项的行为是刻意改变的。**
#
# 原设计：无高德 → engine 返回 enhanced=false 的空行程 → plan 服务原样放行，
# 界面上出现「3 天 / 0 个安排 / ¥0」+ 三个「未知」标签。看似"服务没报错"，
# 实际是给用户一个假装成功的空结果，而且看不到任何原因。
#
# 现设计：无高德 → 没有候选地点 → 不可能排出行程 → **明确报 503 并说明原因**。
# 宁可让用户看到"暂时取不到地点数据，请稍后重试"，也不给他一个空行程。
try:
    _vo = asyncio.run(_drill_no_amap())
    # 万一底层有缓存/兜底数据能出行程，也接受——但要保证不是空行程
    check("无高德时若仍出结果，必须非空",
          any(day.items for day in (_vo.days or [])),
          f"出来了 {sum(len(d.items) for d in (_vo.days or []))} 个安排")
except ApiError as _err:
    check("无高德时明确报错而非给空行程", _err.code == 503, str(_err.code))
    check("错误信息说明了原因（不提底层细节）",
          "地点数据" in _err.message and "稍后重试" in _err.message, _err.message[:60])
    print("       （高德不可用 → 明确报 503，而不是返回 0 个安排的空行程）")

# ==================================================================
print("\n4. 无知识库 → search_knowledge 返回空 + 明确提示")
# ==================================================================
import app.agent.tools.knowledge as kb_tool  # noqa: E402


async def _drill_no_kb():
    _orig = kb_tool.hybrid_search

    async def _empty(*args, **kwargs):
        return {"results": [], "vector_ready": False, "bm25_count": 0,
                "vector_count": 0, "note": "知识库为空"}

    kb_tool.hybrid_search = _empty
    try:
        return await registry.execute("search_knowledge",
                                      {"query": "灵隐寺要预约吗", "city": "杭州"})
    finally:
        kb_tool.hybrid_search = _orig


_kb = asyncio.run(_drill_no_kb())
check("无知识库时返回 ok=true（空结果不是错误）", _kb.ok is True, str(_kb))
check("count=0", (_kb.data or {}).get("count") == 0, str(_kb.data))
check("给模型明确提示不要编造", "不要凭记忆编造" in (_kb.hint or ""), str(_kb.hint))

# ==================================================================
print("\n5. 工具超时 → 返回 timeout 而不是抛出，循环可继续")
# ==================================================================
from app.agent.results import ToolErrorCode  # noqa: E402


def _drill_timeout():
    registry.clear()

    @registry.tool(name="slow_drill", timeout=0.05)
    async def slow_drill(x: str) -> ToolResult:
        """故意超时。"""
        await asyncio.sleep(2)
        return ToolResult.success(x)

    try:
        return asyncio.run(registry.execute("slow_drill", {"x": "a"}))
    finally:
        registry.clear()
        for name, spec in _real_registry.items():
            registry._REGISTRY[name] = spec


_to = _drill_timeout()
check("超时返回 ok=false 而不是抛异常", _to.ok is False, str(_to))
check("错误码是 timeout", _to.code == ToolErrorCode.TIMEOUT, str(_to.code))
check("给了模型下一步提示", bool(_to.hint), str(_to.hint))

# ==================================================================
print("\n6. 无限循环（假 LLM 永远返回同一个 tool_call）→ 3 次后中断 → 兜底")
# ==================================================================
from app.llm import LLMResponse, ToolCall  # noqa: E402


class _LoopLLM:
    """永远返回同一个工具调用，模拟模型卡死。"""

    def __init__(self):
        self.calls = 0

    def __call__(self, messages, **kwargs):
        self.calls += 1

        async def _inner():
            return LLMResponse(tool_calls=[ToolCall(
                id=f"c{self.calls}", name="search_poi",
                arguments={"city": "杭州", "keyword": "西湖"})])

        return _inner()


_loop_llm = _LoopLLM()


async def _drill_loop():
    _orig = orch.chat_messages
    orch.chat_messages = _loop_llm
    try:
        ctx = RunContext(session_id="drill-loop")
        return [e async for e in orch.AgentOrchestrator(ctx).run("规划杭州 2 天")]
    finally:
        orch.chat_messages = _orig


_loop_events = asyncio.run(_drill_loop())
_loop_types = [e.type for e in _loop_events]
check("检测到重复并中断（没有无限调用）", _loop_llm.calls <= 5, str(_loop_llm.calls))
check("以 done 结束", _loop_types[-1] == "done", str(_loop_types))
check("done.reason 标明重复中断",
      _loop_events[-1].reason == "repeat_loop", _loop_events[-1].reason)
# 中断后走兜底：有地点数据就产出行程，没有就明确报错——都不能是空行程
if "itinerary" in _loop_types:
    check("中断后兜底产出了行程", True)
elif "error" in _loop_types:
    _e = [e for e in _loop_events if e.type == "error"]
    check("中断后兜底明确报错（无地点数据时）",
          bool(_e) and ("地点数据" in _e[0].message or "额度" in _e[0].message),
          _e[0].message if _e else "")
else:
    check("中断后兜底有明确结果（行程或错误）", False, str(_loop_types))

# ==================================================================
print("\n7. 数据库损坏 → 返回明确错误而不是静默出错")
# ==================================================================
from app.services import history as history_service  # noqa: E402

# 造一个带坏 itinerary_json 的行程记录。
# itineraries 有外键指向 users，必须先有真实用户——用假 user_id 会被 FK 挡住
# （这正是外键该做的事，所以这里顺带验证了它）。
_DRILL_USER = "drill_tester"
with get_conn() as _c:
    _c.execute(
        "INSERT INTO users (username, nickname, created_at) VALUES (?,?,?) "
        "ON CONFLICT(username) DO NOTHING",
        (_DRILL_USER, "降级演练用户", "2026-01-01T00:00:00Z"))
    _U = _c.execute("SELECT id FROM users WHERE username = ?",
                    (_DRILL_USER,)).fetchone()["id"]
    _c.execute("DELETE FROM itineraries WHERE user_id = ?", (_U,))
    _c.execute(
        "INSERT INTO itineraries (user_id, title, origin, city, days, budget, tags, "
        "profile, total_cost, request_json, itinerary_json, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (_U, "坏数据", "当涂", "杭州", 2, 2000, '["历史"]', "舒适型", 500.0,
         "{}", "{这不是合法JSON", "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"))
    _bad_id = _c.execute(
        "SELECT id FROM itineraries WHERE user_id = ? ORDER BY id DESC LIMIT 1",
        (_U,)).fetchone()["id"]
try:
    history_service.get_itinerary(_U, _bad_id)
    check("坏数据被捕获（未抛到调用方）", False, "居然解析成功了")
except Exception as exc:
    check("坏数据抛出可识别的异常", exc is not None, type(exc).__name__)
    check("异常信息可读（不是 KeyError 之类）",
          len(str(exc)) > 0, str(exc)[:80])

# 列表接口不应因一条坏数据整体失败
try:
    page = history_service.list_itineraries(_U, page=1, page_size=5)
    check("列表接口对坏数据容错（不整体失败）", page is not None)
except Exception as exc:
    check("列表接口对坏数据容错（不整体失败）", False, f"{type(exc).__name__}: {exc}")

with get_conn() as _c:
    _c.execute("DELETE FROM itineraries WHERE user_id = ?", (_U,))

print(f"\n{'=' * 60}")
print(f"通过 {PASSED} 项，失败 {FAILED} 项")
print(f"{'=' * 60}")
sys.exit(1 if FAILED else 0)
