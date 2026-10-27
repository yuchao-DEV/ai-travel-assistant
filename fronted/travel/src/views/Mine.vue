<script setup>
/**
 * 个人中心：展示真实登录用户、行程数量、以及退出登录。
 */
import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { showConfirmDialog, showSuccessToast, showToast } from 'vant'

import { getHistoryList } from '@/api/history'
import { clearProfile, getProfile } from '@/api/agent'
import { useAuth } from '@/composables/useAuth'

const router = useRouter()
const { user, isLoggedIn, ensureInitialized, logout } = useAuth()

const tripCount = ref(null)

const nickname = computed(() => user.value?.nickname || '旅行爱好者')
// 账号密码方案下 UserVO 里**没有 phone 字段**（改用 username）。
// 旧代码读 user.phone，结果恒为 undefined → 界面显示成「已登录 · 」+ 空白。
const accountText = computed(() => user.value?.username || '')
const subTitle = computed(() => {
  if (!isLoggedIn.value) return '登录后体验更多功能'
  return accountText.value ? `已登录 · ${accountText.value}` : '已登录'
})

// 只列**系统真能做到**的入口。
// 旧的「我的收藏 / 浏览历史 / 设置」三个 to 为空字符串，点击只弹
// 「该功能开发中」——那是界面在声称不存在的功能，已删除。
const menus = computed(() => [
  { icon: 'orders-o', label: '我的行程', badge: tripCount.value, to: '/history' },
  { icon: 'chat-o', label: 'AI 会话记录', to: '/agent/history' },
])

// ---- AI 偏好画像：必须可见可删 ----
// 用户有权知道系统记住了什么，并随时清掉。这既是隐私要求，
// 也是"可解释性"的一部分——不能只让画像在提示词里默默生效。
const profile = ref(null)
const profileLoading = ref(false)

async function loadProfile() {
  if (!isLoggedIn.value) {
    profile.value = null
    return
  }
  profileLoading.value = true
  try {
    const res = await getProfile()
    profile.value = res?.data || null
  } catch {
    // 画像拿不到不影响页面其他部分：静默降级，不弹错误
    profile.value = null
  } finally {
    profileLoading.value = false
  }
}

async function onClearProfile() {
  try {
    await showConfirmDialog({
      title: '清除偏好画像',
      message: '清除后 AI 助手将不再记得你的节奏与忌口，此操作不可恢复。',
    })
  } catch {
    return
  }
  try {
    await clearProfile()
    profile.value = null
    showSuccessToast('已清除')
  } catch (err) {
    showToast(err?.message || '清除失败')
  }
}

async function loadTripCount() {
  if (!isLoggedIn.value) {
    tripCount.value = null
    return
  }
  try {
    const res = await getHistoryList(1, 1)
    tripCount.value = res?.data?.total ?? 0
  } catch (e) {
    console.warn('读取行程数量失败', e)
  }
}

function onMenuClick(item) {
  if (!item.to) return
  if (!isLoggedIn.value) {
    router.push({ path: '/login', query: { redirect: item.to } })
    return
  }
  router.push(item.to)
}

async function onLogout() {
  try {
    await showConfirmDialog({ title: '退出登录', message: '确定要退出当前账号吗？' })
  } catch {
    return
  }
  await logout()
  tripCount.value = null
  showSuccessToast('已退出登录')
}

onMounted(async () => {
  loadProfile()
  await ensureInitialized()
  loadTripCount()
})

// 登录/退出后刷新数量
watch(isLoggedIn, () => {
  loadTripCount()
})
</script>

