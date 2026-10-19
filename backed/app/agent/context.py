"""Agent 运行上下文：身份、预算计数器、消息装配、上下文压缩。

**为什么用 contextvar 而不是给每个工具加 ``user_id`` 参数**：

工具的参数会被反射成 JSON Schema 发给模型，``user_id`` 一旦出现在 Schema 里，
模型就可能自己填一个（甚至编一个别人的 id）——那是越权。所以身份走
:func:`current_user_id` 从上下文读取，**永远不进 Schema**。

同时这里也负责"会话不该无限增长"：历史消息按预算裁剪，且**强制保留**关键事实
（城市/天数/预算/标签/节奏/必去/避免），否则压缩会把用户刚说的话丢掉。
"""
import contextvars
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from app.config import AGENT_MAX_TOKENS, LLM_MODEL_AGENT

logger = logging.getLogger("app.agent.context")

# 当前请求的登录用户 id。工具通过它取身份，不通过 JSON Schema。
_current_user_id: contextvars.ContextVar[Optional[int]] = contextvars.ContextVar(
    "agent_user_id", default=None)


def current_user_id() -> Optional[int]:
    """当前 Agent 请求的登录用户 id（匿名时为 None）。"""
    return _current_user_id.get()


def set_user_id(user_id: Optional[int]) -> contextvars.Token:
    """设置当前用户 id，返回可用于复原的 token。"""
    return _current_user_id.set(user_id)


def reset_user_id(token: contextvars.Token) -> None:
    try:
        _current_user_id.reset(token)
    except ValueError:      # 跨上下文 reset，忽略
        pass


# 压缩时必须保住的事实字段（丢了就会"忘记用户刚说的话"）
KEY_FACTS = ("city", "days", "budget", "tags", "profile", "must_go", "avoid")


@dataclass
class RunBudget:
    """一次会话的资源上限。任何一项耗尽都应终止循环走兜底。"""
    max_rounds: int
    max_tool_calls: int
    max_seconds: int
    max_tokens: int
    rounds: int = 0
    tool_calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0

    def add_usage(self, usage: Optional[Dict[str, Any]]) -> None:
        usage = usage or {}
        self.prompt_tokens += int(usage.get("prompt_tokens") or 0)
        self.completion_tokens += int(usage.get("completion_tokens") or 0)

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def exhausted(self) -> Optional[str]:
        """返回耗尽的维度名；还有余量时返回 None。"""
        if self.rounds >= self.max_rounds:
            return "max_rounds"
        if self.tool_calls >= self.max_tool_calls:
            return "max_tool_calls"
        if self.total_tokens >= self.max_tokens:
            return "max_tokens"
        return None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "rounds": self.rounds, "tool_calls": self.tool_calls,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
        }


@dataclass
class RunContext:
    """一次 Agent 会话的上下文。"""
    user_id: Optional[int] = None
    session_id: str = ""
    model: str = ""
    budget: Optional[RunBudget] = None
    facts: Dict[str, Any] = field(default_factory=dict)   # 从对话里累积的关键事实
    started_at: float = 0.0

    def __post_init__(self) -> None:
        if not self.model:
            self.model = LLM_MODEL_AGENT
        if self.budget is None:
            from app.config import (AGENT_MAX_ROUNDS, AGENT_MAX_TOOL_CALLS,
                                    AGENT_MAX_SECONDS)
            self.budget = RunBudget(max_rounds=AGENT_MAX_ROUNDS,
                                    max_tool_calls=AGENT_MAX_TOOL_CALLS,
                                    max_seconds=AGENT_MAX_SECONDS,
                                    max_tokens=AGENT_MAX_TOKENS)

    def note_facts(self, **kwargs: Any) -> None:
        """记录对话中出现的关键事实（只为压缩时保留，不参与决策）。"""
        for key in KEY_FACTS:
            value = kwargs.get(key)
            if value not in (None, "", [], {}):
                self.facts[key] = value


