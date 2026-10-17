<script setup>
/**
 * 我的行程：分页展示后端保存的历史行程，点击回看、左滑删除。
 */
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { showConfirmDialog, showSuccessToast, showToast } from 'vant'

import { deleteHistory, getHistoryList } from '@/api/history'
import { useAuth } from '@/composables/useAuth'

const router = useRouter()
const { isLoggedIn, ensureInitialized } = useAuth()

const PAGE_SIZE = 20

const trips = ref([])
const total = ref(0)
const page = ref(0)
const loading = ref(false)
const finished = ref(false)
const refreshing = ref(false)
const errorMsg = ref('')

/** 列表项 → 展示用字段 */
const decorate = (item) => ({
  ...item,
  dateText: formatDate(item.created_at),
  spotText: `${item.spot_count} 个安排`,
})

function formatDate(iso) {
  if (!iso) return ''
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return iso
  const pad = (n) => String(n).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`
}

/** van-list 触底加载 */
async function onLoad() {
  if (!isLoggedIn.value) {
    loading.value = false
    finished.value = true
    return
  }
  errorMsg.value = ''
  try {
    const res = await getHistoryList(page.value + 1, PAGE_SIZE)
    const data = res?.data || {}
    const items = (data.items || []).map(decorate)
    trips.value = page.value === 0 ? items : [...trips.value, ...items]
    total.value = data.total || 0
    page.value += 1
    finished.value = trips.value.length >= total.value
  } catch (e) {
    errorMsg.value = e.message || '加载失败'
    finished.value = true
  } finally {
    loading.value = false
    refreshing.value = false
  }
}

/** 下拉刷新：重置分页 */
async function onRefresh() {
  page.value = 0
  finished.value = false
  trips.value = []
  await onLoad()
}

async function onDelete(item) {
  try {
    await showConfirmDialog({
      title: '删除行程',
      message: `确定删除「${item.title || item.city}」吗？删除后不可恢复。`,
    })
  } catch {
    return // 用户取消
  }
  try {
    await deleteHistory(item.id)
    trips.value = trips.value.filter((t) => t.id !== item.id)
    total.value = Math.max(total.value - 1, 0)
    showSuccessToast('已删除')
  } catch (e) {
    showToast(e.message || '删除失败')
  }
}

/** 点击回看：直接把历史行程交给行程页渲染，不再重新规划 */
function openTrip(item) {
  router.push({ path: '/timeline', query: { history_id: String(item.id) } })
}

onMounted(async () => {
  // 首屏先确认登录态（可能本地有 token 但已被服务端作废）
  await ensureInitialized()
})
</script>

<template>
  <div class="page-container">
    <van-nav-bar title="我的行程" left-arrow @click-left="router.back()" />

    <!-- 未登录 -->
    <div v-if="!isLoggedIn" class="empty-state">
      <van-icon name="lock" size="42" color="var(--app-hairline-strong)" />
      <div class="empty-title">登录后查看历史行程</div>
      <div class="empty-desc">你的行程会保存在账号下，换设备也不会丢</div>
      <van-button type="primary" round class="empty-btn" @click="router.push('/login')">
        去登录
      </van-button>
    </div>

    <van-pull-refresh v-else v-model="refreshing" @refresh="onRefresh">
      <div class="page-content">
        <div v-if="total" class="total-hint">共 {{ total }} 条行程</div>

        <van-list
          v-model:loading="loading"
          :finished="finished"
          :error="Boolean(errorMsg)"
          :error-text="errorMsg"
          finished-text="没有更多了"
          @load="onLoad"
        >
          <van-swipe-cell v-for="item in trips" :key="item.id">
            <div class="trip-card" @click="openTrip(item)">
              <div class="trip-head">
                <div class="trip-title">{{ item.title || `${item.city}${item.days}日游` }}</div>
                <van-tag plain type="primary">{{ item.profile }}</van-tag>
              </div>
              <div class="trip-summary">{{ item.summary }}</div>
              <div class="trip-foot">
                <span class="trip-city">
                  <van-icon name="location-o" size="13" />
                  {{ item.origin ? `${item.origin} → ${item.city}` : item.city }}
                </span>
                <span class="trip-spot">{{ item.spotText }}</span>
              </div>
              <div class="trip-tags">
                <van-tag v-for="tag in item.tags" :key="tag" type="success" plain class="tag">
                  {{ tag }}
                </van-tag>
                <van-tag v-if="!item.enhanced" type="warning" plain class="tag">降级模式</van-tag>
              </div>
              <div class="trip-date">{{ item.dateText }}</div>
            </div>
            <template #right>
              <van-button square type="danger" text="删除" class="del-btn" @click="onDelete(item)" />
            </template>
          </van-swipe-cell>

          <div v-if="finished && !trips.length && !loading" class="empty-state inner">
            <van-icon name="notes-o" size="42" color="var(--app-hairline-strong)" />
            <div class="empty-title">还没有保存过行程</div>
            <div class="empty-desc">去首页规划一条，生成后会自动存到这里</div>
            <van-button type="primary" round class="empty-btn" @click="router.push('/')">
              去规划
            </van-button>
          </div>
        </van-list>
      </div>
    </van-pull-refresh>
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
  padding: 12px 16px;
}
.total-hint {
  font-size: 12px;
  color: var(--app-text-tertiary);
  padding: 4px 2px 10px;
}
.trip-card {
  background-color: var(--app-surface);
  border-radius: 10px;
  padding: 14px;
  margin-bottom: 12px;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.05);
}
.trip-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}
.trip-title {
  font-size: 16px;
  font-weight: 600;
  color: var(--app-text);
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.trip-summary {
  margin-top: 8px;
  font-size: 13px;
  color: var(--app-text-secondary);
}
.trip-foot {
  margin-top: 8px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-size: 12px;
  color: var(--app-text-tertiary);
}
.trip-city {
  display: inline-flex;
  align-items: center;
  gap: 3px;
}
.trip-tags {
  margin-top: 10px;
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.tag {
  font-size: 11px;
}
.trip-date {
  margin-top: 10px;
  font-size: 12px;
  color: var(--app-hairline-strong);
}
.del-btn {
  height: 100%;
}
.empty-state {
  padding: 90px 32px 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  text-align: center;
}
.empty-state.inner {
  padding: 60px 12px 20px;
}
.empty-title {
  margin-top: 16px;
  font-size: 15px;
  color: var(--app-text-secondary);
  font-weight: 600;
}
.empty-desc {
  margin-top: 8px;
  font-size: 13px;
  color: var(--app-text-tertiary);
  line-height: 1.6;
}
.empty-btn {
  margin-top: 22px;
  padding: 0 34px;
}
</style>
