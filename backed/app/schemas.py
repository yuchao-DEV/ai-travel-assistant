"""数据模型：旧版 LLM 直调 VO + 新版算法引擎 ItineraryVO。"""
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from app.config import MAX_TRIP_DAYS, MIN_TRIP_DAYS


# ==================================================================
# 旧版 DTO/VO（/api/travel/recommend 仍在用，保留不删）
# ==================================================================

class TripBaseInfoDTO(BaseModel):
    city: str = Field(..., description="目的地城市")
    days: int = Field(..., ge=MIN_TRIP_DAYS, le=MAX_TRIP_DAYS,
                      description=f"旅行天数（{MIN_TRIP_DAYS}-{MAX_TRIP_DAYS}）")
    budget: float = Field(..., ge=100, description="预算（不少于100元）")



class TimeSlot(BaseModel):
    spot: Optional[str] = None
    duration: Optional[str] = None
    ticket: Optional[str] = None
    transportation: Optional[str] = None
    description: Optional[str] = None


class DailyItinerary(BaseModel):
    day: Optional[int] = None
    date: Optional[str] = None
    morning: Optional[TimeSlot] = None
    afternoon: Optional[TimeSlot] = None
    evening: Optional[TimeSlot] = None


class BudgetBreakdown(BaseModel):
    accommodation: Optional[str] = None
    food: Optional[str] = None
    transportation: Optional[str] = None
    tickets: Optional[str] = None
    other: Optional[str] = None


class RecommendVO(BaseModel):
    success: bool = False
    city: Optional[str] = None
    days: Optional[int] = None
    totalBudget: Optional[float] = None
    dailyItinerary: Optional[List[DailyItinerary]] = None
    budgetBreakdown: Optional[BudgetBreakdown] = None
    tips: Optional[List[str]] = None
    warnings: Optional[List[str]] = None
    error: Optional[str] = None
    rawResponse: Optional[str] = None


# ==================================================================
# 新版算法引擎 DTO/VO（/api/plan 使用）
# ==================================================================

# 可选兴趣 tag 枚举
VALID_TAGS = ["历史", "自然", "美食", "购物", "艺术", "夜生活"]

# 可选 profile（出行风格）。**不再是"花费档次"**——成本维度已整体移除。
#
# 第三档从「紧凑型」改名为「尽览」而不是沿用「紧凑」：
# VALID_PACES 里已经有一个「紧凑」（节奏维度），两个字段共用一个词会让
# 用户和后来维护者都困惑。命名依据是它的实际语义——绕路权重最高（0.50）
# 且时间窗最长（08:30–21:00），即"同样时间看最多的地方"。
VALID_PROFILES = ["悠闲", "经典", "尽览"]

# profile 预设：时间窗 + 每日选点数增量（决策 C1 的"预设"部分）
#
# 与 PROFILES（评分权重，在 algorithms/models.py）并列，都以 profile 名为键。
# 「每日选点数」不是拍脑袋的：daily_select_target() 按时间窗推导容量后，
# 再叠加这里的 spot_delta。实测结果为 3 / 5 / 8（见执行规格 §4.1）。
PROFILE_TIME: Dict[str, Dict[str, Any]] = {
    "悠闲": {"start": "10:00", "end": "18:00", "spot_delta": -1},
    "经典": {"start": "09:00", "end": "19:00", "spot_delta": 0},
    "尽览": {"start": "08:30", "end": "21:00", "spot_delta": 1},
}

# 旧 profile 名 → 新名（21 条历史行程里仍是旧值，回看时必须能映射）
LEGACY_PROFILES = {"经济型": "悠闲", "舒适型": "经典", "紧凑型": "尽览"}

DEFAULT_PROFILE = "经典"

# 同行伙伴（多选）。只用于**调整行程节奏与优先室内备选**，不改变出行风格。
VALID_COMPANIONS = ["独自", "家庭", "情侣", "朋友", "老人"]

# 行程节奏（单选）。与 profile 分开是刻意的：
# profile 表达"预设的时间窗与选点数"，pace 表达"在预设基础上再调一档"。
VALID_PACES = ["紧凑", "适中", "宽松"]

# 节奏 → 每日选点数量的增减（在 daily_select_target() 的基础上调整）
PACE_SPOT_DELTA = {"紧凑": 1, "适中": 0, "宽松": -2}

