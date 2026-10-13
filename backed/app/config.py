"""应用配置：LLM + 高德地图，从 .env 和环境变量读取。"""
import os
from pathlib import Path

from dotenv import load_dotenv

# 加载 .env 文件（如果存在）
BASE_DIR = Path(__file__).resolve().parent.parent          # backed/
load_dotenv(BASE_DIR / ".env")


def _env_str(name: str, default: str) -> str:
    """读取环境变量；未设置或为空串时回退默认值。

    .env.example 里 DB_PATH= 这类留空写法不应被当成有效值。
    """
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    return value.strip()


# ---------- 路径 ----------
DATA_DIR = BASE_DIR / "data"
CACHE_DIR = BASE_DIR / "cache"

# ---------- 数据库（SQLite，标准库 sqlite3）----------
# 相对路径按 backed/ 解析，避免受启动时工作目录影响
_db_path = Path(_env_str("DB_PATH", "data/app.db"))
DB_PATH = _db_path if _db_path.is_absolute() else (BASE_DIR / _db_path)

# ---------- LLM ----------
# 密钥只从 .env / 环境变量读取，不设硬编码默认值
API_KEY = os.getenv("LLM_API_KEY", "")
BASE_URL = os.getenv("LLM_BASE_URL", "https://api.siliconflow.cn")
MODEL = os.getenv("LLM_MODEL", "deepseek-ai/DeepSeek-V4-Pro")

# ---------- 高德开放平台 ----------
# 用户需申请两个 Key：Web 服务（REST）+ JS API（前端地图）
# 未配置时走降级模式（纯 LLM，enhanced=False）
AMAP_KEY = os.getenv("AMAP_KEY", "")
AMAP_BASE = os.getenv("AMAP_BASE", "https://restapi.amap.com/v3")

# ---------- 缓存 TTL ----------
POI_CACHE_TTL = int(os.getenv("POI_CACHE_TTL", "86400"))    # 24h
MATRIX_CACHE_TTL = int(os.getenv("MATRIX_CACHE_TTL", "604800"))  # 7d

# ---------- 行程天数上限 ----------
# 之前这个上限硬编码在三处且互不一致（schemas 的 PlanRequestDTO 是 1..7、
# nl_parse 是 MAX_DAYS=7、前端又各写了一遍 7），而旧的 TripBaseInfoDTO 却是 1..30。
# 现在统一由这里给出，前端通过 /api/meta 读取同一份。
# 说明：算法本身对天数没有上限；限制来自 POI 池深度——关键词每页 25 条、
# 每个关键词最多取 50 条，天数越多平均每天可用的点越少。
MAX_TRIP_DAYS = int(os.getenv("MAX_TRIP_DAYS", "30"))
MIN_TRIP_DAYS = 1

# ---------- 服务端口 ----------
PORT = int(os.getenv("PORT", "8080"))

# ---------- 用户系统 / 登录态 ----------
# 登录 token 有效期（天）
TOKEN_TTL_DAYS = int(os.getenv("TOKEN_TTL_DAYS", "30"))

# ---------- RAG：Embedding 与检索 ----------
# 总开关。设 RAG_ENABLED=false 时检索相关功能整体停用（Agent 仍可用，只是少了知识）。
RAG_ENABLED = _env_str("RAG_ENABLED", "true").lower() in ("1", "true", "yes")

# Embedding 单独一套配置，因为「对话模型」和「向量模型」常常不在同一个服务上：
# 当前 .env 用的是 DeepSeek 官方（只提供对话，没有 embedding 接口），
# 因此默认会**取不到向量而退化为纯 BM25 检索**——这是设计好的降级路径，不是故障。
# 想启用向量检索，把 EMBED_BASE_URL / EMBED_API_KEY 指向提供 embedding 的服务
# （如硅基流动 SiliconFlow 的 BAAI/bge-m3）即可。
EMBED_MODEL = _env_str("EMBED_MODEL", "BAAI/bge-m3")
# 向量维度。bge-m3 是 1024。**写入前会强校验**，维度不符直接拒绝写入——
# 混入不同维度的向量会让余弦相似度结果完全失去意义，且极难排查。
EMBED_DIM = int(_env_str("EMBED_DIM", "1024"))
# 一次请求最多提交多少条文本
EMBED_BATCH = int(_env_str("EMBED_BATCH", "32"))
# 未单独配置时继承 LLM 的服务地址与密钥
EMBED_API_KEY = _env_str("EMBED_API_KEY", "") or API_KEY
EMBED_BASE_URL = _env_str("EMBED_BASE_URL", "") or BASE_URL

