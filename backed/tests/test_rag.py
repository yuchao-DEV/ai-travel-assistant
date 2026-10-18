"""RAG 骨架离线单测（阶段 1）。

覆盖：分词 / 切分 / 余弦 / pack-unpack / RRF / FTS5 双索引同步 /
      维度守卫 / 无 Embedding 时的降级。

**全部离线**：不请求任何外部服务。Embedding 相关用例通过 monkeypatch 注入，
既验证了守卫逻辑，又不消耗配额。

运行：
    cd backed
    .\\.venv\\Scripts\\python.exe tests\\test_rag.py
"""
import asyncio
import os
import pathlib
import sys

# 必须**在导入 app.config 之前**改掉库路径：config 在导入时就读取 DB_PATH。
_TEST_DB = pathlib.Path(__file__).resolve().parent.parent / "data" / "_test_rag.db"
for _suffix in ("", "-wal", "-shm"):
    _p = pathlib.Path(str(_TEST_DB) + _suffix)
    if _p.exists():
        _p.unlink()
os.environ["DB_PATH"] = str(_TEST_DB)

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app.config import CHUNK_OVERLAP, MAX_CHUNK_SIZE, MIN_CHUNK_SIZE, RAG_RRF_K  # noqa: E402
from app.db import database as db  # noqa: E402
from app.rag import embedding as emb  # noqa: E402
from app.rag import retriever as ret  # noqa: E402
from app.rag.indexer import index_document, index_stats  # noqa: E402
from app.rag.pipeline import chunk_document, clean_text, parse_meta_header  # noqa: E402
from app.rag.retriever import _rrf_fuse, hybrid_search  # noqa: E402
from app.rag.tokenize import STOPWORDS, escape_fts_query, query_terms, tokenize_zh  # noqa: E402

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


db.init_db()
# 默认把 Embedding 关掉：离线用例不该发网络请求
emb.embed_enabled = lambda: False


def run(coro):
    return asyncio.run(coro)


# ==================================================================
print("\n1. 分词（bigram + unigram）")
# ==================================================================
_tokens = tokenize_zh("杭州西湖").split()
check("含 unigram 杭/州/西/湖",
      all(t in _tokens for t in ("杭", "州", "西", "湖")), str(_tokens))
check("含 bigram 杭州/州西/西湖",
      all(t in _tokens for t in ("杭州", "州西", "西湖")), str(_tokens))
check("不含三字以上切法（避免组合爆炸）",
      all(len(t) <= 2 or t.isascii() for t in _tokens), str(_tokens))
check("结果去重", len(_tokens) == len(set(_tokens)), str(_tokens))

_mixed = tokenize_zh("西湖 West Lake")
check("中英混排：中文保留", "西湖" in _mixed and "西" in _mixed, _mixed)
check("中英混排：英文保留并小写", "west" in _mixed and "lake" in _mixed, _mixed)
check("English 停用词被剔除", "the" not in tokenize_zh("the West Lake").split(),
      tokenize_zh("the West Lake"))
check("中文停用词被剔除", "的" not in tokenize_zh("杭州的西湖").split(),
      tokenize_zh("杭州的西湖"))
check("停用词表非空", len(STOPWORDS) > 50, str(len(STOPWORDS)))
check("纯标点 → 空串", tokenize_zh("，。！？…—") == "", repr(tokenize_zh("，。！？…—")))
check("空输入 → 空串", tokenize_zh("") == "" and tokenize_zh(None) == "")
# 查询**只用 bigram**（单字在语料里太常见，会把 BM25 带偏）
check("query_terms 只返回 bigram",
      query_terms("杭州西湖") == ["杭州", "州西", "西湖"], str(query_terms("杭州西湖")))
check("query_terms 不含任何单字",
      all(len(t) >= 2 for t in query_terms("你们能订酒店吗")), str(query_terms("你们能订酒店吗")))
check("query_terms 无 bigram 时退回单字（单字查询兜底）",
      query_terms("湖") == ["湖"], str(query_terms("湖")))
check("query_terms 空输入", query_terms("") == [])
check("escape 用引号包裹并 OR",
      escape_fts_query(["杭州", "西湖"]) == '"杭州" OR "西湖"',
      escape_fts_query(["杭州", "西湖"]))
check("escape 处理引号注入", escape_fts_query(['a"b']) == '"a""b"',
      escape_fts_query(['a"b']))
check("escape 空表 → 空串", escape_fts_query([]) == "")

