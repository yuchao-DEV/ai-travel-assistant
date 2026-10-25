<script setup>
import { reactive, ref, computed, onMounted, onBeforeUnmount, watch, nextTick } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { showToast } from 'vant'
import { getInspiration, getIntercity, getPlan, getRouteThumbnail } from '@/api/travel'
import { getHistoryDetail, saveHistory, updateHistory } from '@/api/history'
import { useAuth } from '@/composables/useAuth'
import { loadLocalTrips, saveLocalTrip } from '@/utils/localTrips'
import { loadAmap, AMAP_VISUAL_ID } from '@/composables/useAmap'
import InspirationCards from '@/components/InspirationCards.vue'
import StorySection from '@/components/StorySection.vue'

const route = useRoute()
// ---- 叙事章节：**每天一章** ----
//
// 每章用当天第一张真实照片（高德 POI.photos）+ 当天导语（LLM daily_summary）+ 两位序号。
// 标题与正文从导语里拆：第一句当衬线大标题，余下当正文——只用真实文本，不另造文案。
// 当天没有照片就**不生成这一章**，绝不放占位图充数。
function splitSummary(summary) {
  const raw = String(summary || '').trim()
  if (!raw) return { title: '', text: '' }
  const parts = raw.split(/[，。；]/).filter((s) => s.trim())
  if (parts.length <= 1) return { title: raw, text: '' }
  return {
    title: parts[0],
    text: parts.slice(1).join('，') + '。',
  }
}

const storyChapters = computed(() => {
  const it = itinerary.value
  if (!it || !Array.isArray(it.days) || !it.days.length) return []
  const chapters = it.days.map((day, i) => {
    const items = day.items || []
    const withPhoto = items.find((x) => Array.isArray(x.photos) && x.photos.length)
    if (!withPhoto) return null          // 没照片就不成章
    const spots = items.filter((x) => x.type === 'spot')
    const { title, text } = splitSummary(day.daily_summary)
    return {
      key: day.date || `ch-${i}`,
      image: withPhoto.photos[0],
      imageAlt: withPhoto.name || '',
      numeral: String(i + 1).padStart(2, '0'),
      kicker: `${it.city || ''} · 第 ${i + 1} 天 · ${spots.length} 处`,
      title,
      text,
      // 最后一章才提示"向下看详细安排"，否则连续三章都在喊同一句
      cue: i === it.days.length - 1 ? '向下看详细安排' : '',
    }
  })
  return chapters.filter(Boolean)
})

/** 「跳过叙述」：直接滚到行程列表（叙事对视差不适的用户是障碍） */
function scrollToTrip() {
  const el = document.querySelector('.timeline-tabs, .trip-panel, .timeline-body')
  if (el && el.scrollIntoView) el.scrollIntoView({ behavior: 'smooth', block: 'start' })
}
const router = useRouter()
const { isLoggedIn, ensureInitialized } = useAuth()

// 带 history_id 进来 = 回看历史行程，直接读后端，不再重新规划
const historyId = computed(() => route.query.history_id || '')
// 退出回看模式后仍要覆盖的那条历史记录 id
const updatingHistoryId = ref('')

const query = reactive({
  // 不设"默认城市"：从「我的行程」回看时 URL 里只有 history_id，
  // 一旦回退到某个硬编码城市，灵感流与城际估算就会按错误的城市去查。
  // 城市以行程为准（见 effectiveCity），此处留空即可。
  city: route.query.city || '',
  origin: route.query.origin || '当涂',
  days: route.query.days ? Number(route.query.days) : 3,
  tags: route.query.tags ? route.query.tags.split(',').filter(Boolean) : [],
  profile: route.query.profile || '经典',
  must_go: route.query.must_go ? route.query.must_go.split(',').filter(Boolean) : [],
  avoid: route.query.avoid ? route.query.avoid.split(',').filter(Boolean) : [],
})

// 本次行程在本机的记录 id：首页提交时生成并透传，回看模式沿用历史记录里的 client_id
const tripId = ref(route.query.trip_id || '')

const loading = ref(false)
const isError = ref(false)
const errorMsg = ref('')
const itinerary = ref(null)
const activeTab = ref('details')
const activeDayIndex = ref(-1)
const editingTitle = ref(false)
const tripTitle = ref('')
const chatInput = ref('')

const typeLabel = (type) => ({spot:'景点',restaurant:'餐饮',hotel:'住宿'})[type] || type || '景点'
const typeColor = (type) => ({spot:'var(--app-brand)',restaurant:'var(--app-danger)',hotel:'var(--app-accent)'})[type] || 'var(--app-brand)'
const visitDuration = (item) => { if(!item.start||!item.end) return '建议玩1-3小时'; const [sh,sm]=item.start.split(':').map(Number); const [eh,em]=item.end.split(':').map(Number); const mins=(eh*60+em)-(sh*60+sm); if(mins<=0) return '建议玩1-3小时'; const h=Math.floor(mins/60); if(h>0) return '建议玩'+h+'-'+(h+1)+'小时'; return '建议玩'+Math.ceil(mins/30)*30+'分钟' }
// 营业时间直接用高德返回的真实值；没有时才退回"时间以景区公告为准"
const openHours = (item) => item.open_hours || '营业时间以景区公告为准'
// 费用来源四档，必须区分清楚：
//   real        = 高德返回的真实人均消费（餐饮）
//   third_party = 知识库语料里的真实成人票价
//   average     = 只有优待票或只有付费服务时按均价算（备注里说明是什么的均价）
//   reserved    = 没有任何数据，**不显示价格**，只显示「门票待确认」+ 备注
const COST_LABELS = { real: '人均', third_party: '票价', average: '均价' }
const costLabel = (item) => COST_LABELS[item.cost_source] || ''
// reserved 时不要显示数字——那会让用户以为是真的票价
const showCost = (item) => item.cost_source !== 'reserved' && item.cost > 0
const costText = (item) => {
  if (item.cost_source === 'reserved') return '门票待确认'
  if (item.cost_source === 'third_party' && item.cost === 0) return '免费'
  return `${costLabel(item)}¥${item.cost}`
}
// 景点图片：优先用高德返回的真实照片；没有真实照片时用首字占位，
// 不再用「文本生成图」冒充实景照片（那本身也是一种编造）
const itemPhoto = (item) => (item.photos && item.photos.length ? item.photos[0] : '')
const photoFallbackText = (name) => (name || '景').slice(0, 1)

// 地图 Marker 的 content 是 HTML 字符串，POI 名称来自高德需转义，避免破坏结构
const escapeHtml = (value) => String(value == null ? '' : value).replace(
  /[&<>"']/g,
  (ch) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch])
)

// 按时间把当天条目分成 上午 / 下午 / 晚上 三段
const timeSections = (items) => {
  const groups = { '上午': [], '下午': [], '晚上': [] }
  for (const item of items || []) {
    if (item.type === 'hotel') continue
    const hour = Number((item.start || '09:00').split(':')[0])
    if (hour < 12) groups['上午'].push(item)
    else if (hour < 18) groups['下午'].push(item)
    else groups['晚上'].push(item)
  }
  return Object.keys(groups)
    .filter((label) => groups[label].length)
    .map((label) => ({ label, list: groups[label] }))
}

// 总览统计 chip：时长 / 地点数 / 气温区间 / 花费
const overviewStats = computed(() => {
  if (!itinerary.value) return []
  const days = itinerary.value.days || []
  const itemCount = days.reduce((n, d) => n + (d.items || []).filter(i => i.type !== 'hotel').length, 0)
  const stats = [
    { label: '时长', value: `${days.length} 天` },
    { label: '安排', value: `${itemCount} 个地点` },
  ]
  const temps = []
  for (const w of itinerary.value.weather || []) {
    const found = String(w).match(/(-?\d+)\s*°\s*\/\s*(-?\d+)\s*°/)
    if (found) temps.push(Number(found[1]), Number(found[2]))
  }
  if (temps.length) stats.push({ label: '气温', value: `${Math.min(...temps)}~${Math.max(...temps)}°C` })
  return stats
})

/* ==================== 旅行灵感流 ====================
 * 规划需要 20–40 秒（LLM 润色 + 路径优化 + 排程），这段空窗期用真实地点内容填住，
 * 而不是让用户盯着一个转圈。灵感接口不调大模型，通常 1–3 秒返回。
 */
const inspirationItems = ref([])
const inspirationCategories = ref([])
const inspirationLoading = ref(false)
const inspirationCity = ref('')      // 当前灵感数据对应的城市

