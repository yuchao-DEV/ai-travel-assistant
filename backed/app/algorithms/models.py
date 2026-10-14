"""算法引擎核心数据模型与 profile 权重。"""
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from app.amap.client import haversine

logger = logging.getLogger("app.models")

# ==================================================================
# 营业时间解析
# ==================================================================

# 匹配 "09:00-16:30" / "9:00~17:00" / "07:30 至 18:00"
_TIME_RANGE_RE = re.compile(
    r"(\d{1,2})\s*[:：]\s*(\d{2})\s*[-~－—–至到]\s*(\d{1,2})\s*[:：]\s*(\d{2})"
)
# 只有这些才代表"全天可游"。
# 注意不能把裸"全天"算进来：高德文本里"周二全天关闭"同样含"全天"，
# 一旦按全天营业处理，就会把闭馆的博物馆当成 24 小时开放。
_FULL_DAY_MARKERS = ("24小时", "00:00-24:00", "全天开放", "全天营业")


def parse_open_ranges(text: str) -> List[Tuple[int, int]]:
    """把高德营业时间文本解析成分钟区间列表。

    高德该字段格式不统一，实测出现过这些形态：
      "09:00-16:30"                      → [(540, 990)]
      "24小时营业"                        → [(0, 1440)]
      "09:00-21:00 09:00-16:30"          → 两个区间
      "周一至周日 10:00-22:00"            → [(600, 1320)]
      "周一，周三-周日09:00-16:30开放…"    → 取首个匹配（并忽略"周三-周日"这种星期范围）
      []                                  → []

    解析不出任何区间时返回空列表，表示"未知"，
    排程阶段会退回默认活动窗口而不是把该点当作不可游览。
    """
    if not text:
        return []
    if isinstance(text, (list, tuple)):
        for item in text:
            ranges = parse_open_ranges(item)
            if ranges:
                return ranges
        return []

    if any(marker in text for marker in _FULL_DAY_MARKERS):
        return [(0, 24 * 60)]

    ranges: List[Tuple[int, int]] = []
    for h1, m1, h2, m2 in _TIME_RANGE_RE.findall(text):
        start = int(h1) * 60 + int(m1)
        end = int(h2) * 60 + int(m2)
        # "00:00-24:00" 这类跨天写法
        if end <= start and end == 0:
            end = 24 * 60
        if 0 <= start < 24 * 60 and start < end <= 24 * 60:
            ranges.append((start, end))
        if len(ranges) >= 3:
            break

    if not ranges:
        return []
    # 合并重叠区间并取并集的边界
    ranges.sort()
    merged = [list(ranges[0])]
    for start, end in ranges[1:]:
        if start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [(s, e) for s, e in merged]


def open_window(ranges: List[Tuple[int, int]]) -> Optional[Tuple[int, int]]:
    """把区间列表压成一个可用的开闭时间窗（最早的开始、最晚的结束）。

    用于排程的粗粒度约束；没有区间时返回 None（视为未知、不约束）。
    """
    if not ranges:
        return None
    return min(s for s, _ in ranges), max(e for _, e in ranges)


# ==================================================================
# Profile 权重（四项归一到 [0,1]，和=1）
# score = w_interest*interest + w_rating*rating - w_detour*detour - w_cost*cost
# ==================================================================

# Profile 权重（三维归一到 [0,1]，和=1）
#
# score = w_interest*interest + w_rating*rating - w_detour*detour
#
# **成本维度已删除**（见《移除预算系统与重构偏好体系-执行规格》决策 B）：
# 票价语料覆盖率只有 23.8%、住宿完全未接入，用"钱"做区分维度既不成立
# 也不可验证。换成两条**有真实数据支撑**的轴：
#   时间轴 —— daily_select_target() 按高德营业时间推导当日容量
#   口碑轴 —— 高德 biz_ext.rating（实测 8,218 个候选里 median 4.5）
#
# 三档的语义差别现在完全落在「绕路容忍度」上：
#   悠闲 —— 宁可少去几个，也要每个点待得住（绕路容忍度低，兴趣权重最高）
#   经典 —— 兴趣与口碑均衡
#   尽览 —— 尽量少折返，同样时间覆盖最多（绕路权重最高 = 最不容忍走回头路）
PROFILES: Dict[str, Dict[str, float]] = {
    "悠闲": {"w_interest": 0.50, "w_rating": 0.30, "w_detour": 0.20},
    "经典": {"w_interest": 0.40, "w_rating": 0.40, "w_detour": 0.20},
    "尽览": {"w_interest": 0.30, "w_rating": 0.20, "w_detour": 0.50},
}

