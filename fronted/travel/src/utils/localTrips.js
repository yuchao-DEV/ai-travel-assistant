/**
 * 本机历史线路存储（localStorage）。
 *
 * 为什么要保留本机存储：
 * - 未登录用户也能看到自己的历史线路；
 * - 已登录用户的行程会同时写后端，本机这份用于离线查看与断网兜底。
 *
 * 记录形态分两种：
 * - 轻量记录：只有 id/title/city/days/date，提交表单时先落一条（点击可重新规划）；
 * - 完整记录：额外带 request + itinerary，规划成功后由 Timeline 回填，
 *   登录后会被自动上传到后端并从本机移除（见 useAuth 的迁移逻辑）。
 */
const TRIPS_KEY = 'my_trips'
const MAX_LOCAL_TRIPS = 10

/** 读本机线路（容错：JSON 损坏时返回空数组） */
export function loadLocalTrips() {
  try {
    const raw = localStorage.getItem(TRIPS_KEY)
    if (!raw) return []
    const parsed = JSON.parse(raw)
    return Array.isArray(parsed) ? parsed : []
  } catch (e) {
    console.warn('读取本机历史线路失败', e)
    return []
  }
}

function persist(trips) {
  try {
    localStorage.setItem(TRIPS_KEY, JSON.stringify(trips.slice(0, MAX_LOCAL_TRIPS)))
  } catch (e) {
    console.warn('写入本机历史线路失败', e)
  }
  return trips.slice(0, MAX_LOCAL_TRIPS)
}

/** 新增或按 id 覆盖一条本机线路，返回最新列表 */
export function saveLocalTrip(trip) {
  if (!trip || !trip.id) return loadLocalTrips()
  const rest = loadLocalTrips().filter((t) => t.id !== trip.id)
  const merged = [trip, ...rest]
  persist(merged)
  return merged.slice(0, MAX_LOCAL_TRIPS)
}

/** 删除若干条本机线路（按 id），返回最新列表 */
export function removeLocalTrips(ids) {
  const idSet = new Set(ids || [])
  const merged = loadLocalTrips().filter((t) => !idSet.has(t.id))
  persist(merged)
  return merged.slice(0, MAX_LOCAL_TRIPS)
}

/**
 * 挑出可以上传到后端的本机记录。
 * 只有同时带 request 与 itinerary 的记录才是"可导入"的完整记录
 * （后端 HistorySaveDTO 需要这两个字段）。
 */
export function pickImportableTrips() {
  return loadLocalTrips().filter(
    (t) => t && t.id && t.request && t.itinerary && t.request.city && t.itinerary.days
  )
}
