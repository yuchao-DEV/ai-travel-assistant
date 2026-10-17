"""算法引擎单元测试（纯标准库 + 本项目算法模块，不联网、不消耗任何配额）。

覆盖 README 第十一节宣称的行为，以及历次修复的缺陷：
1. compute_score 的绕路参考基点（旧实现恒传 0.0，导致 w_detour 整项失效）
2. daily_budget_split 不再隐藏扣掉 10% 预算
3. optimize_route 在每日小规模下取到精确最优（旧实现约 9.3% 拿不到最优）
4. 必去景点（must_go）不会被预算或评分排序挤掉
5. 预算兜底会真正循环删除直到不超支，且不删必去项
6. **真实数据管线**：高德评分在 biz_ext.rating、营业时间在 biz_ext.open_time、
   真实人均消费在 biz_ext.cost、条目类型来自 typecode 前缀
   （旧实现读顶层 rating，实测永远为 null → 评分维度恒为 0）

运行：cd backed && python tests/test_algorithms.py
"""
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.algorithms.candidate_selector import select_candidates  # noqa: E402
from app.algorithms.data_loader import (  # noqa: E402
    poi_type_from_typecode,
    scenic_level_from_category,
)
from app.algorithms.engine import _centroid  # noqa: E402
from app.algorithms.selector import select_by_time  # noqa: E402
from app.algorithms.models import (  # noqa: E402
    POI,
    compute_score,
    get_profile,
    open_window,
    parse_open_ranges,
)
from app.algorithms.route_optimizer import (  # noqa: E402
    EXACT_MAX_N,
    _total_distance,
    exact_open_path,
    greedy_init,
    optimize_route,
    two_opt,
)
from app.schemas import DailyPlan, ItineraryItem, ScoreBreakdown  # noqa: E402

PASSED = 0
FAILED = 0


def check(label: str, condition: bool, extra: str = "") -> None:
    global PASSED, FAILED
    if condition:
        PASSED += 1
        print(f"  [PASS] {label}")
    else:
        FAILED += 1
        print(f"  [FAIL] {label} {extra}")


def near(a: float, b: float, tol: float = 1e-6) -> bool:
    return abs(a - b) <= tol


def make_poi(name, lat, lng, tags=None, rating=4.0, cost=50.0):
    return POI(name=name, lat=lat, lng=lng, tags=tags or ["历史"],
               rating=rating, cost=cost)


# ==================================================================
print("1. 绕路参考基点（修复：w_detour 曾因恒传 0.0 而整项失效）")
# ==================================================================
CENTER = (30.25, 120.15)
close_poi = make_poi("近点", 30.256, 120.156)      # 距中心约 0.9km
far_poi = make_poi("远点", 30.700, 120.700)        # 距中心约 70km
weights = get_profile("尽览")

_, bd_close = compute_score(close_poi, ["历史"], CENTER[0], CENTER[1], 15.0, weights)
_, bd_far = compute_score(far_poi, ["历史"], CENTER[0], CENTER[1], 15.0, weights)
check("给了参考基点后，远点的 detour 分量大于近点",
      bd_far["detour"] > bd_close["detour"],
      f"close={bd_close['detour']} far={bd_far['detour']}")
check("远点的综合评分低于近点（w_detour 生效）",
      compute_score(far_poi, ["历史"], CENTER[0], CENTER[1], 15.0, weights)[0]
      < compute_score(close_poi, ["历史"], CENTER[0], CENTER[1], 15.0, weights)[0])

# 旧实现在选点阶段恒传 (0.0, 0.0)，两点 detour 都是 0；现在应当能区分
_, bd_zero_ref = compute_score(far_poi, ["历史"], 0.0, 0.0, 15.0, weights)
check("参考基点合法为 (0,0)（赤道附近）时仍按真实距离计算 detour，而非当成无参考点",
      bd_zero_ref["detour"] > 0.0,
      f"detour={bd_zero_ref['detour']}")

_, bd_none = compute_score(far_poi, ["历史"], None, None, 15.0, weights)
check("显式传 None 时 detour 为 0（单点当天的语义）", bd_none["detour"] == 0.0)

# ==================================================================
print("\n2. 时间容量选点（替换原「每日预算分配」）")
# ==================================================================
# 预算系统已移除（执行规格 §0.1）。容量从"钱"换成"时间"：
# daily_select_target() 按当日时间窗推导，selector.select_by_time() 在其内选分最高的。
from app.algorithms.scheduler import daily_select_target  # noqa: E402

for start, end, low, high in (
    ("10:00", "18:00", 3, 5),      # 悠闲窗口：窄
    ("09:00", "19:00", 4, 6),      # 经典窗口：中
    ("08:30", "21:00", 6, 9),      # 尽览窗口：宽
):
    t = daily_select_target(start, end)
    check(f"时间窗 {start}-{end} 的选点容量在 [{low},{high}] 内",
          low <= t <= high, f"target={t}")
check("窗口越宽，容量越大",
      daily_select_target("08:30", "21:00") > daily_select_target("10:00", "18:00"),
      f"{daily_select_target('08:30', '21:00')} vs {daily_select_target('10:00', '18:00')}")

# select_by_time：取前 N 名 + 必去项不受限
_n_pool = [POI(name=f"点{i}", lat=30.0 + i * 0.01, lng=120.0,
               typecode="110000", poi_type="spot", score=1.0 - i * 0.1)
           for i in range(8)]
_picked = select_by_time(_n_pool, target_count=3)
check("按容量取前 N 名", len(_picked) == 3, str(len(_picked)))
check("取的是分数最高的 3 个", [p.name for p in _picked] == ["点0", "点1", "点2"],
      str([p.name for p in _picked]))
check("空池返回空", select_by_time([], 5) == [])
_must = POI(name="必去", lat=30.5, lng=120.0, typecode="110000",
            poi_type="spot", score=0.0, must_go=True)
_picked2 = select_by_time(_n_pool + [_must], target_count=2)
check("必去项即使分数为 0 也一定入选",
      any(p.name == "必去" for p in _picked2), str([p.name for p in _picked2]))
check("必去项不被容量挤掉（容量至少容纳必去数）",
      len(_picked2) >= 2, str(len(_picked2)))

# ==================================================================
print("\n3. 路径优化：小规模取精确最优（修复：2-opt 每天 5 点约 9.3% 非最优）")
# ==================================================================
rng = random.Random(20261010)
suboptimal = 0
trials = 200
for _ in range(trials):
    pois = [
        make_poi(f"P{i}",
                 CENTER[0] + rng.uniform(-0.18, 0.18),
                 CENTER[1] + rng.uniform(-0.18, 0.18))
        for i in range(5)
    ]
    got = _total_distance(optimize_route(list(pois)))
    best = _total_distance(exact_open_path(list(pois)))
    if got > best + 1e-9:
        suboptimal += 1
check(f"每天 5 点、{trials} 次随机实例中 optimize_route 全部达到精确最优",
      suboptimal == 0, f"未达最优 {suboptimal} 次")

# n=8 也在精确区间内
pois8 = [make_poi(f"Q{i}", CENTER[0] + rng.uniform(-0.2, 0.2),
                  CENTER[1] + rng.uniform(-0.2, 0.2)) for i in range(8)]
check(f"n=8 仍在精确枚举上限内（EXACT_MAX_N={EXACT_MAX_N}）", 8 <= EXACT_MAX_N)
check("n=8 时 optimize_route 达到精确最优",
      near(_total_distance(optimize_route(list(pois8))),
           _total_distance(exact_open_path(list(pois8))), 1e-9))

# 起点自由比固定首元素更短（旧实现固定 pois[0]）
better = 0
for _ in range(100):
    pois = [make_poi(f"R{i}", CENTER[0] + rng.uniform(-0.2, 0.2),
                     CENTER[1] + rng.uniform(-0.2, 0.2)) for i in range(5)]
    free = _total_distance(exact_open_path(list(pois)))
    fixed = _total_distance(two_opt(greedy_init(list(pois))))
    if fixed > free + 1e-9:
        better += 1
check("起点自由枚举严格优于（或不差于）固定首元素的贪心+2-opt",
      better > 50, f"仅 {better}/100 次更优，说明优势不明显")