// ---- 「已在行程里」到底指什么 ----
//
// 旧代码用一个 ref 记录"用户点过加进行程的名字"，初始为空。
// 于是算法已经排进行程的地点（例如 Day 1 的天坛公园），在灵感页仍然显示
// 「＋ 加进行程」——点下去还会把它升级成 must_go 硬约束并触发一轮重规划。
// 用户会觉得系统不知道自己已经排过了。
//
// 现在把三种状态分清楚：
//   forcedNames    「已加入必去」——用户强制要求，硬约束（query.must_go）
//   itineraryNames 「已在行程中」——当前行程里真实存在的地点（算法排的）
//   addedNames     两者并集，作为灵感卡片按钮的置灰依据
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

// 城市信息的唯一真源是**正在显示的行程**。
// 从「我的行程」回看时 URL 里只有 history_id、没有 city，
// 若直接用 query 就会退回到硬编码的默认值（杭州），
// 于是厦门的历史行程配上杭州的灵感流 / 城际估算。
const effectiveCity = computed(() => {
  const fromPlan = (itinerary.value && itinerary.value.city) || ''
  return (fromPlan || query.city || '').trim()
})

// 预算说明：行程未就绪时为 ''（模板里直接访问 itinerary.xxx 会在渲染期抛错）

const loadInspiration = async (cityArg) => {
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

// 把灵感卡片加入必去：写入 must_go 后重新规划。
// 后端的 must_go 是硬约束——不受单项超预算过滤、不受每日 5 个名额限制。
//
// ## 这里曾经是坏的
//
// 旧实现只 router.replace 了 URL，然后调 fetchPlan()；而 fetchPlan 读的是
// 模块级快照 `query`（在 setup 时从 route.query 生成一次），**新地点从未进入
// 这次请求**。表现就是：卡片显示「已加入必去」，行程却一点没变，
// 只有手动刷新页面才生效——典型的「界面声称的 ≠ 系统实际做的」。
//
// 现在同时更新两处：
//   ① query.must_go（fetchPlan 的入参）
//   ② URL（刷新/分享后仍然生效）
/**
 * 加入/移出必去时，若当前处于「历史回看」模式，先退出回看。
 *
 * 为什么必须退出：`fetchPlan()` 第一件事是
 *
 *     if (historyId.value) { await fetchFromHistory(); return }
 *
 * 回看模式下它只会**重读同一份存档**，完全忽略 must_go —— 表现就是
 * 「提示说已加入必去，行程一点没变」。用户看到的正是这个。
 *
 * 退出回看要同时补齐三件事，否则新一轮规划会缺参数：
 *   - 去掉 URL 里的 history_id（historyId 是 computed，只能从 URL 去掉）
 *   - 把城市从**已加载的行程**里补回 query（回看 URL 通常不带 city）
 *   - 天数/风格同理，以行程实际值为准
 *
 * @returns {boolean} 是否发生过"退出回看"
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

// 把灵感卡片加入必去：写入 must_go 后重新规划。
// 后端的 must_go 是硬约束——不受单项超预算过滤、不受每日 5 个名额限制。
//
// ## 这里曾经是坏的（两次）
//
// ① 旧实现只 router.replace 了 URL，然后调 fetchPlan()；而 fetchPlan 读的是
//    模块级快照 `query`，新地点从未进入这次请求 →「已加入必去」但行程没变。
// ② 从「我的行程」打开时 historyId 有值，fetchPlan 直接走 fetchFromHistory()
//    重读存档，must_go 被彻底忽略 → 同样表现为没反应。
//
// 现在：先更新 query（入参）→ 必要时退出回看模式 → 同步 URL → 再规划。
const addToPlan = async (name) => {
  // 已经在行程里（或已是必去）就不再重复添加：
  // 加进去会把它从"算法选的"升级成 must_go 硬约束，语义变了。
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
  showToast(left ? `已加入必去并重新规划：${name}` : `已加入必去：${name}`)
  await fetchPlan()
}

const removeFromPlan = async (name) => {
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
  showToast(left ? `已移出必去并重新规划：${name}` : `已移出必去：${name}`)
  await fetchPlan()
}

/* ==================== 城际交通（真实距离 + 驾车估算） ==================== */
// 班次数据（铁路/航班）需要 12306 或 OTA 授权，本项目没有；
// 因此只展示真实地理编码算出的距离与驾车耗时，并明确标注为估算，不伪造车次。
const intercity = ref(null)
const intercityLoading = ref(false)
const intercityError = ref('')

const intercitySummary = computed(() => {
  const d = intercity.value
  if (!d || !d.ok) return ''
  const hours = Math.floor(d.drive_minutes / 60)
  const mins = d.drive_minutes % 60
  const duration = hours > 0 ? `${hours}小时${mins ? mins + '分' : ''}` : `${mins}分钟`
  return `${d.distance_km} 公里 · 驾车约 ${duration}`
})

const intercityResolved = computed(() => {
  const d = intercity.value
  if (!d || !d.ok || !d.origin) return ''
  return d.origin.resolved_address || ''
})

const loadIntercity = async () => {
  const origin = (query.origin || '').trim()
  // 目的地取行程里的城市：回看历史时 URL 不带 city，用 query 会算成别的城市
  const dest = effectiveCity.value
  if (!origin || !dest) return
  intercityLoading.value = true
  intercityError.value = ''
  try {
    const res = await getIntercity(origin, dest)
    intercity.value = res && res.data ? res.data : null
    if (intercity.value && !intercity.value.ok) {
      intercityError.value = intercity.value.note || '无法估算城际交通'
    }
  } catch (e) {
    console.warn('城际交通估算失败', e)
    intercityError.value = '城际交通估算服务暂不可用'
  } finally {
    intercityLoading.value = false
  }
}

/* ==================== 路线缩略图 ==================== */
const thumbUrl = ref('')
let thumbObjectUrl = ''
const loadThumbnail = async () => {
  const days = itinerary.value && itinerary.value.days
  if (!days || !days.length) return
  if (thumbObjectUrl) URL.revokeObjectURL(thumbObjectUrl)
  thumbObjectUrl = ''
  thumbUrl.value = ''
  const url = await getRouteThumbnail(days)
  if (url) {
    thumbObjectUrl = url
    thumbUrl.value = url
  }
}
const displayTitle = computed(() => { if(tripTitle.value) return tripTitle.value; if(itinerary.value&&itinerary.value.description) return itinerary.value.description; return query.origin+'出发·'+query.city+query.days+'日深度游' })
const dayPills = computed(() => { if(!itinerary.value||!itinerary.value.days) return []; return itinerary.value.days.map((d,i) => { let label='D'+(i+1); const m=(d.date||'').match(/(\d+)月(\d+)日/); if(m) label=m[1]+'月'+m[2]+'日'; return {index:i,label,rawDate:d.date||''} }) })
const currentDay = computed(() => { if(activeDayIndex.value<0||!itinerary.value||!itinerary.value.days) return null; return itinerary.value.days[activeDayIndex.value] })
const overviewCards = computed(() => { if(!itinerary.value||!itinerary.value.days) return []; return itinerary.value.days.map((day,di) => { const spots=(day.items||[]).filter(i=>i.type!=='hotel'); const hotel=(day.items||[]).find(i=>i.type==='hotel'); return {dayIndex:di,date:day.date||('第'+(di+1)+'天'),spotNames:spots.map(s=>s.name),routeText:spots.map(s=>s.name).join(' -> '),hotelName:hotel?hotel.name:null} }) })

/* ==================== 行程数据 ==================== */

/** 回看模式：直接读后端保存的历史行程，不重新规划 */
const fetchFromHistory = async () => {
  const res = await getHistoryDetail(historyId.value)
  const data = res && res.data
  if (!data || !data.itinerary) throw new Error('历史行程不存在或数据已损坏')
  itinerary.value = data.itinerary
  tripId.value = data.client_id || ('history-' + data.id)
  tripTitle.value = data.title || data.itinerary.description || ''
  // 同步城市/天数等，便于页面其他位置复用
  if (data.request) Object.assign(query, data.request)
}

/** 规划成功后留档：本机一份 + 登录用户同步后端（同一 client_id 幂等） */
const persistTrip = async () => {
  if (!itinerary.value) return
  if (!tripId.value) tripId.value = 't-' + Date.now()
  const request = { ...query }
  const title = tripTitle.value || (query.origin + '出发·' + query.city + query.days + '日游')
  if (!tripTitle.value) tripTitle.value = title

  // 1) 本机留档：未登录用户也能回看，断网也不丢
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

  // 2) 登录用户同步到账号下
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
  } catch (e) {
    console.warn('行程同步到账号失败', e)
    showToast('行程已存到本机，账号同步失败')
  }
}

