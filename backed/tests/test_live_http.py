"""针对**已启动的后端服务**的真实 HTTP 冒烟测试。

与 ``tests/test_auth_history.py`` 的区别：
- 那个用 TestClient 进程内调用，不依赖服务，不消耗配额；
- 这个打真实端口（默认 http://127.0.0.1:8080），并且会真实调用 ``/api/plan``
  （即真实消耗高德与 SiliconFlow 配额），用于验证"注册 → 登录 → 规划 → 落库 → 回看"全链路。

用法：
    1) 另开终端：cd backed && python run.py
    2) python tests/test_live_http.py [base_url]
"""
import sys
import time
from pathlib import Path

import httpx

# 需要先能 import app.*，再取配置里的天数上限
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.config import MAX_TRIP_DAYS  # noqa: E402

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8080"
# 真实规划含串行高德调用 + LLM 润色，给足超时
PLAN_TIMEOUT = 240.0

# 固定测试账号；重复运行时注册会返回 409，自动退化为登录
TEST_USERNAME = "smoke_tester"
TEST_PASSWORD = "smoke_pass_2026"

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


def main() -> int:
    print(f"目标服务：{BASE}\n")

    with httpx.Client(base_url=BASE, timeout=30.0) as client:
        print("1. 服务可用性")
        resp = client.get("/api/meta")
        check("GET /api/meta", resp.status_code == 200 and resp.json().get("success"), resp.text[:200])
        meta = resp.json()
        print(f"       高德启用={meta.get('amap_enabled')}")

        # ---------------- 注册或登录 ----------------
        print("\n2. 账号注册 / 登录")
        token = None
        creds = {"username": TEST_USERNAME, "password": TEST_PASSWORD}
        resp = client.post("/api/auth/register", json={**creds, "nickname": "冒烟测试"})
        if resp.status_code == 200:
            check("注册成功", True)
            token = resp.json()["data"]["token"]
        else:
            # 该测试账号已存在 → 走登录
            check("测试账号已存在，改走登录", resp.status_code == 409, resp.text[:200])
            resp = client.post("/api/auth/login", json=creds)
            check("登录成功", resp.status_code == 200, resp.text[:300])
            if resp.status_code == 200:
                token = resp.json()["data"]["token"]

        # 密码错误必须被拒绝，而不是放行
        resp = client.post("/api/auth/login",
                           json={"username": TEST_USERNAME, "password": "wrong_password"})
        check("错误密码返回 401", resp.status_code == 401, resp.text[:200])

        if not token:
            print("\n无法取得登录态，终止")
            return 1
        headers = {"Authorization": f"Bearer {token}"}

        resp = client.get("/api/auth/me", headers=headers)
        me = resp.json().get("data") or {}
        check("GET /api/auth/me", resp.status_code == 200 and me.get("username") == TEST_USERNAME,
              resp.text[:200])
        check("用户信息不含密码哈希", "password_hash" not in me, str(me.keys()))
        user_id = me["id"]
        print(f"       用户 id={user_id} 账号={me.get('username')}")

        # ---------------- 真实规划 ----------------
        print("\n3. 自然语言解析 /api/parse（真实 LLM，不产生行程）")
        parse_text = "带爸妈去苏州玩三天，预算2500，喜欢历史和园林，轻松点"
        parsed = None
        # source=rule 表示大模型那一步失败了（会退回本地规则解析）。
        # 大模型偶发瞬时断连属正常，因此这里最多重试 2 次；
        # 若始终是 rule，说明大模型链路真的不可用，仍应报失败。
        for attempt in range(3):
            resp = client.post("/api/parse", json={"text": parse_text}, timeout=PLAN_TIMEOUT)
            if resp.status_code != 200:
                break
            parsed = resp.json().get("data") or {}
            if parsed.get("source") == "llm":
                break
            print(f"       大模型返回异常（source={parsed.get('source')}），重试第 {attempt + 1} 次…")
        check("/api/parse 返回 200", resp.status_code == 200, resp.text[:300])
        if resp.status_code == 200:
            req = parsed.get("request") or {}
            check("抽取出目的地", req.get("city") == "苏州", str(req))
            check("抽取出天数", req.get("days") == 3, str(req))
            check("抽取出出行节奏", req.get("profile") == "舒适型", str(req))
            # 预算字段已随预算系统移除，解析结果里不应再出现
            check("解析结果不含已移除的 budget 字段", "budget" not in req, str(req))
            check("明确报告识别到的字段", len(parsed.get("inferred") or []) >= 4,
                  str(parsed.get("inferred")))
            check("解析来源标记为 llm", parsed.get("source") == "llm", str(parsed.get("source")))
            print(f"       inferred={parsed.get('inferred')} assumed={parsed.get('assumed')} "
                  f"source={parsed.get('source')}")

        # 越界输入必须被夹取而不是透传（白名单/范围防线的端到端验证）
        resp = client.post("/api/parse", json={"text": "去杭州玩30天，喜欢历史和不存在的东西",
                                               "defaults": {"city": "", "days": 3,
                                                            "tags": [], "must_go": [], "avoid": [],
                                                            "profile": "舒适型"}},
                           timeout=PLAN_TIMEOUT)
        if resp.status_code == 200:
            req = (resp.json().get("data") or {}).get("request") or {}
            check(f"天数 30 保留为 30（上限已从 7 提升到 {MAX_TRIP_DAYS}）",
                  req.get("days") == 30, str(req))
            check("非法标签被白名单过滤",
                  all(t in ["历史", "自然", "美食", "购物", "艺术", "夜生活"]
                      for t in (req.get("tags") or [])), str(req))
        else:
            check("/api/parse 越界输入仍返回 200", False, resp.text[:200])

        # ---------------- 真实规划 ----------------
        print("\n4. 真实 /api/plan（消耗高德 + LLM 配额）")
        plan_body = {
            "city": "杭州",
            "days": 2,
            "tags": ["历史", "自然"],
            "must_go": [],
            "avoid": [],
            "profile": "舒适型",
        }
        try:
            resp = client.post("/api/plan", json=plan_body, timeout=PLAN_TIMEOUT)
        except httpx.TimeoutException:
            check("/api/plan 在超时内返回", False, f">{PLAN_TIMEOUT}s")
            resp = None
        if resp is not None:
            check("/api/plan 返回 200", resp.status_code == 200, resp.text[:300])
            if resp.status_code == 200:
                itinerary = resp.json()["data"]
                day_count = len(itinerary.get("days") or [])
                item_count = sum(len(d.get("items") or []) for d in itinerary.get("days") or [])
                check("行程天数与请求一致", day_count == 2, f"days={day_count}")
                check("行程含景点条目", item_count > 0, f"items={item_count}")
                check("增强模式标记存在", "enhanced" in itinerary, str(itinerary)[:200])
                check("顶层描述已生成或为空", itinerary.get("description") is None or isinstance(itinerary.get("description"), str))
                print(f"       城市={itinerary.get('city')} 天数={day_count} 条目={item_count} "
                      f"enhanced={itinerary.get('enhanced')}")

                # 真实数据管线：这几项此前恒为空/恒为 0，是回归重点
                all_items = [i for d in itinerary.get("days") or [] for i in d.get("items") or []]
                rated = [i for i in all_items if i.get("score_breakdown", {}).get("rating", 0) > 0]
                check("口碑维度取自 biz_ext.rating（不再恒为 0）",
                      len(rated) == len(all_items) and all_items,
                      f"{len(rated)}/{len(all_items)}")
                with_hours = [i for i in all_items if i.get("open_hours")]
                check("营业时间已填充", len(with_hours) > 0, f"{len(with_hours)}/{len(all_items)}")
                check("费用来源字段存在",
                      all(i.get("cost_source") in ("real", "estimated") for i in all_items),
                      str([i.get("cost_source") for i in all_items])[:200])
                photos = [i for i in all_items if i.get("photos")]
                check("条目带高德真实图片", len(photos) > 0, f"{len(photos)}/{len(all_items)}")
                http_photos = [u for i in all_items for u in i.get("photos") or [] if u.startswith("http://")]
                check("图片地址已归一为 https", not http_photos, str(http_photos)[:200])
                print(f"       rating非零={len(rated)}/{len(all_items)} 有营业时间={len(with_hours)} "
                      f"带图={len(photos)}")

                print("\n4b. 城际交通估算 /api/intercity（真实地理编码）")
                resp = client.get("/api/intercity", params={"origin": "当涂", "dest": "杭州"},
                                  timeout=PLAN_TIMEOUT)
                check("GET /api/intercity", resp.status_code == 200, resp.text[:300])
                ic = resp.json()["data"]
                check("当涂→杭州 解析成功", ic.get("ok") is True, str(ic)[:200])
                check("给出距离与驾车估算",
                      (ic.get("distance_km") or 0) > 0 and (ic.get("drive_minutes") or 0) > 0, str(ic)[:200])
                check("标记为估算而非班次", ic.get("estimated") is True, str(ic)[:200])
                check("回显高德实际解析到的地址", bool(ic.get("origin", {}).get("resolved_address")),
                      str(ic.get("origin"))[:200])
                check("不伪造车次字段", "train_no" not in ic and "schedule" not in ic, str(ic.keys()))
                print(f"       当涂→杭州 {ic.get('distance_km')}km 驾车约{ic.get('drive_minutes')}分 "
                      f"解析={ic.get('origin', {}).get('resolved_address')}")

                # 高德对无法识别的输入会模糊匹配到别处，必须被拒绝
                resp = client.get("/api/intercity",
                                  params={"origin": "不存在的城市xyz", "dest": "杭州"},
                                  timeout=PLAN_TIMEOUT)
                bad = resp.json()["data"]
                check("乱码出发地被拒绝而非模糊匹配", bad.get("ok") is False, str(bad)[:200])

                print("\n4d. 旅行灵感流 /api/inspiration（等待期填充内容）")
                t0 = time.time()
                resp = client.get("/api/inspiration", params={"city": "杭州", "limit": 12},
                                  timeout=PLAN_TIMEOUT)
                insp_secs = time.time() - t0
                check("GET /api/inspiration", resp.status_code == 200, resp.text[:300])
                insp = resp.json()["data"]
                check("灵感流返回卡片", insp.get("total", 0) > 0, str(insp)[:200])
                check("灵感卡片带真实照片",
                      all(i.get("photos") for i in insp.get("items", [])),
                      str([bool(i.get("photos")) for i in insp.get("items", [])])[:200])
                check("灵感卡片带真实评分",
                      all(i.get("rating", 0) > 0 for i in insp.get("items", [])), "存在评分为 0 的卡片")
                check("灵感分类为真实类目（无编造的「新开业」）",
                      all(c["key"] in ("风景", "博物馆", "展览", "美食", "购物", "住宿", "其他")
                          for c in insp.get("categories", [])),
                      str([c["key"] for c in insp.get("categories", [])]))
                check("商圈字段已归一（不出现 [] 字面量）",
                      all(i.get("business_area") != "[]" for i in insp.get("items", [])),
                      str([i.get("business_area") for i in insp.get("items", [])])[:200])
                check("灵感接口足够快（等待期才有意义）", insp_secs < 20, f"{insp_secs:.1f}s")
                resp = client.get("/api/inspiration", params={"city": "不存在的城市xyz"}, timeout=PLAN_TIMEOUT)
                check("乱码城市返回空而非报错",
                      resp.status_code == 200 and resp.json()["data"]["total"] == 0, resp.text[:200])
                print(f"       卡片={insp.get('total')} 分类={[c['key'] for c in insp.get('categories', [])]} "
                      f"耗时={insp_secs:.2f}s")

                print("\n4e. 导入线路/地点 /api/import/text（地点必须逐个经高德校验）")
                import_text = ("苏州两日游：D1 上午去拙政园，中午在平江路吃饭，"
                               "下午逛苏州博物馆；D2 去虎丘，晚上去星槎幻月太空主题乐园。")
                resp = client.post("/api/import/text",
                                   json={"text": import_text, "city": "苏州"},
                                   timeout=PLAN_TIMEOUT)
                check("POST /api/import/text", resp.status_code == 200, resp.text[:300])
                imp = resp.json()["data"]
                _matched = [p for p in imp["places"] if p["matched"]]
                _unmatched = [p for p in imp["places"] if not p["matched"]]
                check("识别出地点", len(imp["places"]) > 0, str(imp)[:200])
                check("匹配到的地点带高德真实坐标",
                      all(p["poi"].get("lat") and p["poi"].get("lng") for p in _matched),
                      str(_matched)[:200])
                check("匹配到的地点都是真实 POI（有名称）",
                      all(p["poi"].get("name") for p in _matched), str(_matched)[:200])
                # 这两条是本功能的核心承诺：不能静默丢弃识别不到的地点
                check("未匹配的地点被明确列出（不静默丢弃）",
                      len(_unmatched) >= 1, f"未匹配 {len(_unmatched)} 个")
                check("未匹配项带有可读原因",
                      all(p.get("reason") for p in _unmatched), str(_unmatched)[:200])
                check("匹配数 + 未匹配数 == 识别总数",
                      imp["matched_count"] + imp["unmatched_count"] == len(imp["places"]),
                      f"{imp['matched_count']}+{imp['unmatched_count']} != {len(imp['places'])}")
                print(f"       识别 {len(imp['places'])} 个：匹配 {imp['matched_count']} / "
                      f"未匹配 {imp['unmatched_count']}，source={imp['source']}")
                for _p in _unmatched[:2]:
                    print(f"         未匹配: {_p['query']} → {_p['reason']}")

                print("\n4c. 路线缩略图 /api/map/thumbnail（高德静态地图，后端代理）")
                pts = ";".join(f"{i['lat']},{i['lng']}" for i in all_items if i.get("lat"))
                sizes = ",".join(str(len(d.get("items") or [])) for d in itinerary.get("days") or [])
                resp = client.get("/api/map/thumbnail",
                                  params={"points": pts, "day_sizes": sizes, "width": 750, "height": 420},
                                  timeout=PLAN_TIMEOUT)
                check("GET /api/map/thumbnail", resp.status_code == 200, resp.text[:200])
                thumb_size = len(resp.content)
                check("返回 PNG 图片",
                      resp.headers.get("content-type", "").startswith("image/png") and thumb_size > 5000,
                      f"{resp.headers.get('content-type')} {thumb_size}B")
                resp = client.get("/api/map/thumbnail", params={"points": ""}, timeout=PLAN_TIMEOUT)
                check("空点位返回 204 而非报错", resp.status_code == 204, str(resp.status_code))
                print(f"       缩略图 {thumb_size}B；空点位返回 {resp.status_code}")

                # ---------------- 落库 ----------------
                print("\n5. 行程落库 / 列表 / 详情")
                save_body = {
                    "client_id": f"smoke-{user_id}",
                    "title": "冒烟测试·杭州2日游",
                    "origin": "当涂",
                    "request": plan_body,
                    "itinerary": itinerary,
                }
                resp = client.post("/api/history", json=save_body, headers=headers)
                check("POST /api/history", resp.status_code == 200, resp.text[:300])
                hid = resp.json()["data"]["id"]

                resp = client.post("/api/history", json=save_body, headers=headers)
                check("幂等：重复保存同 client_id 返回同 id", resp.json()["data"]["id"] == hid,
                      resp.text[:200])

                resp = client.get("/api/history", headers=headers)
                check("GET /api/history", resp.status_code == 200 and resp.json()["data"]["total"] >= 1,
                      resp.text[:300])
                total = resp.json()["data"]["total"]
                print(f"       历史总数={total}")

                resp = client.get(f"/api/history/{hid}", headers=headers)
                body = resp.json()
                check("GET /api/history/{id} 详情", resp.status_code == 200, resp.text[:300])
                check("详情回放的行程与规划一致",
                      len(body["data"]["itinerary"]["days"]) == len(itinerary["days"])
                      and body["data"]["itinerary"]["city"] == itinerary["city"],
                      f"{body['data']['itinerary'].get('city')} / {len(body['data']['itinerary']['days'])} 天")
                check("行程不再含已移除的 total_cost 字段",
                      "total_cost" not in itinerary, str(list(itinerary.keys())))
                check("详情保留原始入参", body["data"]["request"]["city"] == "杭州", resp.text[:300])

                print("\n6. 清理与退出")
                resp = client.delete(f"/api/history/{hid}", headers=headers)
                check("DELETE /api/history/{id}", resp.status_code == 200, resp.text[:200])
                resp = client.get(f"/api/history/{hid}", headers=headers)
                check("删除后 404", resp.status_code == 404, resp.text[:200])
            else:                print(f"       跳过落库验证：/api/plan 未成功。响应片段：{resp.text[:300]}")

        resp = client.post("/api/auth/logout", headers=headers)
        check("POST /api/auth/logout", resp.status_code == 200, resp.text[:200])
        resp = client.get("/api/auth/me", headers=headers)
        check("退出后 token 失效", resp.status_code == 401, resp.text[:200])

    print(f"\n{'=' * 56}")
    print(f"通过 {PASSED} 项，失败 {FAILED} 项")
    print(f"{'=' * 56}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
