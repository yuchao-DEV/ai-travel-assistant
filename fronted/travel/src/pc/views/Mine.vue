<script setup>
/**
 * 桌面端个人中心：账号信息、行程统计、系统状态、退出登录。
 */
import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Cpu, Lock, Star, Tickets } from '@element-plus/icons-vue'

import { getHistoryList } from '@/api/history'
import { clearProfile, getProfile } from '@/api/agent'
import { getMeta } from '@/api/travel'
import { useAuth } from '@/composables/useAuth'

const router = useRouter()
// ---- AI 偏好画像 ----
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
    profile.value = null   // 画像拿不到不影响页面其余部分
  } finally {
    profileLoading.value = false
  }
}

async function onClearProfile() {
  try {
    await ElMessageBox.confirm(
      '清除后 AI 助手将不再记得你的节奏与忌口，此操作不可恢复。',
      '清除偏好画像',
      { type: 'warning', confirmButtonText: '清除', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  try {
    await clearProfile()
    profile.value = null
    ElMessage.success('已清除')
  } catch (err) {
    ElMessage.error(err?.message || '清除失败')
  }
}
const { user, isLoggedIn, ensureInitialized, logout } = useAuth()

const tripCount = ref(null)
// Agent 可调用工具数：项目真正的技术含量，比已废弃的「总花费」更值得展示
const toolCount = ref(null)
const meta = ref(null)

const nickname = computed(() => user.value?.nickname || '未登录')
// 账号密码方案下 UserVO 里**没有 phone 字段**（改用 username）。
// 旧代码读 user.phone，结果永远是 undefined → 界面显示成「已登录 ·  —」。
const accountText = computed(() => {
  if (!isLoggedIn.value) return '登录后可云端保存行程，换设备也能回看'
  return user.value?.username ? `已登录 · ${user.value.username}` : '已登录'
})

async function loadStats() {
  if (!isLoggedIn.value) {
    tripCount.value = null
    return
  }
  // 工具数从 Agent 工具清单接口取真值；拿不到就保持 null，模板回落成 14
  try {
    const tRes = await fetch('/api/agent/tools')
    if (tRes.ok) {
      const tBody = await tRes.json()
      const list = (tBody && tBody.data) || []
      if (Array.isArray(list) && list.length) toolCount.value = list.length
    }
  } catch { /* 不影响主流程 */ }

  try {
    const res = await getHistoryList(1, 50)
    const data = res?.data || {}
    tripCount.value = data.total ?? 0
  } catch (e) {
    console.warn('读取行程统计失败', e)
  }
}

async function onLogout() {
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
  tripCount.value = null
  toolCount.value = null
  ElMessage.success('已退出登录')
  router.push('/')
}

onMounted(async () => {
  loadProfile()
  try {
    meta.value = await getMeta()
  } catch (e) {
    console.warn('读取 meta 失败', e)
  }
  await ensureInitialized()
  await loadStats()
})

watch(isLoggedIn, loadStats)
</script>

<template>
  <div class="mine-page">
    <!-- 用户页眉：与移动端同构（轻量、衬线名、状态点），不再是整块深绿大卡 -->
    <header class="mine-masthead">
      <el-avatar :size="46" class="mine-avatar">
        {{ (user?.nickname || '旅')[0] }}
      </el-avatar>
      <div class="mine-info">
        <div class="mine-name">{{ nickname }}</div>
        <div class="mine-sub">
          <span v-if="isLoggedIn" class="mine-dot"></span>
          <span>{{ accountText }}</span>
        </div>
      </div>
      <el-button v-if="!isLoggedIn" type="primary" round @click="router.push('/login')">
        立即登录 / 注册
      </el-button>
      <el-button v-else round plain type="danger" :icon="Lock" @click="onLogout">
        退出登录
      </el-button>
    </header>

    <div class="grid">
      <div class="pc-card stat-card">
        <el-icon :size="22" color="var(--app-brand)"><Tickets /></el-icon>
        <div class="stat-num">{{ tripCount === null ? '—' : tripCount }}</div>
        <div class="stat-label">已保存行程</div>
      </div>
      <div class="pc-card stat-card">
        <el-icon :size="22" color="var(--app-brand-hover)"><Star /></el-icon>
        <div class="stat-num">{{ toolCount === null ? '14' : toolCount }}</div>
        <div class="stat-label">AI 可调用工具</div>
      </div>
      <div class="pc-card stat-card">
        <el-icon :size="22" color="var(--app-brand-hover)"><Cpu /></el-icon>
        <div class="stat-num">{{ meta?.amap_enabled ? '已启用' : '降级' }}</div>
        <div class="stat-label">高德数据增强</div>
      </div>
      <div class="pc-card stat-card">
        <el-icon :size="22" color="var(--app-warning)"><Lock /></el-icon>
        <div class="stat-num">{{ meta?.rag_enabled ? '已启用' : '降级' }}</div>
        <div class="stat-label">知识库检索</div>
      </div>
    </div>

    <div class="pc-card">
      <h3 class="pc-section-title">关于本项目</h3>
      <el-descriptions :column="2" border>
        <el-descriptions-item label="项目名称">AI 旅游助手</el-descriptions-item>
        <el-descriptions-item label="技术栈">
          Vue 3 + Element Plus（桌面端） / Vant 4（移动端） + FastAPI
        </el-descriptions-item>
        <el-descriptions-item label="算法引擎">
          自研 KMeans++ 分天 → 加权背包选点 → 精确/2-opt 路径优化 → 时间窗排程
        </el-descriptions-item>
        <el-descriptions-item label="大模型职责">
          仅解析需求与撰写文案，不参与选点、排序、时间与费用决策
        </el-descriptions-item>
        <el-descriptions-item label="数据存储">
          SQLite（标准库 sqlite3）：账号密码、登录态、历史行程
        </el-descriptions-item>
        <el-descriptions-item label="降级策略">
          高德不可用时返回 enhanced=false 的结构化行程，服务不报错
        </el-descriptions-item>
      </el-descriptions>

      <!-- AI 助手的记忆：偏好画像必须**可见可删**。
           用户有权知道系统记住了什么，并随时清掉——这既是隐私要求，
           也是可解释性的一部分，不能只让画像在提示词里默默生效。 -->
      <el-card shadow="never" class="pam-card">
        <template #header>
          <div class="pam-head">
            <span class="pam-title">我的偏好画像</span>
            <div>
              <el-button v-if="profile?.has_profile" text type="danger" size="small"
                         @click="onClearProfile">清除画像</el-button>
              <el-button text type="primary" size="small" @click="loadProfile">刷新</el-button>
            </div>
          </div>
        </template>
        <div v-if="profileLoading" class="pam-empty">加载中…</div>
        <div v-else-if="!profile?.has_profile" class="pam-empty">
          AI 助手还没积累你的偏好。多聊几次，它会记住你的节奏与忌口。
        </div>
        <template v-else>
          <el-descriptions :column="2" border size="small">
            <el-descriptions-item label="节奏倾向">
              {{ profile.profile?.pace || '—' }}
            </el-descriptions-item>
            <el-descriptions-item label="预算档位">
              {{ profile.profile?.budget_band || '—' }}
            </el-descriptions-item>
            <el-descriptions-item label="同行人">
              {{ (profile.profile?.companions || []).join('、') || '—' }}
            </el-descriptions-item>
            <el-descriptions-item label="饮食">
              {{ (profile.profile?.dietary || []).join('、') || '—' }}
            </el-descriptions-item>
            <el-descriptions-item label="置信度">
              {{ (profile.profile?.confidence ?? 0).toFixed(2) }}
            </el-descriptions-item>
            <el-descriptions-item label="样本数">
              {{ profile.profile?.sample_size ?? 0 }}
            </el-descriptions-item>
          </el-descriptions>
          <div class="pam-tip">画像只作参考，你每次说的要求一律优先。</div>
        </template>
        <el-divider />
        <el-button text type="primary" @click="router.push('/agent/history')">
          查看 AI 会话记录 ›
        </el-button>
      </el-card>
    </div>
  </div>
</template>

<style scoped>
.mine-page {
  max-width: 1100px;
  margin: 0 auto;
  display: flex;
  flex-direction: column;
  gap: 20px;
}
.profile-card {
  display: flex;
  align-items: center;
  gap: 18px;
  padding: 26px 28px;
  border-radius: var(--pc-radius);
  background: var(--pc-gradient);
  color: var(--app-surface);
  box-shadow: 0 8px 24px rgba(74, 144, 217, 0.22);
}
.profile-avatar {
  background: rgba(255, 255, 255, 0.24);
  color: var(--app-surface);
  font-size: 24px;
  flex-shrink: 0;
}
.profile-info {
  flex: 1;
  min-width: 0;
}
.profile-name {
  font-size: 22px;
  font-weight: 700;
}
.profile-sub {
  margin-top: 8px;
  font-size: 13px;
  opacity: 0.88;
}
.grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 20px;
}
.stat-card {
  text-align: center;
  padding: 22px 16px;
}
.stat-num {
  margin-top: 10px;
  font-size: 22px;
  font-weight: 700;
  color: var(--app-text);
}
.stat-label {
  margin-top: 6px;
  font-size: 12px;
  color: var(--app-text-secondary);
}

.pam-card { margin-top: 16px; border-radius: 12px; }
.pam-head { display: flex; align-items: center; justify-content: space-between; }
.pam-title { font-size: 14px; font-weight: 600; color: var(--app-text); }
.pam-empty { color: var(--app-text-tertiary); font-size: 12.5px; line-height: 1.8; }
.pam-tip { margin-top: 10px; padding: 6px 10px; font-size: 11.5px; color: var(--app-warning); background: var(--app-warning-soft); border-radius: 6px; }

/* ==================== 用户页眉（与移动端同构） ==================== */
.mine-masthead {
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 20px 4px 18px;
  border-bottom: 1px solid var(--app-hairline);
  margin-bottom: 20px;
}
.mine-avatar {
  background: var(--app-brand-soft);
  color: var(--app-brand);
  font-size: 18px;
  flex-shrink: 0;
  border: 1px solid var(--app-hairline);
}
.mine-info { flex: 1; min-width: 0; }
.mine-name {
  font-family: var(--app-font-serif);
  font-size: 24px;
  font-weight: 600;
  line-height: 1.2;
  color: var(--app-text);
}
.mine-sub {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-top: 5px;
  font-size: 12.5px;
  color: var(--app-text-tertiary);
}
.mine-dot {
  width: 5px; height: 5px; border-radius: 50%;
  background: var(--app-success);
}

/* 旧的深绿大卡不再使用 */
.profile-card, .profile-avatar, .profile-name, .profile-sub { display: none !important; }</style>