# ==================================================================
print("\n2. 文档切分")
# ==================================================================
_text = """<!--
city: 杭州
category: transport
source: 自撰整理
-->
# 杭州交通

## 机场接驳
从萧山机场到市区可以坐地铁 19 号线，也可以打车，约 40 分钟。

## 地铁
杭州地铁覆盖主要景点，西湖周边有龙翔桥站与定安路站。
"""
_meta, _body = parse_meta_header(_text)
check("解析出文档头元数据", _meta.get("city") == "杭州" and _meta.get("category") == "transport",
      str(_meta))
check("元数据块已从正文剥离", "city: 杭州" not in _body, _body[:40])

_chunks = chunk_document(_text, meta={"kind": "city"})
check("切出多个块", len(_chunks) >= 2, str(len(_chunks)))
_paths = [c.heading_path for c in _chunks]
check("heading_path 记录层级路径", any("杭州交通" in p for p in _paths), str(_paths))
check("heading_path 含二级标题", any("机场接驳" in p for p in _paths), str(_paths))
check("块都非空", all(c.content.strip() for c in _chunks))
check("chunk_index 连续从 0 开始",
      [c.chunk_index for c in _chunks] == list(range(len(_chunks))),
      str([c.chunk_index for c in _chunks]))
check("meta 合并进每个块", all(c.meta.get("city") == "杭州" for c in _chunks))
check("clean_text 去掉粗体标记", clean_text("**重点**内容") == "重点内容",
      clean_text("**重点**内容"))
check("clean_text 链接保留文字", clean_text("[官网](https://a.com)") == "官网",
      clean_text("[官网](https://a.com)"))
check("空文档 → 空块列表", chunk_document("") == [] and chunk_document(None) == [])

# 超长段落必须被二次切分，且长度不超上限
_long = "杭州西湖风景优美，适合散步。" * 200
_long_chunks = chunk_document("# 长文\n" + _long)
check("超长段落被切分", len(_long_chunks) > 1, str(len(_long_chunks)))
check(f"每块长度 ≤ MAX_CHUNK_SIZE({MAX_CHUNK_SIZE})",
      all(len(c.content) <= MAX_CHUNK_SIZE for c in _long_chunks),
      str([len(c.content) for c in _long_chunks][:6]))

# 相邻块有重叠
_overlap_hit = False
for _a, _b in zip(_long_chunks, _long_chunks[1:]):
    _tail = _a.content[-CHUNK_OVERLAP:]
    if _tail and _tail in _b.content:
        _overlap_hit = True
        break
check(f"相邻块带 CHUNK_OVERLAP({CHUNK_OVERLAP}) 重叠", _overlap_hit)

# 过短块被并入前一块
_short_tail = chunk_document("# T\n" + ("这是一段足够长的正文内容，用来占位。" * 12) + "\n\n短句。")
check("过短尾块被并入前块（不产生孤立的半句话块）",
      all(len(c.content) >= MIN_CHUNK_SIZE or len(_short_tail) == 1
          for c in _short_tail),
      str([len(c.content) for c in _short_tail]))

# 短小节之间**不得**跨标题合并：否则 heading_path 会张冠李戴
_two_short = """# 预约
## 灵隐寺
灵隐寺需要提前预约。
## 浙江省博物馆
之江馆周一闭馆。
"""
_two_short_chunks = chunk_document(_two_short)
check("两个短小节各自成块（不被跨标题合并）", len(_two_short_chunks) == 2,
      str([(c.heading_path, len(c.content)) for c in _two_short_chunks]))
check("每块的 heading_path 指向自己的小节",
      {c.heading_path for c in _two_short_chunks}
      == {"预约 > 灵隐寺", "预约 > 浙江省博物馆"},
      str([c.heading_path for c in _two_short_chunks]))
check("短小节内容没有互相污染",
      all("博物馆" not in c.content for c in _two_short_chunks if "灵隐寺" in c.heading_path),
      str([c.content for c in _two_short_chunks]))

# 同一小节内的短尾块仍要合并
_same_section = "# T\n" + ("这是一段足够长的正文内容，用来占位凑出长度。" * 14) + "\n尾。"
_same_chunks = chunk_document(_same_section)
check("同一小节内的短尾块会被合并",
      all(len(c.content) >= MIN_CHUNK_SIZE for c in _same_chunks),
      str([len(c.content) for c in _same_chunks]))

