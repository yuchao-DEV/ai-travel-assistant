"""业务异常。

与项目既有的统一响应体保持一致：
``{ "success": false, "code": <int>, "message": <str>, "data": null }``

在 ``main.py`` 里注册一个 handler 即可，业务代码只管 raise。
"""
from typing import Optional


class ApiError(Exception):
    """可预期的业务错误（参数不合法、未登录、频率超限、资源不存在等）。"""

    def __init__(self, message: str, code: int = 400, status_code: Optional[int] = None):
        super().__init__(message)
        self.message = message
        # 业务码，客户端据此区分错误类型（400/401/404/409/429/500…）
        self.code = code
        # HTTP 状态码，默认与业务码一致
        self.status_code = status_code if status_code is not None else (
            code if 400 <= code <= 599 else 400
        )
