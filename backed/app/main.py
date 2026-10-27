"""FastAPI 应用入口：用户系统 + 旧版 LLM 直调 + 新版算法引擎路由。"""
import logging
from contextlib import asynccontextmanager
from typing import Any, Optional

from fastapi import Depends, FastAPI, Query
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response, StreamingResponse

from app.config import (
    DB_PATH,
    MAX_TRIP_DAYS,
    MIN_TRIP_DAYS,
    RAG_ENABLED,
    SMS_DEBUG_ECHO,
    SMS_PROVIDER,
    amap_enabled,
)
from app.db import init_db
from app.rag import bootstrap as rag_bootstrap
from app.rag.retriever import hybrid_search
from app.deps import current_token, current_user_optional, current_user_required
from app.errors import ApiError
from app.services import vision as vision_service
from app.schemas import (
    HistoryImportDTO,
    HistorySaveDTO,
    LoginDTO,
    AgentChatRequestDTO,
    ImportTextDTO,
    KbUploadDTO,
    ParseRequestDTO,
    PlanRequestDTO,
    RegisterDTO,
    Result,
    VALID_PROFILES,
    VALID_TAGS,
)
from app.services import agent_session as agent_session_service
from app.services import auth as auth_service
from app.services import city_registry
from app.services import history as history_service
from app.services import kb as kb_service
from app.services.agent_chat import agent_chat_stream
from app.services import importer as importer_service
from app.services import inspiration as inspiration_service
from app.services import intercity as intercity_service
from app.services import mapimage as mapimage_service
from app.services import nl_parse as nl_parse_service
from app.services.plan import plan as plan_service
# 注意：旧版纯 LLM 推荐（app.services.travel）的路由已下线，此处不再导入。
# 代码保留在 app/services/travel.py 作为「纯 LLM → 算法引擎」架构演进的对照。

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger("app.main")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """启动时初始化 SQLite（建库建表，幂等）。"""
    init_db()
    logger.info("SQLite 就绪：%s", DB_PATH)
    logger.info("短信通道：%s（回显验证码=%s）", SMS_PROVIDER, SMS_DEBUG_ECHO)
    # 知识库增量索引：**丢到后台**，不阻塞启动。
    # 索引要读几十个文件、切分、写库，放在启动路径上会明显拖慢冷启动；
    # 而且它靠 content_hash 做增量，重启时内容没变就会整篇跳过（日志里的 skipped N）。
    rag_bootstrap.schedule()
    yield


app = FastAPI(title="AI Travel Assistant", version="2.1.0", lifespan=lifespan)

# 开发环境放开跨域（前端已走 Vite 代理，这里加一层保险）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(_request, exc: RequestValidationError):
    """参数校验失败时返回与 Java 版一致的结构：{ success:false, code:400, message }。"""
    errors = exc.errors()
    message = errors[0]["msg"] if errors else "参数校验失败"
    return JSONResponse(
        status_code=400,
        content={"success": False, "code": 400, "message": message, "data": None},
    )


@app.exception_handler(ApiError)
async def api_error_handler(_request, exc: ApiError):
    """业务异常统一包装，前端只需看 success/code/message。"""
    return JSONResponse(
        status_code=exc.status_code,
        content={"success": False, "code": exc.code, "message": exc.message, "data": None},
    )


# ==================================================================
# 用户系统路由 /api/auth/*
# ==================================================================

@app.post("/api/auth/register", response_model=Result)
def register_api(dto: RegisterDTO):
    """账号密码注册（成功后直接返回登录态）。"""
    data = auth_service.register(dto)
    return Result(success=True, code=200, data=data.model_dump())


@app.post("/api/auth/login", response_model=Result)
def login_api(dto: LoginDTO):
    """账号密码登录。"""
    data = auth_service.login(dto)
    return Result(success=True, code=200, data=data.model_dump())


