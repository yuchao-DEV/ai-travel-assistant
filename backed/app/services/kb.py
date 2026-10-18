"""知识库管理：上传文档、重建索引、统计。

与 :mod:`app.rag.*`（检索骨架）的分工：那边只关心"怎么切、怎么查"，这边关心
"语料从哪来、什么时候重建"。上层路由（``/api/kb/*``）只依赖本模块。

三类语料来源：

1. **随代码同步的语料**（``backed/knowledge/``）——领域规则、通用经验、城市攻略，
   由 :mod:`app.rag.bootstrap` 在启动时增量索引；
2. **导入的城市 POI 语料**（``backed/scripts/import_citydata.py``）——每条 POI
   一篇文档，含介绍与小贴士；
3. **运行时上传的文档**（``upload_document``）——便于后期补充，不必改代码。
"""
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.config import KNOWLEDGE_DIR
from app.rag.indexer import index_document, index_stats, list_documents
from app.rag.pipeline import parse_meta_header

logger = logging.getLogger("app.kb")

# kind 取值（与 config 的说明保持一致）
KIND_RULE = "rule"
KIND_GENERAL = "general"
KIND_CITY = "city"
KIND_POI = "poi"
_VALID_KINDS = (KIND_RULE, KIND_GENERAL, KIND_CITY, KIND_POI)


def _doc_key_from_path(path: Path) -> str:
    """从文件路径推出稳定的 doc_key。

    形如 ``knowledge/cities/杭州/10_transport.md`` → ``kb/杭州/10_transport``，
    ``knowledge/rules/system_overview.md`` → ``kb/rules/system_overview``。
    稳定很重要：内容没变时靠它 + content_hash 跳过重建。
    """
    try:
        rel = path.relative_to(KNOWLEDGE_DIR)
    except ValueError:
        rel = Path(path.name)
    parts = list(rel.with_suffix("").parts)
    return "kb/" + "/".join(parts)


def _read_meta(text: str) -> Dict[str, str]:
    """读取文件头部的 ``<!-- key: value -->`` 元数据块。"""
    meta, _ = parse_meta_header(text)
    return meta


def knowledge_files() -> List[Path]:
    """``knowledge/`` 目录下全部 Markdown 文件（不存在时返回空）。"""
    if not KNOWLEDGE_DIR.exists():
        return []
    return sorted(KNOWLEDGE_DIR.rglob("*.md"))


def list_knowledge_files() -> List[Dict[str, Any]]:
    """列出语料文件及其元数据（供 ``/api/kb/files`` 与验收）。"""
    out: List[Dict[str, Any]] = []
    for path in knowledge_files():
        try:
            text = path.read_text(encoding="utf-8")
        except Exception as exc:
            logger.warning("语料读取失败 %s：%s", path, exc)
            continue
        meta = _read_meta(text)
        out.append({
            "doc_key": _doc_key_from_path(path),
            "file": str(path.relative_to(KNOWLEDGE_DIR)),
            "kind": meta.get("kind") or KIND_GENERAL,
            "city": meta.get("city") or None,
            "category": meta.get("category") or None,
            "source": meta.get("source") or None,
            "chars": len(text),
        })
    return out


async def index_knowledge_file(path: Path, *, with_vectors: bool = True) -> Dict[str, Any]:
    """索引单个语料文件（幂等：内容未变会被跳过）。"""
    text = path.read_text(encoding="utf-8")
    meta = _read_meta(text)
    # 标题取正文里第一个一级标题，没有就用文件名
    title = None
    for line in text.splitlines():
        if line.startswith("# "):
            title = line[2:].strip()
            break
    kind = meta.get("kind") or KIND_GENERAL
    if kind not in _VALID_KINDS:
        kind = KIND_GENERAL
    return await index_document(
        _doc_key_from_path(path),
        title or path.stem,
        text,
        kind=kind,
        city=meta.get("city") or None,
        category=meta.get("category") or None,
        source=meta.get("source") or "自撰整理",
        source_url=meta.get("source_url") or None,
        doc_date=meta.get("doc_date") or None,
        with_vectors=with_vectors,
    )


async def index_knowledge_dir(*, with_vectors: bool = True,
                              only_city: Optional[str] = None) -> Dict[str, Any]:
    """索引整个 ``knowledge/`` 目录，返回统计。

    ``only_city`` 只索引该城市的语料（外加全部 rules / general）。
    """
    files = knowledge_files()
    stat = {"files": 0, "indexed": 0, "skipped": 0, "chunks": 0}
    for path in files:
        meta = _read_meta(path.read_text(encoding="utf-8"))
        if only_city and meta.get("city") and meta["city"] != only_city:
            continue
        stat["files"] += 1
        result = await index_knowledge_file(path, with_vectors=with_vectors)
        if result["skipped"]:
            stat["skipped"] += 1
        else:
            stat["indexed"] += 1
            stat["chunks"] += result["chunks"]
    logger.info("知识库索引：文件 %d，新增/更新 %d，跳过 %d，块 %d",
                stat["files"], stat["indexed"], stat["skipped"], stat["chunks"])
    return stat


async def upload_document(
    title: str,
    text: str,
    *,
    city: Optional[str] = None,
    kind: str = KIND_GENERAL,
    category: Optional[str] = None,
    source: str = "用户上传",
) -> Dict[str, Any]:
    """上传一篇文档并索引（运行时补充语料用，不必改代码）。"""
    if kind not in _VALID_KINDS:
        kind = KIND_GENERAL
    slug = "".join(ch for ch in title if ch.isalnum() or "\u4e00" <= ch <= "\u9fff")[:40]
    doc_key = f"kb/upload/{city + '/' if city else ''}{slug}"
    return await index_document(
        doc_key, title, text,
        kind=kind, city=city, category=category, source=source,
    )


async def reindex(city: Optional[str] = None, *, with_vectors: bool = True) -> Dict[str, Any]:
    """重建语料索引。``city`` 为空时重建全部（含 rules / general）。"""
    return await index_knowledge_dir(with_vectors=with_vectors, only_city=city)


def stats() -> Dict[str, Any]:
    """知识库总览：文档/块/向量 + 语料文件盘点。"""
    base = index_stats()
    files = list_knowledge_files()
    by_kind: Dict[str, int] = {}
    for item in files:
        by_kind[item["kind"]] = by_kind.get(item["kind"], 0) + 1
    base["knowledge_dir"] = str(KNOWLEDGE_DIR)
    base["knowledge_files"] = len(files)
    base["knowledge_by_kind"] = by_kind
    base["documents_preview"] = list_documents(5)
    return base