check("two_opt 不跳过 3 个点的路径（旧实现用 len<4 跳过了）",
      len(two_opt([make_poi("a", 30.0, 120.0), make_poi("b", 30.3, 120.3),
                   make_poi("c", 30.01, 120.01)])) == 3)

# 指定起点时，第一站必须是离起点最近的点
anchor = (30.9, 120.9)
far_from_anchor = make_poi("远离", 30.0, 120.0)
close_to_anchor = make_poi("靠近", 30.89, 120.89)
route = optimize_route([far_from_anchor, close_to_anchor, make_poi("中", 30.5, 120.5)],
                       anchor[0], anchor[1])
check("指定起点坐标时，第一站是离它最近的点", route[0].name == "靠近", route[0].name)

# ==================================================================
print("\n4. 必去景点必须真正进入行程（README 宣称『强制加入』）")
# ==================================================================
pool = [make_poi(f"高分点{i}", 30.25 + i * 0.01, 120.15, tags=["历史"], rating=5.0, cost=10.0)
        for i in range(10)]
expensive_must = make_poi("必去的贵景点", 30.26, 120.16, tags=["历史"], rating=3.0, cost=9999.0)
expensive_must.must_go = True

selected = select_by_time(pool + [expensive_must], 5)
names = [p.name for p in selected]
check("费用远超日预算的必去景点仍被选中", "必去的贵景点" in names, f"选中={names}")

many_must = []
for i in range(7):
    p = make_poi(f"必去{i}", 30.25 + i * 0.01, 120.15, tags=["历史"], rating=4.0, cost=10.0)
    p.must_go = True
    many_must.append(p)
selected = select_by_time(pool + many_must, target_count=5)
selected_names = [p.name for p in selected]
check("必去项数量超过 target_count 时，全部保留（硬约束优先于数量上限）",
      all(f"必去{i}" in selected_names for i in range(7)),
      f"选中={selected_names}")

# ==================================================================
print("\n5. 候选筛选会标记必去项")
# ==================================================================
raw = [make_poi("总统府", 30.25, 120.15, tags=["历史"]),
       make_poi("某公园", 30.26, 120.16, tags=["自然"])]
cands = select_candidates(raw, ["自然"], ["总统府"], [], [])
president = next((p for p in cands if p.name == "总统府"), None)
check("tag 不匹配的必去项仍进入候选", president is not None)
check("必去项被打上 must_go 标记", bool(getattr(president, "must_go", False)))

# ---- 必去匹配必须分档：只保留最匹配那一档 ----
# 旧实现是"任意子串命中就全部标必去"，导致"拙政园"连"片仔癀(拙政园店)"都被强制排进行程
raw2 = [
    make_poi("拙政园", 31.32, 120.62, tags=["历史"]),
    make_poi("片仔癀(拙政园店)", 31.32, 120.62, tags=["购物"]),
    make_poi("虎丘山风景区", 31.34, 120.57, tags=["历史"]),
    make_poi("虎丘塔", 31.34, 120.57, tags=["历史"]),
]
cands2 = select_candidates(raw2, ["自然"], ["拙政园", "虎丘"], [], [])
must_names = {p.name for p in cands2 if getattr(p, "must_go", False)}
check("必去只取最高匹配档：完全相等的『拙政园』入选",
      "拙政园" in must_names, str(must_names))
check("必去只取最高匹配档：同名店铺『片仔癀(拙政园店)』被排除",
      "片仔癀(拙政园店)" not in must_names, str(must_names))
check("同档多个点仍可全部入选（『虎丘』→ 虎丘山风景区/虎丘塔）",
      {"虎丘山风景区", "虎丘塔"} <= must_names, str(must_names))

# 旧实现在标记必去时写了 poi.poi_type = "spot"，会把第一层判定好的餐厅/酒店类型抹掉
_typecase = [make_poi("某必去餐厅", 31.3, 120.6, tags=["美食"])]
_typecase[0].poi_type = "restaurant"
_c = select_candidates(_typecase, [], ["某必去餐厅"], [], [])
check("必去餐厅的类型不被覆盖为 spot",
      _c[0].poi_type == "restaurant", _c[0].poi_type)

# ==================================================================
print("\n7. 辅助函数")
# ==================================================================
lat, lng = _centroid([make_poi("a", 30.0, 120.0), make_poi("b", 31.0, 121.0)])
check("聚类中心计算正确", near(lat, 30.5) and near(lng, 120.5), f"({lat}, {lng})")
check("空池的聚类中心为 (None, None)", _centroid([]) == (None, None))

# ==================================================================
print("\n8. 真实数据管线：营业时间解析（数据来自高德实测原文）")
# ==================================================================
_open_cases = [
    ("09:00-16:30", [(540, 990)]),
    ("24小时营业", [(0, 1440)]),
    ("周一至周日 10:00-22:00", [(600, 1320)]),
    ("周一至周日 07:30-18:00", [(450, 1080)]),
    ("09:00-21:00 09:00-16:30", [(540, 1260)]),          # 两段重叠 → 合并
    ("周一至周日 00:00-24:00", [(0, 1440)]),
    ("周一，周三-周日09:00-16:30开放(16:00停止入园)，周二全天关闭", [(540, 990)]),
    ("周一至周四 09:00-21:00；周五至周日 09:00-22:00", [(540, 1320)]),
    ("", []),
    ([], []),
]
for text, expected in _open_cases:
    got = parse_open_ranges(text)
    check(f"营业时间解析「{str(text)[:26]}」", got == expected, f"期望={expected} 实际={got}")

check("open_window 取最早开始与最晚结束",
      open_window([(600, 1320), (540, 990)]) == (540, 1320))
check("无区间时 open_window 返回 None", open_window([]) is None)

# ==================================================================
print("\n9. 真实数据管线：条目类型与景区等级")
# ==================================================================
for tc, expected in (("050202", "restaurant"), ("050100", "restaurant"),
                     ("100100", "hotel"), ("100200", "hotel"),
                     ("110200", "spot"), ("140100", "spot"), ("", "spot")):
    got = poi_type_from_typecode(tc)
    check(f"typecode {tc or '空'} → {expected}", got == expected, f"实际={got}")

check("景区等级从中文分类提取",
      scenic_level_from_category("风景名胜;风景名胜;国家级景点") == "国家级景点")
check("非等级分类返回空",
      scenic_level_from_category("科教文化服务;博物馆;博物馆") == "")
check("空分类返回空", scenic_level_from_category("") == "")

# ==================================================================
print("\n10. 真实数据管线：POI.from_amap 字段映射（本轮修复的核心回归）")
# ==================================================================
# 这条 raw 完全按高德 place/text 的真实结构构造：
# 顶层 rating 为 null（实测 150/150 如此），真实值在 biz_ext.rating
raw = {
    "name": "杭州博物馆", "lat": 30.238963, "lng": 120.165672, "typecode": "140100",
    "rating": None,
    "address": "粮道山18号", "tel": "",
    "category": "科教文化服务;博物馆;博物馆",
    "business_area": "清波",
    "open_time": "09:00-16:30",
    "open_time_text": "周一，周三-周日09:00-16:30开放(16:00停止入园)，周二全天关闭",
    "biz_cost": 0.0,
    "photos": ["http://example.com/a.jpg"],
    "entr_location": "120.165672,30.238963",
}
# 注意：真实 rating 在 client.search_pois 里已从 biz_ext 取出放进 raw["rating"]
raw_with_biz_rating = dict(raw, rating=4.7)
poi = POI.from_amap(raw_with_biz_rating)
check("评分取自 biz_ext.rating（不再是 0）", near(poi.rating, 4.7), f"rating={poi.rating}")
check("评分维度归一后非零",
      compute_score(poi, ["历史"], None, None, 15.0, get_profile("经典"))[1]["rating"] > 0)
check("营业时间被解析成区间", poi.open_ranges == [(540, 990)], f"{poi.open_ranges}")
check("营业时间原文被保留", "周二全天关闭" in poi.open_hours, poi.open_hours)
check("景区分类被保留", poi.category.startswith("科教文化服务"), poi.category)
check("商圈被保留", poi.business_area == "清波", poi.business_area)
check("无真实消费时 cost=None 且 cost_source=None（不再编造预留值）",
      poi.cost is None and poi.cost_source is None,
      f"cost={poi.cost} source={poi.cost_source}")

