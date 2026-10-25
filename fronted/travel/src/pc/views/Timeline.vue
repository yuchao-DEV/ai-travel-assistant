<script setup>
/**
 * 桌面端行程页：左侧行程面板 + 右侧全幅高德地图。
 *
 * 与移动端共用同一套后端与算法输出：
 * - 带 history_id 进入 = 回看历史行程（直接读 /api/history/{id}，不再重新规划）
 * - 否则调 /api/plan 现场规划，成功后落库（本机 + 登录后同步账号）
 *
 * 桌面端的核心优势是把「地图」和「行程详情」并排展示，
 * 不需要移动端那种可拖拽 Bottom Sheet 的分层交互。
 */
import { reactive, computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { Back, Clock, Coin, Location, Promotion, Refresh, Sunny } from '@element-plus/icons-vue'

import { getHistoryDetail, saveHistory, updateHistory } from '@/api/history'
import { getInspiration, getPlan } from '@/api/travel'
import { useAuth } from '@/composables/useAuth'
import { loadAmap, AMAP_VISUAL_ID } from '@/composables/useAmap'
import { saveLocalTrip } from '@/utils/localTrips'
import InspirationCards from '@/components/InspirationCards.vue'
import StorySection from '@/components/StorySection.vue'

const route = useRoute()
const router = useRouter()
const { isLoggedIn, ensureInitialized } = useAuth()

const historyId = computed(() => route.query.history_id || '')
// 退出回看模式后仍要覆盖的那条历史记录 id
const updatingHistoryId = ref('')

const query = reactive({
  // 不设"默认城市"：从历史记录进入时 URL 里只有 history_id，
  // 一旦回退到硬编码城市，灵感流与城际估算就会按错误的城市去查。
  // 城市以行程为准（见 effectiveCity）。
  city: route.query.city || '',
  origin: route.query.origin || '当涂',
  days: route.query.days ? Number(route.query.days) : 3,
  tags: route.query.tags ? route.query.tags.split(',').filter(Boolean) : [],
  profile: route.query.profile || '经典',
  must_go: route.query.must_go ? route.query.must_go.split(',').filter(Boolean) : [],
  avoid: route.query.avoid ? route.query.avoid.split(',').filter(Boolean) : [],
})

const tripId = ref(route.query.trip_id || '')
const itinerary = ref(null)
const tripTitle = ref('')
const loading = ref(false)
const errorMsg = ref('')
// -1 表示"全部天"
const activeDay = ref(-1)
const savedToAccount = ref(false)
// 面板内的标签页：行程 / 灵感
const panelTab = ref('trip')

/* ==================== 旅行灵感流 ====================
 * 与移动端同一套接口（/api/inspiration 不调大模型、不做路线计算），
 * 用来填住行程生成的 20–40 秒空窗期；卡片「＋」同样写入 must_go 硬约束。
 */
const inspirationItems = ref([])
const inspirationCategories = ref([])
const inspirationLoading = ref(false)
const inspirationCity = ref('')
// ---- 灵感章节化（桌面端）----
//
// 每个**真实分类**成一章：照片取该分类下第一张真实照片，
// 正文列出该分类评分最高的几个地点（真实名称与评分）。
// 分类下没有照片就不成章 —— 宁可少一章，也不放占位图。
const inspirationChapters = computed(() => {
  const items = inspirationItems.value || []
  const cats = inspirationCategories.value || []
  if (!items.length || !cats.length) return []
  return cats
    .map((cat, i) => {
      const group = items.filter((it) => it.category === cat.key)
      if (!group.length) return null
      const withPhoto = group.find((it) => Array.isArray(it.photos) && it.photos.length)
      if (!withPhoto) return null
      const top = [...group]
        .sort((a, b) => (b.rating || 0) - (a.rating || 0))
        .slice(0, 3)
      return {
        key: cat.key,
        image: withPhoto.photos[0],
        imageAlt: withPhoto.name || cat.key,
        numeral: String(i + 1).padStart(2, '0'),
        kicker: `${inspirationCity.value || ''} · ${cat.count} 处`,
        title: cat.key,
        text: `评分最高：${top.map((t) => `${t.name}${t.rating ? ` ${t.rating}` : ''}`).join('、')}`,
        cue: '',
      }
    })
    .filter(Boolean)
})

// ---- 「已在行程里」到底指什么 ----
// 旧代码只记录"用户点过加进行程的名字"，初始为空，于是算法已排进行程的地点
// 在灵感页仍然显示「＋ 加进行程」，点下去还会升级成 must_go 硬约束。
// 现在分开三种状态：
//   forcedNames    必去的硬约束（query.must_go）
//   itineraryNames 当前行程里真实存在的地点（算法排的）
//   addedNames     并集，作为卡片按钮置灰依据
const forcedNames = computed(() => [...(query.must_go || [])])

const itineraryNames = computed(() => {
  const s = new Set()
  for (const day of itinerary.value?.days || []) {
    for (const item of day.items || []) {
      if (item?.name) s.add(item.name)
    }
  }
  return s
})

const addedNames = computed(() => {
  const s = new Set(forcedNames.value)
  for (const n of itineraryNames.value) s.add(n)
  return [...s]
})

// 城市信息的唯一真源是**正在显示的行程**：从历史进入时 URL 不带 city，
// 直接读 query 会拿到空值或历史请求里的旧值。
const effectiveCity = computed(() => {
  const fromPlan = (itinerary.value && itinerary.value.city) || ''
  return (fromPlan || query.city || '').trim()
})

async function loadInspiration(cityArg) {
  const city = (cityArg || effectiveCity.value || '').trim()
  if (!city) return
  inspirationCity.value = city
  inspirationLoading.value = true
  try {
    const res = await getInspiration(city, 120)
    const data = res && res.data ? res.data : null
    if (data) {
      inspirationItems.value = data.items || []
      inspirationCategories.value = data.categories || []
    }
  } catch (e) {
    console.warn('灵感流加载失败（不影响行程生成）', e)
  } finally {
    inspirationLoading.value = false
  }
}

/**
 * 加入/移出必去时，若正处于「历史回看」模式，先退出回看。
 *
 * `fetchPlan()` 开头是 `if (historyId.value) { await fetchFromHistory(); return }`，
 * 回看模式下它只重读同一份存档、完全忽略 must_go —— 于是「提示已加入必去，
 * 行程一点没变」。退出回看要去掉 URL 里的 history_id（historyId 是 computed），
 * 并把城市/天数/风格从**已加载的行程**里补回 query，否则新一轮规划缺参数。
 */
const leaveHistoryMode = () => {
  if (!historyId.value) return false
  // 记住要覆盖的那条记录：URL 里的 history_id 马上会被移除
  updatingHistoryId.value = String(historyId.value)
  const days = itinerary.value?.days?.length
  query.city = effectiveCity.value || query.city
  if (days) query.days = days
  if (itinerary.value?.profile) query.profile = itinerary.value.profile
  return true
}

// 把灵感卡片加入必去：写入 must_go 后重新规划（后端里它是硬约束）。
//
// 要**同时**更新两处并处理回看模式：
//   ① query.must_go —— fetchPlan 的入参（只改 URL 的话本次请求仍用旧快照）
//   ② URL —— 刷新/分享后仍然生效
//   ③ 历史回看模式下先退出，否则 fetchPlan 只会重读存档
async function addToPlan(name) {
  if (!name || addedNames.value.includes(name)) return
  if (!query.must_go.includes(name)) query.must_go.push(name)
  const left = leaveHistoryMode()
  await router.replace({
    path: '/timeline',
    query: {
      ...route.query,
      history_id: undefined,
      city: query.city || undefined,
      days: query.days || undefined,
      profile: query.profile || undefined,
      must_go: query.must_go.join(','),
    },
  })
  ElMessage.success(left ? `已加入必去并重新规划：${name}` : `已加入必去：${name}`)
  await fetchPlan()
}

async function removeFromPlan(name) {
  query.must_go = query.must_go.filter((n) => n !== name)
  const left = leaveHistoryMode()
  await router.replace({
    path: '/timeline',
    query: {
      ...route.query,
      history_id: undefined,
      city: query.city || undefined,
      days: query.days || undefined,
      profile: query.profile || undefined,
      must_go: query.must_go.length ? query.must_go.join(',') : undefined,
    },
  })
  ElMessage.info(left ? `已移出必去并重新规划：${name}` : `已移出必去：${name}`)
  await fetchPlan()
}

const DAY_COLORS = ['var(--app-brand)', 'var(--app-brand-hover)', 'var(--app-warning)', 'var(--app-brand-hover)', 'var(--app-danger)', 'var(--app-accent)', 'var(--app-accent)']

const typeLabel = (t) => ({ spot: '景点', restaurant: '餐饮', hotel: '住宿' })[t] || '景点'
const typeTagType = (t) =>
  ({ spot: 'primary', restaurant: 'danger', hotel: 'warning' })[t] || 'primary'
// 地图 Marker 的 content 是 HTML，POI 名称需转义
const escapeHtml = (value) => String(value == null ? '' : value).replace(
  /[&<>"']/g,
  (ch) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch])
)
// 景点图：优先用高德真实照片，没有则用首字占位（不再用生成图冒充实景）
const itemPhoto = (item) => (item.photos && item.photos.length ? item.photos[0] : '')
const photoFallbackText = (name) => (name || '景').slice(0, 1)

