<script setup>
import { reactive, ref, onMounted, computed, watch } from 'vue'
import { useRouter } from 'vue-router'
import { showToast } from 'vant'
import ImageImportCard from '@/components/ImageImportCard.vue'
import { getMeta, importImage, importText } from '@/api/travel'
import { getHistoryList } from '@/api/history'
import { useAuth } from '@/composables/useAuth'
import { loadLocalTrips, saveLocalTrip } from '@/utils/localTrips'
import { bindForm } from '@/utils/formDraft'

const router = useRouter()
const { isLoggedIn, ensureInitialized } = useAuth()

const tagOptions = ref(['历史', '自然', '美食', '购物', '艺术', '夜生活'])
// 天数上限的唯一来源是后端 /api/meta；取不到时给一个宽松默认值，
// 不要退回旧的 7 —— 那正是"填 8 天被拒"的原因
const minDays = ref(1)
const maxDays = ref(30)
const quickDays = computed(() =>
  Array.from({ length: Math.min(maxDays.value, 7) }, (_, i) => i + 1)
)
// 出行风格三档（与后端 VALID_PROFILES 一致；/api/meta 返回后会被覆盖）
const profileOptions = ref(['悠闲', '经典', '尽览'])
// 出行偏好的三个新维度。取值必须与后端 schemas.py 的 VALID_COMPANIONS /
// VALID_PACES 完全一致——否则用户选了后端不认，界面就在骗人。
const companionOptions = ref(['独自', '家庭', '情侣', '朋友', '老人'])
const paceOptions = ref(['紧凑', '适中', '宽松'])
const timeOptions = ref([
  { label: '偏早出', start: '07:30', end: '' },
  { label: '偏晚归', start: '', end: '22:00' },
])

// 出行表单草稿：从时间线返回首页时恢复上一次填写的内容
const DRAFT_FIELDS = ['origin', 'city', 'days', 'tags', 'profile',
                       'companions', 'pace', 'day_start', 'day_end']

const formData = reactive({
  origin: '当涂',
  city: '',
  days: 3,
  tags: [],
  profile: '经典',
  must_go: [],
  avoid: [],
  // 出行偏好：这三项都会真的改变行程（见后端 engine 的说明）
  companions: [],
  pace: '',
  day_start: '',
  day_end: '',
})

const draft = bindForm('mobile', formData, DRAFT_FIELDS)

const preferenceSummary = computed(() => {
  const parts = []
  if (formData.profile) parts.push(formData.profile)
  if (formData.tags.length) parts.push(formData.tags.join('/'))
  if (formData.companions.length) parts.push(formData.companions.join('/'))
  if (formData.pace) parts.push(formData.pace)
  parts.push(`${formData.days}天`)
  return parts.length ? parts.join(' + ') + `等${parts.length}个` : ''
})

function toggleCompanion(item) {
  const idx = formData.companions.indexOf(item)
  if (idx > -1) formData.companions.splice(idx, 1)
  else formData.companions.push(item)
}

// 行程节奏是单选：再点一次可取消
function togglePace(item) {
  formData.pace = formData.pace === item ? '' : item
}

// 时间安排是单选：偏早出改出发时间、偏晚归改结束时间，都不选则用后端默认
function toggleTime(opt) {
  const active = formData.day_start === opt.start && formData.day_end === opt.end
  formData.day_start = active ? '' : opt.start
  formData.day_end = active ? '' : opt.end
}

function isTimeActive(opt) {
  return formData.day_start === opt.start && formData.day_end === opt.end
}

const hotDestinations = [
  { name: '南京', percent: '32%', img: 'https://trae-api-cn.mchost.guru/api/ide/v1/text_to_image?prompt=Nanjing%20ancient%20city%20wall%20with%20purple%20mountain%20background%20in%20golden%20sunset%2C%20classical%20Chinese%20architecture%2C%20photorealistic%20travel%20photography&image_size=portrait_4_3' },
  { name: '苏州', percent: '26%', img: 'https://trae-api-cn.mchost.guru/api/ide/v1/text_to_image?prompt=Suzhou%20classical%20garden%20with%20white%20walls%2C%20grey%20tiles%2C%20pavilion%20and%20lotus%20pond%2C%20misty%20morning%20light%2C%20photorealistic&image_size=portrait_4_3' },
  { name: '杭州', percent: '21%', img: 'https://trae-api-cn.mchost.guru/api/ide/v1/text_to_image?prompt=Hangzhou%20West%20Lake%20with%20Su%20causeway%2C%20willow%20trees%2C%20Leifeng%20pagoda%20in%20distance%2C%20soft%20spring%20light%2C%20photorealistic&image_size=portrait_4_3' },
  { name: '无锡', percent: '14%', img: 'https://trae-api-cn.mchost.guru/api/ide/v1/text_to_image?prompt=Wuxi%20Taihu%20lake%20scenery%20with%20Turtle%20Head%20Isle%2C%20cherry%20blossoms%2C%20soft%20morning%20mist%2C%20photorealistic%20travel&image_size=portrait_4_3' },
]

const showCityPicker = ref(false)
// 城市清单来自后端 /api/meta —— 单一真源，前端不再硬编码。
// 先前这里固化了 18 个城市，而后端其实能规划任何高德认得的城市。
const FALLBACK_CITIES = ['北京', '上海', '杭州', '南京', '苏州', '成都', '西安', '重庆', '厦门', '青岛']
const allCities = ref([...FALLBACK_CITIES])
const cityKeyword = ref('')
// 城市多了以后滚轮选择器没法用（几百项），改成「搜索 + 列表」
const filteredCities = computed(() => {
  const kw = cityKeyword.value.trim()
  if (!kw) return allCities.value
  return allCities.value.filter((c) => c.includes(kw))
})
// 城市选择器要区分"给哪一项选"。
//
// 旧实现：出发地和目的地共用同一个 popup，而 pickCity 永远写 formData.city（目的地）。
// 于是点「出发地」打开的是目的地选择器；**叉掉出发地之后就再也设不回去了**
// （只能去改「我的」或其他地方，而首页没有第二个入口）。
const cityPickerTarget = ref('city')          // 'origin' | 'city'
const cityPickerTitle = computed(() =>
  cityPickerTarget.value === 'origin' ? '选择出发地' : '选择目的地')

function openCityPicker(target) {
  cityPickerTarget.value = target === 'origin' ? 'origin' : 'city'
  cityKeyword.value = ''
  showCityPicker.value = true
}

function pickCity(city) {
  formData[cityPickerTarget.value] = city
  showCityPicker.value = false
  cityKeyword.value = ''
}

// 本机历史线路（未登录时的来源）与服务端历史线路（登录后的来源）
const localTrips = ref([])
const serverTrips = ref([])
// 首页只展示前两条：登录后以账号下的行程为准
const myTrips = computed(() => (isLoggedIn.value ? serverTrips.value : localTrips.value))

const loadTrips = () => {
  localTrips.value = loadLocalTrips()
}

