import axios from 'axios'
import { clearAuthStorage, getToken } from './token'

// axios 二次封装
const request = axios.create({
  // 开发环境走 vite 代理（/api -> http://localhost:8080），避免跨域
  baseURL: '',
  // 大模型生成行程耗时较长，给足超时时间
  timeout: 200000,
})

// 请求拦截器：统一携带登录态
request.interceptors.request.use(
  (config) => {
    const token = getToken()
    if (token) {
      config.headers.Authorization = `Bearer ${token}`
    }
    return config
  },
  (error) => Promise.reject(error)
)

// 响应拦截器：
// 成功 → 直接返回响应体（页面拿到的就是后端 JSON 本体）
// 失败 → 抛出一个带 code/message 的 Error，方便页面直接弹提示
request.interceptors.response.use(
  (response) => response.data,
  (error) => {
    const status = error.response?.status
    const body = error.response?.data
    const wrapped = new Error(body?.message || error.message || '网络异常，请稍后重试')
    wrapped.code = body?.code ?? status
    wrapped.status = status
    wrapped.data = body?.data ?? null
    wrapped.isNetworkError = !error.response

    // 登录态失效：清掉本地凭证，由路由守卫引导去登录页
    if (status === 401) {
      clearAuthStorage()
    }
    return Promise.reject(wrapped)
  }
)

export default request