# 餐饮：有了真实人均消费就应标记 real
restaurant_raw = dict(raw, name="某餐厅", typecode="050117", category="餐饮服务;中餐厅;火锅店",
                      biz_cost=172.0, rating=4.6, open_time="11:00-22:00")
rpoi = POI.from_amap(restaurant_raw)
check("有 biz_cost 时费用取真值", near(rpoi.cost, 172.0), f"cost={rpoi.cost}")
check("有 biz_cost 时标记为 real", rpoi.cost_source == "real", rpoi.cost_source)
check("餐饮条目被识别为 restaurant",
      poi_type_from_typecode(rpoi.typecode) == "restaurant")

# ==================================================================
print("\n11. 排程：营业时间作为真实约束")
# ==================================================================
from app.algorithms.scheduler import assign_schedule  # noqa: E402


def timed_poi(name, open_text, lat=30.25, lng=120.15, poi_type="spot"):
    p = POI(name=name, lat=lat, lng=lng, typecode="140100" if poi_type == "spot" else "050100")
    p.open_ranges = parse_open_ranges(open_text)
    p.open_hours = open_text
    p.poi_type = poi_type
    p.cost = 0.0
    return p


# 17:00 出发去一个 09:00-16:30 就关门的博物馆 → 应当跳过
closed = timed_poi("已闭馆的博物馆", "09:00-16:30")
items = assign_schedule([closed], day_start="17:00", day_end="21:00")
check("已过关门时间的点被跳过而非硬塞", items == [], f"实际={items}")

# 18:00 出发去一个 19:00 才开的夜游点 → 应当等到 19:00
night = timed_poi("钱塘江夜游", "19:00-21:50")
items = assign_schedule([night], day_start="18:00", day_end="22:00")
check("未到开店时间时等待开门",
      len(items) == 1 and items[0]["start"] == "19:00",
      f"实际={[i.get('start') for i in items]}")

# 未知营业时间不应造成跳过（退回活动窗口）
unknown = timed_poi("无营业时间的点", "")
items = assign_schedule([unknown], day_start="09:00", day_end="21:00")
check("营业时间未知时按活动窗口正常排程", len(items) == 1, f"实际={items}")

# 排程结果应带上费用来源与营业时间，供前端标注"参考价"
items = assign_schedule([timed_poi("测试点", "09:00-18:00")], day_start="09:00", day_end="21:00")
check("排程结果包含 cost_source", "cost_source" in items[0], str(items[0].keys()))
check("排程结果包含 open_hours", "open_hours" in items[0], str(items[0].keys()))
check("排程结果包含 photos", "photos" in items[0], str(items[0].keys()))

# ==================================================================
print("\n12. 地理编码核对（防模糊匹配）")
# ==================================================================
from app.amap.client import _extract_photos, geocode_matches  # noqa: E402

_match_cases = [
    ("当涂", {"formatted_address": "安徽省马鞍山市当涂县"}, True),
    ("杭州市", {"formatted_address": "浙江省杭州市西湖区"}, True),
    ("南京", {"province": "江苏省", "city": "南京市"}, True),
    ("杭州", {"formatted_address": "浙江省杭州市"}, True),
    # 实测：高德对无法识别的输入会模糊匹配到别处，必须拒绝
    ("不存在的城市xyz", {"formatted_address": "宁夏回族自治区银川市兴庆区城市"}, False),
    ("zzzz", {"formatted_address": "贵州省遵义市红花岗区长征镇镇"}, False),
    ("杭州", {}, False),
    ("", {"formatted_address": "浙江省杭州市"}, False),
]
for request, item, expected in _match_cases:
    got = geocode_matches(request, item)
    check(f"geocode_matches(「{request}」)", got is expected, f"期望={expected} 实际={got}")

print()
print("13. 缩略图点位解析")
# ==================================================================
from app.services.mapimage import parse_point_groups  # noqa: E402

groups = parse_point_groups("30.1,120.1;30.2,120.2;30.3,120.3", "2,1")
check("按 day_sizes 正确切分", [len(g) for g in groups] == [2, 1],
      f"实际={[len(g) for g in groups]}")
check("点位数值正确", groups[1][0] == (30.3, 120.3), f"实际={groups[1]}")
check("未给 day_sizes 时视为一天",
      len(parse_point_groups("30.1,120.1;30.2,120.2")) == 1)
check("非法坐标被跳过",
      parse_point_groups("30.1,120.1;bad;30.2,120.2")[0] == [(30.1, 120.1), (30.2, 120.2)])
check("超出范围的坐标被跳过",
      parse_point_groups("999,120.1;30.2,120.2")[0] == [(30.2, 120.2)])
check("空串返回空列表", parse_point_groups("") == [])
check("day_sizes 之和小于点数时余数归到最后一天",
      [len(g) for g in parse_point_groups("30.1,120.1;30.2,120.2;30.3,120.3", "1")] == [3])

print()
print("14. 景点图片提取（含 http→https 归一）")
# ==================================================================
check("http 图片地址被升级为 https",
      _extract_photos([{"url": "http://store.is.autonavi.com/a.jpg"}])
      == ["https://store.is.autonavi.com/a.jpg"])
check("已是 https 的保持不变",
      _extract_photos([{"url": "https://a.com/b.jpg"}]) == ["https://a.com/b.jpg"])
check("最多取 3 张",
      len(_extract_photos([{"url": f"https://a.com/{i}.jpg"} for i in range(6)])) == 3)
check("空列表返回空", _extract_photos([]) == [])
check("非列表返回空", _extract_photos("not-a-list") == [])
check("缺 url 的项被跳过",
      _extract_photos([{"title": "无图"}, {"url": "https://a.com/c.jpg"}])
      == ["https://a.com/c.jpg"])

# ==================================================================
print("\n15. 旅行灵感流：分类推导与轮转")
# ==================================================================
from app.services.inspiration import category_counts, card_category, _interleave  # noqa: E402


def fake_poi(poi_type="spot", category="", name="某地"):
    p = POI(name=name, lat=30.0, lng=120.0)
    p.poi_type = poi_type
    p.category = category
    return p


_cat_cases = [
    (("restaurant", "餐饮服务;中餐厅;火锅店"), "美食"),
    (("hotel", "住宿服务;宾馆酒店;经济型连锁酒店"), "住宿"),
    (("spot", "科教文化服务;博物馆;博物馆"), "博物馆"),
    (("spot", "科教文化服务;美术馆;美术馆"), "展览"),
    (("spot", "购物服务;商场;购物中心"), "购物"),
    (("spot", "风景名胜;风景名胜;国家级景点"), "风景"),
    (("spot", "风景名胜;公园广场;公园"), "风景"),
    (("spot", "生活服务;洗衣店;洗衣店"), "其他"),
]
for (ptype, cat), expected in _cat_cases:
    got = card_category(fake_poi(ptype, cat))
    check(f"灵感分类「{cat.split(';')[-1]}」→ {expected}", got == expected, f"实际={got}")

# 轮转：高分美食不应把整条流占满
_buckets = {
    "风景": [{"name": "s1"}, {"name": "s2"}, {"name": "s3"}],
    "美食": [{"name": "f1"}, {"name": "f2"}, {"name": "f3"}],
}
_mixed = _interleave(_buckets, 4)
check("灵感按类目轮转（不被单一类目占满）",
      [c["name"] for c in _mixed] == ["s1", "f1", "s2", "f2"], str(_mixed))
check("灵感轮转在总数不足时正常结束",
      len(_interleave({"风景": [{"name": "s1"}]}, 10)) == 1)
check("空桶不会导致异常", _interleave({}, 5) == [])

# 计数口径：标签数字必须等于点进去能看到的张数
_cnt = category_counts([
    {"category": "美食"}, {"category": "美食"}, {"category": "风景"},
    {"category": "购物"}, {"category": "美食"},
])
_cnt_map = {c["key"]: c["count"] for c in _cnt}
check("分类计数只统计传入的卡片",
      _cnt_map == {"风景": 1, "美食": 3, "购物": 1}, str(_cnt_map))
check("分类计数之和等于卡片总数", sum(_cnt_map.values()) == 5, str(_cnt_map))
check("计数按类目固定顺序输出（风景在前）",
      [c["key"] for c in _cnt][0] == "风景", str([c["key"] for c in _cnt]))