/** 登录后拉账号下的历史行程（首页只取前两条） */
const loadServerTrips = async () => {
  if (!isLoggedIn.value) {
    serverTrips.value = []
    return
  }
  try {
    const res = await getHistoryList(1, 2)
    serverTrips.value = (res?.data?.items || []).map((item) => ({
      ...item,
      historyId: item.id,
      title: item.title || `${item.city}${item.days}日游`,
      createdAt: '已保存',
      date: (item.created_at || '').slice(0, 10),
    }))
  } catch (e) {
    console.warn('拉取账号历史行程失败', e)
  }
}

const saveTrip = (trip) => {
  localTrips.value = saveLocalTrip(trip)
}

/** 点历史线路：账号里的直接回看，本机的按参数重新规划 */
const openTrip = (trip) => {
  if (trip.historyId) {
    router.push({ path: '/timeline', query: { history_id: String(trip.historyId) } })
    return
  }
  router.push({
    path: '/timeline',
    query: { city: trip.city, days: trip.days, trip_id: trip.id },
  })
}

const showPreferences = ref(false)

// 「一句话规划」已整体移除：自然语言入口移交 AI 助手（/agent）。
// 相关状态（nlText / parsing / parseResult / handleParse / clearParse /
// nlPlaceholder）与 parsePlan 调用一并删除；后端 /api/parse 路由仍在，
// 但首页不再使用——保留它是为了不破坏 test_nl_parse.py 对 parse_request 的覆盖。

// 从「必去 / 避开」里移除一个地点（解析结果需要可反悔）
const removePlace = (field, name) => {
  formData[field] = (formData[field] || []).filter((n) => n !== name)
}

/* ==================== 导入线路/地点 ====================
 * 粘贴一段攻略/行程文字 → 抽出地点 → **逐个经高德反查** → 匹配到的可一键加入必去。
 * 未匹配到的会带原因明确列出，不会静默丢弃——导入功能最常见的坑就是
 * "显示导入成功、实际一半地点没匹配上"，用户完全看不出来。
 */
const showImport = ref(false)
const importTextValue = ref('')
const importing = ref(false)
const importResult = ref(null)
const importPicked = ref([])      // 用户勾选要加入的地点名

const openImport = () => {
  showImport.value = true
  importResult.value = null
  importPicked.value = []
}

/**
 * 文字导入与**攻略图导入**共用的结果处理。
 *
 * 两个来源在后端走同一条管线（图片 → 视觉模型提地名 → 高德逐个校验），
 * 返回结构一致，所以结果处理只写一次。
 */
const applyImportData = (data) => {
  if (!data) {
    showToast('导入失败，请稍后重试')
    return
  }
  importResult.value = data
  // 图片提取失败时后端带 ok=false + reason，如实展示而不是当成空结果
  if (data.ok === false) {
    showToast(data.reason || '没有从图片里提取到地点')
    return
  }
  // 城市冲突：攻略图的城市 ≠ 用户已选的目的地。
  //
  // 旧代码是 `if (data.city && !formData.city) formData.city = data.city`
  // ——表单已有城市时**永不覆盖**，同时后端也让 hint 优先，结果北京攻略
  // 被拿去南京搜，全部未匹配，理由还是误导性的「不是可游览的地点」。
  // 现在后端会把冲突标出来，这里让用户决定，而不是替用户猜。
  if (data.city_conflict && data.city && data.city_hint) {
    pendingConflict.value = data
    showConflictDialog.value = true
    return
  }
  if (data.city && !formData.city) formData.city = data.city
  // 默认勾选全部匹配到的地点；已经在必去里的不重复勾
  importPicked.value = (data.places || [])
    .filter((p) => p.matched && !formData.must_go.includes(p.poi.name))
    .map((p) => p.poi.name)
  if (!data.matched_count) {
    showToast('没有匹配到可加入的地点，请核对下方说明')
  }
}

/** 改用攻略图里的城市：直接套用已核对好的结果，不必重新请求 */
const useDetectedCity = () => {
  const data = pendingConflict.value
  showConflictDialog.value = false
  pendingConflict.value = null
  if (!data) return
  formData.city = data.city
  applyImportData({ ...data, city_conflict: false })
  // 城市已改，现在才显示核对层让用户确认要加哪些地点
  showImport.value = true
  showToast(`已把目的地切换为「${data.city}」`)
}

/** 仍在当前城市核对：带 force_city 重发同一张图 / 同一段文字 */
const forceCurrentCity = async () => {
  const data = pendingConflict.value
  showConflictDialog.value = false
  pendingConflict.value = null
  if (!data) return
  importing.value = true
  try {
    let res
    if (lastImportFile.value) {
      res = await importImage(lastImportFile.value, formData.city || '', formData.city || '')
    } else if (lastImportText.value) {
      res = await importText(lastImportText.value, formData.city || '', formData.city || '')
    }
    if (res && res.data) {
      applyImportData(res.data)
      // 重查结束后再打开核对层；若又冒出新冲突，applyImportData 已经把
      // pendingConflict 设好并弹了问题，这时就不该再开层
      if (!res.data.city_conflict) showImport.value = true
    }
  } catch (err) {
    showToast(err.message || '核对失败，请重试')
  } finally {
    importing.value = false
  }
}

const cancelConflict = () => {
  showConflictDialog.value = false
  pendingConflict.value = null
}

const runImport = async () => {
  const text = importTextValue.value.trim()
  if (!text) {
    showToast('先粘贴一段攻略或行程文字')
    return
  }
  // 保留原始输入：城市冲突时用户选「仍在当前城市核对」要用同一段文字重发。
  // 漏了这行的话，forceCurrentCity 的两个分支都不进，弹窗关了但结果还是旧的。
  lastImportText.value = text
  lastImportFile.value = null
  importing.value = true
  try {
    const res = await importText(text, formData.city || '')
    applyImportData(res && res.data ? res.data : null)
  } catch (e) {
    showToast(e.message || '导入失败，请稍后重试')
  } finally {
    importing.value = false
  }
}

// ---- 攻略图导入 ----
// 上限 8MB 与后端一致；超大图本地就拦掉，省一次网络往返
const IMPORT_IMAGE_MAX = 8 * 1024 * 1024
const importImageName = ref('')
// 保留上一次导入的原始输入：城市冲突时用户选「仍在当前城市核对」，
// 需要把**同一张图/同一段文字**带 force_city 重发一次。
const lastImportFile = ref(null)
const lastImportText = ref('')
// 城市冲突弹窗状态（必须在 applyImportData 之前声明，否则触发 TDZ）
const pendingConflict = ref(null)
const showConflictDialog = ref(false)

