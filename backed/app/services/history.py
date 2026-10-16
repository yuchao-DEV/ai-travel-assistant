"""历史行程服务：落库、分页查询、详情、删除、本地记录批量导入。

边界说明：本模块**不做任何规划决策**，只把算法引擎产出的 ``ItineraryVO``
原文存下来、原样取出。选点/排序/时间/费用仍然只由 ``app/algorithms/`` 决定。
"""
import json
import logging
import sqlite3
from typing import Any, List, Optional

from app.db import get_conn, transaction, utc_now_iso
from app.errors import ApiError
from app.schemas import (
    HistoryDetailVO,
    HistoryImportVO,
    HistoryItemVO,
    HistoryListVO,
    HistorySaveDTO,
    ItineraryVO,
    PlanRequestDTO,
)

logger = logging.getLogger("app.history")

DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 50
# 单次本地导入上限，避免一次请求塞爆数据库
MAX_IMPORT_ITEMS = 100


def _spot_count(itinerary_json: str) -> tuple:
    """从行程 JSON 里算条目数与 enhanced 标记，失败返回 (0, False)。"""
    try:
        data = json.loads(itinerary_json or "{}")
    except (TypeError, ValueError):
        return 0, False
    if not isinstance(data, dict):
        return 0, False
    count = 0
    for day in data.get("days") or []:
        if isinstance(day, dict):
            count += len(day.get("items") or [])
    return count, bool(data.get("enhanced"))


def _loads_array(raw: Optional[str]) -> List[str]:
    """解析 tags JSON 数组，异常时返回空列表。"""
    try:
        value = json.loads(raw or "[]")
    except (TypeError, ValueError):
        return []
    return [str(v) for v in value] if isinstance(value, list) else []


def _row_to_item(row: sqlite3.Row) -> HistoryItemVO:
    """列表行 → 列表项 VO（不返回完整行程，省流量）。"""
    count, enhanced = _spot_count(row["itinerary_json"])
    return HistoryItemVO(
        id=row["id"],
        client_id=row["client_id"],
        title=row["title"],
        origin=row["origin"],
        city=row["city"],
        days=row["days"],
        budget=0.0,   # 已废弃，仅为兼容历史记录保留
        tags=_loads_array(row["tags"]),
        profile=row["profile"],
        total_cost=None,
        spot_count=count,
        enhanced=enhanced,
        created_at=row["created_at"],
    )


def _build_title(origin: Optional[str], city: str, days: int) -> str:
    origin = (origin or "").strip()
    return f"{origin}出发·{city}{days}日游" if origin else f"{city}{days}日游"


def _save_in_conn(conn: sqlite3.Connection, user_id: int, dto: HistorySaveDTO) -> sqlite3.Row:
    """在给定事务内写入/更新一条历史行程。"""
    req = dto.request
    itin = dto.itinerary
    client_id = (dto.client_id or "").strip() or None
    title = (dto.title or "").strip() or _build_title(dto.origin, req.city, req.days)
    now = utc_now_iso()
    created_at = (dto.created_at or "").strip() or now
    tags_json = json.dumps(list(req.tags or []), ensure_ascii=False)

    if client_id:
        existing = conn.execute(
            "SELECT id FROM itineraries WHERE user_id = ? AND client_id = ?",
            (user_id, client_id),
        ).fetchone()
        if existing is not None:
            # 同一本地记录重复上传 → 覆盖更新，保持幂等
            conn.execute(
                "UPDATE itineraries SET title=?, origin=?, city=?, days=?, budget=?, tags=?, "
                "profile=?, total_cost=?, request_json=?, itinerary_json=?, updated_at=? "
                "WHERE id = ?",
                (
                    title,
                    dto.origin,
                    req.city,
                    req.days,
                    0.0,   # 预算系统已移除，列保留 NOT NULL 故写 0
                    tags_json,
                    req.profile,
                    None,
                    req.model_dump_json(),
                    itin.model_dump_json(),
                    now,
                    existing["id"],
                ),
            )
            return conn.execute(
                "SELECT * FROM itineraries WHERE id = ?", (existing["id"],)
            ).fetchone()

    cur = conn.execute(
        "INSERT INTO itineraries (user_id, client_id, title, origin, city, days, budget, tags, "
        "profile, total_cost, request_json, itinerary_json, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            user_id,
            client_id,
            title,
            dto.origin,
            req.city,
            req.days,
            0.0,   # 预算系统已移除，列保留 NOT NULL 故写 0
            tags_json,
            req.profile,
            None,
            req.model_dump_json(),
            itin.model_dump_json(),
            created_at,
            now,
        ),
    )
    return conn.execute("SELECT * FROM itineraries WHERE id = ?", (cur.lastrowid,)).fetchone()


