"""SQLite 数据访问层：连接管理 + 建表 + 时间工具。

设计约定：
- 只用标准库 ``sqlite3``，不引入 ORM，保持项目"依赖极简"的风格；
- sqlite3 连接不是线程安全的，FastAPI 的异步路由会跑在线程池里，
  因此这里**每次操作新建连接**、用完即关（SQLite 本地文件开销极低）；
- 明文时间戳统一为 ISO-8601 UTC 字符串（便于前端展示），
  而过期时间统一存 **epoch 秒整数**（便于直接和当前时间做数值比较，避免格式不一致）。
"""
import logging
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator, Optional

from app.config import DB_PATH

logger = logging.getLogger("app.db")

# ---------------------------------------------------------------- 时间工具

def utc_now_iso() -> str:
    """当前 UTC 时间，ISO-8601 字符串（秒级精度）。"""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def utc_iso_offset(seconds: int) -> str:
    """相对当前时间偏移 ``seconds`` 秒后的 ISO-8601 UTC 字符串。

    因为所有时间戳都由 :func:`utc_now_iso` 生成、格式完全一致（含 ``+00:00`` 后缀），
    所以可以直接在 SQL 里用字符串比较做时间范围筛选。
    """
    moment = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(seconds=seconds)
    return moment.isoformat()


def iso_to_epoch(value: str) -> int:
    """把 :func:`utc_now_iso` 产出的字符串转回 epoch 秒。"""
    return int(datetime.fromisoformat(value).timestamp())


def epoch_now() -> int:
    """当前 UTC 时间的 epoch 秒。"""
    return int(datetime.now(timezone.utc).timestamp())


def epoch_after(seconds: int) -> int:
    """当前时间往后 ``seconds`` 秒的 epoch 秒。"""
    return epoch_now() + seconds


# ---------------------------------------------------------------- 建表 DDL

SCHEMA_SQL = """
-- 用户表：手机号即账号，无密码（验证码登录）
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    -- 账号（3–20 位，归一为小写后存储；中文也允许）
    username      TEXT    NOT NULL UNIQUE,
    -- 密码哈希：pbkdf2$<迭代>$<salt_hex>$<hash_hex>。
    -- 为 NULL 表示旧手机号账号，未设置密码 → 无法登录，需重新注册
    password_hash TEXT,
    nickname      TEXT,
    avatar        TEXT,
    created_at    TEXT    NOT NULL,   -- ISO-8601 UTC
    last_login_at TEXT                -- ISO-8601 UTC
);

-- 短信验证码表：只存哈希，不存明文
CREATE TABLE IF NOT EXISTS sms_codes (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    phone      TEXT    NOT NULL,
    scene      TEXT    NOT NULL,      -- register / login / reset
    code_hash  TEXT    NOT NULL,      -- sha256(phone + ':' + code)
    expires_at INTEGER NOT NULL,      -- epoch 秒
    used       INTEGER NOT NULL DEFAULT 0,
    attempts   INTEGER NOT NULL DEFAULT 0,  -- 校验失败次数，超限即作废
    created_at TEXT    NOT NULL       -- ISO-8601 UTC
);
CREATE INDEX IF NOT EXISTS idx_sms_codes_phone
    ON sms_codes (phone, created_at DESC);

-- 登录态表：不透明 token（随机串），服务端可主动作废
CREATE TABLE IF NOT EXISTS auth_tokens (
    token      TEXT    PRIMARY KEY,
    user_id    INTEGER NOT NULL,
    expires_at INTEGER NOT NULL,      -- epoch 秒
    created_at TEXT    NOT NULL,      -- ISO-8601 UTC
    FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_auth_tokens_user
    ON auth_tokens (user_id);

-- 历史行程表：整份 ItineraryVO 以 JSON 原文落库，
-- 避免结构化列随算法演进频繁改表；索引列只用于列表页展示与筛选。
CREATE TABLE IF NOT EXISTS itineraries (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id        INTEGER NOT NULL,
    client_id      TEXT,              -- 前端本地记录的 id，用于幂等上传
    title          TEXT,
    origin         TEXT,
    city           TEXT    NOT NULL,
    days           INTEGER NOT NULL,
    budget         REAL    NOT NULL,
    tags           TEXT    NOT NULL DEFAULT '[]',   -- JSON 数组
    profile        TEXT    NOT NULL DEFAULT '经典',  -- 旧值为 舒适型，代码里做映射
    total_cost     REAL,
    request_json   TEXT    NOT NULL,  -- PlanRequestDTO 原文
    itinerary_json TEXT    NOT NULL,  -- ItineraryVO 原文
    created_at     TEXT    NOT NULL,  -- ISO-8601 UTC
    updated_at     TEXT    NOT NULL,  -- ISO-8601 UTC
    FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_itineraries_user_created
    ON itineraries (user_id, created_at DESC);
-- 同一用户下 client_id 唯一（client_id 为空时不参与约束）
CREATE UNIQUE INDEX IF NOT EXISTS uniq_itineraries_user_client
    ON itineraries (user_id, client_id) WHERE client_id IS NOT NULL;
"""