// 由 <ImageImportCard> 触发的选择回调：组件负责类型/大小校验并 emit 一个 File，
// 这里只做上传与结果处理（冲突弹窗等交互留在页面层，因为双端 UI 不同）。
const handleImageSelect = async (file) => {
  if (!file) return
  importImageName.value = file.name
  lastImportFile.value = file
  lastImportText.value = ''
  importing.value = true
  try {
    const res = await importImage(file, formData.city || '')
    applyImportData(res && res.data ? res.data : null)
    // 城市冲突时**不要**同时打开核对层：
    // 先让用户回答"换不换城市"，选完再由 useDetectedCity / forceCurrentCity
    // 打开核对层。否则用户会在还没决定城市的时候，就把另一座城市的地点
    // 加进必去，而表单里还是旧城市——接着点"开始规划"就会用错城市。
    if (res && res.data && !res.data.city_conflict) showImport.value = true
    const n = (res?.data?.image_places || []).length
    if (n) showToast(`从图片里提取到 ${n} 个地点`)
  } catch (err) {
    showToast(err.message || '识图失败，请改用粘贴文字')
  } finally {
    importing.value = false
  }
}

const toggleImportPick = (name) => {
  const i = importPicked.value.indexOf(name)
  if (i > -1) importPicked.value.splice(i, 1)
  else importPicked.value.push(name)
}

const applyImport = () => {
  if (!importPicked.value.length) {
    showToast('还没有勾选要加入的地点')
    return
  }
  const merged = [...formData.must_go]
  importPicked.value.forEach((n) => { if (!merged.includes(n)) merged.push(n) })
  formData.must_go = merged
  showToast(`已加入必去：${importPicked.value.length} 个地点`)
  showImport.value = false
}

const toggleTag = (tag) => {
  const idx = formData.tags.indexOf(tag)
  if (idx > -1) formData.tags.splice(idx, 1)
  else formData.tags.push(tag)
}

const selectDestination = (city) => {
  formData.city = city
}
const goPage = (page) => router.push(`/${page}`)

const handleSubmit = () => {
  // 出发地必填。旧实现不校验它，而行程页的 `origin: route.query.origin || '当涂'`
  // 会在空值时**静默回落到硬编码的「当涂」**——用户明明清空了，系统却按当涂算城际
  // 交通和标题，属于"界面声称的 ≠ 系统实际做的"。宁可在这里拦住。
  if (!formData.origin) {
    showToast('请选择出发地')
    return
  }
  if (!formData.city) {
    showToast('请选择目的地')
    return
  }
  if (!formData.days || formData.days < minDays.value || formData.days > maxDays.value) {
    showToast(`天数需在 ${minDays.value}-${maxDays.value} 天之间`)
    return
  }
  // 先在首页落一条轻量记录（此时还没有行程体），规划成功后由行程页回填完整内容
  const tripId = Date.now().toString()
  saveTrip({
    id: tripId,
    title: `${formData.origin}出发·${formData.city}${formData.days}日游`,
    city: formData.city,
    days: formData.days,
    date: new Date().toLocaleDateString('zh-CN'),
    createdAt: '刚刚',
  })
  router.push({
    path: '/timeline',
    query: {
      city: formData.city,
      origin: formData.origin,
      days: formData.days,
      tags: formData.tags.join(','),
      profile: formData.profile,
      // 出行偏好也要传：否则用户在偏好面板里选的节奏/时间在「一句话规划」路径下失效
      companions: (formData.companions || []).join(','),
      pace: formData.pace,
      day_start: formData.day_start,
      day_end: formData.day_end,
      trip_id: tripId,
      // must_go / avoid 是算法里的硬约束，必须传下去。
      // 之前漏传导致「一句话规划」识别出的必去景点被静默丢弃：
      // 回执写着"已识别：必去景点"，用户以为生效了，实际从未进入规划。
      must_go: formData.must_go.length ? formData.must_go.join(',') : undefined,
      avoid: formData.avoid.length ? formData.avoid.join(',') : undefined,
    },
  })
}

onMounted(async () => {
  // 先恢复草稿，再拉元数据（元数据回来会重置 tagOptions，不能覆盖已选值）
  draft.restore()
  loadTrips()
  try {
    const res = await getMeta()
    if (res && res.tags && res.tags.length) tagOptions.value = res.tags
    if (res && res.profiles && res.profiles.length) profileOptions.value = res.profiles
    if (res && res.max_days) maxDays.value = Number(res.max_days)
    if (res && res.min_days) minDays.value = Number(res.min_days)
    // 城市清单由后端给出单一真源（含语料城市与已缓存城市）
    if (res && res.cities && res.cities.length) allCities.value = res.cities
  } catch (e) {
    console.warn('拉取 meta 失败，使用默认选项', e)
  }
  // 登录态确认 + 本机记录迁移由 useAuth 负责，完成后刷新列表
  await ensureInitialized()
  loadTrips()
  await loadServerTrips()
})

// 登录/退出后切换数据来源
watch(isLoggedIn, () => {
  loadTrips()
  loadServerTrips()
})
</script>

