<script setup>
/**
 * 桌面端外壳：顶部导航 + 用户区 + 主内容区。
 * 登录态复用移动端同一份 useAuth（模块级单例，同一套 token 与后端接口）。
 */
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { ChatDotRound, HomeFilled, Location, User } from '@element-plus/icons-vue'

import { useAuth } from '@/composables/useAuth'

const route = useRoute()
const router = useRouter()
const { user, isLoggedIn, ensureInitialized, logout } = useAuth()

const activeMenu = computed(() => {
  const path = route.path
  if (path.startsWith('/timeline')) return '/'
  if (path.startsWith('/history')) return '/mine'
  return path
})

// 行程页需要整屏地图，不套用常规内边距
const isFullBleed = computed(() => route.path.startsWith('/timeline') || route.path.startsWith('/login'))

async function onCommand(command) {  if (command === 'logout') {
    try {
      await ElMessageBox.confirm('确定要退出当前账号吗？', '退出登录', {
        confirmButtonText: '退出',
        cancelButtonText: '取消',
        type: 'warning',
      })
    } catch {
      return
    }
    await logout()
    ElMessage.success('已退出登录')
    router.push('/')
    return
  }
  if (command === 'history') {
    router.push(isLoggedIn.value ? '/history' : '/login')
  }
}

async function goLogin() {
  router.push({ path: '/login', query: { redirect: route.fullPath } })
}

onMounted(() => {
  ensureInitialized()
})
</script>

<template>
  <el-container class="pc-shell">
    <el-header class="pc-header">
      <div class="brand" @click="router.push('/')">
        <div class="brand-logo"><el-icon :size="20"><Location /></el-icon></div>
        <div class="brand-text">
          <div class="brand-title">AI 旅游助手</div>
          <div class="brand-sub">算法驱动 · 桌面端</div>
        </div>
      </div>

      <el-menu :default-active="activeMenu" mode="horizontal" class="pc-menu" router :ellipsis="false">
        <el-menu-item index="/">
          <el-icon><HomeFilled /></el-icon>规划
        </el-menu-item>
        <el-menu-item index="/mine">
          <el-icon><User /></el-icon>我的
        </el-menu-item>
      </el-menu>

      <div class="header-right">
        <template v-if="isLoggedIn">
          <el-dropdown @command="onCommand">
            <span class="user-chip">
              <el-avatar :size="28" class="user-avatar">
                {{ (user?.nickname || '旅')[0] }}
              </el-avatar>
              <span class="user-name">{{ user?.nickname }}</span>
            </span>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item command="history">我的行程</el-dropdown-item>
                <el-dropdown-item command="logout" divided>退出登录</el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
        </template>
        <el-button v-else type="primary" plain round @click="goLogin">登录 / 注册</el-button>
      </div>
    </el-header>

    <el-main :class="['pc-main', { 'is-full': isFullBleed }]">
      <router-view />
    </el-main>
  </el-container>
</template>

<style scoped>
.pc-shell {
  height: 100vh;
}
.pc-header {
  display: flex;
  align-items: center;
  gap: 28px;
  height: 62px;
  padding: 0 26px;
  background: var(--app-surface);
  border-bottom: 1px solid var(--app-border);
  box-shadow: 0 1px 6px rgba(31, 45, 61, 0.04);
  z-index: 10;
}
.brand {
  display: flex;
  align-items: center;
  gap: 10px;
  cursor: pointer;
  flex-shrink: 0;
}
.brand-logo {
  width: 36px;
  height: 36px;
  border-radius: 10px;
  background: var(--pc-gradient);
  color: var(--app-surface);
  display: flex;
  align-items: center;
  justify-content: center;
}
.brand-title {
  font-size: 16px;
  font-weight: 700;
  line-height: 1.2;
}
.brand-sub {
  font-size: 11px;
  color: var(--app-text-secondary);
  letter-spacing: 0.4px;
}
.pc-menu {
  flex: 1;
  border-bottom: none;
}
.header-right {
  flex-shrink: 0;
}
.user-chip {
  display: flex;
  align-items: center;
  gap: 8px;
  cursor: pointer;
  outline: none;
}
.user-avatar {
  background: var(--pc-gradient);
  color: var(--app-surface);
  font-size: 13px;
}
.user-name {
  font-size: 14px;
  color: var(--app-text);
}
.pc-main {
  background: var(--app-bg);
  padding: 22px 26px;
  overflow-y: auto;
}
.pc-main.is-full {
  padding: 0;
  overflow: hidden;
}
</style>
