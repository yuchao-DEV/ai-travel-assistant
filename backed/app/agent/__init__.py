"""Agent 包（阶段 3）。

模块分工：

- :mod:`app.agent.events`     —— SSE 事件定义
- :mod:`app.agent.results`    —— 工具返回值契约
- :mod:`app.agent.registry`   —— 工具注册表（签名反射 + 四道执行守卫）
- :mod:`app.agent.tools.*`    —— 具体工具实现

导入本包**不会**自动注册工具；需要显式 ``import app.agent.tools``。
"""
from app.agent.results import ToolErrorCode, ToolResult

__all__ = ["ToolErrorCode", "ToolResult"]