<template>
  <div class="page-container">
    <!-- 品牌页眉：编辑感 masthead。
         原来只是一个居中的「AI行程助手」，没有层次也没有品牌记忆点。
         现在拆成 kicker（小字、拉开字距）+ 衬线大标题 + 极细分隔线，
         这是杂志刊头的做法，也是「高级感」最省力的一处投入。 -->
    <header class="masthead">
      <div class="masthead-kicker">AI TRAVEL · 算法驱动</div>
      <h1 class="masthead-title">
        去哪里<span class="masthead-serif">看看</span>？
      </h1>
      <div class="masthead-rule"></div>
    </header>

    <div class="page-content">
      <!-- 图片导入：说一句话的能力已整体交给「AI 助手」，
           首页只保留"传张攻略图"这一条输入路径。 -->
      <ImageImportCard
        :loading="importing"
        @select="handleImageSelect"
        @error="showToast"
      />

      <!-- 必去 / 避开：必须可见可删。
           导入后写着"已核对存在 4 个"，却看不到具体是哪几个，用户无法核对——
           而这两个字段会作为硬约束参与规划。 -->
      <div v-if="formData.must_go.length || formData.avoid.length" class="places-card">
        <div v-if="formData.must_go.length" class="nl-line">
          <van-tag type="danger" plain>必去</van-tag>
          <span class="place-chips">
            <span v-for="name in formData.must_go" :key="name" class="place-chip must">
              {{ name }}<van-icon name="cross" size="10" @click="removePlace('must_go', name)" />
            </span>
          </span>
        </div>
        <div v-if="formData.avoid.length" class="nl-line">
          <van-tag plain>避开</van-tag>
          <span class="place-chips">
            <span v-for="name in formData.avoid" :key="name" class="place-chip avoid">
              {{ name }}<van-icon name="cross" size="10" @click="removePlace('avoid', name)" />
            </span>
          </span>
        </div>
        <div class="nl-places-note">这些地点会作为硬约束参与规划：不会被评分排序或时间容量挤掉。</div>
      </div>

      <!-- 出发地 + 目的地 -->
      <div class="route-section">
        <div class="route-input origin-input" @click="openCityPicker('origin')">
          <van-icon name="location-o" size="18" color="var(--app-text-secondary)" />
          <span class="route-label">出发地</span>
          <span class="route-value" :class="{ filled: formData.origin }">{{ formData.origin || '选择城市' }}</span>
          <van-icon v-if="formData.origin" name="clear" size="14" color="var(--app-text-tertiary)" @click.stop="formData.origin = ''" />
        </div>

        <div class="route-divider">
          <span class="route-arrow">
            <van-icon name="arrow-down" size="14" color="var(--app-text-tertiary)" />
          </span>
        </div>

        <div class="route-input dest-input" @click="openCityPicker('city')">
          <van-icon name="search" size="18" color="var(--app-brand)" />
          <span class="route-label">目的地</span>
          <span class="route-value" :class="{ filled: formData.city, highlight: formData.city }">
            {{ formData.city || '想去哪里' }}
          </span>
          <van-icon v-if="formData.city" name="clear" size="14" color="var(--app-text-tertiary)" @click.stop="formData.city = ''" />
          <van-icon v-else name="arrow" size="14" color="var(--app-text-tertiary)" />
        </div>
      </div>

      <!-- 热门目的地：编辑排版的卡片。
           「百分比」原本是右上角一个小角标，和城市名抢焦点又读不懂
           （32% 是什么的 32%？）。改成标题下的一行小字说明，
           城市名用衬线大字压在照片上——这是杂志封面的处理方式。 -->
      <div class="hot-destinations">
        <div class="hot-head">
          <span class="hot-kicker">FEATURED</span>
          <span class="hot-caption">近期热门目的地</span>
        </div>
        <div class="hot-scroll">
          <div
            v-for="dest in hotDestinations"
            :key="dest.name"
            class="dest-card"
            :class="{ active: formData.city === dest.name }"
            @click="selectDestination(dest.name)"
          >
            <img :src="dest.img" alt="" class="dest-img" loading="lazy" />
            <div class="dest-overlay"></div>
            <div class="dest-body">
              <span class="dest-name">{{ dest.name }}</span>
              <span class="dest-meta">热度 {{ dest.percent }}</span>
            </div>
          </div>
        </div>
      </div>

      <!-- 日期 + 偏好 -->
      <div class="param-section">
        <div class="param-item" @click="showPreferences = true">
          <van-icon name="calendar-o" size="18" color="var(--app-text-secondary)" />
          <span class="param-text">{{ formData.days }}天</span>
          <van-icon name="arrow" size="14" color="var(--app-text-tertiary)" />
        </div>
        <div class="param-item" @click="showPreferences = true">
          <van-icon name="like-o" size="18" color="var(--app-text-secondary)" />
          <span class="param-text" v-if="preferenceSummary">{{ preferenceSummary }}</span>
          <span class="param-text placeholder" v-else>选择偏好</span>
          <van-icon name="arrow" size="14" color="var(--app-text-tertiary)" />
        </div>
      </div>

      <!-- AI 规划按钮 -->
      <div class="cta-section">
        <button class="ai-plan-btn" @click="handleSubmit">
          <span class="ai-icon">✦</span>
          <span>AI规划旅程</span>
        </button>
        <div class="manual-link" @click="showToast('手动创建功能开发中')">手动创建线路</div>
      </div>

      <!-- 我的行程：只在已登录且有记录时显示。
           未登录时显示"我的线路"（本地记录）会让用户以为是云端数据，
           点进去才发现要登录——所以未登录时整块不渲染。 -->
      <div v-if="isLoggedIn && myTrips.length" class="my-trips-section">
        <div class="section-header">
          <span class="section-title">我的行程</span>
          <span class="section-link" @click="goPage('history')">查看全部 &rsaquo;</span>
        </div>
        <div
          v-for="trip in myTrips.slice(0, 2)"
          :key="trip.id"
          class="trip-card"
          @click="openTrip(trip)"
        >
          <div class="trip-info">
            <span class="trip-badge">{{ trip.createdAt }}</span>
            <div class="trip-title">{{ trip.title }}</div>
            <div class="trip-meta">
              <span>{{ trip.date }}</span>
              <span class="trip-divider">|</span>
              <span>{{ trip.days }}天</span>
            </div>
          </div>
          <div class="trip-thumb">
            <img :src="hotDestinations.find(d => d.name === trip.city)?.img || hotDestinations[0].img" alt="" loading="lazy" />
          </div>
        </div>
      </div>

      <!-- 未登录时的登录引导 -->
      <div v-if="!isLoggedIn" class="login-entry" @click="router.push('/login')">
        <van-icon name="user-circle-o" size="20" color="var(--app-brand)" />
        <div class="login-entry-text">登录后可云端保存行程，换设备也能回看</div>
        <van-icon name="arrow" size="14" color="var(--app-hairline-strong)" />
      </div>

      <!-- 导入线路/地点 -->
      <div class="import-section" @click="openImport">
        <div class="import-left">
          <div class="import-title">导入线路/地点</div>
          <div class="import-desc">粘贴攻略文字，自动识别地点并逐一到高德校验</div>
        </div>
        <div class="import-icon">
          <van-icon name="down" size="20" color="var(--app-brand)" />
        </div>
      </div>

      <!-- 导入弹层：粘贴文字 → 识别 → 勾选加入必去 -->
      <!-- 城市冲突确认：攻略图的城市和已选目的地不一致时出现一次。
           不能替用户猜——可能是"要去南京但拿北京攻略当参考"，
           也可能是"我改主意想去北京了"。 -->
      <van-dialog
        v-model:show="showConflictDialog"
        :show-cancel-button="false"
        :show-confirm-button="false"
        :close-on-click-overlay="false"
        class="conflict-dialog"
      >
        <div class="conflict-body">
          <div class="conflict-title">城市不一致</div>
          <p class="conflict-text">
            攻略图里识别到
            <b>「{{ pendingConflict && pendingConflict.city }}」</b>
            的地点，但你当前的目的地是
            <b>「{{ pendingConflict && pendingConflict.city_hint }}」</b>。
          </p>
          <p class="conflict-hint">
            已按「{{ pendingConflict && pendingConflict.city }}」核对好
            {{ pendingConflict && pendingConflict.matched_count || 0 }} 个地点。
          </p>
          <div class="conflict-actions">
            <van-button type="primary" block round @click="useDetectedCity">
              改用{{ pendingConflict && pendingConflict.city }}
            </van-button>
            <van-button plain block round @click="forceCurrentCity">
              仍在{{ pendingConflict && pendingConflict.city_hint }}核对
            </van-button>
            <van-button plain block round @click="cancelConflict">
              取消
            </van-button>
          </div>
        </div>
      </van-dialog>

      <van-popup v-model:show="showImport" position="bottom" round :style="{ maxHeight: '86%' }">
        <div class="import-sheet">
          <div class="import-sheet-title">导入线路/地点</div>
          <div class="import-sheet-desc">
            粘贴攻略、聊天记录或行程单，系统会从中识别地点，并到高德逐个核对是否真实存在
          </div>
          <textarea
            v-model="importTextValue"
            class="import-textarea"
            rows="5"
            maxlength="6000"
            placeholder="例如：D1 上午去拙政园，中午在平江路吃饭，下午逛苏州博物馆，晚上到山塘街看夜景"
          ></textarea>

          <div class="import-actions">
            <van-button type="primary" round block :loading="importing" @click="runImport">
              {{ importing ? '正在识别并核对…' : '识别地点' }}
            </van-button>
            <div v-if="formData.city" class="import-city-hint">
              将在「{{ formData.city }}」范围内核对（可在下方切换目的地后重试）
            </div>
          </div>

          <!-- 识别结果 -->
          <div v-if="importResult" class="import-result">
            <template v-if="importResult.places.length">
              <div class="import-group" v-if="importResult.matched_count">
                <div class="import-group-title ok">
                  已核对存在（{{ importResult.matched_count }}）· 点击可取消/勾选
                </div>
                <div class="import-chips">
                  <span
                    v-for="p in importResult.places.filter(x => x.matched)"
                    :key="p.query"
                    class="import-chip"
                    :class="{ picked: importPicked.includes(p.poi.name) }"
                    @click="toggleImportPick(p.poi.name)"
                  >
                    <van-icon
                      :name="importPicked.includes(p.poi.name) ? 'success' : 'plus'"
                      size="11"
                    />{{ p.poi.name }}
                    <em v-if="p.poi.rating">{{ p.poi.rating }}分</em>
                  </span>
                </div>
              </div>

              <div class="import-group" v-if="importResult.unmatched_count">
                <div class="import-group-title bad">
                  未匹配到（{{ importResult.unmatched_count }}）· 不会加入行程
                </div>
                <div v-for="p in importResult.places.filter(x => !x.matched)" :key="p.query" class="import-miss">
                  <span class="import-miss-name">{{ p.query }}</span>
                  <span class="import-miss-reason">{{ p.reason }}</span>
                </div>
              </div>

              <div v-for="(n, i) in importResult.notes" :key="i" class="import-note">{{ n }}</div>

              <van-button
                type="primary"
                round
                block
                :disabled="!importPicked.length"
                @click="applyImport"
              >
                把 {{ importPicked.length }} 个地点加入必去
              </van-button>
            </template>
            <div v-else class="import-empty">没有识别到地点，试试粘贴更完整的行程文字</div>
          </div>
        </div>
      </van-popup>
    </div>

    <!-- 城市选择器 -->
    <van-popup v-model:show="showCityPicker" position="bottom" round :style="{ maxHeight: '80%' }">
      <div class="city-picker">
        <div class="city-picker-head">
          <span class="city-picker-title">{{ cityPickerTitle }}</span>
          <span class="city-picker-count">共 {{ allCities.length }} 个</span>
        </div>
        <van-search
          v-model="cityKeyword"
          :placeholder="`输入城市名筛选，如 广州`"
          shape="round"
          background="transparent"
        />
        <div class="city-list">
          <div
            v-for="c in filteredCities"
            :key="c"
            class="city-item"
            :class="{ active: c === formData[cityPickerTarget] }"
            @click="pickCity(c)"
          >
            {{ c }}
            <van-icon v-if="c === formData[cityPickerTarget]" name="success" size="14" color="var(--app-brand)" />
          </div>
          <div v-if="!filteredCities.length" class="city-empty">
            没有匹配的城市。后端能规划任何高德认得的城市，
            也可以到「AI 助手」里直接用一句话说要去的城市。
          </div>
        </div>
      </div>
    </van-popup>

    <!-- 偏好弹层 -->
    <van-popup v-model:show="showPreferences" position="bottom" round :style="{ maxHeight: '80%' }">
      <div class="pref-popup">
        <div class="pref-header">
          <span class="pref-title">出行偏好</span>
          <van-icon name="cross" size="18" color="var(--app-text-secondary)" @click="showPreferences = false" />
        </div>

        <div class="pref-block">
          <div class="pref-label">天数<span class="pref-hint">最多 {{ maxDays }} 天</span></div>
          <div class="pref-days">
            <button
              v-for="d in quickDays"
              :key="d"
              class="day-chip"
              :class="{ active: formData.days === d }"
              @click="formData.days = d"
            >{{ d }}天</button>
            <!-- 超过 7 天用步进器：天数上限来自后端，前端不再写死 -->
            <van-stepper
              v-model="formData.days"
              :min="minDays"
              :max="maxDays"
              integer
              input-width="40px"
              button-size="22px"
            />
          </div>
        </div>

        <div class="pref-block">
          <div class="pref-label">兴趣偏好（可多选）</div>
          <div class="pref-tags">
            <span
              v-for="tag in tagOptions"
              :key="tag"
              class="pref-tag"
              :class="{ active: formData.tags.includes(tag) }"
              @click="toggleTag(tag)"
            >{{ tag }}</span>
          </div>
        </div>

        <div class="pref-block">
          <div class="pref-label">出行风格</div>
          <div class="pref-profiles">
            <div
              v-for="p in profileOptions"
              :key="p"
              class="pref-profile"
              :class="{ active: formData.profile === p }"
              @click="formData.profile = p"
            >
              <span class="profile-check" v-if="formData.profile === p">
                <van-icon name="success" size="12" color="var(--app-brand)" />
              </span>
              {{ p }}
            </div>
          </div>
        </div>

        <div class="pref-block">
          <div class="pref-label">同行伙伴<span class="pref-hint">可多选，带老人会放慢节奏</span></div>
          <div class="pref-tags">
            <span
              v-for="c in companionOptions"
              :key="c"
              class="pref-tag"
              :class="{ active: formData.companions.includes(c) }"
              @click="toggleCompanion(c)"
            >{{ c }}</span>
          </div>
        </div>

        <div class="pref-block">
          <div class="pref-label">行程节奏<span class="pref-hint">决定一天排几个点、每站待多久</span></div>
          <div class="pref-profiles">
            <div
              v-for="p in paceOptions"
              :key="p"
              class="pref-profile"
              :class="{ active: formData.pace === p }"
              @click="togglePace(p)"
            >
              <span class="profile-check" v-if="formData.pace === p">
                <van-icon name="success" size="12" color="var(--app-brand)" />
              </span>
              {{ p }}
            </div>
          </div>
        </div>

        <div class="pref-block">
          <div class="pref-label">时间安排<span class="pref-hint">不选则默认 09:00–21:00</span></div>
          <div class="pref-tags">
            <span
              v-for="opt in timeOptions"
              :key="opt.label"
              class="pref-tag"
              :class="{ active: isTimeActive(opt) }"
              @click="toggleTime(opt)"
            >{{ opt.label }}</span>
          </div>
        </div>

        <button class="pref-done-btn" @click="showPreferences = false">完成</button>
      </div>
    </van-popup>
  </div>
