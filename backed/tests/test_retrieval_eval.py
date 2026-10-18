"""检索效果评估：Recall@5 / MRR / NDCG@5。

**这个脚本是阶段 2（知识库建设）的准入门槛**：只有这里的指标达标，
才说明检索链路本身是通的，往里灌语料才有意义。

语料与标注集都**写死在脚本内**（种子语料，非完整知识库）：
- 好处：不依赖网络、可离线复跑、指标可比较；
- 代价：这里的分数**不代表真实语料上的效果**，只用于回答"检索链路能不能工作"。

Embedding 可用时会跑真正的混合检索；不可用时自动退化为纯 BM25 并在开头如实标注
——**两种模式都要求达标**，因为降级可用性是本项目的硬要求。

运行：
    cd backed
    .\\.venv\\Scripts\\python.exe tests\\test_retrieval_eval.py
"""
import asyncio
import math
import os
import pathlib
import sys

_TEST_DB = pathlib.Path(__file__).resolve().parent.parent / "data" / "_test_retrieval.db"
for _suffix in ("", "-wal", "-shm"):
    _p = pathlib.Path(str(_TEST_DB) + _suffix)
    if _p.exists():
        _p.unlink()
os.environ["DB_PATH"] = str(_TEST_DB)

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app.db import database as db  # noqa: E402
from app.rag.embedding import embed_enabled  # noqa: E402
from app.rag.indexer import index_document  # noqa: E402
from app.rag.retriever import hybrid_search  # noqa: E402

RECALL_THRESHOLD = 0.75
MRR_THRESHOLD = 0.60

# ---------------------------------------------------------------- 种子语料
SEED_DOCS = [
    ("杭州/00_overview", "杭州概况", "杭州", "overview", """# 杭州概况

杭州位于浙江省北部，是京杭大运河的南端起点。最佳旅行季节是春季三月到五月
与秋季九月到十一月，此时气温适宜、桂花与荷花各有胜景。夏季闷热多雨，
冬季湿冷。西湖是杭州的核心景区，周边还有灵隐寺、西溪湿地、京杭大运河等。

杭州适合慢节奏游览，一次三日行程可以覆盖西湖核心区与运河沿线。
"""),
    ("杭州/10_transport", "杭州交通", "杭州", "transport", """# 杭州交通

## 机场接驳
萧山国际机场在市区东南约 27 公里。可以坐地铁 19 号线进城，也可以坐机场大巴，
打车约 40 分钟。地铁 19 号线连接杭州东站与萧山机场。

## 高铁
杭州东站是主要高铁站，也有杭州站与杭州西站。从东站坐地铁 1 号线可到市区。

## 地铁
杭州地铁覆盖主要景点。西湖周边有龙翔桥站、定安路站、凤起路站，出站步行即可到湖边。
去灵隐寺可以坐地铁到黄龙洞站再换乘公交。
"""),
    ("杭州/20_areas", "杭州片区与动线", "杭州", "areas", """# 杭州片区与动线

## 西湖核心区
西湖环湖约十五公里，苏堤与白堤是主要步行路线。北山街、南山路串起大多数景点。

## 河坊街与南宋御街
河坊街是历史商业街区，小吃与手工艺品聚集，适合傍晚逛。

## 滨江与钱江新城
钱江新城有城市阳台与灯光秀，适合夜游。滨江沿江绿道适合骑行。

## 动线建议
西湖周边建议按"北山街 → 苏堤 → 南山路"的顺序走，避免来回折返。
"""),
    ("杭州/40_notes", "杭州预约与闭馆", "杭州", "notes", """# 杭州预约与闭馆

## 灵隐寺
灵隐寺需要提前在公众号预约，节假日名额紧张，建议提前三天预约。
飞来峰景区门票与灵隐寺香花券是分开的。

## 浙江省博物馆
浙江省博物馆之江馆周一闭馆，需要提前预约。孤山馆区同样需要预约。

## 西溪湿地
西溪湿地部分区域需要预约，东区与西区入口不同。
"""),
    ("杭州/50_with_elders", "带老人游杭州", "杭州", "with_elders", """# 带老人游杭州

带老人游杭州要放慢节奏，每天安排两到三个点即可，午后留出休息时间。

西湖可以坐观光车或游船代替步行，老人走不动路时优先考虑游船与环湖电瓶车。
灵隐寺台阶较多，腿脚不便可以只到飞来峰景区。

随身携带常用药，夏季避开正午时段，雨天改去博物馆与商场。
"""),
    ("苏州/10_transport", "苏州交通", "苏州", "transport", """# 苏州交通

苏州站与苏州北站是主要车站。苏州北站是高铁站，离市区较远，坐地铁 2 号线进城。

拙政园、苏州博物馆在东北街一带，从苏州站坐地铁 4 号线换乘可以到达。
平江路与观前街相邻，步行即可串联。
"""),
    ("general/budget_guide", "预算构成经验", "None", "budget", """# 预算构成经验

一次旅行的花费通常由交通、住宿、餐饮、门票四部分组成。

住宿一般占总预算的百分之三十到四十，是大头。交通（往返大交通）占百分之二十到三十。
餐饮占百分之十五到二十，门票占百分之十左右。

如果预算明显没花完，通常是因为住宿没有计入，或者门票按估算值计算。
"""),
    ("rules/system_overview", "系统能力边界", "None", "rules", """# 系统能力边界

本系统提供行程规划、地点检索、城际距离估算与知识库问答。

**暂不提供**：酒店预订、机票火车票购买、真实门票价格。这些需要第三方授权，
系统只给出估算值并明确标注。

门票价格为参考估算值，不是真实票价。餐饮费用来自高德的人均消费，属于真实数据。
"""),
]

