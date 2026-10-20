"""图片地标识别的离线测试。

**不调真实模型**（那要花额度且不稳定）。这里测的是**我们自己的逻辑**：
降级、格式校验、JSON 容错、候选构造、以及"模型说的话不算数"这条铁律。

真实模型的准确率已记录在 ``app/services/vision.py`` 的文档里（3 张真实照片 1 对 2 错），
那属于模型能力评估，不适合做成会被 CI 反复触碰的断言。

跑法：``cd backed && .venv\\Scripts\\python.exe tests/test_vision.py``
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import vision  # noqa: E402

PASSED = 0
FAILED = 0


def check(name, cond, detail=""):
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  [PASS] {name}")
    else:
        FAILED += 1
        print(f"  [FAIL] {name}  {detail}")


print("=" * 62)
print("图片地标识别")
print("=" * 62)

# ==================================================================
print("\n1. MIME 白名单")
# ==================================================================
check("支持 jpeg", vision.normalize_mime("image/jpeg") == "image/jpeg")
check("支持 png", vision.normalize_mime("image/png") == "image/png")
check("支持 webp", vision.normalize_mime("image/webp") == "image/webp")
check("兼容 image/jpg 这一常见误写", vision.normalize_mime("image/jpg") == "image/jpg")
check("带 charset 参数能剥掉",
      vision.normalize_mime("image/png; charset=binary") == "image/png")
check("大小写不敏感", vision.normalize_mime("IMAGE/JPEG") == "image/jpeg")
check("PDF 不在白名单", vision.normalize_mime("application/pdf") is None)
check("空类型返回 None", vision.normalize_mime(None) is None)
check("文本类型返回 None", vision.normalize_mime("text/plain") is None)

# ==================================================================
print("\n2. data URL（图片不落盘，只在内存里）")
# ==================================================================
u = vision.data_url(b"abc", "image/png")
check("格式正确", u == "data:image/png;base64,YWJj", u)
check("空字节也能编码", vision.data_url(b"", "image/jpeg") == "data:image/jpeg;base64,")
check("二进制安全（含非 UTF-8 字节）",
      vision.data_url(b"\xff\xd8\xff\x00", "image/jpeg").startswith("data:image/jpeg;base64,/9j/"))

# ==================================================================
print("\n3. JSON 容错（模型回复经常不规矩）")
# ==================================================================
check("纯 JSON", vision._extract_json('{"name":"西湖"}') == {"name": "西湖"})
check("```json 围栏", vision._extract_json(
    '好的：\n```json\n{"name":"雷峰塔"}\n```') == {"name": "雷峰塔"})
check("无语言标记的围栏", vision._extract_json(
    '```\n{"name":"拙政园"}\n```') == {"name": "拙政园"})
check("前后有废话", vision._extract_json(
    '判断如下 {"name":"留园","confidence":"high"} 完毕')
    == {"name": "留园", "confidence": "high"})
check("带 null 值", vision._extract_json(
    '{"name":null,"confidence":"low"}') == {"name": None, "confidence": "low"})
check("非 JSON 返回 None", vision._extract_json("我看不出来这是什么") is None)
check("空串返回 None", vision._extract_json("") is None)
check("None 输入返回 None", vision._extract_json(None) is None)
check("单元素数组也能取出对象（宽容处理）",
      vision._extract_json('[{"name":"x"}]') == {"name": "x"})
check("多对象数组不硬拼（会拼成非法 JSON → None）",
      vision._extract_json('[{"a":1},{"b":2}]') is None)

# ==================================================================
print("\n4. 降级：没配 key 时**不抛错**、明确告知")
# ==================================================================
_orig_enabled = vision.vision_enabled


def _disabled():
    return False


vision.vision_enabled = _disabled
try:
    r = asyncio.run(vision.recognize(b"\xff\xd8\xff" + b"0" * 50, "image/jpeg"))
    check("recognized=False", r["recognized"] is False)
    check("degraded=True", r["degraded"] is True)
    check("poi 为 None（不伪造）", r["poi"] is None)
    check("candidates 是空数组（前端不用判空）", r["candidates"] == [])
    check("reason 明确提到配置缺失", "VISION_API_KEY" in r["reason"], r["reason"])
finally:
    vision.vision_enabled = _orig_enabled

# ==================================================================
print("\n5. 输入校验（超大 / 空 / 非法类型）")
# ==================================================================
_orig_max = vision.VISION_MAX_BYTES
vision.VISION_MAX_BYTES = 100
try:
    big = asyncio.run(vision.recognize(b"x" * 200, "image/jpeg"))
    check("超大图片被拒", big["recognized"] is False)
    check("超大提示里带 MB 上限", "MB" in big["reason"], big["reason"])
finally:
    vision.VISION_MAX_BYTES = _orig_max

empty = asyncio.run(vision.recognize(b"", "image/png"))
check("空图被拒", empty["recognized"] is False)
check("空图不返回 poi", empty["poi"] is None)

bad = asyncio.run(vision.recognize(b"x" * 20, "application/pdf"))
check("非法类型被拒", bad["recognized"] is False)

# ==================================================================
print("\n6. 关键铁律：模型的话不算数，高德查不到就不认")
# ==================================================================


async def _fake_vision_returns_fabrication(img, mime):
    """模拟模型编造一个不存在的地点（视觉模型的典型失败模式）。"""
    return {"name": "不存在的虚构景点", "city": "虚构市",
            "confidence": "high", "alternatives": [], "reason": "看起来像"}


async def _fake_amap_none(name, city_hint=''):
    return None


_orig_call = vision._call_vision
_orig_verify = vision._verify_with_amap
vision._call_vision = _fake_vision_returns_fabrication
vision._verify_with_amap = _fake_amap_none
vision.vision_enabled = lambda: True
try:
    r = asyncio.run(vision.recognize(b"\xff\xd8\xff" + b"0" * 50, "image/jpeg"))
    check("高德查不到 → recognized=False", r["recognized"] is False)
    check("高德查不到 → poi 为 None（不把猜测当事实）", r["poi"] is None)
    check("模型原话仍保留在 model_guess 里（可追溯）",
          r["model_guess"]["name"] == "不存在的虚构景点")
    check("reason 说明是地图查不到", "查不到" in r["reason"], r["reason"])
finally:
    vision._call_vision = _orig_call
    vision._verify_with_amap = _orig_verify
    vision.vision_enabled = _orig_enabled

# ==================================================================
print("\n7. 候选构造：name 排首位、去重、截断 4 个")
# ==================================================================


async def _fake_vision_garden(img, mime):
    # 复刻实测：留园被认成拙政园，但候选里含留园（靠展示候选才能救回来）
    return {"name": "拙政园", "city": "苏州", "confidence": "high",
            "alternatives": ["留园", "网师园", "拙政园"], "reason": "江南园林"}


async def _fake_amap_ok(name, city_hint=''):
    return {"name": "拙政园", "city": "苏州", "lat": 31.32, "lng": 120.63}


vision._call_vision = _fake_vision_garden
vision._verify_with_amap = _fake_amap_ok
vision.vision_enabled = lambda: True
try:
    r = asyncio.run(vision.recognize(b"\xff\xd8\xff" + b"0" * 50, "image/jpeg"))
    check("recognized=True", r["recognized"] is True)
    check("poi 来自高德（不是模型原话）", r["poi"]["city"] == "苏州")
    check("候选首位是模型的首选", r["candidates"][0] == "拙政园", str(r["candidates"]))
    check("候选里含留园（用户能纠正）", "留园" in r["candidates"], str(r["candidates"]))
    check("候选去重（拙政园只出现一次）",
          r["candidates"].count("拙政园") == 1, str(r["candidates"]))
    check("候选上限 4 个", len(r["candidates"]) <= 4, str(r["candidates"]))
    check("多候选时提示要用户确认", "确认" in r["reason"], r["reason"])
finally:
    vision._call_vision = _orig_call
    vision._verify_with_amap = _orig_verify
    vision.vision_enabled = _orig_enabled

# ==================================================================
print("\n8. 配置一致性：视觉模型必须是有视觉能力的那个")
# ==================================================================
from app.config import VISION_MAX_BYTES, VISION_MODEL  # noqa: E402

check("单图上限 ≥ 5MB（手机直出够用）", VISION_MAX_BYTES >= 5 * 1024 * 1024,
      str(VISION_MAX_BYTES))
# 实测 deepseek-v4-pro 会回答"我无法查看这张照片"；只有 deepseek-flash 支持
check("默认视觉模型是 deepseek-flash", VISION_MODEL == "deepseek-flash", VISION_MODEL)

print()
print("=" * 62)
print(f"通过 {PASSED} 项，失败 {FAILED} 项")
print("=" * 62)
sys.exit(1 if FAILED else 0)
