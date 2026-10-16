"""用户系统服务：验证码发送、手机号注册/登录、登录态校验与退出。

设计要点：
- 手机号即账号，**无密码**，凭证是短信验证码；
- 验证码只存哈希、一次性消费、带失败计数与频率限制；
- 登录态是**存库的不透明 token**，服务端可主动作废（退出登录即删行）；
- 所有写操作走显式事务，校验失败需要保留的计数（如验证码错误次数）
  在事务内正常返回、由调用方在事务外抛错，避免回滚掉计数。
"""
import logging
import sqlite3
from typing import Optional

from app import security
from app.config import (
    NICKNAME_MAX_LEN,
    SMS_CODE_TTL,
    SMS_HOURLY_LIMIT,
    SMS_MAX_ATTEMPTS,
    SMS_SEND_INTERVAL,
    TOKEN_TTL_DAYS,
)
from app.db import (
    epoch_after,
    epoch_now,
    get_conn,
    iso_to_epoch,
    transaction,
    utc_iso_offset,
    utc_now_iso,
)
from app.errors import ApiError
from app.schemas import (
    SCENE_LOGIN,
    SCENE_REGISTER,
    AuthVO,
    LoginDTO,
    RegisterDTO,
    SendCodeDTO,
    SendCodeVO,
    UserVO,
    VALID_SMS_SCENES,
)
from app.sms import get_sms_provider

logger = logging.getLogger("app.auth")

TOKEN_TTL_SECONDS = TOKEN_TTL_DAYS * 86400


# ---------------------------------------------------------------- 内部工具

def _check_phone(raw: Optional[str]) -> str:
    """规范化并校验手机号，非法直接抛业务错误。"""
    phone = security.normalize_phone(raw or "")
    if not security.is_valid_phone(phone):
        raise ApiError("手机号格式不正确，请输入 11 位中国大陆手机号")
    return phone


def _check_scene(raw: Optional[str]) -> str:
    scene = (raw or SCENE_LOGIN).strip()
    if scene not in VALID_SMS_SCENES:
        raise ApiError(f"不支持的验证码场景: {scene}，可选 {VALID_SMS_SCENES}")
    return scene


def user_to_vo(row: sqlite3.Row) -> UserVO:
    """数据库行 → 对外用户信息（手机号脱敏）。"""
    return UserVO(
        id=row["id"],
        username=row["username"],
        nickname=row["nickname"],
        avatar=row["avatar"],
        created_at=row["created_at"],
        last_login_at=row["last_login_at"],
    )


def _get_user_row(user_id: int) -> Optional[sqlite3.Row]:
    with get_conn() as conn:
        return conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()


def _issue_token(conn: sqlite3.Connection, user_id: int) -> tuple[str, int]:
    """在给定事务内签发一个登录 token（顺带清理该用户的过期 token）。"""
    token = security.generate_token()
    now = epoch_now()
    conn.execute(
        "DELETE FROM auth_tokens WHERE user_id = ? AND expires_at <= ?", (user_id, now)
    )
    conn.execute(
        "INSERT INTO auth_tokens (token, user_id, expires_at, created_at) VALUES (?, ?, ?, ?)",
        (token, user_id, epoch_after(TOKEN_TTL_SECONDS), utc_now_iso()),
    )
    return token, TOKEN_TTL_SECONDS


