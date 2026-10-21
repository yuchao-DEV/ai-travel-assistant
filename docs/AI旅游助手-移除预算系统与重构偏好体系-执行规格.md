# AI 旅游助手 · 移除预算系统 + 重构偏好体系 · 执行规格

> 状态：**待执行**（本文档只写规格，不含代码改动）
> 决策来源：用户三轮确认 — A2-（保留单点票价展示）+ B（时间/评分为区分轴）+ C1（profile 预设 + pace 覆盖）
> 前置依赖：无。可与 Agent+RAG 改造并行，但**先做本文档** —— 它削减 `PlanRequestDTO` 与 `ItineraryVO` 字段，晚做会导致 Agent 工具契约返工。
> 配套文档：[重构设计文档](./AI旅游助手-Agent+RAG-重构设计文档.md)、[落地清单](./AI旅游助手-Agent+RAG-落地清单.md)、[架构图](./AI旅游助手-Agent+RAG-架构图.md)

---

## 0. 一页纸看懂

### 0.1 为什么要做

现行系统的「预算规划」在数据上不成立：

| 事实 | 数据来源 | 影响 |
| --- | --- | --- |
| 门票语料覆盖率 **23.8%**（267/1120） | `poi_prices` 表实测 | 76% 的景点票价未知 |
| 其中 **53.8%** 是 `unknown`、**20.6%** 只是"景区内服务均价"（游船/讲解），不是门票 | 同上 | 真能当门票用的只有 15.3% |
| 语料票价**默认关闭** —— `USE_CORPUS_PRICES` 默认 `false` | `config.py:112` | 实际在用的是 `_guess_cost()` 的预留值 |
| `_guess_cost()` 是硬编码预留：餐饮 80 / 购物 50 / 住宿 300 / 观赏 120 / 其他 50 | `models.py` | 不是票价，是占位数字 |
| 住宿占旅行预算 **40–60%**，完全未接入 | `_budget_note()` 自己承认 | 总花费口径天然缺一大块 |

**结论**：系统能算出一个金额，但那个金额既不含住宿、又主要来自预留值 —— 继续称它为"预算规划"违背项目红线「界面声称的必须等于系统实际做的」。

### 0.2 做什么

```
删除：用户总预算约束 + 全部金额聚合（total_cost / daily_cost / budget_note / _enforce_budget / _guess_cost）
保留：单点票价展示，但只显示有来源的真实数据（third_party / free）
替换：选点约束 从「钱」换成「时间」；出行偏好 从「三套权重」换成「时间窗 + 评分权重双轴」
```

### 0.3 三条决策速览

| 决策 | 内容 | 关键约束 |
| --- | --- | --- |
| **A2-** | `cost` 保留为 `Optional[float]`，仅承载真实数据；`_guess_cost()` 删除 | 数字展示门禁：只有 `cost_source ∈ {third_party, free}` 才显示数字 |
| **B** | profile 改为「时间是主轴、评分是副轴」，三套重命名为 悠闲/经典/紧凑 | **必须先修 `rating_norm`**，否则评分轴无效（见 §4） |
| **C1** | `profile` 是预设（决定权重 + 时间窗 + 选点数），`pace` 是可选覆盖 | 禁止"紧凑型 + 宽松"这类冲突组合生效，按覆盖规则确定行为 |

---

## 1. 范围与不做什么

### 1.1 在范围内

- `PlanRequestDTO.budget` 删除
- `ItineraryVO.total_cost` / `.budget` / `.budget_note` 删除
- `DailyPlan.daily_cost` 删除
- 预算约束链：`daily_budget_split` / `weighted_knapsack` 的容量 / `_enforce_budget` / `_budget_note` 删除
- `_guess_cost()` 删除；`compute_score()` 的 `cost` 维度与 `w_cost` 删除
- `rating_norm()` 下界从 3.0 提到 4.0
- `VALID_PROFILES` 重命名为 悠闲/经典/紧凑；三套 profile 同时定义评分权重与时间预算
- `profile` 与 `pace` 的「预设 + 覆盖」关系
- 前端对应字段与展示的移除/判空

### 1.2 明确不在范围内（避免误伤）

| 对象 | 为什么不动 |
| --- | --- |
| `poi_prices.py`（421 行） | A2- 保留。它是真实票价的数据层 |
| `poi_prices` 表（1120 行） | 保留。`average` 类的 `note` 文本仍有价值 |
| `scheduler.daily_select_target()` | 它是时间容量的真实推导，恰好是本次的**新主约束** |
| `SELECT_HEADROOM` / `DEFAULT_DURATIONS` / `MEAL_MINUTES` | 时间维度基础参数 |
| `scheduler` 的营业时间/用餐时段逻辑 | 与预算无关 |
| `_ensure_meal_stop()` / `_ensure_spot_stop()` / `_cap_meals()` **函数本身** | 分别保证"每天有餐厅""每天有景点""餐厅数量不失控"，都是刚需。只摘掉它们的 `budget_per_day` 形参，**不删函数** |
| `COMPANION_*` 相关逻辑 | 同行伙伴是独立硬约束，本次不碰，但要防止改坏 |
| `algorithms/cluster.py` / `route_optimizer.py` | 与预算无关 |
| `/api/inspiration` / `/api/intercity` / `/api/map/thumbnail` | 旁路能力，不涉及预算聚合 |

---

## 2. 决策 A2- 落地规格

### 2.1 `cost_source` 收敛为三值