const fetchPlan = async () => {
  loading.value = true
  isError.value = false
  errorMsg.value = ''
  // 先确认登录态：回看历史行程需要登录
  await ensureInitialized()
  try {
    if (historyId.value) {
      await fetchFromHistory()
      return
    }
    const res = await getPlan(query)
    if (res && res.code === 200 && res.data) {
      itinerary.value = res.data
    } else if (res && res.data) {
      itinerary.value = res.data
    } else {
      isError.value = true
      errorMsg.value = (res && res.message) || '生成行程失败'
    }
    if (itinerary.value) await persistTrip()
  } catch (error) {
    isError.value = true
    if (historyId.value && (error.code === 401 || error.status === 401)) {
      errorMsg.value = '登录后才能查看这条历史行程'
    } else if (error.isNetworkError === false && error.message) {
      errorMsg.value = error.message
    } else {
      errorMsg.value = '网络异常，请确认后端服务已启动'
    }
    console.error('行程规划请求失败:', error)
  } finally {
    loading.value = false
    // 行程就绪后并行补充「城际交通估算」与「路线缩略图」；
    // 两者失败都只是少一块内容，不影响主流程
    if (itinerary.value) {
      // 行程里的城市才是真源：从历史回看时 URL 不带 city，
      // 若灵感流先前按 query 的默认值拉过，这里必须按真实城市重拉
      const shownCity = ((itinerary.value && itinerary.value.city) || '').trim()
      if (shownCity && shownCity !== inspirationCity.value) {
        loadInspiration(shownCity)
      }
      loadIntercity()
      await nextTick()
      loadThumbnail()
    }
  }
}
const selectDay=(idx)=>{activeDayIndex.value=idx}
const selectTab=(tab)=>{activeTab.value=tab}
const sendChat=()=>{if(chatInput.value.trim())router.push({path:'/chat',query:{q:chatInput.value}})}
const goBack=()=>router.back()
// 展开地图：把 Bottom Sheet 收到最低档，露出全屏地图
const showMap = () => {
  sheetY.value = positions[2]
  nextTick(fitRouteOnMap)
}

/* ==================== 高德地图 ==================== */
const mapContainer = ref(null)
const mapLoading = ref(true)
let mapInstance = null
let mapOverlays = []

// 每天一种路线颜色
const PATH_COLORS = ['var(--app-brand)','var(--app-brand-hover)','var(--app-warning)','var(--app-brand-hover)','var(--app-danger)','var(--app-accent)','var(--app-accent)']

const initMap = async () => {
  try {
    const AMap = await loadAmap()
    await nextTick()
    if (!mapContainer.value || mapInstance) return
    createMap(AMap)
    mapLoading.value = false
  } catch (e) {
    mapLoading.value = false
    console.warn('高德地图加载失败：', e)
  }
  // 渲染叠加物单独 try：适配失败不应被误报成"地图加载失败"
  try {
    renderMapOverlays()
  } catch (e) {
    console.warn('路线叠加物渲染失败：', e)
  }
  watchDevicePixelRatio()
}

/* ==================== 设备像素比（高清渲染） ====================
 * AMap **只在创建地图那一次**读取 window.devicePixelRatio 来决定 canvas 的
 * 实际渲染分辨率。之后再发生像素比变化时，canvas 仍按旧分辨率绘制、被 CSS 拉伸，
 * 底图就会发糊（我们自己用 HTML 画的 Marker 与标注是浏览器原生渲染，所以依旧清晰，
 * 这也是"只有底图糊"的原因）。
 *
 * 典型触发场景：
 * 1. 先以 DPR=1 打开页面，再在 DevTools 里开启设备仿真（DPR 变成 2/3）；
 * 2. 把窗口拖到另一台不同缩放比（DPI）的显示器。
 *
 * 处理方式：检测到像素比变化就重建地图，让它按新分辨率重新渲染。
 * 用 matchMedia 的 resolution 查询来监听——window 的 resize 事件在
 * "只改 DPR、不改窗口尺寸"的仿真场景下未必触发。
 */
let mapDpr = 1
let dprMedia = null

const createMap = (AMap) => {
  // 显式给一个中心点：万一后续的路线适配失败，也不会停在"全国视图"
  const firstSpot = (itinerary.value?.days || [])
    .flatMap((d) => d.items || [])
    .find((i) => i.lat != null && i.lng != null)
  mapInstance = new AMap.Map(mapContainer.value, {
    zoom: 12,
    center: firstSpot ? [Number(firstSpot.lng), Number(firstSpot.lat)] : undefined,
    viewMode: '2D',
    resizeEnable: true,
  })
  mapDpr = window.devicePixelRatio || 1
  // 加载数据可视化平台项目图层
  // 未配置 VITE_AMAP_VISUAL_ID 时跳过（留空是合法配置，不影响基础地图与路线）
  try {
    if (AMAP_VISUAL_ID) {
      // eslint-disable-next-line no-new
      new AMap.Visual('amap://visual/' + AMAP_VISUAL_ID, mapInstance)
    }
  } catch (e) {
    console.warn('Visual 可视化图层加载失败：', e)
  }
}

const onDevicePixelRatioChange = () => {
  const dpr = window.devicePixelRatio || 1
  if (!mapInstance || Math.abs(dpr - mapDpr) < 0.01) {
    watchDevicePixelRatio()
    return
  }
  console.info(`[map] 设备像素比 ${mapDpr} → ${dpr}，重建地图以恢复清晰度`)
  try { mapInstance.destroy() } catch (e) { /* 忽略销毁异常 */ }
  mapInstance = null
  mapOverlays = []
  if (window.AMap && mapContainer.value) {
    createMap(window.AMap)
    renderMapOverlays()
  }
  watchDevicePixelRatio()
}

const watchDevicePixelRatio = () => {
  if (dprMedia && dprMedia.removeEventListener) {
    dprMedia.removeEventListener('change', onDevicePixelRatioChange)
  }
  dprMedia = window.matchMedia(`(resolution: ${window.devicePixelRatio || 1}dppx)`)
  if (dprMedia.addEventListener) {
    dprMedia.addEventListener('change', onDevicePixelRatioChange)
  }
}