# 同行伙伴 → 每日选点数量的调整。多选时取**最保守**的那个
# （同时带老人和朋友的，按老人算——体力约束是硬约束，热闹是软偏好）。
#
# 数值对齐的是实际节奏：不选时一天约 5 个（3 天 14 个），
# 独自 5-6、家庭 3-4、情侣 3-4、朋友 4-5、老人 ≤2-3。
COMPANION_SPOT_DELTA = {
    "独自": 1,
    "家庭": -3,
    "情侣": -3,
    "朋友": -1,
    "老人": -5,
}

# 同行伙伴 → 优先去的地点（**按地点名称关键词匹配**）。
#
# 说明：高德返回的 POI 只有名称/类型/评分这些字段，**没有"亲子友好""浪漫"
# 这类属性**。所以这里只能用名称关键词做偏好——能真正改变选点结果，但不如
# 真实属性精确。等接入更丰富的地点数据后应替换掉这层。
COMPANION_PREFER = {
    "家庭": ("乐园", "动物园", "海洋馆", "水族馆", "公园", "植物园", "科技馆",
             "博物馆", "科学中心", "农场", "亲子"),
    "情侣": ("观景", "夜景", "塔", "桥", "湖", "江", "古镇", "艺术", "咖啡",
             "教堂", "花园", "沙滩", "缆车"),
    "朋友": ("步行街", "夜市", "广场", "美食街", "乐园", "古镇", "街区",
             "天地", "里", "坊"),
    "老人": ("公园", "寺", "庙", "博物馆", "文化", "古镇", "园林", "湖",
             "纪念馆", "故居", "书院"),
    "独自": ("博物馆", "美术馆", "书店", "咖啡", "展览", "艺术"),
}

# 同行伙伴 → **排除**的地点（同样是名称关键词）。
# 老人/家庭排除的是体力消耗大或有安全风险的项目——这类是硬性不合适，
# 不是"分数低一点"，所以直接剔除而不是扣分。
#
# 注意「索道/缆车」**不在**老人的排除项里：它们是省力的上山方式，
# 对老人反而是友好的。要排除的是真正费体力的：爬山、长台阶、涉水、剧烈项目。
COMPANION_AVOID = {
    "老人": ("漂流", "攀岩", "蹦极", "登山", "徒步", "滑翔", "越野", "滑雪",
             "高空", "栈道", "滑索", "探险", "露营", "速降", "冲浪", "潜水"),
    "家庭": ("酒吧", "蹦极", "攀岩", "漂流", "鬼屋", "密室", "夜店"),
    "情侣": ("鬼屋",),
}

# 同行伙伴 → 餐饮口味偏好（按餐厅名关键词加分）
COMPANION_FOOD = {
    "老人": ("粥", "面", "素", "家常", "汤", "蒸", "养生", "素食"),
    "家庭": ("自助", "快餐", "火锅", "面", "披萨", "小吃"),
    "情侣": ("西餐", "日料", "咖啡", "私房", "景观", "法餐", "意餐"),
    "朋友": ("火锅", "烧烤", "串", "小龙虾", "烤肉", "大排档"),
    "独自": ("快餐", "简餐", "面", "小吃", "咖啡"),
}

# 命中的关键词带来的分数加成（相对于 0-1 的归一化总分）
COMPANION_PREFER_BONUS = 0.12
COMPANION_FOOD_BONUS = 0.08

# 带老人同行时额外减少的选点数量：老人体力有限，宁可少去几个也别赶
ELDER_SPOT_DELTA = -1

# 出发/结束时间的合法范围（HH:MM），以及默认值
DEFAULT_DAY_START = "09:00"
DEFAULT_DAY_END = "21:00"
EARLY_DAY_START = "07:30"          # 偏早出
LATE_DAY_END = "22:00"             # 偏晚归


