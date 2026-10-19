"""记忆系统离线单测（阶段 4）。

覆盖清单 4.4 的 7 组用例，重点是**不覆盖显式输入**与**越权隔离**。

运行：
    cd backed
    .venv\\Scripts\\python.exe tests\\test_memory.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.agent import memory  # noqa: E402
from app.db.database import get_conn, init_db  # noqa: E402
from app.services import agent_session  # noqa: E402

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


init_db()
# 测试用的用户 id（避免与真实数据冲突，且可重复运行）
U1, U2 = 900101, 900102
with get_conn() as _c:
    _c.execute("DELETE FROM user_profiles WHERE user_id IN (?,?)", (U1, U2))
    _c.execute("DELETE FROM agent_sessions WHERE user_id IN (?,?)", (U1, U2))
    _c.execute("DELETE FROM itineraries WHERE user_id IN (?,?)", (U1, U2))

# ==================================================================
print("\n1. 规则抽取（确定性，无 LLM）")
# ==================================================================
_r = memory.extract_from_text("带爸妈去苏州，别太累，我不吃辣")
check("清单原用例：带爸妈 + 别太累 + 不吃辣",
      _r.get("companions") == ["老人"] and _r.get("pace") == ["悠闲"]
      and _r.get("dietary") == ["不吃辣"], str(_r))

check("特种兵 → 尽览", memory.extract_from_text("想特种兵式多逛几个").get("pace") == ["尽览"])
check("省钱不再映射到任何 profile（预算系统已移除）",
      memory.extract_from_text("想省钱穷游").get("pace") is None,
      str(memory.extract_from_text("想省钱穷游")))
check("带孩子 → 家庭（必须是 VALID_COMPANIONS 里的合法值）",
      memory.extract_from_text("带孩子去").get("companions") == ["家庭"],
      str(memory.extract_from_text("带孩子去")))
check("儿童不再是画像取值（旧值非法，与表单对不上）",
      "儿童" not in str(memory.extract_from_text("带孩子去")))
check("海鲜过敏 → 不吃海鲜", memory.extract_from_text("海鲜过敏").get("dietary") == ["不吃海鲜"])
check("吃素 → 素食", memory.extract_from_text("我吃素").get("dietary") == ["素食"])
check("没提到就不出字段（区分「没提到」与「提到空」）",
      memory.extract_from_text("帮我规划杭州三天") == {}, str(memory.extract_from_text("帮我规划杭州三天")))
check("空输入安全", memory.extract_from_text("") == {})
check("None 输入安全", memory.extract_from_text(None) == {})
check("多个禁忌同时抽到",
      set(memory.extract_from_text("不吃辣也不吃香菜").get("dietary") or [])
      == {"不吃辣", "不吃香菜"},
      str(memory.extract_from_text("不吃辣也不吃香菜")))

# ==================================================================
print("\n2. 历史统计")
# ==================================================================
_hist = [{"profile": "经典", "budget": 2200, "days": 2, "tags": ["历史", "自然"]},
         {"profile": "经典", "budget": 2200, "days": 2, "tags": ["历史"]},
         {"profile": "经典", "budget": 2200, "days": 2, "tags": ["自然"]}]
_s = memory.infer_from_history(_hist)
check("3 次都是经典型 → pace=经典", _s.get("pace") == "经典", str(_s))
check("预算中位数 2200 → 中档", _s.get("budget_band") == "中", str(_s))
check("记录样本数", _s.get("sample_size") == 3, str(_s))
check("兴趣词频归一", abs(sum((_s.get("interests") or {}).values()) - 1.0) < 0.01, str(_s))
check("空历史不崩", memory.infer_from_history([]) .get("sample_size") == 0)
check("众数并列时不猜（返回 None）",
      memory.infer_from_history([{"profile": "经典"}, {"profile": "尽览"}]).get("pace") is None,
      str(memory.infer_from_history([{"profile": "经典"}, {"profile": "尽览"}])))
check("预算档位分档",
      memory._budget_band(300) == "低" and memory._budget_band(1000) == "中"
      and memory._budget_band(3000) == "高")

# ==================================================================
print("\n3. 置信度随样本量上升")
# ==================================================================
check("0 样本 confidence = 0", memory._confidence_for(0) == 0.0)
check("0 样本 < 0.3（提示词会走「请确认」）", memory._confidence_for(0) < 0.3)
check("1 样本仍 < 0.3（一次说明不了偏好）", memory._confidence_for(1) < 0.3,
      str(memory._confidence_for(1)))
check("置信度单调不减",
      all(memory._confidence_for(i) <= memory._confidence_for(i + 1) for i in range(10)))
check("置信度上限 < 1（永远不会「确定」）", memory._confidence_for(99) < 1.0)

# ==================================================================
print("\n4. 绝不覆盖显式输入（最重要的一组）")
# ==================================================================
_profile = {"pace": "悠闲", "budget_band": "中",
            "companions": ["老人"], "dietary": ["不吃辣"],
            "interests": {"历史": 0.6}, "confidence": 0.7, "sample_size": 3}
_explicit = {"profile": "尽览", "pace": "紧凑", "days": 2, "budget": 3000,
             "tags": ["自然"], "companions": ["朋友"], "must_go": ["灵隐寺"]}
_text = memory.render_profile_prompt(_profile, _explicit)

check("显式输入出现且标注「本次要求（优先）」", "本次要求（优先" in _text, _text[:120])
check("画像出现且标注「仅供参考」", "仅供参考" in _text, _text[:120])
check("两者都在（模型才有依据取舍）",
      "紧凑" in _text and "悠闲" in _text, _text[:200])
check("显式的紧凑被写进「本次要求」段",
      _text.index("紧凑") < _text.index("悠闲") or "本次要求" in _text[:200],
      _text[:200])
check("画像里的老人与显式里的朋友并存（不互相抹掉）",
      "老人" in _text and "朋友" in _text, _text[:250])
check("画像说明了冲突时的优先级", "以本次请求为准" in _text, _text[:250])
check("必去景点被带上", "灵隐寺" in _text, _text[:250])

# 只有画像、无显式输入
_text2 = memory.render_profile_prompt(_profile, None)
check("无显式输入时只有画像段", "本次要求" not in _text2 and "仅供参考" in _text2)
check("高置信度不提示「请确认」", "请主动向用户确认" not in _text2)

# 低置信度 → 提示确认
_low = dict(_profile, confidence=0.1, sample_size=0)
_text3 = memory.render_profile_prompt(_low, None)
check("低置信度提示主动确认", "请主动向用户确认" in _text3, _text3[:250])
check("低置信度仍标注仅供参考", "仅供参考" in _text3)

# 无画像无显式 → 空串
check("无画像无显式输入返回空串", memory.render_profile_prompt(None, None) == "")
check("空画像返回空串", memory.render_profile_prompt({}, None) == "")

# ==================================================================
print("\n5. 画像读写与隐私删除")
# ==================================================================
memory.save_profile(U1, {"pace": "悠闲", "budget_band": "中",
                         "companions": ["老人"], "dietary": ["不吃辣"],
                         "interests": {"历史": 0.7}, "confidence": 0.5,
                         "sample_size": 2})
_loaded = memory.load_profile(U1)
check("写入后能读回", _loaded is not None and _loaded["pace"] == "悠闲", str(_loaded))
check("JSON 字段正确反序列化",
      _loaded["companions"] == ["老人"] and _loaded["dietary"] == ["不吃辣"], str(_loaded))
check("匿名用户读不到", memory.load_profile(None) is None)
check("不存在的用户返回 None", memory.load_profile(999999) is None)

check("clear_profile 返回 True", memory.clear_profile(U1) is True)
check("删除后读不到", memory.load_profile(U1) is None)
check("重复删除返回 False（没东西可删）", memory.clear_profile(U1) is False)

# 隔离：删 U1 不影响 U2
memory.save_profile(U1, {"pace": "尽览", "companions": [], "dietary": [],
                         "interests": {}, "confidence": 0.3, "sample_size": 1})
memory.save_profile(U2, {"pace": "悠闲", "companions": [], "dietary": [],
                         "interests": {}, "confidence": 0.3, "sample_size": 1})
memory.clear_profile(U1)
check("删除 A 不影响 B", memory.load_profile(U2) is not None
      and memory.load_profile(U2)["pace"] == "悠闲")

# ==================================================================
print("\n6. observe_and_update：并集语义与优先级")
# ==================================================================
memory.clear_profile(U1)
memory.observe_and_update(U1, message="我不吃辣")
_p = memory.load_profile(U1)
check("从对话抽到画像", _p is not None and _p["dietary"] == ["不吃辣"], str(_p))
memory.observe_and_update(U1, message="带爸妈去，别太累")
_p = memory.load_profile(U1)
check("禁忌是并集（后来的话不覆盖之前说的）",
      "不吃辣" in _p["dietary"], str(_p["dietary"]))
check("同行人也被记下", _p["companions"] == ["老人"], str(_p["companions"]))
check("本次话里的节奏优先", _p["pace"] == "悠闲", str(_p["pace"]))
check("匿名用户不做任何事", memory.observe_and_update(None, message="我不吃辣") is None)
memory.clear_profile(U1)

# 有行程产出时样本 +1（历史统计与预算档位由 §2 直接覆盖——
# itineraries 表有外键指向 users，测试里不造真实用户，避免耦合）
memory.observe_and_update(U1, message="帮我规划杭州", itinerary={"city": "杭州"})
_p = memory.load_profile(U1)
check("有行程产出时样本 +1", _p["sample_size"] == 1, str(_p["sample_size"]))
check("无历史行程时预算档位留空（不猜）", _p["budget_band"] is None, str(_p["budget_band"]))
check("置信度随样本更新", _p["confidence"] == memory._confidence_for(1), str(_p["confidence"]))
memory.clear_profile(U1)

# ==================================================================
print("\n7. 会话 CRUD 与越权隔离")
# ==================================================================
_sid_a = agent_session.create(U1, "帮我规划杭州三天")
agent_session.append_message(_sid_a, "user", "帮我规划杭州三天")
agent_session.append_message(_sid_a, "assistant", "好的，我来看看。")
agent_session.touch(_sid_a)

check("A 能读到自己的会话",
      agent_session.get(_sid_a, U1) is not None)
check("B 读 A 的会话返回 None（不泄露存在性）",
      agent_session.get(_sid_a, U2) is None)
check("匿名读 A 的会话返回 None",
      agent_session.get(_sid_a, None) is None)

_detail = agent_session.get(_sid_a, U1)
check("会话详情含消息", len(_detail["messages"]) == 2, str(len(_detail["messages"])))
check("会话详情含工具调用字段", "tool_calls" in _detail)
check("消息计数已更新", _detail["message_count"] == 2, str(_detail["message_count"]))
check("标题取自首条消息", _detail["title"].startswith("帮我规划"), _detail["title"])

_list = agent_session.list_sessions(U1)
check("列表只含自己的会话", _list["total"] == 1, str(_list["total"]))
check("B 的列表为空", agent_session.list_sessions(U2)["total"] == 0)

check("B 删不掉 A 的会话", agent_session.delete(_sid_a, U2) is False)
check("被拒绝后会话仍在", agent_session.get(_sid_a, U1) is not None)
check("A 能删自己的会话", agent_session.delete(_sid_a, U1) is True)
check("删除后读不到", agent_session.get(_sid_a, U1) is None)
check("重复删除返回 False", agent_session.delete(_sid_a, U1) is False)

# 匿名会话隔离
_sid_anon = agent_session.create(None, "匿名请求")
check("匿名会话匿名可读", agent_session.get(_sid_anon, None) is not None)
check("匿名会话登录用户读不到", agent_session.get(_sid_anon, U1) is None)
agent_session.delete(_sid_anon, None)

# 清理
with get_conn() as _c:
    _c.execute("DELETE FROM user_profiles WHERE user_id IN (?,?)", (U1, U2))
    _c.execute("DELETE FROM agent_sessions WHERE user_id IN (?,?)", (U1, U2))
    _c.execute("DELETE FROM itineraries WHERE user_id IN (?,?)", (U1, U2))

print(f"\n{'=' * 60}")
print(f"通过 {PASSED} 项，失败 {FAILED} 项")
print(f"{'=' * 60}")
sys.exit(1 if FAILED else 0)
