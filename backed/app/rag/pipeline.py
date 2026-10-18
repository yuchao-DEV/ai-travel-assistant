"""文档切分管线：解析 → 清洗 → 切分 → 元数据。

切分策略（按优先级）：
1. **优先按 Markdown 标题切**——标题本身带语义，"杭州交通 > 机场接驳"这种
   标题路径能显著提升检索命中率，所以要作为独立的元数据保留下来；
2. 超长段落再按句子边界二次切分，**保持标点完整**，绝不从句子中间截断；
3. 相邻块之间保留 ``CHUNK_OVERLAP`` 的重叠，避免答案正好落在切口两侧；
4. 过短的尾块并入前一块（否则会产生大量"半句话"块污染检索）。

文档头部支持一段 HTML 注释形式的元数据（见 2.1 节语料规范）：

    <!--
    city: 杭州
    category: transport
    source: 自撰整理
    source_url: https://example.com/ref
    doc_date: 2026-01
    -->
"""
import hashlib
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from app.config import CHUNK_OVERLAP, MAX_CHUNK_SIZE, MIN_CHUNK_SIZE

# 文档头部的元数据注释块
_META_RE = re.compile(r"^\s*<!--(.*?)-->\s*", re.S)
# Markdown 标题
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*$")
# 句子边界：中文句读 + 英文句点（后跟空格或结尾），保留标点
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[。！？!?；;])|(?<=\.)\s+")
# 需要清理的 Markdown 行内标记
_INLINE_MARKS = [
    (re.compile(r"\*\*(.+?)\*\*"), r"\1"),      # 粗体
    (re.compile(r"\*(.+?)\*"), r"\1"),          # 斜体
    (re.compile(r"`(.+?)`"), r"\1"),            # 行内代码
    (re.compile(r"\[(.+?)\]\(.+?\)"), r"\1"),   # 链接 → 保留文字
]


@dataclass
class Chunk:
    """一个可检索的文档块。"""
    content: str
    chunk_index: int
    heading_path: str = ""
    meta: Dict[str, Any] = field(default_factory=dict)

    @property
    def char_count(self) -> int:
        return len(self.content)


def parse_meta_header(text: str) -> Tuple[Dict[str, str], str]:
    """剥离并解析文档头部的 ``<!-- key: value -->`` 元数据块。

    返回 ``(meta 字典, 去掉元数据块的正文)``。
    """
    if not text:
        return {}, ""
    match = _META_RE.match(text)
    if not match:
        return {}, text
    meta: Dict[str, str] = {}
    for line in match.group(1).splitlines():
        line = line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            meta[key] = value
    return meta, text[match.end():]


def clean_text(text: str) -> str:
    """清洗 Markdown 行内标记与多余空白，保留段落结构。"""
    if not text:
        return ""
    out = text.replace("\r\n", "\n").replace("\r", "\n")
    for pattern, repl in _INLINE_MARKS:
        out = pattern.sub(repl, out)
    # 去掉 HTML 注释残留与表格分隔行
    out = re.sub(r"<!--.*?-->", "", out, flags=re.S)
    out = re.sub(r"^\s*\|?[\s:\-|]+\|?\s*$", "", out, flags=re.M)
    # 压缩多余空行与行尾空白
    out = re.sub(r"[ \t]+", " ", out)
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out.strip()


def _split_sentences(text: str) -> List[str]:
    """按句读切分，保留标点。"""
    parts = [p.strip() for p in _SENTENCE_SPLIT_RE.split(text) if p and p.strip()]
    return parts or ([text.strip()] if text.strip() else [])


def _hard_split(text: str, limit: int) -> List[str]:
    """句子本身就超长时的兜底：按 limit 硬切（宁可切坏也不能让块无限大）。"""
    return [text[i:i + limit] for i in range(0, len(text), limit)] or [text]


