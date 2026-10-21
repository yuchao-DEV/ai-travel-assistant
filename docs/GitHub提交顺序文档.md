# AI 旅游助手 · GitHub 提交顺序文档

> **版本 v4（2026-10-23 更新）** —— 依据项目最新代码重新盘点。
> 用途：为**新建的 GitHub 仓库**构建一条干净、可读的里程碑提交历史。
> 与现有阿里云 Codeup 历史**完全解耦** —— 本地 `main` 上的 3 个旧提交保持不动，Codeup 远端也不动。
>
> 盘点基准：**177 个文件**（85 个已提交 + 92 个待提交），另有 **7 个本地产物明确排除**（见 §5.2 ~ §5.6）。
> 注：本文件自身（`docs/GitHub提交顺序文档.md`）也是待提交文件之一，已计入 C18。

---

## 0-A. 更新记录

> 本文档随项目演进改过三版。每版都重新盘点了文件，不是简单改数字。

### v4（2026-10-23）：部署产物 + 真实缺陷修复

| 变化 | 影响 |
| --- | --- |
| 新增 `_deploy/`（`backend.tar.gz` 318 KB + `frontend-dist.tar.gz` 612 KB） | **部署打包产物**，必须排除（§5.6） |
| 新增 `backed/_vbashi.py` | 一次性调试脚本（文件自述「执行完即删」），必须排除 |
| 新增 `docs/部署指南.md` | 面向阿里云 ECS 的完整上线步骤，**正常提交** → C18 由 19 → 20 |
| **6 个已提交文件又有改进**（见下方「后续修复提交」） | 新增 **C20 ~ C22** 三个 follow-up 提交 |
| 总数 176 → **177** | 新增 1 个文档，排除 3 个本地产物 |

**关于「6 个已提交文件又有改进」**：`engine.py`（购物中心不再作种子关键词）、`amap/client.py`（天气 `casts` 层级取错）、`services/importer.py`（八市/第八市场被误判为非地点）、`services/nl_parse.py`（同行伙伴自然语言抽取，+72 行）、`data/cities.json`（关键词表）、`.gitignore`（注释重写 + 新增忽略）。

这些都是**在这些模块提交之后才发现的真实缺陷**，无法塞回原提交（否则要重写 C01~C10，且会与已推送的远端冲突）。因此采用**在末尾追加修复提交**的做法 —— 这既符合真实开发节奏，也不破坏已完成的历史。

### v3（2026-10-22 第二次更新）：移除旧 SSE 对话

| 变化 | 影响 |
| --- | --- |
| **删除 `backed/app/services/chat.py`** | 该文件已随 C06 提交，故 **amend C06** 剔除 → C06 由 10 → 9 个文件 |
| **删除 `fronted/travel/src/views/Chat.vue`** | 移动端 SSE 对话页，尚未提交 → C10 由 33 → 31 |
| **删除 `fronted/travel/src/pc/views/Chat.vue`** | 桌面端 SSE 对话页，尚未提交 |
| **移除 `POST /api/travel/chat` 路由** | 路由总数 **32 → 31** → C08 描述更新 |
| `chat.py` 已无任何引用 | 旧的纯 LLM 流式对话被 `/api/agent/chat` 取代 |
| **总数 179 → 176** | C06 −1、C10 −2 |

> 项目侧同时有大量文件被修改内容（`main.py`、`schemas.py`、`Mine.vue`、`Timeline.vue` 等），但**文件数量没有变化**，不影响提交分区。

### v2（2026-10-22 首次更新）：认证改为账号密码 + 新增三大能力

| 变化 | 影响 |
| --- | --- |
| **认证从「手机号 + 短信验证码」改为「账号 + 密码」** | 新增 `users.username` / `password_hash`（pbkdf2），`/api/auth/send-code` 路由已删除；`RegisterDTO`/`LoginDTO`/`UserVO` 改为 username 字段 | 
| 遗留：`auth.py` 的 `send_code` / `_consume_code`、`sms.py`、`sms_codes` 表**未清理** | 属死代码，不影响运行；提交时如实保留 |
| **新增视觉能力** | `services/vision.py`、`/api/vision/recognize`、`/api/import/image` |
| **新增知识库管理 API** | `/api/kb/stats` `/files` `/reindex` `/upload` `/search` |
| **新增 Agent API** | `/api/agent/chat` `/tools` `/sessions` `/session/{id}` `/profile` |
| 新增 22.5 MB 数据库备份文件 | **必须排除**（§5.3） |
| 计划侧：C07 改名、路由数更新、总数修正 180→181、新增 §5.3 与 §2.4、C10 补 `.env.example` | 已在各节落地 |