| 取值 | 含义 | 来源 | 显示数字？ |
| --- | --- | --- | --- |
| `real` | 高德真实人均消费 | `amap biz_ext.cost`（餐饮类实测 24/25 有值） | ✅ 显示 |
| `third_party` | 语料真实门票价 | `poi_prices` 的 `KIND_TICKET` | ✅ 显示 |
| `free` | 明确免费 | `poi_prices` 的 `KIND_FREE` | ✅ 显示「免费」 |
| ~~`average`~~ | **不再显示数字** | `KIND_CONCESSION_AVG` / `KIND_SERVICE_AVG` | ❌ 只出 `cost_note` 文本 |
| ~~`reserved`~~ | **该取值删除** | 原 `_guess_cost()` | ❌ `cost=None`，不显示 |

**数字展示门禁的精确定义**（前端与测试都用这条）：

```python
def should_show_price(item) -> bool:
    """只有这两个来源才允许显示数字金额。"""
    return item.cost_source in ("real", "third_party", "free") and item.cost is not None
```

> 注意 `real` 也在其中：餐饮的真实人均是高德数据，本来就该显示。用户确认的 A2- 门槛是 `(third_party, free)` 针对**门票**；餐饮的 `real` 不受影响。

### 2.2 `apply_known_prices()` 语义调整

| 票价类别 | 现状 | 改为 |
| --- | --- | --- |
| `KIND_TICKET` | `cost=price`, `cost_source="third_party"` | **不变** |
| `KIND_FREE` | `cost=0`, `cost_source="third_party"` | `cost=0`, `cost_source="free"`（独立取值，便于前端显示「免费」而非「¥0」） |
| `KIND_CONCESSION_AVG` | `cost=avg`, `cost_source="average"` | **`cost=None`**, `cost_source="average"`，`cost_note` 保留原文（含真实区间） |
| `KIND_SERVICE_AVG` | `cost=avg`, `cost_source="average"` | **`cost=None`**, `cost_source="average"`，`cost_note` 说明是服务均价 |
| `KIND_UNKNOWN` | `cost_source="reserved"` | **`cost=None`**, `cost_source=None` 或 `"unknown"`，`cost_note` 保留语料原文 |

**为什么 `free` 要独立取值**：`cost=0` + `third_party` 无法区分「免费」与「真实票价 ¥0（不可能）」，前端要显示「免费」得靠语义清晰的取值。

### 2.3 `POI.from_amap()` 调整

删除 `_guess_cost()` 及其调用。改为：

```python
biz_cost = float(raw.get("biz_cost") or 0.0)
has_real_cost = biz_cost > 0
return cls(
    ...,
    cost=biz_cost if has_real_cost else None,        # 不再猜
    cost_source="real" if has_real_cost else None,   # 无数据就是 None
)
```

**注意**：`cost` 从 `float`（默认 0.0）改为 `Optional[float]`（默认 None）。所有下游读数必须处理 None —— 这是最容易漏的地方（见 §9 坑 2）。

---

## 3. 删除清单（逐符号）

### 3.1 `app/schemas.py`

| 删除 | 保留 / 修改 |
| --- | --- |
| `PlanRequestDTO.budget` | — |
| `ItineraryVO.total_cost` | — |
| `ItineraryVO.budget` | — |
| `ItineraryVO.budget_note` | — |
| `DailyPlan.daily_cost` | — |
| `ScoreBreakdown.cost` | 只剩 `interest` / `rating` / `detour` 三维 |
| `VALID_PROFILES = ["经济型","舒适型","紧凑型"]` | → `["悠闲", "经典", "紧凑"]` |
| — | `ItineraryItem.cost` 改为 `Optional[float] = None` |
| — | `ItineraryItem.cost_source` 改为 `Optional[str] = None`，取值集合见 §2.1 |
| — | `ItineraryItem.cost_note` 保留（`average` / `unknown` 时的说明文本） |

### 3.2 `app/algorithms/knapsack.py`

| 删除 | 说明 |
| --- | --- |
| `daily_budget_split()` | 无预算即无拆分 |
| `weighted_knapsack(pool, budget, target_count)` 的 `budget` 参数 | 签名改为 `select_by_time(pool, target_count)` |

**模块改名**：`knapsack.py` → `selector.py`。函数 `weighted_knapsack` → `select_by_time`。

> **为什么不叫"背包"了**：背包问题需要容量与重量，删掉 cost 两者都没了。继续叫 knapsack 会永久误导后来人，也会让答辩时被问"你的背包容量是什么"而无法回答。

**新函数的语义**（保持既有行为的可解释性）：

```python
def select_by_time(pool: list[POI], target_count: int) -> list[POI]:
    """按 score 降序取前 target_count 个。

    时间容量已经通过 target_count 体现（由 scheduler.daily_select_target()
    按当日时间窗推导）。必去项（must_go=True）不受 target_count 限制，一定入选。
    """
```

**注意**：现有 `weighted_knapsack` 有「局部搜索提升总分」与「必去项硬约束」两段逻辑，**必须保留**：

- 必去项硬约束：`must_go=True` 的点不受 `target_count` 与任何容量限制 —— 这是 README 里已修复过的历史缺陷，别改回去
- 局部搜索：在无容量的情况下，它的意义变成"用未选的高分点替换已选的低分点"，仍可保留；**但若实现复杂，可以删掉并在文档里说明**（诚实优于保留半残逻辑）

### 3.3 `app/algorithms/models.py`