def _pack_sentences(sentences: List[str], max_size: int) -> List[str]:
    """把句子贪心装箱成不超过 max_size 的块（不切断句子）。"""
    blocks: List[str] = []
    buf = ""
    for sentence in sentences:
        if len(sentence) > max_size:
            if buf:
                blocks.append(buf)
                buf = ""
            blocks.extend(_hard_split(sentence, max_size))
            continue
        if not buf:
            buf = sentence
        elif len(buf) + len(sentence) <= max_size:
            buf += sentence
        else:
            blocks.append(buf)
            buf = sentence
    if buf:
        blocks.append(buf)
    return blocks


def _apply_overlap(blocks: List[str], overlap: int) -> List[str]:
    """给相邻块加尾部重叠，避免答案正好落在切口上。"""
    if overlap <= 0 or len(blocks) <= 1:
        return blocks
    out = [blocks[0]]
    for prev, cur in zip(blocks, blocks[1:]):
        tail = prev[-overlap:] if len(prev) > overlap else prev
        out.append(tail + cur if not cur.startswith(tail) else cur)
    return out


def _merge_short_tail(chunks: List[Tuple[str, str]], min_size: int) -> List[Tuple[str, str]]:
    """把过短的尾块并入前一块——但**只在同一个标题路径内合并**。

    合并的目的是消掉"被从中间切开、只剩半句话"的碎片，那只发生在同一小节内部。
    跨小节合并会把"灵隐寺要预约"和"博物馆周一闭馆"揉成一块、共用一个标题，
    检索命中后给出的 heading_path 是错的，反而降低了可解释性。
    短而聚焦的块对检索是有利的，不必强行凑长度。
    """
    if not chunks:
        return []
    merged: List[Tuple[str, str]] = []
    for content, heading in chunks:
        if merged and len(content) < min_size and merged[-1][1] == heading:
            prev_content, prev_heading = merged[-1]
            merged[-1] = (prev_content + content, prev_heading)
        else:
            merged.append((content, heading))
    return merged


def chunk_document(text: str, meta: Optional[Dict[str, Any]] = None) -> List[Chunk]:
    """把一篇文档切成可检索的块。

    ``meta`` 会合并进每个 :class:`Chunk` 的 ``meta``（文档头部注释里的字段优先级更低）。
    返回值保证：块非空、块内不截断句子（除超长句兜底）、相邻块有重叠。
    """
    header_meta, body = parse_meta_header(text or "")
    merged_meta: Dict[str, Any] = {**header_meta, **(meta or {})}
    body = clean_text(body)
    if not body:
        return []

    # 1) 先按标题分段，记录层级路径
    sections: List[Tuple[str, str]] = []   # (正文, 标题路径)
    path: List[str] = []
    buf: List[str] = []

    def flush() -> None:
        content = "\n".join(buf).strip()
        if content:
            sections.append((content, " > ".join(path)))
        buf.clear()

    for line in body.split("\n"):
        heading = _HEADING_RE.match(line)
        if heading:
            flush()
            level = len(heading.group(1))
            title = heading.group(2).strip()
            del path[level - 1:]
            while len(path) < level - 1:
                path.append("")
            path.append(title)
        else:
            buf.append(line)
    flush()

    # 2) 每段内按句子装箱 + 重叠。
    # 装箱预算要先扣掉重叠量，否则「块 ≤ MAX_CHUNK_SIZE」与「相邻块有重叠」
    # 这两个约束无法同时成立（重叠会额外加在块首）。
    budget = MAX_CHUNK_SIZE - CHUNK_OVERLAP
    if budget <= 0:
        budget = MAX_CHUNK_SIZE
    collected: List[Tuple[str, str]] = []
    for content, heading in sections:
        for block in _apply_overlap(
            _pack_sentences(_split_sentences(content), budget), CHUNK_OVERLAP
        ):
            block = block.strip()
            if block:
                collected.append((block, heading))

    # 3) 合并过短块
    collected = _merge_short_tail(collected, MIN_CHUNK_SIZE)

    return [
        Chunk(content=content, chunk_index=i, heading_path=heading, meta=dict(merged_meta))
        for i, (content, heading) in enumerate(collected)
    ]


def content_hash(text: str) -> str:
    """文档正文的稳定哈希，用于增量索引时判断"内容有没有变"。"""
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()