### v1.1：前端密钥移出源码

`useAmap.js` 的硬编码 Key 改为读 Vite 环境变量（`VITE_AMAP_KEY` 等），真实值放 `.env.local`（已忽略）。详见 §5.4。

### ⚠️ 一个仍待处理的问题：README 与代码不符

`README.md` 仍在大量描述**手机号验证码登录**，但代码已经是账号密码：

```
第  57 行  - **手机号注册 / 验证码登录**：无密码体系...
第  77 行  | 数据库 | ... 用户、验证码、登录态...
第 108 行  | 短信通道 | 注册/登录验证码...
第 147 行  │       ├── security.py  # 手机号校验、验证码哈希...
第 214 行  │   ├── Login.vue        # 手机号验证码登录/注册
```

**README 会在 C19 公开到 GitHub**。建议在 C19 前同步更新这些段落，否则公开仓库的说明与代码矛盾。这项不在提交计划内，需另行处理。

---

## 0. 开工前必须定的三件事

### Q1 仓库公开还是私有？（决定后续全部动作）

| 选区 | 后果 | 建议 |
| --- | --- | --- |
| **私有** | 高德 Key、参赛文档、QQ 邮箱均只有自己可见 | 最省事 |
| **公开** ✅ 已选定 | Key 已通过环境变量移出源码（见 Q3，已完成）；仍需决定 ② 参赛文档、③ 提交邮箱 | 见 Q2 |

### Q2 3 份参赛文档（3.5 MB）要不要进 GitHub？

```
docs/AI旅游助手-技术文档.pdf       1332 KB
docs/AI旅游助手-技术文档-new.pdf   1388 KB
docs/AI旅游助手-技术文档.docx       810 KB
```

- **私有仓库**：建议**包含**（保住完整交付物）
- **若参赛要求赛前不公开**：建议加进 `.gitignore` 后**不提交**

### Q3 高德密钥怎么处理？—— ✅ 已解决

**结论**：改用 Vite 环境变量，源码零硬编码。仓库可安全公开，本地开发也只需配一次。

原方案里"换成占位符"的代价是每次本地跑都要重填，已弃用。详细改动与验证见 **§5.4**。

---

## 1. 提交顺序总表

**19 个新建提交**（C01~C19，跨 2026-10-13 ~ 2026-10-21）**+ 3 个后续修复提交**（C20~C22，2026-10-22）。**顺序按依赖关系排，不按时间倒序。**

| # | 日期 | 提交信息 | 文件数 | 主题 |
| --- | --- | --- | --- | --- |
| 01 | 10-13 | `chore: 仓库初始化与工程配置` | 12 | 忽略规则、依赖、构建配置 |
| 02 | 10-13 | `feat(backend): 配置、数据层与建库` | 8 | config / SQLite / 城市与 typecode 表 |
| 03 | 10-14 | `feat(amap): 高德数据层与文件缓存` | 3 | POI / 天气 / 路径 / 缓存 |
| 04 | 10-14 | `feat(algorithms): 行程规划算法引擎` | 9 | 聚类 / 选点 / 路径 / 排程 |
| 05 | 10-15 | `feat(llm): 大模型客户端与 JSON 抽取` | 2 | 多轮 / 流式 / 稳健取 JSON |
| 06 | 10-15 | `feat(services): 规划、解析与旁路服务` | 9 | plan / polish / nl_parse / 灵感 / 城际 |
| 07 | 10-16 | `feat(auth): 账号密码登录与历史行程持久化` | 5 | pbkdf2 密码 / token / 鉴权依赖 / 历史 |
| 08 | 10-16 | `feat(api): 31 个路由与接口契约` | 2 | main.py / schemas.py（含 vision / kb / agent 路由） |
| 09 | 10-17 | `test: 算法、解析与鉴权测试` | 4 | 离线单测 + 真实 HTTP 冒烟 |
| 10 | 10-17 | `feat(web): 双端前端基础` | 31 | Vant 移动端 + Element Plus 桌面端（含 `.env.example`） |
| 11 | 10-18 | `feat(rag): 中文分词、FTS5 与混合检索` | 9 | bigram / 双索引 / RRF / 重排 |
| 12 | 10-18 | `feat(kb): 城市知识库与索引脚本` | 20 | 语料 13 篇 + 采集/审计脚本 |
| 13 | 10-19 | `feat(price): 票价数据与城市资源池` | 5 | 票价抽取 / 城市注册 / POI 池 |
| 14 | 10-19 | `feat(agent): 工具注册与 Function Calling 主循环` | 26 | 14 个工具 / 编排 / 兜底 / 记忆 |
| 15 | 10-20 | `feat(vision): 图片识别导入` | 2 | 图片 → 地点 |
| 16 | 10-20 | `test: 七项降级演练` | 1 | 异常场景不崩溃 |
| 17 | 10-21 | `feat(web): 双端 Agent 对话页` | 8 | 思考链 / 工具卡片 / SSE |
| 18 | 10-21 | `docs: Agent+RAG 设计与执行文档` | 20 | 4 篇设计 + 部署指南 + 技术文档与配图 |
| 19 | 10-21 | `docs: README` | 1 | 项目总说明 |
| 20 | 10-22 | `fix(amap): 天气字段层级与种子关键词` | 0 | 修改 `client.py` / `engine.py` / `cities.json` |
| 21 | 10-22 | `fix(services): 市井美食地误杀与同行伙伴抽取` | 0 | 修改 `importer.py` / `nl_parse.py` |
| 22 | 10-22 | `chore: 忽略部署产物与一次性脚本` | 0 | 修改 `.gitignore` |

