"""FastAPI 依赖：解析 ``Authorization: Bearer <token>`` 并提供当前登录用户。

这里刻意用同步 ``def`` 定义依赖：FastAPI 会把同步依赖放到线程池里执行，
而 SQLite 操作是阻塞式的，放线程池可以避免卡住事件循环。
"""
from typing import Any, Optional

from fastapi import Depends, Header

from app.errors import ApiError
from app.services import auth as auth_service


def extract_bearer(authorization: Optional[str]) -> Optional[str]:
    """从 Authorization 头里取出 Bearer token；格式不对返回 None。"""
    if not authorization:
        return None
    parts = authorization.split(None, 1)
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1].strip() or None
    return None


def current_token(authorization: Optional[str] = Header(default=None)) -> Optional[str]:
    """只取 token，不校验有效性（退出登录用）。"""
    return extract_bearer(authorization)


def current_user_optional(
    authorization: Optional[str] = Header(default=None),
) -> Optional[Any]:
    """可选登录：token 有效返回用户行，否则返回 None，不报错。"""
    return auth_service.authenticate(extract_bearer(authorization))


def current_user_required(user: Optional[Any] = Depends(current_user_optional)) -> Any:
    """必须登录：无有效 token 时抛 401。"""
    if user is None:
        raise ApiError("未登录或登录态已失效，请重新登录", code=401)
    return user