# ==================================================================
print("\n3. 向量工具：cosine / pack / unpack")
# ==================================================================
check("余弦：手算对照（误差<1e-6）",
      abs(emb.cosine([1.0, 2.0, 3.0], [4.0, 5.0, 6.0]) - 32 / (14 ** 0.5 * 77 ** 0.5)) < 1e-6,
      str(emb.cosine([1.0, 2.0, 3.0], [4.0, 5.0, 6.0])))
check("余弦：正交向量 → 0", abs(emb.cosine([1.0, 0.0], [0.0, 1.0])) < 1e-9)
check("余弦：自身 → 1.0", abs(emb.cosine([0.3, 0.4, 0.5], [0.3, 0.4, 0.5]) - 1.0) < 1e-9)
check("余弦：零向量 → 0（不抛异常）", emb.cosine([0.0, 0.0], [1.0, 1.0]) == 0.0)
check("余弦：长度不等 → 0（不抛异常）", emb.cosine([1.0], [1.0, 2.0]) == 0.0)
check("余弦：空向量 → 0", emb.cosine([], [1.0]) == 0.0)

_vec = [0.1, -2.5, 3.14159, 0.0, 1e-3]
_round = emb.unpack(emb.pack(_vec))
check("unpack(pack(v)) 长度一致", len(_round) == len(_vec), str(len(_round)))
check("unpack(pack(v)) 数值在 float32 精度内",
      all(abs(a - b) < 1e-6 for a, b in zip(_vec, _round)), str(_round))
check("pack 是 float32 小端（4 字节/元素）", len(emb.pack(_vec)) == 4 * len(_vec))
check("unpack 空 blob → 空列表", emb.unpack(b"") == [])

# ==================================================================
print("\n4. RRF 融合")
# ==================================================================
def _hit(cid, name):
    return {"chunk_id": cid, "doc_key": f"k{cid}", "title": name, "content": name}


# bm25: A B C ；vector: C A
_fused = _rrf_fuse([_hit(1, "A"), _hit(2, "B"), _hit(3, "C")],
                   [_hit(3, "C"), _hit(1, "A")], RAG_RRF_K)
_order = [n.title for n in _fused]
# A = 1/(k+1)+1/(k+2)，C = 1/(k+1)+1/(k+3)，B = 1/(k+2)
check("两路都靠前的排最前面（A 第一）", _order[0] == "A", str(_order))
check("次优为 C（另一路第一 + 本路第三）", _order[1] == "C", str(_order))
check("只被单路命中的排最后（B）", _order[2] == "B", str(_order))
check("融合顺序符合 1/(k+rank) 公式",
      abs(_fused[0].score - (1 / (RAG_RRF_K + 1) + 1 / (RAG_RRF_K + 2))) < 1e-12,
      str(_fused[0].score))
check("记录了两路名次", _fused[0].bm25_rank == 1 and _fused[0].vector_rank == 2,
      f"{_fused[0].bm25_rank}/{_fused[0].vector_rank}")
check("只有 BM25 时也能融合（无向量降级）",
      len(_rrf_fuse([_hit(1, "A"), _hit(2, "B")], None, RAG_RRF_K)) == 2)
check("空输入 → 空结果", _rrf_fuse([], None, RAG_RRF_K) == [])

# ==================================================================
print("\n5. 双索引写入：FTS5 与主表必须始终一致")
# ==================================================================
_doc = """<!--
city: 杭州
category: notes
-->
# 杭州预约与闭馆
## 灵隐寺
灵隐寺需要提前在公众号预约，节假日名额紧张。
## 浙江省博物馆
浙江省博物馆之江馆周一闭馆，需要提前预约。
"""
_result = run(index_document("city:杭州/40_notes", "杭州预约与闭馆", _doc,
                             kind="city", city="杭州", category="notes",
                             source="自撰整理"))
check("索引写入成功", _result["chunks"] > 0, str(_result))
check("无 Embedding 时如实标注未写入向量", _result["vector_ready"] is False, str(_result))

_stats = index_stats()
check("documents 计数正确", _stats["documents"] == 1, str(_stats))
check("doc_chunks 与 doc_chunks_fts 行数相等",
      _stats["fts_synced"] and _stats["fts_rows"] == _stats["chunks"], str(_stats))
check("chunks >= 2（按标题切开了）", _stats["chunks"] >= 2, str(_stats["chunks"]))

# 幂等：同一内容重复索引应跳过
_again = run(index_document("city:杭州/40_notes", "杭州预约与闭馆", _doc,
                            kind="city", city="杭州"))
check("同内容重复索引被跳过（增量索引）", _again["skipped"] is True, str(_again))
check("跳过后块数不变", index_stats()["chunks"] == _stats["chunks"])

