"""Agent 协议层：系统提示词 + 输出校验（F1 数值校验 / F3 引用校验）。

**F1（数值校验）**：答复里出现的金额与时刻，必须能在**结构化行程**里找到出处。
模型很擅长"顺手写一个看起来合理的数字"——"门票大约 60 元""9:30 到灵隐寺"，
而行程里根本没有这两个值。这类编造最难被发现，因为它读起来完全合理。
处理方式是**按句剥离**：含无出处数值的句子整句删掉，而不是把数字改掉
（改数字等于替模型编一个新的）。

**F3（引用校验）**：答复里若写了 ``[3]`` 这样的角标，而本次检索并没有返回
``chunk_id=3``，就把角标去掉——不能让用户看到一个指向不存在来源的引用。
只剥角标不删句子：句子本身可能没问题，只是引用标错了。

两道校验都**只做减法**：绝不改写、绝不新增。校验器自己编内容就失去了意义。
"""
import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

# 金额：¥60 / ￥60.5 / 60元
_AMOUNT_RE = re.compile(r"(?:[¥￥]\s*(\d+(?:\.\d+)?))|(?:(\d+(?:\.\d+)?)\s*元)")
# 时刻：9:30 / 09:30
_TIME_RE = re.compile(r"\b([01]?\d|2[0-3]):([0-5]\d)\b")
# 角标引用：[1] 【2】
_CITE_RE = re.compile(r"[\[【](\d{1,3})[\]】]")
# 句子切分（中文句末标点）
_SENT_SPLIT_RE = re.compile(r"(?<=[。！？；\n])")

# 数值容差：行程里的 60 与文案里的 60.0 视为同一个数
_EPS = 0.01


@dataclass
class ValidationReport:
    """校验结果。``removed_sentences`` 会被记进日志，便于回溯模型编了什么。"""
    text: str
    removed_sentences: List[str] = field(default_factory=list)
    removed_citations: List[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.removed_sentences or self.removed_citations)


def collect_allowed_values(itinerary: Optional[Dict[str, Any]]) -> Tuple[Set[float], Set[str]]:
    """从结构化行程里收集"有出处"的金额与时刻。"""
    amounts: Set[float] = set()
    times: Set[str] = set()
    if not itinerary:
        return amounts, times

    def _add_amount(value: Any) -> None:
        try:
            amounts.add(round(float(value), 2))
        except (TypeError, ValueError):
            pass

    def _add_time(value: Any) -> None:
        text = str(value or "").strip()
        if _TIME_RE.fullmatch(text):
            hh, mm = text.split(":")
            times.add(f"{int(hh):02d}:{mm}")

    for day in itinerary.get("plan") or []:
        for item in day.get("items") or []:
            _add_amount(item.get("cost"))
            _add_time(item.get("start"))
            _add_time(item.get("end"))
    return amounts, times


def _amount_matches(value: float, allowed: Set[float]) -> bool:
    return any(abs(value - a) <= _EPS for a in allowed)


def _time_matches(value: str, allowed: Set[str]) -> bool:
    if value in allowed:
        return True
    # 9:30 与 09:30 视为同一个时刻
    hh, mm = value.split(":")
    return f"{int(hh):02d}:{mm}" in allowed


def validate_answer(
    text: str,
    *,
    itinerary: Optional[Dict[str, Any]] = None,
    known_chunk_ids: Optional[Iterable[int]] = None,
) -> ValidationReport:
    """对最终答复做 F1 / F3 校验，返回清理后的文本与处理记录。

    ``itinerary`` 为空时**跳过 F1**——没生成行程就没有"有出处的数值"这个基准，
    此时剥离所有金额会把正常回答（如"预算是 3000 元"）也删掉。
    """
    report = ValidationReport(text=text or "")
    if not report.text:
        return report

    # ---------------------------------------------------------- F3 引用校验
    if known_chunk_ids is not None:
        allowed_ids = {int(x) for x in known_chunk_ids}

        def _citesub(match: "re.Match[str]") -> str:
            if int(match.group(1)) in allowed_ids:
                return match.group(0)
            report.removed_citations.append(match.group(0))
            return ""

        report.text = _CITE_RE.sub(_citesub, report.text)

    # ---------------------------------------------------------- F1 数值校验
    if itinerary:
        amounts, times = collect_allowed_values(itinerary)
        kept: List[str] = []
        for sentence in _SENT_SPLIT_RE.split(report.text):
            if not sentence.strip():
                continue
            bad = False
            for match in _AMOUNT_RE.finditer(sentence):
                raw = match.group(1) or match.group(2)
                try:
                    value = float(raw)
                except (TypeError, ValueError):
                    continue
                if not _amount_matches(value, amounts):
                    bad = True
                    break
            if not bad:
                for match in _TIME_RE.finditer(sentence):
                    if not _time_matches(match.group(0), times):
                        bad = True
                        break
            if bad:
                report.removed_sentences.append(sentence.strip())
            else:
                kept.append(sentence)
        report.text = "".join(kept).strip()

    return report