合计 **177 个文件**（C01 为 12 个，剔除 `.trae/` 与 `tools/`；C06 因移除旧 SSE 对话为 9 个；C10 因移除两个 `Chat.vue` 为 31 个，含 `.env.example`；C18 含新增的部署指南）。
**C20~C22 只是修改已提交的文件，不新增文件**，故文件数为 0。
**另有 2 个文件明确排除**：`app.db.bak-before-auth`（§5.3）、`write_home.js`（§5.2）。

> **注**：C01 已先行完成（14 个文件），C02 亦已完成（8 个文件）。上表为完整 19 个提交的规划口径。

---

## 2. 前置：建立新历史

```powershell
cd <仓库根目录>

# ---- 2.1 备份当前状态（出问题可一键回退）----
git branch backup-codeup-main main

# ---- 2.2 建无父提交分支；工作区文件全部保留 ----
git checkout --orphan gh
git rm -rf --cached .

# 确认：此刻所有文件都是「未跟踪」，但磁盘上一个都没少
git status --short | Measure-Object | Select-Object -ExpandProperty Count
# 应显示 ≈184（被 .gitignore 排除的 venv/cache/pyc 不会出现）

# ---- 2.3 定义按日期提交的辅助函数（本终端会话内有效）----
function Commit-At {
    param([string]$Date, [string]$Message)
    $env:GIT_AUTHOR_DATE   = $Date
    $env:GIT_COMMITTER_DATE = $Date
    git commit -q -m $Message
    if ($LASTEXITCODE -ne 0) { throw "提交失败：$Message" }
    Write-Host "✔ $Date  $Message"
}
```

> `Commit-At` 同时设置 author 与 committer 日期，避免 GitHub 上两者不一致。

### 2.4 当前实际进度：**C01 ~ C06 已完成**（含一次重建）

**最新状态**（2026-10-22 第二次更新后实测）：

```
e2e2a55  2026-10-15 22:40  feat(services): 规划、解析与旁路服务          ← C06（tip，已剔除 chat.py）
f2d6135  2026-10-15 20:05  feat(llm): 大模型客户端与 JSON 抽取          ← C05
6ebeada  2026-10-14 23:20  feat(algorithms): 行程规划算法引擎            ← C04
0c8458e  2026-10-14 19:50  feat(amap): 高德数据层与文件缓存              ← C03
b1da99b  2026-10-13 21:15  feat(backend): 配置、数据层与建库             ← C02
d41ef80  2026-10-13 09:40  chore: 仓库初始化与工程配置                   ← C01（12 文件）

已跟踪文件：43      剩余待提交：133      合计 176
```

**历史上发生过两次调整，都留作记录：**

**① C02 被 amend 过一次。** `backed/app/db/database.py` 在 C02 提交后又改了（新增 `users.username` / `password_hash` / `_migrate_users_to_account`），导致工作区持续为脏。因 C02 当时是 tip，amend 不产生分叉，直接并入。

**② C01 被重建过一次（剔除 `.trae/` 与 `tools/`）。** 这两个文件先在 C01 里提交，随后在 GitHub 网页上被分两次删除，远端多出 `8bc01c0`、`dadab39` 两个提交，与本地历史分叉。为尊重该决定，C01 重做为 **12 个文件**（原 14），并把两者加入 `.gitignore`（详见 §5.5）。

重建后的操作要点（供复核）：

```powershell
# 从 save-work 分支（保存了完整 6 提交，46 文件）取回全部内容
git reset --hard save-work

# 回到 C01，剔除两个文件后 amend
git reset --hard 83cdb2c
git rm --cached .trae/rules/git-commit-message.md tools/install_electron.py
Add-Content .gitignore "`n# 本地 IDE 规则与开发辅助脚本（不随仓库分发）`n.trae/`ntools/install_electron.py"
git add .gitignore
git commit --amend --no-edit --date="2026-10-13T09:40:00+08:00"

