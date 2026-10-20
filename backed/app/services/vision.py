"""图片地标识别：视觉模型识图 → 高德校验 → 可信结果。

## 为什么这一层需要"识别 + 校验"两段

视觉模型会**流畅地编造**：给它一张普通街景，它能自信地说"这是乌镇西栅"。
所以模型输出只能当作**候选**，必须拿名字去高德查一遍：

- 查得到 → 用高德的**标准名、坐标、所在城市**，而不是模型说的
- 查不到 → 如实返回 `recognized=False`，**不把模型的猜测当事实**

这是本项目一贯的做法（算法决定，LLM 只做它真正擅长的事——这里是"感知"）。

## 实测准确率（必须如实告知维护者）

用高德真实照片测了 3 张：

| 真值 | 模型输出 | 置信度 | 结果 |
| --- | --- | --- | --- |
| 苏州博物馆(本馆) | 苏州博物馆 | high | 对 |
| 留园 | 拙政园（候选含留园） | **high** | **错** |
| 艺圃 | 拙政园（候选含留园、狮子林） | medium | 错 |

结论两条：

1. **辨识度高的地标（现代建筑、标志性塔楼）准**；江南园林这类外观高度相似的
   成组景点**分不清**——这是图片信息本身不足，不是模型缺陷。
2. **`confidence` 不能当门禁**：留园那次是 high 却错了。所以识别结果必须作为
   **候选**交给用户确认，不能直接采用。

这决定了产品定位：这是「照片辅助找地点」（省打字），不是「拍照自动规划」。

## 三条对外承诺

1. `recognized=False` 时**不返回伪造的 poi**，前端显示"没认出可靠地标"
2. 置信度原样透出，`low` 时前端要提示"可能不准"
3. `alternatives` 一并返回，多候选时交给用户判断
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
from typing import Any, Dict, List, Optional

import httpx

from app.amap.client import _parse_amap_json
from app.config import (
    AMAP_KEY,
    MAX_GUIDE_PLACES,
    VISION_API_KEY,
    VISION_BASE_URL,
    VISION_MAX_BYTES,
    VISION_MODEL,
    VISION_RETRIES,
    VISION_TIMEOUT,
)

logger = logging.getLogger("app.vision")

# 允许的图片类型（浏览器/手机都在这几种之内）
ALLOWED_MIME = {
    "image/jpeg": "jpg",
    "image/jpg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
    "image/heic": "heic",
    "image/heif": "heif",
}

PROMPT = """你是旅行地标识别助手。看这张照片，判断它拍摄的是哪个**具体地点**。

严格规则：
- 只回答你能从画面里**真正辨认**的地点。看不出就返回 null，**绝对不要猜**。
- 给最具体的名字：「雷峰塔」优于「西湖」优于「杭州」。
- 画面是室内、食物、人物特写、商品、文档截图 → 一律返回 null。
- 有多个可能时，把最可能的放 name，其余放 alternatives。