<template>
  <div class="page-container">
    <van-nav-bar title="我的" />
    <div class="page-content">
      <!-- 用户信息：轻量页眉，不再是整块深绿大卡。
           原来那张卡占首屏近 1/4，却只承载「昵称 + 已登录」两条信息，
           把真正有用的菜单挤到了屏幕外。 -->
      <header
        class="masthead"
        :class="{ 'masthead--tappable': !isLoggedIn }"
        @click="!isLoggedIn && router.push('/login')"
      >
        <div class="masthead-avatar">
          <van-icon name="user-o" size="20" />
        </div>
        <div class="masthead-body">
          <h1 class="masthead-name">{{ nickname }}</h1>
          <div class="masthead-meta">
            <span v-if="isLoggedIn" class="status-dot"></span>
            <span>{{ subTitle }}</span>
          </div>
        </div>
        <van-icon
          v-if="!isLoggedIn"
          name="arrow"
          size="15"
          color="var(--app-text-tertiary)"
        />
      </header>

      <!-- 未登录提示 -->
      <div v-if="!isLoggedIn" class="login-tip card">
        <div class="login-tip-text">登录后行程会保存到账号，换设备也能回看</div>
        <van-button type="primary" size="small" round @click="router.push('/login')">
          立即登录 / 注册
        </van-button>
      </div>

      <!-- 功能菜单 -->
      <div class="card menu-card">
        <van-cell-group :border="false">
          <van-cell
            v-for="item in menus"
            :key="item.label"
            :title="item.label"
            :value="item.badge !== null && item.badge !== undefined ? `${item.badge} 条` : ''"
            is-link
            class="menu-cell"
            @click="onMenuClick(item)"
          >
            <template #icon>
              <van-icon :name="item.icon" size="20" class="menu-icon" />
            </template>
          </van-cell>
        </van-cell-group>
      </div>

      <!-- AI 助手的记忆：偏好画像 + 会话记录。
           画像必须**可见可删**——用户有权知道系统记住了什么、并随时清掉。
           这既是隐私要求，也是"可解释性"的一部分。 -->
      <div class="card agent-profile-card">
        <div class="profile-head">
          <span class="profile-title">我的偏好画像</span>
          <van-button v-if="profile && profile.has_profile" plain size="mini" type="danger"
                      round @click="onClearProfile">清除</van-button>
        </div>
        <div v-if="profileLoading" class="profile-empty">加载中…</div>
        <div v-else-if="!profile || !profile.has_profile" class="profile-empty">
          AI 助手还没积累你的偏好。多聊几次，它会记住你的节奏与忌口。
        </div>
        <template v-else>
          <div class="profile-row" v-if="profile.profile?.pace">
            <span class="profile-k">节奏倾向</span><span class="profile-v">{{ profile.profile.pace }}</span>
          </div>
          <div class="profile-row" v-if="profile.profile?.budget_band">
            <span class="profile-k">预算印象</span><span class="profile-v">{{ profile.profile.budget_band }}</span>
          </div>
          <div class="profile-row" v-if="(profile.profile?.companions || []).length">
            <span class="profile-k">同行人</span>
            <span class="profile-v">{{ profile.profile.companions.join('、') }}</span>
          </div>
          <div class="profile-row" v-if="(profile.profile?.dietary || []).length">
            <span class="profile-k">饮食</span>
            <span class="profile-v">{{ profile.profile.dietary.join('、') }}</span>
          </div>
          <div class="profile-row">
            <span class="profile-k">置信度</span>
            <span class="profile-v">{{ (profile.profile?.confidence ?? 0).toFixed(2) }}</span>
          </div>
          <div class="profile-tip">画像只作参考，你每次说的要求一律优先。</div>
        </template>
      </div>

      <!-- 退出登录 -->
      <div v-if="isLoggedIn" class="card logout-card">
        <van-button block plain type="danger" round @click="onLogout">退出登录</van-button>
      </div>

      <!-- 关于 -->
      <div class="card about-card">
        <div class="about-title">智能旅游助手</div>
        <div class="about-desc">基于 AI 的智能景点介绍与行程规划系统</div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.page-container {
  min-height: 100vh;
  min-height: 100dvh;
  background-color: var(--app-surface-muted);
  padding-bottom: 80px;
}
.page-content {
  padding: 16px;
}
.card {
  background-color: var(--app-surface);
  border-radius: 10px;
  padding: 16px;
  margin-top: 12px;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.05);
}
/* 用户信息 */
.profile-card {
  display: flex;
  align-items: center;
  gap: 14px;
  background: linear-gradient(135deg, var(--app-brand), var(--app-brand-hover));
  border-radius: 10px;
  padding: 22px 18px;
  color: var(--app-surface);
}
.avatar {
  flex-shrink: 0;
  width: 60px;
  height: 60px;
  border-radius: 50%;
  background: rgba(255, 255, 255, 0.25);
  display: flex;
  align-items: center;
  justify-content: center;
}
.profile-info {
  flex: 1;
  min-width: 0;
}
.nickname {
  font-size: 19px;
  font-weight: 700;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.sub-title {
  margin-top: 6px;
  font-size: 13px;
  opacity: 0.85;
}
/* 未登录提示 */
.login-tip {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}
.login-tip-text {
  flex: 1;
  font-size: 13px;
  color: var(--app-text-secondary);
  line-height: 1.6;
}
/* 菜单 */
.menu-card {
  padding: 4px 0;
  overflow: hidden;
}
.menu-card :deep(.van-cell) {
  padding: 14px 16px;
  font-size: 15px;
}
.menu-icon {
  margin-right: 12px;
  color: var(--app-brand);
}
.logout-card {
  padding: 14px 16px;
}
/* 关于 */
.about-card {
  text-align: center;
  padding: 20px 16px;
}
.about-title {
  font-size: 16px;
  font-weight: 600;
  color: var(--app-text);
}
.about-desc {
  margin-top: 8px;
  font-size: 13px;
  color: var(--app-text-tertiary);
}

/* AI 偏好画像（与上面的蓝色用户信息卡区分：那个也叫 profile-card，别撞名） */
.agent-profile-card { padding: 2px 0 0; }
.agent-profile-card .profile-head {
  display: flex; align-items: center; justify-content: space-between;
  padding: 10px 16px 6px;
}
.agent-profile-card .profile-title { font-size: 14px; font-weight: 600; color: var(--app-text); }
.agent-profile-card .profile-empty { padding: 2px 16px 10px; font-size: 12.5px; color: var(--app-text-tertiary); line-height: 1.7; }
.agent-profile-card .profile-row { display: flex; padding: 4px 16px; font-size: 13px; }
.agent-profile-card .profile-k { width: 76px; color: var(--app-text-secondary); flex: 0 0 76px; }
.agent-profile-card .profile-v { color: var(--app-text); flex: 1; }
.agent-profile-card .profile-tip {
  margin: 6px 16px 10px; padding: 6px 8px; font-size: 11.5px; color: var(--app-warning);
  background: var(--app-warning-soft); border-radius: 6px; line-height: 1.6;
}

/* ==================== 用户页眉（轻量） ==================== */
.masthead {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 18px 4px 16px;
  border-bottom: 1px solid var(--app-hairline);
  margin-bottom: 16px;
}
.masthead--tappable { cursor: pointer; }

.masthead-avatar {
  flex-shrink: 0;
  width: 44px;
  height: 44px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--app-brand-soft);
  color: var(--app-brand);
  border: 1px solid var(--app-hairline);
}

.masthead-body { flex: 1; min-width: 0; }

.masthead-name {
  margin: 0;
  font-family: var(--app-font-serif);
  font-size: 21px;
  font-weight: 600;
  line-height: 1.2;
  color: var(--app-text);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.masthead-meta {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-top: 4px;
  font-size: 11.5px;
  color: var(--app-text-tertiary);
  letter-spacing: 0.02em;
}
.status-dot {
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: var(--app-success);
}

/* 老样式不再使用，留着会继续渲染成深绿大卡 */
.profile-card, .profile-info, .nickname { display: none !important; }
</style>
