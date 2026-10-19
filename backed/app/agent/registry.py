"""工具注册表：``@tool`` 装饰器 + 签名反射生成 JSON Schema + 执行守卫。

**schema 从函数签名反射生成，绝不手写第二份。** 手写 schema 与实现漂移是
Agent 项目最典型的故障：模型照着 schema 传参、函数却按另一套签名解析，
表现为"工具总是失败"，而且极难定位。反射生成则保证两者永远一致。

**``execute()`` 的四道守卫**（任何一道都返回 :class:`ToolResult` 而不是抛异常）：

1. **白名单**：名字不在注册表里 → ``unknown_tool``。模型编造工具名是常见的，
   绝不能让它落到任意可调用对象上。
2. **参数校验**：用 Pydantic 按签名校验，非法入参 → ``invalid_args`` 并把
   校验错误回给模型，让它自己改参数重试。
3. **身份**：``requires_user=True`` 的工具在未登录时 → ``needs_login``。
4. **超时**：执行超过 ``timeout`` → ``timeout``。外部服务（高德/LLM）可能挂起，
   不能让一个工具把整个会话拖死。
"""
import asyncio
import inspect
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence

from pydantic import BaseModel, create_model

from app.agent.results import ToolErrorCode, ToolResult

logger = logging.getLogger("app.agent.registry")