</template>

<style scoped>
.page-container {
  min-height: 100vh;
  min-height: 100dvh;
  background: var(--app-bg);
  padding-bottom: 70px;
}

.nav-bar {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 48px;
  background: var(--app-surface);
  border-bottom: 1px solid var(--app-hairline);
}
.nav-title {
  font-size: 17px;
  font-weight: 600;
  color: var(--app-text);
}

.page-content {
  padding: 16px;
}

/* 出发地+目的地 */
.route-section {
  background: var(--app-surface);
  border-radius: 14px;
  padding: 4px 16px;
  box-shadow: 0 2px 12px rgba(0, 0, 0, 0.04);
}
.route-input {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 14px 0;
  cursor: pointer;
}
.route-label {
  font-size: 13px;
  color: var(--app-text-secondary);
  flex-shrink: 0;
  width: 48px;
}
.route-value {
  flex: 1;
  font-size: 16px;
  color: var(--app-text-secondary);
}
.route-value.filled {
  color: var(--app-text);
  font-weight: 600;
}
.route-value.highlight {
  color: var(--app-brand);
}
.route-divider {
  display: flex;
  justify-content: center;
  position: relative;
}
.route-divider::before {
  content: '';
  position: absolute;
  left: 0;
  right: 0;
  top: 50%;
  border-top: 1px solid var(--app-hairline);
}
.route-arrow {
  position: relative;
  z-index: 1;
  width: 28px;
  height: 28px;
  border-radius: 50%;
  background: var(--app-surface);
  display: flex;
  align-items: center;
  justify-content: center;
  border: 1px solid var(--app-hairline);
}

