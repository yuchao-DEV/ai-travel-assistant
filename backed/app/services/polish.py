"""LLM 润色服务：只负责表达，不参与决策。

输入：算法引擎产出的 ItineraryVO（结构化行程）
输出：顶层 description + 每日 daily_summary

约束：不得新增/修改时间、地点、费用
后处理：实体校验——从 LLM 输出抽取"疑似 POI 名"的片段，与行程里真实存在的
        地点名对照，**删掉含未知 POI 名的整句**，并把删除记录返回出来。
"""
import logging
import re
from typing import List, Tuple

from app.llm import chat
from app.schemas import ItineraryVO
from app.services.llm_json import extract_json

logger = logging.getLogger("app.polish")

# 文案生成用温度：比抽取高（要自然），但比默认 0.7 低（减少发散编造）
POLISH_TEMPERATURE = 0.3

# 疑似 POI 名的形态特征（地名常见后缀）。**不依赖词典**——
# 靠词典就只能拦住已知景点，恰恰拦不住模型编出来的新名字。
#
# 两点刻意的取舍：
# ① 惰性量词从最早位置起匹配，所以片段可能带上前导动词（"下午去灵隐寺"）。
#    这是可接受的：命中判定用"互为包含"，``name in span`` 照样成立，
#    而且片段只用于内部判断、不会出现在日志或返回给用户的删除记录里。
# ② 后缀表里**故意没有单字「园」**。加上它确实能多认出「拙政园」「留园」，
#    但也会把「自家花园」「校园」这类通用描述误判成未知地点并整句删掉。
#    宁可漏拦（保留一句可疑文案），也不要误删（丢掉一句正常文案）。
_PLACE_SHAPE_RE = re.compile(
    r"[\u4e00-\u9fa5]{2,12}?"
    r"(?:景区|风景区|公园|博物馆|美术馆|纪念馆|图书馆|展览馆|科技馆|"
    r"寺|庙|塔|桥|街|路|巷|古镇|老街|步行街|广场|山|湖|岛|湾|"
    r"大剧院|音乐厅|乐园|温泉|湿地)"
)
# 书名号/引号包裹的专名，通常就是在说某个具体地点
_QUOTED_RE = re.compile(r"[《【\"'“”「」]([^》】\"'“”「」]{2,20})[》】\"'“”「」]")

# 句子终止符（中英文），切句时保留在句尾
_SENTENCE_END_RE = re.compile(r"[。！？!?\.]+")

# 地名核心的最短长度。
# 单字核心（"湖""山""街"）和双字核心（"西湖"）太短，无法与"湖光""山色""湖边"
# 这类通用写景词区分——实测「尽享湖光山色。」会被抽成片段「尽享湖」、
# 「傍晚在湖边散步」抽成「傍晚在湖」，「这里山清水秀」抽成「这里山」，
# 三句通用描述都会被当成"行程外景点"整句删掉。要求 ≥3 字后这些都不再误命中。
MIN_CORE_LEN = 3

# 前导填充词：时间、动作、方位、程度。这些绝不可能是地名的一部分，
# 惰性量词却会把它们吞进片段，所以匹配后要剥掉。
_LEAD_FILLER_RE = re.compile(
    r"^(?:第[一二三四五六七八九十\d]+天|[Dd]\d|"
    r"上午|下午|晚上|早上|中午|傍晚|夜里|清晨|当天|次日|"
    r"漫步|步行|夜游|游览|参观|前往|途经|经过|走进|登顶|登上|爬上|"
    r"这里|那里|前面|后面|旁边|附近|"
    r"可以|还能|又可|再|还|也|都|就|很|最|非常|十分|"
    r"尽享|享受|感受|品味|领略|欣赏|体验|"
    # 存现动词 + 量词：「前面有座桥」要剥成「桥」而不是「有座桥」
    r"有|座|个|条|间|处|栋|幢|片|块|"
    r"在|到|去|逛|游|看|赏|观|登|访|走|吃|住|玩|沿)+"
)


