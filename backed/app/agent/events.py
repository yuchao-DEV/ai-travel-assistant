"""Agent 事件：SSE 流的原子单位。

前端按 ``type`` 分发到不同的 UI 组件（状态条 / 思考过程 / 工具卡片 /
知识引用 / 行程卡 / 最终答复）。**每个事件都必须能独立解析**——SSE 逐条推送，
前端拿到的可能是任意前缀，不能有跨事件的隐含状态。

``to_sse()`` 产出 ``data: {...}\\n\\n``（SSE 规范要求空行结尾）。
"""
import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class AgentEvent:
    """事件基类。``type`` 决定前端如何渲染。"""
    type: str = "event"

    def payload(self) -> Dict[str, Any]:
        """事件的业务字段（子类覆盖）。"""
        return {}

    def to_dict(self) -> Dict[str, Any]:
        body = {"type": self.type}
        body.update(self.payload())
        return body

    def to_sse(self) -> str:
        return f"data: {json.dumps(self.to_dict(), ensure_ascii=False)}\n\n"


# ---------------------------------------------------------------- 进度类

@dataclass
class StatusEvent(AgentEvent):
    """粗粒度进度提示（"正在检索知识库…"）。用于填补工具执行的空窗。"""
    text: str = ""
    stage: str = ""
    type: str = "status"

    def payload(self) -> Dict[str, Any]:
        return {"text": self.text, "stage": self.stage}


@dataclass
class ThoughtEvent(AgentEvent):
    """模型的思考/说明片段。**只推送模型真实产出的文字**，不伪造思考过程。"""
    text: str = ""
    type: str = "thought"

    def payload(self) -> Dict[str, Any]:
        return {"text": self.text}


# ---------------------------------------------------------------- 工具类

@dataclass
class ToolStartEvent(AgentEvent):
    """工具开始执行。``args`` 原样给出，便于前端展示"它到底查了什么"。"""
    call_id: str = ""
    name: str = ""
    args: Dict[str, Any] = field(default_factory=dict)
    round: int = 0
    type: str = "tool_start"

    def payload(self) -> Dict[str, Any]:
        return {"call_id": self.call_id, "name": self.name,
                "args": self.args, "round": self.round}


@dataclass
class ToolEndEvent(AgentEvent):
    """工具执行结束。成功/失败都发，前端据此决定卡片是绿还是黄。"""
    call_id: str = ""
    name: str = ""
    ok: bool = True
    elapsed_ms: int = 0
    summary: str = ""                 # 一句话结果摘要（不含大段数据）
    error: str = ""
    code: str = ""
    type: str = "tool_end"

    def payload(self) -> Dict[str, Any]:
        body: Dict[str, Any] = {
            "call_id": self.call_id, "name": self.name, "ok": self.ok,
            "elapsed_ms": self.elapsed_ms, "summary": self.summary,
        }
        if self.error:
            body["error"] = self.error
        if self.code:
            body["code"] = self.code
        return body


# ---------------------------------------------------------------- 知识类

@dataclass
class KnowledgeEvent(AgentEvent):
    """检索到的知识来源。带 ``chunk_id`` 与标题，前端可展示引用角标。"""
    sources: List[Dict[str, Any]] = field(default_factory=list)
    vector_ready: bool = False
    type: str = "knowledge"

    def payload(self) -> Dict[str, Any]:
        return {"sources": self.sources, "vector_ready": self.vector_ready}


@dataclass
class ChunkEvent(AgentEvent):
    """最终答复的流式文本分片。"""
    text: str = ""
    type: str = "chunk"

    def payload(self) -> Dict[str, Any]:
        return {"text": self.text}


# ---------------------------------------------------------------- 结果类

@dataclass
class ItineraryEvent(AgentEvent):
    """行程产出。整个 Agent 会话最重要的一个事件。"""
    itinerary: Optional[Dict[str, Any]] = None
    type: str = "itinerary"

    def payload(self) -> Dict[str, Any]:
        return {"itinerary": self.itinerary}


@dataclass
class ProfileUpdateEvent(AgentEvent):
    """画像更新（阶段 4 的记忆系统用）。本阶段只定义协议，不产出。"""
    profile: Dict[str, Any] = field(default_factory=dict)
    type: str = "profile_update"

    def payload(self) -> Dict[str, Any]:
        return {"profile": self.profile}


@dataclass
class ErrorEvent(AgentEvent):
    """会话级错误。致命错误发完它再发 done，前端据此显示失败态。"""
    message: str = ""
    code: str = ""
    type: str = "error"

    def payload(self) -> Dict[str, Any]:
        body: Dict[str, Any] = {"message": self.message}
        if self.code:
            body["code"] = self.code
        return body


@dataclass
class DoneEvent(AgentEvent):
    """流结束标志。``reason`` 说明正常收敛还是触发了兜底/上限。"""
    reason: str = "completed"          # completed / fallback / max_rounds / error
    rounds: int = 0
    tool_calls: int = 0
    usage: Dict[str, Any] = field(default_factory=dict)
    type: str = "done"

    def payload(self) -> Dict[str, Any]:
        return {"reason": self.reason, "rounds": self.rounds,
                "tool_calls": self.tool_calls, "usage": self.usage}
