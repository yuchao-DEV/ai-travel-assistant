"""记忆系统：短期上下文 + 长期画像。

## 一条不可违背的纪律

**画像永远只是"建议"，与用户本次的显式输入冲突时，一律以本次为准。**

这不是靠代码里的 if/else 保证的，而是靠**提示词的写法**——见
:func:`render_profile_prompt`：画像段落带显式的优先级声明，且显式输入会被并列
列出并标注"本次要求（优先）"。模型看到两个来源时才有依据做取舍；
如果只把画像塞进去，模型很可能把它当成硬约束，反而覆盖用户刚说的话。

## 为什么规则优先、LLM 只做兜底

抽取"不吃辣""带爸妈""别太累"这类信息，正则就够，而且：

- **确定性**：同样的输入永远同样的结果，可离线单测
- **零成本**：不花 token、不走网络
- **不幻觉**：不会把"我随便吃点"抽成"素食"

LLM 只在这一层完全抽不到、且样本足够时才兜底（本阶段暂不启用）。
"""
import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from app.db.database import get_conn, utc_now_iso

logger = logging.getLogger("app.agent.memory")

# ---------------------------------------------------------------- 规则表

# 饮食禁忌
_DIETARY_RULES: Dict[str, Tuple[str, ...]] = {
    "不吃辣": ("不吃辣", "不能吃辣", "忌辣", "怕辣", "不要辣"),
    "素食": ("素食", "吃素", "全素"),
    "不吃海鲜": ("不吃海鲜", "海鲜过敏"),
    "清真": ("清真", "穆斯林"),
    "不吃香菜": ("不吃香菜", "不要香菜"),
}

# 同行人
_COMPANION_RULES: Dict[str, Tuple[str, ...]] = {
    # 取值必须与 ``schemas.VALID_COMPANIONS`` 一致（独自/家庭/情侣/朋友/老人），
    # 否则画像里的同行人前端勾不上。
    # 旧表里有一项叫「儿童」——它不是合法值，「家庭」则从未被产出过，
    # 导致记忆里"带孩子"的偏好永远无法映射到表单的「家庭」。
    "老人": ("带爸妈", "带父母", "带老人", "和爸妈", "老人同行", "陪爸妈",
             "带长辈", "和长辈", "爷爷", "奶奶", "外公", "外婆"),
    "家庭": ("带孩子", "带娃", "带儿子", "带女儿", "亲子", "小朋友",
             "孩子", "儿子", "女儿", "全家", "一家人", "家庭"),
    "情侣": ("和对象", "和女友", "和男友", "情侣", "二人世界", "蜜月"),
    "朋友": ("和朋友", "几个朋友", "同学一起", "同事一起"),
}

# 节奏倾向（映射到 profile 的取值，保持与 schemas.VALID_PROFILES 一致）
_PACE_RULES: Dict[str, Tuple[str, ...]] = {
    "悠闲": ("别太累", "轻松", "慢一点", "休闲", "不赶", "悠闲", "少去几个"),
    "尽览": ("多逛几个", "特种兵", "尽量多", "多去几个", "排满", "高效", "紧凑"),
    "经典": ("口碑", "高评分", "值得去", "必看", "经典", "舒适"),
    # 「省钱」类词**故意不映射**：预算系统移除后按预算选点已不存在。
    # 把它们映射到某个 profile 是编造，静默忽略也是欺骗——应走 notes 提示用户。
}

# 预算档位（元/天）
_BUDGET_BANDS = (("低", 400.0), ("中", 1200.0))     # 超过 1200 算"高"

# confidence 随样本量上升。**样本 1 次不提升可信度**——一次行程说明不了偏好，
# 所以 n=1 仍落在 0.3 以下，提示词会走"样本很少，请主动确认"那一档。
_CONFIDENCE_BY_SAMPLE = {0: 0.0, 1: 0.25, 2: 0.5, 3: 0.7, 4: 0.78}
_CONFIDENCE_MAX = 0.85


def _confidence_for(sample_size: int) -> float:
    if sample_size <= 0:
        return 0.0
    return _CONFIDENCE_BY_SAMPLE.get(sample_size, _CONFIDENCE_MAX)


# ---------------------------------------------------------------- 规则抽取

def extract_from_text(text: str) -> Dict[str, List[str]]:
    """从一句话里按规则抽出画像字段（无 LLM）。

    返回 ``{"companions": [...], "dietary": [...], "pace": [...]}``，
    没抽到的字段不出现在结果里（区分"没提到"和"提到了空"）。
    """
    content = text or ""
    out: Dict[str, List[str]] = {}
    dietary = [k for k, words in _DIETARY_RULES.items()
               if any(w in content for w in words)]
    if dietary:
        out["dietary"] = dietary
    companions = [k for k, words in _COMPANION_RULES.items()
                  if any(w in content for w in words)]
    if companions:
        out["companions"] = companions
    pace = [k for k, words in _PACE_RULES.items()
            if any(w in content for w in words)]
    if pace:
        out["pace"] = pace
    return out