| 删除 | 说明 |
| --- | --- |
| `PROFILES[*]["w_cost"]` | 三套 profile 全部删 cost 权重 |
| `compute_score(...)` 的 `budget_per_day` 参数 | — |
| `compute_score(...)` 里的 `cost_norm()` 调用与 `- w_cost*cost` 项 | 评分从四维降为三维 |
| `cost_norm()` 函数 | 无引用 |
| `_guess_cost()` 函数 | A2- 的核心删除项 |
| `POI.cost_source` 的 `reserved` 语义 | 改为可选 |

**修改**：`rating_norm()` 见 §4.3（必须改，否则决策 B 失效）。

**保留**：`POI.cost` / `POI.cost_source` / `POI.cost_note` 字段本身（承载真实票价）。

### 3.4 `app/algorithms/engine.py`

> **本表刻意不写行号。** 本文件正在被并行改造（已加入 `companions` / `pace` / `day_start` / `_cap_meals` / `_ensure_spot_stop`），行号会立即过时。请按**符号名搜索**定位。

**删除**

| 符号 / 代码 | 说明 |
| --- | --- |
| `from app.algorithms.knapsack import daily_budget_split, weighted_knapsack` | 改为 `from app.algorithms.selector import select_by_time` |
| `budgets = daily_budget_split(req.budget, req.days)` | 无预算即无拆分 |
| `budget_per_day = budgets[day_idx]` | — |
| `from app.config import ... USE_CORPUS_PRICES ...` | 改为无条件调用 |
| `if USE_CORPUS_PRICES:` 包裹 | `apply_known_prices()` 改为**无条件调用** |
| `total_cost = 0.0` 及其累加 | — |
| `if total_cost > req.budget:` 分支与 `_enforce_budget(...)` 调用 | — |
| `def _budget_note(...)` 整个函数 | — |
| `def _enforce_budget(...)` 整个函数 | — |
| `ItineraryVO(total_cost=..., budget=..., budget_note=...)` | — |
| `DailyPlan(daily_cost=...)` 各处（含 `_fallback_itinerary`） | — |
| `ItineraryItem(cost_source=item.get("cost_source", "reserved"))` | 默认值改 `None` |

**修改（`budget_per_day` 参数要摘掉的调用点与函数）**

| 符号 | 现状 | 改为 |
| --- | --- | --- |
| `compute_score(poi, tags, ref_lat, ref_lng, max_detour, budget_per_day, weights)` | **两处调用**（选点阶段 + 排序后） | 去掉 `budget_per_day` 实参 |
| `weighted_knapsack(pool, budget_per_day, target_count=...)` | — | `select_by_time(pool, target_count)` |
| `def _ensure_meal_stop(selected, meal_candidates, budget_per_day)` | 用 `total + p.cost <= budget_per_day` 选餐厅，不够就腾掉低分点（**实测 3 处引用**） | **删 `budget_per_day` 参数与 affordability 循环**；改为直接取评分最高、且营业时间覆盖饭点的餐厅；保留 `must_go=True` 标记 |
| `def _ensure_spot_stop(selected, spot_pool, budget_per_day)` | **同样吃 `budget_per_day`**（上一版规格漏了，实测 3 处引用：`sum(p.cost)` + `total + poi.cost <= budget_per_day`） | 同上去掉预算参数与 affordability 判断 |
| `def _meal_pool_for_day(..., budget_per_day, ...)` | 形参里带预算，但**只用于转发给 `compute_score`**（实测 2 处引用，都是签名与转发） | 去掉该形参即可 |

> **`_cap_meals(selected, limit)` 不需要改**：实测内部**零处** `cost`/`budget` 引用，它只按 `score` 排序截断。上一版规格误列了它。

**保留**

- `price_stat = apply_known_prices(pois, req.city)`（用于 `cost_note` 与日志）
- `_centroid()` / `_companion_bonus()` / `_safe_time()` 全部
- `companions` 相关的 `spot_delta`（取 min 不相加）与候选池剔除逻辑
- `day_start` / `day_end` / `pace` 的读取（但要接入 profile 预设，见 §4.4）

### 3.5 `app/services/poi_prices.py`（保留文件，改两处）

| 改动 | 位置 |
| --- | --- |
| `apply_known_prices()` 的 `AVG_KINDS` 分支：`poi.cost = float(hit["price"])` → `poi.cost = None`（只保留 `cost_note`） | 搜 `AVG_KINDS` |
| `KIND_UNKNOWN` 分支：`poi.cost_source = "reserved"` → `poi.cost = None; poi.cost_source = None` | 搜 `"reserved"` |
| `KIND_FREE` 分支：`cost_source="third_party"` → `"free"` | 搜 `KIND_FREE` |

**保留**：`extract_ticket()` / `_from_skus()` / `normalize_name()` / `match_price()` / `replace_city_prices()` / `load_city_prices()` / `price_stats()` 全部匹配算法。

### 3.6 `app/services/nl_parse.py`

| 删除 | 说明 |
| --- | --- |
| `DEFAULT_BUDGET` / `MIN_BUDGET` / `MAX_BUDGET` | — |
| `_coerce_budget()` | — |
| `FIELD_LABELS["budget"]` | — |
| `parse_request()` 里 budget 段（`_coerce_budget` 调用与 `inferred`/`assumed` 追加） | — |
| prompt 里的 `- budget:` 字段规则 | `_build_prompt()` |
| 规则解析里的预算正则（`元/块/人民币`、`预算\s*\d+`） | `_rule_parse()` |

