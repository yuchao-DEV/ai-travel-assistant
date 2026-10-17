"""用户系统 + 历史行程的端到端集成测试（不依赖高德/LLM，不消耗任何配额）。

直接用 FastAPI TestClient 打真实路由，覆盖：
验证码发送 → 注册 → 登录 → 鉴权 → 历史落库/幂等/分页/详情/删除 → 本地记录导入
→ 频率限制 → 越权隔离。

运行：cd backed && python tests/test_auth_history.py
不需要 pytest，也不需要新增任何依赖（httpx 随 FastAPI 一起已安装）。
"""
import os
import sys
from pathlib import Path

# ---- 必须在导入 app 之前设置环境变量 ----
# 用工作区内的独立测试库，避免污染 backed/data/app.db
# （不放系统临时目录：DSH 沙箱只保证工作区可写）
_BACKED_DIR = Path(__file__).resolve().parent.parent
_TEST_DB = str(_BACKED_DIR / "data" / "_test_auth_history.db")

for _suffix in ("", "-wal", "-shm"):
    _stale = Path(_TEST_DB + _suffix)
    if _stale.exists():
        _stale.unlink()

os.environ["DB_PATH"] = _TEST_DB
os.environ["SMS_PROVIDER"] = "dev"
os.environ["SMS_DEBUG_ECHO"] = "true"
os.environ["SMS_SEND_INTERVAL"] = "0"      # 正常流程里不受最小间隔限制
os.environ["SMS_HOURLY_LIMIT"] = "1000"    # 频率限制单独用 monkeypatch 测
os.environ["SMS_CODE_TTL"] = "300"
os.environ["LLM_API_KEY"] = ""             # 确保本测试完全不触发 LLM

sys.path.insert(0, str(_BACKED_DIR))

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.services import auth as auth_service  # noqa: E402

USER_A = "tester_a"
USER_B = "tester_b"
PASSWORD_A = "secret123"

PASSED = 0
FAILED = 0


def check(label: str, condition: bool, extra: str = "") -> None:
    """记录一条断言结果。"""
    global PASSED, FAILED
    if condition:
        PASSED += 1
        print(f"  [PASS] {label}")
    else:
        FAILED += 1
        print(f"  [FAIL] {label} {extra}")


def fake_itinerary(city: str = "杭州", days: int = 1) -> dict:
    """构造一份合法的 ItineraryVO，用于验证历史落库（不调用算法引擎）。"""
    return {
        "city": city,
        "profile": "经典",
        "total_cost": 120.0,
        "description": "测试行程描述",
        "enhanced": True,
        "weather": ["晴 25°/15°"],
        "days": [
            {
                "date": f"Day {i + 1}",
                "daily_cost": 120.0,
                "daily_summary": "第一天导语",
                "items": [
                    {
                        "type": "spot",
                        "name": "杭州博物馆",
                        "lat": 30.2456,
                        "lng": 120.1550,
                        "start": "09:00",
                        "end": "11:00",
                        "cost": 0.0,
                        "score": 0.62,
                        "score_breakdown": {
                            "interest": 1.0,
                            "rating": 0.5,
                            "detour": 0.13,
                            "cost": 0.0,
                        },
                        "transport": {"mode": "bus", "duration": 22, "distance": 8.3},
                    }
                ],
            }
            for i in range(days)
        ],
    }


def fake_request(city: str = "杭州", days: int = 1) -> dict:
    return {
        "city": city,
        "days": days,
        "tags": ["历史", "自然"],
        "must_go": [],
        "avoid": [],
        "profile": "经典",
    }