# ---------------------------------------------------------------- 系统提示词

SYSTEM_PROMPT = """你是「AI 旅游助手」的行程助手。你的职责是理解用户意图、调用工具取数、\
并用工具返回的真实数据作答。

## 铁律

1. **不编造事实**。景点、坐标、评分、价格、营业时间、天气、距离，一律来自工具返回。
   工具没给的就不要写；拿不到就如实说"暂时查不到"。
2. **产出行程只能通过 plan_itinerary**。不要自己挑选景点、自己编排顺序——\
   行程由算法引擎生成，你负责收集参数与解释结果。
2b. **用户说了出发地就必须传给 plan_itinerary 的 `origin`**。\
   例："从北京出发去亳州玩三天" → `city=亳州, days=3, origin=北京`。\
   漏传 `origin` 会让前端行程页退回默认的「当涂」，城际距离与交通全按错误起点算。\
   用户没说出发地时可以不传。
3. **涉及预约、闭馆、票价口径、系统能力边界的问题，必须先调 search_knowledge**。\
   这些内容在你的记忆里不可靠。
4. **不要用相近的名字顶替**。用户说"灵隐寺"但只找到"灵隐寺飞来峰"时，\
   要说明差异，而不是当成同一个地点。

## 门票口径（必须原样传达，不得简化）

行程里每个地点的费用都带 `cost_source`：

- `real`：高德返回的真实人均消费（只有餐饮有）
- `third_party`：知识库语料里的**真实成人票价**
- `average`：语料里只有优待票或含服务的票，按**均价**估算，**不等于门票价**
- `average`：只有优待票或只有服务项（游船/讲解），**不是门票价**，界面上**不显示数字**，只显示备注文本
- `None`：没有票价数据，界面上不显示数字，只显示「门票待确认」

`average` 和 `None` 的地点都**不要编价格**；`average` 时如要提及，必须说明"这是服务或优待票的均价，不是门票价"。

## 能力边界（用户问起时如实回答）

- 能做：行程规划、地点检索、灵感推荐、城际距离与驾车耗时估算、旅途问答
- **不能做**：订酒店、买机票火车票、查真实班次、给出门票的最终价格

## 交互

- 信息不足时**先问清楚**再调用工具（目的地、天数、预算、兴趣）。\
  但不要反复追问——能从上下文推断的就直接用，并说明你假设了什么。
- 每次调用工具前，用一句自然语言说明你在做什么。
- 最终答复简洁口语化，不要罗列工具的原始 JSON。
- 如果工具失败，基于已有信息继续回答，并说明哪部分没查到。

【最终答复的排版规范】——不遵守会被前端渲染成一大坨难读的段落：
1. **每一天单独起一行**，格式为 `Day 1（主题）`，后面紧跟该天的行程。
2. **每个停靠点单独一行**，以 `- ` 开头，例如：
   `- 09:00 苏州博物馆 → 11:10 拙政园 → 15:58 园林博物馆`
3. **每一条提醒单独一行**，用 `1. ` `2. ` `3. ` 编号，不要把它们挤进一段话里。
4. **段落之间空一行**。不要用 `###`、表格、链接、图片。
5. 只允许 `**加粗**` 做强调；不要写 `---` 分隔线。
6. 不要在句子中间硬塞 `Day 2（西线）`——它必须独立成行。

正确示例（注意每一行）：
```
Day 1（市区东线，步行可串）
- 09:00 苏州博物馆 → 11:10 拙政园 → 15:58 园林博物馆
- 16:36 平江路吃饭

Day 2（西线）
- 09:00 沧浪亭 → 12:57 留园 → 15:03 寒山寺

几个要提醒你的：
1. 天气：10/23 小雨，园林多在户外，建议带伞。
2. 闭馆日：苏州博物馆周一全天闭馆。
3. 门票：票价数据未取到，显示「门票待确认」时不要编数字。
```"""