# 最终返回给调用方的条数
RAG_TOP_K = int(_env_str("RAG_TOP_K", "5"))
# 每路召回条数（BM25 与向量各自取这么多，再融合）
RAG_RECALL_K = int(_env_str("RAG_RECALL_K", "30"))
# RRF 平滑常数，见 retriever 的融合公式
RAG_RRF_K = int(_env_str("RAG_RRF_K", "60"))
# 向量扫描上限：SQLite 里没有向量索引，全表算余弦；
# 这个上限避免 chunk 上万后单次查询变慢（超出部分按 chunk_id 截断）。
RAG_VECTOR_SCAN_LIMIT = int(_env_str("RAG_VECTOR_SCAN_LIMIT", "2000"))

# 切分参数
MAX_CHUNK_SIZE = int(_env_str("MAX_CHUNK_SIZE", "500"))
MIN_CHUNK_SIZE = int(_env_str("MIN_CHUNK_SIZE", "80"))
CHUNK_OVERLAP = int(_env_str("CHUNK_OVERLAP", "60"))


# ---------- 候选池是否用知识库景点清单扩充 ----------
# 开启后：知识库的景点名逐个反查高德，命中的真实 POI 合并进候选池。
#
# 为什么要开：高德关键词搜索有取样上限（max_per_keyword=50）且偏市中心，
# 实测北京 5 个关键词只取到 228 个 POI，明十三陵、北京动物园、清华大学、
# 红砖美术馆这些知名景点**都不在候选池里**（高德直接搜名字明明搜得到）。
# 后果是行程只能在市中心的小点里打转，一个城市最值得去的地方从原理上进不来。
#
# 代价：每城首次多约 90–100 次高德调用（之后 24 小时走缓存）。
# 没有知识库语料的城市自动跳过，行为与开启前完全一致。
POI_POOL_AUGMENT = _env_str("POI_POOL_AUGMENT", "true").lower() in ("1", "true", "yes")

# ---------- 知识库 ----------
# 随代码同步的语料目录（领域规则 / 通用经验 / 城市攻略），启动时增量索引。
KNOWLEDGE_DIR = Path(_env_str("KNOWLEDGE_DIR", "knowledge"))
if not KNOWLEDGE_DIR.is_absolute():
    KNOWLEDGE_DIR = BASE_DIR / KNOWLEDGE_DIR

# 是否把高德缓存 POI 也转成 L1 文档索引。
# **默认关闭**：L1 层已由 scripts/import_citydata.py 用带「介绍 / 小贴士」的语料
# 建好（1,120 篇 / 2,069 块），而高德 POI 没有长文本，再索引一遍只会产生
# 薄文档、拉低检索质量。保留开关与代码，等有更丰富的 POI 文本来源时启用。
POI_INDEX_ENABLED = _env_str("POI_INDEX_ENABLED", "false").lower() in ("1", "true", "yes")
POI_INDEX_CONCURRENCY = int(_env_str("POI_INDEX_CONCURRENCY", "1"))

