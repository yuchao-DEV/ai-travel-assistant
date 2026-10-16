"""安全工具：手机号校验、验证码哈希、登录 token 生成。

- 验证码与 token 都只保存"不可逆"形态或随机串，库里不存明文验证码；
- 比较哈希时用 ``hmac.compare_digest`` 防时序侧信道。
"""
import hashlib
import hmac
import re
import secrets

from app.config import PHONE_PATTERN

PHONE_RE = re.compile(PHONE_PATTERN)

# token 熵：32 字节 → 43 字符 urlsafe 串
TOKEN_RANDOM_BYTES = 32
# 验证码位数
CODE_DIGITS = 6
# 验证码取值上限（含前导零，全部补足 6 位）
_CODE_MODULUS = 10 ** CODE_DIGITS


def is_valid_phone(phone: str) -> bool:
    """校验中国大陆手机号（11 位，1[3-9] 开头）。"""
    return bool(phone) and bool(PHONE_RE.match(phone.strip()))


def normalize_phone(phone: str) -> str:
    """去掉空格等噪声字符。"""
    return re.sub(r"\s+", "", phone or "")


def generate_token() -> str:
    """生成登录 token（URL 安全随机串）。"""
    return secrets.token_urlsafe(TOKEN_RANDOM_BYTES)


def generate_code() -> str:
    """生成 6 位数字验证码，使用密码学安全随机源。"""
    return str(secrets.randbelow(_CODE_MODULUS)).zfill(CODE_DIGITS)


def hash_code(phone: str, code: str) -> str:
    """验证码哈希：绑定手机号，避免同一验证码跨号复用。"""
    raw = f"{phone}:{code}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def verify_code_hash(phone: str, code: str, expected_hash: str) -> bool:
    """常数时间比较验证码哈希。"""
    return hmac.compare_digest(hash_code(phone, code), expected_hash or "")


def mask_phone(phone: str) -> str:
    """手机号脱敏，用于日志输出：13800001111 → 138****1111。"""
    if not phone or len(phone) < 7:
        return "***"
    return f"{phone[:3]}****{phone[-4:]}"


def default_nickname(phone: str) -> str:
    """默认昵称：旅行者 + 手机号后四位。"""
    suffix = phone[-4:] if len(phone) >= 4 else phone
    return f"旅行者{suffix}"
# ==================================================================
# 密码哈希（账号密码登录）
# ==================================================================
#
# 只用标准库 pbkdf2_hmac，不引入 bcrypt/argon2 依赖。
# 存储格式：``pbkdf2$<迭代次数>$<salt_hex>$<hash_hex>``
#   - 迭代次数写进字符串，以后调高阈值时**老密码仍可验证**（平滑升级）
#   - 每个用户独立随机 salt，防彩虹表
#
# 为什么不存明文：数据库文件会随项目提交/拷贝，明文密码一旦泄露，
# 用户在别处的同密码账号也会被撞库。

import hashlib as _hashlib
import hmac as _hmac
import secrets as _secrets

PASSWORD_MIN_LEN = 6
PASSWORD_MAX_LEN = 64
PBKDF2_ITERATIONS = 200_000


def hash_password(password: str) -> str:
    """把明文密码转成可存储的哈希串。"""
    salt = _secrets.token_bytes(16)
    digest = _hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                                  salt, PBKDF2_ITERATIONS)
    return f"pbkdf2${PBKDF2_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """校验密码。存的格式不对/为空时一律返回 False，不抛错。"""
    if not password or not stored:
        return False
    try:
        algo, iter_s, salt_hex, hash_hex = stored.split("$")
        if algo != "pbkdf2":
            return False
        iterations = int(iter_s)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(hash_hex)
    except (ValueError, AttributeError):
        return False
    digest = _hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                                  salt, iterations)
    # 恒定时间比较，避免时序侧信道
    return _hmac.compare_digest(digest, expected)


def normalize_username(raw: str) -> str:
    """账号归一：去空白 + 转小写（大小写不敏感，避免"Alice"/"alice"撞车）。"""
    return (raw or "").strip().lower()


_USERNAME_RE = re.compile(r"^[a-z0-9_\u4e00-\u9fa5]{3,20}$")


def is_valid_username(username: str) -> bool:
    """账号规则：3–20 位，字母/数字/下划线/中文。"""
    return bool(_USERNAME_RE.match(username or ""))