# ---------------------------------------------------------------- RAG 知识库
#
# 三张表与现有 4 张表**没有任何外键交叉**（doc_chunks → documents 是知识库内部关系），
# 因此整块 DROP 掉也不会影响用户、历史行程等既有数据（见落地清单附录 B 回滚方案）。

SCHEMA_RAG_SQL = """
-- 知识库文档：L1 POI / L2 城市攻略 / L3 通用经验 / L4 领域规则
CREATE TABLE IF NOT EXISTS documents (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_key      TEXT    NOT NULL UNIQUE,  -- 稳定标识，如 'city:杭州/10_transport'
    kind         TEXT    NOT NULL,         -- poi / city / general / rule
    city         TEXT,                     -- 通用与规则类为空
    category     TEXT,                     -- transport / food / notes ...
    title        TEXT    NOT NULL,
    source       TEXT,                     -- 来源说明（自撰整理 / 高德）
    source_url   TEXT,
    doc_date     TEXT,
    content_hash TEXT    NOT NULL,         -- 增量索引用：内容没变就跳过重建
    char_count   INTEGER NOT NULL DEFAULT 0,
    created_at   TEXT    NOT NULL,         -- ISO-8601 UTC
    updated_at   TEXT    NOT NULL          -- ISO-8601 UTC
);
CREATE INDEX IF NOT EXISTS idx_documents_city_kind ON documents (city, kind);
CREATE INDEX IF NOT EXISTS idx_documents_hash ON documents (content_hash);

-- 文档切分块（段落级）
CREATE TABLE IF NOT EXISTS doc_chunks (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id  INTEGER NOT NULL,
    doc_key      TEXT    NOT NULL,
    chunk_index  INTEGER NOT NULL,
    heading_path TEXT,                     -- 如 '杭州交通 > 机场接驳'
    content      TEXT    NOT NULL,
    tokens       TEXT    NOT NULL,         -- tokenize_zh 切好的空格分隔词串，供 FTS5 用
    char_count   INTEGER NOT NULL DEFAULT 0,
    city         TEXT,
    kind         TEXT,
    FOREIGN KEY (document_id) REFERENCES documents (id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_doc_chunks_doc ON doc_chunks (document_id, chunk_index);
CREATE INDEX IF NOT EXISTS idx_doc_chunks_city ON doc_chunks (city, kind);

-- 向量：一行一块。dim 冗余存一份，便于发现历史遗留的异维数据。
CREATE TABLE IF NOT EXISTS embeddings (
    chunk_id   INTEGER PRIMARY KEY,
    model      TEXT    NOT NULL,
    dim        INTEGER NOT NULL,
    vector     BLOB    NOT NULL,           -- float32 小端拼接
    created_at TEXT    NOT NULL,           -- ISO-8601 UTC
    FOREIGN KEY (chunk_id) REFERENCES doc_chunks (id) ON DELETE CASCADE
);

-- 结构化票价：供**规划**路径查用（与 RAG 检索解耦）。
--
-- 语料里的门票是自然语言（"参考票价 ¥12.5 - ¥25，共 3 种票可选"），
-- 直接拿去做预算算不了，所以入库时抽成结构化值，规划时按名称回查。
-- price 为 NULL 表示"语料里没有可解析价格"——这种要如实标为未知，
-- 不能拿占位数字冒充票价。
CREATE TABLE IF NOT EXISTS poi_prices (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    city       TEXT    NOT NULL,
    name       TEXT    NOT NULL,           -- 语料里的中文名
    name_norm  TEXT    NOT NULL,           -- 归一化名，用于从严匹配
    price      REAL,                       -- 元；NULL = 未知
    is_free    INTEGER NOT NULL DEFAULT 0, -- 语料明确写"免费"
    kind       TEXT,                       -- ticket/free/service_only/unknown
    note       TEXT,                       -- 给用户看的说明（如"仅有付费服务，非门票费用"）
    price_text TEXT,                       -- 原始门票文本
    source     TEXT,                       -- 来源说明
    updated_at TEXT    NOT NULL            -- ISO-8601 UTC
);
CREATE INDEX IF NOT EXISTS idx_poi_prices_city_norm ON poi_prices (city, name_norm);
"""