**修改**：`_PROFILE_KEYWORDS` 的口语词映射（见 §4.5）。

**注意**：`ParsedPlanVO.request` 是 `PlanRequestDTO`，删 `budget` 后其 `assumed` / `inferred` 列表也要去掉「预算」项。

### 3.7 `app/services/history.py` + `app/db/database.py`

**表结构**（`itineraries` 的 `budget REAL NOT NULL` 与 `total_cost REAL`）：

**建议保留列，写入填 0，代码注释标废弃。** 理由见 §9 坑 3。

| 改动 | 位置 |
| --- | --- |
| 写入时 `budget=0`、`total_cost=None` | `history.py` 的 `_save_in_conn()` 两处 INSERT/UPDATE |
| `HistoryItemVO.total_cost` 改为可选或删除 | `schemas.py` |
| 列上加注释"已废弃，仅为兼容历史数据保留" | `database.py` 的 DDL |

**另一种选择**：`ALTER TABLE itineraries DROP COLUMN`（SQLite 3.35+ 支持，本机 3.45 ✅）。但不推荐 —— 21 条历史行程的读取逻辑要一起改，风险大于收益。

### 3.8 `app/config.py`

| 删除 | 说明 |
| --- | --- |
| `USE_CORPUS_PRICES` | 改为无条件调用 `apply_known_prices()` |

### 3.9 `app/algorithms/scheduler.py`

| 改动 | 位置 |
| --- | --- |
| `_schedule_once()` 输出字典里的 `"cost"` / `"cost_source"` 透传 | 保留字段，但默认值从 `"reserved"` 改为 `None` |

### 3.10 `app/services/inspiration.py`

`_to_card()` 里的 `"cost"` / `"cost_source"`：**保留**（灵感卡片展示真实人均与免费信息），但要处理 `None`。前端按 §2.1 门禁显示。

### 3.11 前端

| 文件 | 删除/修改 | 风险 |
| --- | --- | --- |
| `views/Home.vue`（16 处） | 删预算输入框与 `form.budget`；`VALID_PROFILES` 改从 `/api/meta` 读 | ⚠️ 模板判空 |
| `pc/views/Home.vue`（16 处） | 同上 | ⚠️ 模板判空 |
| `views/Timeline.vue`（22 处） | 删「总花费」chip、每日金额、`budgetNote` 块；票价展示改按 §2.1 门禁 | ⚠️ **白屏高危** |
| `pc/views/Timeline.vue` | 同上 | ⚠️ 白屏高危 |
| `views/Mine.vue`（2 处） | 删"总花费"统计 | — |
| `pc/views/Mine.vue` | 同上 | — |
| `views/History.vue`（1 处） | 删预算列 | — |
| `components/InspirationCards.vue`（4 处） | 票价按门禁显示 | — |
| `api/travel.js`（2 处） | `getPlan()` 请求体去掉 `budget` | 必改 |
| `utils/travel.js` / `formDraft.js` / `localTrips.js` | 本地草稿里删 budget 字段 | 旧草稿兼容 |

---

## 4. 决策 B + C1 落地规格

### 4.1 三套 profile 定义

| profile | interest | rating | detour | 时间窗 | `spot_delta` | 实测每日选点数 | 体验 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| **悠闲** | 0.50 | 0.30 | 0.20 | 10:00–18:00 | **−1** | **3** | 3–4 个点，每个待得住 |
| **经典** | 0.40 | 0.40 | 0.20 | 09:00–19:00 | **0** | **5** | 4–6 个点，优先高口碑 |
| **紧凑** | 0.30 | 0.20 | 0.50 | 08:30–21:00 | **+1** | **8** | 6–8 个点，少绕路 |

**「每日选点数」这一列是实测算出来的，可直接当测试的期望值。** 公式（`scheduler.daily_select_target`）：

```
窗口 = day_end − day_start
可用 = 窗口 − 两餐 90min
容量 = floor(可用 / (单点 120min + 交通 15min))
target = max(3, round(容量 × SELECT_HEADROOM 1.8))
选点数 = max(1, target + spot_delta)
```

实测结果：

| 场景 | 窗口 | 可用 | 容量 | target | delta | **选点数** |
| --- | --- | --- | --- | --- | --- | --- |
| 悠闲 10:00–18:00 | 480 | 390 | 2 | 4 | −1 | **3** |
| 经典 09:00–19:00 | 600 | 510 | 3 | 5 | 0 | **5** |
| 紧凑 08:30–21:00 | 750 | 660 | 4 | 7 | +1 | **8** |
| （现状）09:00–21:00 | 720 | 630 | 4 | 7 | 0 | 7 |
| （现状）宽松 | 720 | 630 | 4 | 7 | −2 | 5 |

> **注意这里存在"双重收窄"**：时间窗变窄会**自动**把 `target` 降下来，`spot_delta` 又在上面再减一次。上表已把这个复合效果算进去了 —— `悠闲` 的 3 个点是"窗口窄 + delta −1"共同作用的结果。**不要**把 delta 设得过大，否则某些天会只剩 1 个点。
>
> 若希望三档差距更平缓，可只改时间窗、把三档 `spot_delta` 都设为 0（此时选点数为 4 / 5 / 7）。**但我建议保留 delta**，因为它让"节奏"成为一个独立的、用户可在表单里单独调节的旋钮（见 C1）。

### 4.2 命名迁移与既有常量

**现状常量**（`schemas.py`）：

