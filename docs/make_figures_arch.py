"""生成 Agent + RAG 预期架构图（Pillow 手绘，无 matplotlib）。

输出：docs/figures/agent_rag_arch.png

配色与 docs/make_figures.py 保持一致，便于插入同一份技术文档。

运行：<python> docs/make_figures_arch.py
"""
import os
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).resolve().parent / "figures" / "agent_rag_arch.png"

# ---------------- 配色（与前端主色 / make_figures.py 一致）----------------
PRIMARY = "#4A7DFF"
PRIMARY_DEEP = "#3B63D4"
PURPLE = "#7B68EE"
ACCENT = "#F0A020"
GREEN = "#4A8C5E"
RED = "#D65D4A"
DARK = "#1F2D3D"
MUTED = "#8A94A6"
LIGHT = "#EEF3FF"
LIGHT2 = "#F7F9FC"
BORDER = "#C9D8FF"
GREY = "#DDE3EC"

FONT_CANDIDATES = [
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/simhei.ttf",
    "C:/Windows/Fonts/simsun.ttc",
]
BOLD_CANDIDATES = [
    "C:/Windows/Fonts/msyhbd.ttc",
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/simhei.ttf",
]
_cache = {}


def font(size, bold=False):
    key = (size, bold)
    if key in _cache:
        return _cache[key]
    for path in (BOLD_CANDIDATES if bold else FONT_CANDIDATES):
        if os.path.exists(path):
            try:
                f = ImageFont.truetype(path, size)
                _cache[key] = f
                return f
            except Exception:
                continue
    f = ImageFont.load_default()
    _cache[key] = f
    return f


W, H = 1400, 1020
img = Image.new("RGB", (W, H), "#FFFFFF")
d = ImageDraw.Draw(img)


# ---------------- 绘图工具 ----------------
def group(x, y, w, h, title, color=BORDER, fill=LIGHT2, tc=MUTED):
    """分组容器：浅底 + 标题。"""
    d.rounded_rectangle([x, y, x + w, y + h], radius=12,
                        fill=fill, outline=color, width=2)
    if title:
        d.text((x + 14, y + 9), title, font=font(15, True), fill=tc)


def node(x, y, w, h, title, sub=None, color=PRIMARY, fill="#FFFFFF",
         tc=DARK, tsize=15, ssize=12):
    """节点：白底圆角 + 彩色描边，标题加粗、副文本灰。"""
    d.rounded_rectangle([x, y, x + w, y + h], radius=9,
                        fill=fill, outline=color, width=2)
    if sub:
        d.text((x + w / 2, y + h / 2 - 12), title, font=font(tsize, True),
               fill=tc, anchor="mm")
        d.text((x + w / 2, y + h / 2 + 11), sub, font=font(ssize),
               fill=MUTED, anchor="mm")
    else:
        d.text((x + w / 2, y + h / 2), title, font=font(tsize, True),
               fill=tc, anchor="mm")