# 旧 profile 名 → 新名。无成本维度后「经济型」是空承诺，映射到「悠闲」。
# 历史行程（21 条）里的 profile 字段仍是旧值，回看时必须能映射。
LEGACY_PROFILES: Dict[str, str] = {
    "经济型": "悠闲",
    "舒适型": "经典",
    "紧凑型": "尽览",
}

DEFAULT_PROFILE = "经典"


def get_profile(name: str) -> Dict[str, float]:
    """获取 profile 权重。旧名自动映射，未知名称回退到「经典」。"""
    canonical = LEGACY_PROFILES.get(name, name)
    weights = PROFILES.get(canonical)
    if weights is None:
        return PROFILES[DEFAULT_PROFILE]
    if canonical != name:
        logger.info("profile「%s」是旧名，已映射为「%s」", name, canonical)
    return weights


# ==================================================================
# POI 数据模型（从高德 API 结果 + typecode_map 富化后）
# ==================================================================

@dataclass
class POI:
    """一个兴趣点（景点/餐厅/酒店）。"""
    name: str
    lat: float
    lng: float
    typecode: str = ""
    rating: float = 0.0           # 高德评分 [0,5]，来自 biz_ext.rating
    tags: List[str] = field(default_factory=list)  # 兴趣 tag: 历史/自然/...
    indoor: bool = False          # 是否室内
    address: str = ""
    cost: Optional[float] = None   # 费用（门票或人均消费）。**None = 无可靠数据**
    # cost_source 取值集合（只有前三个允许显示数字）：
    #   real        —— 高德返回的真实人均消费（餐饮实测 24/25 有值）
    #   third_party —— 知识库语料里的真实门票价（poi_prices 的 KIND_TICKET）
    #   free        —— 明确免费（KIND_FREE，前端显示「免费」而不是「¥0」）
    #   average     —— 只有优待票/服务项目，均价**不显示**，只出 cost_note
    #   None        —— 完全无数据，不显示任何数字
    # 原 `reserved`（预算预留）已删除：那是个编造的数字，不是票价。
    cost_source: Optional[str] = None
    cost_note: str = ""            # 费用说明（如"门票免费，¥135 是游船均价，非门票费用"）
    open_hours: str = ""          # 营业时间原文（展示用，如 "周二至周日 09:00-16:30"）
    open_ranges: List[Tuple[int, int]] = field(default_factory=list)  # 解析后的分钟区间
    poi_type: str = "spot"        # spot/restaurant/hotel（由 typecode 前缀判定）
    must_go: bool = False         # 用户指定的必去项（硬约束：不被预算/排序挤掉）
    # 以下为高德返回、此前被丢弃的字段
    category: str = ""            # 高德中文三级分类，如 "风景名胜;风景名胜;国家级景点"
    scenic_level: str = ""        # 从 category 提取的等级词，如 "国家级景点"
    business_area: str = ""       # 商圈，如 "西湖"
    photos: List[str] = field(default_factory=list)
    # 以下为算法计算阶段填充
    score: float = 0.0
    score_breakdown: Dict[str, float] = field(default_factory=dict)

    @classmethod
    def from_amap(cls, raw: Dict[str, Any]) -> "POI":
        """从高德 API 原始结果构造。

        **不再猜测费用。** 餐饮类高德会返回真实人均（biz_cost），景点类实测为空；
        从前那种"按类型给个预留值"的做法已删除——那个数字不是票价，
        而界面一旦显示它，就是在声称系统知道票价。
        """
        biz_cost = float(raw.get("biz_cost") or 0.0)
        has_real_cost = biz_cost > 0
        return cls(
            name=raw.get("name", ""),
            lat=raw.get("lat", 0.0),
            lng=raw.get("lng", 0.0),
            typecode=raw.get("typecode", ""),
            rating=float(raw.get("rating") or 0.0),
            address=raw.get("address", ""),
            # 只有高德真的给了人均才填数字；否则 None（下游必须处理 None）
            cost=biz_cost if has_real_cost else None,
            cost_source="real" if has_real_cost else None,
            open_hours=raw.get("open_time_text", "") or raw.get("open_time", ""),
            open_ranges=parse_open_ranges(
                raw.get("open_time", "") or raw.get("open_time_text", "")
            ),
            category=raw.get("category", ""),
            business_area=raw.get("business_area", ""),
            photos=list(raw.get("photos") or []),
        )