/* 热门目的地轮播 */
.hot-destinations {
  margin-top: 16px;
}
.hot-scroll {
  display: flex;
  gap: 10px;
  overflow-x: auto;
  -webkit-overflow-scrolling: touch;
  padding-bottom: 4px;
}
.hot-scroll::-webkit-scrollbar {
  display: none;
}
.dest-card {
  position: relative;
  flex-shrink: 0;
  width: 120px;
  height: 150px;
  border-radius: 12px;
  overflow: hidden;
  cursor: pointer;
  transition: transform 0.2s;
}
.dest-card.active {
  box-shadow: 0 0 0 3px var(--app-brand);
}
.dest-card:active {
  transform: scale(0.97);
}
.dest-img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}
.dest-overlay {
  position: absolute;
  inset: 0;
  background: linear-gradient(180deg, rgba(0,0,0,0.1) 0%, rgba(0,0,0,0.55) 100%);
}
.dest-percent {
  position: absolute;
  top: 8px;
  left: 8px;
  padding: 2px 8px;
  font-size: 11px;
  color: var(--app-surface);
  background: rgba(0, 0, 0, 0.5);
  border-radius: 10px;
  backdrop-filter: blur(4px);
}
.dest-name {
  position: absolute;
  bottom: 10px;
  left: 10px;
  font-size: 16px;
  font-weight: 700;
  color: var(--app-surface);
  text-shadow: 0 1px 4px rgba(0,0,0,0.3);
}

/* 日期+偏好 */
.param-section {
  margin-top: 16px;
  background: var(--app-surface);
  border-radius: 14px;
  overflow: hidden;
  box-shadow: 0 2px 12px rgba(0, 0, 0, 0.04);
}
.param-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 16px;
  cursor: pointer;
  border-bottom: 1px solid var(--app-hairline);
}
.param-item:last-child {
  border-bottom: none;
}
.param-text {
  flex: 1;
  font-size: 15px;
  color: var(--app-text);
}
.param-text.placeholder {
  color: var(--app-text-secondary);
}

/* AI按钮 */
.cta-section {
  margin-top: 20px;
  text-align: center;
}
.ai-plan-btn {
  width: 100%;
  height: 50px;
  border: none;
  border-radius: 14px;
  background: linear-gradient(135deg, var(--app-brand) 0%, var(--app-brand-hover) 100%);
  color: var(--app-surface);
  font-size: 17px;
  font-weight: 600;
  letter-spacing: 2px;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  cursor: pointer;
  box-shadow: 0 6px 20px rgba(74, 144, 217, 0.35);
  transition: all 0.2s;
}
.ai-plan-btn:active {
  transform: scale(0.98);
  box-shadow: 0 3px 12px rgba(74, 144, 217, 0.3);
}
.ai-icon {
  font-size: 18px;
}
.manual-link {
  margin-top: 14px;
  font-size: 14px;
  color: var(--app-brand);
  cursor: pointer;
}