class PlanRequestDTO(BaseModel):
    """规划请求：前端表单提交。"""
    city: str = Field(..., description="目的地城市")
    days: int = Field(..., ge=MIN_TRIP_DAYS, le=MAX_TRIP_DAYS,
                      description=f"旅行天数（{MIN_TRIP_DAYS}-{MAX_TRIP_DAYS}）")
    tags: List[str] = Field(..., description=f"兴趣标签，可选: {VALID_TAGS}")
    must_go: List[str] = Field(default_factory=list, description="必去景点名称列表")
    avoid: List[str] = Field(default_factory=list, description="想避免的景点名称列表")
    profile: str = Field(default=DEFAULT_PROFILE,
                         description=f"出行风格: {VALID_PROFILES}")
    # 注：`budget` 已删除。票价语料覆盖仅 23.8%、住宿完全未接入，
    # 用预算做约束在数据上不成立（见执行规格 §0.1）。若旧客户端仍传 budget，
    # Pydantic 默认忽略未知字段，不会报错。
    # ---- 出行偏好（选择你的出行偏好页）----
    # 这几个字段都会**真的影响行程**，不是记下来不用的：
    #   companions → 带老人时减少每日选点、优先室内备选
    #   pace       → 直接调整每日选点数量
    #   day_start / day_end → 传给排程器决定每天几点开始、几点收工
    companions: List[str] = Field(
        default_factory=list,
        description=f"同行伙伴，可多选: {VALID_COMPANIONS}。带老人会放慢节奏")
    pace: str = Field(
        default="", description=f"行程节奏: {VALID_PACES}；留空按 profile 推导")
    day_start: str = Field(
        default="", description=f"每天开始时间 HH:MM；留空用 {DEFAULT_DAY_START}")
    day_end: str = Field(
        default="", description=f"每天结束时间 HH:MM；留空用 {DEFAULT_DAY_END}")


class Transport(BaseModel):
    """景点间交通信息。"""
    mode: str = Field(description="交通方式: walk/bus/subway/taxi")
    duration: int = Field(description="耗时（分钟）")
    distance: float = Field(description="距离（公里）")


class ScoreBreakdown(BaseModel):
    """单点评分的三个分量，用于前端可解释展示。

    **成本维度已删除**（评分从四维降为三维）——它依赖预算，而预算系统整体移除。
    """
    interest: float = Field(description="兴趣匹配 [0,1]")
    rating: float = Field(description="评分归一 [0,1]，高德 4.0→0、4.5→0.5、4.9→0.9")
    detour: float = Field(description="绕路成本 [0,1]")


class ItineraryItem(BaseModel):
    """行程中的单个条目（景点/餐厅/酒店）。"""
    type: str = Field(description="类型: spot/restaurant/hotel")
    name: str = Field(description="名称")
    lat: float = Field(description="纬度")
    lng: float = Field(description="经度")
    start: str = Field(description="开始时间 HH:MM")
    end: str = Field(description="结束时间 HH:MM")
    # **cost 是 Optional**：None 表示没有可靠数据，前端不得显示数字。
    # 从前这里是必填 float（默认 0.0），下游任何 `sum(p.cost)` 都要能处理 None。
    cost: Optional[float] = Field(
        default=None, description="费用（元）。None = 无可靠数据，不显示数字")
    cost_source: Optional[str] = Field(
        default=None,
        description=(
            "费用来源。只有 real / third_party / free 三种允许显示数字："
            "real=高德真实人均；third_party=语料真实门票价；free=明确免费；"
            "average=只有优待票或服务项目，**均价不显示**，只出 cost_note；"
            "None=无数据。原 reserved（预算预留）已删除"
        ),
    )
    cost_note: str = Field(
        default="",
        description="费用说明。average / None 时前端应显示它而不是价格",
    )
    open_hours: Optional[str] = Field(
        default=None, description="高德返回的营业时间原文，未知时为 null"
    )
    photos: List[str] = Field(
        default_factory=list,
        description="高德返回的真实图片地址（已归一为 https），最多 3 张；可能为空",
    )
    score: float = Field(description="综合评分 [0,1]")
    score_breakdown: ScoreBreakdown = Field(description="评分分量")
    transport: Optional[Transport] = Field(default=None, description="到下一景点的交通")


class DailyPlan(BaseModel):
    """单日行程。"""
    date: str = Field(description="日期，如 Day 1 / 2026-10-10")
    items: List[ItineraryItem] = Field(description="当日条目列表")
    # daily_cost 已删除：单价多为 None（无可靠数据），加总没有意义。
    daily_summary: Optional[str] = Field(default=None, description="LLM 生成的每日导语")


class ItineraryVO(BaseModel):
    """算法引擎输出的结构化行程，替代旧 RecommendVO。

    注：`total_cost` / `budget` / `budget_note` 已删除。理由见执行规格 §0.1：
    门票语料覆盖 23.8%、住宿未接入，任何"总花费"都缺一大块且主要来自预留值，
    继续输出它就是在声称系统算得准。
    """
    days: List[DailyPlan] = Field(description="每日行程")
    profile: str = Field(description="使用的出行风格 profile")
    weather: List[str] = Field(description="每日天气简述")
    city: str = Field(description="城市")
    description: Optional[str] = Field(default=None, description="LLM 润色的顶层描述")
    enhanced: bool = Field(default=True, description="True=高德数据增强; False=无地点数据")


