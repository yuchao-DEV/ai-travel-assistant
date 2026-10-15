"""大模型调用工具。

分两层：

1. **底层**：``chat_messages()`` / ``chat_stream_messages()``——直接对 messages
   数组操作，支持 ``tools`` / ``tool_choice`` / ``response_format``，返回结构化的
   :class:`LLMResponse`。Agent 循环用它（需要拿到完整的 ``tool_calls``）。
2. **便捷层**：``chat()`` / ``chat_stream()``——system + user 两段式，保持原有
   签名不变（**4 个现有调用点零改动**），内部转调底层实现。

关于流式：**首期不做流式工具调用**（见设计文档 §4.5）。主循环用非流式拿到完整的
``tool_calls``，规避分片 JSON 解析的复杂度；只在最终答复时用流式逐字输出。
"""
import json
import logging
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator, Dict, List, Optional

import httpx

from app.config import API_KEY, BASE_URL, MODEL

logger = logging.getLogger("app.llm")

# 超时配置（与 Java 版一致：connect 30s / read 300s / write 60s）
_TIMEOUT = httpx.Timeout(connect=30.0, read=300.0, write=60.0, pool=30.0)

# 采样温度默认值。抽成常量而不是散落在函数里，便于后续按场景调参时统一收口。
DEFAULT_TEMPERATURE = 0.7


@dataclass
class ToolCall:
    """模型请求调用的一次工具。"""
    id: str
    name: str
    arguments: Dict[str, Any] = field(default_factory=dict)
    raw_arguments: str = ""      # 原始 JSON 字符串，解析失败时便于排查


@dataclass
class LLMResponse:
    """一次非流式调用的完整结果。"""
    content: str = ""
    tool_calls: List[ToolCall] = field(default_factory=list)
    usage: Dict[str, Any] = field(default_factory=dict)
    raw: Dict[str, Any] = field(default_factory=dict)
    finish_reason: str = ""

    @property
    def has_tool_calls(self) -> bool:
        return bool(self.tool_calls)


def _build_messages(
    system_prompt: str,
    user_prompt: str,
    stream: bool,
    temperature: float = DEFAULT_TEMPERATURE,
    model: Optional[str] = None,
) -> dict:
    """构造两段式请求体（便捷层用）。

    ``model`` 传 None 时继承 :data:`app.config.MODEL`，因此调用方只在需要
    覆盖默认模型时才传值。
    """
    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": user_prompt})
    return {
        "model": model or MODEL,
        "messages": messages,
        "temperature": temperature,
        "stream": stream,
    }


def _headers(stream: bool = False) -> Dict[str, str]:
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {API_KEY}",
    }
    if stream:
        headers["Accept"] = "text/event-stream"
    return headers


def _parse_tool_calls(message: Dict[str, Any]) -> List[ToolCall]:
    """把响应里的 ``tool_calls`` 解析成 :class:`ToolCall` 列表。

    ``function.arguments`` 是 **JSON 字符串**。解析失败时**不抛异常**——
    把它留成空 dict 并由 ``raw_arguments`` 带上原文，让上层（Agent 循环）
    有机会把"参数不合法"作为工具结果回给模型让它重试，而不是整个请求崩掉。
    """
    out: List[ToolCall] = []
    for index, raw_call in enumerate(message.get("tool_calls") or []):
        if not isinstance(raw_call, dict):
            continue
        func = raw_call.get("function") or {}
        raw_args = func.get("arguments") or ""
        if isinstance(raw_args, dict):        # 少数服务端会直接给对象
            parsed, raw_args = raw_args, json.dumps(raw_args, ensure_ascii=False)
        else:
            try:
                parsed = json.loads(raw_args) if raw_args.strip() else {}
            except (json.JSONDecodeError, TypeError):
                parsed = {}
            if not isinstance(parsed, dict):
                parsed = {}
        out.append(ToolCall(
            id=raw_call.get("id") or f"call_{index}",
            name=(func.get("name") or "").strip(),
            arguments=parsed,
            raw_arguments=raw_args if isinstance(raw_args, str) else "",
        ))
    return out


def _extract_response(data: Dict[str, Any]) -> LLMResponse:
    """把 OpenAI 兼容响应转成 :class:`LLMResponse`。"""
    choices = data.get("choices") or []
    if not choices:
        return LLMResponse(usage=data.get("usage") or {}, raw=data)
    choice = choices[0]
    message = choice.get("message") or {}
    return LLMResponse(
        content=message.get("content") or "",
        tool_calls=_parse_tool_calls(message),
        usage=data.get("usage") or {},
        raw=data,
        finish_reason=choice.get("finish_reason") or "",
    )


