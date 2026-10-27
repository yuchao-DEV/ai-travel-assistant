<script setup>
/**
 * 桌面端个人中心。
 *
 * ## 信息架构（本次重排）
 *
 * 1. 账户页眉：头像 / 昵称 / 登录态 / 退出
 * 2. 指标区：**可点击**的数字卡（已保存行程、AI 工具数）+ 两个**状态卡**
 *    （高德增强、知识库检索）——状态用「圆点 + 文字」而不是把「已启用」
 *    塞进原本放数字的位置，避免四张卡的视觉权重被拉平。
 * 3. 最近行程：真正有用的内容（原来这一块放的是「关于本项目」技术表格，
 *    对用户零价值，已删除）。
 * 4. 偏好画像：AI 记住了什么，可见可删（隐私要求）。
 *
 * ## 删掉的东西及理由
 *
 * - 「关于本项目」技术栈表格：参赛材料的读者是评委，不是使用者。
 *   这类信息属于 README / 技术文档，放在终端界面里只是噪音。
 * - 「预算档位」行：预算系统已整体移除，算法不再按它决策；
 *   留着会让人以为改了它能影响行程。画像里没有这项时也不显示空行。
 */
import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { ArrowRight, Clock, Cpu, DataLine, Lock, Star, Tickets } from '@element-plus/icons-vue'

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
// 最近行程：从「我的行程」列表里取前 3 条，比空着更有用
const recentTrips = ref([])

const nickname = computed(() => user.value?.nickname || '未登录')
// 账号密码方案下 UserVO 里**没有 phone 字段**（改用 username）。
// 旧代码读 user.phone，结果永远是 undefined → 界面显示成「已登录 ·  —」。
const accountText = computed(() => {
  if (!isLoggedIn.value) return '登录后可云端保存行程，换设备也能回看'
  return user.value?.username ? `已登录 · ${user.value.username}` : '已登录'
})

/** 画像里"有值才显示"的条目（避免出现一排「—」） */
const profileRows = computed(() => {
  const p = profile.value?.profile || {}
  const rows = []
  if (p.pace) rows.push({ k: '节奏', v: p.pace })
  // 预算档位是 AI 从历史花费**推断**出来的记忆，不是规划输入：
  // 预算系统已移除，算法不按它决策，但作为"AI 记住了什么"要如实展示。
  if (p.budget_band) rows.push({ k: '预算印象', v: p.budget_band })
  if ((p.companions || []).length) rows.push({ k: '同行', v: p.companions.join('、') })
  if ((p.dietary || []).length) rows.push({ k: '饮食', v: p.dietary.join('、') })
  if ((p.interests || []).length) rows.push({ k: '兴趣', v: p.interests.join('、') })
  return rows
})

const profileMeta = computed(() => {
  const p = profile.value?.profile || {}
  const parts = []
  if (p.sample_size) parts.push(`样本 ${p.sample_size} 次`)
  if (typeof p.confidence === 'number') parts.push(`置信度 ${p.confidence.toFixed(2)}`)
  return parts.join(' · ')
})

