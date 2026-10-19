"""结构化票价：从语料文本里抽价格，按名称从严匹配回高德 POI。

**为什么要从严**：高德叫「杭州西湖风景名胜区-花港观鱼」，语料叫「西湖」。
匹配规则一放宽，就会把甲景点的票价贴到乙景点上——**标错价格比不标价格更糟**，
因为用户会拿着一个看起来确定、实际错误的数字去做决策。
所以这里只接受**高置信度匹配**（归一化后完全相等，或语料名以 ≥4 字的长度
完整出现在高德名里），宁可匹配不上、走"票价未知"。

三档费用来源（``cost_source``）：

- ``real``       高德直接返回的真实人均消费（餐饮）
- ``third_party`` 语料里的真实票价（本文档负责）
- ``None``       没有任何数据，不写 cost（前端显示「门票待确认」）
                 —— 展示时必须说清是"预留"而不是"票价"
"""
import logging
import ast
import re
from typing import Any, Dict, List, Optional, Tuple

from app.db.database import get_conn, utc_now_iso

logger = logging.getLogger("app.poi_prices")

_PRICE_RE = re.compile(r"[¥￥]\s*([0-9]+(?:\.[0-9]+)?)")
# 明确写"免费 / 免票 / 无需门票"
#
# 刻意**不把"凭身份证进入"算作免费**：它在博物馆语境下通常确实意味着免费，
# 但也可能是"凭证件购票"。判断错误的代价是双面的——标成免费会让预算少算，
# 标成未知只是保守一点。不确定的一律归为未知。
_FREE_RE = re.compile(r"(免费|免票|不需.{0,4}门票|无需.{0,4}门票|不收费|不收门票)")

# 景区**内部**的付费服务（游船/索道/讲解…）：这类按用户规则取均价
_ATTRACTION_SERVICE_WORDS = (
    "讲解", "导游", "游船", "船票", "摇橹船", "竹筏", "观光隧道", "索道",
    "缆车", "观光车", "摆渡", "接驳", "观光票", "演出", "表演", "旅拍", "摄影",
)
# **旅游打包产品**（一日游/酒店/跟团…）：不是景区里的服务，绝不能混进均价。
# 实测把「杭州博物馆预约票 ¥30」和「【酒店套餐】¥699」一起平均，得出 ¥364.5
# ——而杭州博物馆其实是免费的。
_PACKAGE_WORDS = (
    "一日游", "半日游", "多日游", "纯玩", "跟团", "酒店", "住宿", "包车",
    "接送", "保险", "套餐", "团购", "代订", "研学", "定制", "私人", "快速通道",
    "免排队", "直通车",
)
# 兼容旧名：既不是门票、也不是景区内服务的词，统一按"打包产品"排除
_SERVICE_WORDS = _ATTRACTION_SERVICE_WORDS + _PACKAGE_WORDS
# 只认这几类**单人/成人**票价（老人票、儿童票、学生票、优待票都不是标准票价）
_SKU_STANDARD = ("成人票", "单人票", "全价票", "标准票")

# 票价类别
KIND_TICKET = "ticket"              # 真·成人门票价（可直接当门票用）
KIND_FREE = "free"                  # 明确免费
KIND_CONCESSION_AVG = "concession_avg"  # 只有优待/学生票 → 取这些票种的均价
KIND_SERVICE_AVG = "service_avg"    # 只有景区内付费服务 → 取服务项目均价
KIND_UNKNOWN = "unknown"            # 数据不足：不显示价格，只给备注

