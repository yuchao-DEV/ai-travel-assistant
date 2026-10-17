/**
 * 登录凭证的本地存储。
 *
 * 单独成模块是为了打断循环依赖：
 * request.js 需要读 token，useAuth.js 需要写 token，
 * 若 request.js 直接依赖 useAuth.js 就会形成 request → useAuth → api → request 的环。
 */
const TOKEN_KEY = 'travel_token'
const USER_KEY = 'travel_user'

// 内存缓存 + localStorage 持久化：刷新页面后仍保持登录
let memoryToken = ''

try {
  memoryToken = localStorage.getItem(TOKEN_KEY) || ''
} catch (e) {
  console.warn('读取登录凭证失败', e)
}

export function getToken() {
  return memoryToken
}

export function setToken(value) {
  memoryToken = value || ''
  try {
    if (memoryToken) localStorage.setItem(TOKEN_KEY, memoryToken)
    else localStorage.removeItem(TOKEN_KEY)
  } catch (e) {
    console.warn('写入登录凭证失败', e)
  }
}

/** 读取缓存的用户信息（仅用于首屏渲染，真实身份仍以后端 /api/auth/me 为准） */
export function getStoredUser() {
  try {
    const raw = localStorage.getItem(USER_KEY)
    return raw ? JSON.parse(raw) : null
  } catch (e) {
    console.warn('读取用户缓存失败', e)
    return null
  }
}

export function setStoredUser(user) {
  try {
    if (user) localStorage.setItem(USER_KEY, JSON.stringify(user))
    else localStorage.removeItem(USER_KEY)
  } catch (e) {
    console.warn('写入用户缓存失败', e)
  }
}

export function clearAuthStorage() {
  setToken('')
  setStoredUser(null)
}
