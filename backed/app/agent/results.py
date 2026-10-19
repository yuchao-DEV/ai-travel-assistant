"""工具返回值契约。

**为什么要有统一契约**：Agent 循环里工具失败是常态（参数给错、外部服务挂了、
用户没登录），如果工具直接抛异常，主循环就得对每个工具写一遍 try/except。
统一成 :class:`ToolResult` 之后，主循环只有一条路径：把结果（无论成败）作为
工具消息回给模型，让它自己决定重试还是换思路。

三条不变量（由 ``__post_init__`` 强制）：

1. ``ok`` 与 ``error`` 互斥——成功必须无错误信息，失败必须有；
2. 失败时 ``data`` 必须为空——避免"失败了却还带着半截数据"欺骗上层；
3. ``is_itinerary`` 只可能在成功时为真。
"""
from dataclasses import dataclass, field
from typing import Any, ClassVar, Dict, List, Optional


class ToolErrorCode:
    """错误码常量。前端与日志按码分类，不靠解析中文文案。"""
    UNKNOWN_TOOL = "unknown_tool"       # 不在白名单
    INVALID_ARGS = "invalid_args"       # 参数不合法
    NEEDS_LOGIN = "needs_login"         # 该工具需要登录
    TIMEOUT = "timeout"                 # 执行超时
    RATE_LIMIT = "rate_limit"           # 配额/频率超限
    UPSTREAM = "upstream"               # 外部服务失败（高德等）
    NOT_FOUND = "not_found"             # 查询目标不存在
    INTERNAL = "internal"               # 其他内部错误


@dataclass
class ToolResult:
    """一次工具调用的结果。"""
    ok: bool
    data: Any = None
    error: str = ""
    code: str = ""
    hint: str = ""                       # 给**模型**的提示（不是给用户的）
    is_itinerary: bool = False
    elapsed_ms: int = 0
    extra: Dict[str, Any] = field(default_factory=dict)

    # 允许子类/调用方自定义的元字段白名单
    _ALLOWED_EXTRA: ClassVar[Optional[List[str]]] = None

    def __post_init__(self) -> None:
        if self.ok and self.error:
            raise ValueError("ToolResult 不变量：成功不能带 error")
        if not self.ok and not self.error:
            raise ValueError("ToolResult 不变量：失败必须有 error")
        if not self.ok and self.data not in (None, [], {}, ""):
            raise ValueError("ToolResult 不变量：失败不能带 data")
        if self.is_itinerary and not self.ok:
            raise ValueError("ToolResult 不变量：is_itinerary 只可能在成功时为真")

    # ---------------------------------------------------------- 构造快捷方式
    @classmethod
    def success(cls, data: Any = None, *, is_itinerary: bool = False,
                hint: str = "", **extra: Any) -> "ToolResult":
        return cls(ok=True, data=data, is_itinerary=is_itinerary,
                   hint=hint, extra=extra)

    @classmethod
    def failure(cls, error: str, *, code: str = ToolErrorCode.INTERNAL,
                hint: str = "", **extra: Any) -> "ToolResult":
        return cls(ok=False, error=error, code=code, hint=hint, extra=extra)

    # ---------------------------------------------------------- 序列化
    def to_dict(self) -> Dict[str, Any]:
        """给模型看的形式。失败时把 hint 一并给出，引导它下一步怎么做。"""
        out: Dict[str, Any] = {"ok": self.ok}
        if self.ok:
            out["data"] = self.data
        else:
            out["error"] = self.error
            if self.code:
                out["code"] = self.code
        if self.hint:
            out["hint"] = self.hint
        if self.extra:
            out.update(self.extra)
        return out

    def to_message(self, tool_call_id: str) -> Dict[str, Any]:
        """转成 OpenAI 兼容的 ``role=tool`` 消息。"""
        import json
        return {
            "role": "tool",
            "tool_call_id": tool_call_id,
            "content": json.dumps(self.to_dict(), ensure_ascii=False),
        }