@app.get("/api/auth/me", response_model=Result)
def me_api(user: Optional[Any] = Depends(current_user_required)):
    """取当前登录用户信息。"""
    return Result(success=True, code=200, data=auth_service.user_to_vo(user).model_dump())


@app.post("/api/auth/logout", response_model=Result)
def logout_api(token: Optional[str] = Depends(current_token)):
    """退出登录：删除服务端 token（幂等）。"""
    if not token:
        raise ApiError("未携带登录凭证", code=401)
    auth_service.logout(token)
    return Result(success=True, code=200, data={"logged_out": True})


# ==================================================================
# 历史行程路由 /api/history/*
# ==================================================================

@app.get("/api/history", response_model=Result)
def history_list_api(
    page: int = 1,
    page_size: int = history_service.DEFAULT_PAGE_SIZE,
    user: Optional[Any] = Depends(current_user_required),
):
    """分页列出当前用户的历史行程（不含完整行程体）。"""
    data = history_service.list_itineraries(user["id"], page, page_size)
    return Result(success=True, code=200, data=data.model_dump())


@app.post("/api/history", response_model=Result)
def history_save_api(
    dto: HistorySaveDTO,
    user: Optional[Any] = Depends(current_user_required),
):
    """把一次已生成的行程存为历史记录（带 client_id 时幂等）。

    入参是算法引擎的原文，后端不做二次加工。
    """
    data = history_service.save_itinerary(user["id"], dto)
    return Result(success=True, code=200, data=data.model_dump())


@app.put("/api/history/{history_id}", response_model=Result)
def history_update_api(
    history_id: int,
    dto: HistorySaveDTO,
    user: Optional[Any] = Depends(current_user_required),
):
    """**覆盖更新**一条已有历史行程（按 id，而不是按 client_id）。

    为什么需要它：用户在「历史回看」里从灵感加地点，期望的是"改这条行程"，
    而不是"再多一条"。旧实现只能靠 `client_id` 做幂等更新，而迁移过来的
    老记录 `client_id` 为 NULL，永远匹配不上 → 每次都新增一条。
    """
    data = history_service.update_itinerary(user["id"], history_id, dto)
    return Result(success=True, code=200, data=data.model_dump())


@app.post("/api/history/import", response_model=Result)
def history_import_api(
    dto: HistoryImportDTO,
    user: Optional[Any] = Depends(current_user_required),
):
    """批量导入前端 localStorage 里的旧记录（逐条校验，幂等跳过）。"""
    data = history_service.import_local(user["id"], dto.trips)
    return Result(success=True, code=200, data=data.model_dump())


@app.get("/api/history/{history_id}", response_model=Result)
def history_detail_api(
    history_id: int,
    user: Optional[Any] = Depends(current_user_required),
):
    """取一条历史行程的完整内容（含当时的规划入参）。"""
    data = history_service.get_itinerary(user["id"], history_id)
    return Result(success=True, code=200, data=data.model_dump())


@app.delete("/api/history/{history_id}", response_model=Result)
def history_delete_api(
    history_id: int,
    user: Optional[Any] = Depends(current_user_required),
):
    """删除一条历史行程。"""
    history_service.delete_itinerary(user["id"], history_id)
    return Result(success=True, code=200, data={"deleted": history_id})


# ==================================================================
# 已下线路由
# ==================================================================
# POST /api/travel/recommend（旧版纯 LLM 推荐）已**下线**。
#
# 下线原因：该端点把「每天上午/下午/晚上去哪个景点、门票多少、时长多久」
# 整份交给大模型直接生成，产出无法被校验——实体校验需要一个基准名单
# （算法引擎产出的真实行程），而这里的 LLM 输出本身就是行程，没有 ground truth。
# 它与本项目「算法决策、LLM 只负责表达」的核心架构直接冲突，
# 且前端唯一的调用方 views/Detail.vue 从未挂载路由，下线对功能零影响。
#
# 代码保留在 app/services/travel.py 与 app/schemas.py(TripBaseInfoDTO/RecommendVO)，
# 作为「纯 LLM → 算法引擎」演进的对照，不再对外暴露。


