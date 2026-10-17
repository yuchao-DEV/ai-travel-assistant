/**
 * 表单状态保持（sessionStorage）。
 *
 * ## 为什么需要
 *
 * UX 规范 [中] State｜Preserve Screen State：返回上一屏时应恢复滚动与表单内容。
 *
 * 我们的实际场景：用户在首页填好城市/天数/预算/兴趣/偏好 → 点「开始规划」跳到
 * 时间线 → 看完点「重新规划」返回首页 → **表单全空了**，得重填一遍。
 * 这是很具体的重复劳动，不是抽象的规范条目。
 *
 * ## 为什么用 sessionStorage 而不是 localStorage
 *
 * 表单草稿是**一次会话内**的临时状态：
 * - 用 localStorage 会让用户第二天打开还看到上次没提交的草稿，反而困惑
 * - sessionStorage 随标签页关闭清除，符合"草稿"语义
 *
 * ## 只存表单，不存解析结果与生成结果
 *
 * 解析回执（parseResult）与行程数据体积大且易过期，不持久化——
 * 恢复一个过期的行程比空表单更糟。
 */
import { watch } from 'vue'

const PREFIX = 'nl-trip-form:'

export function saveForm(key, value) {
  try {
    sessionStorage.setItem(PREFIX + key, JSON.stringify(value))
  } catch {
    // 隐私模式/配额满时 sessionStorage 会抛异常：静默降级即可，
    // 表单草稿丢了不影响主流程
  }
}

export function loadForm(key) {
  try {
    const raw = sessionStorage.getItem(PREFIX + key)
    if (!raw) return null
    const parsed = JSON.parse(raw)
    return parsed && typeof parsed === 'object' ? parsed : null
  } catch {
    return null
  }
}

export function clearForm(key) {
  try {
    sessionStorage.removeItem(PREFIX + key)
  } catch {
    // 同上
  }
}

/**
 * 把一个 reactive 表单绑定到 sessionStorage。
 *
 * @param {string} key          存储键（移动端/桌面端用不同键，互不干扰）
 * @param {object} form         reactive 对象
 * @param {string[]} fields     只持久化这些字段（白名单，避免把临时状态存进去）
 * @returns {{ restore: Function, stop: Function }}
 *          restore() 恢复并开始自动保存；stop() 停止监听
 */
export function bindForm(key, form, fields) {
  let unwatch = null

  function restore() {
    const saved = loadForm(key)
    if (saved) {
      for (const field of fields) {
        if (field in saved && saved[field] !== undefined && saved[field] !== null) {
          form[field] = saved[field]
        }
      }
    }
    return saved !== null
  }

  // 用 watch 的 deep + 白名单：监听整个 form，变化时只存指定字段，
  // 比逐个字段 watch 好维护
  unwatch = watch(
    () => fields.map((f) => form[f]),
    () => {
      const snapshot = {}
      for (const field of fields) snapshot[field] = form[field]
      saveForm(key, snapshot)
    },
    { deep: true },
  )

  return {
    restore,
    stop: () => (unwatch ? unwatch() : null),
  }
}