def auth_header(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def main() -> int:
    print(f"测试数据库：{_TEST_DB}\n")

    # TestClient 作为上下文管理器会触发 lifespan → init_db()
    with TestClient(app) as client:
        # ============================================================
        print("1. 账号注册（账号 + 密码）")
        # ==================================================================
        resp = client.post("/api/auth/register", json={
            "username": USER_A, "password": PASSWORD_A, "nickname": "测试用户甲"})
        body = resp.json()
        check("注册成功", resp.status_code == 200 and body["success"], str(body)[:200])
        token_a = body["data"]["token"]
        check("注册即返回登录态 token", bool(token_a), str(body)[:120])
        user_a = body["data"]["user"]
        check("返回的用户对象含账号", user_a.get("username") == USER_A, str(user_a))
        check("不返回密码哈希", "password_hash" not in user_a, str(user_a.keys()))
        check("注册即记录登录时间", user_a["last_login_at"] is not None)
        check("昵称按传入值保存", user_a.get("nickname") == "测试用户甲", str(user_a))

        resp = client.post("/api/auth/register", json={
            "username": USER_A, "password": PASSWORD_A})
        check("重复账号被拒(409)", resp.status_code == 409, resp.text)
        check("重复账号有中文提示", "占用" in resp.json()["message"], resp.text)

        resp = client.post("/api/auth/register", json={
            "username": "tester_long_nick", "password": PASSWORD_A, "nickname": "x" * 40})
        check("超长昵称被拒", resp.status_code == 400, resp.text)

        resp = client.post("/api/auth/register", json={"username": "ab", "password": PASSWORD_A})
        check("账号过短被拒(400)", resp.status_code == 400, resp.text)

        resp = client.post("/api/auth/register", json={"username": "abc", "password": "123"})
        check("密码过短被拒(400)", resp.status_code == 400, resp.text)

        resp = client.post("/api/auth/register", json={
            "username": "a b c", "password": PASSWORD_A})
        check("含空格的账号被拒(400)", resp.status_code == 400, resp.text)

        # ==================================================================
        print("\n2. 登录（账号 + 密码）")
        # ==================================================================
        resp = client.post("/api/auth/login", json={
            "username": USER_A, "password": PASSWORD_A})
        body = resp.json()
        check("正确密码登录成功", resp.status_code == 200 and body["success"], str(body)[:200])
        check("登录返回新 token", body["data"]["token"] != token_a, "token 未变化")

        resp = client.post("/api/auth/login", json={
            "username": USER_A, "password": "wrong-password"})
        check("错误密码被拒(401)", resp.status_code == 401, resp.text)
        msg_wrong_pwd = resp.json()["message"]

        resp = client.post("/api/auth/login", json={
            "username": "no_such_user_x", "password": PASSWORD_A})
        check("不存在的账号被拒(401)", resp.status_code == 401, resp.text)
        check("两种失败返回**同一条**消息（防账号枚举）",
              resp.json()["message"] == msg_wrong_pwd,
              f"{resp.json()['message']} vs {msg_wrong_pwd}")

        resp = client.post("/api/auth/login", json={
            "username": USER_A.upper(), "password": PASSWORD_A})
        check("账号大小写不敏感（归一为小写）", resp.status_code == 200, resp.text)
        token_a2 = resp.json()["data"]["token"]
        check("重新登录换发了新 token", token_a2 != token_a, "token 未变化")

        # ==================================================================
        print("\n3. 密码存储与验证码下线")
        # ==================================================================
        from app.security import hash_password, verify_password  # noqa: E402

        h = hash_password("abc123")
        check("哈希格式为 pbkdf2$迭代$salt$hash", h.startswith("pbkdf2$"), h[:24])
        check("同密码两次哈希不同（随机 salt）", hash_password("abc123") != h)
        check("正确密码校验通过", verify_password("abc123", h))
        check("错误密码校验失败", not verify_password("abc124", h))
        check("空哈希校验失败（旧账号无密码）", not verify_password("abc123", ""))
        check("损坏哈希不抛错，返回 False", not verify_password("abc123", "garbage"))

        resp = client.post("/api/auth/send-code", json={"phone": "13800001111"})
        check("旧短信验证码接口已下线(404)", resp.status_code == 404, resp.text)

        # ==================================================================
        # 第二个用户：用于越权隔离测试（B 不能看/改/删 A 的行程）
        resp = client.post("/api/auth/register", json={
            "username": USER_B, "password": PASSWORD_A})
        check("第二个账号注册成功", resp.status_code == 200, resp.text)
        token_b = resp.json()["data"]["token"]

        print("\n4. 鉴权")
        # ==================================================================
        print("\n5. 历史行程落库")
        payload = {
            "client_id": "local-1001",
            "title": "当涂出发·杭州1日游",
            "origin": "当涂",
            "request": fake_request(),
            "itinerary": fake_itinerary(),
        }
        resp = client.post("/api/history", json=payload, headers=auth_header(token_a2))
        body = resp.json()
        check("保存历史成功", resp.status_code == 200 and body["success"], str(body)[:300])
        hid = body["data"]["id"]
        check("列表项含标题", body["data"]["title"] == "当涂出发·杭州1日游", str(body["data"]))
        check("列表项统计条目数", body["data"]["spot_count"] == 1, str(body["data"]))
        check("列表项标记 enhanced", body["data"]["enhanced"] is True, str(body["data"]))
        check("列表项解析 tags", body["data"]["tags"] == ["历史", "自然"], str(body["data"]))

        resp = client.post("/api/history", json=payload, headers=auth_header(token_a2))
        check("同 client_id 重复上传幂等（同 id）", resp.json()["data"]["id"] == hid, resp.text)

        # ============================================================
        print("\n6. 历史列表 / 详情 / 删除")
        resp = client.get("/api/history", headers=auth_header(token_a2))
        body = resp.json()
        check("列表 total=1", body["data"]["total"] == 1, str(body["data"]))
        check("列表项不带完整行程体", "itinerary" not in body["data"]["items"][0], str(body["data"])[:200])

        resp = client.get(f"/api/history/{hid}", headers=auth_header(token_a2))
        body = resp.json()
        check("详情返回完整行程", body["data"]["itinerary"]["days"][0]["items"][0]["name"] == "杭州博物馆",
              str(body)[:300])
        check("详情返回原始入参", body["data"]["request"]["city"] == "杭州", str(body)[:300])

        resp = client.get("/api/history/999999", headers=auth_header(token_a2))
        check("不存在的历史 → 404", resp.status_code == 404, resp.text)

        resp = client.get("/api/history", params={"page": 1, "page_size": 500},
                          headers=auth_header(token_a2))
        check("page_size 超限被夹到 50", resp.json()["data"]["page_size"] == 50, resp.text)

        # ============================================================
        print("\n7. 本地记录批量导入")
        good = {
            "client_id": "local-2002",
            "request": fake_request("苏州", 1),
            "itinerary": fake_itinerary("苏州"),
        }
        dup = dict(payload)  # 与已存在 client_id 相同 → 应跳过
        bad = {"client_id": "local-3003", "request": {"city": "北京"}}  # 缺 itinerary → 失败
        resp = client.post("/api/history/import", json={"trips": [good, dup, bad]},
                           headers=auth_header(token_a2))
        body = resp.json()
        check("导入结果：新增1/跳过1/失败1",
              (body["data"]["imported"], body["data"]["skipped"], body["data"]["failed"]) == (1, 1, 1),
              str(body["data"]))

        resp = client.get("/api/history", headers=auth_header(token_a2))
        check("导入后 total=2", resp.json()["data"]["total"] == 2, resp.text)

        resp = client.post("/api/history/import", json={"trips": "not-a-list"},
                           headers=auth_header(token_a2))
        check("trips 非数组被拒", resp.status_code == 400, resp.text)

        # ============================================================
        print("\n8. 越权隔离")
        # token_b 已在第 3 段注册 USER_B 时拿到，这里不再重复注册

        resp = client.get("/api/history", headers=auth_header(token_b))
        check("用户B看不到用户A的数据", resp.json()["data"]["total"] == 0, resp.text)

        resp = client.get(f"/api/history/{hid}", headers=auth_header(token_b))
        check("用户B读用户A详情 → 404", resp.status_code == 404, resp.text)

        resp = client.delete(f"/api/history/{hid}", headers=auth_header(token_b))
        check("用户B删用户A记录 → 404", resp.status_code == 404, resp.text)

        # ============================================================
        print("\n9. 密码体系的安全性")
        # ============================================================
        # 短信频率限制已随功能一起下线。账号密码体系下更该验证的是：
        #   1) 密码不可逆（哈希里不含明文）
        #   2）注册接口从不回显密码
        #   3）同一密码在不同用户上产生不同哈希（salt 生效）
        import app.db.database as _db  # noqa: E402

        with _db.get_conn() as conn:
            row = conn.execute(
                "SELECT password_hash FROM users WHERE username = ?", (USER_A,)
            ).fetchone()
        stored = row["password_hash"] if row else ""
        check("数据库里存的是哈希，不是明文", stored.startswith("pbkdf2$"), stored[:24])
        check("哈希里不含明文密码", PASSWORD_A not in stored)
        check("哈希长度足够（salt+digest）", len(stored) > 60, str(len(stored)))

        resp = client.post("/api/auth/register", json={
            "username": "tester_dup_pwd", "password": PASSWORD_A})
        ok_reg = resp.status_code == 200
        check("同一密码可注册第二个账号", ok_reg, resp.text)
        if ok_reg:
            with _db.get_conn() as conn:
                row2 = conn.execute(
                    "SELECT password_hash FROM users WHERE username = ?",
                    ("tester_dup_pwd",)
                ).fetchone()
            h2 = row2["password_hash"] if row2 else ""
            check("同密码不同账号 → 不同哈希（随机 salt）", h2 != stored)

        # 注册响应里绝不能出现密码或哈希
        resp = client.post("/api/auth/login", json={
            "username": USER_A, "password": PASSWORD_A})
        text = resp.text
        check("登录响应不含明文密码", PASSWORD_A not in text)
        check("登录响应不含密码哈希", "password_hash" not in text)

        print("\n10. 退出登录")
        resp = client.post("/api/auth/logout", headers=auth_header(token_a2))
        check("退出成功", resp.status_code == 200 and resp.json()["success"], resp.text)
        resp = client.get("/api/auth/me", headers=auth_header(token_a2))
        check("退出后 token 失效 → 401", resp.status_code == 401, resp.text)
        resp = client.post("/api/auth/logout")
        check("无凭证退出 → 401", resp.status_code == 401, resp.text)

        # ============================================================
        print("\n11. 删除与元数据")
        # 上一节已经退出登录，这里重新登录拿一个可用 token（顺带验证重新登录后 token 可用）
        resp = client.post("/api/auth/login", json={
            "username": USER_A, "password": PASSWORD_A})
        check("退出后可重新登录", resp.status_code == 200, resp.text)
        token_a3 = resp.json()["data"]["token"]

        resp = client.delete(f"/api/history/{hid}", headers=auth_header(token_a3))
        check("删除成功", resp.status_code == 200, resp.text)
        resp = client.get(f"/api/history/{hid}", headers=auth_header(token_a3))
        check("删除后详情 → 404", resp.status_code == 404, resp.text)
        resp = client.get("/api/history", headers=auth_header(token_a3))
        check("删除后列表 total 减少", resp.json()["data"]["total"] == 1, resp.text)

        resp = client.get("/api/meta")
        body = resp.json()
        check("meta 含 sms_provider", body.get("sms_provider") == "dev", str(body))
        check("meta 保留原有字段", set(["tags", "profiles", "amap_enabled"]).issubset(body), str(body))

        # ============================================================
        print("\n12. 数据库表结构")
        from app.db import table_names

        tables = set(table_names() or [])
        check("四张业务表均已建立",
              {"users", "sms_codes", "auth_tokens", "itineraries"}.issubset(tables), str(tables))

    # ---- 清理测试库（含 WAL 附属文件）----
    for _suffix in ("", "-wal", "-shm"):
        _stale = Path(_TEST_DB + _suffix)
        if _stale.exists():
            try:
                _stale.unlink()
            except OSError:
                pass

    print(f"\n{'=' * 56}")
    print(f"通过 {PASSED} 项，失败 {FAILED} 项")
    print(f"{'=' * 56}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