# ==================================================================
# 评分函数
# ==================================================================

def interest_match(poi_tags: List[str], user_tags: List[str]) -> float:
    """计算 POI 的兴趣匹配度 [0,1]。

    用户选 N 个 tag，内部权重各 1/N。
    matched = Σ(1/N for t in poi.tags if t in user_tags)
    interest = matched / 1.0  （因为用户权重和=1）
    """
    if not user_tags:
        return 0.0
    w = 1.0 / len(user_tags)
    matched = sum(w for t in poi_tags if t in user_tags)
    return min(1.0, matched)


def rating_norm(rating: float) -> float:
    """高德评分 [4,5] → [0,1]，截断。

    **下界从 3.0 提到 4.0。** 实测 8,218 个带评分候选：中位 4.5、
    77.7% ≥4.0、54.9% ≥4.5。用 (r-3)/2 会把绝大多数点压到 0.75–0.95
    的窄带里（4.0→0.50、4.5→0.75、4.9→0.95），rating 维度**几乎不参与决策**。

    提到 4.0 之后区分度回到真实分布区间：

        4.0→0.0  4.3→0.3  4.5→0.5  4.7→0.7  4.9→0.9

    **有意副作用**：3.9 分的景点 rating_norm = 0，不再获得正分。
    这是对的——"3 分以上就算中等"这种口径在高德的实际分布里没有意义。
    """
    return max(0.0, min(1.0, (rating - 4.0) / 1.0))


def detour_norm(detour_km: float, max_detour_km: float) -> float:
    """绕路距离归一 [0,1]。"""
    if max_detour_km <= 0:
        return 0.0
    return max(0.0, min(1.0, detour_km / max_detour_km))


def compute_score(poi: POI, user_tags: List[str], ref_lat: Optional[float],
                  ref_lng: Optional[float], max_detour_km: float,
                  weights: Dict[str, float]) -> tuple:
    """计算单点综合评分（三维）。

    ``ref_lat`` / ``ref_lng`` 是绕路分量的**参考基点**，两个阶段语义不同：

    - **选点阶段**（顺序未知）：传当日候选池的聚类中心，
      距离即"该点相对当日活动区域中心的偏移"，是绕路成本的顺序无关估计；
    - **排序之后**：传真实的上一个点，反映实际游览顺序下的绕路。

    传 ``None`` 表示没有参考点（例如单点当天），绕路分量按 0 计。
    注意这里必须用 ``is None`` 判断，不能用真值判断——否则纬度为 0 的
    合法坐标（赤道附近）会被误当成"无参考点"。

    **成本维度已删除**：它依赖 `budget_per_day`，而预算系统整体移除
    （票价语料覆盖 23.8%、住宿未接入，用钱做区分不可验证）。
    评分从四维降为三维：兴趣 + 口碑 − 绕路。

    返回 (score, breakdown_dict)。
    """
    im = interest_match(poi.tags, user_tags)
    rn = rating_norm(poi.rating)
    if ref_lat is None or ref_lng is None:
        detour = 0.0
    else:
        detour = haversine(ref_lat, ref_lng, poi.lat, poi.lng)
    dn = detour_norm(detour, max_detour_km)

    score = (weights["w_interest"] * im
             + weights["w_rating"] * rn
             - weights["w_detour"] * dn)

    breakdown = {
        "interest": round(im, 4),
        "rating": round(rn, 4),
        "detour": round(dn, 4),
    }
    return round(score, 4), breakdown