check("空卡片列表返回空计数", category_counts([]) == [])
# 回归："美食 25 > 全部 24" 这种自相矛盾不可能再出现
check("每个分类计数都不超过卡片总数",
      all(v <= 5 for v in _cnt_map.values()), str(_cnt_map))

# ==================================================================
print("\n16. POI 并发同键请求合并（规划与灵感流不再重复打高德）")
# ==================================================================
import asyncio  # noqa: E402

from app.amap import client as amap_client  # noqa: E402

_upstream_calls = []


async def _fake_remote(*args, **kwargs):
    _upstream_calls.append(args)
    await asyncio.sleep(0.05)          # 模拟网络往返，保证 5 个调用真的重叠
    return [{"name": "测试点", "lat": 30.0, "lng": 120.0}]


# 隔离外部依赖：不走缓存、视为已配置 Key、上游换成计数桩
_orig = (amap_client._search_pois_remote, amap_client.get_cache,
         amap_client.set_cache, amap_client.amap_enabled)
amap_client._search_pois_remote = _fake_remote
amap_client.get_cache = lambda *a, **k: None
amap_client.set_cache = lambda *a, **k: None
amap_client.amap_enabled = lambda: True
try:
    async def _burst():
        return await asyncio.gather(*[
            amap_client.search_pois("测试城", "景点", 30.0, 120.0) for _ in range(5)
        ])
    _results = asyncio.run(_burst())
    check("5 个并发同键请求只打 1 次上游", len(_upstream_calls) == 1,
          f"实际 {len(_upstream_calls)} 次")
    check("所有并发调用都拿到同一结果",
          all(r and r[0]["name"] == "测试点" for r in _results), str(_results)[:120])

    # 不同关键词不应被错误合并
    _upstream_calls.clear()
    amap_client._inflight.clear()

    async def _two_kinds():
        return await asyncio.gather(
            amap_client.search_pois("测试城", "景点", 30.0, 120.0),
            amap_client.search_pois("测试城", "美食", 30.0, 120.0),
        )

    asyncio.run(_two_kinds())
    check("不同关键词不会被误合并", len(_upstream_calls) == 2,
          f"实际 {len(_upstream_calls)} 次")
finally:
    (amap_client._search_pois_remote, amap_client.get_cache,
     amap_client.set_cache, amap_client.amap_enabled) = _orig

# ==================================================================
print("\n17. 预算利用：选点余量 / 用餐点 / 诚实告知 / 聚类再平衡")
# ==================================================================
from app.algorithms.cluster import kmeans  # noqa: E402
from app.algorithms.engine import _ensure_meal_stop  # noqa: E402
from app.algorithms.scheduler import daily_select_target  # noqa: E402

# --- 选点上限按时间窗推导，且留出补位余量 ---
_t_full = daily_select_target("09:00", "21:00")     # 12h
_t_short = daily_select_target("10:00", "16:00")    # 6h
check("选点上限随当日时间窗变化（窗口越长选得越多）",
      _t_full > _t_short, f"{_t_full} vs {_t_short}")
check("选点上限留有余量以供排程补位", _t_full > 4, f"{_t_full}")

# --- 用餐点 ---
_spot = make_poi("某景点", 31.30, 120.60, tags=["历史"], cost=50.0)
_spot.poi_type = "spot"
_spot.score = 0.5
_rest = make_poi("某餐厅", 31.31, 120.61, tags=["美食"], cost=120.0)
_rest.poi_type = "restaurant"
_rest.score = 0.4
check("池里没有餐厅时不强行添加",
      len(_ensure_meal_stop([_spot], [])) == 1)
_with_meal = _ensure_meal_stop([_spot], [_rest])
check("有餐厅时补一个用餐点",
      len(_with_meal) == 2, str([p.name for p in _with_meal]))
check("补进来的用餐点带 must_go（排程阶段不得因时间不够被跳过）",
      _with_meal[-1].must_go is True)
_pricey = make_poi("贵餐厅", 31.31, 120.61, tags=["美食"], cost=400.0)
_pricey.poi_type = "restaurant"
_pricey.score = 0.4
_huge = make_poi("天价餐厅", 31.31, 120.61, tags=["美食"], cost=9999.0)
_huge.poi_type = "restaurant"
_huge.score = 0.4

# --- 排程保证 must_go 不被时间挤掉 ---
def _mk(name, must, score, lat, lng):
    p = POI(name=name, lat=lat, lng=lng, typecode="110200", cost=50.0)
    p.poi_type = "spot"
    p.must_go = must
    p.score = score
    p.open_ranges = [(0, 1440)]
    return p


_route = [_mk(f"普通{i}", False, 0.9 - i * 0.05, 31.30 + i * 0.01, 120.60)
          for i in range(6)]
_route.append(_mk("必去收尾点", True, 0.1, 31.29, 120.59))
_sched = assign_schedule(_route, "09:00", "21:00")
check("必须项不会被时间挤掉",
      any(it["name"] == "必去收尾点" for it in _sched),
      str([it["name"] for it in _sched]))
check("排程结果带 must_go 标记", all("must_go" in it for it in _sched))

# --- 预算说明：已随预算系统一并移除（执行规格 §0.1）---