# 更新内容 → 块被整体替换，FTS 同步
_doc2 = _doc + "\n## 西湖\n西湖部分区域周末限流。\n"
run(index_document("city:杭州/40_notes", "杭州预约与闭馆", _doc2,
                   kind="city", city="杭州", category="notes"))
_stats2 = index_stats()
check("更新后 FTS 仍与主表一致", _stats2["fts_synced"], str(_stats2))
check("更新后文档数仍为 1（整体替换，不重复）", _stats2["documents"] == 1, str(_stats2))

# 直接对 doc_chunks 增删改，验证三个触发器都生效
with db.transaction() as conn:
    conn.execute(
        """INSERT INTO doc_chunks (document_id, doc_key, chunk_index, heading_path,
                                   content, tokens, char_count, city, kind)
           SELECT id, doc_key, 999, '触发器测试', '触發器测试内容', ?, 6, city, kind
           FROM documents LIMIT 1""",
        (tokenize_zh("触发器测试内容"),),
    )
_c1 = index_stats()
check("INSERT 触发器同步 FTS", _c1["fts_synced"], str(_c1))

with db.transaction() as conn:
    conn.execute("UPDATE doc_chunks SET content = ?, tokens = ? WHERE chunk_index = 999",
                 ("改过的内容", tokenize_zh("改过的内容")))
_c2 = index_stats()
check("UPDATE 触发器同步 FTS", _c2["fts_synced"], str(_c2))
with db.get_conn() as conn:
    _row = conn.execute(
        """SELECT count(*) AS n FROM doc_chunks_fts
           WHERE doc_chunks_fts MATCH 'tokens : ("改过" OR "内容")'"""
    ).fetchone()
check("UPDATE 后新内容可被检索到", int(_row["n"]) >= 1, str(dict(_row)))

with db.transaction() as conn:
    conn.execute("DELETE FROM doc_chunks WHERE chunk_index = 999")
_c3 = index_stats()
check("DELETE 触发器同步 FTS", _c3["fts_synced"], str(_c3))
check("删除后行数回到插入前", _c3["chunks"] == _stats2["chunks"],
      f"{_stats2['chunks']} -> {_c3['chunks']}")

# ==================================================================
print("\n6. 维度守卫")
# ==================================================================
_wrong = [[0.0] * 512]
try:
    emb._guard_dim(_wrong)
    check("维度不符时抛 EmbeddingDimensionError", False, "没有抛异常")
except emb.EmbeddingDimensionError as exc:
    check("维度不符时抛 EmbeddingDimensionError", True)
    check("错误信息里给出处置建议（清空 embeddings 重建）",
          "embeddings" in str(exc), str(exc)[:80])

emb._guard_dim([[0.0] * 1024])  # 正确维度不应抛错
check("维度正确时不抛异常", True)


async def _fake_bad_batch(texts):
    return [[0.0] * 512 for _ in texts]


_orig_enabled, _orig_fetch = emb.embed_enabled, emb._fetch_batch
emb.embed_enabled = lambda: True
emb._fetch_batch = _fake_bad_batch
try:
    run(emb.embed_texts(["维度守卫用例"]))
    check("embed_texts 遇到维度不符会向上抛错（不静默降级）", False, "没有抛异常")
except emb.EmbeddingDimensionError:
    check("embed_texts 遇到维度不符会向上抛错（不静默降级）", True)

# index_document 面对维度错误：不写向量、但仍写全文索引（并打 ERROR 日志）
_before_vectors = index_stats()["vectors"]
_bad = run(index_document("test:维度守卫", "维度守卫", "# 维度\n" + "这段内容用于验证维度守卫。\n",
                          kind="general"))
_after = index_stats()
check("维度错误时 embeddings 表行数不变",
      _after["vectors"] == _before_vectors, f"{_before_vectors} -> {_after['vectors']}")
check("维度错误时不写入向量（vector_ready=False）", _bad["vector_ready"] is False, str(_bad))
check("维度错误时全文索引仍然写入（知识库不至于为空）", _bad["chunks"] > 0, str(_bad))
check("维度错误后 FTS 仍一致", _after["fts_synced"], str(_after))

emb.embed_enabled, emb._fetch_batch = _orig_enabled, _orig_fetch

# ==================================================================
print("\n7. 混合检索与降级")
# ==================================================================
_hit_res = run(hybrid_search("灵隐寺要预约吗", city="杭州", top_k=5))
check("检索有结果", len(_hit_res["results"]) > 0, str(_hit_res)[:200])
check("最相关的是灵隐寺段落",
      "灵隐寺" in _hit_res["results"][0]["content"], str(_hit_res["results"][0])[:80])
