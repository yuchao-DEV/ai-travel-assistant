import request from '@/utils/request'

/**
 * 账号密码注册（成功后直接返回登录态）
 * @param {{username: string, password: string, nickname?: string, avatar?: string}} p
 */
export function register({ username, password, nickname, avatar }) {
  return request.post('/api/auth/register', {
    username,
    password,
    nickname: nickname || undefined,
    avatar: avatar || undefined,
  })
}

/**
 * 账号密码登录
 * @param {{username: string, password: string}} p
 */
export function login({ username, password }) {
  return request.post('/api/auth/login', { username, password })
}

/** 取当前登录用户 */
export function getMe() {
  return request.get('/api/auth/me')
}

/** 退出登录（服务端删除 token） */
export function logout() {
  return request.post('/api/auth/logout')
}