# ==================================================================
# 图片地标识别
# ==================================================================
#
# multipart 解析需要 python-multipart。FastAPI 在**注册路由时**就会检查它
# （因为签名里有 File），所以不能只 try 住 import —— 必须在注册前判断，
# 缺依赖时干脆不注册这个路由，让其余 API 照常可用。
#
# 缺依赖时的行为：/api/vision/recognize 返回 404，前端按"不可用"降级。
try:  # pragma: no cover - 取决于环境
    import multipart as _multipart  # noqa: F401
    from fastapi import File, UploadFile  # noqa: F401
    _UPLOAD_OK = True
except Exception:  # pragma: no cover
    _UPLOAD_OK = False


if _UPLOAD_OK:

    @app.post("/api/vision/recognize")
    async def vision_recognize_api(file: UploadFile = File(...)):
        """上传一张照片，识别其中的地标。

        返回 ``recognized`` 表示**是否认出了经高德核实的地点**——
        模型自己说的话不算数，查不到就如实说查不到。
        """
        # 先按上限读，超了直接拒，不把大文件整个读进内存
        limit = vision_service.VISION_MAX_BYTES
        data = await file.read(limit + 1)
        if len(data) > limit:
            raise ApiError(
                f"图片超过 {limit // 1024 // 1024} MB，请压缩后重试。", code=413)
        if not data:
            raise ApiError("没有读到图片内容，请重新选择文件。", code=400)
        result = await vision_service.recognize(data, file.content_type)
        return {"success": True, "code": 200, "data": result}


@app.post("/api/plan", response_model=Result)
async def plan_api(dto: PlanRequestDTO):
    """算法引擎版行程规划：高德POI → 算法选点排序 → LLM 润色。

    本接口保持无状态：即使用户已登录也不会自动落库，
    历史行程由前端在拿到结果后显式调用 ``POST /api/history`` 保存，
    以免同一行程被重复渲染时反复写库。
    """
    data = await plan_service(dto)
    return Result(success=True, code=200, data=data.model_dump(exclude_none=False))


@app.post("/api/parse", response_model=Result)
async def parse_api(dto: ParseRequestDTO):
    """自然语言需求解析：把一句话变成结构化规划参数（不产生行程）。

    对接「输入一句话 → 表单自动填好 → 用户核对后提交」的交互。
    本接口**只做抽取**，不选点、不定顺序、不算费用；
    真正的规划仍然由 ``POST /api/plan`` 的算法引擎完成。
    """
    data = await nl_parse_service.parse_request(dto)
    return Result(success=True, code=200, data=data.model_dump())


@app.get("/api/debug/plan")
async def debug_plan(
    city: str = "杭州",
    days: int = 3,
    tags: str = "历史,自然,美食",
    profile: str = "经典",
):
    """调试路由：GET 方便浏览器直接访问，打印 ItineraryVO 完整结构。

    用法: /api/debug/plan?city=杭州&days=3&tags=历史,自然,美食&profile=经典
    """
    tag_list = [t.strip() for t in tags.split(",") if t.strip()]
    dto = PlanRequestDTO(
        city=city,
        days=days,
        tags=tag_list,
        must_go=[],
        avoid=[],
        profile=profile if profile in VALID_PROFILES else "经典",
    )
    data = await plan_service(dto)
    return {
        "success": True,
        "amap_enabled": amap_enabled(),
        "request": dto.model_dump(),
        "itinerary": data.model_dump(),
    }