def _budget_band(per_day: float) -> str:
    for label, limit in _BUDGET_BANDS:
        if per_day <= limit:
            return label
    return "高"


def _median(values: List[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2


def _mode(values: List[str]) -> Optional[str]:
    """众数；并列时返回 None（不做任意选择）。"""
    if not values:
        return None
    counts: Dict[str, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    top = max(counts.values())
    winners = [k for k, v in counts.items() if v == top]
    return winners[0] if len(winners) == 1 else None


def infer_from_history(itineraries: List[Dict[str, Any]]) -> Dict[str, Any]:
    """从历史行程统计出画像（无 LLM）。

    ``itineraries`` 每项含 ``profile`` / ``budget`` / ``days`` / ``tags``。
    **样本 < 2 时 confidence 不提升**——一次行程说明不了偏好。
    """
    profiles = [it["profile"] for it in itineraries if it.get("profile")]
    per_day = [
        float(it.get("budget") or 0) / max(1, int(it.get("days") or 1))
        for it in itineraries if it.get("budget")
    ]
    tag_counts: Dict[str, int] = {}
    for it in itineraries:
        for tag in it.get("tags") or []:
            tag_counts[tag] = tag_counts.get(tag, 0) + 1

    interests: Dict[str, float] = {}
    if tag_counts:
        total = sum(tag_counts.values())
        interests = {k: round(v / total, 3) for k, v in
                     sorted(tag_counts.items(), key=lambda kv: -kv[1])}

    result: Dict[str, Any] = {"sample_size": len(itineraries)}
    pace = _mode(profiles)
    if pace:
        result["pace"] = pace
    if per_day:
        result["budget_band"] = _budget_band(_median(per_day))
        result["budget_median"] = _median([float(it.get("budget") or 0)
                                           for it in itineraries if it.get("budget")])
    if interests:
        result["interests"] = interests
    return result


# ---------------------------------------------------------------- 画像读写

def load_profile(user_id: Optional[int]) -> Optional[Dict[str, Any]]:
    """读取长期画像；没有或匿名时返回 None。"""
    if not user_id:
        return None
    try:
        with get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM user_profiles WHERE user_id = ?", (user_id,)
            ).fetchone()
    except Exception as exc:
        logger.warning("读取画像失败：%s", exc)
        return None
    if not row:
        return None
    return {
        "user_id": row["user_id"],
        "pace": row["pace"],
        "budget_band": row["budget_band"],
        "companions": json.loads(row["companions"] or "[]"),
        "dietary": json.loads(row["dietary"] or "[]"),
        "interests": json.loads(row["interests"] or "{}"),
        "confidence": float(row["confidence"] or 0),
        "sample_size": int(row["sample_size"] or 0),
        "updated_at": row["updated_at"],
    }


def save_profile(user_id: int, profile: Dict[str, Any]) -> None:
    """写入画像（整行替换）。"""
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO user_profiles
               (user_id, pace, budget_band, companions, dietary, interests,
                confidence, sample_size, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?)
               ON CONFLICT(user_id) DO UPDATE SET
                 pace=excluded.pace, budget_band=excluded.budget_band,
                 companions=excluded.companions, dietary=excluded.dietary,
                 interests=excluded.interests, confidence=excluded.confidence,
                 sample_size=excluded.sample_size, updated_at=excluded.updated_at""",
            (user_id, profile.get("pace"), profile.get("budget_band"),
             json.dumps(profile.get("companions") or [], ensure_ascii=False),
             json.dumps(profile.get("dietary") or [], ensure_ascii=False),
             json.dumps(profile.get("interests") or {}, ensure_ascii=False),
             float(profile.get("confidence") or 0),
             int(profile.get("sample_size") or 0), utc_now_iso()),
        )


def clear_profile(user_id: int) -> bool:
    """删除画像（隐私）。返回是否确实删除了记录。"""
    with get_conn() as conn:
        cur = conn.execute("DELETE FROM user_profiles WHERE user_id = ?", (user_id,))
        return cur.rowcount > 0


def observe_and_update(user_id: Optional[int], *, message: str = "",
                       itinerary: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """观察一次会话并更新画像。

    两个来源合并：**本次对话里说出来的**（规则抽取）优先于**历史统计**——
    用户刚说的话比历史模式更可信。匿名用户不做任何事。
    """
    if not user_id:
        return None
    existing = load_profile(user_id) or {
        "companions": [], "dietary": [], "interests": {},
        "sample_size": 0, "confidence": 0.0,
    }

    extracted = extract_from_text(message)

    # 画像里的集合字段做并集（说过的禁忌不该被后来的话覆盖掉）
    companions = sorted(set(existing.get("companions") or []) | set(extracted.get("companions") or []))
    dietary = sorted(set(existing.get("dietary") or []) | set(extracted.get("dietary") or []))

    profile = {
        "companions": companions,
        "dietary": dietary,
        "interests": existing.get("interests") or {},
        "pace": existing.get("pace"),
        "budget_band": existing.get("budget_band"),
        "sample_size": int(existing.get("sample_size") or 0),
    }

    # 本次话里明确说的节奏优先于历史统计
    if extracted.get("pace"):
        profile["pace"] = extracted["pace"][0]

    # 有行程产出 → 样本 +1，并用历史统计补上下文
    if itinerary:
        profile["sample_size"] += 1
        history = list_profile_itineraries(user_id)
        stats = infer_from_history(history)
        profile["interests"] = stats.get("interests") or profile["interests"]
        if not extracted.get("pace") and stats.get("pace"):
            profile["pace"] = stats["pace"]
        if stats.get("budget_band"):
            profile["budget_band"] = stats["budget_band"]

    profile["confidence"] = _confidence_for(profile["sample_size"])
    save_profile(user_id, profile)
    return profile


def list_profile_itineraries(user_id: int, limit: int = 20) -> List[Dict[str, Any]]:
    """取用户历史行程的画像相关字段（用于统计）。"""
    try:
        with get_conn() as conn:
            rows = conn.execute(
                "SELECT profile, budget, days, tags FROM itineraries "
                "WHERE user_id = ? ORDER BY id DESC LIMIT ?", (user_id, limit)
            ).fetchall()
    except Exception as exc:
        logger.warning("读取历史行程失败：%s", exc)
        return []
    out: List[Dict[str, Any]] = []
    for row in rows:
        try:
            tags = json.loads(row["tags"] or "[]")
        except (json.JSONDecodeError, TypeError):
            tags = []
        out.append({"profile": row["profile"], "budget": row["budget"],
                    "days": row["days"], "tags": tags})
    return out


# ---------------------------------------------------------------- 提示词渲染

_PACE_LABEL = {"经典": "经典", "尽览": "尽览", "悠闲": "悠闲"}


def render_profile_prompt(profile: Optional[Dict[str, Any]],
                          explicit: Optional[Dict[str, Any]] = None) -> str:
    """把画像渲染成**建议句式**的提示词片段。

    ``explicit`` 是用户本次的显式输入（profile / pace / companions …）。
    两者都在时，显式输入会被标注「本次要求（优先）」，画像标注「仅供参考」——
    **这是"绝不越权覆盖显式输入"的唯一保障**，比任何代码判断都可靠：
    模型能同时看到两个来源，才知道该听谁的。
    """
    explicit = explicit or {}
    lines: List[str] = []

    has_profile = bool(profile) and (
        profile.get("pace") or profile.get("budget_band")
        or profile.get("companions") or profile.get("dietary")
        or profile.get("interests")
    )

    if explicit:
        req: List[str] = []
        if explicit.get("profile"):
            req.append(f"花费档次={explicit['profile']}")
        if explicit.get("pace"):
            req.append(f"行程节奏={explicit['pace']}")
        if explicit.get("companions"):
            req.append(f"同行人={'、'.join(explicit['companions'])}")
        if explicit.get("days"):
            req.append(f"天数={explicit['days']}")
        if explicit.get("budget"):
            req.append(f"预算={explicit['budget']}")
        if explicit.get("tags"):
            req.append(f"兴趣={'、'.join(explicit['tags'])}")
        if explicit.get("must_go"):
            req.append(f"必去={'、'.join(explicit['must_go'])}")
        if req:
            lines.append("【本次要求（优先，必须遵守）】" + "；".join(req))

    if has_profile:
        conf = float(profile.get("confidence") or 0)
        items: List[str] = []
        if profile.get("pace"):
            items.append(f"出行节奏倾向：{profile['pace']}")
        if profile.get("budget_band"):
            items.append(f"预算档位：{profile['budget_band']}")
        if profile.get("companions"):
            items.append(f"同行人：{'、'.join(profile['companions'])}")
        if profile.get("dietary"):
            items.append(f"饮食：{'、'.join(profile['dietary'])}")
        interests = profile.get("interests") or {}
        if interests:
            top = list(interests.items())[:3]
            items.append("兴趣倾向：" + "、".join(f"{k}({v:.0%})" for k, v in top))
        lines.append("【用户长期偏好（仅供参考，若与本次请求冲突，一律以本次请求为准）】"
                     + "；".join(items))
        lines.append(f"画像置信度：{conf:.1f}")
        if conf < 0.3:
            lines.append("说明：画像样本很少，信息不足时请主动向用户确认，"
                         "不要按画像推测。")
        elif conf < 0.7:
            lines.append("说明：画像样本一般，与用户本次说法不一致时以本次为准。")

    return "\n".join(lines)
