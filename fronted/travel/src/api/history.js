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

/** 删除历史行程 */
export function deleteHistory(historyId) {
  return request.delete(`/api/history/${historyId}`)
}

/** 批量导入本地记录（按 client_id 幂等去重） */
export function importHistory(trips) {
  return request.post('/api/history/import', { trips })
}
