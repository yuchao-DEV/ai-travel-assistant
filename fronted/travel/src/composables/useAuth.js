/**
 * 登录态管理（模块级单例）。
 *
 * 项目未引入 Pinia，这里用一个模块级 ref 充当全局 store：
 * 任何组件调用 useAuth() 拿到的都是同一份状态。
 */
import { computed, ref } from 'vue'

import { getMe, login as apiLogin, logout as apiLogout, register as apiRegister } from '@/api/auth'
import { importHistory } from '@/api/history'
import { clearAuthStorage, getStoredUser, getToken, setStoredUser, setToken } from '@/utils/token'
import { pickImportableTrips, removeLocalTrips } from '@/utils/localTrips'

const token = ref(getToken())
const user = ref(getStoredUser())
// 是否已完成一次服务端身份校验（首屏用它决定要不要显示骨架/加载态）
const initialized = ref(false)

const isLoggedIn = computed(() => Boolean(token.value && user.value))

/** 写入会话（登录/注册成功后调用），并把本机历史记录迁移到后端 */
function applySession(data) {
  token.value = data.token
  user.value = data.user
  setToken(data.token)
  setStoredUser(data.user)
  initialized.value = true
  // 不阻塞登录流程，失败也不影响登录结果
  syncLocalTrips()
}

function clearSession() {
  token.value = ''
  user.value = null
  clearAuthStorage()
}

/**
 * 把本机"完整记录"上传到后端并删除本地副本（后端按 client_id 幂等）。
 * 轻量的元数据记录无法上传（缺少行程体），继续留在本机。
 */
async function syncLocalTrips() {
  const pending = pickImportableTrips()
  if (!pending.length) return { imported: 0, skipped: 0, failed: 0 }
  try {
    const res = await importHistory(
      pending.map((t) => ({
        client_id: t.id,
        title: t.title,
        origin: t.origin,
        request: t.request,
        itinerary: t.itinerary,
        created_at: t.createdAt ? new Date(t.createdAt).toISOString() : undefined,
      }))
    )
    const data = res?.data || {}
    if (data.failed > 0) {
      // 有记录被后端判为不合法，无法确定是哪几条 → 本机全部保留，下次再试
      return data
    }
    // 新增或"后端已存在"，两种情况本机副本都可以清掉
    removeLocalTrips(pending.map((t) => t.id))
    return data
  } catch (e) {
    console.warn('本机历史记录迁移失败，稍后会重试', e)
    return { imported: 0, skipped: 0, failed: pending.length }
  }
}

/** 注册（账号 + 密码） */
async function register({ username, password, nickname }) {
  const res = await apiRegister({ username, password, nickname })
  applySession(res.data)
  return res.data
}

/** 登录（账号 + 密码） */
async function login({ username, password }) {
  const res = await apiLogin({ username, password })
  applySession(res.data)
  return res.data
}

/** 用服务端数据刷新本地用户信息；token 失效则清空会话 */
async function refreshUser() {
  if (!token.value) {
    initialized.value = true
    return null
  }
  try {
    const res = await getMe()
    user.value = res.data
    setStoredUser(res.data)
    return res.data
  } catch (e) {
    if (e.code === 401 || e.status === 401) clearSession()
    return null
  } finally {
    initialized.value = true
  }
}

/** 首次进入应用时校验一次登录态，并顺带重试本机记录迁移 */
async function ensureInitialized() {
  if (initialized.value) return
  await refreshUser()
  if (isLoggedIn.value) syncLocalTrips()
}

/** 退出登录：无论后端是否成功，本地凭证一定清掉 */
async function logout() {
  try {
    if (token.value) await apiLogout()
  } catch (e) {
    console.warn('退出登录接口失败，仍清理本地凭证', e)
  } finally {
    clearSession()
  }
}

export function useAuth() {
  return {
    token,
    user,
    initialized,
    isLoggedIn,
  
    register,
    login,
    refreshUser,
    ensureInitialized,
    logout,
    syncLocalTrips,
    clearSession,
  }
}