def sanitize_tool_pairs(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """保证 ``tool`` 消息一定有配对的 ``tool_calls``，否则整段丢弃。

    ## 为什么必须有这一步

    OpenAI 兼容接口的硬约束：

        Messages with role 'tool' must be a response to a preceding
        message with 'tool_calls'   （DeepSeek 400 invalid_request_error）

    而 ``compact_messages`` 按"最近 N 条"切分时，**很容易把一个工具调用组
    从中间切断**：assistant 的 ``tool_calls`` 落了进"更早"区间（content 为空
    被丢弃），而它后面的 ``tool`` 回复落在"最近"区间被保留。
    下一次请求就 400，整轮 Agent 退化成兜底文案。

    这里做一次确定性的收敛：
      - ``tool`` 消息的 ``tool_call_id`` 不在"当前已声明的 tool_calls"里 → 丢弃
      - assistant 声明的 tool_calls 若**一个回复都没有** → 该 assistant 也丢弃
        （只剩 tool_calls 却没有 tool 回复，同样是非法请求体）
      - 只回复了一部分的 → 把 assistant 的 tool_calls 裁剪成已回复的那几个
    """
    out: List[Dict[str, Any]] = []
    # 当前这一组 tool_calls 的 id 列表（按顺序）
    pending: List[str] = []
    # pending 对应的 tool 回复收集
    replies: Dict[str, Dict[str, Any]] = {}
    assistant_idx: Optional[int] = None

    def flush() -> None:
        """结算上一组：把 assistant 的 tool_calls 裁到只剩已回复的。"""
        nonlocal pending, replies, assistant_idx
        if assistant_idx is None:
            pending, replies, assistant_idx = [], {}, None
            return
        kept_ids = [i for i in pending if i in replies]
        if not kept_ids:
            # 一个都没回复 → 这条 assistant 也不能留
            out.pop(assistant_idx)
        else:
            msg = out[assistant_idx]
            calls = msg.get("tool_calls") or []
            msg["tool_calls"] = [c for c in calls if c.get("id") in kept_ids]
            if not msg["tool_calls"]:
                out.pop(assistant_idx)
            else:
                out.extend(replies[i] for i in kept_ids)
        pending, replies, assistant_idx = [], {}, None

    for msg in messages:
        role = msg.get("role")
        if role == "assistant" and msg.get("tool_calls"):
            flush()
            out.append(dict(msg))
            assistant_idx = len(out) - 1
            pending = [c.get("id") for c in msg["tool_calls"] if c.get("id")]
            continue
        if role == "tool":
            tcid = msg.get("tool_call_id")
            if tcid in pending:
                replies[tcid] = dict(msg)
            # 否则：孤立 tool 消息，直接丢弃
            continue
        flush()
        out.append(dict(msg))
    flush()
    return out


def compact_messages(messages: List[Dict[str, Any]],
                     facts: Optional[Dict[str, Any]] = None,
                     keep_recent: int = 8) -> List[Dict[str, Any]]:
    """裁剪历史消息，但**永不丢弃关键事实**。

    规则：
    - system 永远保留（提示词里有能力边界与口径，丢了会开始编造）；
    - 最近 ``keep_recent`` 条原样保留（模型需要看到最近的工具结果）；
    - 更早的消息只留"用户说了什么"，工具的大段返回结果丢弃（最占 token）；
    - ``facts`` 里累积的关键事实以一条 system 备注补回——这是压缩后不"失忆"的关键。

    注意：**不做 LLM 摘要**。摘要是又一次 LLM 调用，既慢又可能引入幻觉，
    而 Agent 循环本身就需要省下每一轮的时间预算。

    最后一定过一遍 :func:`sanitize_tool_pairs`——裁剪边界几乎必然落在某个
    工具调用组中间，不过这一遍就会生成非法的 ``tool`` 消息序列（见该函数说明）。
    """
    if len(messages) <= keep_recent + 2:
        return sanitize_tool_pairs(messages)

    head = [m for m in messages if m.get("role") == "system"][:1]
    tail = messages[-keep_recent:]
    # 关键：如果切点落在一个工具调用组中间，向前扩到组边界，
    # 否则 tail 开头会是一条孤立的 tool 消息。
    start = len(messages) - keep_recent
    while start > 0 and messages[start].get("role") == "tool":
        start -= 1
    tail = messages[start:]
    middle = messages[1:start]

    kept: List[Dict[str, Any]] = []
    for msg in middle:
        role = msg.get("role")
        if role == "user":
            content = (msg.get("content") or "")[:300]
            kept.append({"role": "user", "content": content})
        elif role == "assistant" and msg.get("content"):
            # 助手的文字说明有价值；工具调用本身不再回放
            kept.append({"role": "assistant",
                         "content": (msg.get("content") or "")[:200]})
    # 连续同角色的消息会让部分服务端报错，这里合并相邻 user
    merged: List[Dict[str, Any]] = []
    for msg in kept:
        if merged and merged[-1]["role"] == msg["role"]:
            merged[-1]["content"] += "\n" + msg["content"]
        else:
            merged.append(dict(msg))

    out = list(head)
    if facts:
        summary = "；".join(f"{k}={v}" for k, v in facts.items() if v not in (None, "", [], {}))
        if summary:
            out.append({"role": "system",
                        "content": f"（会话中已确认的事实，请继续沿用）{summary}"})
    out.extend(merged)
    out.extend(tail)
    return sanitize_tool_pairs(out)