const totalItems = computed(() => {
  if (!itinerary.value?.days) return 0
  return itinerary.value.days.reduce((sum, d) => sum + (d.items?.length || 0), 0)
})

const visibleDays = computed(() => {
  if (!itinerary.value?.days) return []
  if (activeDay.value < 0) return itinerary.value.days
  return [itinerary.value.days[activeDay.value]].filter(Boolean)
})

/* ==================== 地图 ==================== */
const mapEl = ref(null)
const mapLoading = ref(true)
const mapError = ref('')
let mapInstance = null
let overlays = []

function clearOverlays() {
  if (!mapInstance) return
  if (overlays.length) mapInstance.remove(overlays)
  overlays = []
}

/** 按当前选中的天重绘覆盖物（-1 = 全部天） */
function renderMap() {
  if (!mapInstance || !window.AMap || !itinerary.value?.days) return
  clearOverlays()

  const days = itinerary.value.days
  const indexes = activeDay.value < 0 ? days.map((_, i) => i) : [activeDay.value]

  indexes.forEach((di) => {
    const day = days[di]
    const items = day?.items || []
    if (!items.length) return
    const color = DAY_COLORS[di % DAY_COLORS.length]
    const path = []

    items.forEach((item, ii) => {
      path.push([item.lng, item.lat])
      const marker = new window.AMap.Marker({
        position: [item.lng, item.lat],
        offset: new window.AMap.Pixel(-14, -14),
        content: `<div class="pc-marker" style="background:${color}">${ii + 1}</div>`,
        // POI 名称 label，用高德原文
        label: {
          content: `<div class="pc-marker-label">${escapeHtml(item.name)}</div>`,
          direction: 'right',
        },
        title: item.name,
        zIndex: 100 + ii,
      })
      marker.on('click', () => {
        ElMessage({
          message: `${di + 1}日 · ${item.start}-${item.end} ${item.name}（${typeLabel(item.type)}）`,
          duration: 2600,
        })
      })
      overlays.push(marker)
    })

    if (path.length > 1) {
      overlays.push(
        new window.AMap.Polyline({
          path,
          strokeColor: color,
          strokeWeight: 5,
          strokeOpacity: 0.85,
          lineJoin: 'round',
          showDir: true,
        })
      )
    }
  })

  if (overlays.length) {
    mapInstance.add(overlays)
    mapInstance.setFitView(overlays, false, [90, 90, 90, 90])
  }
}