# 逐层重建 C02，再 cherry-pick C03~C06
git checkout save-work -- .gitignore backed/app/__init__.py backed/app/config.py `
                          backed/app/errors.py backed/app/db backed/data backed/run.py
git add .gitignore backed/app/__init__.py backed/app/config.py `
        backed/app/errors.py backed/app/db backed/data backed/run.py
git commit -m "feat(backend): 配置、数据层与建库" --date="2026-10-13T21:15:00+08:00"

git cherry-pick dcff148 82e5634 e6df203 384894d   # 按顺序，逐个设置 GIT_COMMITTER_DATE
```

> **要记住的关键点**：远端那 4 个提交（`83cdb2c → a324d2c → 8bc01c0 → dadab39`）与重建后的历史和**没有任何共同祖先**（C01 被重做，根提交已不同）。因此推送必须用 `--force` 或 `--force-with-lease`，属于整体替换。

**下一步**：从 **C07 · feat(auth): 账号密码登录与历史行程持久化** 继续。

---

## 3. 逐提交执行

### C01 · chore: 仓库初始化与工程配置

```powershell
git add .gitignore .trae/rules/git-commit-message.md tools/install_electron.py `
        backed/.env.example backed/requirements.txt `
        fronted/travel/.gitignore fronted/travel/.vscode/extensions.json `
        fronted/travel/jsconfig.json fronted/travel/package.json `
        fronted/travel/package-lock.json fronted/travel/vite.config.js `
        fronted/travel/index.html fronted/travel/pc/index.html fronted/travel/README.md
Commit-At "2026-10-13T09:40:00+08:00" "chore: 仓库初始化与工程配置"
```

### C02 · feat(backend): 配置、数据层与建库

```powershell
git add backed/app/__init__.py backed/app/config.py backed/app/errors.py `
        backed/app/db backed/data backed/run.py
Commit-At "2026-10-13T21:15:00+08:00" "feat(backend): 配置、数据层与建库"
```

### C03 · feat(amap): 高德数据层与文件缓存

```powershell
git add backed/app/amap
Commit-At "2026-10-14T19:50:00+08:00" "feat(amap): 高德数据层与文件缓存"
```

### C04 · feat(algorithms): 行程规划算法引擎

```powershell
git add backed/app/algorithms
Commit-At "2026-10-14T23:20:00+08:00" "feat(algorithms): 行程规划算法引擎"
```

> 注意 `algorithms/` 里已含最终的 `selector.py`（原 `knapsack.py` 已被删除）。新历史中不存在 `knapsack.py`。

### C05 · feat(llm): 大模型客户端与 JSON 抽取

```powershell
git add backed/app/llm.py backed/app/services/llm_json.py
Commit-At "2026-10-15T20:05:00+08:00" "feat(llm): 大模型客户端与 JSON 抽取"
```

### C06 · feat(services): 规划、解析与旁路服务

```powershell
git add backed/app/services
# 卸下属于后续提交的服务模块
git restore --staged backed/app/services/auth.py `
                        backed/app/services/history.py `
                        backed/app/services/kb.py `
                        backed/app/services/poi_prices.py `
                        backed/app/services/city_registry.py `
                        backed/app/services/poi_pool.py `
                        backed/app/services/agent_chat.py `
                        backed/app/services/agent_session.py `
                        backed/app/services/vision.py
Commit-At "2026-10-15T22:40:00+08:00" "feat(services): 规划、解析与旁路服务"
```

### C07 · feat(auth): 账号密码登录与历史行程持久化

> **v2 修正**：认证已从「手机号 + 短信验证码」迁移为「账号 + 密码」（pbkdf2）。
> `sms.py` 与 `auth.py` 里的验证码函数属**遗留死代码**，本轮不清理、如实提交。

```powershell
git add backed/app/security.py backed/app/sms.py backed/app/deps.py `
        backed/app/services/auth.py backed/app/services/history.py
Commit-At "2026-10-16T20:30:00+08:00" "feat(auth): 账号密码登录与历史行程持久化"
```

### C08 · feat(api): 31 个路由与接口契约

> `main.py` 当前共有 **31 个路由**（认证 4 + 历史 5 + 规划与旁路 6 + 视觉 1 + 导入 2 + 知识库 5 + Agent 7 + meta 1）。
> 旧的 `POST /api/travel/chat`（SSE 流式对话）已随 `services/chat.py` 一并移除，由 `/api/agent/chat` 取代。
> 因「一个文件只能进一个提交」，此处已包含 C11–C15 才实现的路由注册——见 §8 的固有限制说明。

