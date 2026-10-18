"""把 ``data/citydata/*.csv`` 导入知识库（L1 POI 文本层）。

.. note::

   **原始 CSV 刻意不随仓库分发。** 这批数据抓取自去哪儿网（travel.qunar.com），
   版权归原站，仓库只提供本转换脚本。需要知识库内容时：

       把 CSV 放到 <仓库根>/data/citydata/   （一城一文件，如 杭州.csv）
       然后运行本脚本

   顺带一提，这恰好也是本项目「不编造数据」纪律的延伸：数据可以来自第三方，
   但来源必须可追溯、可替换、且不擅自再分发。

数据画像（352 城 / 33,174 条，实测）：介绍 16,750 条（平均 282 字），
开放时间 37%、门票 66%、建议游玩时间 41%。这批**长文本**正好补上高德 API
的缺口——高德给结构化事实（经纬度/评分/营业时间），没有可检索的叙述文本。

**四项清洗**（都是实测出来的问题，不清洗会直接污染检索质量）：

1. **介绍去重**：抓取时把「预览截断版 + 完整版」拼在了一起，13.8% 的记录内部
   有重复。不处理会产生重复 chunk，让 Recall 指标虚高。
2. **地址拆块**：65% 的地址字段其实是多行复合块，含「地址/电话/官网」三类信息。
3. **名字拆分**：98% 的名字后面直接跟英文名（``"芹川村Qinchuan Village"``），
   没有分隔符，展示与精确匹配都会失效。
4. **截断痕迹清理**：15% 的介绍带 ``...`` / ``…`` 截断标记。

用法::

    cd backed
    .venv\\Scripts\\python.exe scripts\\import_citydata.py                # 项目支持的 12 城
    .venv\\Scripts\\python.exe scripts\\import_citydata.py --all          # 全部 352 城
    .venv\\Scripts\\python.exe scripts\\import_citydata.py --city 杭州 苏州
    .venv\\Scripts\\python.exe scripts\\import_citydata.py --dry-run      # 只看清洗效果，不写库
"""
import argparse
import asyncio
import ast
import csv
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_BACKED = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKED))

from app.config import BASE_DIR  # noqa: E402
from app.db.database import init_db  # noqa: E402
from app.rag.indexer import index_document, index_stats  # noqa: E402
from app.services.poi_prices import (  # noqa: E402
    extract_ticket,
    price_stats,
    replace_city_prices,
)

# CSV 默认放在**仓库根**的 data/citydata/（不在 backed/ 下）
DEFAULT_CSV_DIR = BASE_DIR.parent / "data" / "citydata"

# 复合字段里的标签行，如 "地址:" / "电话:" / "官网:"
_LABEL_RE = re.compile(r"^([\u4e00-\u9fffA-Za-z]{1,6})\s*[:：]\s*$")
# 名字尾部的英文名（前面必须是中文字或右括号，保证切在合理位置）
_NAME_SPLIT_RE = re.compile(r"^(.*[\u4e00-\u9fff）)】」])([A-Za-z][A-Za-z0-9 ,.'\-&()]*)$")
# 截断痕迹
_ELLIPSIS_RE = re.compile(r"[.。]{3,}|…+")


# ---------------------------------------------------------------- 清洗

def split_name(raw: str) -> Tuple[str, str]:
    """把 ``"芹川村Qinchuan Village"`` 拆成 ``("芹川村", "Qinchuan Village")``。

    98% 的名字都带英文尾缀且无分隔符，不拆会影响展示（列表里出现中英连写）
    与精确匹配（用户输入中文名匹配不上）。
    """
    raw = (raw or "").strip()
    match = _NAME_SPLIT_RE.match(raw)
    if match:
        return match.group(1).strip(), match.group(2).strip()
    return raw, ""


def split_compound_field(raw: str) -> Dict[str, str]:
    """把 ``"地址:\\nxxx\\n\\n电话:\\nyyy"`` 这样的复合块拆成 ``{标签: 值}``。

    实测 65% 的地址字段是多行复合块，42.5% 里面含电话。不拆的话"电话"
    会被当成地址的一部分拼进正文，检索"电话"时会命中一堆无意义的片段。
    """
    if not raw:
        return {}
    out: Dict[str, str] = {}
    current: Optional[str] = None
    for line in raw.replace("\r\n", "\n").split("\n"):
        line = line.strip()
        if not line:
            continue
        label = _LABEL_RE.match(line)
        if label:
            current = label.group(1)
            out.setdefault(current, "")
            continue
        key = current or "地址"
        out[key] = (out.get(key, "") + " " + line).strip()
    return out