def arrow(x1, y1, x2, y2, color=PRIMARY_DEEP, dash=False, width=2, label=None):
    """箭头；dash=True 为降级虚线。"""
    if dash:
        total = ((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5
        seg = max(int(total / 14), 1)
        for i in range(0, seg, 2):
            t0, t1 = i / seg, min((i + 1) / seg, 1.0)
            d.line([x1 + (x2 - x1) * t0, y1 + (y2 - y1) * t0,
                    x1 + (x2 - x1) * t1, y1 + (y2 - y1) * t1], fill=color, width=width)
    else:
        d.line([x1, y1, x2, y2], fill=color, width=width)
    # 箭头三角
    if abs(x2 - x1) >= abs(y2 - y1):
        s = 1 if x2 > x1 else -1
        d.polygon([(x2, y2), (x2 - 10 * s, y2 - 5), (x2 - 10 * s, y2 + 5)], fill=color)
    else:
        s = 1 if y2 > y1 else -1
        d.polygon([(x2, y2), (x2 - 5, y2 - 10 * s), (x2 + 5, y2 - 10 * s)], fill=color)
    if label:
        d.text(((x1 + x2) / 2 + 8, (y1 + y2) / 2), label, font=font(11), fill=MUTED)


# ---------------- 标题 ----------------
d.text((W / 2, 30), "AI 旅游助手 · Agent + RAG 预期架构",
       font=font(24, True), fill=DARK, anchor="mm")
d.text((W / 2, 60), "实线 = 主链路　·　虚线 = 降级路径　·　灰底 = 零改动保留",
       font=font(13), fill=MUTED, anchor="mm")

# ---------------- 1. 用户 ----------------
node(W / 2 - 75, 88, 150, 44, "用户自然语言", color=PURPLE,
     fill="#F3F0FF", tc=PURPLE)

# ---------------- 2. 前端 ----------------
group(40, 160, 250, 140, "前端 · Vue3 双端同工程")
node(56, 192, 218, 42, "AgentChat", "思考链 + 工具卡片", PRIMARY)
node(56, 242, 218, 42, "Timeline / Home / Mine", "现有页面保留", MUTED, fill=LIGHT2)

# ---------------- 3. 接入层 ----------------
group(330, 160, 250, 140, "接入层 · FastAPI :8080")
node(346, 192, 218, 42, "/api/agent/chat", "SSE 事件流（新）", GREEN)
node(346, 242, 218, 42, "现有 18 个路由", "行为不变", MUTED, fill=LIGHT2)

# ---------------- 4. Agent 运行时 ----------------
group(620, 160, 380, 262, "Agent 运行时")
node(636, 192, 172, 52, "orchestrator", "Function Calling 循环", PRIMARY)
node(820, 192, 164, 52, "RunBudget", "6 轮 / 8 工具 / 50s", ACCENT)
node(636, 258, 172, 52, "memory", "短期会话 + 长期画像", PRIMARY)
node(820, 258, 164, 52, "fallback", "确定性编排", RED)
d.text((636, 326), "轮数用尽 / 模型不支持 tools → 走 fallback，",
       font=font(11), fill=MUTED)
d.text((636, 344), "等价于今天的 /api/plan：最差体验 = 今天的体验",
       font=font(11), fill=MUTED)
d.rounded_rectangle([636, 368, 984, 404], radius=8, fill="#F0F4FF",
                    outline=BORDER, width=1)
d.text((810, 386), "触发条件：无 tools · 超预算 · 重复调用 · 异常",
       font=font(11), fill=PRIMARY_DEEP, anchor="mm")

# ---------------- 5. 工具层 ----------------
group(40, 440, 700, 250, "工具层 · 14 个工具（包装现有模块，不重写）")
node(58, 474, 320, 58, "plan_itinerary", "唯一产出 ItineraryVO 的工具", RED)
node(392, 474, 330, 58, "search_poi · search_knowledge",
     "resolve_city · get_weather", PRIMARY)
node(58, 548, 320, 58, "filter / score / route", "schedule / budget", PRIMARY)
node(392, 548, 330, 58, "recall_history · get_poi_detail",
     "estimate_intercity · get_city_intro", PRIMARY)
d.text((58, 626), "守卫：白名单 · Pydantic 二次校验 · 越权隔离（user_id 取自 ctx）· 超时兜底",
       font=font(11), fill=MUTED)
d.text((58, 646), "成本：按意图选择性挂载（首轮只给 6 个核心工具，省约 40% 输入 token）",
       font=font(11), fill=MUTED)

# ---------------- 6. RAG 层 ----------------
group(770, 440, 300, 250, "RAG 层")
node(786, 474, 268, 56, "retriever", "BM25 ∥ 向量 → RRF → 重排", PRIMARY)
node(786, 544, 268, 56, "pipeline + indexer", "切分 → bigram → 双索引", PURPLE)
node(786, 614, 268, 56, "embedding", "bge-m3 + 缓存", PURPLE)

# ---------------- 7. 闭环：引擎 / 存储 / 外部 ----------------
group(40, 720, 500, 150, "算法引擎（零改动）")
d.rounded_rectangle([58, 754, 522, 850], radius=9, fill="#FFFBF0",
                    outline=ACCENT, width=2)
d.text((290, 776), "engine.solve()", font=font(16, True), fill=DARK, anchor="mm")
d.text((290, 802), "地理编码 → 天气 → POI → 候选筛选 → KMeans++ 聚类",
       font=font(12), fill=DARK, anchor="mm")
d.text((290, 826), "→ 加权背包 → 路径优化 → 时间排程 → 预算兜底",
       font=font(12), fill=DARK, anchor="mm")

group(560, 720, 250, 150, "存储 · SQLite app.db")
node(576, 752, 218, 34, "users / itineraries", "现有，不动", MUTED, fill=LIGHT2, tsize=13, ssize=11)
node(576, 792, 218, 34, "documents / chunks / embeddings", "RAG", PURPLE, tsize=11, ssize=11)
node(576, 832, 218, 34, "sessions / messages / profiles", "Agent + 记忆", PRIMARY, tsize=11, ssize=11)

group(830, 720, 240, 150, "外部服务")
node(846, 752, 208, 34, "高德 POI / 天气 / 路径", None, GREEN, tsize=12)
node(846, 792, 208, 34, "LLM · chat + tools", None, GREEN, tsize=12)
node(846, 832, 208, 34, "Embedding API", None, GREEN, tsize=12)

# ---------------- 连线 ----------------
arrow(W / 2, 132, W / 2, 158)                                  # 用户 → 前端
arrow(250, 213, 328, 213)                                      # AgentChat → agent/chat
arrow(50, 243, 328, 262)                                       # Timeline → 现有路由
arrow(455, 213, 618, 218)                                      # 接入层 → orchestrator
arrow(700, 424, 700, 470)                                      # Agent → 工具层（走 x=700 空档）
arrow(1000, 320, 1074, 498, color=PURPLE)                      # Agent → RAG（从右侧直线绕入）
arrow(330, 626, 330, 752, color=ACCENT)                        # 工具层 → 算法引擎（单条聚合）
arrow(455, 626, 700, 750, color=PRIMARY_DEEP)                  # Agent 会话/记忆 → 存储落库
arrow(1074, 680, 1074, 738, color=PURPLE)                      # RAG → 存储（垂直短连）
d.text((1180, 566), "索引后台异步", font=font(11), fill=MUTED, anchor="mm")
d.text((1180, 586), "检索本地 SQLite", font=font(11), fill=MUTED, anchor="mm")
arrow(920, 646, 950, 752, color=GREEN)                         # embedding → 外部服务

# 降级虚线：orchestrator → fallback
arrow(812, 244, 818, 258, color=RED, dash=True)
d.text((795, 434), "降级", font=font(11), fill=RED)

# ---------------- 图例 ----------------
d.rounded_rectangle([560, 890, 1070, 995], radius=10, fill=LIGHT2, outline=GREY, width=1)
d.text((578, 902), "五条要点", font=font(14, True), fill=DARK)
for i, line in enumerate([
    "① 算法引擎零改动，plan_itinerary 是它唯一入口",
    "② 唯一产出 ItineraryVO 的工具 → 景点/时间/费用仍只由算法决定",
    "③ 兜底路径与主链路等价：最差体验 = 今天的体验",
    "④ RAG 只被 Agent 读，不被 Agent 写（索引后台异步）",
    "⑤ 新表与老表不交叉，删新表不影响既有数据",
]):
    d.text((578, 926 + i * 15), line, font=font(11), fill=MUTED)

OUT.parent.mkdir(parents=True, exist_ok=True)
# 轻微降采样让线条更平滑
try:
    img = img.resize((int(W * 0.95), int(H * 0.95)), Image.LANCZOS)
except Exception:
    img = img.resize((int(W * 0.95), int(H * 0.95)))
img.save(OUT)
print(f"saved: {OUT}  ({img.size[0]}x{img.size[1]})")
