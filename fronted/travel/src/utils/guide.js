/**
 * 新手引导的「看过没有」状态。
 *
 * ## 为什么不用一个布尔值
 *
 * 引导内容会变（加了新功能、改了文案），如果只存 `true/false`，
 * 老用户永远看不到新版引导。这里带版本号：版本提升时全部重置，
 * 用户会重新看一次——但**同一版本内只出现一次**，不打扰。
 *
 * ## 为什么不用后端
 *
 * 引导是「这台设备的首次体验」，不是账号属性。同一账号换设备
 * 应该再讲一次；而且未登录也用得上，放后端没有必要。
 */
const STORAGE_KEY = 'app:guide'
const VERSION = 1

function readState() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return { version: VERSION, seen: {} }
    const parsed = JSON.parse(raw)
    if (!parsed || parsed.version !== VERSION) return { version: VERSION, seen: {} }
    return { version: VERSION, seen: parsed.seen || {} }
  } catch {
    // 隐私模式 / 存储被禁用：当作没看过，但不写入（写会抛错）
    return { version: VERSION, seen: {} }
  }
}

function writeState(state) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(state))
    return true
  } catch {
    return false
  }
}

/** 是否已经看过某个引导（id 如 'home-notice' / 'pc-tour'） */
export function hasSeenGuide(id) {
  return !!readState().seen[id]
}

/** 标记已看过 */
export function markGuideSeen(id) {
  const state = readState()
  state.seen[id] = true
  writeState(state)
}

/** 重置全部引导（「重新看一遍引导」用） */
export function resetGuides() {
  writeState({ version: VERSION, seen: {} })
}

export const GUIDE_IDS = {
  homeNotice: 'home-notice',
  pcTour: 'pc-tour',
}