```python
VALID_PROFILES = ["经济型", "舒适型", "紧凑型"]
VALID_PACES    = ["紧凑", "适中", "宽松"]
PACE_SPOT_DELTA = {"紧凑": 1, "适中": 0, "宽松": -2}   # 注意：宽松是 -2
```

| 旧值 | 新值 | 迁移策略 |
| --- | --- | --- |
| 经济型 | **悠闲** | 无成本维度后"经济"是空承诺。`get_profile()` 遇旧值做映射并记 warning |
| 舒适型 | **经典** | 意图相近（兴趣与口碑均衡） |
| 紧凑型 | **紧凑** | 不变 |

**注意三点**：

1. `PACE_SPOT_DELTA["宽松"]` 是 **−2**，与 `紧凑` 的 +1 **不对称**。若 profile 的 delta 与 pace 的 delta 混用，需明确"profile delta 是预设、pace delta 是覆盖"，不要相加。
2. `DEFAULT_DAY_START` / `DEFAULT_DAY_END` **在 `schemas.py` 与 `scheduler.py` 各有一份**（`"09:00"` / `"21:00"`）。接 profile 预设时要改对地方——建议**统一从 `schemas.py` 的 profile 定义取**，`scheduler.py` 的那份保留为函数默认参数。
3. `get_profile()` 目前是"未知名称回退舒适型"；改造后回退目标应改为 **`经典`**。

### 4.3 `rating_norm()` 必须改（决策 B 的前提）

**现状问题**（实测 211 个带评分 POI）：

```
中位 4.5 / ≥4.0 占 80.6% / ≥4.5 占 54.5% / 3.0–4.0 占 18% / 标准差 0.51
```

而 `rating_norm(r) = clamp((r-3)/2)`：

| 评分 | 现值 |
| --- | --- |
| 4.0 | 0.50 |
| 4.3 | 0.65 |
| 4.5 | 0.75 |
| 4.9 | 0.95 |

80% 的点挤在 0.65–0.95 的窄带 → **评分轴几乎无区分度**，决策 B 会失效。

**改为**：

```python
def rating_norm(rating: float) -> float:
    """高德评分 [4.0, 5.0] → [0,1]，截断。

    下界从 3.0 提到 4.0：高德评分分布高度集中在高分区（实测 80.6% ≥4.0，
    中位 4.5），用 (r-3)/2 会把绝大多数候选压到 0.65–0.95 的窄带，
    使 rating 维度失去区分能力。提到 4.0 后：
        4.0→0.0  4.3→0.3  4.5→0.5  4.7→0.7  4.9→0.9
    """
    return max(0.0, min(1.0, (rating - 4.0) / 1.0))
```

**副作用**：`rating < 4.0` 的点 `rating_norm = 0`（不再贡献正分）。这是**有意的** —— 3.9 分的景点不该因为"3 分以上就算中等"而获得正分。

### 4.4 profile 与 pace 的关系（C1）

**覆盖优先级**：

```
profile 的默认时间预算（时间窗 + 每日选点数）
        ↓ 被覆盖
req.pace 显式给出时 → 用 PACE_SPOT_DELTA
        ↓ 再叠加
req.day_start / req.day_end 显式给出时 → 覆盖时间窗
        ↓ 再叠加
req.companions 非空 → spot_delta 取 min（不相加）+ 候选池剔除
```

**修改点**：`engine.py` 里搜 `spot_delta = PACE_SPOT_DELTA.get(req.pace or "", 0)`

```python
# 现状
spot_delta = PACE_SPOT_DELTA.get(req.pace or "", 0)

# 改为（伪代码）
profile_time = PROFILE_TIME[req.profile]          # 时间窗 + spot_delta 预设
day_start = _safe_time(req.day_start, profile_time.start)   # 显式 > 预设
day_end   = _safe_time(req.day_end,   profile_time.end)
spot_delta = (PACE_SPOT_DELTA[req.pace] if req.pace
              else profile_time.spot_delta)                # 显式 pace 覆盖预设
if companions:
    spot_delta += min(COMPANION_SPOT_DELTA.get(c, 0) for c in companions)   # 保持取 min
select_target = max(1, daily_select_target(day_start, day_end) + spot_delta)
```

**关键约束**：

1. `COMPANION_SPOT_DELTA` 仍然**取 min 不相加**。现有代码已有注释解释原因（"同时带老人和朋友时按老人算——体力约束是硬约束，热闹是软偏好"），且用的是 `min`。**别改坏。**
2. `profile_time` 需要新增一个结构（建议在 `schemas.py` 里定义为 `PROFILE_TIME` 字典），与 `PROFILES`（评分权重）并列，两者都以 profile 名为键。
3. **`DEFAULT_DAY_START` / `DEFAULT_DAY_END` 目前有两份**：`schemas.py` 与 `scheduler.py`。改 profile 预设时，`_safe_time` 的兜底值应改为 `profile_time.start/end`，而 `scheduler` 那份保留为函数级默认参数（`daily_select_target(day_start=DEFAULT_DAY_START, ...)`）。**不要只改一处**。
4. 注意 `daily_select_target()` 的返回值已经是"上限"（含 `SELECT_HEADROOM=1.8` 的冗余系数），`spot_delta` 是在其上再调。§4.1 的实测表已体现这个复合效果。

### 4.5 `nl_parse` 的口语词映射调整