```powershell
git add backed/app/main.py backed/app/schemas.py
Commit-At "2026-10-16T23:10:00+08:00" "feat(api): 31 个路由与接口契约"
```

### C09 · test: 算法、解析与鉴权测试

```powershell
git add backed/tests/test_algorithms.py backed/tests/test_nl_parse.py `
        backed/tests/test_auth_history.py backed/tests/test_live_http.py
Commit-At "2026-10-17T10:20:00+08:00" "test: 算法、解析与鉴权测试"
```

### C10 · feat(web): 双端前端基础

```powershell
git add fronted/travel
# 卸下属于 C17 的 Agent 页面
git restore --staged fronted/travel/src/api/agent.js `
                        fronted/travel/src/components/AgentTimeline.vue `
                        fronted/travel/src/components/StorySection.vue `
                        fronted/travel/src/composables/useAgentStream.js `
                        fronted/travel/src/views/AgentChat.vue `
                        fronted/travel/src/views/AgentHistory.vue `
                        fronted/travel/src/pc/views/AgentChat.vue `
                        fronted/travel/src/pc/views/AgentHistory.vue
Commit-At "2026-10-17T22:55:00+08:00" "feat(web): 双端前端基础"
```

> `fronted/travel/write_home.js` 是生成 Home.vue 的临时脚本，**默认不提交**（见 §5.2）。

### C11 · feat(rag): 中文分词、FTS5 与混合检索

```powershell
git add backed/app/rag backed/tests/test_rag.py backed/tests/test_retrieval_eval.py
Commit-At "2026-10-18T11:30:00+08:00" "feat(rag): 中文分词、FTS5 与混合检索"
```

### C12 · feat(kb): 城市知识库与索引脚本

```powershell
git add backed/knowledge backed/app/services/kb.py backed/scripts
Commit-At "2026-10-18T20:45:00+08:00" "feat(kb): 城市知识库与索引脚本"
```

### C13 · feat(price): 票价数据与城市资源池

```powershell
git add backed/app/services/poi_prices.py backed/app/services/city_registry.py `
        backed/app/services/poi_pool.py `
        docs/ticket-gap-list.md docs/ticket-match-review.md
Commit-At "2026-10-19T12:10:00+08:00" "feat(price): 票价数据与城市资源池"
```

### C14 · feat(agent): 工具注册与 Function Calling 主循环

```powershell
git add backed/app/agent backed/app/services/agent_chat.py `
        backed/app/services/agent_session.py `
        backed/tests/test_agent_loop.py backed/tests/test_agent_protocol.py `
        backed/tests/test_agent_tools.py backed/tests/test_memory.py `
        backed/tests/test_hallucination_guard.py
Commit-At "2026-10-19T22:25:00+08:00" "feat(agent): 工具注册与 Function Calling 主循环"
```

### C15 · feat(vision): 图片识别导入

```powershell
git add backed/app/services/vision.py backed/tests/test_vision.py
Commit-At "2026-10-20T10:40:00+08:00" "feat(vision): 图片识别导入"
```

### C16 · test: 七项降级演练

```powershell
git add backed/tests/test_degradation.py
Commit-At "2026-10-20T15:20:00+08:00" "test: 七项降级演练"
```

### C17 · feat(web): 双端 Agent 对话页

```powershell
git add fronted/travel/src/api/agent.js `
        fronted/travel/src/components/AgentTimeline.vue `
        fronted/travel/src/components/StorySection.vue `
        fronted/travel/src/composables/useAgentStream.js `
        fronted/travel/src/views/AgentChat.vue `
        fronted/travel/src/views/AgentHistory.vue `
        fronted/travel/src/pc/views/AgentChat.vue `
        fronted/travel/src/pc/views/AgentHistory.vue
Commit-At "2026-10-21T11:15:00+08:00" "feat(web): 双端 Agent 对话页"
```

### C18 · docs: Agent+RAG 设计与执行文档

> **先按 Q2 决定参赛文档是否包含。** 用「整目录 add + 卸载例外」的写法，比逐个列文件更不容易漏（本文件自身、以及日后新增的文档都会被自动带上）。

```powershell
git add docs
# 这两个属于 C13
git restore --staged docs/ticket-gap-list.md docs/ticket-match-review.md
# 若 Q2 选「不包含参赛文档」，再卸载这三个：
# git restore --staged docs/AI旅游助手-技术文档.pdf docs/AI旅游助手-技术文档-new.pdf docs/AI旅游助手-技术文档.docx
Commit-At "2026-10-21T17:30:00+08:00" "docs: Agent+RAG 设计与执行文档"
```

### C19 · docs: README

```powershell
git add README.md
Commit-At "2026-10-21T19:00:00+08:00" "docs: README"
```

---

## 4. 推送前校验（不要跳过）

```powershell
cd <仓库根目录>