async function initMap() {
  if (!mapEl.value) return
  mapLoading.value = true
  mapError.value = ''
  try {
    const AMap = await loadAmap()
    mapInstance = new AMap.Map(mapEl.value, {
      zoom: 11,
      center: [120.15, 30.25],
      viewMode: '2D',
      mapStyle: 'amap://styles/normal',
    })
    // 可视化图层（配置缺失时只告警，不影响基础地图）
    try {
      if (AMAP_VISUAL_ID) {
        const visual = new AMap.Visual('amap://visual/' + AMAP_VISUAL_ID, mapInstance)
        if (visual && typeof visual.setOptions === 'function') visual.setOptions({ zooms: [3, 20] })
      }
    } catch (e) {
      console.warn('可视化图层加载失败（不影响基础地图）', e)
    }
    mapLoading.value = false
    await nextTick()
    renderMap()
  } catch (e) {
    console.error('地图初始化失败', e)
    mapLoading.value = false
    mapError.value = '地图加载失败：请检查高德 JS Key 与域名白名单配置'
  }
}

/* ==================== 行程数据 ==================== */

/** 规划成功后落库：本机留档 + 登录用户同步账号 */
async function persistTrip() {
  if (!itinerary.value) return
  if (!tripId.value) tripId.value = 'pc-' + Date.now()
  const request = { ...query }
  const title = tripTitle.value || `${query.origin}出发·${query.city}${query.days}日游`
  if (!tripTitle.value) tripTitle.value = title

  saveLocalTrip({
    id: tripId.value,
    title,
    origin: query.origin,
    city: query.city,
    days: query.days,
    request,
    itinerary: itinerary.value,
    createdAt: new Date().toISOString(),
  })

  if (!isLoggedIn.value) return
  try {
    // 历史回看模式下的改动：覆盖原记录，而不是新增一条
    if (updatingHistoryId.value) {
      try {
        await updateHistory(updatingHistoryId.value, {
          client_id: tripId.value,
          title,
          origin: query.origin,
          request,
          itinerary: itinerary.value,
        })
        savedToAccount.value = true
        return
      } catch (err) {
        // 记录已被删除（404）时不要静默丢改动：清掉覆盖目标，落到下面的新增
        console.warn('覆盖历史行程失败，改为新增一条', err)
        updatingHistoryId.value = ''
      }
    }
    await saveHistory({
      client_id: tripId.value,
      title,
      origin: query.origin,
      request,
      itinerary: itinerary.value,
    })
    savedToAccount.value = true
  } catch (e) {
    console.warn('行程同步到账号失败', e)
  }
}