```python
_PROFILE_KEYWORDS = {
    "紧凑": ["紧凑", "效率", "尽量多", "行程满", "特种兵", "多逛几个"],
    "悠闲": ["悠闲", "轻松", "不累", "慢节奏", "带爸妈", "带孩子", "亲子", "老人", "休闲"],
    "经典": ["经典", "口碑", "高评分", "值得去", "必看"],
}
```

**删除**：原来的「经济型」关键词（`省钱 / 经济 / 穷游 / 便宜 / 实惠 / 性价比 / 预算紧`）。

**注意**：这类词删掉后，用户说"省钱"将无法映射到任何 profile。两种处理：
- **推荐**：单独识别"省钱"类词并放入 `notes`，提示用户"当前版本不再提供按预算选点"
- 备选：映射到「悠闲」（少去几个点 = 少花钱，间接成立）

我建议**推荐项** —— 明确告知比悄悄映射更符合项目红线。

---

## 5. Schema 兼容性（已验证）

### 5.1 旧数据能读

实测：`schemas.py` **未配置 `extra="forbid"`**，Pydantic v2 默认忽略未知字段。

```
数据库现有历史行程：21 条
模拟删除 budget / total_cost / cost / daily_cost 后：
  id=3:  旧 request_json（含 budget）解析 OK
  id=4:  解析 OK
  id=11: 解析 OK
```

**结论：删除这些字段后，旧数据不会解析失败，不需要数据迁移。**

### 5.2 但新数据不含旧字段

新行程的 `itinerary_json` 不含 `total_cost`。若前端仍读 `itinerary.total_cost` → 拿到 `undefined` → 可能白屏。**这是 §9 坑 2 的核心。**

---

## 6. 实施顺序（依赖关系）

```
第 1 步  rating_norm()           独立、无依赖、可先做（决策 B 的前提）
第 2 步  models.py               删 w_cost / cost_norm / _guess_cost，改 compute_score 签名
第 3 步  schemas.py              删字段 + profile 重命名 + cost 改 Optional
第 4 步  selector.py             由 knapsack.py 改名，删 daily_budget_split
第 5 步  scheduler.py            cost 默认值改 None
第 6 步  poi_prices.py           改 apply_known_prices 三处分支
第 7 步  engine.py               删预算链 + 接 profile_time + 无条件调 apply_known_prices
第 8 步  nl_parse.py             删预算抽取 + 改 profile 关键词
第 9 步  history.py / database   写入填 0 + 列注释
第 10 步 config.py               删 USE_CORPUS_PRICES
第 11 步 测试                     先跑基线，再逐个修断言
第 12 步 前端 6 个文件            删字段 + 全量判空
第 13 步 缓存                    POI_CACHE_VERSION v4 → v5
第 14 步 文档                    README + Agent+RAG 三份文档
```

**为什么这个顺序**：前 3 步是"地基"（字段与评分），改动后其它文件的类型错误会**主动暴露**接入点，比从引擎往下改更容易发现遗漏。

---

## 7. 测试改动

### 7.1 影响面（实测统计）

| 文件 | 相关断言数 | 处理 |
| --- | --- | --- |
| `test_algorithms.py` | **61** | 删预算用例；背包用例改时间容量；profile 权重断言全改；文件顶部的 `from app.algorithms.engine import _enforce_budget, _centroid` 要删掉 `_enforce_budget`（`_centroid` 保留） |
| `test_nl_parse.py` | **17** | 删预算抽取/夹取用例；`assumed` 里去掉"预算"；profile 关键词用例改新值 |
| `test_live_http.py` | **11** | `/api/plan` 请求体去 `budget`；响应断言去 `total_cost` |
| `test_auth_history.py` | **5** | 历史保存/读取字段调整 |
| `test_retrieval_eval.py` | **5** | 通常只是构造测试数据的字段，改即可 |
| `test_hallucination_guard.py` | **1** | 检查是否引用 `cost` |
| `test_rag.py` | 0 | 不受影响 |

**执行方必须先跑一遍现有测试拿到基线数量**，改完后核对减少数是否符合预期（例如 `test_algorithms.py` 若从 140 项降到 120 项，要能解释少掉的 20 项都是预算相关）。

### 7.2 必须新增的断言

| 断言 | 目的 |
| --- | --- |
| **三套 profile 结果互不相同** | 防止再次塌缩（经济型/舒适型曾经几乎重合） |
| `rating_norm(4.0)==0` / `(4.5)==0.5` / `(4.9)==0.9` | 保证评分轴有效 |
| **不存在 `cost_source` 为 `average`/`unknown` 却带非空 `cost` 的条目** | A2- 的数字门禁 |
| `cost_source` 取值集合 ⊆ `{real, third_party, free, average, None}` | 防止 `reserved` 回流 |
| 带老人时 `spot_delta` 取 min 不相加 | 防止改坏既有正确逻辑 |
| 「紧凑 + 宽松」冲突输入行为确定 | C1 覆盖规则 |
| 旧 profile 名（经济型/舒适型）能映射到新值 | 兼容性 |

---

## 8. 验收标准

### 8.1 功能

- [ ] `POST /api/plan` 请求体**不再含** `budget`；若仍传 `budget`，被忽略而非报错
- [ ] 响应**不含** `total_cost` / `budget` / `budget_note` / `daily_cost`
- [ ] `item.cost` 仅在 `cost_source ∈ {real, third_party, free}` 时非空
- [ ] `cost_source` 不再出现 `reserved`
- [ ] 无票价数据的景点，前端显示「门票待确认」+ `cost_note` 文本，**不显示数字**
- [ ] 明确免费的景点显示「免费」而非「¥0」
- [ ] `VALID_PROFILES` 为 `["悠闲","经典","紧凑"]`，旧值能兼容映射
- [ ] 三套 profile 的每日点位数与绕行距离**可测量地不同**

