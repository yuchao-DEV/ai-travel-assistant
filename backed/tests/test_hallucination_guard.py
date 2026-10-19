"""实体校验（幻觉防线）单测。

被测对象：``app.services.polish._validate_entities``

**这个文件的存在理由**：旧实现的 ``if`` 与 ``else`` 两个分支做的事完全一样
（都是 ``result.append(sentence)``），所以"删除含行程外 POI 名的句子"这条
约束**从未真正生效过**——而它是"LLM 只负责表达、不参与决策"这一架构承诺的
唯一技术保障。最后一条用例专门断言"新实现与旧实现行为不同"，
防止有人在后来的重构里又把它改回空转。

运行：
    cd backed
    .\\.venv\\Scripts\\python.exe tests\\test_hallucination_guard.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.polish import (  # noqa: E402
    _extract_poi_like_spans,
    _shares_distinctive_substring,
    _split_sentences,
    _validate_entities,
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


# ==================================================================
print("\n1. 切句")
# ==================================================================
check("按中文句号切句", _split_sentences("今天很好。明天更好。") == ["今天很好。", "明天更好。"],
      str(_split_sentences("今天很好。明天更好。")))
check("问号/感叹号也切", len(_split_sentences("去吗？去吧！好。")) == 3,
      str(_split_sentences("去吗？去吧！好。")))
check("英文句点也切", len(_split_sentences("A. B.")) == 2, str(_split_sentences("A. B.")))
check("换行也切", len(_split_sentences("第一行\n第二行")) == 2,
      str(_split_sentences("第一行\n第二行")))
check("末尾无标点也成句", _split_sentences("没有句号") == ["没有句号"])
check("标点保留在句尾", _split_sentences("只有一句。") == ["只有一句。"])
check("空字符串不抛异常", _split_sentences("") == [])
check("纯空白不产生句子", _split_sentences("   \n  ") == [])

# ==================================================================
print("\n2. 疑似 POI 名的形态抽取")
# ==================================================================
check("认出「灵隐寺」", any("灵隐寺" in s for s in _extract_poi_like_spans("下午去灵隐寺看看")),
      str(_extract_poi_like_spans("下午去灵隐寺看看")))
check("前导动词被剥掉（片段归一成地名核心）",
      _extract_poi_like_spans("下午去灵隐寺看看") == ["灵隐寺"],
      str(_extract_poi_like_spans("下午去灵隐寺看看")))
check("「登宝石山」归一为「宝石山」",
      _extract_poi_like_spans("登宝石山赏景") == ["宝石山"],
      str(_extract_poi_like_spans("登宝石山赏景")))
check("认出「西湖景区」", any("西湖景区" in s for s in _extract_poi_like_spans("西湖景区很美")),
      str(_extract_poi_like_spans("西湖景区很美")))
check("认出「拙政园」这类园名不依赖后缀也由引号兜住",
      _extract_poi_like_spans("推荐《拙政园》") == ["拙政园"],
      str(_extract_poi_like_spans("推荐《拙政园》")))
check("通用描述抽不出地名", _extract_poi_like_spans("这一天会很轻松") == [],
      str(_extract_poi_like_spans("这一天会很轻松")))
check("空输入返回空列表", _extract_poi_like_spans("") == [])

# --- 写景通用句不该被抽成地名（这是误删重灾区） ---
for _generic in ("尽享湖光山色。", "傍晚在湖边散步很惬意。",
                 "这里山清水秀，适合放松。", "前面有座桥。", "参观寺庙要注意礼仪。"):
    check(f"通用句抽不出地名：{_generic}", _extract_poi_like_spans(_generic) == [],
          str(_extract_poi_like_spans(_generic)))

# ==================================================================
print("\n3. 实体校验：按清单 0.3 的用例组")
# ==================================================================
# --- 保留通用描述 ---
_clean, _removed = _validate_entities("这一天节奏轻松，适合慢慢逛。", ["西湖"])
check("保留通用描述（原文返回）", _clean == "这一天节奏轻松，适合慢慢逛。", repr(_clean))
check("保留通用描述（无删除记录）", _removed == [], str(_removed))

# --- 保留合法景点 ---
_clean, _removed = _validate_entities("西湖的清晨人最少。", ["西湖"])
check("保留合法景点", _clean == "西湖的清晨人最少。" and _removed == [],
      f"{_clean!r} {_removed}")

# --- 删除未知景点 ---
_text = "下午去灵隐寺看飞来峰。西湖很美。"
_clean, _removed = _validate_entities(_text, ["西湖"])
check("删除含未知景点的那句", "灵隐寺" not in _clean, repr(_clean))
check("保留含合法景点的句子", "西湖很美。" in _clean, repr(_clean))
check("删除记录含被删的句子", len(_removed) == 1 and "灵隐寺" in _removed[0], str(_removed))

# --- 保留部分匹配 ---
_clean, _removed = _validate_entities("西湖景区里的苏堤值得走一走。", ["西湖"])
check("句中命中合法名即整句保留（不误删苏堤）",
      _clean == "西湖景区里的苏堤值得走一走。" and _removed == [],
      f"{_clean!r} {_removed}")

# --- 不误删无地名句 ---
_clean, _removed = _validate_entities("建议穿舒适的鞋。", ["西湖"])
check("不误删无地名句", _clean == "建议穿舒适的鞋。" and _removed == [],
      f"{_clean!r} {_removed}")

# --- 空输入 ---
for _empty in ("", None):
    try:
        _c, _r = _validate_entities(_empty, ["西湖"])
        check(f"空输入 {_empty!r} 不抛异常", True)
        check(f"空输入 {_empty!r} 返回空删除记录", _r == [], str(_r))
    except Exception as exc:  # pragma: no cover
        check(f"空输入 {_empty!r} 不抛异常", False, repr(exc))

# --- 无合法名可比对时一律不删（否则会把文案删空） ---
_clean, _removed = _validate_entities("下午去灵隐寺。西湖很美。", [])
check("valid_names 为空时不删除任何内容",
      _clean == "下午去灵隐寺。西湖很美。" and _removed == [], f"{_clean!r} {_removed}")

# --- 多句混合：只删该删的 ---
_mixed = "上午去拙政园。中午在平江路吃饭。下午去灵隐寺。晚上回酒店休息。"
_clean, _removed = _validate_entities(_mixed, ["拙政园", "平江路"])
check("混合文本只删未知景点句",
      "拙政园" in _clean and "平江路" in _clean and "灵隐寺" not in _clean,
      repr(_clean))
check("混合文本删除数为 1", len(_removed) == 1, str(_removed))
check("混合文本保留通用句", "晚上回酒店休息。" in _clean, repr(_clean))
check("拼接后句子顺序不乱",
      _clean == "上午去拙政园。中午在平江路吃饭。晚上回酒店休息。", repr(_clean))

# --- 合法名与疑似名互为包含（双向） ---
_clean, _removed = _validate_entities("西湖景区值得去。", ["西湖景区"])
check("合法名比疑似名更长时也算命中", _removed == [], str(_removed))

# ==================================================================
print("\n4. 回归：真实运行中的误删（最重要的一组）")
# ==================================================================
# 真实数据复现：一次真实 /api/plan 里，官方名很长而文案用的是短称，
# 形态片段又带前导动词，导致"包含关系"两个方向都不成立，
# 整句**合法**描述被删掉，description 直接变成空串。
_REAL_NAMES = [
    "杭州西湖风景名胜区-花港观鱼", "杭州西湖风景名胜区-曲院风荷",
    "北山街历史文化街区", "杭州西湖风景名胜区-柳浪闻莺",
    "7017浙菜·建德味((钱江新城店)", "杭州西湖风景名胜区宝石山",
    "杭州基督教会崇一堂", "钱江新城灯光秀", "城市阳台江堤步道",
    "钱江世纪城沿江公园绿道观景平台", "杭州钱塘江夜游(滨江码头)",
]
_REAL_DESC = (
    "杭州两日舒适之旅，预算692元，漫步西湖花港观鱼、曲院风荷、柳浪闻莺，"
    "登宝石山赏景，逛北山街；次日访崇一堂，观钱江新城灯光秀，走城市阳台与沿江绿道，"
    "夜游钱塘江，尽享江南韵味与现代风情。"
)
# 这三个片段是实测抽出来的、且旧判据全部判失败的
for _span in ("漫步西湖", "登宝石山", "逛北山"):
    check(f"片段「{_span}」能被判为命中（修复前为未命中）",
          any(_shares_distinctive_substring(_span, n) for n in _REAL_NAMES),
          f"片段={_span}")

_clean, _removed = _validate_entities(_REAL_DESC, _REAL_NAMES)
check("真实合法描述不被误删（description 不再变空串）",
      _clean == _REAL_DESC and _removed == [], f"clean={_clean[:40]!r} removed={_removed}")
check("真实描述里未被清空", len(_clean) > 50, str(len(_clean)))

# 同一批合法名下，真正的幻觉仍必须被删掉
_hallucinated = _REAL_DESC + "下午还可以去雷峰塔和宋城。"
_clean2, _removed2 = _validate_entities(_hallucinated, _REAL_NAMES)
check("同批合法名下仍能拦住真幻觉", len(_removed2) == 1 and "雷峰塔" in _removed2[0],
      str(_removed2))
check("拦住幻觉的同时保留了合法描述", _REAL_DESC in _clean2, repr(_clean2[:40]))

# --- 写景通用句不得被删除（修复前 4 句全被误删） ---
for _generic in ("尽享湖光山色。", "傍晚在湖边散步很惬意。",
                 "这里山清水秀，适合放松。", "在这里可以尽享湖光山色，感受江南韵味。"):
    _c, _r = _validate_entities(_generic, _REAL_NAMES)
    check(f"通用写景句不被误删：{_generic}", _c == _generic and _r == [],
          f"clean={_c!r} removed={_r}")

# 单字/双字核心不作为判定依据（MIN_CORE_LEN 的边界）
check("单字核心被丢弃（湖）", _extract_poi_like_spans("尽享湖") == [],
      str(_extract_poi_like_spans("尽享湖")))
check("双字核心也被丢弃（西湖，短于 MIN_CORE_LEN）",
      _extract_poi_like_spans("漫步西湖") == [], str(_extract_poi_like_spans("漫步西湖")))
check("三字核心保留（宝石山）",
      _extract_poi_like_spans("登宝石山") == ["宝石山"],
      str(_extract_poi_like_spans("登宝石山")))

# 共享子串判据本身的边界
check("无公共子串 → 不命中", not _shares_distinctive_substring("雷峰塔", "杭州基督教会崇一堂"))
check("互为包含 → 命中", _shares_distinctive_substring("西湖", "杭州西湖风景名胜区"))
check("空串 → 不命中",
      not _shares_distinctive_substring("", "西湖") and not _shares_distinctive_substring("西湖", ""))
check("单字重合不算命中（min_len=2）",
      not _shares_distinctive_substring("塔", "杭州基督教会崇一堂"))

# ==================================================================
print("\n5. 回归：旧行为确实是空转")
# ==================================================================
def _legacy_validate_entities(text, valid_names):
    """旧实现（保留在此仅用于对照）。

    注意 if/else 两个分支做的事完全一样，所以它永远原样返回。
    """
    import re as _re
    if not text or not valid_names:
        return text
    sentences = _re.split(r"([。！？\.\!\?])", text)
    result = []
    i = 0
    while i < len(sentences):
        sentence = sentences[i]
        has_valid = any(name in sentence for name in valid_names)
        if has_valid or len(sentence.strip()) < 10:
            result.append(sentence)
            result.append(sentences[i + 1]) if i + 1 < len(sentences) else None
            i += 2 if i + 1 < len(sentences) else 1
        else:
            result.append(sentence)
            result.append(sentences[i + 1]) if i + 1 < len(sentences) else None
            i += 2 if i + 1 < len(sentences) else 1
    return "".join(result)


_unknown_text = "下午去灵隐寺看飞来峰。西湖很美。"
_new_clean, _new_removed = _validate_entities(_unknown_text, ["西湖"])
_old_clean = _legacy_validate_entities(_unknown_text, ["西湖"])
check("旧实现在未知景点句上确实什么都不删（证明它是空转）",
      _old_clean == _unknown_text and "灵隐寺" in _old_clean, repr(_old_clean))
check("新实现在同一输入上行为不同（防线真的生效了）",
      _new_clean != _old_clean and "灵隐寺" not in _new_clean,
      f"new={_new_clean!r} old={_old_clean!r}")

# 再钉一层：新实现必须返回元组，避免有人改回只返回字符串
_result = _validate_entities("西湖很美。", ["西湖"])
check("返回值是 (文本, 删除列表) 二元组",
      isinstance(_result, tuple) and len(_result) == 2
      and isinstance(_result[0], str) and isinstance(_result[1], list),
      str(type(_result)))

print(f"\n{'=' * 60}")
print(f"通过 {PASSED} 项，失败 {FAILED} 项")
print(f"{'=' * 60}")
sys.exit(1 if FAILED else 0)