def _build_polish_prompt(itinerary: ItineraryVO) -> str:
    """构造润色 prompt：把结构化行程喂给 LLM，让它只生成文案。"""
    # 提取行程摘要给 LLM（不含 score 等内部数据）
    days_summary = []
    for day in itinerary.days:
        items_str = []
        for item in day.items:
            items_str.append(f"  - {item.start}-{item.end} {item.name}（{item.cost}元）")
        items_text = "\n".join(items_str) if items_str else "  （无安排）"
        weather = itinerary.weather[0] if itinerary.weather else "未知"
        days_summary.append(
            f"第{len(days_summary) + 1}天（{day.date}，{weather}）：\n{items_text}"
        )

    itinerary_text = "\n\n".join(days_summary)

    return (
        "你是一个旅游文案润色师。以下是算法引擎生成的结构化行程，"
        "请你为它撰写顶层描述和每日导语。\n\n"
        f"城市：{itinerary.city}\n"
        f"偏好：{itinerary.profile}\n"
            f"行程详情：\n{itinerary_text}\n\n"
        "要求：\n"
        "1. 生成一段顶层描述（50-100字），概述本次行程的亮点和特色\n"
        "2. 为每一天生成一句导语（20-40字），点出当天最值得期待的体验\n"
        "3. 只能描述上述已列出的景点和费用，不得新增、修改或替换任何景点、时间、费用\n"
        "4. 语言自然有温度，避免模板化\n\n"
        "只返回如下 JSON，不要任何多余文字或代码块标记：\n"
        "{\n"
        '  "description": "顶层描述",\n'
        '  "daily_summaries": ["第1天导语", "第2天导语", ...]\n'
        "}\n"
    )


def _core_name(span: str) -> str:
    """剥掉前导填充词，得到地名核心（反复剥直到稳定）。"""
    prev, cur = None, (span or "").strip()
    while prev != cur:
        prev = cur
        cur = _LEAD_FILLER_RE.sub("", cur).strip()
    return cur


def _extract_poi_like_spans(text: str) -> List[str]:
    """抽取文本中"疑似景点名"（形态特征 + 核心长度过滤，**非词典匹配**）。

    只做粗筛：宁可多抽（后续只在"完全不命中"时才删句），
    也不要想当然地按词典精确匹配——模型编造的名字本来就不在词典里。

    两处处理都是为了**减少误删通用描述**：
    ① 剥掉前导填充词，让「下午去灵隐寺」归一成「灵隐寺」；
    ② 核心短于 ``MIN_CORE_LEN`` 的直接丢弃（「尽享湖」→「湖」不算地名）。
    引号/书名号包裹的专名不做长度过滤——那是明确的专名标记。
    """
    if not text:
        return []
    spans = [m.group(1).strip() for m in _QUOTED_RE.finditer(text) if m.group(1).strip()]
    for m in _PLACE_SHAPE_RE.finditer(text):
        core = _core_name(m.group(0))
        if len(core) >= MIN_CORE_LEN:
            spans.append(core)
    return spans


def _split_sentences(text: str) -> List[str]:
    """按中英文句读切句，标点保留在句尾（便于原样拼回）。"""
    if not text:
        return []
    out: List[str] = []
    buf = ""
    for ch in text:
        if ch == "\n":
            if buf.strip():
                out.append(buf)
            buf = ""
            continue
        buf += ch
        if _SENTENCE_END_RE.fullmatch(ch):
            if buf.strip():
                out.append(buf)
            buf = ""
    if buf.strip():
        out.append(buf)
    return out