class Result(BaseModel):
    """统一响应包装，对应 Java 的 Result<T>。"""
    success: bool = True
    code: int = 200
    message: Optional[str] = None
    data: Optional[Any] = None


# ==================================================================
# 用户系统 DTO/VO（/api/auth/* 使用）
# ==================================================================

# 验证码业务场景
SCENE_REGISTER = "register"
SCENE_LOGIN = "login"
VALID_SMS_SCENES = [SCENE_REGISTER, SCENE_LOGIN]


class SendCodeDTO(BaseModel):
    """发送验证码请求。"""
    phone: str = Field(..., description="手机号（中国大陆 11 位）")
    scene: str = Field(default=SCENE_LOGIN, description=f"场景，可选: {VALID_SMS_SCENES}")


class SendCodeVO(BaseModel):
    """发送验证码响应。"""
    phone: str = Field(description="脱敏后的手机号")
    scene: str = Field(description="业务场景")
    expires_in: int = Field(description="验证码有效期（秒）")
    debug_code: Optional[str] = Field(
        default=None,
        description="仅开发/模拟模式回显，方便本地联调；生产环境为 null",
    )


class RegisterDTO(BaseModel):
    """账号密码注册请求。"""
    username: str = Field(..., min_length=3, max_length=20,
                          description="账号：3–20 位，字母/数字/下划线/中文")
    password: str = Field(..., min_length=6, max_length=64,
                          description="密码：6–64 位")
    nickname: Optional[str] = Field(default=None, description="昵称，可选")
    avatar: Optional[str] = Field(default=None, description="头像 URL，可选")


class LoginDTO(BaseModel):
    """账号密码登录请求。"""
    username: str = Field(..., description="账号")
    password: str = Field(..., min_length=1, max_length=64, description="密码")


class UserVO(BaseModel):
    """用户公开信息（不含任何敏感字段）。"""
    id: int = Field(description="用户 ID")
    username: str = Field(description="账号")
    nickname: Optional[str] = Field(default=None, description="昵称")
    avatar: Optional[str] = Field(default=None, description="头像 URL")
    created_at: str = Field(description="注册时间（ISO-8601 UTC）")
    last_login_at: Optional[str] = Field(default=None, description="上次登录时间")


class AuthVO(BaseModel):
    """注册/登录成功后的凭证与用户信息。"""
    token: str = Field(description="登录 token，后续放在 Authorization: Bearer <token>")
    expires_in: int = Field(description="token 有效期（秒）")
    user: UserVO = Field(description="用户信息")


# ==================================================================
# 历史行程 DTO/VO（/api/history/* 使用）
# ==================================================================

class HistorySaveDTO(BaseModel):
    """把一次已生成的行程存为历史记录。

    入参与出参都是算法产出的原文，后端不做任何二次加工，
    保证"选点/排序/时间/费用只由算法决定"这条边界不被破坏。
    """
    client_id: Optional[str] = Field(
        default=None, description="前端本地记录 id，用于幂等去重（同用户下唯一）"
    )
    title: Optional[str] = Field(default=None, description="行程标题")
    origin: Optional[str] = Field(default=None, description="出发地")
    request: PlanRequestDTO = Field(description="当时的规划入参")
    itinerary: ItineraryVO = Field(description="算法引擎产出的完整行程")
    created_at: Optional[str] = Field(
        default=None, description="生成时间（ISO-8601），不传则用服务端当前时间"
    )


class HistoryItemVO(BaseModel):
    """历史行程列表项：只带列表页需要的字段，不含完整行程。"""
    id: int = Field(description="历史记录 ID")
    client_id: Optional[str] = Field(default=None, description="前端本地记录 id")
    title: Optional[str] = Field(default=None, description="行程标题")
    origin: Optional[str] = Field(default=None, description="出发地")
    city: str = Field(description="目的地城市")
    days: int = Field(description="天数")
    # 已废弃：预算系统移除后不再产生新值，新行程写 0。仅为兼容 21 条历史记录保留。
    budget: float = Field(default=0.0, description="【已废弃】预算，新数据恒为 0")
    tags: List[str] = Field(description="兴趣标签")
    profile: str = Field(description="出行风格（可能是旧名：经济型/舒适型/紧凑型）")
    # 已废弃：新行程恒为 None（总花费口径不成立）
    total_cost: Optional[float] = Field(
        default=None, description="【已废弃】行程总费用，新数据恒为 None")
    spot_count: int = Field(default=0, description="景点/餐饮条目总数")
    enhanced: bool = Field(default=False, description="是否高德数据增强")
    created_at: str = Field(description="生成时间（ISO-8601 UTC）")