@app.post("/api/import/text")
async def import_text_api(payload: ImportTextDTO):
    """导入线路/地点：从一段文字里抽出地点，**逐个经高德反查验证**。

    - 匹配到的地点可直接加入 `must_go`（算法里的硬约束）；
    - 匹配不到的**明确列出并说明原因**，绝不静默丢弃——
      导入功能最常见的坑就是"显示导入成功、实际一半没匹配上"。
    """
    data = await importer_service.import_text(
        payload.text, payload.city, force_city=getattr(payload, "force_city", "") or None)
    return {"success": True, "code": 200, "data": data}


# ==================================================================
# 攻略图 → 行程导入
# ==================================================================
#
# 与 /api/import/text 是**同一条管线**，只是输入来源不同：
#   图片 →（视觉模型）→ 城市 + 地点名 →（importer.import_text）→ 高德逐个校验
# 这样两个入口对同一批地点给出的"匹配/未匹配"结论完全一致。
if _UPLOAD_OK:

    @app.post("/api/import/image")
    async def import_image_api(
        file: UploadFile = File(...),
        city: str = Query(default="", description="用户已选的目的地（仅作候选，不压过图上城市）"),
        force_city: str = Query(default="", description="用户明确指定：仍在某个城市核对"),
    ):
        """从攻略图里提取地点并导入（复用文字导入的校验管线）。"""
        limit = vision_service.VISION_MAX_BYTES
        data = await file.read(limit + 1)
        if len(data) > limit:
            raise ApiError(
                f"图片超过 {limit // 1024 // 1024} MB，请压缩后重试。", code=413)
        if not data:
            raise ApiError("没有读到图片内容，请重新选择文件。", code=400)

        guide = await vision_service.extract_guide(data, file.content_type)
        if not guide["ok"]:
            # 提取失败如实返回，**不用空结果冒充成功**
            return {
                "success": True,
                "code": 200,
                "data": {
                    "ok": False,
                    "reason": guide["reason"],
                    "city": guide.get("city") or (city or None),
                    "places": [],
                    "matched_count": 0,
                    "unmatched_count": 0,
                    "notes": [],
                },
            }

        # 拼成一段文字交给现有管线：城市一行 + 地点逐行
        # （extract_places 对"地点列表"这种形态识别最稳）
        #
        # **只放图上识别出的城市**，不要拿表单城市兜底：
        # 旧代码写 `guide["city"] or city`，表单城市会被塞进正文，
        # 于是 extract_places 必然回读出表单城市，"内容城市"永远等于"表单城市"，
        # 冲突检测形同虚设（用户传北京图、表单选南京，也检不出来）。
        lines = [guide["city"] or ""] + guide["places"]
        text = "\n".join(x for x in lines if x)
        # city_hint 只传表单城市；城市优先级由 importer.import_text 统一判定
        data_out = await importer_service.import_text(
            text, city or None, force_city=force_city or None)

        data_out["ok"] = True
        data_out["reason"] = ""
        data_out["from_image"] = True
        data_out["image_places"] = guide["places"]
        if guide.get("notes"):
            data_out["notes"] = list(data_out.get("notes") or []) + [guide["notes"]]
        return {"success": True, "code": 200, "data": data_out}


@app.get("/api/inspiration")
async def inspiration_api(
    city: str = Query(..., description="目的地城市"),
    limit: int = Query(120, ge=1, le=200,
                       description="返回卡片数上限；需足够大以覆盖各分类"),
):
    """旅行灵感流：目的地的真实地点卡片。

    **刻意不调用大模型、不做路线计算**——它要能在行程生成的 20–40 秒空窗期里
    立刻返回内容。只输出高德真实数据（照片/评分/人均消费/商圈/营业时间），
    不含任何新闻式文案。
    """
    data = await inspiration_service.build(city, limit)
    return {"success": True, "code": 200, "data": data}