# 4.1 提交数量与顺序
git log --oneline --date=short --pretty=format:"%ad  %s" | Sort-Object   # 逆序看更直观
( git log --oneline | Measure-Object ).Count      # 应为 19

# 4.2 关键：工作区必须干净（说明 181 个文件全部已提交）
# 期望输出为空；若残留 app.db.bak-before-auth 或 write_home.js，
# 说明 §2.4 的 ignore 规则没生效
git status --short
# 若出现任何文件名，说明有文件漏了，回到对应 Cxx 补

# 4.3 确认四类不该出现的都没进来
git ls-files | Select-String 'node_modules|\.venv|\.pyc|backed/cache|app\.db|docs/_work|\.bak-before-auth|write_home'
# 期望：无输出

# 4.4 确认新历史里没有旧的大文件（venv / 大 PDF）
git rev-list --objects --all | git cat-file --batch-check='%(objectsize) %(rest)' |
  Sort-Object {[int64]($_ -split ' ')[0]} -Descending | Select-Object -First 5

# 4.5 与旧 main 对比：新历史应少 knapsack.py 与 Detail.vue，且不含 venv
git diff --stat main gh -- | Select-Object -Last 5
```

**4.2 是最重要的一条。** 只要 `git status` 里还有未提交的业务文件，说明提交分组漏了。

---

## 5. 待定与必须排除的文件

### 5.1 参赛文档不进仓库（若 Q2 选「不包含」）

在 **C01** 之前追加到 `.gitignore`：

```powershell
Add-Content .gitignore "`n# 参赛交付物（赛前不公开）`ndocs/*.pdf`ndocs/*.docx"
```

然后 C18 里把「卸载参赛文档」那三行的注释打开（或直接整段跳过 `git add docs` 里的三个文件）。

### 5.2 `fronted/travel/write_home.js`（17 KB，临时脚本）—— **默认排除**

它是**用 Node 脚本生成 `Home.vue` 内容的开发辅助工具**（`fs.writeFileSync` 写模板字符串），不属于交付物。

已包含在 §2.4 的 ignore 规则里（`fronted/travel/write_home.js`）。若你想保留，在 **C10** 之后追加：

```powershell
git add -f fronted/travel/write_home.js
Commit-At "2026-10-17T23:20:00+08:00" "chore: 首页生成脚本"
```

### 5.3 `backed/data/app.db.bak-before-auth`（22.5 MB）—— **必须排除** ⚠️

**v2 新增项。** 这是认证迁移前对数据库做的备份，**22.5 MB**，且现有 `.gitignore` 的 `*.db` 规则**匹配不到它**（文件名以 `.bak-before-auth` 结尾），因此它**出现在待提交列表里**。

一旦提交，仓库体积会凭空多出 22.5 MB，且 Git 历史无法轻易瘦身。

**处理**（已包含在 §2.4 的 ignore 规则中）：

```powershell
Add-Content .gitignore "`n# 数据库备份与临时脚本`nbacked/data/*.bak*`nfronted/travel/write_home.js"
```

**若它已经被误提交**（例如执行过 `git add -A`），在下一次提交前撤回：

```powershell
git restore --staged backed/data/app.db.bak-before-auth
```

> 建议同时把它从工作区移走（例如挪到 `D:\db-backups\`），既省 22.5 MB 磁盘，也彻底消除误提交的可能。

### 5.4 高德密钥 —— ✅ 已处理完毕（无需再动手）

**原计划**是把 Key 换成占位符，代价是本地开发每次都要重填。**已改为更彻底的方案**：前端密钥走 Vite 环境变量，源码中不留任何 Key。

已完成的三处改动：

| 文件 | 改动 | 归属提交 |
| --- | --- | --- |
| `fronted/travel/src/composables/useAmap.js` | 改为读 `import.meta.env.VITE_AMAP_KEY` / `VITE_AMAP_SECURITY_CODE` / `VITE_AMAP_VISUAL_ID`；缺 Key 时抛明确错误 | C10 |
| `fronted/travel/.env.example` | 新增模板（进仓库，无真实值） | C10 |
| `fronted/travel/.env.local` | 已写入真实 Key，**被 `*.local` 规则忽略，不会提交** | 不入库 |
| `fronted/travel/src/views/Timeline.vue` | 移动端补 `if (AMAP_VISUAL_ID)` 守卫（桌面端原本已有） | C10 |
| `README.md` | 第七节「前端 Key」「Visual 项目 ID」改写为环境变量方式；开发约定第 4 条与 FAQ 同步 | C19 |

**本地开发不受影响**：`.env.local` 里已是真实值，`npm run dev` 照常工作；换机器时只需把 `.env.example` 复制成 `.env.local` 再填一次。

> ⚠️ **两条仍然建议做的加固**（都不影响仓库）：
> 1. 去高德控制台给该 Key 配**域名白名单**（含 `localhost`）——JS Key 天生会暴露在浏览器里，白名单才是真防护；
> 2. 这些 Key 曾进入过阿里云 Codeup 的历史。若 Codeup 仓库是公开的，建议**重置 Key**。

---

### 5.5 `.trae/` 与 `tools/` —— **不随仓库分发**（v3 新增）

| 文件 | 内容 | 排除理由 |
| --- | --- | --- |
| `.trae/rules/git-commit-message.md` | 53 字节，内容只有"简洁"两字 | Trae IDE 的本地规则配置 |
| `tools/install_electron.py` | 4.8 KB / 136 行 | 本地装 Electron 的辅助脚本 |

**来历**：这两个文件曾随 C01 一起提交，随后在 GitHub 网页上被分两次删除：

```
8bc01c0  Delete tools directory
dadab39  Delete .trae/rules directory
```

为尊重这个决定，**C01 已重做为 12 个文件**（原 14 个），两者改为加入 `.gitignore` 排除：

```
# 本地 IDE 规则与开发辅助脚本（不随仓库分发）
.trae/
tools/install_electron.py
```

> **注意**：`.gitignore` 里写的是 `.trae/`，因此该目录下**任何**文件都不会入库；`tools/` 只排除 `install_electron.py`，将来若在 `tools/` 下放通用脚本仍可提交。

---

### 5.6 `_deploy/` 与 `backed/_vbashi.py` —— **本地产物，必须排除**（v4 新增）

| 文件 | 大小 | 性质 |
| --- | --- | --- |
| `_deploy/backend.tar.gz` | 317.7 KB | 后端源码打包产物 |
| `_deploy/frontend-dist.tar.gz` | 611.6 KB | 前端 `dist` 打包产物 |
| `backed/_vbashi.py` | 753 B | 一次性调试脚本（文件自述「执行完即删」） |

前两个是**部署时打出来的包**，`.gitignore` 里虽有 `dist/`，但 `.tar.gz` 匹配不到；第三个是排查「八市 / 第八市场」匹配问题时临时写的脚本。

三个加起来约 930 KB，且都可由源码或命令重新生成 —— **提交它们只会让仓库变胖，没有任何价值**。

已加入忽略规则：

```
# 部署打包产物（本地产物，勿提交）
_deploy/
# 一次性调试脚本（执行完即删）
backed/_vbashi.py
```

> `docs/部署指南.md` 则是**要提交**的 —— 它记录了面向阿里云 ECS 的真实上线步骤、nginx 反代配置与三处必改的坑，属于有价值的文档，归入 C18。

---

## 6. 建立 GitHub 远端并推送

### 6.1 在 GitHub 网页建仓库

- 仓库名建议：`ai-travel-assistant`
- **不要**勾选 `Add a README file` / `Add .gitignore` / `Choose a license`（会导致首次 push 冲突）
- 记下地址：`https://github.com/<你的用户名>/ai-travel-assistant.git`