def save_itinerary(user_id: int, dto: HistorySaveDTO) -> HistoryItemVO:
    """保存一次已生成的行程（带 client_id 时幂等更新）。"""
    with transaction() as conn:
        row = _save_in_conn(conn, user_id, dto)
    logger.info("历史行程已保存 user_id=%s id=%s city=%s", user_id, row["id"], row["city"])
    return _row_to_item(row)


def list_itineraries(user_id: int, page: int = 1, page_size: int = DEFAULT_PAGE_SIZE) -> HistoryListVO:
    """按生成时间倒序分页列出用户的历史行程。"""
    page = max(int(page or 1), 1)
    page_size = min(max(int(page_size or DEFAULT_PAGE_SIZE), 1), MAX_PAGE_SIZE)
    offset = (page - 1) * page_size

    with get_conn() as conn:
        total = conn.execute(
            "SELECT COUNT(*) AS c FROM itineraries WHERE user_id = ?", (user_id,)
        ).fetchone()["c"]
        rows = conn.execute(
            "SELECT * FROM itineraries WHERE user_id = ? "
            "ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?",
            (user_id, page_size, offset),
        ).fetchall()

    return HistoryListVO(
        total=total,
        page=page,
        page_size=page_size,
        items=[_row_to_item(r) for r in rows],
    )


def get_itinerary(user_id: int, history_id: int) -> HistoryDetailVO:
    """取一条历史行程的完整内容（含原始入参）。"""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM itineraries WHERE id = ? AND user_id = ?", (history_id, user_id)
        ).fetchone()
    if row is None:
        raise ApiError("历史行程不存在", code=404)

    try:
        request = PlanRequestDTO.model_validate_json(row["request_json"])
        itinerary = ItineraryVO.model_validate_json(row["itinerary_json"])
    except Exception as exc:  # 数据损坏时给出明确提示，而不是 500 堆栈
        logger.error("历史行程 id=%s 数据损坏：%s", history_id, exc)
        raise ApiError("该历史行程数据已损坏，无法读取", code=500) from exc

    item = _row_to_item(row)
    return HistoryDetailVO(**item.model_dump(), request=request, itinerary=itinerary)


def delete_itinerary(user_id: int, history_id: int) -> None:
    """删除一条历史行程（只能删自己的）。"""
    with transaction() as conn:
        cur = conn.execute(
            "DELETE FROM itineraries WHERE id = ? AND user_id = ?", (history_id, user_id)
        )
        if cur.rowcount == 0:
            # 事务内抛错会回滚，但这里没有需要保留的写入，直接抛即可
            raise ApiError("历史行程不存在", code=404)
    logger.info("历史行程已删除 user_id=%s id=%s", user_id, history_id)


def import_local(user_id: int, trips: List[Any]) -> HistoryImportVO:
    """批量导入前端本地记录：逐条校验，个别非法不影响其余。"""
    if not isinstance(trips, list):
        raise ApiError("trips 必须是数组")
    if len(trips) > MAX_IMPORT_ITEMS:
        raise ApiError(f"单次最多导入 {MAX_IMPORT_ITEMS} 条记录")

    imported = 0
    skipped = 0
    failed = 0
    ids: List[int] = []

    for raw in trips:
        try:
            dto = HistorySaveDTO.model_validate(raw)
        except Exception as exc:
            failed += 1
            logger.warning("导入本地行程失败（数据不合法）：%s", exc)
            continue

        client_id = (dto.client_id or "").strip() or None
        if client_id:
            with get_conn() as conn:
                exists = conn.execute(
                    "SELECT id FROM itineraries WHERE user_id = ? AND client_id = ?",
                    (user_id, client_id),
                ).fetchone()
            if exists is not None:
                skipped += 1
                continue

        try:
            item = save_itinerary(user_id, dto)
        except Exception as exc:
            failed += 1
            logger.warning("导入本地行程失败：%s", exc)
            continue
        ids.append(item.id)
        imported += 1

    logger.info(
        "本地记录导入完成 user_id=%s 新增=%s 跳过=%s 失败=%s", user_id, imported, skipped, failed
    )
    return HistoryImportVO(imported=imported, skipped=skipped, failed=failed, ids=ids)
