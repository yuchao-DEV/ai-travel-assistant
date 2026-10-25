import request from '@/utils/request'

/**
 * 分页拉取历史行程（列表项不含完整行程体）
 * @param {number} page 页码，从 1 开始
 * @param {number} pageSize 每页条数，后端上限 50
 */
export function getHistoryList(page = 1, pageSize = 20) {
  return request.get('/api/history', { params: { page, page_size: pageSize } })
}

/** 取一条历史行程的完整内容（含当时的规划入参） */
export function getHistoryDetail(historyId) {
  return request.get(`/api/history/${historyId}`)
}

/**
 * 保存历史行程
 * @param {object} payload { client_id, title, origin, request, itinerary, created_at }
 */
export function saveHistory(payload) {
  return request.post('/api/history', payload)
}

/**
 * **覆盖更新**一条已有历史行程（按 id）。
 *
 * 与 saveHistory 的区别：saveHistory 靠 client_id 幂等，而迁移过来的老记录
 * client_id 为 NULL，永远匹配不上 → 每次都新增一条。用户在「历史回看」里
 * 加地点时期望的是"改这条"，所以要用这个。
 */
export function updateHistory(historyId, payload) {
  return request.put(`/api/history/${historyId}`, payload)
}

/** 删除历史行程 */
export function deleteHistory(historyId) {
  return request.delete(`/api/history/${historyId}`)
}

/** 批量导入本地记录（按 client_id 幂等去重） */
export function importHistory(trips) {
  return request.post('/api/history/import', { trips })
}