// 把路线适配到地图可视区。
//
// 这里**刻意不用 AMap 的 setFitView + avoid**：实测在移动端会出现"整张地图缩到全国"
// 的异常（避让区与容器尺寸的相互作用不稳定），导致明明只有几公里的路线看不见。
// 改为自己按墨卡托公式算缩放级别——结果确定、可复现，也便于排查。
const fitRouteOnMap = () => {
  if (!mapInstance || !window.AMap || !mapOverlays.length) return
  const el = mapContainer.value
  // 容器尚未完成布局时算不出结果，下一帧重试（否则会把地图留在默认视图）
  if (!el || el.clientWidth < 80 || el.clientHeight < 80) {
    requestAnimationFrame(fitRouteOnMap)
    return
  }

  // 收集所有叠加物的坐标（Marker 取 position，Polyline 取 path）
  const pts = []
  mapOverlays.forEach((ov) => {
    if (typeof ov.getPosition === 'function') {
      const pos = ov.getPosition()
      if (pos && pos.lng != null) pts.push([Number(pos.lng), Number(pos.lat)])
    }
    if (typeof ov.getPath === 'function') {
      const path = ov.getPath()
      if (Array.isArray(path)) {
        path.forEach((q) => { if (q && q.lng != null) pts.push([Number(q.lng), Number(q.lat)]) })
      }
    }
  })
  if (!pts.length) return

  const lngs = pts.map((p) => p[0])
  const lats = pts.map((p) => p[1])
  const minLng = Math.min(...lngs); const maxLng = Math.max(...lngs)
  const minLat = Math.min(...lats); const maxLat = Math.max(...lats)
  const center = [(minLng + maxLng) / 2, (minLat + maxLat) / 2]

  // 面板遮住下半屏：可用高度取"地图顶部到面板顶部"，再留上下内边距
  const mapH = el.clientHeight
  const sheetTop = sheetY.value > 0 ? sheetY.value : mapH
  const usableH = Math.max(sheetTop - 64 - 28, 120)
  const usableW = Math.max(el.clientWidth - 48 - 48, 120)

  // Web 墨卡托：每像素米数 = 156543.03392 * cos(lat) / 2^zoom
  const R = 6378137
  const rad = Math.PI / 180
  const spanLatM = (maxLat - minLat) * rad * R
  const spanLngM = (maxLng - minLng) * rad * R * Math.cos(center[1] * rad)
  const needMPerPx = Math.max(spanLatM / usableH, spanLngM / usableW)
  const zoom = needMPerPx > 0
    ? Math.log2(156543.03392 * Math.cos(center[1] * rad) / needMPerPx)
    : 15
  // 单点或极近的点给城市级缩放；上限 16 避免贴到街道
  const safeZoom = Math.max(4, Math.min(16, zoom))

  // setZoomAndCenter 会把中心点放在**整个容器**的中央，但面板遮住了下半屏，
  // 于是路线被压到面板边缘（实测三天的气泡全挤在面板上沿）。
  // 这里把地图中心向南偏移半个遮挡高度，使路线落在可见区域的正中。
  const mPerPx = 156543.03392 * Math.cos(center[1] * rad) / Math.pow(2, safeZoom)
  const shiftPx = Math.max((mapH - sheetTop) / 2, 0)
  const deltaLat = shiftPx * mPerPx / 111320
  mapInstance.setZoomAndCenter(safeZoom, [center[0], center[1] - deltaLat])
}
const renderMapOverlays = () => {
  if (!mapInstance || !window.AMap || !itinerary.value || !itinerary.value.days) return
  const AMap = window.AMap

  mapInstance.remove(mapOverlays)
  mapOverlays = []

  const allDays = itinerary.value.days
  // 总览显示全部天；具体日期只显示当天
  const dayEntries = activeDayIndex.value >= 0
    ? [[activeDayIndex.value, allDays[activeDayIndex.value]]]
    : allDays.map((d, i) => [i, d])

  dayEntries.forEach(([dayIdx, day]) => {
    const color = PATH_COLORS[dayIdx % PATH_COLORS.length]
    const spots = (day.items || []).filter(i => i.type !== 'hotel' && i.lat != null && i.lng != null)
    const path = []
    spots.forEach((item, si) => {
      const position = [Number(item.lng), Number(item.lat)]
      path.push(position)
      const marker = new AMap.Marker({
        position,
        anchor: 'center',
        title: item.name,
        // 序号圆点 + POI 名称 label（名称直接用高德原文，不做改写）
        content: '<div class="map-marker" style="background:' + color + '">' + (si + 1) + '</div>',
        label: {
          content: '<div class="map-marker-label">' + escapeHtml(item.name) + '</div>',
          direction: 'right',
        },
      })
      marker.on('click', () => {
        showToast(item.name)
      })
      mapOverlays.push(marker)
    })
    if (path.length > 1) {
      const polyline = new AMap.Polyline({
        path,
        strokeColor: color,
        strokeWeight: 4,
        strokeOpacity: 0.75,
        lineJoin: 'round',
        lineCap: 'round',
        showDir: true,
      })
      mapOverlays.push(polyline)
    }

    // 按天汇总的提示气泡：第 N 天 · 首个地点 · 还有 M 个地点
    // 总览模式下才显示，避免与单天视图重复
    if (activeDayIndex.value < 0 && spots.length) {
      const clat = spots.reduce((s, i) => s + Number(i.lat), 0) / spots.length
      const clng = spots.reduce((s, i) => s + Number(i.lng), 0) / spots.length
      const more = spots.length - 1
      // 同城多日游时各天质心几乎重合，气泡会叠在一起，
      // 因此按天序号做一点像素级错位把它们岔开
      const spread = (dayIdx - (allDays.length - 1) / 2) * 34
      const callout = new AMap.Marker({
        position: [clng, clat],
        anchor: 'center',
        zIndex: 60,
        offset: new AMap.Pixel(0, spread),
        content:
          '<div class="map-day-callout" style="border-color:' + color + '">' +
          '<div class="mdc-day" style="background:' + color + '">第 ' + (dayIdx + 1) + ' 天</div>' +
          '<div class="mdc-name">' + escapeHtml(spots[0].name) + '</div>' +
          (more > 0 ? '<div class="mdc-more">还有 ' + more + ' 个地点</div>' : '') +
          '</div>',
      })
      callout.on('click', () => selectDay(dayIdx))
      mapOverlays.push(callout)
    }
  })

  if (mapOverlays.length) {
    mapInstance.add(mapOverlays)
    fitRouteOnMap()
  }
}

// 行程数据到达、日期切换时刷新地图
watch(itinerary, renderMapOverlays)
watch(activeDayIndex, renderMapOverlays)

/* ==================== Bottom Sheet 可拖拽面板 ==================== */
// 面板距视口顶部的距离（px），三档吸附
const FULL_TOP = 56                      // 完全展开时面板距顶部
const sheetY = ref(0)                    // 当前 translateY
const sheetDragging = ref(false)
let positions = [0, 0, 0]                 // [full, half, peek]
let dragStartY = 0
let dragStartSheetY = 0

const computePositions = () => {
  const h = window.innerHeight
  positions = [FULL_TOP, Math.round(h * 0.42), Math.round(h * 0.64)]
  // 首次默认半屏（上半地图、下半面板）
  if (!sheetY.value) sheetY.value = positions[1]
}
const clamp = (v, min, max) => Math.max(min, Math.min(max, v))

const onSheetPointerDown = (e) => {
  sheetDragging.value = true
  dragStartY = e.clientY
  dragStartSheetY = sheetY.value
  try { e.currentTarget.setPointerCapture(e.pointerId) } catch (err) { /* ignore */ }
}
const onSheetPointerMove = (e) => {
  if (!sheetDragging.value) return
  const next = clamp(dragStartSheetY + (e.clientY - dragStartY), positions[0], positions[2])
  sheetY.value = next
}
const onSheetPointerUp = () => {
  if (!sheetDragging.value) return
  sheetDragging.value = false
  // 吸附到最近的一档
  let best = positions[1], bestDist = Infinity
  positions.forEach((p) => {
    const d = Math.abs(p - sheetY.value)
    if (d < bestDist) { bestDist = d; best = p }
  })
  sheetY.value = best
  // 面板位置变了，可见地图区域也跟着变，重新适配路线
  fitRouteOnMap()
}
// 点击把手：半屏与全屏之间切换
const toggleSheet = () => {
  if (sheetDragging.value) return
  sheetY.value = sheetY.value === positions[0] ? positions[1] : positions[0]
  fitRouteOnMap()
}
const onWindowResize = () => {
  computePositions()
  sheetY.value = clamp(sheetY.value, positions[0], positions[2])
  // 有些环境只改像素比不发 matchMedia 事件，resize 时兜底再查一次
  const dpr = window.devicePixelRatio || 1
  if (mapInstance && Math.abs(dpr - mapDpr) >= 0.01) {
    onDevicePixelRatioChange()
    return
  }
  fitRouteOnMap()
}

onMounted(() => {
  computePositions()
  window.addEventListener('resize', onWindowResize)
  // 灵感流与规划并行发起：它不依赖规划结果，能先回来填住等待期
  loadInspiration()
  fetchPlan()
  initMap()
})

onBeforeUnmount(() => {
  // 释放缩略图的 blob URL，避免离开页面后内存不回收
  if (thumbObjectUrl) {
    URL.revokeObjectURL(thumbObjectUrl)
    thumbObjectUrl = ''
  }
  window.removeEventListener('resize', onWindowResize)
  if (dprMedia && dprMedia.removeEventListener) {
    dprMedia.removeEventListener('change', onDevicePixelRatioChange)
  }
  try { if (mapInstance) mapInstance.destroy() } catch (e) { /* 忽略 */ }
  mapInstance = null
  mapOverlays = []
})
</script>