def _consume_code(phone: str, code: str, scene: str) -> Optional[str]:
    """校验并消费验证码。

    成功返回 ``None``；失败返回错误文案。
    失败时对错误次数的累加会**正常提交**（因此这里不抛异常、把抛错交给调用方）。
    """
    now = epoch_now()
    with transaction() as conn:
        row = conn.execute(
            "SELECT id, code_hash, attempts FROM sms_codes "
            "WHERE phone = ? AND scene = ? AND used = 0 AND expires_at > ? "
            "ORDER BY id DESC LIMIT 1",
            (phone, scene, now),
        ).fetchone()
        if row is None:
            return "验证码不存在或已过期，请重新获取"
        if row["attempts"] >= SMS_MAX_ATTEMPTS:
            return "验证码错误次数过多，请重新获取"

        if not security.verify_code_hash(phone, code, row["code_hash"]):
            conn.execute(
                "UPDATE sms_codes SET attempts = attempts + 1 WHERE id = ?", (row["id"],)
            )
            left = SMS_MAX_ATTEMPTS - row["attempts"] - 1
            return f"验证码不正确，还可尝试 {max(left, 0)} 次"

        # 一次性消费
        conn.execute("UPDATE sms_codes SET used = 1 WHERE id = ?", (row["id"],))
        return None


# ---------------------------------------------------------------- 对外能力

async def send_code(dto: SendCodeDTO) -> SendCodeVO:
    """下发验证码（带账号状态检查与频率限制）。"""
    phone = _check_phone(dto.phone)
    scene = _check_scene(dto.scene)

    with get_conn() as conn:
        exists = conn.execute(
            "SELECT id FROM users WHERE phone = ?", (phone,)
        ).fetchone() is not None

    if scene == SCENE_REGISTER and exists:
        raise ApiError("该手机号已注册，请直接登录", code=409)
    if scene == SCENE_LOGIN and not exists:
        raise ApiError("该手机号尚未注册，请先注册", code=404)

    # ---- 频率限制：最小间隔 + 每小时上限 ----
    with get_conn() as conn:
        last = conn.execute(
            "SELECT created_at FROM sms_codes WHERE phone = ? ORDER BY id DESC LIMIT 1",
            (phone,),
        ).fetchone()
        hour_count = conn.execute(
            "SELECT COUNT(*) AS c FROM sms_codes WHERE phone = ? AND created_at > ?",
            (phone, utc_iso_offset(-3600)),
        ).fetchone()["c"]

    if last is not None:
        elapsed = epoch_now() - iso_to_epoch(last["created_at"])
        if elapsed < SMS_SEND_INTERVAL:
            wait = max(SMS_SEND_INTERVAL - elapsed, 1)
            raise ApiError(f"发送过于频繁，请 {wait} 秒后再试", code=429)
    if hour_count >= SMS_HOURLY_LIMIT:
        raise ApiError("该手机号发送次数已达上限，请稍后再试", code=429)

    code = security.generate_code()
    # 同一手机号+场景下，旧验证码立即失效，避免多码并存
    with transaction() as conn:
        conn.execute(
            "UPDATE sms_codes SET used = 1 WHERE phone = ? AND scene = ? AND used = 0",
            (phone, scene),
        )
        conn.execute(
            "INSERT INTO sms_codes (phone, scene, code_hash, expires_at, used, attempts, created_at) "
            "VALUES (?, ?, ?, ?, 0, 0, ?)",
            (phone, scene, security.hash_code(phone, code), epoch_after(SMS_CODE_TTL), utc_now_iso()),
        )

    provider = get_sms_provider()
    try:
        sent = await provider.send(phone, code, scene)
    except NotImplementedError as exc:
        logger.error("短信通道未接入：%s", exc)
        raise ApiError(f"短信通道未接入：{exc}", code=501) from exc

    if not sent:
        raise ApiError("验证码发送失败，请稍后重试", code=502)

    logger.info("验证码已发送 场景=%s 手机号=%s", scene, security.mask_phone(phone))
    return SendCodeVO(
        phone=security.mask_phone(phone),
        scene=scene,
        expires_in=SMS_CODE_TTL,
        # 只有开发/模拟模式才回显，方便本地无短信通道时联调
        debug_code=code if provider.echoes_code else None,
    )