def dedupe_intro(text: str) -> str:
    """去掉「预览截断版 + 完整版」拼接造成的重复。

    抓取产物形如 ``<被截断的开头>...<完整正文（开头与前面重复）>``。
    做法：拿开头 24 字当指纹，若它出现第二次，就丢掉第二次之前的所有内容
    ——留下的是完整版。指纹取 24 字是为了避免短句巧合命中。
    """
    text = (text or "").strip()
    if len(text) < 80:
        return _ELLIPSIS_RE.sub("", text).strip()
    head = text[:24]
    first = text.find(head)
    second = text.find(head, first + 1)
    if second > 0:
        text = text[second:]
    # 清理残留的截断标记与重复标点
    text = _ELLIPSIS_RE.sub("", text)
    text = re.sub(r"[，,]\s*[，,]", "，", text)
    return text.strip()


# 票种关键词：只从真实的票名里提取，不凭空假设
_TICKET_KINDS = ("成人票", "儿童票", "优待票", "学生票", "老人票",
                 "亲子票", "家庭票", "套票", "联票", "年票")
_PRICE_RE = re.compile(r"[¥￥]\s*([0-9]+(?:\.[0-9]+)?)")


def _flatten_ticket(obj, row_name: str = "", limit: int = 3) -> str:
    """把票种字典摊平成一句人话。

    抓取时 ``门票`` 字段有 16.1% 是 Python 字典字面量，形如
    ``{'三亚西岛门票+往返船票成人票': ['¥90起'], ...}``——OTA 的商品 SKU，
    里面混着成人票、优待票、讲解团、酒店套餐。

    **不能简单报"最低价"**：那通常取到的是优待票价或讲解服务价。实测这样会把
    免费的国博写成 ¥108、把拙政园 ¥70 的门票写成 ¥35。

    所以这里分两种表述：
    - 能定位到**标准成人票价**时，报出价格区间；
    - 否则只说明"有多种票/以购票页为准"，不编造一个数字。
    """
    pairs: List[Tuple[str, str]] = []
    if isinstance(obj, dict):
        for key, value in obj.items():
            name = " ".join(str(key).split())
            price = ("、".join(str(x) for x in value)
                     if isinstance(value, (list, tuple)) else str(value))
            pairs.append((name, price.strip()))
    elif isinstance(obj, (list, tuple)):
        pairs = [("", "、".join(str(x) for x in obj))]
    if not pairs:
        return ""

    prices: List[float] = []
    for _, price in pairs:
        prices.extend(float(m.group(1)) for m in _PRICE_RE.finditer(price))

    if not prices:
        return "；".join(f"{n} {p}".strip() for n, p in pairs[:limit])

    # 先用与规划路径同一套从严规则判断"有没有可信的标准成人票价"
    trusted, is_free, kind, _note = extract_ticket(
        json.dumps(obj, ensure_ascii=False), row_name)
    if is_free:
        return "免费"
    if kind == "service_only":
        return "该景点门票免费或需现场购买；语料中的付费项目为游船/讲解等服务，非门票费用"
    if trusted is None:
        return (f"门票需按票种购买（共 {len(pairs)} 种，含成人票/优待票/"
                f"讲解服务等，价格差异较大），以购票页与实际门市价为准")

    kinds = []
    for name, _ in pairs:
        for kind in _TICKET_KINDS:
            if kind in name and kind not in kinds:
                kinds.append(kind)
    kind_hint = f"，含{'/'.join(kinds[:4])}等" if kinds else ""
    return (f"成人票价 ¥{trusted:g} 起{kind_hint}"
            f"（票价来自第三方，以购票页为准）")


def normalize_ticket(raw: str, row_name: str = "") -> str:
    """门票字段归一：字面量 → 人话；普通文本 → 原样（只做空白整理）。"""
    raw = (raw or "").strip()
    if not raw:
        return ""
    if raw[0] in "{[":
        try:
            obj = ast.literal_eval(raw)
        except (ValueError, SyntaxError):
            return " ".join(raw.split())
        return _flatten_ticket(obj, row_name)
    return " ".join(raw.split())


def normalize_season(raw: str, limit: int = 200) -> str:
    """建议季节：少数记录把整段气候描述塞了进来（70 条超 200 字），截断避免喧宾夺主。"""
    text = " ".join((raw or "").split())
    if len(text) <= limit:
        return text
    return text[:limit].rstrip("，,。；; ") + "…"


# ---------------------------------------------------------------- 转文档