SCHEMA_AGENT_SQL = """
-- Agent 工具调用留痕（阶段 3）。
-- 记全每次调用的参数、结果、耗时：排障时"模型到底查了什么"全靠它，
-- 而且这是评估 Agent 行为（调了几次、哪些工具失败）的唯一数据来源。
CREATE TABLE IF NOT EXISTS tool_calls (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id     TEXT    NOT NULL,        -- 一次会话
    user_id        INTEGER,                 -- 匿名时为 NULL
    round          INTEGER NOT NULL,        -- 第几轮循环
    tool_name      TEXT    NOT NULL,
    arguments      TEXT,                    -- JSON 原文（截断到 2000 字符）
    result_ok      INTEGER NOT NULL DEFAULT 0,
    result_summary TEXT,                    -- 一句话摘要，不存完整结果
    error          TEXT,
    code           TEXT,                    -- ToolErrorCode
    is_repeat      INTEGER NOT NULL DEFAULT 0,  -- 是否为重复调用（未真正执行）
    elapsed_ms     INTEGER NOT NULL DEFAULT 0,
    created_at     TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tool_calls_session ON tool_calls (session_id, id);
CREATE INDEX IF NOT EXISTS idx_tool_calls_created ON tool_calls (created_at);

-- 会话（阶段 4）。匿名会话 user_id 为 NULL，登录后归到用户名下。
CREATE TABLE IF NOT EXISTS agent_sessions (
    id            TEXT    PRIMARY KEY,        -- uuid hex
    user_id       INTEGER,                    -- 匿名时为 NULL
    title         TEXT,                       -- 取首条用户消息的前若干字
    created_at    TEXT    NOT NULL,
    updated_at    TEXT    NOT NULL,
    message_count INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_agent_sessions_user
    ON agent_sessions (user_id, updated_at DESC);

-- 会话消息。工具调用单独记在 tool_calls 表；这里只存对话与最终答复。
CREATE TABLE IF NOT EXISTS agent_messages (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT    NOT NULL,
    role       TEXT    NOT NULL,              -- user / assistant
    content    TEXT    NOT NULL,
    created_at TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_agent_messages_session
    ON agent_messages (session_id, id);

-- 长期画像（阶段 4）。
-- **原则**：画像只作"建议"，与本次显式输入冲突时一律以本次为准。
-- 这条纪律靠提示词写法保证（见 app/agent/memory.py 的 render_profile_prompt），
-- 不是靠这张表——表里只管存。
CREATE TABLE IF NOT EXISTS user_profiles (
    user_id     INTEGER PRIMARY KEY,
    pace        TEXT,                         -- 出行节奏倾向
    budget_band TEXT,                         -- 预算档位：低 / 中 / 高
    companions  TEXT,                         -- JSON 数组
    dietary     TEXT,                         -- JSON 数组
    interests   TEXT,                         -- JSON 对象（标签 → 权重）
    confidence  REAL    NOT NULL DEFAULT 0,   -- 0-1，随样本量上升
    sample_size INTEGER NOT NULL DEFAULT 0,
    updated_at  TEXT    NOT NULL
);
"""