def register(dto: RegisterDTO) -> AuthVO:
    """账号密码注册：建号并直接登录。

    与旧的手机号方案不同，这里**不校验验证码**——账号是自选的，
    密码只有本人知道，不需要证明"手机号归属"。
    """
    username = security.normalize_username(dto.username)
    if not security.is_valid_username(username):
        raise ApiError("账号需 3–20 位，只能是字母、数字、下划线或中文")
    if len(dto.password) < security.PASSWORD_MIN_LEN:
        raise ApiError(f"密码至少 {security.PASSWORD_MIN_LEN} 位")
    if len(dto.password) > security.PASSWORD_MAX_LEN:
        raise ApiError(f"密码最长 {security.PASSWORD_MAX_LEN} 位")

    nickname = (dto.nickname or "").strip()
    if len(nickname) > NICKNAME_MAX_LEN:
        raise ApiError(f"昵称最长 {NICKNAME_MAX_LEN} 个字符")
    if not nickname:
        nickname = username

    with get_conn() as conn:
        if conn.execute("SELECT id FROM users WHERE username = ?",
                        (username,)).fetchone():
            raise ApiError("该账号已被占用，换一个试试", code=409)

    now = utc_now_iso()
    pwd_hash = security.hash_password(dto.password)
    try:
        with transaction() as conn:
            cur = conn.execute(
                "INSERT INTO users (username, password_hash, nickname, avatar,"
                " created_at, last_login_at) VALUES (?, ?, ?, ?, ?, ?)",
                (username, pwd_hash, nickname, dto.avatar or None, now, now),
            )
            user_id = cur.lastrowid
            token, expires_in = _issue_token(conn, user_id)
            row = conn.execute("SELECT * FROM users WHERE id = ?",
                               (user_id,)).fetchone()
    except sqlite3.IntegrityError as exc:
        # 并发注册时唯一索引兜底
        raise ApiError("该账号已被占用，换一个试试", code=409) from exc

    logger.info("新用户注册成功 id=%s 账号=%s", user_id, username)
    return AuthVO(token=token, expires_in=expires_in, user=user_to_vo(row))


def login(dto: LoginDTO) -> AuthVO:
    """账号密码登录。

    账号不存在与密码错误返回**同一条消息**——分开说等于告诉攻击者
    哪些账号是真实存在的（账号枚举）。
    """
    username = security.normalize_username(dto.username)
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM users WHERE username = ?",
                           (username,)).fetchone()

    # 旧手机号账号 password_hash 为 NULL，verify_password 会返回 False
    stored = row["password_hash"] if row else None
    if not security.verify_password(dto.password, stored or ""):
        if row is not None and not stored:
            raise ApiError("该账号是旧版手机号账号，尚未设置密码，请重新注册")
        raise ApiError("账号或密码错误", code=401)

    now = utc_now_iso()
    with transaction() as conn:
        conn.execute("UPDATE users SET last_login_at = ? WHERE id = ?",
                     (now, row["id"]))
        token, expires_in = _issue_token(conn, row["id"])
        row = conn.execute("SELECT * FROM users WHERE id = ?",
                           (row["id"],)).fetchone()

    logger.info("用户登录成功 id=%s 账号=%s", row["id"], username)
    return AuthVO(token=token, expires_in=expires_in, user=user_to_vo(row))


def authenticate(token: Optional[str]) -> Optional[sqlite3.Row]:
    """用 token 换用户行；无效或过期返回 None。"""
    if not token:
        return None
    with get_conn() as conn:
        return conn.execute(
            "SELECT u.* FROM auth_tokens t JOIN users u ON u.id = t.user_id "
            "WHERE t.token = ? AND t.expires_at > ?",
            (token, epoch_now()),
        ).fetchone()


def get_user(user_id: int) -> sqlite3.Row:
    """按 ID 取用户，不存在则抛 404。"""
    row = _get_user_row(user_id)
    if row is None:
        raise ApiError("用户不存在", code=404)
    return row


def logout(token: Optional[str]) -> None:
    """退出登录：删除服务端 token（幂等）。"""
    if not token:
        return
    with transaction() as conn:
        conn.execute("DELETE FROM auth_tokens WHERE token = ?", (token,))
    logger.info("用户退出登录")