<template>
  <div class="timeline-page">
    <!-- 全屏地图 -->
    <div class="map-stage">
      <div ref="mapContainer" class="map-container"></div>
      <div v-if="mapLoading" class="map-loading">
        <van-loading size="22" color="var(--app-brand)" vertical>地图加载中</van-loading>
      </div>
    </div>

    <!-- 地图悬浮导航（容器不拦截事件，仅按钮可点） -->
    <div class="map-nav">
      <div class="map-btn" @click="goBack"><van-icon name="arrow-left" size="18" color="var(--app-text)" /></div>
      <div class="map-actions">
        <div class="map-btn" @click="showToast('消息')"><van-icon name="chat-o" size="18" color="var(--app-text)" /><span class="map-dot"></span></div>
        <div class="map-btn" @click="showToast('分享')"><van-icon name="share-o" size="18" color="var(--app-text)" /></div>
        <div class="map-btn" @click="showToast('更多')"><van-icon name="ellipsis" size="18" color="var(--app-text)" /><span class="map-dot"></span></div>
      </div>
    </div>

    <!-- 可上下拖拽的底部面板 -->
    <div
      class="bottom-sheet"
      :class="{ dragging: sheetDragging }"
      :style="{ transform: 'translateY(' + sheetY + 'px)' }"
    >
      <div
        class="sheet-handle"
        @pointerdown="onSheetPointerDown"
        @pointermove="onSheetPointerMove"
        @pointerup="onSheetPointerUp"
        @pointercancel="onSheetPointerUp"
        @click="toggleSheet"
      >
        <span class="sheet-grip"></span>
      </div>
      <div class="sheet-body">
    <!-- 行程标题区 -->
    <div class="trip-header">
      <div class="trip-title-row">
        <h1 v-if="!editingTitle" class="trip-title" @click="editingTitle = true">
          {{ displayTitle }} <van-icon name="edit" size="14" color="var(--app-text-secondary)" />
        </h1>
        <input v-else v-model="tripTitle" class="trip-title-input" placeholder="输入行程标题"
          @blur="editingTitle = false" @keyup.enter="editingTitle = false" autofocus />
      </div>
      <div class="trip-meta-row">
        <div class="trip-date">
          <van-icon name="calendar-o" size="14" color="var(--app-text-secondary)" />
          <span>出发日期: {{ itinerary?.days?.[0]?.date || '待定' }}</span>
        </div>
        <div class="trip-companions">
          <div class="companion-avatar"></div>
          <div class="companion-add" @click="showToast('邀请同行')"><van-icon name="plus" size="14" color="var(--app-surface)" /></div>
        </div>
      </div>
    </div>

    <!-- Tab 导航。
         原来还有第三个「预订管理」——项目没有任何预订能力（后端零预订接口），
         点进去永远是一句「暂无预订内容」。那是界面在声称一个不存在的功能，
         已删除。桌面端本来就没有这个 tab，删掉后双端也一致了。 -->
    <div class="tab-nav">
      <div v-for="tab in [{key:'details',label:'行程详情'},{key:'inspiration',label:'旅行灵感'}]"
        :key="tab.key" class="tab-item" :class="{active:activeTab===tab.key}" @click="selectTab(tab.key)">
        {{ tab.label }}
      </div>
    </div>

    <!-- 加载态：用真实地点内容填住 20–40 秒的规划空窗期 -->
    <div v-if="loading" class="loading-box">
      <div class="loading-head">
        <van-loading size="22" color="var(--app-brand)">算法引擎正在为你规划行程</van-loading>
        <p class="loading-tip">选点 · 聚类 · 路线优化 · 时间排程</p>
        <p class="loading-hint">生成需要一点时间，先看看 {{ query.city }} 值得去的地方</p>
      </div>

      <!-- 行程骨架：告诉用户"即将出现什么"，比纯转圈更能留住人 -->
      <div class="sk-trip" aria-hidden="true">
        <div class="sk-stats">
          <div v-for="n in 3" :key="n" class="sk-stat">
            <div class="sk-line sk-line--value"></div>
            <div class="sk-line sk-line--label"></div>
          </div>
        </div>
        <div v-for="d in 2" :key="'day' + d" class="sk-day">
          <div class="sk-line sk-line--day"></div>
          <div v-for="i in 3" :key="'it' + i" class="sk-item">
            <div class="sk-thumb"></div>
            <div class="sk-item-body">
              <div class="sk-line sk-line--title"></div>
              <div class="sk-line sk-line--meta"></div>
            </div>
          </div>
        </div>
      </div>

      <InspirationCards
        :items="inspirationItems"
        :categories="inspirationCategories"
        :added="addedNames"
      :forced="forcedNames"
        :loading="inspirationLoading"
        @add="addToPlan"
      />
    </div>

    <!-- 错误态 -->
    <div v-else-if="isError" class="error-box">
      <van-empty image="error" :description="errorMsg" />
      <van-button v-if="historyId && !isLoggedIn" type="primary" round @click="router.push('/login')">
        去登录
      </van-button>
      <van-button v-else type="primary" round @click="fetchPlan">重新加载</van-button>
    </div>

    <!-- 行程详情 -->
    <div v-else-if="activeTab==='details'" class="tab-content">
      <div class="date-bar">
        <div class="date-scroll">
          <div class="date-pill" :class="{active:activeDayIndex===-1}" @click="selectDay(-1)">
            <van-icon name="bars" size="13" /><span>总览</span>
          </div>
          <div v-for="pill in dayPills" :key="pill.index" class="date-pill" :class="{active:activeDayIndex===pill.index}" @click="selectDay(pill.index)">
            {{ pill.label }}
          </div>
          <div class="date-pill" :class="{active:activeDayIndex===-2}" @click="selectDay(-2)">待安排</div>
          <div class="date-pill add-pill" @click="showToast('添加日期')"><van-icon name="plus" size="14" color="var(--app-text-secondary)" /></div>
        </div>
      </div>

      <!-- 总览 -->
      <div v-if="activeDayIndex===-1" class="overview-section">
        <!-- 叙事章节：首屏用第一张真实照片 + 大号序号 + 衬线标题 + 导语。
             照片来自高德 POI.photos，导语是 LLM 写的 daily_summary——
             这两样本来就在数据里，只是从来没用起来。 -->
        <StorySection
          v-for="(ch, ci) in storyChapters"
          :key="ch.key"
          :image="ch.image"
          :image-alt="ch.imageAlt"
          :numeral="ch.numeral"
          :kicker="ch.kicker"
          :title="ch.title"
          :text="ch.text"
          :cue="ch.cue"
          :skippable="ci === 0"
          class="day-chapter"
          @skip="scrollToTrip"
        />

        <!-- 行程总览卡：统计 chip + 路线缩略图 -->
        <div class="overview-card">
          <div class="overview-stats">
            <div v-for="s in overviewStats" :key="s.label" class="ov-stat">
              <div class="ov-value">{{ s.value }}</div>
              <div class="ov-label">{{ s.label }}</div>
            </div>
          </div>
          <div class="overview-map" @click="showMap">
            <img v-if="thumbUrl" :src="thumbUrl" alt="路线缩略图" class="overview-thumb" />
            <div v-else class="overview-thumb fallback">
              <van-icon name="map-marked" size="24" color="var(--app-text-tertiary)" />
              <span>路线缩略图生成中</span>
            </div>
            <div class="overview-map-btn">
              <van-icon name="location-o" size="14" color="var(--app-brand)" /><span>查看地图</span>
            </div>
          </div>
        </div>

        <div v-for="card in overviewCards" :key="card.dayIndex" class="day-overview-card" @click="selectDay(card.dayIndex)">
          <div class="day-overview-header">
            <span class="day-date-text">{{ card.date }}</span>
            <span class="day-route-title">{{ card.routeText }}</span>
          </div>
          <div class="day-route-detail">
            <span v-for="(name,i) in card.spotNames" :key="i" class="route-stop">
              {{ name }}<span v-if="i<card.spotNames.length-1" class="route-arrow-icon"> -> </span>
            </span>
            <span v-if="card.hotelName" class="route-stop hotel-stop">
              <span class="hotel-icon">H</span>{{ card.hotelName }}
            </span>
          </div>
          <div class="day-overview-footer">
            <!-- 每日金额已移除：门票语料覆盖仅 23.8%、住宿未接入，
                 `day.daily_cost` 已随预算系统删除。显示 0 元等于声称
                 "这天不用花钱"，那是在编造。 -->
            <van-icon name="delete-o" size="16" color="var(--app-text-tertiary)" @click.stop="showToast('删除该天')" />
          </div>
        </div>
      </div>

      <!-- 待安排 -->
      <div v-else-if="activeDayIndex===-2" class="unscheduled-section">
        <div class="unscheduled-header">
          <span class="unscheduled-title">待安排</span>
          <span class="one-click-plan" @click="showToast('一键规划')">
            <van-icon name="star-o" size="14" color="var(--app-brand)" />一键规划
          </span>
        </div>
        <div class="empty-unscheduled">
          <div class="empty-illustration"></div>
          <p class="empty-text">暂无待安排地点</p>
          <div class="add-unscheduled" @click="showToast('添加待安排的地点')">
            <van-icon name="add-o" size="14" color="var(--app-brand)" /><span>添加待安排的地点</span>
          </div>
        </div>
      </div>

      <!-- 具体日期 -->
      <div v-else-if="currentDay" class="day-detail-section">
        <div class="day-detail-header">
          <span class="day-detail-title">{{ currentDay.date || ('第'+(activeDayIndex+1)+'天') }}</span>
          <span class="optimize-btn" @click="showToast('路线已优化')">
            <van-icon name="exchange" size="14" color="var(--app-brand)" />顺路优化
          </span>
        </div>

        <!-- 城际交通：真实距离 + 驾车耗时估算 -->
        <!-- 这里刻意不显示车次与时刻表：班次数据需要 12306 / OTA 授权，本项目没有，
             伪造一个车次号在答辩现场被追问会很难看。 -->
        <div class="transport-section">
          <div class="transport-title">如何到达{{ query.city }}</div>
          <div v-if="intercityLoading" class="transport-card loading">
            <van-loading size="16" /> <span>正在估算城际距离…</span>
          </div>
          <div v-else-if="intercityError" class="transport-card muted">
            <van-icon name="info-o" size="16" color="var(--app-warning)" />
            <span>{{ intercityError }}</span>
          </div>
          <div v-else-if="intercitySummary" class="transport-card">
            <div class="transport-route">
              <div class="transport-endpoint">
                <span class="transport-place strong">{{ intercity.origin.name }}</span>
                <span class="transport-sub">出发地</span>
              </div>
              <div class="transport-line-mid">
                <span class="transport-duration">{{ intercitySummary }}</span>
                <div class="transport-dots"><span class="t-dot"></span><span class="t-line"></span><span class="t-dot"></span></div>
                <span class="transport-est">估算</span>
              </div>
              <div class="transport-endpoint">
                <span class="transport-place strong">{{ intercity.dest.name }}</span>
                <span class="transport-sub">目的地</span>
              </div>
            </div>
            <div v-if="intercityResolved" class="transport-resolved">
              <van-icon name="location-o" size="12" color="var(--app-text-secondary)" />
              <span>出发地解析为：{{ intercityResolved }}</span>
            </div>
            <div class="transport-note">
              铁路 / 航班班次数据未接入，以上为两地直线距离与驾车耗时估算
            </div>
            <a
              v-if="intercity.navigation_url"
              class="transport-link"
              :href="intercity.navigation_url"
              target="_blank"
              rel="noopener"
            >
              <van-icon name="guide-o" size="14" color="var(--app-brand)" />在高德地图中查看路线
            </a>
          </div>
          <div v-else class="transport-card muted">
            <van-icon name="info-o" size="16" color="var(--app-text-secondary)" />
            <span>填写出发地后可估算城际距离与驾车耗时</span>
          </div>
        </div>

        <!-- 景点列表：按 上午 / 下午 / 晚上 分段 -->
        <div v-for="section in timeSections(currentDay.items)" :key="section.label" class="time-section">
          <div class="time-section-head">
            <span class="ts-bar" :style="{ background: PATH_COLORS[activeDayIndex % PATH_COLORS.length] }"></span>
            <span class="ts-label">{{ section.label }}</span>
            <span class="ts-count">{{ section.list.length }} 个安排</span>
          </div>

          <template v-for="(item,ii) in section.list" :key="section.label + '-' + ii">
            <div v-if="item.type!=='hotel'" class="attraction-card">
              <div class="attraction-thumb">
                <img v-if="itemPhoto(item)" :src="itemPhoto(item)" alt="" loading="lazy" />
                <div v-else class="thumb-fallback">{{ photoFallbackText(item.name) }}</div>
              </div>
              <div class="attraction-info">
                <div class="attraction-name">{{ item.name }}</div>
                <div class="attraction-meta">
                  <span class="meta-item">{{ item.start }}-{{ item.end }}</span><span class="meta-dot">.</span>
                  <span class="meta-item">{{ openHours(item) }}</span>
                </div>
                <div class="attraction-price">
                  <!-- reserved 时只显示「门票待确认」，不显示数字——那会让用户以为是真的票价 -->
                  <span v-if="showCost(item)" class="price-text">{{ costText(item) }}</span>
                  <span v-else-if="item.cost_source === 'reserved'" class="price-text muted">门票待确认</span>
                  <span v-if="item.cost_note" class="cost-note">{{ item.cost_note }}</span>
                  <span v-else class="price-free">免费</span>
                </div>
              </div>
              <div class="attraction-more"><van-icon name="ellipsis" size="16" color="var(--app-text-tertiary)" /></div>
            </div>
          <div v-else class="hotel-card-inline">
            <span class="hotel-badge" :style="{background:typeColor(item.type)}">住</span>
            <span class="hotel-name">{{ item.name }}</span>
          </div>
          <!-- 景点间交通：真实数据缺失时不显示，
               不再像旧实现那样填 22 分钟 / 8.3 公里 这类假数值 -->
          <div
            v-if="item.type!=='hotel' && item.transport && ii<section.list.length-1 && section.list[ii+1].type!=='hotel'"
            class="inter-spot-transport"
          >
            <van-icon name="logistics" size="14" color="var(--app-text-secondary)" />
            <span class="inter-transport-text">
              {{ Math.ceil(item.transport.duration) }}分钟 · {{ item.transport.distance }}公里
            </span>
            <span class="navigate-link" @click="showToast('导航')">导航</span>
          </div>
          </template>
        </div>

        <!-- 当日花费已移除：daily_cost 随预算系统删除，显示 0 元是在编造 -->
      </div>
    </div>

    <!-- 旅行灵感 -->
    <div v-else-if="activeTab==='inspiration'" class="tab-content">
      <div class="inspiration-content">
        <InspirationCards
          :items="inspirationItems"
          :categories="inspirationCategories"
          :added="addedNames"
      :forced="forcedNames"
          :loading="inspirationLoading"
          @add="addToPlan"
        />
        <!-- 已加入必去的地点：可一键移出（must_go 是硬约束，要能反悔）。
             这里必须用 forcedNames 而不是 addedNames——后者还包含
             "算法排进行程"的地点，那些不是硬约束，也不该出现"移出"按钮 -->
        <div v-if="forcedNames.length" class="added-list">
          <div class="added-title">已加入必去（{{ forcedNames.length }}）</div>
          <div v-for="name in forcedNames" :key="name" class="added-row">
            <span class="added-name">{{ name }}</span>
            <span class="added-remove" @click="removeFromPlan(name)">
              <van-icon name="cross" size="12" />移出
            </span>
          </div>
          <div class="added-note">「必去」在算法里是硬约束：不会被评分排序挤掉。</div>
        </div>
      </div>
    </div>

      </div>
    </div>

    <!-- 底部 AI 对话栏 -->
    <div class="chat-bar">
      <div class="chat-input-wrapper">
        <input v-model="chatInput" class="chat-input" placeholder="向游游询问行程安排" @keyup.enter="sendChat" />
        <button class="chat-send" @click="sendChat"><van-icon name="guide-o" size="18" color="var(--app-surface)" /></button>
      </div>
      <div class="chat-add" @click="showToast('添加内容')"><van-icon name="plus" size="18" color="var(--app-brand)" /></div>
    </div>
  </div>