def _ensure_poi_price_columns(conn: sqlite3.Connection) -> None:
    """给已存在的 poi_prices 补列（幂等）。

    这张表完全是导入脚本的派生产物，但重建要重跑导入，所以用 ALTER 增量补列更省事。
    """
    cols = {row[1] for row in conn.execute("PRAGMA table_info(poi_prices)").fetchall()}
    if not cols:
        return
    for column in ("kind", "note"):
        if column not in cols:
            conn.execute(f"ALTER TABLE poi_prices ADD COLUMN {column} TEXT")

# FTS5 外部内容表：不重复存正文，靠 rowid 指回 doc_chunks。
#
# **中文检索必须走 tokens 列**：FTS5 自带的 unicode61 分词器会把一整串中文
# 当成单个 token（"杭州西湖" → 一个词），直接索引 content 是检索不出东西的。
# tokens 列存的是 tokenize_zh 切好的空格分隔词串，unicode61 按空格切正好合适。
FTS5_SQL = """
CREATE VIRTUAL TABLE doc_chunks_fts USING fts5(
    content,
    tokens,
    content='doc_chunks',
    content_rowid='id',
    tokenize='unicode61'
);
"""

# 外部内容表的同步触发器：必须覆盖 INSERT / UPDATE / DELETE 三种。
# 删除与更新都要先用 FTS5 的 'delete' 命令**显式从倒排索引里摘除旧行**，
# 否则倒排索引会与主表不一致——而且是**静默**不一致，检索结果会莫名其妙。
FTS5_TRIGGERS_SQL = """
CREATE TRIGGER IF NOT EXISTS doc_chunks_ai AFTER INSERT ON doc_chunks BEGIN
    INSERT INTO doc_chunks_fts(rowid, content, tokens)
    VALUES (new.id, new.content, new.tokens);
END;

CREATE TRIGGER IF NOT EXISTS doc_chunks_ad AFTER DELETE ON doc_chunks BEGIN
    INSERT INTO doc_chunks_fts(doc_chunks_fts, rowid, content, tokens)
    VALUES ('delete', old.id, old.content, old.tokens);
END;

CREATE TRIGGER IF NOT EXISTS doc_chunks_au AFTER UPDATE ON doc_chunks BEGIN
    INSERT INTO doc_chunks_fts(doc_chunks_fts, rowid, content, tokens)
    VALUES ('delete', old.id, old.content, old.tokens);
    INSERT INTO doc_chunks_fts(rowid, content, tokens)
    VALUES (new.id, new.content, new.tokens);
END;
"""

# 启动初始化只做一次，加锁避免多 worker 重复执行
_init_lock = threading.Lock()
_initialized = False


def _connect() -> sqlite3.Connection:
    """新建一个已配置好的连接。"""
    conn = sqlite3.connect(str(DB_PATH), timeout=10.0)
    conn.row_factory = sqlite3.Row
    # 外键约束是连接级开关，必须每次开启
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 10000")
    return conn


@contextmanager
def get_conn() -> Iterator[sqlite3.Connection]:
    """只读/单语句场景：拿一个连接，自动提交并关闭。"""
    conn = _connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