async function fetchFromHistory() {
  const res = await getHistoryDetail(historyId.value)
  const data = res && res.data
  if (!data || !data.itinerary) throw new Error('历史行程不存在或数据已损坏')
  itinerary.value = data.itinerary
  tripId.value = data.client_id || `history-${data.id}`
  tripTitle.value = data.title || data.itinerary.description || ''
  savedToAccount.value = true
  if (data.request) Object.assign(query, data.request)
}

async function fetchPlan() {
  loading.value = true
  errorMsg.value = ''
  savedToAccount.value = false
  await ensureInitialized()
  try {
    if (historyId.value) {
      await fetchFromHistory()
    } else if (!query.city) {
      // 没有目的地就不要发请求：以前会退回到硬编码的城市，静默给出错误城市的行程
      errorMsg.value = '缺少目的地，请回首页选择目的地后再规划'
      return
    } else {
      const res = await getPlan(query)
      if (res && res.code === 200 && res.data) itinerary.value = res.data
      else if (res && res.data) itinerary.value = res.data
      else errorMsg.value = (res && res.message) || '生成行程失败'
      if (itinerary.value) await persistTrip()
    }
  } catch (error) {
    errorMsg.value =
      historyId.value && (error.code === 401 || error.status === 401)
        ? '登录后才能查看这条历史行程'
        : error.message || '网络异常，请确认后端服务已启动'
    console.error('行程规划请求失败:', error)
  } finally {
    loading.value = false
    await nextTick()
    renderMap()
    // 行程就绪后按真实城市校准灵感流（从历史进入时 URL 不带 city）
    const shownCity = ((itinerary.value && itinerary.value.city) || '').trim()
    if (shownCity && shownCity !== inspirationCity.value) loadInspiration(shownCity)
  }
}

function selectDay(index) {
  activeDay.value = index
}

onMounted(async () => {
  // 灵感流与规划并行发起：它不依赖规划结果，能先回来填住等待期
  loadInspiration()
  await initMap()
  await fetchPlan()
})

onBeforeUnmount(() => {
  clearOverlays()
  if (mapInstance) {
    mapInstance.destroy()
    mapInstance = null
  }
})

watch(activeDay, () => renderMap())

// 从「灵感」切回「行程」时，地图容器刚从 display:none 恢复，
// AMap 记的还是 0×0 尺寸 —— 必须 resize 一次，否则一片灰。
watch(panelTab, async (tab) => {
  if (tab !== 'trip') return
  await nextTick()
  if (mapInstance) mapInstance.resize()
  renderMap()
})
</script>