# ---------------------------------------------------------------- 标注集
# 每条 = (查询, 相关 doc_key 集合)。查询刻意用口语化表达，模拟真实用户。
CASES = [
    ("老人走不动路，杭州怎么安排", {"杭州/50_with_elders"}),
    ("灵隐寺要预约吗", {"杭州/40_notes"}),
    ("西湖边上有哪些地铁站", {"杭州/10_transport"}),
    ("萧山机场怎么到市区", {"杭州/10_transport"}),
    ("杭州什么时候去最好", {"杭州/00_overview"}),
    ("河坊街有什么好逛的", {"杭州/20_areas"}),
    ("浙江省博物馆周一闭馆吗", {"杭州/40_notes"}),
    ("苏州北站到拙政园怎么走", {"苏州/10_transport"}),
    ("预算里住宿一般占多少", {"general/budget_guide"}),
    ("你们能帮我订酒店吗", {"rules/system_overview"}),
    ("杭州三天怎么安排不累", {"杭州/50_with_elders", "杭州/00_overview"}),
    ("门票价格是真实的吗", {"rules/system_overview"}),
    ("西湖周边动线怎么走不折返", {"杭州/20_areas"}),
    ("西溪湿地需要预约吗", {"杭州/40_notes"}),
]


def dedupe_by_doc(keys):
    """把 chunk 级结果压成**文档级**有序列表（同一文档只保留最好名次）。

    标注集是按「文档」标的，而检索返回的是「块」。若不按文档去重，
    同一篇文档的多个块会挤进 top5、被重复计分——NDCG 会算出大于 1 的值
    （数学上不可能），指标随之失去意义。
    """
    seen = set()
    out = []
    for key in keys:
        if key not in seen:
            seen.add(key)
            out.append(key)
    return out


def recall_at_k(retrieved, relevant, k):
    if not relevant:
        return 0.0
    top = retrieved[:k]
    return len(set(top) & relevant) / len(relevant)


def reciprocal_rank(retrieved, relevant):
    for i, key in enumerate(retrieved, start=1):
        if key in relevant:
            return 1.0 / i
    return 0.0


def ndcg_at_k(retrieved, relevant, k):
    dcg = 0.0
    for i, key in enumerate(retrieved[:k], start=1):
        if key in relevant:
            dcg += 1.0 / math.log2(i + 1)
    ideal = sum(1.0 / math.log2(i + 1) for i in range(1, min(len(relevant), k) + 1))
    return dcg / ideal if ideal > 0 else 0.0


async def main() -> int:
    db.init_db()

    # 灌入种子语料（幂等：内容没变会被跳过）
    for doc_key, title, city, category, text in SEED_DOCS:
        await index_document(
            f"seed/{doc_key}", title, text,
            kind="city" if city != "None" else "general",
            city=None if city == "None" else city,
            category=category,
            source="自撰整理（种子语料）",
        )

    from app.rag.indexer import index_stats
    stats = index_stats()
    mode = "混合检索（BM25 + 向量）" if stats["vector_ready"] else "纯 BM25（Embedding 不可用，已降级）"
    print(f"\n语料：{stats['documents']} 篇 / {stats['chunks']} 块"
          f"（向量 {stats['vectors']}）")
    print(f"FTS 与主表一致：{stats['fts_synced']}")
    print(f"检索模式：{mode}")
    print(f"标注集：{len(CASES)} 条\n")
    print(f"{'查询':<26} {'Recall@5':>9} {'RR':>6} {'NDCG@5':>8}  命中")
    print("-" * 78)

    recalls, rrs, ndcgs = [], [], []
    for query, relevant in CASES:
        # 取 10 条再按文档去重，保证去重后仍有 ≥5 篇供 Recall@5 评估
        res = await hybrid_search(query, city=None, top_k=10)
        keys = dedupe_by_doc([r["doc_key"].replace("seed/", "") for r in res["results"]])
        r5 = recall_at_k(keys, relevant, 5)
        rr = reciprocal_rank(keys, relevant)
        nd = ndcg_at_k(keys, relevant, 5)
        recalls.append(r5)
        rrs.append(rr)
        ndcgs.append(nd)
        flag = "✓" if r5 > 0 else "✗"
        hit = keys[0] if keys else "-"
        print(f"{query:<26} {r5:>9.2f} {rr:>6.2f} {nd:>8.2f}  {flag} {hit}")

    n = len(CASES)
    avg_recall = sum(recalls) / n
    avg_mrr = sum(rrs) / n
    avg_ndcg = sum(ndcgs) / n
    print("-" * 78)
    print(f"{'平均':<26} {avg_recall:>9.3f} {avg_mrr:>6.3f} {avg_ndcg:>8.3f}")
    print(f"\n门槛：Recall@5 ≥ {RECALL_THRESHOLD}，MRR ≥ {MRR_THRESHOLD}")

    ok = avg_recall >= RECALL_THRESHOLD and avg_mrr >= MRR_THRESHOLD
    print(f"结果：{'达标 ✓' if ok else '未达标 ✗'}")

    for _suffix in ("", "-wal", "-shm"):
        _p = pathlib.Path(str(_TEST_DB) + _suffix)
        if _p.exists():
            try:
                _p.unlink()
            except OSError:
                pass
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