@dataclass
class ToolSpec:
    """一个已注册的工具。"""
    name: str
    description: str
    parameters: Dict[str, Any]                 # JSON Schema
    func: Callable[..., Any]
    requires_user: bool = False
    timeout: float = 20.0
    core: bool = False                          # 是否属于 CORE_TOOLS（首轮挂载）
    is_async: bool = True
    arg_model: Optional[type] = None            # 动态生成的 Pydantic 模型

    def openai_schema(self) -> Dict[str, Any]:
        """OpenAI ``tools`` 参数里的单条定义。"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


_REGISTRY: Dict[str, ToolSpec] = {}


# ---------------------------------------------------------------- 反射

def _build_arg_model(func: Callable[..., Any], name: str) -> type:
    """从函数签名动态生成 Pydantic 模型（同时用于参数校验与 schema 生成）。"""
    fields: Dict[str, Any] = {}
    for pname, param in inspect.signature(func).parameters.items():
        if param.kind in (param.VAR_POSITIONAL, param.VAR_KEYWORD):
            continue
        annotation = param.annotation
        if annotation is inspect.Parameter.empty:
            annotation = str
        if param.default is inspect.Parameter.empty:
            fields[pname] = (annotation, ...)
        else:
            fields[pname] = (annotation, param.default)
    return create_model(f"{name}_Args", **fields)


def _flatten_optional(prop: Dict[str, Any]) -> Dict[str, Any]:
    """把 ``anyOf: [{type: X}, {type: null}]`` 压回 ``{type: X}``。

    Pydantic 对 ``X | None`` 会生成 anyOf 结构，但**越简单的 schema 模型越不容易
    传错参**。可选性已经由 ``required`` 列表表达了，这里再带一个 null 分支只会
    增加模型的困惑。只有一个非 null 分支时才压平；多分支（真正的联合类型）保持原样。
    """
    any_of = prop.get("anyOf")
    if not isinstance(any_of, list):
        return prop
    non_null = [b for b in any_of if b.get("type") != "null"]
    if len(non_null) != 1:
        return prop
    merged = dict(non_null[0])
    # 保留原有的 description/default 等
    for key, value in prop.items():
        if key != "anyOf":
            merged.setdefault(key, value)
    return merged


def _schema_from_model(model: type) -> Dict[str, Any]:
    """Pydantic 模型 → 给模型看的 JSON Schema（去掉噪音字段、压平 Optional）。"""
    schema = model.model_json_schema()
    schema.pop("title", None)
    props = schema.get("properties") or {}
    for key, prop in list(props.items()):
        prop = _flatten_optional(prop)
        prop.pop("title", None)
        props[key] = prop
    return schema


def _first_doc_line(func: Callable[..., Any]) -> str:
    doc = inspect.getdoc(func) or ""
    for line in doc.splitlines():
        line = line.strip()
        if line:
            return line
    return func.__name__


# ---------------------------------------------------------------- 装饰器

def tool(
    name: Optional[str] = None,
    *,
    description: Optional[str] = None,
    requires_user: bool = False,
    timeout: float = 20.0,
    core: bool = False,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """把一个函数注册成 Agent 工具。

    ``description`` 不传时取 docstring 第一行；``parameters`` 永远由签名反射生成。
    """
    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        tool_name = name or func.__name__
        model = _build_arg_model(func, tool_name)
        spec = ToolSpec(
            name=tool_name,
            description=description or _first_doc_line(func),
            parameters=_schema_from_model(model),
            func=func,
            requires_user=requires_user,
            timeout=timeout,
            core=core,
            is_async=asyncio.iscoroutinefunction(func),
            arg_model=model,
        )
        if tool_name in _REGISTRY:
            logger.warning("工具重名，后注册的覆盖先前定义：%s", tool_name)
        _REGISTRY[tool_name] = spec
        return func
    return decorator


# ---------------------------------------------------------------- 查询

def get(name: str) -> Optional[ToolSpec]:
    return _REGISTRY.get(name)


def all_tools() -> Dict[str, ToolSpec]:
    return dict(_REGISTRY)


def specs(names: Optional[Sequence[str]] = None) -> List[ToolSpec]:
    """按名字取工具规格；``names=None`` 返回全部。"""
    if names is None:
        return list(_REGISTRY.values())
    return [_REGISTRY[n] for n in names if n in _REGISTRY]


def schemas(names: Optional[Sequence[str]] = None) -> List[Dict[str, Any]]:
    """生成 OpenAI ``tools`` 参数。只传白名单里的名字，未知名字静默跳过。"""
    return [spec.openai_schema() for spec in specs(names)]


def core_names() -> List[str]:
    return [n for n, s in _REGISTRY.items() if s.core]


def clear() -> None:
    """清空注册表（测试用）。"""
    _REGISTRY.clear()


# ---------------------------------------------------------------- 执行

async def execute(
    name: str,
    args: Dict[str, Any],
    *,
    user_id: Optional[int] = None,
    timeout: Optional[float] = None,
) -> ToolResult:
    """执行一个工具，四道守卫全在这里。

    **永远返回 ToolResult，永远不抛异常**——主循环因此只需要一条路径。
    """
    started = time.monotonic()

    # 守卫 1：白名单
    spec = _REGISTRY.get(name)
    if spec is None:
        return ToolResult.failure(
            f"没有名为「{name}」的工具",
            code=ToolErrorCode.UNKNOWN_TOOL,
            hint=f"可用的工具：{', '.join(sorted(_REGISTRY))}",
        )

    # 守卫 3：身份
    if spec.requires_user and not user_id:
        return ToolResult.failure(
            f"工具「{name}」需要登录后才能使用",
            code=ToolErrorCode.NEEDS_LOGIN,
            hint="请先引导用户登录，或换用不需要登录的工具继续",
        )

    # 守卫 2：参数校验
    try:
        validated = spec.arg_model(**(args or {}))
    except Exception as exc:
        return ToolResult.failure(
            f"参数不合法：{exc}",
            code=ToolErrorCode.INVALID_ARGS,
            hint="请按工具的 JSON Schema 修正参数后重试",
        )
    kwargs = validated.model_dump()

    # 守卫 4：超时
    limit = timeout or spec.timeout
    try:
        if spec.is_async:
            result = await asyncio.wait_for(spec.func(**kwargs), timeout=limit)
        else:
            result = await asyncio.wait_for(
                asyncio.to_thread(spec.func, **kwargs), timeout=limit)
    except asyncio.TimeoutError:
        return ToolResult.failure(
            f"工具「{name}」执行超过 {limit:g} 秒",
            code=ToolErrorCode.TIMEOUT,
            hint="可以换用更简单的查询，或基于已有信息继续回答",
        )
    except Exception as exc:                       # 工具内部异常也要收成结果
        logger.warning("工具 %s 执行异常：%s", name, exc, exc_info=True)
        return ToolResult.failure(
            f"工具「{name}」执行失败：{exc}",
            code=ToolErrorCode.INTERNAL,
            hint="可以换个思路或换用其他工具",
        )

    if not isinstance(result, ToolResult):
        # 工具作者忘了返回 ToolResult：包成成功结果而不是崩掉
        result = ToolResult.success(result)
    result.elapsed_ms = int((time.monotonic() - started) * 1000)
    return result