### 6.2 认证方式（本机无 SSH key、无 gh CLI）

用 **HTTPS + Personal Access Token**：

1. GitHub → Settings → Developer settings → Personal access tokens → **Tokens (classic)** → Generate new token
2. 勾选 `repo` 权限，生成后复制（只显示一次）
3. 推送时用户名填 GitHub 用户名，**密码位置粘贴 token**

```powershell
git remote add github https://github.com/<你的用户名>/ai-travel-assistant.git
git push -u github gh:main
```

### 6.3 推送后让本地 main 指向新历史（可选）

```powershell
# 旧 main（含 Codeup 3 个提交）已备份到 backup-codeup-main
git checkout -B main gh
git branch -D gh

# Codeup 远端保持不动；若要继续推 Codeup，用：
# git push origin main --force     ← 会把 Codeup 历史也换成新的，谨慎
```

**风险提示**：`git push origin main --force` 会覆盖 Codeup 上的旧历史。若 Codeup 那边还有价值（例如评审已看过），**建议不要推 Codeup**，只让 GitHub 用新历史。

---

## 7. 回滚

任何一步出错都可以一键回到起点：

```powershell
cd <仓库根目录>

# 丢弃 gh 分支，回到原来的 main
git checkout main
git branch -D gh

# 清掉可能设过的环境变量
Remove-Item Env:GIT_AUTHOR_DATE, Env:GIT_COMMITTER_DATE -ErrorAction SilentlyContinue

# 若已推到 GitHub，删除该仓库或：
git remote remove github
```