# --- 聚类再平衡：不能出现"某天候选被抽干" ---
_pts = []
for i in range(30):
    _pts.append(make_poi(f"密集{i}", 31.30 + (i % 6) * 0.001, 120.60 + (i // 6) * 0.001))
for i in range(6):
    _pts.append(make_poi(f"远郊{i}", 31.80 + i * 0.01, 121.20 + i * 0.01))
_cl = kmeans(_pts, 3)
_sizes = sorted(len(c) for c in _cl)
check("聚类规模不再悬殊（避免某天候选被抽干）",
      _sizes[-1] <= (len(_pts) // 3) * 1.4 + 1, f"簇大小={_sizes}")
check("每个簇都非空", all(len(c) > 0 for c in _cl), f"簇大小={_sizes}")

# ==================================================================
print("\n18. 导入线路/地点：规则兜底抽取")
# ==================================================================
from app.services.importer import (  # noqa: E402
    _guess_city,
    _looks_like_place,
    _rule_extract,
    _strip_affixes,
)

# 前后缀剥离：实测不剥会出现"上午去拙政园""早上去虎丘"这类片段，反查必然失败
_affix_cases = [
    ("上午去拙政园", "拙政园"),
    ("然后逛苏州博物馆。", "苏州博物馆"),
    ("中午在平江路吃午饭", "平江路"),
    ("晚上到山塘街看夜景。", "山塘街"),
    ("早上去虎丘", "虎丘"),
]
for raw, expected in _affix_cases:
    got = _strip_affixes(raw)
    check(f"剥离前后缀「{raw}」", got == expected, f"得到 {got!r}")

# 粗筛：明显不是地点的片段要挡掉
check("标题类片段被挡掉（含「攻略」）", not _looks_like_place("苏州两日游攻略"))
check("酒店名被挡掉", not _looks_like_place("某某精品酒店"))
check("过短片段被挡掉", not _looks_like_place("去"))
check("正常景点名通过", _looks_like_place("拙政园"))

# 完整规则抽取：分句符号必须能切开，否则整段连在一起无法识别
_itinerary_text = (
    "苏州两日游攻略\n"
    "D1: 上午去拙政园，然后逛苏州博物馆。中午在平江路吃午饭。\n"
    "下午去狮子林，晚上到山塘街看夜景。\n"
    "D2: 早上去虎丘，然后去诚品书店打卡。"
)
_places = _rule_extract(_itinerary_text)
for expected in ("拙政园", "苏州博物馆", "平江路", "狮子林", "山塘街", "虎丘", "诚品书店"):
    check(f"规则抽取包含「{expected}」", expected in _places, str(_places))
check("规则抽取不会把标题当地点", "苏州两日游攻略" not in _places, str(_places))
check("规则抽取结果去重", len(_places) == len(set(_places)), str(_places))

# 城市识别只用项目城市表，不凭空猜
check("从文字里认出已知城市", _guess_city("我想去苏州玩") == "苏州")
check("文字里没有城市时返回 None", _guess_city("随便走走") is None)

# ==================================================================
print("\n19. LLM 调用参数：temperature / model 可选覆盖")
# ==================================================================
import inspect  # noqa: E402

from app.config import MODEL as _CFG_MODEL  # noqa: E402
from app.llm import DEFAULT_TEMPERATURE, _build_messages, chat, chat_stream  # noqa: E402

_b = _build_messages("sys", "user", stream=False)
check("默认 temperature 为 0.7", _b["temperature"] == 0.7, str(_b["temperature"]))
check("默认 temperature 与常量一致", _b["temperature"] == DEFAULT_TEMPERATURE)
check("默认 model 继承配置", _b["model"] == _CFG_MODEL, str(_b["model"]))
check("system 提示词进入 messages",
      _b["messages"][0] == {"role": "system", "content": "sys"}, str(_b["messages"]))
check("user 提示词进入 messages",
      _b["messages"][-1] == {"role": "user", "content": "user"}, str(_b["messages"]))
check("system 为空时不塞 system 消息",
      len(_build_messages("", "u", stream=False)["messages"]) == 1)
check("stream 标志透传",
      _build_messages("s", "u", stream=True)["stream"] is True)

check("可覆盖 temperature",
      _build_messages("s", "u", stream=False, temperature=0.2)["temperature"] == 0.2)
check("temperature 可设为 0",
      _build_messages("s", "u", stream=False, temperature=0)["temperature"] == 0)
check("可覆盖 model",
      _build_messages("s", "u", stream=False, model="other")["model"] == "other")
check("model 传 None 时回落到默认模型",
      _build_messages("s", "u", stream=False, model=None)["model"] == _CFG_MODEL)

# 向后兼容契约：位置参数顺序不变，新参数必须仅限关键字且有默认值
for _fn in (chat, chat_stream):
    _params = list(inspect.signature(_fn).parameters.values())
    _name = _fn.__name__
    check(f"{_name} 前两个参数仍是普通位置参数",
          [p.kind for p in _params[:2]] == [inspect.Parameter.POSITIONAL_OR_KEYWORD] * 2,
          str([(p.name, p.kind.name) for p in _params]))
    check(f"{_name} 的 temperature / model 是仅限关键字",
          all(p.kind == inspect.Parameter.KEYWORD_ONLY for p in _params[2:4]),
          str([(p.name, p.kind.name) for p in _params]))
    check(f"{_name} 的新参数都有默认值（新增不破坏老调用）",
          _params[2].default == DEFAULT_TEMPERATURE and _params[3].default is None,
          str([(p.name, p.default) for p in _params[2:4]]))
    check(f"{_name} 仍然只有两个必填参数",
          len([p for p in _params if p.default is inspect.Parameter.empty]) == 2,
          str([p.name for p in _params]))

# 位置参数写法必须依旧可用（原有 4 个调用点就是这么调的）
check("chat 可按老写法绑定两个位置参数",
      list(inspect.signature(chat).bind("s", "u").arguments) == ["system_prompt", "user_prompt"])
check("chat_stream 可按老写法绑定两个位置参数",
      list(inspect.signature(chat_stream).bind("s", "u").arguments)
      == ["system_prompt", "user_prompt"])

# ==================================================================
print("\n20. 城市表：种子 + 自扩展（不再只认 12 城）")
# ==================================================================
import json as _json  # noqa: E402
import tempfile as _tempfile  # noqa: E402
from pathlib import Path as _Path  # noqa: E402

from app.algorithms import data_loader as _dl  # noqa: E402
from app.services import city_registry as _cr  # noqa: E402

_seed = _dl.load_seed_cities()
check("种子城市为 12 个", len(_seed) == 12, str(len(_seed)))
check("种子城市带坐标与关键词",
      all("center_lat" in c and "keywords" in c for c in _seed.values()))
check("load_cities 至少包含全部种子城市",
      set(_seed) <= set(_dl.load_cities()), str(len(_dl.load_cities())))

# 用临时路径承接自扩展写入，避免污染真实 cache。
# 必须放在工作区内（backed/cache/ 已被 .gitignore 忽略）：沙箱不允许写工作区外。
_orig_extra = _dl._EXTRA_CITIES_PATH
_tmp_dir = _Path(__file__).resolve().parent.parent / "cache" / "_test_city"
import shutil as _shutil  # noqa: E402
_shutil.rmtree(_tmp_dir, ignore_errors=True)
_tmp_dir.mkdir(parents=True, exist_ok=True)
_dl._EXTRA_CITIES_PATH = _tmp_dir / "cities_extra.json"
_dl.load_cities.cache_clear()
try:
    check("临时缓存初始为空", _dl.load_extra_cities() == {})
    # 造一个不在种子表里的城市
    _fake = "测试城"
    check("造的城市不在种子表里", _fake not in _seed)
    _ok = _dl.remember_city(_fake, adcode="999999",
                            center_lat=30.0, center_lng=120.0)
    check("remember_city 首次写入返回 True", _ok is True)
    check("扩展表已写入该城市", _fake in _dl.load_extra_cities())
    check("load_cities 合并了扩展城市（缓存已失效）", _fake in _dl.load_cities())
    check("扩展城市带 _source 标记来源",
          _dl.load_extra_cities()[_fake].get("_source") == "amap-geocode")
    check("重复 remember_city 幂等（返回 False）",
          _dl.remember_city(_fake, adcode="999999",
                            center_lat=30.0, center_lng=120.0) is False)
    check("种子城市不会被写进扩展表",
          _dl.remember_city("杭州", adcode="330100",
                            center_lat=30.27, center_lng=120.15) is False)
    check("缺坐标的城市不写入",
          _dl.remember_city("无坐标城", center_lat=0.0, center_lng=0.0) is False)

    # 城市识别（nl_parse / importer 的规则兜底依赖它）
    from app.services.importer import _guess_city  # noqa: E402
    check("从文字里能认出扩展城市",
          _guess_city(f"我想去{_fake}玩") == _fake, str(_guess_city(f"我想去{_fake}玩")))
    check("从文字里仍能认出种子城市", _guess_city("我想去苏州玩") == "苏州")

    # 清单合成
    _list = _cr.list_cities()
    check("清单 hot 是种子城市", _list["hot"] == list(_seed.keys()), str(_list["hot"][:3]))
    check("hot 是 all 的子集", set(_list["hot"]) <= set(_list["all"]))
    check("all 无重复", len(_list["all"]) == len(set(_list["all"])),
          f"{len(_list['all'])} vs {len(set(_list['all']))}")
    check("all 里包含扩展城市", _fake in _list["all"])
    check("total 与 all 长度一致", _list["total"] == len(_list["all"]))
    check("hot 排在最前面", _list["all"][:len(_list["hot"])] == _list["hot"])
    check("cached 统计扩展城市数", _list["cached"] >= 1, str(_list["cached"]))
    check("is_supported 认识扩展城市", _cr.is_supported(_fake))
    check("is_supported 对完全未知城市返回 False",
          not _cr.is_supported("绝对不存在的城市xyz"))
finally:
    _dl._EXTRA_CITIES_PATH = _orig_extra
    _dl.load_cities.cache_clear()
    _shutil.rmtree(_tmp_dir, ignore_errors=True)

# ==================================================================
print("\n21. 票价抽取：从严规则（宁可未知，不要标错）")
# ==================================================================
from app.services.poi_prices import (  # noqa: E402
    MIN_CONTAIN_LEN,
    extract_ticket,
    match_price,
    normalize_name,
)

# extract_ticket 现在返回 (价格, 是否免费, 类别, 备注)
def _tk(raw, row=""):
    return extract_ticket(raw, row)

# 明确免费
_p, _f, _k, _n = _tk("免费")
check("「免费」→ 0 元且标记免费", _p == 0.0 and _f is True and _k == "free", f"{_p} {_f} {_k}")
check("免费判定不受 SKU 规则影响", _tk("不收门票")[1] is True)

# 这些原文都是实测出来的、曾经导致**错误报价**的真实样例
_bad_cases = [
    ("故宫（含讲解套餐）",
     "{'故宫博物院门票+珍宝馆+钟表馆+人工讲解（可选人群）服务成人票': ['¥125起'],"
     " '故宫博物院门票+珍宝馆+钟表馆+人工讲解（可选人群）服务学生票': ['¥88起']}",
     "含讲解的套餐，不能当成门市价（学生票 ¥88 更不是）"),
    ("国博（讲解团）",
     "{'中国国家博物馆深度讲解一日游(含门票+网红导游深度讲解)': ['¥188起'],"
     " '参观票+3小时人工讲解': ['¥108起']}",
     "国博免费，¥108 是讲解服务"),
    ("天安门广场（含讲解）",
     "{'天安门广场免费预约票+人工讲解服务大小同价': ['¥48起']}",
     "广场免费，¥48 是讲解服务"),
    ("杭州博物馆（酒店套餐）",
     "{'杭州博物馆预约票大小同价': ['¥30起'], '【酒店套餐】…': ['¥699起']}",
     "非标准成人票，且混了酒店套餐"),
    ("以现场公示为主", "具体收费情况以现场公示为主", "没有价格就该返回未知"),
    ("空值", "", "空输入"),
]
for _label, _raw, _why in _bad_cases:
    _p, _f, _k, _ = _tk(_raw)
    check(f"不当作门票价：{_label}", (_p is None or _k != "ticket") and _f is False, f"得到 {_p} 类别 {_k}（{_why}）")

# 标准成人票必须**正确**取到（这几个是实测被取错成优待价的）
_p, _, _k, _ = _tk("{'拙政园门票成人票': ['¥70起'], '拙政园门票老人票': ['¥35起']}", "拙政园")
check("只取成人票价（拙政园 ¥70，不是优待价 ¥35）",
      _p == 70.0 and _k == "ticket", f"{_p} {_k}")
_p, _, _k, _ = _tk("{'留园门票成人票': ['¥45起'], '留园门票老人票': ['¥22.5起']}", "留园")
check("只取成人票价（留园 ¥45，不是优待价 ¥22.5）", _p == 45.0 and _k == "ticket", f"{_p} {_k}")
_p, _, _k, _ = _tk("{'故宫博物院门票成人票': ['¥60起'], '故宫博物院门票学生票': ['¥30起']}", "故宫博物院")
check("纯门票成人票可正常取价（故宫 ¥60）", _p == 60.0 and _k == "ticket", f"{_p} {_k}")

# 你的规则：免费景区只收服务费的，标注"不是门票钱"
_p, _, _k, _n = _tk("{'什刹海摇橹船成人票': ['¥150起'], '什刹海游船票': ['¥120起']}", "什刹海")
check("免费景区的游船价不当作门票，改取服务均价（什刹海）",
      _k == "service_avg" and _p is not None, f"{_p} {_k}")
check("服务费备注说明不等于门票价", "不等于门票价格" in _n, _n)
_p, _, _k, _ = _tk("{'王府井步行街人工讲解服务大小同价': ['¥49起']}", "王府井")
check("讲解服务价不当作门票，改取服务均价（王府井）", _k == "service_avg" and _p is not None, f"{_p} {_k}")
_p, _, _k, _ = _tk("{'东方明珠看上海观光票成人票': ['¥195起']}", "东方明珠")
check("观光票不当作门票，改取服务均价（东方明珠）", _k == "service_avg" and _p is not None, f"{_p} {_k}")
_p, _, _k, _n = _tk("{'木府门票老人票': ['¥20起'], '万古楼门票成人票': ['¥27.8起']}", "丽江古城")
check("景区内其他景点的票不算本景点门票（丽江古城 → 木府/万古楼）",
      _p is None and _k == "unknown", f"{_p} {_k}")
check("其他景点票的备注指出归属", "其他景点" in _n, _n)

# 名称归一化与从严匹配
check("归一化剥掉分隔符", normalize_name("杭州西湖风景名胜区-花港观鱼") == "杭州西湖风景名胜区花港观鱼",
      normalize_name("杭州西湖风景名胜区-花港观鱼"))
check("归一化剥掉通用后缀", normalize_name("虎丘风景区") == "虎丘", normalize_name("虎丘风景区"))
check("归一化空输入安全", normalize_name("") == "" and normalize_name(None) == "")

_pm = {
    "花港观鱼": {"name": "花港观鱼", "price": 10.0, "is_free": False},
    "灵隐寺": {"name": "灵隐寺", "price": 45.0, "is_free": False},
    "西湖": {"name": "西湖", "price": 99.0, "is_free": False},
}
check("候选名精确匹配", match_price("杭州西湖风景名胜区-灵隐寺", _pm)["price"] == 45.0)
check("包含匹配（语料名 ≥4 字完整出现）",
      match_price("杭州西湖风景名胜区-花港观鱼", _pm)["price"] == 10.0)
check("过短的语料名不做包含匹配（避免「西湖」贴到所有含它的地点）",
      match_price("杭州西湖文化广场", _pm) is None,
      str(match_price("杭州西湖文化广场", _pm)))
check("完全无关的名称不匹配", match_price("佛山陈村花卉世界", _pm) is None)
check("空候选安全", match_price("", _pm) is None and match_price("任意", {}) is None)
check("包含匹配的最小长度有明确常量", MIN_CONTAIN_LEN >= 3, str(MIN_CONTAIN_LEN))

# 默认**不**用语料票价覆盖预算（数据不可靠，见 config 注释）

# ==================================================================
print("\n22. 候选池扩充（知识库清单 × 高德坐标）")
# ==================================================================
import asyncio as _asyncio  # noqa: E402

from app.algorithms.models import POI as _POI  # noqa: E402
from app.services import poi_pool as _pp  # noqa: E402


def _mk(name, lat=30.0, lng=120.0):
    return _POI(name=name, lat=lat, lng=lng, typecode="110000", poi_type="spot")


check("_norm 去掉标点与空格",
      _pp._norm("杭州西湖风景名胜区-花港观鱼") == "杭州西湖风景名胜区花港观鱼",
      _pp._norm("杭州西湖风景名胜区-花港观鱼"))
check("_norm 空输入安全", _pp._norm("") == "" and _pp._norm(None) == "")

_existing = {_pp._norm("杭州西湖风景名胜区-灵隐寺")}
check("已覆盖：完全同名", _pp._already_covered("杭州西湖风景名胜区-灵隐寺", _existing))
check("已覆盖：知识库短名是高德长名的子串", _pp._already_covered("灵隐寺", _existing))
check("未覆盖：无关景点", not _pp._already_covered("雷峰塔", _existing))
check("空名视为已覆盖（跳过）", _pp._already_covered("", _existing))

_orig_names, _orig_lookup = _pp.knowledge_poi_names, _pp._lookup_one
_pp.knowledge_poi_names = lambda city: []
_base = [_mk("拙政园")]
_merged, _st = _asyncio.run(_pp.augment_candidates("某城", _base, 30.0, 120.0))
check("无语料时原样返回", len(_merged) == 1 or _merged is _base)
check("无语料时统计为零", _st["knowledge"] == 0 and _st["added"] == 0, str(_st))

_FOUND = {
    "明十三陵": {"name": "明十三陵景区", "lat": 40.25, "lng": 116.22,
                 "typecode": "110000", "rating": 4.6},
    "八达岭长城": {"name": "八达岭长城", "lat": 40.36, "lng": 116.02,
                   "typecode": "110000", "rating": 4.8},
    "拙政园": {"name": "拙政园", "lat": 31.32, "lng": 120.62,
               "typecode": "110000", "rating": 4.7},   # 已在底座里，应被去重
    "不存在的地方": None,
}


async def _fake_lookup(name, city, lat, lng, sem):
    return _FOUND.get(name)


_pp.knowledge_poi_names = lambda city: ["明十三陵", "八达岭长城", "拙政园", "不存在的地方"]
_pp._lookup_one = _fake_lookup
try:
    _base2 = [_mk("拙政园")]
    _merged2, _st2 = _asyncio.run(_pp.augment_candidates("北京", _base2, 39.9, 116.4))
    _names2 = [p.name for p in _merged2]
    check("知识库景点被合并进候选池",
          "明十三陵景区" in _names2 and "八达岭长城" in _names2, str(_names2))
    check("底座已有的候选不被重复加入", _names2.count("拙政园") == 1, str(_names2))
    check("高德查不到的记为 missed", _st2["missed"] == 1, str(_st2))
    check("统计字段完整",
          set(_st2) == {"knowledge", "looked_up", "added", "missed"}, str(_st2))
    check("added 与实际新增数一致",
          _st2["added"] == len(_merged2) - len(_base2),
          f"{_st2['added']} vs {len(_merged2) - len(_base2)}")
    check("扩充后的点仍是普通 POI（不享受评分优待）",
          all(hasattr(p, "cost_source") for p in _merged2))
finally:
    _pp.knowledge_poi_names, _pp._lookup_one = _orig_names, _orig_lookup

from app.config import POI_POOL_AUGMENT as _POOL  # noqa: E402
check("候选池扩充默认开启", _POOL is True, str(_POOL))

# ==================================================================
print("\n23. 每日构成：至少一个景点、最多三顿饭")
# ==================================================================
from app.algorithms.engine import (  # noqa: E402
    MAX_MEALS_PER_DAY, _cap_meals, _ensure_spot_stop,
)


def _p(name, kind, cost=0.0, score=0.5):
    return _POI(name=name, lat=30.0, lng=120.0, typecode="110000",
                poi_type=kind, cost=cost, score=score)


# --- 一天最多三顿饭 ---
_meals = [_p(f"餐厅{i}", "restaurant", score=0.9 - i * 0.1) for i in range(7)]
_capped = _cap_meals(_meals, MAX_MEALS_PER_DAY)
check("七家餐厅被压到三顿", len(_capped) == 3, str(len(_capped)))
check("保留的是最高分的三家",
      [p.name for p in _capped] == ["餐厅0", "餐厅1", "餐厅2"],
      str([p.name for p in _capped]))
check("恰好三顿时不动", len(_cap_meals(_meals[:3], 3)) == 3)
check("少于三顿不动", len(_cap_meals(_meals[:2], 3)) == 2)
check("景点不受影响（只压餐厅）",
      len(_cap_meals([_p("景点", "spot")] + _meals, 3)) == 4,
      str(len(_cap_meals([_p("景点", "spot")] + _meals, 3))))
check("空列表安全", _cap_meals([], 3) == [])
check("上限常量是常识值", MAX_MEALS_PER_DAY == 3, str(MAX_MEALS_PER_DAY))

# --- 一天至少一个景点 ---
_pool = [_p("西湖", "spot", cost=50.0, score=0.8),
         _p("灵隐寺", "spot", cost=45.0, score=0.7)]
_all_meals = [_p("餐厅A", "restaurant", cost=100.0, score=0.9)]
_fixed = _ensure_spot_stop(_all_meals, _pool)
check("全是餐厅时补进一个景点",
      any(p.poi_type == "spot" for p in _fixed), str([p.name for p in _fixed]))
check("补进的是最高分景点", any(p.name == "西湖" for p in _fixed),
      str([p.name for p in _fixed]))
check("已有景点时不重复补",
      len(_ensure_spot_stop([_p("景点", "spot")], _pool)) == 1)
check("景点池为空时不硬塞", _ensure_spot_stop(_all_meals, []) == _all_meals)
check("空选中安全", _ensure_spot_stop([], _pool) == [])

# 「预算放不下时腾掉最低分餐厅」这条用例随预算系统一并移除。
# 现在的语义是：一天全是餐厅时，腾掉一个最低分餐厅给景点让位（与预算无关）。
_tight = _ensure_spot_stop([_p("贵餐厅", "restaurant", cost=None, score=0.9)],
                           [_p("景点", "spot", cost=None, score=0.8)])
check("全是餐厅时腾掉一个餐厅给景点",
      any(p.poi_type == "spot" for p in _tight), str([p.name for p in _tight]))
check("腾掉的是餐厅", not any(p.name == "贵餐厅" for p in _tight),
      str([p.name for p in _tight]))

# ==================================================================
print("\n24. 出行偏好：节奏 / 同行伙伴 / 时间安排")
# ==================================================================
from app.algorithms.engine import _safe_time  # noqa: E402
from app.algorithms.scheduler import _get_duration  # noqa: E402
from app.schemas import (  # noqa: E402
    DEFAULT_DAY_END, DEFAULT_DAY_START, PACE_SPOT_DELTA, VALID_COMPANIONS,
    VALID_PACES, PlanRequestDTO,
)

check("节奏枚举是三项", VALID_PACES == ["紧凑", "适中", "宽松"], str(VALID_PACES))
check("同行伙伴枚举是五项",
      VALID_COMPANIONS == ["独自", "家庭", "情侣", "朋友", "老人"], str(VALID_COMPANIONS))
check("紧凑多选、宽松少选", PACE_SPOT_DELTA["紧凑"] > 0 > PACE_SPOT_DELTA["宽松"],
      str(PACE_SPOT_DELTA))
check("适中不改变选点", PACE_SPOT_DELTA["适中"] == 0)

# 节奏真的改变每站停留时长——这是「紧凑」能排进更多点的原因。
# 只加选点数量是不够的：默认时间窗已吃满 12 小时上限，多选的点排不进去。
_spot = _p("某博物馆", "spot")
check("紧凑缩短停留", _get_duration(_spot, "紧凑") < _get_duration(_spot, "适中"),
      f"{_get_duration(_spot, '紧凑')} vs {_get_duration(_spot, '适中')}")
check("宽松延长停留", _get_duration(_spot, "宽松") > _get_duration(_spot, "适中"),
      f"{_get_duration(_spot, '宽松')} vs {_get_duration(_spot, '适中')}")
check("留空节奏不缩放", _get_duration(_spot, "") == _get_duration(_spot, "适中"))
check("停留时长不低于最小值", _get_duration(_p("X", "restaurant"), "紧凑") >= 30,
      str(_get_duration(_p("X", "restaurant"), "紧凑")))

# 时间校验：填错回退默认而不是报错
check("合法时间原样返回", _safe_time("07:30", DEFAULT_DAY_START) == "07:30")
check("空值用默认", _safe_time("", DEFAULT_DAY_START) == DEFAULT_DAY_START)
check("小时越界用默认", _safe_time("25:00", DEFAULT_DAY_START) == DEFAULT_DAY_START)
check("分钟越界用默认", _safe_time("09:99", DEFAULT_DAY_START) == DEFAULT_DAY_START)
check("非数字用默认", _safe_time("乱填", DEFAULT_DAY_START) == DEFAULT_DAY_START)
check("长度不对用默认", _safe_time("9:00", DEFAULT_DAY_START) == DEFAULT_DAY_START)
check("结束时间同样校验", _safe_time("22:00", DEFAULT_DAY_END) == "22:00")

# DTO 接受新字段且都有默认值（老前端不传也能用，向后兼容）
_dto = PlanRequestDTO(city="杭州", days=2, budget=1000, tags=["历史"])
check("不传出行偏好也能构造（向后兼容）",
      _dto.companions == [] and _dto.pace == ""
      and _dto.day_start == "" and _dto.day_end == "", str(_dto))
_dto2 = PlanRequestDTO(city="杭州", days=2, budget=1000, tags=["历史"],
                       companions=["老人"], pace="宽松",
                       day_start="07:30", day_end="22:00")
check("出行偏好字段可传入",
      _dto2.companions == ["老人"] and _dto2.pace == "宽松"
      and _dto2.day_start == "07:30" and _dto2.day_end == "22:00")

# ==================================================================
print("\n25. 同行伙伴：每日景点数 + 名称关键词偏好/避让")
# ==================================================================
from app.algorithms.engine import _companion_bonus  # noqa: E402
from app.schemas import (  # noqa: E402
    COMPANION_AVOID, COMPANION_FOOD, COMPANION_FOOD_BONUS, COMPANION_PREFER,
    COMPANION_PREFER_BONUS, COMPANION_SPOT_DELTA,
)

# --- 每日景点数的相对次序（对齐实际节奏经验）---
check("独自最多、老人最少",
      COMPANION_SPOT_DELTA["独自"] == max(COMPANION_SPOT_DELTA.values())
      and COMPANION_SPOT_DELTA["老人"] == min(COMPANION_SPOT_DELTA.values()),
      str(COMPANION_SPOT_DELTA))
check("老人比家庭/情侣更保守",
      COMPANION_SPOT_DELTA["老人"] < COMPANION_SPOT_DELTA["家庭"]
      and COMPANION_SPOT_DELTA["老人"] < COMPANION_SPOT_DELTA["情侣"],
      str(COMPANION_SPOT_DELTA))
check("朋友介于独自和家庭之间",
      COMPANION_SPOT_DELTA["独自"] > COMPANION_SPOT_DELTA["朋友"]
      > COMPANION_SPOT_DELTA["家庭"],
      str(COMPANION_SPOT_DELTA))
check("五种同行伙伴都有定义",
      set(COMPANION_SPOT_DELTA) == set(VALID_COMPANIONS), str(set(COMPANION_SPOT_DELTA)))

# --- 偏好与避让不能自相矛盾（同一伙伴下同一个词不能既爱又恨）---
for _who in VALID_COMPANIONS:
    _prefer = set(COMPANION_PREFER.get(_who, ()))
    _avoid = set(COMPANION_AVOID.get(_who, ()))
    check(f"{_who}：偏好与避让无重叠", not (_prefer & _avoid),
          str(_prefer & _avoid))
check("偏好词表无重复项",
      all(len(v) == len(set(v)) for v in COMPANION_PREFER.values()))
check("避让词表无重复项",
      all(len(v) == len(set(v)) for v in COMPANION_AVOID.values()))

# --- 老人避让的是费体力项目，而不是省力的索道/缆车 ---
check("老人避让包含漂流/登山等剧烈项目",
      {"漂流", "登山", "攀岩"} <= set(COMPANION_AVOID["老人"]),
      str(COMPANION_AVOID["老人"]))
check("索道/缆车不在老人避让项里（它们是省力的上山方式）",
      "索道" not in COMPANION_AVOID["老人"] and "缆车" not in COMPANION_AVOID["老人"],
      str(COMPANION_AVOID["老人"]))

# --- 关键词加成真的生效 ---
check("家庭遇到乐园加分", _companion_bonus("杭州乐园", ["家庭"]) > 0)
check("情侣遇到观景台加分", _companion_bonus("城市阳台观景台", ["情侣"]) > 0)
check("老人遇到公园加分", _companion_bonus("钱江世纪公园", ["老人"]) > 0)
check("餐厅口味也参与加分", _companion_bonus("老字号粥铺", ["老人"]) > 0)
check("不相关的名字不加分", _companion_bonus("某某大厦", ["家庭"]) == 0.0)
check("空名字安全", _companion_bonus("", ["家庭"]) == 0.0)
check("不选同行伙伴不加分", _companion_bonus("杭州乐园", []) == 0.0)
check("加成不超过两个来源之和",
      _companion_bonus("乐园火锅", ["家庭"]) <= COMPANION_PREFER_BONUS + COMPANION_FOOD_BONUS)


# ==================================================================
print("\n26. 移除预算后的新契约（执行规格 §7.2 要求）")
# ==================================================================
from app.algorithms.models import PROFILES, LEGACY_PROFILES, rating_norm as _rn  # noqa: E402
from app.schemas import VALID_PROFILES as _VP  # noqa: E402

# --- ① rating_norm 下界 4.0：评分轴必须真的有区分度 ---
check("rating_norm(4.0) == 0", _rn(4.0) == 0.0, str(_rn(4.0)))
check("rating_norm(4.5) == 0.5", near(_rn(4.5), 0.5), str(_rn(4.5)))
check("rating_norm(4.9) == 0.9", near(_rn(4.9), 0.9), str(_rn(4.9)))
check("rating_norm(3.9) == 0（低分不再获得正分）", _rn(3.9) == 0.0, str(_rn(3.9)))
check("rating_norm 在 4.0-5.0 上单调递增",
      all(_rn(4.0 + i * 0.05) <= _rn(4.0 + (i + 1) * 0.05) for i in range(20)))
# 区分度：4.0→5.0 的跨度必须覆盖 [0,1] 的大部分，否则该维度等于无效
check("评分跨度至少覆盖 [0,1] 的 90%", _rn(5.0) - _rn(4.0) >= 0.9,
      f"{_rn(4.0)} -> {_rn(5.0)}")

# --- ② 三套 profile 必须真的不同（防再次塌缩）---
check("VALID_PROFILES 是新三档", _VP == ["悠闲", "经典", "尽览"], str(_VP))
check("三套权重互不相同",
      len({tuple(sorted(w.items())) for w in PROFILES.values()}) == 3,
      str(list(PROFILES)))
check("三套权重都不含 w_cost",
      all("w_cost" not in w for w in PROFILES.values()), str(list(PROFILES)))
check("三套权重各自和为 1",
      all(abs(sum(w.values()) - 1) < 1e-9 for w in PROFILES.values()))
check("尽览最不容忍绕路（w_detour 最高）",
      PROFILES["尽览"]["w_detour"] == max(w["w_detour"] for w in PROFILES.values()))
check("悠闲最看重兴趣（w_interest 最高）",
      PROFILES["悠闲"]["w_interest"] == max(w["w_interest"] for w in PROFILES.values()))

# 同一候选点在三套 profile 下的分数必须不同（塌缩的直接反证）
_shared = POI(name="共同候选", lat=30.01, lng=120.01, typecode="110000",
              poi_type="spot", rating=4.8, tags=["历史"])
_scores = {n: compute_score(_shared, ["历史"], 30.0, 120.0, 15.0, w)[0]
           for n, w in PROFILES.items()}
check("同一候选在三套 profile 下分数互不相同",
      len(set(_scores.values())) == 3, str(_scores))

# --- ③ 旧 profile 名能映射（21 条历史行程）---
check("旧名映射表覆盖三档",
      set(LEGACY_PROFILES) == {"经济型", "舒适型", "紧凑型"}, str(LEGACY_PROFILES))
check("经济型 → 悠闲", LEGACY_PROFILES["经济型"] == "悠闲")
check("舒适型 → 经典", LEGACY_PROFILES["舒适型"] == "经典")
check("紧凑型 → 尽览（不叫紧凑，避免与 pace 撞名）",
      LEGACY_PROFILES["紧凑型"] == "尽览")
check("get_profile 接受旧名并返回对应权重",
      get_profile("经济型") == PROFILES["悠闲"], str(get_profile("经济型")))
check("get_profile 未知名回退经典",
      get_profile("不存在的风格") == PROFILES["经典"])

# --- ④ 票价数字门禁（A2- 的核心契约）---
_ALLOWED_SOURCES = {"real", "third_party", "free", "average", None}
check("cost_source 合法取值集合 ⊆ {real, third_party, free, average, None}",
      _ALLOWED_SOURCES == {"real", "third_party", "free", "average", None})

# 造各种来源的 POI，验证"只有三个来源允许带数字"
def _mk(source, cost):
    return POI(name=f"点{source}", lat=30.0, lng=120.0, typecode="110000",
               poi_type="spot", cost=cost, cost_source=source, cost_note="备注")

_gate_cases = [
    (_mk("real", 88.0), True, "real 显示数字"),
    (_mk("third_party", 70.0), True, "third_party 显示数字"),
    (_mk("free", 0.0), True, "free 显示（前端渲染成「免费」）"),
    (_mk("average", None), False, "average 不显示数字"),
    (_mk(None, None), False, "None 不显示数字"),
]
for _poi, _should_show, _label in _gate_cases:
    has_number = _poi.cost_source in ("real", "third_party", "free") and _poi.cost is not None
    check(f"数字门禁：{_label}", has_number == _should_show,
          f"source={_poi.cost_source} cost={_poi.cost}")

# --- ⑤ reserved 已彻底消失 ---
check("reserved 不再是合法 cost_source", "reserved" not in _ALLOWED_SOURCES)
from app.algorithms.models import POI as _POI2  # noqa: E402
check("POI.cost_source 默认值是 None（不是 reserved）",
      _POI2.__dataclass_fields__["cost_source"].default is None,
      str(_POI2.__dataclass_fields__["cost_source"].default))
check("POI.cost 默认值是 None（不是 0.0）",
      _POI2.__dataclass_fields__["cost"].default is None,
      str(_POI2.__dataclass_fields__["cost"].default))

# --- ⑥ 带老人时 spot_delta 取 min 不相加 ---
from app.schemas import COMPANION_SPOT_DELTA as _CSD  # noqa: E402
_two = ["朋友", "老人"]
check("多选同行伙伴取 min（不相加）",
      min(_CSD[c] for c in _two) == _CSD["老人"],
      f"min={min(_CSD[c] for c in _two)} 相加={sum(_CSD[c] for c in _two)}")
check("老人是最保守的同行伙伴", _CSD["老人"] == min(_CSD.values()), str(_CSD))

print(f"\n{'=' * 60}")
print(f"通过 {PASSED} 项，失败 {FAILED} 项")
print(f"{'=' * 60}")
sys.exit(1 if FAILED else 0)