只输出 JSON，不要解释，不要 markdown 代码块：
{"name": "地点名或null", "city": "所在城市或null", "confidence": "high|medium|low", "alternatives": ["其他可能的地名"], "reason": "一句话依据"}"""


def vision_enabled() -> bool:
    """是否配好了视觉模型。没配就让调用方走降级，而不是报 500。"""
    return bool(VISION_API_KEY)


def normalize_mime(content_type: Optional[str]) -> Optional[str]:
    """把 content-type 归一成白名单里的键。不支持的类型返回 None。"""
    if not content_type:
        return None
    mime = content_type.split(";")[0].strip().lower()
    return mime if mime in ALLOWED_MIME else None


def data_url(image_bytes: bytes, mime: str) -> str:
    """转成 data URL。图片**不落盘**，只在内存里存活到这个请求结束。"""
    return f"data:{mime};base64,{base64.b64encode(image_bytes).decode('ascii')}"


def _extract_json(text: str) -> Optional[Dict[str, Any]]:
    """从模型回复里抠出 JSON。模型常带 ```json 包裹或前后废话，都要能容错。"""
    if not text:
        return None
    raw = text.strip()
    # 去掉 markdown 代码围栏
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.S)
    if fence:
        raw = fence.group(1)
    else:
        # 退一步：取第一个 { 到最后一个 }
        start, end = raw.find("{"), raw.rfind("}")
        if start < 0 or end <= start:
            return None
        raw = raw[start:end + 1]
    try:
        obj = json.loads(raw)
        return obj if isinstance(obj, dict) else None
    except (ValueError, TypeError):
        logger.warning("视觉模型返回的不是合法 JSON：%s", text[:200])
        return None


async def _call_vision(image_bytes: bytes, mime: str) -> Optional[Dict[str, Any]]:
    """识别地标（固用 PROMPT）。见 _call_vision_with_prompt。"""
    return await _call_vision_with_prompt(image_bytes, mime, PROMPT)


async def _call_vision_with_prompt(image_bytes: bytes, mime: str,
                                   prompt: str) -> Optional[Dict[str, Any]]:
    """调用视觉模型（可指定 prompt）。带重试——实测服务端会**随机断连**。

    ## 为什么要重试（实测记录，2026-10）

    同一个请求、同样的参数，`max_tokens` 从 800 到 2500 连续 4 次全部
    `httpx.RemoteProtocolError: Server disconnected without sending a response`，
    而 `max_tokens=3000` 立刻成功（9.4 秒，结果正确）。

    **所以断连与 max_tokens 无关，是服务端抖动。** 结论：必须重试，
    不能把一次断连当成"识别不可用"报给用户。
    """
    url = f"{VISION_BASE_URL.rstrip('/')}/chat/completions"
    payload = {
        "model": VISION_MODEL,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "image_url",
                     "image_url": {"url": data_url(image_bytes, mime)}},
                    {"type": "text", "text": prompt},
                ],
            }
        ],
        "temperature": 0.1,   # 识别任务要稳定，不要发挥
        # **必须给足**：deepseek-flash 是思考模型，答案先写在
        # reasoning_content 里。实测 max_tokens=300 时思考没结束就被截断，
        # content 返回空字符串 —— 会被误判成"模型不支持识图"。
        "max_tokens": 3000,
    }
    headers = {
        "Authorization": f"Bearer {VISION_API_KEY}",
        "Content-Type": "application/json",
    }

    last_err = ""
    for attempt in range(1, VISION_RETRIES + 1):
        try:
            async with httpx.AsyncClient(timeout=VISION_TIMEOUT) as client:
                resp = await client.post(url, json=payload, headers=headers)
            if resp.status_code != 200:
                last_err = f"HTTP {resp.status_code}"
                logger.warning("视觉模型 %s（第 %d 次）：%s",
                               last_err, attempt, resp.text[:160])
            else:
                body = resp.json()
                content = (body.get("choices") or [{}])[0].get("message", {}).get("content")
                if isinstance(content, list):
                    content = "".join(
                        part.get("text", "") for part in content
                        if isinstance(part, dict))
                parsed = _extract_json(content or "")
                if parsed:
                    return parsed
                # 200 但没解析出 JSON：可能是思考被截断，重试一次也许就好
                last_err = "返回内容不是合法 JSON"
                logger.warning("视觉模型 %s（第 %d 次）：%r",
                               last_err, attempt, (content or "")[:120])
        except httpx.TimeoutException as exc:
            # 注意：str(httpx.ReadTimeout()) 常常是**空串**，
            # 直接打 exc 只会得到"视觉模型调用失败："这种没信息的日志。
            last_err = f"超时 {VISION_TIMEOUT}s"
            logger.warning("视觉模型超时（%s，第 %d 次）", type(exc).__name__, attempt)
        except Exception as exc:
            # RemoteProtocolError 也走这里：服务端随机断连，靠重试兜
            last_err = type(exc).__name__
            logger.warning("视觉模型调用失败（%s，第 %d 次）：%s",
                           type(exc).__name__, attempt, exc)

        if attempt < VISION_RETRIES:
            await asyncio.sleep(1.5 * attempt)   # 1.5s / 3s 退避

    logger.warning("视觉模型重试 %d 次仍失败：%s", VISION_RETRIES, last_err)
    return None


async def _amap_get(endpoint: str, params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """调高德并做反爬检测。不是 JSON（处罚页）时返回 None 而不是抛错。"""
    if not AMAP_KEY:
        return None
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                f"https://restapi.amap.com/v3/{endpoint}", params={"key": AMAP_KEY, **params})
        data = _parse_amap_json(resp.text, endpoint)
        if not data or str(data.get("status")) != "1":
            return None
        return data
    except Exception as exc:
        logger.warning("高德 %s 调用失败：%s", endpoint, exc)
        return None


def _loc_to_lat_lng(location: Any) -> Optional[tuple]:
    """高德的 'lng,lat' 字符串 → (lat, lng)。"""
    if not isinstance(location, str) or "," not in location:
        return None
    try:
        lng_s, lat_s = location.split(",", 1)
        return float(lat_s), float(lng_s)
    except (TypeError, ValueError):
        return None


async def _verify_with_amap(name: str,
                           city_hint: str = "") -> Optional[Dict[str, Any]]:
    """拿地名去高德核实，返回**高德的标准结果**（不是模型说的）。

    返回 ``{name, city, district, address, lat, lng, poi_type}``；核实不到返回 None。

    ## 为什么要三级降级

    实测高德各端点**可用性不一致**（2026-10 观测）：

    - ``place/text`` 经常返回反爬处罚页（HTML，不是 JSON）
    - ``geocode`` 有时返回 ``SERVICE_NOT_AVAILABLE``
    - ``place/around`` 一直可用，但必须先有一个坐标中心

    所以按 ①→②→③ 依次试，任何一级成功即返回：
    ① 名字全国搜（最准）
    ② 把名字当地址解析（不受 place/text 限流影响）
    ③ 用模型给的城市名拿到城市中心，再按名字周边搜
    """
    if not name:
        return None

    # ---- ① place/text：按名字全国搜 ----
    data = await _amap_get("place/text", {
        "keywords": name, "offset": 5, "page": 1, "extensions": "base"})
    pois = (data or {}).get("pois") or []
    for poi in pois:
        latlng = _loc_to_lat_lng(poi.get("location"))
        if latlng:
            return {
                "name": poi.get("name") or name,
                "city": (poi.get("cityname") or "").strip(),
                "district": (poi.get("adname") or "").strip(),
                "address": poi.get("address")
                if isinstance(poi.get("address"), str) else "",
                "lat": latlng[0], "lng": latlng[1],
                "poi_type": poi.get("type") or "",
                "verified_by": "place/text",
            }

    # ---- ② geocode：把名字当地址解析 ----
    data = await _amap_get("geocode", {"address": name})
    for g in (data or {}).get("geocodes") or []:
        latlng = _loc_to_lat_lng(g.get("location"))
        if latlng:
            return {
                "name": g.get("formatted_address") or name,
                "city": (g.get("city") or "").strip()
                if isinstance(g.get("city"), str) else "",
                "district": (g.get("district") or "").strip()
                if isinstance(g.get("district"), str) else "",
                "address": g.get("formatted_address") or "",
                "lat": latlng[0], "lng": latlng[1],
                "poi_type": "",
                "verified_by": "geocode",
            }

    # ---- ③ 城市中心 + 周边搜（只在模型给了城市时可用）----
    if city_hint:
        city_data = await _amap_get("geocode", {"address": city_hint})
        center = None
        for g in (city_data or {}).get("geocodes") or []:
            center = _loc_to_lat_lng(g.get("location"))
            if center:
                break
        if center:
            data = await _amap_get("place/around", {
                "keywords": name,
                "location": f"{center[1]},{center[0]}",
                "radius": 50000,
                "offset": 5,
                "page": 1,
            })
            for poi in (data or {}).get("pois") or []:
                latlng = _loc_to_lat_lng(poi.get("location"))
                if latlng:
                    return {
                        "name": poi.get("name") or name,
                        "city": city_hint,
                        "district": (poi.get("adname") or "").strip(),
                        "address": poi.get("address")
                        if isinstance(poi.get("address"), str) else "",
                        "lat": latlng[0], "lng": latlng[1],
                        "poi_type": poi.get("type") or "",
                        "verified_by": "place/around",
                    }

    logger.info("高德三级校验都没找到：%s（城市提示=%s）", name, city_hint or "无")
    return None


# ==================================================================
# 攻略图 → 行程文字（喂给 importer.import_text）
# ==================================================================

GUIDE_PROMPT = """这是旅行攻略图/笔记截图。把图里提到的**地点**按图上顺序提取出来。

严格规则：
- 只提取图上**真实写了**的地点，一个都不要补。
- 只提取"要去的地方"（景点/街区/市场/公园/博物馆/沙滩之类）。
  餐厅、酒店、商店如果图里列为行程站点，也算。
- 忽略：交通方式（车程/步行/公交/地铁）、时长、门票金额、开放时间、广告词、
  水印、作者昵称、互动按钮、"点赞收藏"这类文字。
- city 只填图上明确写出的城市；没写就填 null，**不要根据景点反推城市**。

只输出 JSON，不要解释，不要 markdown：
{"city": "城市名或null", "places": ["按图上顺序的地点名"], "notes": "图上其他有关行程的信息，一句话"}"""


async def extract_guide(image_bytes: bytes,
                        content_type: Optional[str]) -> Dict[str, Any]:
    """从攻略图里提取城市与地点名，供 ``importer.import_text`` 使用。

    返回::

        {"ok": bool, "city": str|None, "places": [str], "notes": str, "reason": str}

    ``ok=False`` 时调用方应把 ``reason`` 原样展示给用户，**不要伪造结果**。
    """
    if not vision_enabled():
        return {"ok": False, "city": None, "places": [], "notes": "",
                "reason": "图片识别服务未配置（缺少 VISION_API_KEY），请改用粘贴文字导入。"}

    if len(image_bytes) > VISION_MAX_BYTES:
        return {"ok": False, "city": None, "places": [], "notes": "",
                "reason": f"图片过大（{len(image_bytes) // 1024} KB），"
                          f"请压缩到 {VISION_MAX_BYTES // 1024 // 1024} MB 以内。"}

    mime = normalize_mime(content_type)
    if not mime:
        return {"ok": False, "city": None, "places": [], "notes": "",
                "reason": "只支持 JPG / PNG / WebP / HEIC 格式的图片。"}

    parsed = await _call_vision_with_prompt(image_bytes, mime, GUIDE_PROMPT)
    if not parsed:
        return {"ok": False, "city": None, "places": [], "notes": "",
                "reason": "识图服务暂时不可用（网络或额度），请改用粘贴文字导入。"}

    raw_places = parsed.get("places")
    places: List[str] = []
    if isinstance(raw_places, list):
        for x in raw_places:
            if isinstance(x, str) and x.strip():
                name = x.strip()
                # 去重但保序：图上同一地点可能出现多次（比如简介里再提一次）
                if name not in places:
                    places.append(name)

    city = parsed.get("city")
    city = city.strip() if isinstance(city, str) and city.strip() else None
    notes = parsed.get("notes")
    notes = notes.strip() if isinstance(notes, str) else ""

    if not places:
        return {"ok": False, "city": city, "places": [], "notes": notes,
                "reason": "这张图里没有提取到可识别的地点。"
                          "可以换一张攻略图，或改用粘贴文字导入。"}

    return {"ok": True, "city": city, "places": places[:MAX_GUIDE_PLACES],
            "notes": notes, "reason": ""}


async def recognize(image_bytes: bytes, content_type: Optional[str]) -> Dict[str, Any]:
    """识别图片里的地标。

    返回结构::

        {
          "recognized": bool,          # 是否认出了**经高德核实**的地标
          "poi": {...} | None,         # 高德核实后的地点（不是模型原话）
          "model_guess": {...} | None, # 模型的原始判断（含置信度，仅供展示）
          "reason": str,               # 给用户看的一句话结论
          "degraded": bool,            # 是否因未配模型而降级
        }
    """
    if not vision_enabled():
        return {
            "recognized": False,
            "poi": None,
            "candidates": [],
            "model_guess": None,
            "degraded": True,
            "reason": "图片识别服务未配置（缺少 VISION_API_KEY），请手动输入地点。",
        }

    if len(image_bytes) > VISION_MAX_BYTES:
        return {
            "recognized": False,
            "poi": None,
            "candidates": [],
            "model_guess": None,
            "degraded": False,
            "reason": f"图片过大（{len(image_bytes) // 1024} KB），"
                      f"请压缩到 {VISION_MAX_BYTES // 1024 // 1024} MB 以内。",
        }

    mime = normalize_mime(content_type)
    if not mime:
        return {
            "recognized": False,
            "poi": None,
            "candidates": [],
            "model_guess": None,
            "degraded": False,
            "reason": "只支持 JPG / PNG / WebP / HEIC 格式的图片。",
        }

    guess = await _call_vision(image_bytes, mime)
    if not guess:
        return {
            "recognized": False,
            "poi": None,
            "candidates": [],
            "model_guess": None,
            "degraded": False,
            "reason": "识图服务暂时不可用（网络或额度），请手动输入地点。",
        }

    name = (guess.get("name") or "").strip() if isinstance(guess.get("name"), str) else ""
    confidence = str(guess.get("confidence") or "").lower() or "low"
    alternatives: List[str] = [
        a for a in (guess.get("alternatives") or []) if isinstance(a, str)
    ][:3]

    if not name:
        return {
            "recognized": False,
            "poi": None,
            "candidates": [],
            "model_guess": {"confidence": confidence, "reason": guess.get("reason") or ""},
            "degraded": False,
            "reason": "画面里没有可辨认的地标。可以换成景点外观照片，或手动输入地点。",
        }

    # ---- 关键一步：模型说的话不算数，高德查到才算 ----
    city_hint = guess.get("city") if isinstance(guess.get("city"), str) else ""
    verified = await _verify_with_amap(name, city_hint)
    if not verified:
        # 试一次候选名，但只在模型自己给了候选时
        for alt in alternatives:
            verified = await _verify_with_amap(alt, city_hint)
            if verified:
                name = alt
                confidence = "low"   # 换用候选命中，置信度必须降级
                break

    if not verified:
        return {
            "recognized": False,
            "poi": None,
            "candidates": [],
            "model_guess": {
                "name": name,
                "confidence": confidence,
                "alternatives": alternatives,
                "reason": guess.get("reason") or "",
            },
            "degraded": False,
            "reason": f"模型认为是「{name}」，但地图上查不到这个地点，"
                      f"不作为结果使用。请手动确认名称。",
        }

    # ---- 候选列表：name 排首位，其余是模型给的其他可能 ----
    # 前端必须展示成"请确认"，而不是"识别结果：X"。
    # 依据：实测留园被认成拙政园（confidence=high），
    # 但 alternatives 里正好有"留园"——列出来就能被用户纠正。
    candidates = [name] + [a for a in alternatives if a and a != name]

    reason = "已在候选里找到该地点，请确认名称是否正确"
    if confidence == "low":
        reason = "识别把握不大，请从候选中确认"
    elif len(candidates) > 1:
        reason = "外观相似的景点容易混淆，请从候选中确认"
    return {
        "recognized": True,
        "poi": verified,
        "candidates": candidates[:4],
        "model_guess": {
            "name": name,
            "confidence": confidence,
            "alternatives": alternatives,
            "reason": guess.get("reason") or "",
        },
        "degraded": False,
        "reason": reason,
    }