<template>
  <div class="timeline-layout">
    <!-- 左：行程面板 -->
    <aside class="panel">
      <div class="panel-head">
        <el-button link :icon="Back" @click="router.push('/')">返回</el-button>
        <el-button
          link
          type="primary"
          :icon="Refresh"
          :loading="loading"
          @click="fetchPlan"
        >
          重新规划
        </el-button>
      </div>

      <div v-if="loading" class="panel-loading">
        <el-skeleton :rows="4" animated />
        <div class="pc-muted" style="margin-top: 14px; text-align: center">
          算法引擎规划中：选点 · 聚类 · 路径优化 · 时间排程
        </div>
        <div class="pc-muted" style="margin-top: 6px; text-align: center">
          生成需要一点时间，先看看 {{ effectiveCity || '目的地' }} 值得去的地方
        </div>
        <!-- 等待期用真实地点内容填住，而不是只有一个骨架屏 -->
        <InspirationCards
          :items="inspirationItems"
          :categories="inspirationCategories"
          :added="addedNames"
          :forced="forcedNames"
          :loading="inspirationLoading"
          @add="addToPlan"
        />
      </div>

      <el-empty v-else-if="errorMsg" :description="errorMsg">
        <el-button type="primary" @click="fetchPlan">重新加载</el-button>
      </el-empty>

      <template v-else-if="itinerary">
        <div class="trip-header">
          <h2 class="trip-title">{{ tripTitle }}</h2>
          <div class="trip-stats">
            <span><el-icon><Location /></el-icon>{{ itinerary.city }}</span>
            <span><el-icon><Clock /></el-icon>{{ itinerary.days?.length }} 天 / {{ totalItems }} 个安排</span>
          </div>
          <div class="trip-tags">
            <el-tag size="small" effect="plain" type="primary">{{ itinerary.profile }}</el-tag>
            <el-tag
              v-for="w in itinerary.weather || []"
              :key="w"
              size="small"
              effect="plain"
              type="info"
            >
              <el-icon><Sunny /></el-icon>{{ w }}
            </el-tag>
            <el-tag v-if="savedToAccount" size="small" effect="plain" type="success">
              已存入我的行程
            </el-tag>
          </div>
          <p v-if="itinerary.description" class="trip-desc">{{ itinerary.description }}</p>
          <!-- AI 生成内容的标注（UX 规范要求"明确标注 AI 生成"）。
               这里同时说明分工：文案是 AI 写的，行程结构是算法排的——
               这既满足标注要求，也正好是项目的差异点，不该藏起来。 -->
          <div v-if="itinerary.description" class="ai-disclosure">
            <el-tag size="small" effect="plain" type="warning">AI 生成</el-tag>
            <span>以上介绍文字由大模型撰写；景点、顺序、时间由算法引擎排定</span>
          </div>
        </div>

        <!-- 行程 / 灵感 两个标签页，与移动端结构保持一致 -->
        <el-tabs v-model="panelTab" class="panel-tabs">
          <el-tab-pane label="行程" name="trip">
            <!-- 天选择 -->
            <div class="day-switch">
              <el-radio-group v-model="activeDay" size="small">
                <el-radio-button :value="-1">全部</el-radio-button>
                <el-radio-button v-for="(d, i) in itinerary.days" :key="i" :value="i">
                  D{{ i + 1 }}
                </el-radio-button>
              </el-radio-group>
            </div>

            <div class="day-list">
              <div v-for="(day, di) in visibleDays" :key="di" class="day-block">
                <div class="day-head">
                  <span class="day-name">
                    {{ activeDay < 0 ? `第 ${di + 1} 天` : `第 ${activeDay + 1} 天` }}
                  </span>
                  <span class="day-cost">¥{{ day.daily_cost }}</span>
                </div>
                <p v-if="day.daily_summary" class="day-summary">{{ day.daily_summary }}</p>

                <div v-for="(item, ii) in day.items" :key="ii" class="item-card">
                  <div class="item-top">
                    <div class="item-thumb">
                      <img v-if="itemPhoto(item)" :src="itemPhoto(item)" alt="" loading="lazy" />
                      <div v-else class="thumb-fallback">{{ photoFallbackText(item.name) }}</div>
                    </div>
                    <span class="item-index" :style="{ background: DAY_COLORS[(activeDay < 0 ? di : activeDay) % DAY_COLORS.length] }">
                      {{ ii + 1 }}
                    </span>
                    <span class="item-name">{{ item.name }}</span>
                    <el-tag size="small" :type="typeTagType(item.type)" effect="plain">
                      {{ typeLabel(item.type) }}
                    </el-tag>
                  </div>
                  <div class="item-meta">
                    <span>{{ item.start }} - {{ item.end }}</span>
                    <span>
                      ¥{{ item.cost }}
                      <!-- 诚实标注：高德只对餐饮返回真实人均消费，景区门票为参考估算 -->
                      <!-- 四档来源含义不同：
                           real=高德真实人均；third_party=知识库真实票价；
                           average=仅有优待票/付费服务时按均价算；
                           reserved=无数据 → **不显示数字**，只显示「门票待确认」+ 备注 -->
                      <span v-if="item.cost_source === 'real'" class="cost-src real">高德人均</span>
                      <span v-else-if="item.cost_source === 'third_party' && item.cost > 0" class="cost-src real">票价</span>
                      <span v-else-if="item.cost_source === 'third_party'" class="cost-src real">免费</span>
                      <span v-else-if="item.cost_source === 'average'" class="cost-src est">均价</span>
                      <span v-else class="cost-src est">门票待确认</span>
                      <span v-if="item.cost_note" class="cost-note">{{ item.cost_note }}</span>
                    </span>
                    <!-- score 是算法加权总分（兴趣 + 口碑 − 绕路），取值范围 0–1。
                         直接显示 "0.36" 会让用户以为是满分 1 分的评分、觉得这地方很差；
                         换算成百分比并说清是什么分，才不至于误导。 -->
                    <span class="score">匹配度 {{ Math.round((item.score || 0) * 100) }}%</span>
                  </div>
                  <div v-if="item.open_hours" class="item-hours">
                    <el-icon><Clock /></el-icon>{{ item.open_hours }}
                  </div>

                  <!-- 可解释评分：亮出算法的**三维**分量。
                       费用维度已随后端 ScoreBreakdown 一起删除（预算系统移除），
                       这里若继续渲染 cost，会画出一条永远为 0 的空进度条——
                       既占地方又让人以为"这个点不花钱/没数据"。 -->
                  <div class="breakdown">
                    <div v-for="(label, key) in { interest: '兴趣', rating: '口碑', detour: '绕路' }" :key="key" class="bd-item">
                      <span class="bd-label">{{ label }}</span>
                      <el-progress
                        :percentage="Math.round((item.score_breakdown?.[key] || 0) * 100)"
                        :show-text="false"
                        :stroke-width="5"
                        :color="key === 'detour' ? 'var(--app-warning)' : 'var(--app-brand)'"
                      />
                    </div>
                  </div>

                  <div v-if="item.transport" class="item-transport">
                    <el-icon><Promotion /></el-icon>
                    前往下一站：{{ item.transport.mode }} · {{ item.transport.duration }} 分钟 ·
                    {{ item.transport.distance }} km
                  </div>
                </div>
              </div>
            </div>
          </el-tab-pane>

          <el-tab-pane label="灵感" name="inspiration">
            <InspirationCards
              :items="inspirationItems"
              :categories="inspirationCategories"
              :added="addedNames"
          :forced="forcedNames"
              :loading="inspirationLoading"
              @add="addToPlan"
            />
            <!-- 已加入必去的地点：可一键移出（必须项要能反悔） -->
            <div v-if="forcedNames.length" class="added-list">
              <div class="added-title">已加入必去（{{ forcedNames.length }}）</div>
              <div v-for="name in forcedNames" :key="name" class="added-row">
                <span class="added-name">{{ name }}</span>
                <el-button link type="info" size="small" @click="removeFromPlan(name)">移出</el-button>
              </div>
              <div class="added-note">「必去」在算法里是硬约束：不会被预算或评分排序挤掉。</div>
            </div>
          </el-tab-pane>
        </el-tabs>
      </template>
    </aside>

    <!-- 右：地图 ↔ 灵感章节（按 tab 互斥）
         理由：灵感是"看有什么值得去"，不需要路线地图；
         而全幅叙事章节塞进左侧 300px 窄栏就退化成小卡片了。 -->
    <section
      v-if="panelTab === 'inspiration' && inspirationChapters.length"
      class="map-wrap insp-pane"
    >
      <StorySection
        v-for="ch in inspirationChapters"
        :key="ch.key"
        :image="ch.image"
        :image-alt="ch.imageAlt"
        :numeral="ch.numeral"
        :kicker="ch.kicker"
        :title="ch.title"
        :text="ch.text"
        variant="light"
        class="insp-chapter"
      />
    </section>
    <!-- 地图容器**必须一直挂在 DOM 上**：AMap 实例绑定的是这个 div，
         用 v-if 把它卸载掉，切回「行程」时拿到的是新节点，
         旧实例失效 → 地图空白。所以这里用 v-show 只隐藏不销毁。 -->
    <section v-show="!(panelTab === 'inspiration' && inspirationChapters.length)"
             class="map-wrap">
      <div ref="mapEl" class="map-canvas"></div>
      <div v-if="mapLoading" class="map-mask">
        <el-icon class="is-loading" :size="26"><Refresh /></el-icon>
        <span>地图加载中…</span>
      </div>
      <el-alert
        v-if="mapError"
        class="map-alert"
        type="warning"
        :title="mapError"
        :closable="false"
        show-icon
      />
    </section>
  </div>