`backup-codeup-main` 分支是额外保险，确认无误后可删：`git branch -D backup-codeup-main`

---

## 8. 必须知道的一个限制

**新历史里，每个提交包含的是该文件的「最终版本」，不是当时版本。**

因为工作区里只有最终文件，无法还原"同一文件被多次修改"的过程。典型影响：

| 文件 | 后果 |
| --- | --- |
| `schemas.py` | 最终版**已无 `budget` 字段** → 新历史里从未出现过预算 |
| `engine.py` | 最终版**已用 `selector`** → 从未出现过 `knapsack` |
| `main.py` | 最终版**已注册 Agent 路由** → C08 就有了 C14 才实现的接口 |
| `config.py` / `database.py` | 已含 RAG / vision / 票价配置与建表 → C02 就已完整 |

**因此 C08 等较早的提交无法独立运行**（引用了尚不存在的模块）。

这不是错误，而是"事后补历史"的固有代价。取舍建议：

- ✅ **值得**：提交按主题分组、信息清晰，`git log` 读起来是一条完整的建设脉络
- ❌ **不要期待**：能在任意提交上 `git checkout` 后跑通系统
- 📌 **答辩时的正确说法**：讲"这套架构是怎么分模块演进的"，而不是"我每一步都能跑"

如果确实需要「每个提交都能跑」，只有一条路：手工重放每一次真实修改（工作量远大于收益，不推荐）。

---

## 附录 A · 文件归属总表（177 个）

| 提交 | 目录/文件 | 数量 |
| --- | --- | --- |
| C01 | `.gitignore`、`backed/.env.example`、`backed/requirements.txt`、`fronted/travel/{.gitignore,.vscode,jsconfig.json,package.json,package-lock.json,vite.config.js,index.html,pc/index.html,README.md}` | 12 |
| C02 | `backed/app/{__init__.py,config.py,errors.py,db/,run.py}`、`backed/data/` | 8 |
| C03 | `backed/app/amap/` | 3 |
| C04 | `backed/app/algorithms/` | 9 |
| C05 | `backed/app/llm.py`、`backed/app/services/llm_json.py` | 2 |
| C06 | `backed/app/services/`（除 9 个） | 9 |
| C07 | `security.py`、`sms.py`、`deps.py`、`services/auth.py`、`services/history.py` | 5 |
| C08 | `main.py`、`schemas.py` | 2 |
| C09 | `tests/test_{algorithms,nl_parse,auth_history,live_http}.py` | 4 |
| C10 | `fronted/travel/`（除 8 个 Agent 文件） | 31 |
| C11 | `backed/app/rag/`、`tests/test_{rag,retrieval_eval}.py` | 9 |
| C12 | `backed/knowledge/`、`services/kb.py`、`scripts/` | 20 |
| C13 | `services/poi_prices.py`、`city_registry.py`、`poi_pool.py`、`docs/ticket-*.md` | 5 |
| C14 | `backed/app/agent/`、`agent_chat.py`、`agent_session.py`、5 个 Agent 测试 | 26 |
| C15 | `services/vision.py`、`tests/test_vision.py` | 2 |
| C16 | `tests/test_degradation.py` | 1 |
| C17 | 8 个 Agent 前端文件 | 8 |
| C18 | `docs/`（除 `ticket-*.md` 2 个） | 20 |
| C19 | `README.md` | 1 |
| C20~C22 | 仅修改已提交文件（`client.py` / `engine.py` / `cities.json` / `importer.py` / `nl_parse.py` / `.gitignore`），不新增文件 | 0 |
| **合计** | | **177** |

> **关于「跨提交重复出现在 add 范围里」**：C01 已提交的 9 个前端配置文件，会被 C10 的 `git add fronted/travel` 再次覆盖到；同理 C05 的 `llm_json.py` 也在 C06 的 `git add backed/app/services` 范围内。
> **这不是错误** —— git 只暂存「有改动」的文件，已提交且未修改的文件再次 `git add` 不会产生任何暂存内容。上面写的文件数已按实际暂存结果统计。

## 附录 B · 提交信息风格

项目 `.trae/rules/git-commit-message.md` 只要求「简洁」。本文档统一用：

```
<类型>(<模块>): <中文简述，20 字内>
```

类型：`feat` / `test` / `docs` / `chore` / `refactor` / `fix`

20 字以内的例子：
- `feat(rag): 中文分词、FTS5 与混合检索`
- `refactor: 移除预算系统`（本项目已发生，但新历史中不体现为独立提交）