# 需要"取均价"的类别（用户规则）
AVG_KINDS = (KIND_CONCESSION_AVG, KIND_SERVICE_AVG)
# 完全不显示价格的类别：只给备注
NO_PRICE_KINDS = (KIND_UNKNOWN,)
# 归一化时只剥**景区类**后缀。
#
# 关键教训：早先把「博物馆/美术馆/图书馆/科技馆/公园/广场」也一并剥掉，
# 结果不同场所塌缩成同一个键——实测把「上海美术馆(中华艺术宫)」匹配到了
# 「上海科技馆」（两者剥完都是"上海"，¥88）、把「厦门市图书馆」匹配到了
# 「厦门市博物馆」（都是"厦门市"）、把「人民公园」匹配到了「人民广场」。
#
# 区分原则：
# - 「X风景区 / X景区 / X旅游区」通常就是同一个地方的不同叫法 → 该剥；
# - 「X博物馆 / X图书馆 / X公园 / X广场」是**不同场所** → 绝不能剥。
_STRIP_WORDS = (
    "风景名胜区", "风景区", "景区", "旅游区", "度假区",
)
# 仅用于"次级候选"的通用词表：剥掉后再比对，但要额外满足长度与唯一性约束
_STRIP_SECONDARY = ("公园", "古镇", "老街", "步行街")
# 名称里的分隔符（高德常用 "-"、"·"、"()" 组合长名）
_SPLIT_RE = re.compile(r"[-—–·・|/、,，()（）\[\]【】\s]+")
# 归一化时保留的字符
_KEEP_RE = re.compile(r"[^\u4e00-\u9fffA-Za-z0-9]")


def extract_ticket(raw: str, row_name: str = "") -> Tuple[Optional[float], bool, str, str]:
    """把语料的门票文本抽成 ``(价格, 是否免费, 类别, 备注)``。

    类别取值：``ticket``（真门票）/ ``free``（明确免费）/
    ``service_only``（只有景区内付费服务，**不是门票钱**）/ ``unknown``。

    **这个字段是 OTA 商品 SKU 列表，不是门市价。** 实测原文形如：

        {'拙政园门票成人票': ['¥70起'], '拙政园门票老人票': ['¥35起']}
        {'丽江古城': …实际是 '木府门票老人票' / '万古楼门票成人票'}

    三条规则（都来自实测踩过的坑）：

    1. **只取单人/成人票价**。早先取"最低价"会拿到老人票价——拙政园 ¥70 被
       写成 ¥35、留园 ¥45 被写成 ¥22.5。
    2. **SKU 名必须提到这一行自己的地名**。丽江古城那一行的票其实是木府/万古楼的，
       如实报出来等于把别处的票价贴到这个景点上。
    3. **只有服务项目时，明确标注"不是门票钱"**。免费的什刹海被匹配到 ¥150、
       王府井步行街被匹配到 ¥49——那些是游船、讲解等服务费。按你的要求，
       这种情况不报成票价，而是记为"景区内付费服务（非门票）"。
    """
    text = " ".join((raw or "").split())
    if not text:
        return None, False, KIND_UNKNOWN, ""

    # ① 明确免费（且不夹带服务项目价格）
    if _FREE_RE.search(text) and not any(w in text for w in _SERVICE_WORDS):
        return 0.0, True, KIND_FREE, "门票免费"

    # ② SKU 字典（抓取时 dump 出来的 Python 字面量）
    if text[0] in "{[":
        try:
            obj = ast.literal_eval(text)
        except (ValueError, SyntaxError):
            obj = None
        if isinstance(obj, dict):
            return _from_skus(obj, text, row_name)

    # ③ 纯文本：含「门票」、含价格、且不含服务词，才算门票价；否则按规则归类
    prices = [float(m.group(1)) for m in _PRICE_RE.finditer(text)]
    if prices:
        if any(w in text for w in _SERVICE_WORDS) or "门票" not in text:
            avg = sum(prices) / len(prices)
            return (round(avg, 1), False, KIND_SERVICE_AVG,
                    f"该景点门票免费或需现场购买；¥{avg:.1f} 是景区内付费服务的均价，"
                    f"**不是门票费用**")
        return min(prices), False, KIND_TICKET, ""
    # 没有可解析价格 → **不给价格**，把原文当备注（用户规则）
    return None, False, KIND_UNKNOWN, text.strip()