function formatDate(iso) {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  const pad = (n) => String(n).padStart(2, '0')
  return `${d.getMonth() + 1}月${d.getDate()}日 ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

function openTrip(t) {
  router.push({ path: '/timeline', query: { history_id: t.id } })
}

async function loadStats() {
  if (!isLoggedIn.value) {
    tripCount.value = null
    recentTrips.value = []
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
    recentTrips.value = (data.items || []).slice(0, 3)
  } catch (e) {
    console.warn('读取行程统计失败', e)
    recentTrips.value = []
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
  recentTrips.value = []
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

watch(isLoggedIn, () => {
  loadStats()
})
</script>

<template>
  <div class="mine-page">
    <!-- 账户页眉：与移动端同构（轻量、衬线名、状态点），不再是整块深绿大卡 -->
    <header class="mine-masthead">
      <el-avatar :size="52" class="mine-avatar">
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

    <!-- 指标区：两个数字卡（可点击）+ 两个状态卡 -->
    <section class="stat-grid">
      <button
        type="button"
        class="pc-card stat-card"
        :class="{ 'is-link': isLoggedIn }"
        @click="router.push('/history')"
      >
        <span class="stat-icon"><el-icon :size="20"><Tickets /></el-icon></span>
        <span class="stat-num">{{ tripCount === null ? '—' : tripCount }}</span>
        <span class="stat-label">已保存行程</span>
        <span class="stat-hint">
          {{ isLoggedIn ? '查看全部' : '登录后云端保存' }}
          <el-icon v-if="isLoggedIn" :size="12"><ArrowRight /></el-icon>
        </span>
      </button>

      <button type="button" class="pc-card stat-card is-link" @click="router.push('/agent')">
        <span class="stat-icon warm"><el-icon :size="20"><Star /></el-icon></span>
        <span class="stat-num">{{ toolCount === null ? '14' : toolCount }}</span>
        <span class="stat-label">AI 可调用工具</span>
        <span class="stat-hint">去问助手<el-icon :size="12"><ArrowRight /></el-icon></span>
      </button>

      <div class="pc-card stat-card is-status">
        <span class="stat-icon"><el-icon :size="20"><Cpu /></el-icon></span>
        <span class="stat-status">
          <span class="status-dot" :class="{ off: !meta?.amap_enabled }"></span>
          {{ meta ? (meta.amap_enabled ? '已启用' : '降级中') : '—' }}
        </span>
        <span class="stat-label">高德数据增强</span>
        <span class="stat-hint">真实 POI / 营业时间 / 距离</span>
      </div>

      <div class="pc-card stat-card is-status">
        <span class="stat-icon warn"><el-icon :size="20"><DataLine /></el-icon></span>
        <span class="stat-status">
          <span class="status-dot" :class="{ off: !meta?.rag_enabled }"></span>
          {{ meta ? (meta.rag_enabled ? '已启用' : '降级中') : '—' }}
        </span>
        <span class="stat-label">知识库检索</span>
        <span class="stat-hint">预约规则 / 票价口径</span>
      </div>
    </section>

    <!-- 最近行程：删掉「关于本项目」后补上的真实内容 -->
    <section class="pc-card recent-card">
      <div class="card-head">
        <h3 class="pc-section-title">最近行程</h3>
        <el-button
          v-if="isLoggedIn"
          text
          type="primary"
          size="small"
          @click="router.push('/history')"
        >
          全部行程 ›
        </el-button>
      </div>

      <div v-if="!isLoggedIn" class="recent-empty">
        <p class="empty-text">登录后，规划过的行程会保存在这里，换设备也能回看。</p>
        <el-button type="primary" round @click="router.push('/login')">立即登录</el-button>
      </div>
      <div v-else-if="!recentTrips.length" class="recent-empty">
        <p class="empty-text">还没有保存的行程。去规划一次，或直接问问 AI 助手。</p>
        <div class="empty-actions">
          <el-button type="primary" round @click="router.push('/')">开始规划</el-button>
          <el-button round @click="router.push('/agent')">AI 助手</el-button>
        </div>
      </div>
      <ul v-else class="recent-list">
        <li v-for="t in recentTrips" :key="t.id">
          <button type="button" class="recent-row" @click="openTrip(t)">
            <span class="recent-main">
              <span class="recent-title">{{ t.title || `${t.origin || ''}出发·${t.city}${t.days}日游` }}</span>
              <span class="recent-meta">
                {{ t.city }} · {{ t.days }} 天 · {{ t.spot_count }} 个安排
              </span>
            </span>
            <span class="recent-time">
              <el-icon :size="12"><Clock /></el-icon>{{ formatDate(t.created_at) }}
            </span>
          </button>
        </li>
      </ul>
    </section>

    <!-- AI 助手的记忆：偏好画像必须**可见可删**。
         用户有权知道系统记住了什么，并随时清掉——这既是隐私要求，
         也是可解释性的一部分，不能只让画像在提示词里默默生效。 -->
    <section class="pc-card">
      <div class="card-head">
        <h3 class="pc-section-title">我的偏好画像</h3>
        <div class="card-actions">
          <el-button
            v-if="profile?.has_profile"
            text
            type="danger"
            size="small"
            @click="onClearProfile"
          >
            清除画像
          </el-button>
          <el-button text type="primary" size="small" @click="loadProfile">刷新</el-button>
        </div>
      </div>

      <div v-if="profileLoading" class="pam-empty">加载中…</div>
      <div v-else-if="!profile?.has_profile" class="pam-empty">
        AI 助手还没积累你的偏好。多聊几次，它会记住你的节奏与忌口。
      </div>
      <template v-else>
        <div class="chip-row">
          <span v-for="row in profileRows" :key="row.k" class="chip">
            <span class="chip-k">{{ row.k }}</span>
            <span class="chip-v">{{ row.v }}</span>
          </span>
          <span v-if="!profileRows.length" class="chip-empty">暂时只记住了很少的信息</span>
        </div>
        <div v-if="profileMeta" class="pam-tip">{{ profileMeta }}</div>
        <div class="pam-tip">画像只作参考，你每次说的要求一律优先。</div>
      </template>

      <el-divider />
      <el-button text type="primary" @click="router.push('/agent/history')">
        查看 AI 会话记录 ›
      </el-button>
    </section>
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

/* ---------------- 账户页眉 ---------------- */
.mine-masthead {
  display: flex;
  align-items: center;
  gap: 18px;
  padding: 22px 26px;
  border-radius: var(--app-radius-lg);
  background: var(--app-surface);
  border: 1px solid var(--app-hairline);
  box-shadow: var(--app-elev-1);
}
.mine-avatar {
  background: var(--app-brand-soft, rgba(66, 127, 98, 0.14));
  color: var(--app-brand-hover);
  font-size: 22px;
  font-weight: 700;
  flex-shrink: 0;
}
.mine-info {
  flex: 1;
  min-width: 0;
}
.mine-name {
  font-family: var(--app-font-serif, 'Songti SC', serif);
  font-size: 23px;
  font-weight: 700;
  letter-spacing: 0.01em;
  color: var(--app-text);
}
.mine-sub {
  margin-top: 6px;
  display: flex;
  align-items: center;
  gap: 7px;
  font-size: 13px;
  color: var(--app-text-secondary);
}
.mine-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--app-success, #3f9e6f);
  box-shadow: 0 0 0 3px var(--app-success-soft, rgba(63, 158, 111, 0.16));
}

/* ---------------- 指标区 ---------------- */
.stat-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 16px;
}
.stat-card {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 6px;
  padding: 18px 18px 16px;
  text-align: left;
  font: inherit;
  color: inherit;
  background: var(--app-surface);
  border: 1px solid var(--app-hairline);
  border-radius: var(--app-radius-lg, 14px);
  box-shadow: var(--app-elev-1);
  /* 数值加载完成会从「—」变成数字，预留高度避免卡片跳动 */
  min-height: 132px;
  transition: transform 0.16s var(--app-ease-out, ease-out),
              box-shadow 0.16s var(--app-ease-out, ease-out);
}
.stat-card.is-link {
  cursor: pointer;
}
.stat-card.is-link:hover {
  transform: translateY(-2px);
  box-shadow: var(--app-elev-2);
}
.stat-card:active {
  transform: scale(0.99);
}
.stat-card:focus-visible {
  outline: 2px solid var(--app-brand);
  outline-offset: 2px;
}
.stat-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 34px;
  height: 34px;
  border-radius: 10px;
  margin-bottom: 4px;
  background: var(--app-brand-soft, rgba(66, 127, 98, 0.12));
  color: var(--app-brand-hover);
}
.stat-icon.warm {
  background: var(--app-accent-soft, rgba(196, 138, 63, 0.14));
  color: var(--app-accent, #b07c33);
}
.stat-icon.warn {
  background: var(--app-warning-soft, rgba(190, 120, 50, 0.14));
  color: var(--app-warning, #b0702f);
}
.stat-num {
  font-size: 28px;
  font-weight: 700;
  line-height: 1.1;
  color: var(--app-text);
  font-variant-numeric: tabular-nums;
}
/* 状态卡：主值不是数字，字号降一档，避免与数字卡争抢 */
.stat-status {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  font-size: 18px;
  font-weight: 650;
  color: var(--app-text);
  min-height: 31px;
}
.status-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--app-success, #3f9e6f);
}
.status-dot.off {
  background: var(--app-danger, #c0503f);
}
.stat-label {
  font-size: 13px;
  color: var(--app-text-secondary);
}
.stat-hint {
  margin-top: auto;
  display: inline-flex;
  align-items: center;
  gap: 3px;
  font-size: 12px;
  color: var(--app-text-tertiary);
}

/* ---------------- 卡片头 ---------------- */
.card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}
.card-actions {
  display: flex;
  align-items: center;
  gap: 4px;
}

/* ---------------- 最近行程 ---------------- */
.recent-card {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.recent-empty {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 14px;
  padding: 8px 0 4px;
}
.empty-text {
  font-size: 13.5px;
  color: var(--app-text-secondary);
  line-height: 1.7;
}
.empty-actions {
  display: flex;
  gap: 10px;
}
.recent-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
}
.recent-row {
  width: 100%;
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 13px 6px;
  border: none;
  border-bottom: 1px solid var(--app-hairline);
  background: transparent;
  font: inherit;
  color: inherit;
  text-align: left;
  cursor: pointer;
  border-radius: 8px;
  transition: background 0.15s var(--app-ease-out, ease-out);
}
.recent-row:hover {
  background: var(--app-brand-soft, rgba(66, 127, 98, 0.07));
}
.recent-row:active {
  transform: scale(0.997);
}
.recent-row:focus-visible {
  outline: 2px solid var(--app-brand);
  outline-offset: -2px;
}
.recent-list li:last-child .recent-row {
  border-bottom: none;
}
.recent-main {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.recent-title {
  font-size: 14.5px;
  font-weight: 600;
  color: var(--app-text);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.recent-meta {
  font-size: 12.5px;
  color: var(--app-text-tertiary);
}
.recent-time {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 12.5px;
  color: var(--app-text-tertiary);
  flex-shrink: 0;
}

/* ---------------- 偏好画像 ---------------- */
.pam-empty {
  font-size: 13.5px;
  color: var(--app-text-secondary);
  line-height: 1.75;
  padding: 6px 0;
}
.chip-row {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
}
.chip {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 7px 12px;
  border-radius: 999px;
  background: var(--app-brand-soft, rgba(66, 127, 98, 0.1));
  border: 1px solid var(--app-hairline);
}
.chip-k {
  font-size: 12px;
  color: var(--app-text-tertiary);
}
.chip-v {
  font-size: 13px;
  font-weight: 600;
  color: var(--app-brand-hover);
}
.chip-empty {
  font-size: 13px;
  color: var(--app-text-tertiary);
}
.pam-tip {
  margin-top: 10px;
  font-size: 12.5px;
  color: var(--app-text-tertiary);
  line-height: 1.7;
}

@media (max-width: 980px) {
  .stat-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
</style>
