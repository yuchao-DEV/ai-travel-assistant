import { createRouter, createWebHistory } from 'vue-router'

import { useAuth } from '@/composables/useAuth'

const routes = [
  {
    path: '/',
    name: 'home',
    component: () => import('@/views/Home.vue'),
  },
  {
    path: '/agent/history',
    name: 'agent-history',
    component: () => import('@/views/AgentHistory.vue'),
  },
  {
    path: '/agent',
    name: 'agent',
    component: () => import('@/views/AgentChat.vue'),
  },
  {
    path: '/mine',
    name: 'mine',
    component: () => import('@/views/Mine.vue'),
  },
  {
    path: '/timeline',
    name: 'timeline',
    component: () => import('@/views/Timeline.vue'),
  },
  {
    path: '/login',
    name: 'login',
    component: () => import('@/views/Login.vue'),
    // 登录页不显示底部 tabbar
    meta: { hideTabbar: true },
  },
  {
    path: '/history',
    name: 'history',
    component: () => import('@/views/History.vue'),
    meta: { requiresAuth: true },
  },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

// 需要登录的页面统一跳登录页，并记录来源以便登录后回跳
router.beforeEach(async (to) => {
  const { isLoggedIn, ensureInitialized } = useAuth()
  // 首次进入应用时校验一次登录态（本地 token 可能已失效）
  await ensureInitialized()
  if (to.meta.requiresAuth && !isLoggedIn.value) {
    return { path: '/login', query: { redirect: to.fullPath } }
  }
  return true
})

export default router