@app.get("/api/intercity")
async def intercity_api(
    origin: str = Query(..., description="出发地，如「当涂」"),
    dest: str = Query(..., description="目的地，如「杭州」"),
):
    """城际交通估算：真实地理编码 + 直线距离 + 驾车耗时估算。

    班次数据（铁路/航班）未接入——那需要 12306 或 OTA 授权。
    本接口**不伪造车次与时刻**，只给出能力边界内的真实距离与估算耗时。
    """
    data = await intercity_service.intercity(origin, dest)
    return {"success": True, "code": 200, "data": data}


@app.get("/api/map/thumbnail")
async def map_thumbnail_api(
    points: str = Query(..., description="点位串：lat,lng;lat,lng;..."),
    day_sizes: str = Query("", description="每天点位数，逗号分隔，如 5,5,3；为空视为一天"),
    width: int = Query(750, ge=200, le=1024),
    height: int = Query(420, ge=120, le=1024),
):
    """行程路线缩略图（高德静态地图，后端代理，Key 不外泄）。

    返回 PNG；高德不可用时返回 204，前端隐藏缩略图即可。
    """
    groups = mapimage_service.parse_point_groups(points, day_sizes)
    image = await mapimage_service.route_thumbnail(groups, width, height)
    if image is None:
        return Response(status_code=204)
    return Response(
        content=image,
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=3600"},
    )


# ==================================================================
# Agent 会话（/api/agent/*）
# ==================================================================