</template>

<style scoped>
.timeline-layout {
  display: grid;
  grid-template-columns: 460px minmax(0, 1fr);
  height: 100%;
  overflow: hidden;
}
.panel {
  background: var(--app-surface);
  border-right: 1px solid var(--app-border);
  padding: 14px 18px 20px;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
}
.panel-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 10px;
}
.panel-loading {
  padding-top: 20px;
}
.trip-header {
  padding-bottom: 14px;
  border-bottom: 1px solid var(--app-bg-subtle);
}
.trip-title {
  margin: 0;
  font-size: 19px;
  font-weight: 700;
  line-height: 1.4;
}
.trip-stats {
  margin-top: 10px;
  display: flex;
  flex-wrap: wrap;
  gap: 16px;
  font-size: 13px;
  color: var(--app-text-secondary);
}
.trip-stats span {
  display: inline-flex;
  align-items: center;
  gap: 5px;
}
.trip-tags {
  margin-top: 10px;
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.trip-desc {
  margin: 10px 0 0;
  font-size: 13px;
  line-height: 1.7;
  color: var(--app-text-secondary);
}
.day-switch {
  padding: 4px 0 6px;
}
/* 行程 / 灵感 标签页 */
.panel-tabs {
  margin-top: 6px;
}
.panel-tabs :deep(.el-tabs__header) {
  margin-bottom: 10px;
}
.panel-tabs :deep(.el-tabs__content) {
  overflow: visible;
}
/* 灵感页里"已加入必去"的清单 */
.added-list {
  margin-top: 16px;
  padding: 14px;
  border: 1px solid var(--app-surface-muted);
  border-radius: 10px;
}
.added-title { font-size: 13px; font-weight: 600; color: var(--app-text); margin-bottom: 10px; }
.added-row {
  display: flex; align-items: center; justify-content: space-between;
  gap: 10px; padding: 6px 0; border-top: 1px solid var(--app-bg-subtle);
}
.added-name { font-size: 13px; color: var(--app-text); }
.added-note {
  margin-top: 10px; padding-top: 10px;
  border-top: 1px dashed var(--app-hairline);
  font-size: 11px; color: var(--app-text-secondary); line-height: 1.6;
}
.day-list {
  display: flex;
  flex-direction: column;
  gap: 18px;
}
.day-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  margin-bottom: 4px;
}
.day-name {
  font-size: 15px;
  font-weight: 600;
}
.day-cost {
  font-size: 13px;
  color: var(--app-danger);
  font-weight: 600;
}
.day-summary {
  margin: 0 0 10px;  font-size: 12.5px;
  color: var(--app-text-secondary);
  line-height: 1.7;
}
.item-card {
  border: 1px solid var(--app-bg-subtle);
  border-radius: 8px;
  padding: 12px;
  margin-bottom: 10px;
  transition: all 0.2s;
}
.item-card:hover {
  border-color: var(--app-brand-soft);
  box-shadow: 0 2px 10px rgba(var(--app-brand-rgb), 0.08);
}
.item-top {
  display: flex;
  align-items: center;
  gap: 8px;
}
.item-thumb {
  flex-shrink: 0;
  width: 34px;
  height: 34px;
  border-radius: 6px;
  overflow: hidden;
  background: var(--app-surface-muted);
}
.item-thumb img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}
/* 没有真实照片时的首字占位，避免用生成图冒充实景 */
.thumb-fallback {
  width: 100%;
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 15px;
  font-weight: 700;
  color: var(--app-surface);
  background: linear-gradient(135deg, var(--app-accent-soft), var(--app-accent-bright));
}
.item-index {
  flex-shrink: 0;
  width: 20px;
  height: 20px;
  border-radius: 50%;
  color: var(--app-surface);
  font-size: 12px;
  display: flex;
  align-items: center;
  justify-content: center;
}
.item-name {
  flex: 1;
  font-size: 14px;
  font-weight: 600;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.item-meta {
  margin-top: 8px;
  display: flex;
  gap: 14px;
  font-size: 12px;
  color: var(--app-text-secondary);
  align-items: center;
  flex-wrap: wrap;
}
.cost-src {
  margin-left: 5px;
  padding: 0 5px;
  border-radius: 3px;
  font-size: 10px;
  line-height: 16px;
}
.cost-src.real {
  color: var(--app-brand-hover);
  background: var(--app-success-soft);
}
.cost-src.est {
  color: var(--app-warning);
  background: var(--app-warning-soft);
}
.item-hours {
  margin-top: 6px;
  font-size: 12px;
  color: var(--app-text-secondary);
  display: flex;
  align-items: center;
  gap: 5px;
  line-height: 1.5;
}
.score {
  color: var(--app-brand);
  font-weight: 600;
}
.breakdown {
  margin-top: 10px;
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 6px 14px;
}
.bd-item {
  display: flex;
  align-items: center;
  gap: 8px;
}
.bd-label {
  font-size: 11px;
  color: var(--app-text-secondary);
  width: 26px;
  flex-shrink: 0;
}
.bd-item :deep(.el-progress) {
  flex: 1;
}
.item-transport {
  margin-top: 10px;
  padding-top: 8px;
  border-top: 1px dashed var(--app-bg-subtle);
  font-size: 12px;
  color: var(--app-text-secondary);
  display: flex;
  align-items: center;
  gap: 6px;
}
.map-wrap {
  position: relative;
  height: 100%;
  background: var(--app-surface-muted);
}
.map-canvas {
  width: 100%;
  height: 100%;
}
.map-mask {
  position: absolute;
  inset: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 10px;
  background: rgba(238, 243, 250, 0.9);
  color: var(--app-text-secondary);
  font-size: 13px;
  z-index: 5;
}
.map-alert {
  position: absolute;
  left: 16px;
  top: 16px;
  width: 340px;
  z-index: 6;
}
</style>

<style>
/* 地图 Marker 是 AMap 注入的 DOM，不能写在 scoped 里 */
.pc-marker {
  width: 28px;
  height: 28px;
  border-radius: 50%;
  color: var(--app-surface);
  font-size: 13px;
  font-weight: 600;
  display: flex;
  align-items: center;
  justify-content: center;
  border: 2px solid var(--app-surface);
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.25);
  cursor: pointer;
}
/* POI 名称 label，用高德原文 */
.pc-marker-label {
  background: var(--app-surface);
  border-radius: 4px;
  padding: 2px 7px;
  font-size: 12px;
  color: var(--app-text);
  white-space: nowrap;
  box-shadow: 0 1px 5px rgba(0, 0, 0, 0.18);
}

