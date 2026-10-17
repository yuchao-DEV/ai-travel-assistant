/**
 * 桌面端路由。
 *
 * 刻意用 **hash 模式**（`/pc/#/timeline`）而不是 history 模式，原因：
 * 桌面端与移动端同一个 Vite 工程、同一个 dist，history 模式下
 * 刷新 `/pc/timeline` 会被 Vite / 静态服务器回退到移动端的 index.html。
 * hash 模式不需要任何服务端 rewrite 规则，开发、预览、静态托管都能直接用。
 */
import { createRouter, createWebHashHistory } from 'vue-router'

import { useAuth } from '@/composables/useAuth'

const routes = [
  { path: '/', name: 'pc-home', component: () => import('../views/Home.vue') },
  { path: '/timeline', name: 'pc-timeline', component: () => import('../views/Timeline.vue') },
  { path: '/agent', name: 'pc-agent', component: () => import('../views/AgentChat.vue') },
  { path: '/agent/history', name: 'pc-agent-history', component: () => import('../views/AgentHistory.vue') },
  { path: '/mine', name: 'pc-mine', component: () => import('../views/Mine.vue') },
  {
    path: '/history',
    name: 'pc-history',
    component: () => import('../views/History.vue'),
    meta: { requiresAuth: true },
  },
  { path: '/login', name: 'pc-login', component: () => import('../views/Login.vue') },
]

const router = createRouter({
  history: createWebHashHistory('/pc/'),
  routes,
  scrollBehavior: () => ({ top: 0 }),
})

// 需要登录的页面统一跳登录页，并记录来源以便登录后回跳
router.beforeEach(async (to) => {
  const { isLoggedIn, ensureInitialized } = useAuth()
  await ensureInitialized()
  if (to.meta.requiresAuth && !isLoggedIn.value) {
    return { path: '/login', query: { redirect: to.fullPath } }
  }
  return true
})

export default router