@app.post("/api/agent/chat")
async def agent_chat_api(
    dto: AgentChatRequestDTO,
    user: Optional[Any] = Depends(current_user_optional),
):
    """Agent 主入口：SSE 事件流。匿名可用，登录则启用长期记忆。

    事件类型见 ``app/agent/events.py``；前端按 ``type`` 分发渲染。
    """
    return StreamingResponse(
        agent_chat_stream(dto, user_id=user["id"] if user else None,
                          session_id=dto.session_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/agent/tools")
async def agent_tools_api():
    """列出 Agent 可用工具（调试用）。"""
    from app.agent import registry
    import app.agent.tools  # noqa: F401  导入即注册
    tools = registry.all_tools()
    return {"success": True, "code": 200, "data": {
        "total": len(tools),
        "core": sorted(registry.core_names()),
        "tools": [
            {"name": name, "description": spec.description,
             "core": spec.core, "requires_user": spec.requires_user,
             "timeout": spec.timeout}
            for name, spec in sorted(tools.items())
        ],
    }}


# ==================================================================
# 知识库（/api/kb/*）
# ==================================================================

@app.get("/api/kb/stats")
async def kb_stats_api():
    """知识库总览：文档/块/向量数、FTS 是否一致、语料文件盘点。

    ``vector_ready=false`` 是**正常降级**（未配置 Embedding 服务），
    此时检索走纯 BM25，不是故障。
    """
    return {"success": True, "code": 200, "data": kb_service.stats()}


@app.get("/api/kb/files")
async def kb_files_api():
    """列出 ``knowledge/`` 目录下的语料文件及其元数据。"""
    files = kb_service.list_knowledge_files()
    return {"success": True, "code": 200,
            "data": {"total": len(files), "files": files}}


@app.post("/api/kb/reindex")
async def kb_reindex_api(
    city: Optional[str] = Query(None, description="只重建该城市语料；留空重建全部"),
    with_vectors: bool = Query(True, description="是否同时计算向量"),
):
    """重建知识库索引（幂等：内容未变的文档会被跳过）。"""
    stat = await kb_service.reindex(city, with_vectors=with_vectors)
    return {"success": True, "code": 200, "data": stat}


@app.post("/api/kb/upload")
async def kb_upload_api(dto: KbUploadDTO):
    """上传一篇文档并索引（运行时补充语料，不必改代码）。"""
    data = await kb_service.upload_document(
        dto.title, dto.text, city=dto.city, kind=dto.kind, category=dto.category)
    return {"success": True, "code": 200, "data": data}


@app.get("/api/kb/search")
async def kb_search_api(
    q: str = Query(..., description="查询词"),
    city: Optional[str] = Query(None, description="限定城市（会同时检索通用/规则语料）"),
    top_k: int = Query(5, ge=1, le=20),
):
    """知识库检索（混合检索：BM25 + 向量 + RRF）。供调试与验收用。"""
    return {"success": True, "code": 200, "data": await hybrid_search(q, city, top_k)}


@app.get("/api/agent/sessions")
async def agent_sessions_api(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=50),
    user: Optional[Any] = Depends(current_user_optional),
):
    """列出会话（只列自己的；匿名只列匿名会话）。"""
    data = agent_session_service.list_sessions(
        user["id"] if user else None, page=page, page_size=page_size)
    return {"success": True, "code": 200, "data": data}


@app.get("/api/agent/session/{session_id}")
async def agent_session_detail_api(
    session_id: str,
    user: Optional[Any] = Depends(current_user_optional),
):
    """会话详情（含消息与工具调用时间线）。

    查不到一律 404，**不区分"不存在"与"无权访问"**——否则等于告诉调用方
    "这个 id 确实存在，只是不属于你"。
    """
    data = agent_session_service.get(session_id, user["id"] if user else None)
    if not data:
        raise ApiError("会话不存在", code=404)
    return {"success": True, "code": 200, "data": data}


@app.delete("/api/agent/session/{session_id}")
async def agent_session_delete_api(
    session_id: str,
    user: Optional[Any] = Depends(current_user_optional),
):
    """删除会话（连同消息与工具调用记录）。"""
    ok = agent_session_service.delete(session_id, user["id"] if user else None)
    if not ok:
        raise ApiError("会话不存在", code=404)
    return {"success": True, "code": 200, "data": {"deleted": True}}


@app.get("/api/agent/profile")
async def agent_profile_api(user: Optional[Any] = Depends(current_user_required)):
    """读取长期偏好画像。**画像只作建议**，与本次请求冲突时以本次为准。"""
    from app.agent.memory import load_profile
    profile = load_profile(user["id"])
    return {"success": True, "code": 200, "data": {
        "has_profile": bool(profile),
        "profile": profile,
        "note": "这是历史推断，仅供参考；与用户本次明确要求冲突时以本次为准",
    }}


@app.delete("/api/agent/profile")
async def agent_profile_clear_api(user: Optional[Any] = Depends(current_user_required)):
    """删除长期偏好画像（隐私）。删除后画像不会再出现在提示词里。"""
    from app.agent.memory import clear_profile
    deleted = clear_profile(user["id"])
    return {"success": True, "code": 200, "data": {"deleted": deleted}}


@app.get("/api/meta")
async def meta_api():
    """元数据接口：返回可用的 tag / profile / **城市清单**，供前端表单渲染。

    城市清单由后端统一给出（原先前端把 18 个城市硬编码在两个文件里，
    与后端实际能力不一致）。前端不应再自己维护一份。

    另外返回短信通道与数据库状态，方便前端在开发模式下给出提示。
    """
    cities = city_registry.list_cities()
    return {
        "success": True,
        "tags": VALID_TAGS,
        "profiles": VALID_PROFILES,
        # 天数上限由后端给出单一来源，前端不再自己写死一个 7
        "min_days": MIN_TRIP_DAYS,
        "max_days": MAX_TRIP_DAYS,
        # 城市清单：hot 用于快捷入口，all 是完整可选列表。
        # 注意"可选"不等于"有语料"——没有语料的城市照样能规划（POI 来自高德）。
        "cities": cities["all"],
        "hot_cities": cities["hot"],
        "city_stats": {
            "total": cities["total"],
            "hot": len(cities["hot"]),
            "cached": cities["cached"],
            "with_corpus": cities["with_corpus"],
        },
        "amap_enabled": amap_enabled(),
        "rag_enabled": RAG_ENABLED,
        "sms_provider": SMS_PROVIDER,
        "sms_debug_echo": SMS_DEBUG_ECHO,
    }