check("无 Embedding 时 vector_ready=False", _hit_res["vector_ready"] is False,
      str(_hit_res["vector_ready"]))
check("降级时给出可读说明",
      "关键词检索" in _hit_res["note"], _hit_res["note"])
check("结果带来源字段（doc_key / title）",
      bool(_hit_res["results"][0]["doc_key"]) and bool(_hit_res["results"][0]["title"]))
check("结果含 bm25 名次", _hit_res["results"][0]["bm25_rank"] is not None,
      str(_hit_res["results"][0]))

_city_filter = run(hybrid_search("预约", city="苏州", top_k=5))
check("城市过滤：苏州查不到杭州专有段落",
      all(r["city"] in (None, "苏州") for r in _city_filter["results"]),
      str([(r["city"], r["title"]) for r in _city_filter["results"]]))
check("城市过滤仍保留通用类内容（city 为 NULL）",
      all(r["city"] != "杭州" for r in _city_filter["results"]),
      str([r["city"] for r in _city_filter["results"]]))

check("空查询返回空结果且不抛异常",
      run(hybrid_search("", city="杭州"))["results"] == [])
_miss = run(hybrid_search("完全不存在的词汇组合 zzzqqq", city="杭州"))
check("查不到时返回空列表（不抛异常）", isinstance(_miss["results"], list), str(_miss)[:120])

# BM25 的负分排序方向：最相关的必须排第一
_rank_check = run(hybrid_search("浙江省博物馆 闭馆", city="杭州", top_k=5))
check("BM25 排序方向正确（最相关的排第一）",
      len(_rank_check["results"]) > 0
      and "博物馆" in _rank_check["results"][0]["content"],
      str([r["content"][:24] for r in _rank_check["results"]]))

# ==================================================================
print("\n8. 模拟 Embedding 可用时的融合（不发网络请求）")
# ==================================================================
async def _fake_good_batch(texts):
    """给每段文本一个稳定的假向量：含"灵隐"的维度置 1，其余 0。"""
    out = []
    for t in texts:
        vec = [0.0] * 1024
        vec[0] = 1.0 if "灵隐" in t else 0.0
        vec[1] = 1.0 if "博物馆" in t else 0.0
        out.append(vec)
    return out


emb.embed_enabled = lambda: True
emb._fetch_batch = _fake_good_batch
# 向量缓存改到 cache/ 下（该目录已被 .gitignore 忽略），避免污染真实缓存
# 也避免在 data/ 下留下会被误提交的目录
_TEST_EMBED_CACHE = pathlib.Path(__file__).resolve().parent.parent / "cache" / "_test_embed"
emb._EMBED_CACHE_DIR = _TEST_EMBED_CACHE
_res = run(index_document("test:向量", "向量用例",
                          "# 灵隐寺\n灵隐寺需要提前预约，飞来峰在寺前。\n",
                          kind="general"))
check("Embedding 可用时写入向量", _res["vector_ready"] is True, str(_res))
check("embeddings 表有行", index_stats()["vectors"] > 0, str(index_stats()["vectors"]))
check("向量维度记录正确", index_stats()["vector_dims"] == [1024],
      str(index_stats()["vector_dims"]))

_vec_res = run(hybrid_search("灵隐寺", city=None, top_k=5))
check("有向量时 vector_ready=True", _vec_res["vector_ready"] is True, str(_vec_res)[:160])
check("向量路参与融合（结果带 vector_rank）",
      any(r["vector_rank"] is not None for r in _vec_res["results"]),
      str([r["vector_rank"] for r in _vec_res["results"]]))
check("有向量时不显示降级提示", _vec_res["note"] == "", _vec_res["note"])

emb.embed_enabled, emb._fetch_batch = _orig_enabled, _orig_fetch

# ==================================================================
print(f"\n{'=' * 60}")
print(f"通过 {PASSED} 项，失败 {FAILED} 项")
print(f"{'=' * 60}")

# 清理测试库与测试用向量缓存
import shutil  # noqa: E402

for _suffix in ("", "-wal", "-shm"):
    _p = pathlib.Path(str(_TEST_DB) + _suffix)
    if _p.exists():
        try:
            _p.unlink()
        except OSError:
            pass
shutil.rmtree(_TEST_EMBED_CACHE, ignore_errors=True)

sys.exit(1 if FAILED else 0)