def _colloquial_tags(rec: Dict[str, str], tips: str) -> List[str]:
    """从结构化字段里提炼大白话标签。

    这是 RAG 效果最容易被忽略、收益却最直接的一步：用户的真实问法是
    「周一能去吗」「要门票吗」「要预约吗」，而这些词往往不在原始字段里。
    显式写出这些句子，语义检索与 BM25 才能命中。
    """
    tags: List[str] = []
    hours = rec.get("开放时间") or ""
    ticket = rec.get("门票") or ""

    if "周一" in hours and re.search(r"周[一1].{0,8}(闭|不开放|休息)", hours):
        tags.append("周一不开放，安排行程时要避开周一")
    if re.search(r"(免费|不需门票|免票)", ticket):
        tags.append("不需要门票，可以免费参观")
    if "预约" in tips or "预约" in hours:
        tags.append("需要提前预约")
    if re.search(r"(身份证|证件)", ticket + tips):
        tags.append("需要携带身份证等证件")
    if re.search(r"(建议|适合).{0,6}(上午|清晨)", tips + hours):
        tags.append("适合上午前往")
    return tags


def poi_to_text(rec: Dict[str, str], city: str) -> str:
    """把一条 CSV 记录写成一段**自然语言**描述。

    刻意写成完整句子而不是 ``字段名: 值`` 的堆砌——向量模型对结构文本的
    语义理解很差（见落地清单 2.4 节）。
    """
    zh, en = split_name(rec.get("名字", ""))
    extra = split_compound_field(rec.get("地址", ""))
    address = extra.get("地址", "")
    phone = extra.get("电话", "")
    website = extra.get("官网", "")
    intro = dedupe_intro(rec.get("介绍", ""))
    tips = (rec.get("小贴士") or "").strip()
    rating = (rec.get("评分") or "").strip()
    ticket = normalize_ticket(rec.get("门票", ""), zh)
    hours = (rec.get("开放时间") or "").strip()
    duration = (rec.get("建议游玩时间") or "").strip()
    season = normalize_season(rec.get("建议季节", ""))

    parts: List[str] = []
    parts.append(f"{zh}（{en}）" if en else zh)
    loc = f"，位于{city}"
    if address:
        loc += f"，地址是{address}"
    parts.append(loc + "。")

    if rating:
        parts.append(f"游客评分 {rating} 分。")
    if ticket:
        # 摊平后的票价文本自带免责说明，避免重复叠加
        if "第三方" in ticket:
            parts.append(f"门票：{ticket}。")
        else:
            parts.append(f"门票：{ticket}（门票信息来自第三方，可能变动，出行前请再次确认）。")
    if hours and "咨询" not in hours:
        parts.append(f"开放时间：{hours}。")
    if duration:
        parts.append(f"{duration.rstrip('。')}。")
    if season:
        parts.append(f"适合季节：{season}。")
    if phone:
        parts.append(f"咨询电话：{phone}。")
    if website:
        parts.append(f"官网：{website}。")

    if intro:
        parts.append(intro)

    if tips:
        # 小贴士是最高价值的实操信息（预约规则、单向路线、闭馆日），单独成段
        parts.append(f"实用小贴士：{tips}")

    tags = _colloquial_tags(rec, tips + hours)
    if tags:
        parts.append("；".join(tags) + "。")

    text = "".join(parts)
    # 收尾清理：源数据里常见 "；。"、"。。"、多余空格
    text = re.sub(r"[；;]\s*。", "。", text)
    text = re.sub(r"。{2,}", "。", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text.strip()


# ---------------------------------------------------------------- 读取

def load_city(csv_dir: Path, city: str) -> List[Dict[str, str]]:
    """读入一个城市的 CSV。"""
    path = csv_dir / f"{city}.csv"
    if not path.exists():
        return []
    with open(path, encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def dedupe_records(rows: List[Dict[str, str]]) -> List[Dict[str, str]]:
    """同城内按中文名去重，保留评分更高的一条。"""
    best: Dict[str, Dict[str, str]] = {}
    for row in rows:
        zh, _ = split_name(row.get("名字", ""))
        if not zh:
            continue
        try:
            score = float(row.get("评分") or 0)
        except ValueError:
            score = 0.0
        prev = best.get(zh)
        if prev is None:
            best[zh] = row
        else:
            try:
                prev_score = float(prev.get("评分") or 0)
            except ValueError:
                prev_score = 0.0
            if score > prev_score:
                best[zh] = row
    return list(best.values())


def supported_cities() -> List[str]:
    """项目 cities.json 里支持的城市（默认导入范围）。"""
    path = _BACKED / "data" / "cities.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return [c["name"] for c in data["cities"]]
    except Exception:
        return []


# ---------------------------------------------------------------- 主流程

async def run(args: argparse.Namespace) -> int:
    csv_dir = Path(args.csv_dir).resolve()
    if not csv_dir.exists():
        print(f"找不到语料目录：{csv_dir}")
        print("请把 citydata 的 CSV 放到该目录（一城一文件，如 杭州.csv）后重试。")
        print("注意：原始 CSV 刻意不随仓库分发（版权归原站），详见本文件头部说明。")
        return 2

    if args.city:
        cities = args.city
    elif args.all:
        cities = sorted(p.stem for p in csv_dir.glob("*.csv"))
    else:
        cities = supported_cities() or sorted(p.stem for p in csv_dir.glob("*.csv"))

    if args.limit_cities:
        cities = cities[:args.limit_cities]

    init_db()
    print(f"语料目录：{csv_dir}")
    print(f"待处理城市：{len(cities)} 个")
    if args.dry_run:
        print("模式：DRY-RUN（只清洗并打印样例，不写库）\n")
    else:
        print()

    total_records = 0
    total_docs = 0
    price_rows: List[Dict[str, Any]] = []
    for i, city in enumerate(cities, start=1):
        rows = load_city(csv_dir, city)
        if not rows:
            continue
        records = dedupe_records(rows)
        total_records += len(records)

        if args.dry_run:
            if i <= 2:
                print(f"=== {city}（原始 {len(rows)} 条 → 去重后 {len(records)} 条）===")
                for rec in records[:2]:
                    text = poi_to_text(rec, city)
                    print(f"\n--- doc_key=citydata/{city}/{split_name(rec['名字'])[0]} ---")
                    print(f"长度 {len(text)} 字")
                    print(text[:520] + ("…" if len(text) > 520 else ""))
                print()
            continue

        for rec in records:
            zh, _ = split_name(rec.get("名字", ""))
            if not zh:
                continue
            text = poi_to_text(rec, city)
            if len(text) < 30:
                continue
            result = await index_document(
                f"citydata/{city}/{zh}",
                f"{city}·{zh}",
                text,
                kind="poi",
                city=city,
                category="poi",
                source="去哪儿网（travel.qunar.com）",
                source_url=rec.get("链接") or None,
                with_vectors=not args.no_vectors,
            )
            if not result["skipped"]:
                total_docs += 1

            # 结构化票价：单独落一张表供**规划**路径查用。
            # 只认「单人/成人票价」；只有游船/讲解等服务项目时记为
            # service_only，前端会说明"非门票费用"，绝不当成门票钱。
            price, is_free, kind, note = extract_ticket(rec.get("门票", ""), zh)
            price_rows.append({
                "name": zh,
                "price": price,
                "is_free": is_free,
                "kind": kind,
                "note": note,
                "price_text": (rec.get("门票") or "").strip(),
                "source": "去哪儿网（travel.qunar.com）",
            })

        if price_rows:
            replace_city_prices(city, price_rows)
            price_rows.clear()

        if i % 20 == 0 or i == len(cities):
            print(f"  进度 {i}/{len(cities)}  已写入 {total_docs} 篇")

    if args.dry_run:
        print(f"DRY-RUN 完成：{len(cities)} 城 / {total_records} 条记录（未写库）")
        return 0

    stats = index_stats()
    print(f"\n导入完成：{len(cities)} 城 / {total_records} 条记录 / 本次写入 {total_docs} 篇")
    print(f"知识库现状：documents={stats['documents']}  chunks={stats['chunks']}  "
          f"vectors={stats['vectors']}")
    print(f"FTS 与主表一致：{stats['fts_synced']}")
    ps = price_stats()
    if ps["total"]:
        print(f"结构化票价：{ps['total']} 条，其中可用 {ps['known']} 条"
              f"（含价格 {ps['with_price']}，明确免费 {ps['free']}）"
              f"，覆盖 {len(ps['cities'])} 城")
        print("  说明：规划时只对**高置信度名称匹配**的景点套用真实票价；"
              "匹配不上的仍按类型预留，并在前端标注为「门票预留」而非「票价」。")
    if not stats['vector_ready']:
        reason = stats.get('embed_circuit')
        if reason:
            print(f"提示：未写入向量——Embedding 已熔断（{reason}）。"
                  f"当前 LLM 服务不提供 embedding 接口属正常情况；"
                  f"把 EMBED_BASE_URL / EMBED_API_KEY 指向提供 embedding 的服务后重建即可。")
        else:
            print("提示：未写入向量（Embedding 未配置），检索走纯 BM25。"
                  "配置 EMBED_BASE_URL / EMBED_API_KEY 后重建即可启用向量检索。")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="把 data/citydata/*.csv 导入 RAG 知识库",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--csv-dir", default=str(DEFAULT_CSV_DIR),
                        help=f"CSV 目录（默认 {DEFAULT_CSV_DIR}）")
    parser.add_argument("--city", nargs="+", help="只处理指定城市")
    parser.add_argument("--all", action="store_true", help="处理目录下全部城市（352 城）")
    parser.add_argument("--limit-cities", type=int, help="只处理前 N 个城市（调试用）")
    parser.add_argument("--no-vectors", action="store_true", help="跳过向量化")
    parser.add_argument("--dry-run", action="store_true", help="只清洗并打印样例，不写库")
    return asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    sys.exit(main())
