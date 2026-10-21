# AI 旅游助手 · Agent + RAG 预期架构图

> 对应 [重构设计文档](./AI旅游助手-Agent+RAG-重构设计文档.md) 的 §2 目标架构。
> 实线 = 主链路；虚线 = 降级路径。**算法引擎一行不改**，只是被包成了工具。

![Agent + RAG 预期架构](./figures/agent_rag_arch.png)

<details>
<summary>Mermaid 源码（可在支持 Mermaid 的编辑器中折叠/重绘）</summary>

```mermaid
flowchart TB
    U(["用户自然语言"])

    subgraph FE["前端 Vue3 · 双端同工程"]
        AC["AgentChat<br/>思考链 + 工具卡片"]
        TL["Timeline / Home / Mine<br/>（保留现有页面）"]
    end

    subgraph APIZ["接入层 · FastAPI :8080"]
        AG["POST /api/agent/chat<br/>SSE 事件流"]
        OLD["现有 18 个路由<br/>（行为不变）"]
    end

    subgraph AGENTZ["Agent 运行时"]
        ORC["orchestrator<br/>Function Calling 循环"]
        BUD["RunBudget<br/>6 轮 / 8 工具 / 50s"]
        MEM["memory<br/>短期会话 + 长期画像"]
        FB["fallback<br/>确定性编排"]
    end

    subgraph TOOLZ["工具层 · 14 个工具"]
        direction LR
        T1["plan_itinerary<br/>（唯一产出 ItineraryVO）"]
        T2["search_poi · search_knowledge<br/>resolve_city · get_weather"]
        T3["filter / score / route<br/>schedule / budget"]
        T4["recall_history · estimate_intercity<br/>get_poi_detail · get_city_intro"]
    end

    subgraph RAGZ["RAG 层"]
        PIPE["pipeline + indexer<br/>切分 → bigram → 双索引"]
        RET["retriever<br/>BM25 ∥ 向量 → RRF → 重排"]
        EMB["embedding<br/>bge-m3 + 缓存"]
    end

    subgraph ENGZ["算法引擎（零改动）"]
        SOLVE["engine.solve()<br/>聚类 → 背包 → 路径 → 排程 → 预算"]
    end

    subgraph DBZ["SQLite app.db"]
        D1[("users / itineraries<br/>（现有，不动）")]
        D2[("documents / doc_chunks<br/>embeddings")]
        D3[("agent_sessions / messages<br/>tool_calls / user_profiles")]
    end

    subgraph EXTZ["外部服务"]
        AM["高德 · POI / 天气 / 路径"]
        LLM["LLM · chat + tools"]
        EAPI["Embedding API"]
    end

    U --> AC
    AC --> AG
    TL --> OLD
    AG --> ORC
    ORC --> BUD
    ORC --> MEM
    ORC --> TOOLZ
    ORC -.->|超预算 / 不支持 tools| FB
    FB --> T1
    T1 --> SOLVE
    T2 --> RET
    T2 --> AM
    T3 --> SOLVE
    T4 --> D1
    RET --> PIPE
    PIPE --> EMB
    EMB --> EAPI
    PIPE --> D2
    RET --> D2
    MEM --> D3
    ORC --> D3
    ORC --> LLM
    SOLVE --> AM

    classDef keep fill:#eef,stroke:#446,stroke-width:1px
    classDef neu fill:#efe,stroke:#464
    class ENGZ,D1 keep
    class RAGZ,AGENTZ,TOOLZ neu
```

</details>

## 五条要看出来的设计

| # | 设计 | 在图上哪里 |
| --- | --- | --- |
| 1 | **算法引擎零改动** | `ENGZ` 与现有 `D1` 是仅有的两个"不改"块；`plan_itinerary` 是它唯一的入口 |
| 2 | **唯一产出 `ItineraryVO` 的工具** | `T1`。Agent 不允许自己写行程——这是"景点/时间/费用只能由算法决定"红线的落地方式 |
| 3 | **兜底与主链路等价** | `ORC ⇢ FB` 虚线。预算耗尽 = 走今天的 `/api/plan`，**最差体验 = 今天的体验** |
| 4 | **RAG 只被 Agent 读、不被 Agent 写** | `T2 → RET` 是单向下行；索引由 `PIPE` 在后台异步做 |
| 5 | **新表与老表不交叉** | `D2`/`D3` 与 `D1` 无外键交叉（仅 `user_id` 指向 `users`），删新表不影响既有数据 |

## 与现状的差异（就这四处）

| | 现状 | 改造后 |
| --- | --- | --- |
| 入口 | 表单 → `/api/plan` | 自然语言 → `/api/agent/chat`（**表单保留**） |
| 决策 | 固定流水线 | Agent 自主决定调哪些工具（**决策权仍在算法引擎**） |
| 知识 | 无 | 混合检索（BM25 + 向量 + RRF + 重排） |
| 记忆 | 无 | 会话上下文 + 跨会话用户画像 |

## 阶段 7 增量（可选，图中未画）

```
新增：lookup_transit_schedule / check_booking_rule / check_closure_notice
      + web_cache 表 + <web> 标签隔离
约束：只进答复文本，绝不写回 ItineraryVO（W4）
```