def _from_skus(obj: Dict[Any, Any], text: str, row_name: str) -> Tuple[Optional[float], bool, str, str]:
    """从 SKU 字典里挑出票价，并按**用户规则**分四类处理。

    一条 SKU 要算"真门票"，必须同时满足：含「门票」二字、含单人/成人票种、
    不含讲解/套餐等服务词、且以本行地名开头。判据的来历见各处注释。

    挑不出真门票时，按用户定的规则分两类处理：

    - **只有优待/学生票** → 取这些票种的**均价**（成人票价通常更高，备注里说明）；
    - **只有景区内付费服务**（游船/讲解/观光票等）→ 取服务项目的**均价**，
      并注明"不是门票钱"。

    其余（没有价格文本 / 只有别处的票）一律**不给价格**，只留备注，
    由前端显示成"门票待确认"而不是编一个数字。
    """
    ticket_prices: List[float] = []
    concession_prices: List[float] = []
    service_prices: List[float] = []
    other_names: List[str] = []

    for key, value in obj.items():
        name = " ".join(str(key).split())
        raw_val = ("、".join(str(x) for x in value)
                   if isinstance(value, (list, tuple)) else str(value))
        found = [float(m.group(1)) for m in _PRICE_RE.finditer(raw_val)]
        if not found:
            continue

        # 判据 1：没有「门票」二字 → 不是门票，归入服务价
        if "门票" not in name:
            # 但要排除旅游打包产品（一日游/酒店/跟团），它们不是景区的服务
            if not any(w in name for w in _PACKAGE_WORDS):
                service_prices.extend(found)
            continue
        # 含"门票+"的打包套餐同理，不算门票也不算景区服务
        if any(w in name for w in _PACKAGE_WORDS):
            other_names.append(name)
            continue
        if any(w in name for w in _ATTRACTION_SERVICE_WORDS):
            service_prices.extend(found)
            continue
        # 判据 4：必须以**这一行地名开头**，才算本景点的门票
        # （不能用"行名出现在 SKU 名里"——「鼓浪屿」是「故宫鼓浪屿外国文物馆门票成人票」
        #   的子串，会把外国文物馆的票价贴到鼓浪屿头上）
        if row_name and not name.startswith(row_name):
            other_names.append(name)
            continue
        # 判据 2：标准成人/单人票
        if any(std in name for std in _SKU_STANDARD):
            ticket_prices.append(min(found))
        else:
            # 是本地门票，但不是成人票（老人/儿童/学生/优待）
            concession_prices.extend(found)

    if ticket_prices:
        return min(ticket_prices), False, KIND_TICKET, ""
    if concession_prices:
        avg = sum(concession_prices) / len(concession_prices)
        return (round(avg, 1), False, KIND_CONCESSION_AVG,
                f"语料只有优待/学生票（¥{min(concession_prices):g}–¥{max(concession_prices):g}），"
                f"此处取这些票种的均价 ¥{avg:.1f}；成人票价通常更高，以现场公示为准")
    if service_prices:
        avg = sum(service_prices) / len(service_prices)
        return (round(avg, 1), False, KIND_SERVICE_AVG,
                f"语料中只有含服务的票（¥{min(service_prices):g}–¥{max(service_prices):g}，"
                f"含讲解/游船/观光等项目），无法单独确定门票价；"
                f"此处按这些票的均价 ¥{avg:.1f} 估算，**不等于门票价格**")
    if other_names:
        return (None, False, KIND_UNKNOWN,
                f"语料中未见本景点门票，只有景区内其他景点/项目或旅游打包产品的票"
                f"（如「{other_names[0][:16]}」）；本景点可能免费或需现场购票")
    return None, False, KIND_UNKNOWN, text.strip()


def normalize_name(name: str) -> str:
    """归一化地名：只留中英文数字，剥掉通用后缀与分隔符。

    ``"杭州西湖风景名胜区-花港观鱼"`` → ``"杭州西湖花港观鱼"``
    ``"花港观鱼"`` → ``"花港观鱼"``
    """
    text = _KEEP_RE.sub("", (name or "").strip())
    if not text:
        return ""
    # 反复剥后缀，直到稳定（"西湖风景区公园" → "西湖"）
    prev = None
    while prev != text:
        prev = text
        for word in _STRIP_WORDS:
            if text.endswith(word) and len(text) > len(word):
                text = text[: -len(word)]
    return text


