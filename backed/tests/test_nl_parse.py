"""自然语言需求解析的单元测试（离线，不调用大模型）。

覆盖三道防线：
1. 白名单：tags / profile 只能取官方枚举值；
2. 范围夹取：天数夹到 1-7、预算不低于 100，并把夹取动作回报给用户；
3. 规则兜底：LLM 不可用或输出不可解析时，退回确定性正则解析。

LLM 分支用 monkeypatch 替换 ``_extract`` 来模拟，不消耗任何配额。

运行：cd backed && python tests/test_nl_parse.py
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import MAX_TRIP_DAYS  # noqa: E402
from app.schemas import ParseRequestDTO, PlanRequestDTO  # noqa: E402
from app.services import nl_parse  # noqa: E402
from app.services.llm_json import extract_json  # noqa: E402

PASSED = 0
FAILED = 0


def check(label: str, condition: bool, extra: str = "") -> None:
    global PASSED, FAILED
    if condition:
        PASSED += 1
        print(f"  [PASS] {label}")
    else:
        FAILED += 1
        print(f"  [FAIL] {label} {extra}")


# ==================================================================
print("1. 规则解析（LLM 不可用时的兜底）")
# ==================================================================
cases = [
    ("我想去南京玩三天，预算2000元，喜欢历史和美食",
     {"city": "南京", "days": 3}, {"历史", "美食"}),
    ("穷游厦门两天，随便逛逛",
     {"city": "厦门", "days": 2}, set()),
    ("带爸妈去杭州，轻松一点不要太累",
     {"city": "杭州", "profile": "悠闲"}, set()),
    ("去北京五日游，预算5000块，想看古迹和博物馆",
     {"city": "北京", "days": 5}, {"历史"}),
]
for text, expected, expected_tags in cases:
    parsed = nl_parse._rule_parse(text)
    ok = all(parsed.get(k) == v for k, v in expected.items())
    check(f"规则解析「{text[:18]}…」→ {expected}", ok, f"实际={parsed}")
    if expected_tags:
        check(f"  兴趣标签包含 {expected_tags}", expected_tags.issubset(set(parsed.get("tags") or [])),
              f"实际={parsed.get('tags')}")

must = nl_parse._rule_parse("必去总统府和中山陵").get("must_go") or []
check("规则解析能识别必去项，且按连接词拆成多个",
      "总统府" in must and "中山陵" in must, f"实际={must}")
check("规则解析能识别避开项",
      bool(nl_parse._rule_parse("避开人多的商业街").get("avoid")),
      f"实际={nl_parse._rule_parse('避开人多的商业街')}")
check("「中山陵」不会被误判为自然偏好（曾用裸『山』字匹配）",
      "自然" not in (nl_parse._rule_parse("想去中山陵看看").get("tags") or []),
      f"实际={nl_parse._rule_parse('想去中山陵看看').get('tags')}")

# ==================================================================
print("\n2. 字段清洗与白名单")
# ==================================================================
check("城市「北京市」归一为「北京」", nl_parse._clean_city("北京市") == "北京")
check("城市带空格噪声被拒绝", nl_parse._clean_city("南京 苏州") is None)
check("城市为空被拒绝", nl_parse._clean_city("  ") is None)
check("城市含标点被拒绝", nl_parse._clean_city("南京!") is None)
check("超长字符串被拒绝", nl_parse._clean_city("南京" * 8) is None)

days, notes = nl_parse._coerce_days(0)
check("天数 0 夹到 1 且有提示", days == 1 and bool(notes), f"days={days} notes={notes}")
days, notes = nl_parse._coerce_days(9999)
# 上限来自 config.MAX_TRIP_DAYS（不再硬编码 7），这里跟着一起断言
check(f"天数超上限夹到 {MAX_TRIP_DAYS} 且有提示",
      days == MAX_TRIP_DAYS and bool(notes), f"days={days} notes={notes}")
days, notes = nl_parse._coerce_days("abc")
check("天数非数字返回 None 且无提示", days is None and notes == [])

check("标签去重", nl_parse._clean_tags(["美食", "美食"]) == ["美食"])
check("标签数量截断到 6", len(nl_parse._clean_tags(list(nl_parse.VALID_TAGS) * 3)) <= 6)
check("偏好白名单过滤", nl_parse._clean_profile("豪华型") is None)
check("合法偏好通过", nl_parse._clean_profile("尽览") == "尽览")

places = nl_parse._clean_places(["总统府", "", "x" * 30, 123, "中山陵", "总统府"])
check("景点名清洗：去空/去超长/去非字符串/去重",
      places == ["总统府", "中山陵"], f"实际={places}")

# ==================================================================
print("\n3. LLM 回复的 JSON 抽取")
# ==================================================================
check("纯 JSON", extract_json('{"city": "南京"}') == {"city": "南京"})
check("markdown 代码块包裹",
      extract_json('```json\n{"city": "南京"}\n```') == {"city": "南京"})
check("前后有解释文字",
      extract_json('好的，结果如下：\n{"city": "南京"}\n希望有帮助') == {"city": "南京"})
check("数组返回 None（调用方无需再判类型）", extract_json('[1, 2, 3]') is None)
check("坏 JSON 返回 None", extract_json('{"city": ') is None)
check("空字符串返回 None", extract_json("") is None)

# ==================================================================
print("\n4. parse_request 全流程（模拟 LLM 输出）")
# ==================================================================
_original_extract = nl_parse._extract


def run_with_extract(fake):
    nl_parse._extract = fake
    try:
        return asyncio.run(nl_parse.parse_request(ParseRequestDTO(text="随便说说")))
    finally:
        nl_parse._extract = _original_extract


# 4a. LLM 给出越界值 → 全部夹取/过滤，并回报给用户
async def _bad_llm(_text):
    return (
        {"city": "南京市", "days": 99, "tags": ["美食", "不存在"],
         "profile": "豪华型", "must_go": ["总统府"], "avoid": []},
        "llm",
        [],
    )


vo = run_with_extract(_bad_llm)
check(f"越界天数被夹到 {MAX_TRIP_DAYS}", vo.request.days == MAX_TRIP_DAYS,
      f"days={vo.request.days}")
check("非法标签被白名单丢掉", vo.request.tags == ["美食"], f"tags={vo.request.tags}")
check("非法偏好回落到默认舒适型", vo.request.profile == "经典", f"profile={vo.request.profile}")
check("城市「南京市」归一为「南京」", vo.request.city == "南京", f"city={vo.request.city}")
check("必须在 notes 里说明夹取动作（天数越界）",
      any("天数" in n for n in vo.notes), f"notes={vo.notes}")
check("识别出的字段写进 inferred",
      "目的地" in vo.inferred and "必去景点" in vo.inferred, f"inferred={vo.inferred}")
check("越界的字段仍记为已识别（值来自文本）",
      "天数" in vo.inferred, f"inferred={vo.inferred}")

# 4b. LLM 返回不可解析 → 退回规则
async def _no_json(_text):
    return nl_parse._rule_parse(_text), "rule", ["大模型返回内容无法解析，已用本地规则解析，请核对表单"]


vo = run_with_extract(_no_json)
check("降级时 source 标记为 rule", vo.source == "rule", vo.source)
check("降级时给出提示", any("规则解析" in n for n in vo.notes), f"notes={vo.notes}")

# 4c. 文本没提到城市 → 用 defaults 兜底并提示
async def _no_city(_text):
    return {"city": None, "days": 4, "budget": None, "tags": [], "profile": None,
            "must_go": [], "avoid": []}, "llm", []


nl_parse._extract = _no_city
try:
    vo = asyncio.run(nl_parse.parse_request(
        ParseRequestDTO(text="去玩四天", defaults=PlanRequestDTO(
            city="苏州", days=2, budget=1500, tags=["历史"], profile="悠闲"))
    ))
finally:
    nl_parse._extract = _original_extract

check("城市缺失时沿用 defaults", vo.request.city == "苏州", vo.request.city)
check("标签缺失时沿用 defaults", vo.request.tags == ["历史"], f"tags={vo.request.tags}")
check("偏好缺失时沿用 defaults", vo.request.profile == "悠闲", vo.request.profile)
check("缺城市时给出提示", any("目的地" in n for n in vo.notes), f"notes={vo.notes}")
check("兜底字段写进 assumed（预算已移除）",
      "目的地" in vo.assumed and "预算" not in vo.assumed, f"assumed={vo.assumed}")

# 4d. 空文本被拒
try:
    asyncio.run(nl_parse.parse_request(ParseRequestDTO(text="   ")))
    check("空文本应当抛出业务异常", False, "未抛异常")
except Exception as exc:
    check("空文本被拒（ApiError）", type(exc).__name__ == "ApiError", type(exc).__name__)

print(f"\n{'=' * 60}")
print(f"通过 {PASSED} 项，失败 {FAILED} 项")
print(f"{'=' * 60}")
sys.exit(1 if FAILED else 0)