# ---------- Agent（阶段 3）----------
# Agent 循环用的模型。留空则继承 LLM_MODEL。
# 拆出来是因为"会调工具的模型"与"只做抽取的模型"常常选得不一样。
LLM_MODEL_AGENT = _env_str("LLM_MODEL_AGENT", "") or MODEL
# 廉价模型（抽取、摘要这类不要求推理能力的活），留空则继承 LLM_MODEL
LLM_MODEL_CHEAP = _env_str("LLM_MODEL_CHEAP", "") or MODEL
# 工具调用总开关：auto=按模型能力自动判定；on=强制开启；off=关闭（走确定性兜底）
LLM_TOOLS_ENABLED = _env_str("LLM_TOOLS_ENABLED", "auto").lower()
# Agent 主循环上限：轮数 / 单轮工具调用数 / 总时长（秒）/ 累计 token
AGENT_MAX_ROUNDS = int(_env_str("AGENT_MAX_ROUNDS", "6"))
AGENT_MAX_TOOL_CALLS = int(_env_str("AGENT_MAX_TOOL_CALLS", "8"))
AGENT_MAX_SECONDS = int(_env_str("AGENT_MAX_SECONDS", "50"))
AGENT_MAX_TOKENS = int(_env_str("AGENT_MAX_TOKENS", "24000"))

# ---------- 短信验证码 ----------# dev = 不真发短信，验证码写入日志；后续接阿里云/腾讯云只需新增 provider
SMS_PROVIDER = os.getenv("SMS_PROVIDER", "dev")
# 验证码有效期（秒）
SMS_CODE_TTL = int(os.getenv("SMS_CODE_TTL", "300"))
# 同一手机号两次发送的最小间隔（秒）
SMS_SEND_INTERVAL = int(os.getenv("SMS_SEND_INTERVAL", "60"))
# 同一手机号每小时最多发送次数
SMS_HOURLY_LIMIT = int(os.getenv("SMS_HOURLY_LIMIT", "10"))
# 单个验证码最多允许校验失败次数，超过即作废
SMS_MAX_ATTEMPTS = int(os.getenv("SMS_MAX_ATTEMPTS", "5"))
# dev 模式下是否把验证码回显到接口响应里（仅本地调试用，生产必须 false）
SMS_DEBUG_ECHO = os.getenv("SMS_DEBUG_ECHO", "true").lower() in ("1", "true", "yes")

# 手机号正则：中国大陆 11 位
PHONE_PATTERN = r"^1[3-9]\d{9}$"
# 昵称长度上限
NICKNAME_MAX_LEN = 20


def amap_enabled() -> bool:
    """高德 Key 是否已配置。"""
    return bool(AMAP_KEY)
# ---------- 图片地标识别（Qwen-VL）----------
#
# 视觉模型与对话模型**分开配置**，理由同 RAG 的 embedding：
# DeepSeek 只提供对话，没有多模态接口。这里默认指向阿里云百炼的
# OpenAI 兼容端点，换成任何 OpenAI 兼容的视觉服务都能用。
#
# 没配 VISION_API_KEY 时，/api/vision/recognize 会**降级**返回
# degraded=True，而不是报 500 —— 让前端提示"请手动输入"。
# 默认复用 LLM_API_KEY：实测 deepseek-flash 支持图像理解（v4-pro 不支持）
VISION_API_KEY = _env_str("VISION_API_KEY", "") or _env_str("LLM_API_KEY", "")
VISION_BASE_URL = _env_str("VISION_BASE_URL", "") or _env_str(
    "LLM_BASE_URL", "https://api.deepseek.com")
# **必须是 deepseek-flash**：实测 deepseek-v4-pro 会明确回答"我无法查看这张照片"。
VISION_MODEL = _env_str("VISION_MODEL", "deepseek-flash")
# 实测：1MB base64 的攻略图 + 思考模型，90 秒会超时（httpx.ReadTimeout，
# 且 str(exc) 是空串，日志里看不出原因）。给到 180 秒。
VISION_TIMEOUT = float(_env_str("VISION_TIMEOUT", "180"))
# 重试次数：实测视觉请求会被服务端随机断连（RemoteProtocolError），
# 同一个请求连续 4 次失败、第 5 次立刻成功。所以必须重试。
VISION_RETRIES = int(_env_str("VISION_RETRIES", "3"))
# 攻略图单次最多提取多少个地点（多了高德反查会很慢，也很容易超时）
MAX_GUIDE_PLACES = int(_env_str("MAX_GUIDE_PLACES", "20"))
# 单张图上限：太大既慢又贵，8MB 足够手机直出
VISION_MAX_BYTES = int(_env_str("VISION_MAX_BYTES", str(8 * 1024 * 1024)))