def name_candidates(name: str) -> List[str]:
    """把高德长名拆成若干候选（用于精确匹配）。

    ``"杭州西湖风景名胜区-花港观鱼"`` → ``["杭州西湖风景名胜区-花港观鱼",
    "杭州西湖风景名胜区", "花港观鱼"]``
    """
    raw = (name or "").strip()
    if not raw:
        return []
    parts = [p for p in _SPLIT_RE.split(raw) if p]
    out: List[str] = [raw]
    out.extend(parts)
    # 去重保序
    seen = set()
    uniq = []
    for item in out:
        if item not in seen:
            seen.add(item)
            uniq.append(item)
    return uniq


# 完整包含匹配时的最小长度：低于这个长度不予包含匹配，
# 避免"西湖"这种通用名把票价贴到所有含它的地点上
MIN_CONTAIN_LEN = 4


def match_price(amap_name: str, price_map: Dict[str, Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """在某个城市的票价表里为高德 POI 找一个高置信度匹配。

    ``price_map`` 的键是归一化后的语料名。匹配规则（从严，按优先级）：

    1. 高德的某个候选名**归一化后完全相等**；
    2. 语料名长度 ≥ ``MIN_CONTAIN_LEN``，且完整出现在高德全名的归一化结果里。

    匹配不上返回 None —— 交给上层标成"票价未知"，而不是编一个数字。
    """
    if not amap_name or not price_map:
        return None

    for cand in name_candidates(amap_name):
        norm = normalize_name(cand)
        if norm and norm in price_map:
            return price_map[norm]

    full = normalize_name(amap_name)
    if full:
        best: Optional[Tuple[int, Dict[str, Any]]] = None
        for norm, info in price_map.items():
            if len(norm) >= MIN_CONTAIN_LEN and norm in full:
                # 多个命中时取最长的那个（最具体）
                if best is None or len(norm) > best[0]:
                    best = (len(norm), info)
        if best:
            return best[1]
    return None


# ---------------------------------------------------------------- 读写

def replace_city_prices(city: str, rows: List[Dict[str, Any]]) -> int:
    """整体替换某城市的票价表。

    ``rows`` 每项含 ``name/price/is_free/kind/note/price_text/source``。
    """
    now = utc_now_iso()
    with get_conn() as conn:
        conn.execute("DELETE FROM poi_prices WHERE city = ?", (city,))
        conn.executemany(
            """INSERT INTO poi_prices
               (city, name, name_norm, price, is_free, kind, note,
                price_text, source, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            [
                (city, r["name"], normalize_name(r["name"]), r.get("price"),
                 1 if r.get("is_free") else 0, r.get("kind") or KIND_UNKNOWN,
                 r.get("note") or "", r.get("price_text") or "",
                 r.get("source") or "", now)
                for r in rows if r.get("name")
            ],
        )
    return len(rows)


def load_city_prices(city: str) -> Dict[str, Dict[str, Any]]:
    """载入某城市的票价表（归一化名 → 票价信息）。规划路径每次规划调一次。"""
    if not city:
        return {}
    try:
        with get_conn() as conn:
            rows = conn.execute(
                "SELECT name, name_norm, price, is_free, kind, note, price_text, source "
                "FROM poi_prices WHERE city = ?", (city,)
            ).fetchall()
    except Exception as exc:
        logger.warning("票价表读取失败，按「无票价数据」继续：%s", exc)
        return {}
    out: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        norm = row["name_norm"]
        if not norm or norm in out:
            continue
        out[norm] = {
            "name": row["name"],
            "price": row["price"],
            "is_free": bool(row["is_free"]),
            "kind": row["kind"] or KIND_UNKNOWN,
            "note": row["note"] or "",
            "price_text": row["price_text"] or "",
            "source": row["source"] or "",
        }
    return out


def price_stats() -> Dict[str, Any]:
    """票价表统计（供验收与调试）。按类别统计，避免重复计数。"""
    try:
        with get_conn() as conn:
            total = int(conn.execute("SELECT count(*) FROM poi_prices").fetchone()[0])
            by_kind = {
                (r["kind"] or KIND_UNKNOWN): int(r["n"])
                for r in conn.execute(
                    "SELECT COALESCE(kind, ?) AS kind, count(*) AS n "
                    "FROM poi_prices GROUP BY kind", (KIND_UNKNOWN,)).fetchall()
            }
            cities = [r["city"] for r in conn.execute(
                "SELECT DISTINCT city FROM poi_prices ORDER BY city").fetchall()]
    except Exception:
        return {"total": 0, "with_price": 0, "free": 0, "known": 0,
                "by_kind": {}, "cities": []}
    ticket = by_kind.get(KIND_TICKET, 0)
    free = by_kind.get(KIND_FREE, 0)
    return {
        "total": total,
        "with_price": ticket + free,   # 真正能用的（真实票价 + 明确免费）
        "ticket": ticket,
        "free": free,
        "known": ticket + free,
        "service_only": by_kind.get(KIND_SERVICE, 0),
        "unknown": by_kind.get(KIND_UNKNOWN, 0),
        "by_kind": by_kind,
        "cities": cities,
    }


def apply_known_prices(pois: List[Any], city: str) -> Dict[str, int]:
    """把已知票价写回 POI 列表（就地修改 cost / cost_source）。

    返回统计 ``{"matched": n, "free": n, "unknown": n}``——
    把命中数暴露出来，才能被测试断言、被日志审计。
    """
    price_map = load_city_prices(city)
    stat = {"matched": 0, "free": 0, "unknown": 0}
    if not price_map:
        return stat

    for poi in pois:
        # 只覆盖景点：餐饮的人均消费来自高德真实数据，比语料更准
        if getattr(poi, "poi_type", "") != "spot":
            continue
        hit = match_price(getattr(poi, "name", ""), price_map)
        if not hit:
            stat["unknown"] += 1
            continue
        kind = hit.get("kind") or KIND_UNKNOWN
        note = hit.get("note") or ""
        if kind == KIND_FREE:
            # 明确免费 → **独立取值 "free"**，不是 third_party。
            # cost=0 + third_party 无法区分「免费」与「真实票价 ¥0（不可能）」，
            # 前端要显示「免费」而不是「¥0」，得靠语义清晰的取值。
            poi.cost = 0.0
            poi.cost_source = "free"
            poi.cost_note = note or "门票免费"
            stat["free"] += 1
            stat["matched"] += 1
        elif kind == KIND_TICKET and hit.get("price") is not None:
            poi.cost = float(hit["price"])
            poi.cost_source = "third_party"
            poi.cost_note = note
            stat["matched"] += 1
        elif kind in AVG_KINDS:
            # 只有优待票 / 只有服务项目：**不显示数字**，只保留备注。
            #
            # 这里推翻了早先"按均价算"的做法（用户已确认改回本规格）。
            # 理由：均价不是门票价——"游船均价 ¥135"写进金额栏，
            # 等于把一项服务的价格冒充成门票。数字只在能代表门票时才出现。
            poi.cost = None
            poi.cost_source = "average"
            poi.cost_note = note
            stat["average"] = stat.get("average", 0) + 1
            stat["unknown"] += 1
        else:
            # 没有价格数据（文本无价格 / 无字段 / 只有别处的票）：
            # **不编价格**，把语料原文作为备注带出去，前端显示"门票待确认"
            poi.cost = None
            poi.cost_source = None
            poi.cost_note = note
            stat["unknown"] += 1
    if stat["matched"]:
        logger.info("票价匹配：%s 命中 %d 个（免费 %d），未知 %d 个",
                    city, stat["matched"], stat["free"], stat["unknown"])
    return stat