### 8.2 兼容性

- [ ] **21 条历史行程回看全部不报 500**
- [ ] 新行程能正常落库（`itineraries.budget NOT NULL` 不报错）
- [ ] 本地草稿（localStorage）里的旧 `budget` 字段不导致前端崩溃
- [ ] 缓存命中与冷启动两种情况下 `cost` 行为一致

### 8.3 前端

- [ ] 移动端 `/` 与桌面端 `/pc/` **均无白屏**
- [ ] `npm run build` 通过（同时构建两个入口）
- [ ] 所有被删字段的模板访问已改为 `?.` 或 `v-if`

### 8.4 回归

- [ ] `test_algorithms.py` / `test_nl_parse.py` / `test_auth_history.py` 全绿
- [ ] `test_live_http.py` 全绿
- [ ] 减少的断言数可逐项解释

---

## 9. 五个坑（按风险排序）

### 坑 1：前端 51 处引用，白屏风险最高

`Timeline.vue` 22 处、`Home.vue` 16 处、`AgentChat.vue` 5 处、`InspirationCards.vue` 4 处、`Mine.vue` 2 处、`History.vue` 1 处。

**`Timeline.vue:790` 就有一条白屏事故的遗迹注释**：

```
直接写 itinerary.budget_note 会在渲染期抛错导致整页空白
```

**本项目已因"模板访问不存在字段"白屏 3 次**（README M14 明确记录）。这次删字段是最容易复发的一次。

**必须做**：
1. 所有 `itinerary.total_cost` / `day.daily_cost` / `item.cost` 访问改成 `?.` 或 `v-if`
2. **双端都验**（`/` 与 `/pc/`），不能只验一端
3. `npm run build` 必须通过

### 坑 2：`cost` 从 `float` 变 `Optional[float]`，None 会传染

`POI.cost` 默认从 `0.0` 改为 `None` 后，所有下游算术都会炸：

| 位置 | 现状 | 需改为 |
| --- | --- | --- |
| `POI.cost` 求和 | `sum(p.cost for p in ...)` | 跳过 None |
| `_ensure_meal_stop()` | `total = sum(p.cost for p in acc)` + affordability 循环 | 整段删除（本轮去掉预算约束） |
| `_ensure_spot_stop()` | `total = sum(p.cost for p in acc)` + `total + poi.cost <= budget_per_day` | 整段删除 |
| `scheduler._schedule_once()` 的 `"cost": poi.cost` | 直接透传 | 透传 None 是安全的 |
| 前端 `item.cost > 0` | `None > 0` 为 false（安全） | 但要防止 `¥${null}` 渲染 |

**建议**：加一个工具函数统一处理

```python
def cost_or_none(poi) -> Optional[float]:
    """只有有来源的票价才返回值。"""
```

### 坑 3：`itineraries.budget NOT NULL`

```sql
budget    REAL NOT NULL,
total_cost REAL,          -- 这个可空
```

新行程不再有 budget 值 → **若 INSERT 不填 budget 会失败**。

**建议**：写入时 `budget=0`，列保留并在 DDL 注释标"已废弃，仅为兼容历史数据保留"。

### 坑 4：POI 缓存里有旧的预留 cost

`backed/cache/<城市>/pois_v4_*.json` 里每个 POI 都带 `cost` 与 `cost_source: "reserved"`。

A2- 之后 `_guess_cost` 不再运行，但**缓存命中时旧字段仍会被读出来**，导致前端又显示编造的数字。

**必须做**：`POI_CACHE_VERSION` 从 `v4` 升到 `v5`。

项目既有约定（`amap/client.py` 的注释明确写了）：

> 缓存 schema 版本：提取字段发生变化时必须递增，否则会命中旧结构的缓存

### 坑 5：本文件正在被并行改造，**行号不可信**

本规格第一版曾大量引用行号，复核时发现：

| 现象 | 说明 |
| --- | --- |
| `engine.py` 已知规模不一致 | `Get-Content` 计 517 行、`grep -n` 却报到第 603 行 —— 不同工具对行尾/编码的计数不同 |
| 上次阅读后又新增了代码 | `companions` / `pace` / `day_start` / `day_end` / `_ensure_spot_stop` / `_cap_meals` / `POI_POOL_AUGMENT` 都是后来加的 |
| 第一版规格**漏了** `_ensure_spot_stop`、**误列**了 `_cap_meals` | 仅靠阅读归类会出错 |

**执行方请一律按符号名搜索定位，不要按行号跳转。** 若发现符号不存在，说明该文件在你的工作副本里已经变了，以实际代码为准。

---

## 10. 文档连带义务

改完后**必须同步**，否则文档与代码矛盾：

### 10.1 `README.md`

