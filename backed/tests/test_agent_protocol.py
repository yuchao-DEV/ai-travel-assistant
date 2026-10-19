"""Agent 协议层离线单测（阶段 3 · 第二批）。

覆盖 F1 数值校验、F3 引用校验、系统提示词的关键约束。

**全部离线**，不调用任何外部服务。

运行：
    cd backed
    .venv\\Scripts\\python.exe tests\\test_agent_protocol.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.agent.protocol import (  # noqa: E402
    SYSTEM_PROMPT, collect_allowed_values, validate_answer,
)

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


# 一份典型的行程（用于校验基准）
ITINERARY = {
    "city": "杭州",
    "plan": [
        {"date": "第 1 天", "items": [
            {"name": "灵隐寺", "start": "09:00", "end": "11:00", "cost": 45.0},
            {"name": "楼外楼", "start": "12:00", "end": "13:00", "cost": 120.0},
        ]},
    ],
}

# ==================================================================
print("\n1. 从行程收集「有出处」的数值")
# ==================================================================
_amounts, _times = collect_allowed_values(ITINERARY)
check("收集到条目费用", 45.0 in _amounts and 120.0 in _amounts, str(sorted(_amounts)))
check("收集到开始/结束时刻",
      "09:00" in _times and "11:00" in _times and "12:00" in _times, str(sorted(_times)))
check("空行程返回空集合", collect_allowed_values(None) == (set(), set()))
check("畸形行程不抛异常",
      collect_allowed_values({"plan": [{"items": [{"cost": "不是数字"}]}]})[0] == set())

# ==================================================================
print("\n2. F1 数值校验：编造的金额/时刻要剥离")
# ==================================================================
_r = validate_answer("灵隐寺门票 45 元，中午在楼外楼吃饭。", itinerary=ITINERARY)
check("有出处的金额不被剥离",
      "45" in _r.text and _r.changed is False, f"{_r.text!r} removed={_r.removed_sentences}")

_r = validate_answer("灵隐寺门票大约 60 元。中午在楼外楼吃饭。", itinerary=ITINERARY)
check("编造的金额所在句被剥离",
      "60" not in _r.text and "楼外楼" in _r.text, f"{_r.text!r}")
check("剥离记录里留下原句", len(_r.removed_sentences) == 1, str(_r.removed_sentences))

_r = validate_answer("早上 09:00 到灵隐寺。11:30 离开。", itinerary=ITINERARY)
check("有出处的时刻不被剥离", "09:00" in _r.text, f"{_r.text!r}")
check("编造的时刻所在句被剥离（11:30 不在行程里）",
      "11:30" not in _r.text and "09:00" in _r.text, f"{_r.text!r}")

# 剥离粒度是**整句**（与 polish.py 的既有约定一致）：同一句里既有正确又有编造的
# 数值时，整句都会被去掉。宁可少说，也不留半个错数字。
_r = validate_answer("早上 09:00 到灵隐寺，11:30 离开。", itinerary=ITINERARY)
check("同句混有编造数值时整句剥离（保守但绝不留下错数据）",
      "11:30" not in _r.text and "09:00" not in _r.text and _r.text == "",
      f"{_r.text!r}")

_r = validate_answer("总预算 3000 元，预计花掉 1200 元。", itinerary=ITINERARY)
check("只含条目金额时不再被视为有出处（预算已移除）",
      len(_r.removed_sentences) >= 1, str(_r.removed_sentences))

_r = validate_answer("门票约 ￥60。交通 ￥30。", itinerary=ITINERARY)
check("￥ 符号形式也能识别（按句各剥离一次）",
      len(_r.removed_sentences) == 2, str(_r.removed_sentences))

# 没有行程时跳过 F1（否则正常回答会被误删）
_r = validate_answer("一般来说预算 3000 元比较合适。", itinerary=None)
check("无行程时跳过 F1（不误删正常回答）", _r.changed is False, str(_r.removed_sentences))
_r = validate_answer("门票大约 60 元。", itinerary={})
check("空行程字典同样跳过 F1", _r.changed is False, str(_r.removed_sentences))

# 不该误伤的情形
_r = validate_answer("建议带 1000 到 2000 元现金以备不时之需。", itinerary=ITINERARY)
check("非行程金额（备现金建议）也会被剥离——这是刻意的保守",
      len(_r.removed_sentences) >= 1, str(_r.removed_sentences))

# ==================================================================
print("\n3. F3 引用校验：不存在的 chunk_id 要剥离")
# ==================================================================
_r = validate_answer("灵隐寺需要提前预约[1]，周一闭馆[2]。", known_chunk_ids=[1, 2])
check("命中已有 chunk_id 的角标保留",
      "[1]" in _r.text and "[2]" in _r.text and _r.changed is False, f"{_r.text!r}")

_r = validate_answer("灵隐寺需要提前预约[1]，据说很灵[7]。", known_chunk_ids=[1, 2])
check("不存在的 chunk_id 角标被剥离",
      "[1]" in _r.text and "[7]" not in _r.text, f"{_r.text!r}")
check("剥离的是角标而不是整句",
      "据说很灵" in _r.text, f"{_r.text!r}")
check("剥离记录留下原角标", _r.removed_citations == ["[7]"], str(_r.removed_citations))

_r = validate_answer("参考【3】的说法。", known_chunk_ids=[1])
check("中文方括号形式也能识别", "[3]" not in _r.text and "【3】" not in _r.text, f"{_r.text!r}")

_r = validate_answer("预约规则见[1]。", known_chunk_ids=None)
check("known_chunk_ids=None 时跳过 F3",
      "[1]" in _r.text and _r.changed is False, f"{_r.text!r}")

# ==================================================================
print("\n4. 两道校验的交互与安全性")
# ==================================================================
_r = validate_answer("灵隐寺门票 45 元[1]，不过听说要 60 元[9]。",
                     itinerary=ITINERARY, known_chunk_ids=[1])
check("F1 与 F3 同时生效",
      "60" not in _r.text and "[9]" not in _r.text, f"{_r.text!r}")
check("两句处理都有记录",
      len(_r.removed_sentences) == 1 and _r.removed_citations == ["[9]"],
      f"{_r.removed_sentences} {_r.removed_citations}")

check("空文本安全返回", validate_answer("", itinerary=ITINERARY).text == "")
check("None 文本安全返回", validate_answer(None, itinerary=ITINERARY).text == "")
_r = validate_answer("一切正常的回答，没有任何数字。", itinerary=ITINERARY)
check("正常回答不被改动", _r.changed is False and _r.text == "一切正常的回答，没有任何数字。",
      f"{_r.text!r}")
check("校验只做减法（不新增内容）",
      len(validate_answer("好的。", itinerary=ITINERARY).text) <= len("好的。"))

# ==================================================================
print("\n5. 系统提示词的关键约束")
# ==================================================================
for _kw, _why in (
    ("不编造事实", "反幻觉总则"),
    ("plan_itinerary", "产出行程的唯一入口"),
    ("search_knowledge", "预约/闭馆类必须先查知识库"),
    ("门票待确认", "无票价时的展示口径"),
    ("average", "均价口径"),
    ("不等于门票价", "均价必须说明不是门票价"),
    ("订酒店", "能力边界：不能订酒店"),
    ("机票火车票", "能力边界：不能买票"),
    ("先问清楚", "信息不足时先追问"),
):
    check(f"提示词包含「{_kw}」（{_why}）", _kw in SYSTEM_PROMPT)

check("提示词不承诺真实班次", "真实班次" in SYSTEM_PROMPT or "班次" in SYSTEM_PROMPT)
check("提示词要求工具失败时如实说明", "没查到" in SYSTEM_PROMPT or "查不到" in SYSTEM_PROMPT)

print(f"\n{'=' * 60}")
print(f"通过 {PASSED} 项，失败 {FAILED} 项")
print(f"{'=' * 60}")
sys.exit(1 if FAILED else 0)