async def chat_messages(
    messages: List[Dict[str, Any]],
    *,
    model: Optional[str] = None,
    temperature: float = DEFAULT_TEMPERATURE,
    tools: Optional[List[Dict[str, Any]]] = None,
    tool_choice: Optional[str] = None,
    response_format: Optional[Dict[str, Any]] = None,
    timeout: Optional[httpx.Timeout] = None,
    max_tokens: Optional[int] = None,
) -> LLMResponse:
    """底层非流式调用：传完整的 messages 数组，返回结构化响应。

    Agent 循环专用——它必须拿到完整的 ``tool_calls``，所以这里不做流式。
    """
    body: Dict[str, Any] = {
        "model": model or MODEL,
        "messages": messages,
        "temperature": temperature,
        "stream": False,
    }
    if tools:
        body["tools"] = tools
        # 不指定时默认 auto：让模型自己决定是否调用工具
        body["tool_choice"] = tool_choice or "auto"
    if response_format:
        body["response_format"] = response_format
    if max_tokens:
        body["max_tokens"] = max_tokens

    url = f"{BASE_URL}/v1/chat/completions"
    async with httpx.AsyncClient(timeout=timeout or _TIMEOUT) as client:
        resp = await client.post(url, json=body, headers=_headers())
        resp.raise_for_status()
        return _extract_response(resp.json())


async def chat(
    system_prompt: str,
    user_prompt: str,
    *,
    temperature: float = DEFAULT_TEMPERATURE,
    model: Optional[str] = None,
) -> str:
    """非流式对话，返回完整回复内容。

    ``temperature`` / ``model`` 都是**仅限关键字**的可选参数，且都有默认值，
    所以原有调用点的位置参数写法完全不受影响（``chat(sys, user)`` 照旧可用）。
    ``model`` 传 None 表示沿用配置里的默认模型。

    注意：``temperature`` 是否被采纳由模型决定，部分推理模型会忽略该字段；
    调用方需要按目标模型自行确认参数适用范围。
    """
    response = await chat_messages(
        _build_messages(system_prompt, user_prompt, stream=False,
                        temperature=temperature, model=model)["messages"],
        model=model, temperature=temperature,
    )
    return response.content


async def chat_stream_messages(
    messages: List[Dict[str, Any]],
    *,
    model: Optional[str] = None,
    temperature: float = DEFAULT_TEMPERATURE,
    tools: Optional[List[Dict[str, Any]]] = None,
    timeout: Optional[httpx.Timeout] = None,
) -> AsyncGenerator[str, None]:
    """底层流式调用：逐片 yield 文本（空内容已跳过）。

    只用于**最终答复**的逐字输出；带 ``tools`` 的流式工具调用首期不支持。
    """
    body: Dict[str, Any] = {
        "model": model or MODEL,
        "messages": messages,
        "temperature": temperature,
        "stream": True,
    }
    if tools:
        body["tools"] = tools

    url = f"{BASE_URL}/v1/chat/completions"
    async with httpx.AsyncClient(timeout=timeout or _TIMEOUT) as client:
        async with client.stream("POST", url, json=body, headers=_headers(True)) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line.startswith("data:"):
                    continue
                data_str = line[5:].strip()
                if not data_str or data_str == "[DONE]":
                    continue
                try:
                    chunk = json.loads(data_str)
                except json.JSONDecodeError:
                    continue
                choices = chunk.get("choices") or []
                if not choices:
                    continue
                delta = choices[0].get("delta") or {}
                content = delta.get("content")
                # 仅返回非空文本，跳过思考阶段的 null
                if isinstance(content, str) and content:
                    yield content


async def chat_stream(
    system_prompt: str,
    user_prompt: str,
    *,
    temperature: float = DEFAULT_TEMPERATURE,
    model: Optional[str] = None,
) -> AsyncGenerator[str, None]:
    """流式对话，逐个 yield 文本分片（空内容已跳过）。

    ``temperature`` / ``model`` 语义与 :func:`chat` 一致，同样只加关键字参数，
    保持签名向后兼容。
    """
    messages = _build_messages(system_prompt, user_prompt, stream=True,
                               temperature=temperature, model=model)["messages"]
    async for piece in chat_stream_messages(messages, model=model,
                                            temperature=temperature):
        yield piece
