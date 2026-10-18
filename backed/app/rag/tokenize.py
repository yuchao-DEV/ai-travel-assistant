"""中文分词：bigram + unigram 混合，零依赖。

为什么不用结巴等分词器：本项目坚持"只依赖标准库"（见 README 的依赖表）。
而 FTS5 自带的 ``unicode61`` 分词器把**一整串中文当成一个 token**，
"杭州西湖" 会变成单个词，检索直接失效——所以必须自己切好、以空格分隔写入。

**为什么 bigram + unigram 混用**：
- 只用 unigram（单字）：召回高但噪音极大，"西湖"会命中所有含"西"或"湖"的文档；
- 只用 bigram（双字）：精度高但漏召回，查询"杭州"时就匹配不到"杭州市"被切成的
  "杭州/州市"（实际上能匹配，但"西湖区"切成的"西湖/湖区"匹配"西湖"是可以的）；
- 混用两者：bigram 保精度、unigram 保召回，由 BM25 的 IDF 自动压低常见单字的权重。

这是 FTS5 方案里最容易被忽略、却直接决定检索质量的一步。
"""
import re
from typing import List, Set

# 中文（含扩展 A 区常用字）与英数字的切分：
# 中文按字切，英文/数字按整词切（"West Lake" → west / lake）
_TOKEN_RE = re.compile(r"[\u4e00-\u9fff]+|[A-Za-z0-9]+")

# 停用词：中文虚词 + 英文常见虚词。
# 这些字单独出现时几乎没有区分度，留着只会制造噪音命中。
STOPWORDS: Set[str] = {
    # 中文单字虚词
    "的", "了", "和", "与", "及", "或", "是", "在", "有", "我", "你", "他", "她", "它",
    "这", "那", "个", "们", "也", "都", "就", "还", "很", "最", "而", "但", "并",
    "被", "把", "给", "让", "从", "到", "对", "为", "以", "之", "其", "该", "等",
    "着", "过", "地", "得", "着", "啊", "吧", "呢", "吗", "呀", "哦",
    # 中文常见虚词（双字及以上）
    "我们", "你们", "他们", "可以", "能够", "以及", "或者", "但是", "因为", "所以",
    "如果", "这个", "那个", "这些", "那些", "什么", "怎么", "如何", "已经", "非常",
    "一个", "一些", "进行", "通过", "对于", "关于", "由于", "并且", "而且",
    # 英文
    "a", "an", "the", "of", "to", "in", "on", "at", "for", "and", "or", "is", "are",
    "was", "were", "be", "been", "it", "its", "this", "that", "these", "those",
    "with", "by", "from", "as", "we", "you", "they", "he", "she",
}


def tokenize_zh(text: str) -> str:
    """把文本切成空格分隔的检索词串（bigram + unigram）。

    >>> tokenize_zh("杭州西湖")
    '杭 杭州 州 州西 西 西湖 湖'

    英文/数字按整词保留并转小写：``"West Lake"`` → ``"west lake"``。
    纯标点或空白返回空串。
    """
    if not text:
        return ""

    out: List[str] = []
    for match in _TOKEN_RE.finditer(text):
        piece = match.group(0)
        if piece.isascii():
            word = piece.lower()
            if word not in STOPWORDS:
                out.append(word)
            continue
        # 中文：先出 unigram，再出 bigram
        chars = [c for c in piece if c not in STOPWORDS]
        out.extend(chars)
        out.extend(chars[i] + chars[i + 1] for i in range(len(chars) - 1))

    # 去重但保序：重复词对 BM25 没有额外收益，反而让 tokens 列变长
    seen: Set[str] = set()
    uniq: List[str] = []
    for token in out:
        if token not in seen:
            seen.add(token)
            uniq.append(token)
    return " ".join(uniq)


def query_terms(text: str) -> List[str]:
    """把查询串切成词表（**只用 bigram**，仅在无 bigram 时才退到单字）。

    **为什么查询不能带单字**：索引里保留单字是为了保召回，但查询一旦带上单字，
    BM25 会被噪音主导——实测「你们能订酒店吗」切出单字 能/订/酒/店，
    这四个字在 1,133 篇语料里到处都有，于是真正含"酒店预订"的《系统能力边界》
    被挤到第 4 名；而 RRF 只看名次，最终结果就是错的。

    语料越大、单字的相对区分度越低，这个问题越明显。所以：
    查询优先用 bigram（"酒店"比"酒"选择性高得多），只有查询短到切不出 bigram
    （如单字查询"湖"）时才退到单字。
    """
    if not text:
        return []
    full = tokenize_zh(text).split()
    bigrams = [t for t in full if len(t) >= 2]
    if bigrams:
        return bigrams
    return full


def escape_fts_query(terms: List[str]) -> str:
    """把词表拼成安全的 FTS5 MATCH 表达式。

    每个词用双引号包住再 OR 起来——这样 "" " 等特殊字符不会破坏语法。
    权重交给 BM25 的 IDF 处理，这里不做人工加权。
    """
    safe = [t.replace('"', '""') for t in terms if t]
    if not safe:
        return ""
    return " OR ".join(f'"{t}"' for t in safe)