/* 我的线路 */
.my-trips-section {
  margin-top: 28px;
}
.section-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 12px;
}
.section-title {
  font-size: 17px;
  font-weight: 700;
  color: var(--app-text);
}
.section-link {
  font-size: 13px;
  color: var(--app-brand);
  cursor: pointer;
}
.trip-card {
  display: flex;
  align-items: center;
  gap: 12px;
  background: var(--app-surface);
  border-radius: 14px;
  padding: 14px;
  margin-bottom: 10px;
  box-shadow: 0 2px 12px rgba(0, 0, 0, 0.04);
  cursor: pointer;
  transition: transform 0.15s;
}
.trip-card:active {
  transform: scale(0.98);
}
.trip-info {
  flex: 1;
  min-width: 0;
}
.trip-badge {
  display: inline-block;
  padding: 2px 8px;
  font-size: 11px;
  color: var(--app-brand);
  background: var(--app-brand-soft);
  border-radius: 8px;
  margin-bottom: 6px;
}
.trip-title {
  font-size: 15px;
  font-weight: 600;
  color: var(--app-text);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.trip-meta {
  margin-top: 4px;
  font-size: 12px;
  color: var(--app-text-secondary);
}
.trip-divider {
  margin: 0 6px;
}
.trip-thumb {
  flex-shrink: 0;
  width: 64px;
  height: 64px;
  border-radius: 10px;
  overflow: hidden;
}
.trip-thumb img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

/* 自然语言规划 */
.nl-section {
  background: var(--app-surface);
  border-radius: 14px;
  padding: 14px;
  margin-bottom: 14px;
  box-shadow: 0 2px 12px rgba(0, 0, 0, 0.04);
}
.nl-head {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 10px;
}
.nl-title {
  font-size: 15px;
  font-weight: 600;
  color: var(--app-text);
}
.nl-hint {
  flex: 1;
  text-align: right;
  font-size: 11px;
  color: var(--app-text-secondary);
}
.nl-input {
  padding: 0;
  background: var(--app-bg-subtle);
  border-radius: 10px;
}
.nl-input :deep(.van-field__body) {
  padding: 8px 10px;
}
.nl-actions {
  margin-top: 12px;
  display: flex;
  gap: 8px;
}
.nl-receipt {
  margin-top: 12px;
  padding-top: 10px;
  border-top: 1px dashed var(--app-border);
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.nl-line {
  display: flex;
  align-items: flex-start;
  gap: 6px;
  font-size: 12px;
  color: var(--app-text-secondary);
  line-height: 1.6;
}
.nl-line .van-tag {
  flex-shrink: 0;
  margin-top: 2px;
}
.nl-note {
  color: var(--app-warning);
}

/* 必去 / 避开 的地点 chips：必须可见，否则解析结果无法核对 */
/* .places-card 是「一句话规划」删除后独立出来的卡片（原来嵌在 .nl-section 里），
   现在自己占一层，所以要自带背景/圆角/阴影与上下间距。 */
.places-card {
  margin-bottom: 14px;
  padding: 14px;
  background: var(--app-surface);
  border-radius: var(--app-radius-lg);
  border: 1px solid var(--app-hairline);
  box-shadow: var(--app-elev-1);
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.nl-places {
  margin-top: 10px;
  padding-top: 10px;
  border-top: 1px dashed var(--app-border);
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.place-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.place-chip {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 2px 8px;
  border-radius: 10px;
  font-size: 12px;
  line-height: 18px;
}
.place-chip.must {
  background: var(--app-danger-soft);
  color: var(--app-danger);
}
.place-chip.avoid {
  background: var(--app-bg-subtle);
  color: var(--app-text-secondary);
  text-decoration: line-through;
}
.nl-places-note {
  font-size: 11px;
  color: var(--app-text-secondary);
  line-height: 1.6;
}

/* ==================== 城市选择（搜索 + 列表） ==================== */
.city-picker { display: flex; flex-direction: column; max-height: 80vh; }
.city-picker-head {
  display: flex; align-items: baseline; justify-content: space-between;
  padding: 16px 16px 4px;
}
.city-picker-title { font-size: 16px; font-weight: 600; color: var(--app-text); }
.city-picker-count { font-size: 12px; color: var(--app-text-secondary); }
.city-list { flex: 1; overflow-y: auto; padding: 4px 0 calc(12px + env(safe-area-inset-bottom)); }
.city-item {
  display: flex; align-items: center; justify-content: space-between;
  padding: 13px 16px; font-size: 15px; color: var(--app-text);
  border-bottom: 1px solid var(--app-bg-subtle);
}
.city-item.active { color: var(--app-brand); font-weight: 600; background: var(--app-bg-subtle); }
.city-empty { padding: 28px 20px; text-align: center; font-size: 12.5px; color: var(--app-text-secondary); line-height: 1.8; }

/* ==================== 导入线路/地点 ==================== */
.import-sheet {
  padding: 18px 16px calc(20px + env(safe-area-inset-bottom));
  max-height: 86vh;
  overflow-y: auto;
}
.import-sheet-title { font-size: 16px; font-weight: 600; color: var(--app-text); }
.import-sheet-desc {
  margin-top: 6px; font-size: 12px; color: var(--app-text-secondary); line-height: 1.6;
}
.import-textarea {
  margin-top: 12px; width: 100%; box-sizing: border-box;
  padding: 11px 12px; border: none; border-radius: 10px;
  background: var(--app-bg-subtle); font-size: 13px; color: var(--app-text);
  line-height: 1.7; resize: none; font-family: inherit;
}
.import-textarea::placeholder { color: var(--app-text-secondary); }
.import-image-bar {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 10px 0 4px;
  flex-wrap: wrap;
}
.import-image-input { display: none; }
.import-image-name { font-size: 12px; color: var(--app-text-secondary); }
.import-image-hint { font-size: 11px; color: var(--app-text-tertiary); }

.import-actions { margin-top: 12px; }
.import-city-hint { margin-top: 8px; text-align: center; font-size: 11px; color: var(--app-text-secondary); }
.import-result { margin-top: 16px; display: flex; flex-direction: column; gap: 14px; }
.import-group-title { font-size: 12px; font-weight: 600; margin-bottom: 8px; }
.import-group-title.ok { color: var(--app-brand-hover); }
.import-group-title.bad { color: var(--app-warning); }
.import-chips { display: flex; flex-wrap: wrap; gap: 8px; }
.import-chip {
  display: inline-flex; align-items: center; gap: 4px;
  padding: 5px 11px; border-radius: 14px; font-size: 12.5px;
  background: var(--app-bg-subtle); color: var(--app-text-secondary); border: 1px solid transparent;
}
.import-chip.picked { background: var(--app-success-soft); color: var(--app-success); border-color: var(--app-success-soft); }
.import-chip em { font-style: normal; font-size: 10px; opacity: .7; }
.import-miss {
  display: flex; align-items: baseline; gap: 8px;
  padding: 7px 10px; margin-bottom: 6px; border-radius: 8px;
  background: var(--app-warning-soft); font-size: 12px;
}
.import-miss-name { color: var(--app-text); font-weight: 600; flex-shrink: 0; }
.import-miss-reason { color: var(--app-warning); line-height: 1.5; }
.import-note { font-size: 11px; color: var(--app-text-secondary); line-height: 1.6; }
.import-empty { padding: 20px 0; text-align: center; font-size: 12px; color: var(--app-text-secondary); }

/* 登录引导 */
.login-entry {
  margin-top: 16px;
  display: flex;
  align-items: center;
  gap: 10px;
  background: var(--app-surface);
  border-radius: 14px;
  padding: 14px 16px;
  box-shadow: 0 2px 12px rgba(0, 0, 0, 0.04);
  cursor: pointer;
}
.login-entry-text {
  flex: 1;
  font-size: 13px;
  color: var(--app-text-secondary);
}

/* 导入功能 */
.import-section {
  margin-top: 16px;
  display: flex;
  align-items: center;
  gap: 12px;
  background: var(--app-surface);
  border-radius: 14px;
  padding: 16px;
  box-shadow: 0 2px 12px rgba(0, 0, 0, 0.04);
  cursor: pointer;
}
.import-left {
  flex: 1;
}
.import-title {
  font-size: 15px;
  font-weight: 600;
  color: var(--app-text);
}
.import-desc {
  margin-top: 4px;
  font-size: 12px;
  color: var(--app-text-secondary);
}
.import-icon {
  width: 40px;
  height: 40px;
  border-radius: 10px;
  background: var(--app-brand-soft);
  display: flex;
  align-items: center;
  justify-content: center;
}

/* 偏好弹层 */
.pref-popup {
  padding: 20px 16px 30px;
}
.pref-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 20px;
}
.pref-title {
  font-size: 18px;
  font-weight: 700;
  color: var(--app-text);
}
.pref-block {
  margin-bottom: 22px;
}
.pref-label {
  font-size: 14px;
  font-weight: 600;
  color: var(--app-text);
  margin-bottom: 10px;
}
.pref-days {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.day-chip {
  padding: 8px 18px;
  font-size: 14px;
  color: var(--app-text-secondary);
  background: var(--app-surface-muted);
  border: 1px solid var(--app-border);
  border-radius: 20px;
  cursor: pointer;
  transition: all 0.15s;
}
.day-chip.active {
  color: var(--app-surface);
  background: var(--app-text);
  border-color: transparent;
  font-weight: 600;
}
.pref-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.pref-tag {
  padding: 8px 16px;
  font-size: 14px;
  color: var(--app-text-secondary);
  background: var(--app-surface-muted);
  border: 1px solid var(--app-border);
  border-radius: 20px;
  cursor: pointer;
  transition: all 0.15s;
}
.pref-tag.active {
  color: var(--app-brand);
  background: var(--app-brand-soft);
  border-color: var(--app-brand);
  font-weight: 600;
}
.pref-profiles {
  display: flex;
  gap: 10px;
}
.pref-profile {
  position: relative;
  flex: 1;
  text-align: center;
  padding: 12px 0;
  font-size: 14px;
  color: var(--app-text-secondary);
  background: var(--app-surface-muted);
  border: 1px solid var(--app-border);
  border-radius: 10px;
  cursor: pointer;
  transition: all 0.15s;
}
.pref-profile.active {
  color: var(--app-brand);
  background: var(--app-brand-soft);
  border-color: var(--app-brand);
  font-weight: 600;
}
.profile-check {
  position: absolute;
  top: 4px;
  right: 4px;
}
.pref-done-btn {
  width: 100%;
  height: 48px;
  border: none;
  border-radius: 12px;
  background: linear-gradient(135deg, var(--app-brand) 0%, var(--app-brand-hover) 100%);
  color: var(--app-surface);
  font-size: 16px;
  font-weight: 600;
  cursor: pointer;
  box-shadow: 0 4px 16px rgba(74, 144, 217, 0.3);
}

/* ==================== 触摸目标下限 ====================
   实测这些 chip/按钮的高度只有 32px 左右，低于 44pt/48dp 的舒适点击尺寸
   （WCAG 2.5.8 的 24px 底线能过，但手机上容易点错）。
   统一抬高到 44px，同时保持视觉节奏不变：只加 min-height，不加 padding。 */
.pref-tag,
.pref-profile,
.city-item,
.import-sheet .van-button,
.nl-actions .van-button {
  min-height: 44px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
}
.city-item { display: flex; justify-content: space-between; }
.pref-profile { flex: 1; }
/* ==================== 两条路径的关系分隔 ====================
   解决"上面一句话能填城市、下面又有目的地字段"的困惑：
   明确告诉用户字段是可选的、上面那条路更快。 */
.path-divider {
  display: flex; align-items: center; gap: 10px;
  margin: 14px 0 10px; padding: 0 2px;
}
.path-divider-line { flex: 1; height: 1px; background: var(--app-border); }
.path-divider-text { font-size: 11.5px; color: var(--app-text-tertiary); flex: 0 0 auto; }

/* ==================== 品牌页眉（杂志刊头） ==================== */
.masthead {
  padding: 22px 20px 14px;
  background: transparent;
}
.masthead-kicker {
  font-size: 10.5px;
  letter-spacing: 0.22em;
  color: var(--app-text-tertiary);
  text-transform: uppercase;
}
.masthead-title {
  margin: 8px 0 0;
  font-family: var(--app-font-cn);
  font-size: 26px;
  font-weight: 600;
  line-height: 1.25;
  letter-spacing: -0.01em;
  color: var(--app-text);
}
/* 中英混排里给关键字换衬线，形成对比（编辑排版常用的手法） */
.masthead-serif {
  font-family: var(--app-font-serif);
  font-weight: 500;
  font-style: italic;
  letter-spacing: 0;
  color: var(--app-brand);
  margin: 0 2px;
}
.masthead-rule {
  margin-top: 14px;
  height: 1px;
  background: linear-gradient(
    to right,
    var(--app-hairline-strong),
    var(--app-hairline) 45%,
    transparent);
}

/* ==================== 卡片质感统一 ==================== */
/* 用分层阴影 + 低透明描边替代实心灰边框：更"贵"，也不会在森绿底下发黄 */
.nl-section,
.route-section,
.trip-card,
.hot-card {
  border-radius: var(--app-radius-lg);
  box-shadow: var(--app-elev-1);
  border: 1px solid var(--app-hairline);
}

/* 主操作卡（一句话规划）权重更高 */
.nl-section {
  box-shadow: var(--app-elev-2);
}

/* 分隔线「或直接填写」淡化 */
.divider-line,
.nl-divider {
  background: var(--app-hairline);
}

/* ==================== 热门目的地（编辑排版卡） ==================== */
.hot-head {
  display: flex;
  align-items: baseline;
  gap: 10px;
  padding: 0 2px 10px;
}
.hot-kicker {
  font-size: 10px;
  letter-spacing: 0.22em;
  color: var(--app-brand);
  font-weight: 600;
}
.hot-caption {
  font-size: 11.5px;
  color: var(--app-text-tertiary);
}

.dest-card {
  position: relative;
  flex-shrink: 0;
  width: 132px;
  height: 176px;
  border-radius: var(--app-radius-lg);
  overflow: hidden;
  cursor: pointer;
  box-shadow: var(--app-elev-1);
  transition: transform var(--app-dur-fast) var(--app-ease-out),
              box-shadow var(--app-dur-base) var(--app-ease-out);
}
.dest-card:active { transform: scale(var(--app-press-scale)); }
.dest-card.active {
  box-shadow: var(--app-elev-2), 0 0 0 2px var(--app-brand);
}

.dest-img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
  transition: transform var(--app-dur-slow) var(--app-ease-out);
}
.dest-card:active .dest-img { transform: scale(1.04); }

/* 底部渐变压暗：保证白字在任意亮度的照片上都够对比度 */
.dest-overlay {
  position: absolute;
  inset: 0;
  background: linear-gradient(
    to top,
    rgba(12, 20, 17, 0.86) 0%,
    rgba(12, 20, 17, 0.46) 34%,
    rgba(12, 20, 17, 0.08) 62%,
    transparent 100%);
}

.dest-body {
  position: absolute;
  left: 12px;
  right: 12px;
  bottom: 12px;
  display: flex;
  flex-direction: column;
  gap: 3px;
}
/* 衬线城市名：与页眉的衬线呼应，形成品牌一致性 */
.dest-name {
  font-family: var(--app-font-serif);
  font-size: 21px;
  font-weight: 600;
  color: var(--app-text-inverse);
  line-height: 1.1;
  letter-spacing: 0.01em;
  text-shadow: 0 1px 8px rgba(12, 20, 17, 0.45);
}
.dest-meta {
  font-size: 10.5px;
  color: rgba(255, 255, 255, 0.78);
  letter-spacing: 0.06em;
}

@media (prefers-reduced-motion: reduce) {
  .dest-card,
  .dest-img { transition: none !important; }
  .dest-card:active,
  .dest-card:active .dest-img { transform: none !important; }
}

/* ==================== 城市冲突弹窗 ==================== */
.conflict-body { padding: 20px 18px 16px; text-align: left; }
.conflict-title {
  font-family: var(--app-font-serif);
  font-size: 17px;
  font-weight: 600;
  color: var(--app-brand-hover);
}
.conflict-text {
  margin: 10px 0 0;
  font-size: 13.5px;
  line-height: 1.75;
  color: var(--app-text);
}
.conflict-text b { color: var(--app-brand); font-weight: 600; }
.conflict-hint {
  margin: 8px 0 0;
  font-size: 12px;
  line-height: 1.7;
  color: var(--app-text-tertiary);
}
.conflict-actions {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin-top: 18px;
}
/* ==================== 首页纵向节奏 ====================
   `.page-content` 是普通 block（非 flex），子卡片之间的间距靠各自 margin。
   原来「一句话规划」卡片自带 margin-bottom:14px，删掉后图片导入卡与
   出发地卡片会贴在一起——这里显式补回同一档间距（14px），
   与 .hot-destinations / .param-section 的 16px 视觉上一致。 */
.page-content > .img-import {
  margin-bottom: 14px;
}
.page-content > .places-card {
  margin-bottom: 14px;
}</style>
