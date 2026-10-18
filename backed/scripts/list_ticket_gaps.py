"""导出"无法提供票价"的全部景点清单，供人工核对（一次性分析脚本）。

用法：
    cd backed
    .venv\\Scripts\\python.exe scripts\\list_ticket_gaps.py
输出：
    docs/ticket-gap-list.md
"""
import sys
from collections import Counter, defaultdict
from pathlib import Path

_BACKED = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKED))

from app.db.database import get_conn  # noqa: E402

OUT = _BACKED.parent / "docs" / "ticket-gap-list.md"

# 归类顺序（也是报告里的章节顺序）
GROUPS = [
    ("text_no_price", "文本无价格（「以现场公示为主」这类）"),
    ("no_field", "完全没有门票字段"),
    ("other_place", "只有景区内其他景点的票（同鼓浪屿）"),
    ("concession_only", "只有优待/学生票，无成人票价"),
    ("service_only", "只有景区内付费服务（游船/讲解等，非门票）"),
]


def classify(kind: str, note: str) -> str:
    """只对**无法提供票价**的行归类；能提供票价的返回空串（不参与分组）。"""
    if kind in ("ticket", "free"):
        return ""
    if kind == "service_only":
        return "service_only"
    if "其他景点" in note:
        return "other_place"
    if "优待" in note or "学生" in note:
        return "concession_only"
    if note:
        return "text_no_price"
    return "no_field"


def main() -> int:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT city, name, COALESCE(kind,'unknown') k, "
            "COALESCE(note,'') note, COALESCE(price_text,'') txt, price "
            "FROM poi_prices ORDER BY city, name"
        ).fetchall()

    buckets = defaultdict(list)
    for r in rows:
        group = classify(r["k"], r["note"])
        if group:
            buckets[group].append(dict(r))

    total = len(rows)
    usable = sum(1 for r in rows if r["k"] in ("ticket", "free"))

    lines = [
        "# 无法提供票价的景点清单（供核对）",
        "",
        f"知识库共 **{total}** 条景点，其中 **{usable}** 条能给出票价"
        f"（真实成人票价或明确免费），其余 **{total - usable}** 条无法给出。",
        "",
        "按原因分组如下。请核对：如果某条其实**有公开票价**，或**明确免费**，"
        "告诉我，我可以补进票价表（加进 `data/citydata` 的覆盖或单独维护一份补充表）。",
        "",
    ]

    counts = Counter()
    for key, label in GROUPS:
        items = buckets.get(key, [])
        counts[key] = len(items)
        lines.append(f"## {label} —— {len(items)} 条")
        lines.append("")
        if not items:
            lines.append("（无）")
            lines.append("")
            continue
        lines.append("| # | 城市 | 景点 | 语料门票原文 |")
        lines.append("| --- | --- | --- | --- |")
        for i, r in enumerate(items, start=1):
            txt = (r["txt"] or "").replace("\n", " ").replace("|", "/")[:120]
            lines.append(f"| {i} | {r['city']} | {r['name']} | {txt} |")
        lines.append("")

    lines.insert(6, "| 原因 | 数量 |\n| --- | --- |\n" + "\n".join(
        f"| {label} | {counts[key]} |" for key, label in GROUPS) + f"\n| **合计** | **{total - usable}** |\n")

    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"  知识库 {total} 条，可用 {usable} 条，无法提供票价 {total - usable} 条")
    print(f"  已写入 {OUT}")
    print()
    for key, label in GROUPS:
        print(f"    {label:<38} {counts[key]:>4}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