def _shares_distinctive_substring(span: str, name: str, min_len: int = 2) -> bool:
    """判断疑似片段与合法名是否"指向同一个地方"。

    为什么不能只用包含关系：**文案里的叫法通常比高德官方名短得多**。
    实测一次真实行程，官方名是「杭州西湖风景名胜区-花港观鱼」，而文案写的是
    「漫步西湖花港观鱼」；形态抽取得到的片段又是「漫步西湖」（带前导动词）。
    此时 `span in name` 与 `name in span` **两个方向都不成立**，
    整句合法描述会被当成幻觉删掉——一次真实运行就把 description 删成了空串。

    也不能靠"剥掉前导动词"来解决：那会把「观前街」「登高路」这类
    本身以动词字开头的地名削坏。

    所以改为**共享特征子串**：只要片段与合法名有长度 ≥ ``min_len`` 的公共子串，
    就认为指的是同一个地方。这个方向偏保守（宁可保留），符合本模块
    "宁可漏拦一句可疑文案，也不要误删一句正常文案"的取舍。
    """
    if not span or not name:
        return False
    if span in name or name in span:
        return True
    for i in range(len(span) - min_len + 1):
        if span[i:i + min_len] in name:
            return True
    return False


def _validate_entities(text: str, valid_names: List[str]) -> Tuple[str, List[str]]:
    """实体校验：删除含未知 POI 名的句子。

    返回 ``(净化后的文本, 被删除的句子列表)``——
    把删除记录返回出来，才能被测试断言、被日志审计、被效果评估统计。
    签名从"只返回字符串"改成元组是**有意的**：旧实现两个分支做的事完全一样
    （都是 ``result.append(sentence)``），实体校验其实从未删过任何句子，
    而调用方也无从知道。只返回字符串的话，这个 bug 可以一直藏着。

    规则（保守优先，避免误删通用描述）：
      1. 句中出现了疑似 POI 名，且与某个 ``valid_names`` 共享特征子串 → 保留；
      2. 句中出现了疑似 POI 名，且与所有 ``valid_names`` 都无共同特征 → 删除并记录；
      3. 句中没有任何疑似 POI 名 → 一律保留（通用描述，如"这一天会很轻松"）。
    """
    if not text:
        return text or "", []
    if not valid_names:
        # 没有可比对的合法名时不做任何删除：此时"全部未知"是必然的，删了就只剩空文案
        return text, []

    names = [n for n in valid_names if n]
    kept: List[str] = []
    removed: List[str] = []
    for sentence in _split_sentences(text):
        spans = _extract_poi_like_spans(sentence)
        if not spans:
            kept.append(sentence)          # 规则 3：通用描述
            continue
        hit = any(
            _shares_distinctive_substring(span, name)
            for span in spans
            for name in names
        )
        if hit:
            kept.append(sentence)
        else:
            removed.append(sentence)       # 规则 2：疑似地点名但行程里没有
    return "".join(kept), removed


async def polish_itinerary(itinerary: ItineraryVO) -> ItineraryVO:
    """对结构化行程做 LLM 润色，回填 description 和 daily_summary。

    约束：LLM 不得新增/修改时间地点费用，后处理做实体校验。
    """
    if not itinerary.enhanced or not itinerary.days:
        return itinerary

    # 收集所有合法 POI 名
    all_names = []
    for day in itinerary.days:
        for item in day.items:
            all_names.append(item.name)

    prompt = _build_polish_prompt(itinerary)

    try:
        # 文案类任务适度低温：比默认 0.7 收敛，但不至于像抽取那样死板
        response = await chat("", prompt, temperature=POLISH_TEMPERATURE)
        data = extract_json(response)
        if data:
            # 实体校验：删掉含行程外 POI 名的句子，并记录删了什么
            description, removed = _validate_entities(
                data.get("description", ""), all_names
            )
            itinerary.description = description
            if removed:
                logger.warning("润色描述含行程外实体，已删除 %d 句：%s",
                               len(removed), removed[:3])

            daily_summaries = data.get("daily_summaries", [])
            for i, day in enumerate(itinerary.days):
                if i < len(daily_summaries):
                    clean, removed_day = _validate_entities(
                        daily_summaries[i], all_names
                    )
                    day.daily_summary = clean
                    if removed_day:
                        logger.warning("第 %d 天导语含行程外实体，已删除 %d 句：%s",
                                       i + 1, len(removed_day), removed_day[:3])
    except Exception as e:
        print(f"[polish] LLM 润色失败（不影响行程本身）: {e}")

    return itinerary
