/**
 * 助手行程的本地暂存：AgentChat 写入，Timeline 读取。
 *
 * 完整 ItineraryVO 有几十 KB，不能塞进 URL query；用 sessionStorage 在同标签页
 * 内传递，刷新也在。取不到时调用方回落到"重新规划"，不会白屏。
 */
const KEY_PREFIX = 'agent_trip:'

export function stashAgentTrip(itinerary) {
  if (!itinerary) return ''
  const token = `t${Date.now()}${Math.random().toString(36).slice(2, 8)}`
  try {
    sessionStorage.setItem(KEY_PREFIX + token, JSON.stringify(itinerary))
    return token
  } catch (e) {
    // 隐私模式/配额满：静默失败，时间线会走重新规划
    console.warn('助手行程暂存失败，将回退为重新规划', e)
    return ''
  }
}

export function readAgentTrip(token) {
  if (!token) return null
  try {
    const raw = sessionStorage.getItem(KEY_PREFIX + token)
    return raw ? JSON.parse(raw) : null
  } catch (e) {
    return null
  }
}

export function clearAgentTrip(token) {
  if (!token) return
  try {
    sessionStorage.removeItem(KEY_PREFIX + token)
  } catch (e) { /* 忽略 */ }
}