class HistoryDetailVO(HistoryItemVO):
    """历史行程详情：列表项字段 + 原始入参与完整行程。"""
    request: PlanRequestDTO = Field(description="当时的规划入参")
    itinerary: ItineraryVO = Field(description="完整行程")


class HistoryListVO(BaseModel):
    """历史行程分页列表。"""
    total: int = Field(description="总条数")
    page: int = Field(description="当前页码，从 1 开始")
    page_size: int = Field(description="每页条数")
    items: List[HistoryItemVO] = Field(description="当前页数据")


class HistoryImportDTO(BaseModel):
    """批量导入本地（localStorage）历史记录。

    ``trips`` 刻意用宽松类型：本地记录属于用户自产数据，可能来自旧版本格式，
    逐条在服务层校验可以让"部分非法"不至于让整批导入失败。
    """
    trips: List[Any] = Field(
        default_factory=list,
        description="待导入的本地记录数组，每项结构同 HistorySaveDTO",
    )


class HistoryImportVO(BaseModel):
    """批量导入结果。"""
    imported: int = Field(description="新写入条数")
    skipped: int = Field(description="已存在而跳过的条数")
    failed: int = Field(description="校验失败而丢弃的条数")
    ids: List[int] = Field(default_factory=list, description="本次写入的记录 ID")


# ==================================================================
# 自然语言需求解析 DTO/VO（/api/parse 使用）
# ==================================================================

class ParseRequestDTO(BaseModel):
    """把一句自然语言需求解析成结构化规划参数。"""
    text: str = Field(..., min_length=1, max_length=500, description="用户自然语言描述")
    defaults: Optional[PlanRequestDTO] = Field(
        default=None,
        description="可选兜底值（例如表单里已经填好的内容）；文本中没提到的字段沿用它",
    )


class AgentChatMessageDTO(BaseModel):
    """Agent 多轮对话里的一条历史消息。"""
    role: str = Field(default="user", description="user / assistant")
    content: str = Field(default="", max_length=4000)


class AgentChatRequestDTO(BaseModel):
    """Agent 会话请求。"""
    message: str = Field(..., min_length=1, max_length=2000,
                         description="用户这句话")
    session_id: str = Field(default="", max_length=64,
                            description="会话标识；留空则服务端生成")
    history: List[AgentChatMessageDTO] = Field(
        default_factory=list, description="最近几轮对话，用于多轮上下文")


class KbUploadDTO(BaseModel):
    """上传一篇知识库文档。"""
    title: str = Field(..., min_length=1, max_length=120)
    text: str = Field(..., min_length=1, max_length=50000)
    city: Optional[str] = Field(default=None, max_length=20)
    kind: str = Field(default="general", description="rule / general / city / poi")
    category: Optional[str] = Field(default=None, max_length=40)


class ImportTextDTO(BaseModel):
    """从一段文字导入地点（攻略、聊天记录、行程单等）。"""
    text: str = Field(..., min_length=1, max_length=6000, description="要导入的文字")
    city: Optional[str] = Field(
        default=None, max_length=20,
        description="目的地城市；给了就以它为准做限定城市的高德反查",
    )


class ParsedPlanVO(BaseModel):
    """解析结果：可直接提交给 /api/plan 的结构化参数 + 给用户看的解释。

    刻意把"识别到什么""哪些是猜的""哪些被夹取了"都暴露出来，
    让用户提交前能核对——这也是本项目"算法决策、LLM 只抽取"的可解释性体现。
    """
    request: PlanRequestDTO = Field(description="可直接提交 /api/plan 的规划参数")
    inferred: List[str] = Field(
        default_factory=list, description="成功从文本里识别出的字段（中文名）"
    )
    assumed: List[str] = Field(
        default_factory=list, description="文本里没提到、用了默认值兜底的字段（中文名）"
    )
    notes: List[str] = Field(
        default_factory=list, description="需要提示用户的说明（越界夹取、降级解析等）"
    )
    source: str = Field(default="llm", description="解析来源：llm / rule")
    raw_text: str = Field(default="", description="原始输入文本")