.cost-note{display:block;margin-top:4px;font-size:11px;color:var(--app-text-tertiary);line-height:1.6}
/* AI 生成内容标注 */
.ai-disclosure {
  display: flex; align-items: center; gap: 8px; flex-wrap: wrap;
  margin-top: 8px; padding: 6px 10px; font-size: 11.5px; color: var(--app-warning);
  background: var(--app-warning-soft); border-radius: var(--app-radius-xs);
  line-height: 1.6;
}

/* 右侧灵感章节区：铺满整个右半屏，纵向滚动。
   注意 .timeline-layout 是 height:100% + overflow:hidden，
   所以这里必须自带 overflow-y:auto 与 min-height:0，
   否则章节会被父容器裁掉且**没有滚动条**。 */
.map-wrap.insp-pane {
  display: block;
  overflow-y: auto;
  /* grid/flex 子项默认 min-height:auto，会把 auto 滚动撑失效 */
  min-height: 0;
  padding: 0;
  background: var(--app-bg);
  scrollbar-gutter: stable;
}
.insp-chapter { min-height: 0; margin: 0 0 18px; }
.insp-chapter:last-child { margin-bottom: 0; }
.insp-pane { padding: 18px; }
.insp-chapter :deep(.story-body) { padding: 24px 28px 28px; }
</style>