@contextmanager
def transaction() -> Iterator[sqlite3.Connection]:
    """写操作场景：显式事务，异常自动回滚。"""
    conn = _connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _ensure_fts(conn: sqlite3.Connection) -> bool:
    """建 FTS5 虚拟表与同步触发器（幂等）。

    **不能写成 ``CREATE VIRTUAL TABLE IF NOT EXISTS``**：SQLite 对虚拟表不支持
    ``IF NOT EXISTS``，重复执行会直接报错。所以先查 ``sqlite_master`` 再决定建不建。

    返回是否新建了 FTS 表（供日志使用）。FTS5 不可用（极少数编译选项缺失的
    SQLite）时返回 False 并记警告，检索会退化为 LIKE 匹配而不是整体失败。
    """
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='doc_chunks_fts'"
    ).fetchone()
    if exists:
        return False
    try:
        conn.executescript(FTS5_SQL)
        conn.executescript(FTS5_TRIGGERS_SQL)
    except sqlite3.OperationalError as exc:
        logger.warning("FTS5 不可用（检索将退化为 LIKE 匹配）：%s", exc)
        return False
    return True


def _migrate_users_to_account(conn: sqlite3.Connection) -> None:
    """把手机号版 users 表迁移成账号密码版（幂等）。

    判定依据是"有没有 username 列"，而不是手机号列是否还在 ——
    因为迁移后表里就没有 phone 列了。
    """
    cols = {row[1] for row in conn.execute("PRAGMA table_info(users)")}
    if not cols or "username" in cols:
        return   # 表不存在（刚建好就是新结构）或已迁移

    logger.info("迁移 users 表：手机号 → 账号密码（旧账号需重新注册设密码）")
    # PRAGMA foreign_keys 在事务内无效，必须先关再开
    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        conn.executescript("""
            CREATE TABLE users_new (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                username      TEXT    NOT NULL UNIQUE,
                password_hash TEXT,
                nickname      TEXT,
                avatar        TEXT,
                created_at    TEXT    NOT NULL,
                last_login_at TEXT
            );
            INSERT INTO users_new (id, username, password_hash, nickname,
                                   avatar, created_at, last_login_at)
            SELECT id, phone, NULL, nickname, avatar, created_at, last_login_at
            FROM users;
            DROP TABLE users;
            ALTER TABLE users_new RENAME TO users;
        """)
        conn.commit()
        logger.info("users 表迁移完成")
    finally:
        conn.execute("PRAGMA foreign_keys = ON")


def init_db() -> None:
    """首次启动建库建表（幂等）。"""
    global _initialized
    with _init_lock:
        if _initialized:
            return
        Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
        conn = _connect()
        try:
            # WAL 提升读写并发，且是持久化设置，设一次即可
            conn.execute("PRAGMA journal_mode = WAL")
            conn.executescript(SCHEMA_SQL)
            conn.executescript(SCHEMA_RAG_SQL)
            conn.executescript(SCHEMA_AGENT_SQL)
            _ensure_poi_price_columns(conn)
            # 手机号版 users → 账号密码版（幂等；旧账号会保留但需重设密码）
            _migrate_users_to_account(conn)
            _ensure_fts(conn)
            conn.commit()
        finally:
            conn.close()
        _initialized = True


def fts_available() -> bool:
    """FTS5 虚拟表是否已就绪。"""
    if not db_exists():
        return False
    try:
        with get_conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='doc_chunks_fts'"
            ).fetchone()
        return row is not None
    except sqlite3.Error:
        return False


def fts_row_count(conn: sqlite3.Connection) -> int:
    """FTS 倒排索引里的行数，用于断言"与 doc_chunks 一致"。"""
    return int(conn.execute("SELECT count(*) FROM doc_chunks_fts").fetchone()[0])


def db_exists() -> bool:
    """数据库文件是否已存在（用于状态展示）。"""
    return Path(DB_PATH).exists()


def table_names() -> Optional[list]:
    """列出当前库中的表名，供调试接口用。"""
    if not db_exists():
        return None
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
    return [r["name"] for r in rows]