| 位置 | 改什么 |
| --- | --- |
| 一、核心特性 | 「结构化参数输入」里的"总预算（≥100 元）"删除；「多偏好方案」改为时间/评分双轴表述 |
| 二、技术栈 | 无变化 |
| 四、系统架构 | 「一次 `/api/plan` 请求的完整流水线」mermaid 图删「加权背包」「预算校验」节点 |
| 八、API 接口文档 | `/api/plan` 参数去 `budget`；响应示例去 `total_cost` |
| 九、数据契约 ItineraryVO | 删 `total_cost` / `budget` / `budget_note` / `daily_cost` / `cost`（改为可选） |
| **十一、第 4 节** | 加权评分从四维改三维 |
| **十一、第 5 节** | **标题从「每日预算与加权背包」改为「时间容量与评分选点」**，内容整段重写 |
| **十一、第 8 节** | 「预算兜底校验」整节删除 |
| 十二、降级策略 | 删预算相关行 |
| 十三、开发约定 | 第 3 条"不引入重型求解器"仍成立 |
| 十五、版本与历史 | 新增一条（注意 **M15 已被 Agent+RAG 占用**，用 M16） |

### 10.2 Agent+RAG 三份文档

| 文档 | 位置 | 改什么 |
| --- | --- | --- |
| 重构设计文档 | §1.3 资产表 | `knapsack` 那一行改为「时间容量选点」 |
| 重构设计文档 | §4.3.2 | **`plan_itinerary` 工具的参数必须删掉 `budget`** |
| 重构设计文档 | §13.2 代码索引 | `knapsack.py` → `selector.py` |
| 落地清单 | 阶段 3 测试示例 | 构造 `PlanRequestDTO` 的样例去 `budget` |
| 落地清单 | 阶段 0 的 `test_algorithms.py` 140 项 | 数字要更新（会减少） |
| 架构图 | PNG + Mermaid | 图里没有直接提 budget，但「算法引擎」框内文案可保持 |

### 10.3 新增本文档到索引

建议在 `README` 的 `docs/` 说明里加一行指向本文档。

---

## 11. 一句话总结

**这次改造把"算不准的预算规划"换成"算得准的时间与口碑规划"。**

删掉的是一套用编造价格驱动的约束；换来的是两个可验证的约束 —— **一天能在真实时间窗里走几个点**，以及**在候选池里谁的口碑真的更高**。前者的数据来自高德营业时间与 `daily_select_target()`，后者的数据来自高德 `biz_ext.rating`（150/150 有值）。

顺带，它还修掉了一个隐性问题：`rating_norm` 的下界 3.0 让 80% 的候选点挤在 0.65–0.95 的窄带里，评分维度长期"有名无实"。提到 4.0 之后，这个维度才真正开始参与决策。

---

## 附录 A · 快速核查命令

执行前后各跑一次，用于对比。

```powershell
cd <仓库根目录>\backed
$py = ".venv\Scripts\python.exe"

# 1. 基线：现有测试通过数
& $py tests\test_algorithms.py
& $py tests\test_nl_parse.py
& $py tests\test_auth_history.py

# 2. 票价数据现状（验证 A2- 的门禁是否有数据支撑）
& $py -c "from app.services.poi_prices import price_stats; import json; print(json.dumps(price_stats(), ensure_ascii=False, indent=2))"

# 3. 评分分布（验证 rating_norm 改动是否必要）
& $py -c "
import json, glob
rs=[]
for f in glob.glob('cache/*/pois_v4_*.json')[:40]:
    for p in (json.load(open(f,encoding='utf-8')).get('payload') or []):
        r=p.get('rating')
        if isinstance(r,(int,float)) and r>0: rs.append(float(r))
rs.sort(); n=len(rs)
print('n=',n,'中位=',rs[n//2] if n else None,'>=4.0占比=%.1f%%'%(100*sum(1 for x in rs if x>=4.0)/n if n else 0))
"

# 4. 验证旧数据可解析（坑：历史行程回看 500）
& $py -c "
import sqlite3
from app.schemas import PlanRequestDTO, ItineraryVO
c=sqlite3.connect('data/app.db'); c.row_factory=sqlite3.Row
for r in c.execute('select id,request_json,itinerary_json from itineraries'):
    PlanRequestDTO.model_validate_json(r['request_json'])
    ItineraryVO.model_validate_json(r['itinerary_json'])
print('全部历史行程解析 OK')
"
```

## 附录 B · 决策溯源

| 决策 | 提出 | 依据 |
| --- | --- | --- |
| 移除以预算为约束的选点 | 用户 | 门票/住宿数据无法获得 |
| **A2-**（保留单点票价而非全删） | 用户 | 267 条真实票价（`ticket`+`free`）是干净资产，与"算不准总花费"是两件事 |
| 数字展示门禁 `{third_party, free}` | 用户 | 只有这两个来源是可信的真实数据 |
| **B**（高评分 + 时间做区分） | 用户 | 钱作为区分维度已失效，需换成可验证的轴 |
| **B 的前提修正**：先改 `rating_norm` | 本文档 | 实测 80.6% 的 POI 评分 ≥4.0，`(r-3)/2` 使评分轴无区分度 |
| `rating_norm` 下界 3.0 → 4.0 | 本文档 | 把区分度放回真实分布区间 |
| **C1**（profile 预设 + pace 覆盖） | 本文档 | `VALID_PROFILES` 与 `VALID_PACES` 都含"紧凑"，语义冲突必须解决 |
| 经济型 → 悠闲 | 本文档 | 无成本维度后"经济"是空承诺 |
| `knapsack.py` → `selector.py` | 本文档 | 无容量无重量，不再是背包问题，不改名会永久误导 |
| `itineraries` 两列保留填 0 | 本文档 | 删列风险大于收益（21 条历史数据仍在读） |
| `POI_CACHE_VERSION` 升 v5 | 本文档 | 项目既有约定，否则命中旧缓存会带回 `reserved` |
