"""工具集中注册。

**导入本模块即完成注册**（``@tool`` 装饰器在导入时执行）。新增工具文件后
必须在这里 import 一次，否则注册表里不会有它——这是刻意的显式清单，
避免"某个工具悄悄被加载/没被加载"这种难查的问题。

工具分两档，靠 :attr:`ToolSpec.core` 区分（见 `orchestrator` 的选择性挂载）：

- **CORE**（6 个）：`resolve_city` / `search_knowledge` / `get_weather` /
  `search_poi` / `plan_itinerary` / `recall_history`——首轮就挂，覆盖绝大多数请求。
- **ADVANCED**（8 个）：其余。只在用户意图涉及"调整/为什么/超预算"时才挂，
  以省下每轮约 900 token 的 schema 开销。
"""
from app.agent.tools import (  # noqa: F401
    _poi,
    decide,
    geo,
    knowledge,
    memory_tool,
    plan,
    search,
    traffic,
    weather,
)

__all__ = [
    "_poi", "decide", "geo", "knowledge", "memory_tool",
    "plan", "search", "traffic", "weather",
]