</template>

<style scoped>
.timeline-page{position:fixed;inset:0;overflow:hidden;background:var(--app-bg-subtle)}
/* 全屏地图 */
.map-stage{position:absolute;inset:0;z-index:0}
.map-container{width:100%;height:100%;background:var(--app-border)}
.map-loading{position:absolute;inset:0;z-index:1;display:flex;align-items:center;justify-content:center;background:var(--app-surface-muted)}
.map-stage :deep(.amap-logo){opacity:.55}
.map-stage :deep(.amap-copyright){opacity:.55}
.map-stage :deep(.map-marker){width:26px;height:26px;border-radius:50%;color:var(--app-surface);font-size:13px;font-weight:700;display:flex;align-items:center;justify-content:center;border:2.5px solid var(--app-surface);box-shadow:0 2px 6px rgba(0,0,0,.28);box-sizing:border-box;cursor:pointer}
/* POI 名称 label：直接用高德原文，不做改写 */
.map-stage :deep(.map-marker-label){background:var(--app-surface);border-radius:4px;padding:1px 6px;font-size:11px;color:var(--app-text);white-space:nowrap;box-shadow:0 1px 4px rgba(0,0,0,.18)}
/* 按天汇总气泡（总览模式） */
.map-stage :deep(.map-day-callout){background:var(--app-surface);border:2px solid var(--app-brand);border-radius:8px;padding:6px 9px;min-width:104px;box-shadow:0 3px 12px rgba(0,0,0,.16);cursor:pointer}
.map-stage :deep(.mdc-day){display:inline-block;color:var(--app-surface);font-size:10px;border-radius:3px;padding:1px 5px;margin-bottom:4px}
.map-stage :deep(.mdc-name){font-size:12px;font-weight:600;color:var(--app-text);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:150px}
.map-stage :deep(.mdc-more){font-size:10px;color:var(--app-text-secondary);margin-top:2px}
.map-stage :deep(.amap-marker-content){width:auto!important;height:auto!important;background:transparent!important;border:none!important}
/* 悬浮导航：容器放行指针事件，仅按钮可交互 */
.map-nav{position:absolute;top:0;left:0;right:0;z-index:30;display:flex;align-items:center;justify-content:space-between;padding:12px 16px;padding-top:44px;pointer-events:none}
.map-btn{position:relative;width:36px;height:36px;border-radius:50%;background:rgba(255,255,255,.92);backdrop-filter:blur(8px);display:flex;align-items:center;justify-content:center;cursor:pointer;box-shadow:0 2px 8px rgba(0,0,0,.08);pointer-events:auto}
.map-actions{display:flex;gap:10px}
.map-dot{position:absolute;top:6px;right:6px;width:7px;height:7px;border-radius:50%;background:var(--app-warning)}
/* 可拖拽底部面板 */
.bottom-sheet{position:absolute;top:0;left:0;right:0;height:100%;z-index:20;display:flex;flex-direction:column;will-change:transform;transition:transform .32s cubic-bezier(.22,.61,.36,1)}
.bottom-sheet.dragging{transition:none}
.sheet-handle{flex-shrink:0;height:22px;display:flex;align-items:center;justify-content:center;background:var(--app-surface);border-radius:18px 18px 0 0;cursor:grab;touch-action:none;box-shadow:0 -2px 12px rgba(0,0,0,.05)}
.sheet-handle:active{cursor:grabbing}
.sheet-grip{width:38px;height:4px;border-radius:2px;background:var(--app-border)}
.sheet-body{flex:1;min-height:0;display:flex;flex-direction:column;background:var(--app-bg-subtle);overflow:hidden}
.trip-header{flex-shrink:0;background:var(--app-surface);padding:16px;box-shadow:0 2px 8px rgba(0,0,0,.03)}
.trip-title-row{display:flex;align-items:flex-start;gap:6px}
.trip-title{flex:1;font-size:18px;font-weight:700;color:var(--app-text);line-height:1.5;cursor:pointer;margin:0}
.trip-title-input{flex:1;border:none;border-bottom:1px solid var(--app-brand);font-size:18px;font-weight:700;color:var(--app-text);outline:none;padding:2px 0}
.trip-meta-row{margin-top:10px;display:flex;align-items:center;justify-content:space-between}
.trip-date{display:flex;align-items:center;gap:6px;font-size:13px;color:var(--app-text-secondary)}
.trip-companions{display:flex;align-items:center}
.companion-avatar{width:28px;height:28px;border-radius:50%;background:linear-gradient(135deg,var(--app-brand),var(--app-brand-hover));border:2px solid var(--app-surface)}
.companion-add{width:28px;height:28px;border-radius:50%;background:var(--app-hairline);border:2px solid var(--app-surface);display:flex;align-items:center;justify-content:center;margin-left:-8px;cursor:pointer}
.tab-nav{flex-shrink:0;display:flex;background:var(--app-surface);border-top:1px solid var(--app-hairline)}
.tab-item{flex:1;text-align:center;padding:12px 0;font-size:15px;color:var(--app-text-secondary);cursor:pointer;position:relative;transition:color .2s}
.tab-item.active{color:var(--app-text);font-weight:600}
.tab-item.active::after{content:'';position:absolute;bottom:0;left:50%;transform:translateX(-50%);width:24px;height:3px;border-radius:2px;background:linear-gradient(90deg,var(--app-brand),var(--app-brand-hover))}
.loading-box{flex:1;min-height:0;overflow-y:auto;-webkit-overflow-scrolling:touch;padding:22px 0 90px}
.loading-head{display:flex;flex-direction:column;align-items:center;gap:8px;padding:0 16px 16px;text-align:center}
.loading-tip{font-size:12px;color:var(--app-text-secondary);letter-spacing:1px}
.loading-hint{margin-top:4px;font-size:13px;color:var(--app-text-secondary)}
/* 已加入必去列表 */
.added-list{margin:6px 16px 0;padding:14px;background:var(--app-surface);border-radius:14px;box-shadow:0 2px 10px rgba(0,0,0,.04)}
.added-title{font-size:13px;font-weight:600;color:var(--app-text);margin-bottom:10px}
.added-row{display:flex;align-items:center;justify-content:space-between;gap:10px;padding:7px 0;border-top:1px solid var(--app-bg-subtle)}
.added-name{font-size:13px;color:var(--app-text);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.added-remove{display:flex;align-items:center;gap:3px;flex-shrink:0;font-size:12px;color:var(--app-text-secondary)}
.added-note{margin-top:10px;padding-top:10px;border-top:1px dashed var(--app-hairline);font-size:11px;color:var(--app-text-secondary);line-height:1.6}
.error-box{flex:1;min-height:0;overflow-y:auto;padding:40px 16px 84px;display:flex;flex-direction:column;align-items:center;gap:8px}
.tab-content{flex:1;min-height:0;overflow-y:auto;-webkit-overflow-scrolling:touch;overscroll-behavior:contain;padding-bottom:84px}
.date-bar{background:var(--app-surface);padding:12px 16px;border-bottom:1px solid var(--app-hairline)}
.date-scroll{display:flex;gap:8px;overflow-x:auto;-webkit-overflow-scrolling:touch;padding-bottom:2px}
.date-scroll::-webkit-scrollbar{display:none}
.date-pill{flex-shrink:0;display:flex;align-items:center;gap:3px;padding:7px 16px;font-size:13px;color:var(--app-text-secondary);background:var(--app-surface-muted);border-radius:20px;cursor:pointer;white-space:nowrap;transition:all .15s}
.date-pill.active{color:var(--app-surface);background:var(--app-text);font-weight:600}
.add-pill{background:transparent;border:1px dashed var(--app-border)}
.overview-section{padding:12px 16px}
.day-overview-card{background:var(--app-surface);border-radius:14px;padding:16px;margin-bottom:12px;box-shadow:0 2px 10px rgba(0,0,0,.04);cursor:pointer;transition:transform .15s}
.day-overview-card:active{transform:scale(.98)}
.day-overview-header{display:flex;align-items:baseline;gap:10px;margin-bottom:8px}
.day-date-text{font-size:14px;font-weight:600;color:var(--app-text);flex-shrink:0}
.day-route-title{font-size:14px;color:var(--app-text-secondary);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.day-route-detail{font-size:13px;color:var(--app-text-secondary);line-height:1.8}
.route-stop{color:var(--app-text)}
.route-arrow-icon{color:var(--app-text-tertiary);margin:0 2px}
.hotel-stop{color:var(--app-accent);font-weight:500}
.hotel-icon{display:inline-flex;align-items:center;justify-content:center;width:16px;height:16px;border-radius:4px;background:var(--app-accent);color:var(--app-surface);font-size:10px;margin-right:4px;vertical-align:middle}
.day-overview-footer{margin-top:8px;display:flex;align-items:center;justify-content:space-between}
.unscheduled-section{padding:16px}
.unscheduled-header{display:flex;align-items:center;justify-content:space-between;margin-bottom:20px}
.unscheduled-title{font-size:17px;font-weight:700;color:var(--app-text)}
.one-click-plan{display:flex;align-items:center;gap:4px;font-size:14px;color:var(--app-brand);cursor:pointer}
.empty-unscheduled{display:flex;flex-direction:column;align-items:center;padding-top:60px}
.empty-illustration{width:120px;height:120px;border-radius:16px;background:linear-gradient(135deg,var(--app-brand-soft) 0%,var(--app-accent-soft) 100%);margin-bottom:16px}
.empty-text{font-size:14px;color:var(--app-text-secondary);text-align:center;line-height:1.6}
.add-unscheduled{margin-top:20px;display:flex;align-items:center;gap:4px;font-size:14px;color:var(--app-brand);cursor:pointer}
.day-detail-section{padding:12px 16px}
.day-detail-header{display:flex;align-items:center;justify-content:space-between;margin-bottom:16px;gap:10px}
.day-detail-title{flex:1;font-size:15px;font-weight:600;color:var(--app-text);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.optimize-btn{display:flex;align-items:center;gap:4px;padding:5px 12px;font-size:13px;color:var(--app-brand);background:var(--app-brand-soft);border-radius:16px;flex-shrink:0;cursor:pointer}
.transport-section{background:var(--app-surface);border-radius:14px;padding:16px;margin-bottom:16px;box-shadow:0 2px 10px rgba(0,0,0,.04)}
.transport-title{font-size:14px;font-weight:600;color:var(--app-text);margin-bottom:12px}
.transport-card{border:1px solid var(--app-hairline);border-radius:10px;padding:14px}
.transport-card.loading{display:flex;align-items:center;gap:10px;font-size:13px;color:var(--app-text-secondary)}
.transport-card.muted{display:flex;align-items:flex-start;gap:8px;font-size:13px;color:var(--app-text-secondary);line-height:1.6}
.transport-route{display:flex;align-items:center;justify-content:space-between}
.transport-endpoint{display:flex;flex-direction:column;align-items:flex-start;min-width:0}
.transport-place{margin-top:2px;font-size:12px;color:var(--app-text-secondary)}
.transport-place.strong{font-size:15px;font-weight:700;color:var(--app-text);margin:0;max-width:96px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.transport-sub{margin-top:3px;font-size:11px;color:var(--app-text-tertiary)}
.transport-line-mid{flex:1;display:flex;flex-direction:column;align-items:center;padding:0 10px;min-width:0}
.transport-duration{font-size:11px;color:var(--app-brand);margin-bottom:4px;text-align:center}
.transport-est{font-size:10px;color:var(--app-warning);background:var(--app-warning-soft);border-radius:3px;padding:0 4px;margin-top:4px}
.transport-dots{display:flex;align-items:center;width:100%}
.t-dot{width:6px;height:6px;border-radius:50%;background:var(--app-brand);flex-shrink:0}
.t-line{flex:1;height:2px;background:linear-gradient(90deg,var(--app-brand),var(--app-brand-hover))}
.transport-resolved{display:flex;align-items:center;gap:4px;margin-top:10px;font-size:11px;color:var(--app-text-secondary);line-height:1.5}
.transport-note{margin-top:8px;padding-top:8px;border-top:1px dashed var(--app-hairline);font-size:11px;color:var(--app-text-secondary);line-height:1.6}
.transport-link{display:inline-flex;align-items:center;gap:4px;margin-top:8px;font-size:12px;color:var(--app-brand);text-decoration:none}
/* 上午 / 下午 / 晚上 分段 */
.time-section{margin-bottom:6px}
.time-section-head{display:flex;align-items:center;gap:8px;margin:14px 2px 10px}
.ts-bar{width:3px;height:14px;border-radius:2px;flex-shrink:0}
.ts-label{font-size:14px;font-weight:600;color:var(--app-text)}
.ts-count{font-size:11px;color:var(--app-text-tertiary)}
/* 总览卡：统计 chip + 路线缩略图 */
.overview-card{background:var(--app-surface);border-radius:14px;padding:16px;margin-bottom:12px;box-shadow:0 2px 10px rgba(0,0,0,.04)}
.overview-stats{display:flex;gap:8px;margin-bottom:14px}
/* 预算未被用满时的原因说明（诚实告知，而不是让用户以为算错了） */
.ov-stat{flex:1;text-align:center;background:var(--app-bg-subtle);border-radius:10px;padding:10px 4px;min-width:0}
.ov-value{font-size:15px;font-weight:700;color:var(--app-text);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.ov-label{margin-top:3px;font-size:11px;color:var(--app-text-secondary)}
.overview-map{position:relative;border-radius:10px;overflow:hidden;background:var(--app-surface-muted)}
.overview-thumb{width:100%;height:150px;object-fit:cover;display:block}
.overview-thumb.fallback{display:flex;flex-direction:column;align-items:center;justify-content:center;gap:6px;font-size:12px;color:var(--app-text-tertiary)}
.overview-map-btn{position:absolute;right:10px;bottom:10px;display:flex;align-items:center;gap:4px;background:var(--app-surface);border-radius:16px;padding:5px 12px;font-size:12px;color:var(--app-text);box-shadow:0 2px 8px rgba(0,0,0,.12)}
.attraction-card{display:flex;gap:12px;background:var(--app-surface);border-radius:14px;padding:14px;margin-bottom:8px;box-shadow:0 2px 10px rgba(0,0,0,.04)}
.attraction-thumb{flex-shrink:0;width:80px;height:80px;border-radius:10px;overflow:hidden;background:var(--app-surface-muted)}
.attraction-thumb img{width:100%;height:100%;object-fit:cover;display:block}
/* 没有真实照片时的首字占位，避免用生成图冒充实景 */
.thumb-fallback{width:100%;height:100%;display:flex;align-items:center;justify-content:center;font-size:26px;font-weight:700;color:var(--app-surface);background:linear-gradient(135deg,var(--app-accent-soft),var(--app-accent-bright))}
.attraction-info{flex:1;min-width:0;display:flex;flex-direction:column;justify-content:space-between}
.attraction-name{font-size:15px;font-weight:600;color:var(--app-text);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.attraction-meta{display:flex;align-items:center;gap:4px;font-size:12px;color:var(--app-text-secondary)}
.meta-dot{color:var(--app-hairline)}
.attraction-price{display:flex;align-items:baseline;gap:2px;font-size:13px}
.price-text{color:var(--app-warning);font-weight:500}
/* 拿不到真实票价时不显示数字，只给「门票待确认」+ 语料原文备注 */
.price-text.muted{color:var(--app-text-secondary);font-weight:400}
.cost-note{display:block;margin-top:4px;font-size:11px;color:var(--app-text-tertiary);line-height:1.6}
.price-free{color:var(--app-brand-hover)}
.attraction-more{flex-shrink:0;padding:4px}
.hotel-card-inline{display:flex;align-items:center;gap:8px;background:var(--app-surface);border-radius:14px;padding:12px 14px;margin-bottom:8px;box-shadow:0 2px 10px rgba(0,0,0,.04)}
.hotel-badge{display:inline-flex;align-items:center;justify-content:center;width:22px;height:22px;border-radius:6px;color:var(--app-surface);font-size:11px;font-weight:600;flex-shrink:0}
.hotel-name{font-size:14px;color:var(--app-text);font-weight:500}
.inter-spot-transport{display:flex;align-items:center;gap:6px;padding:6px 0 6px 20px;margin-bottom:8px}
.inter-transport-text{font-size:12px;color:var(--app-text-secondary)}
.navigate-link{font-size:12px;color:var(--app-brand);cursor:pointer;margin-left:4px}
.inspiration-categories{background:var(--app-surface);padding:12px 16px;border-bottom:1px solid var(--app-hairline)}
.inspiration-content{padding:20px 16px}
.inspiration-empty{display:flex;flex-direction:column;align-items:center;padding-top:60px}
.chat-bar{position:fixed;bottom:0;left:0;right:0;display:flex;align-items:center;gap:10px;padding:10px 16px;background:var(--app-surface);border-top:1px solid var(--app-hairline);z-index:100}
.chat-input-wrapper{flex:1;display:flex;align-items:center;background:var(--app-surface-muted);border-radius:22px;padding:0 4px 0 16px}
.chat-input{flex:1;border:none;background:transparent;padding:9px 0;font-size:14px;color:var(--app-text);outline:none}
.chat-input::placeholder{color:var(--app-text-tertiary)}
.chat-send{width:32px;height:32px;border:none;border-radius:50%;background:linear-gradient(135deg,var(--app-brand),var(--app-brand-hover));display:flex;align-items:center;justify-content:center;cursor:pointer;flex-shrink:0}
.chat-add{width:32px;height:32px;border-radius:50%;background:var(--app-brand-soft);display:flex;align-items:center;justify-content:center;cursor:pointer;flex-shrink:0}

/* ==================== 加载骨架 ==================== */
.sk-trip { padding: 4px 16px 8px; }

.sk-stats {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 10px;
  margin: 14px 0 18px;
  padding: 14px 12px;
  background: var(--app-surface);
  border: 1px solid var(--app-hairline);
  border-radius: var(--app-radius-lg);
  box-shadow: var(--app-elev-1);
}
.sk-stat { display: flex; flex-direction: column; align-items: center; gap: 7px; }

.sk-day {
  background: var(--app-surface);
  border: 1px solid var(--app-hairline);
  border-radius: var(--app-radius-lg);
  box-shadow: var(--app-elev-1);
  padding: 14px;
  margin-bottom: 12px;
}

.sk-line {
  border-radius: 4px;
  background: linear-gradient(
    90deg,
    var(--app-surface-muted) 0%,
    var(--app-bg-subtle) 42%,
    var(--app-surface-muted) 84%);
  background-size: 300% 100%;
  animation: sk-shimmer 1.35s var(--app-ease-in-out) infinite;
}
.sk-line--value { width: 44px; height: 15px; }
.sk-line--label { width: 32px; height: 9px; }
.sk-line--day   { width: 66px; height: 15px; margin-bottom: 12px; }
.sk-line--title { width: 62%; height: 12px; }
.sk-line--meta  { width: 40%; height: 9px; margin-top: 7px; }

.sk-item { display: flex; gap: 10px; margin-bottom: 12px; }
.sk-item:last-child { margin-bottom: 0; }
.sk-thumb {
  width: 48px; height: 48px; flex-shrink: 0;
  border-radius: var(--app-radius-sm);
  background: var(--app-surface-muted);
  animation: sk-shimmer 1.35s var(--app-ease-in-out) infinite;
  background-size: 300% 100%;
}
.sk-item-body { flex: 1; padding-top: 8px; }

@keyframes sk-shimmer {
  0%   { background-position: 120% 0; }
  100% { background-position: -120% 0; }
}
@media (prefers-reduced-motion: reduce) {
  .sk-line, .sk-thumb { animation: none !important; }
}
</style>
