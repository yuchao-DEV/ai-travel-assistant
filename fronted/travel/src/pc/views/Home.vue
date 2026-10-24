<script setup>
/**
 * 桌面端首页：左侧规划表单（含「一句话规划」），右侧历史行程与热门目的地。
 *
 * 与移动端一致的分工：自然语言解析（/api/parse）只负责把话翻译成结构化参数，
 * 真正的选点/排序/时间/费用由算法引擎在 /api/plan 里完成。
 */
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import ImageImportCard from '@/components/ImageImportCard.vue'
import { ElMessage , ElMessageBox } from 'element-plus'
import { ChatDotRound, Picture } from '@element-plus/icons-vue'

import { getHistoryList } from '@/api/history'
import StorySection from '@/components/StorySection.vue'
import { getMeta, importImage, importText } from '@/api/travel'
import { bindForm } from '@/utils/formDraft'
import { useAuth } from '@/composables/useAuth'
import { loadLocalTrips, saveLocalTrip } from '@/utils/localTrips'

const router = useRouter()
// ---- 叙事首屏 ----
//
// 背景图是**项目自带的静态图**（public/hero.jpg），不再临时去 API 取。
// 好处：首屏没有网络依赖、不会闪、也不会因为某个城市没照片就整段消失。
const formCard = ref(null)

const hero = ref({
  image: '/hero.jpg',
  imageAlt: '远山日出下的登山者剪影',
  numeral: '',
  kicker: 'AI 行程助手 · 算法驱动',
  // 多行标题：换行由 CSS 的 white-space: pre-line 保留
  title: '每一次出发，\n不是为了抵达顶峰，\n而是去遇见路上崭新的自己。',
  text: '',
  cue: '向下开始规划',
})

/** 「跳过叙述」：直接滚到表单 */
function scrollToForm() {
  const el = formCard.value
  if (el && el.scrollIntoView) el.scrollIntoView({ behavior: 'smooth', block: 'start' })
}

const { isLoggedIn, ensureInitialized } = useAuth()

const tagOptions = ref(['历史', '自然', '美食', '购物', '艺术', '夜生活'])
// 出行风格三档（与后端 VALID_PROFILES 一致；/api/meta 返回后会被覆盖）
const profileOptions = ref(['悠闲', '经典', '尽览'])
// 天数上限来自后端 /api/meta，前端不再写死 7
const minDays = ref(1)
const maxDays = ref(30)
// 城市清单同样来自 /api/meta —— 单一真源，前端不再硬编码。
// 先前这里固化了 18 个城市，而后端其实能规划任何高德认得的城市，
// 两边不同步就会出现"选得到却规划不了"或"能规划却选不到"。
// 兜底列表只在 /api/meta 拿不到时才用（极端降级）。
const FALLBACK_CITIES = ['北京', '上海', '杭州', '南京', '苏州', '成都', '西安', '重庆', '厦门', '青岛']
const allCities = ref([...FALLBACK_CITIES])

// 出行表单草稿：从时间线返回首页时恢复上一次填写的内容
const DRAFT_FIELDS = ['origin', 'city', 'days', 'tags', 'profile',
                       'companions', 'pace', 'day_start', 'day_end']

const form = reactive({
  origin: '当涂',
  city: '',
  days: 3,
  tags: ['历史'],
  profile: '经典',
  // 必去/避开必须存在于表单模型里：模板里读 form.must_go.length，
  // 字段缺失会在渲染期抛 TypeError，整个 PC 首页直接白屏（生产构建同样）
  must_go: [],
  avoid: [],
  // 出行偏好（与移动端一致）。这三项后端都真的会用：
  //   companions → 每日选点数 + 名称关键词偏好/避让
  //   pace       → 每日选点数 + 每站停留时长
  //   day_start/end → 排程时间窗
  companions: [],
  pace: '',
  day_start: '',
  day_end: '',
})

// 表单草稿绑定：从时间线返回首页时恢复上一次填写的内容
const draft = bindForm('pc', form, DRAFT_FIELDS)

// 取值必须与后端 schemas.py 的 VALID_COMPANIONS / VALID_PACES 一致，
// 否则用户选了后端不认，界面就在骗人。
const companionOptions = ref(['独自', '家庭', '情侣', '朋友', '老人'])
const paceOptions = ref(['紧凑', '适中', '宽松'])
const timeOptions = ref([
  { label: '偏早出', start: '07:30', end: '' },
  { label: '偏晚归', start: '', end: '22:00' },
])

function toggleCompanion(item) {
  const idx = form.companions.indexOf(item)
  if (idx > -1) form.companions.splice(idx, 1)
  else form.companions.push(item)
}

function togglePace(item) {
  form.pace = form.pace === item ? '' : item
}

// 时间安排的一个选项同时管 start 与 end 两个字段，
// 所以单选组绑定的是一个**折算出来的键**（当前生效选项的 label），
// 变化时再写回两个字段。直接绑 form.day_start 是无法表达"取消"的。
const timeKey = computed(() => {
  const hit = timeOptions.value.find(
    opt => form.day_start === opt.start && form.day_end === opt.end)
  return hit ? hit.label : ''
})

/** 点一下选中，再点一下取消。绑在按钮的 click 上（见模板注释）。 */
function toggleTime(opt) {
  if (!opt) return
  const active = form.day_start === opt.start && form.day_end === opt.end
  form.day_start = active ? '' : opt.start
  form.day_end = active ? '' : opt.end
}


const localTrips = ref([])
const serverTrips = ref([])
const tripLoading = ref(false)

const trips = computed(() => (isLoggedIn.value ? serverTrips.value : localTrips.value))

const hotDestinations = [
  { name: '南京', percent: 32 },
  { name: '苏州', percent: 26 },
  { name: '杭州', percent: 21 },
  { name: '无锡', percent: 14 },
]

async function loadLocal() {
  localTrips.value = loadLocalTrips()
}

async function loadServerTrips() {
  if (!isLoggedIn.value) {
    serverTrips.value = []
    return
  }
  tripLoading.value = true
  try {
    const res = await getHistoryList(1, 6)
    serverTrips.value = (res?.data?.items || []).map((item) => ({
      ...item,
      historyId: item.id,
      title: item.title || `${item.city}${item.days}日游`,
      date: (item.created_at || '').slice(0, 10),
    }))
  } catch (e) {
    console.warn('拉取账号历史行程失败', e)
  } finally {
    tripLoading.value = false
  }
}

// 「一句话规划」已整体移除：自然语言入口移交 AI 助手（/agent）。
// handleParse / parsePlan / nlText / parseResult 一并删除。

/* ==================== 导入线路/地点 ====================
 * 粘贴一段攻略/行程文字 → 抽出地点 → **逐个经高德反查** → 匹配到的加入必去。
 * 未匹配的带原因明确列出，不静默丢弃（与移动端同一套后端逻辑）。
 */
const showImport = ref(false)
const importTextValue = ref('')
const importing = ref(false)
// 城市冲突弹窗状态 + 重试所需的原始输入
const pendingConflict = ref(null)
const lastImportFile = ref(null)
const lastImportText = ref('')
const importResult = ref(null)
const importPicked = ref([])

function openImport() {
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
function applyImportData(data) {
  if (!data) {
    ElMessage.error('导入失败，请稍后重试')
    return
  }
  importResult.value = data
  // 图片提取失败时后端带 ok=false + reason，如实展示而不是当成空结果
  if (data.ok === false) {
    ElMessage.warning(data.reason || '没有从图片里提取到地点')
    return
  }
  // 城市冲突：攻略图的城市 ≠ 用户已选目的地。
  //
  // 旧代码 `if (data.city && !form.city) form.city = data.city` —— 表单已有城市时
  // 永不覆盖，后端也让 hint 优先，结果北京攻略被拿去南京搜，全部未匹配，
  // 理由还是误导性的「不是可游览的地点」。现在让用户明确选择。
  if (data.city_conflict && data.city && data.city_hint) {
    pendingConflict.value = data
    showConflict()
    return
  }
  if (data.city && !form.city) form.city = data.city
  importPicked.value = (data.places || [])
    .filter((p) => p.matched && !form.must_go.includes(p.poi.name))
    .map((p) => p.poi.name)
  if (!data.matched_count) {
    ElMessage.warning('没有匹配到可加入的地点，请核对下方说明')
  }
}

/** 城市冲突确认：改用图片城市 / 仍在当前城市核对 / 取消 */
function showConflict() {
  const d = pendingConflict.value
  if (!d) return
  ElMessageBox.confirm(
    `攻略图里识别到「${d.city}」的地点，但你当前的目的地是「${d.city_hint}」。` +
    `\n已按「${d.city}」核对好 ${d.matched_count || 0} 个地点。`,
    '城市不一致',
    {
      confirmButtonText: `改用${d.city}`,
      cancelButtonText: `仍在${d.city_hint}核对`,
      distinguishCancelAndClose: true,
      type: 'warning',
    },
  ).then(() => {
    // 改用图片城市：直接套用已核对结果，无需重新请求
    form.city = d.city
    pendingConflict.value = null
    applyImportData({ ...d, city_conflict: false })
    // 城市已确定，这时才打开核对框让用户确认加哪些地点
    showImport.value = true
    ElMessage.success(`已把目的地切换为「${d.city}」`)
  }).catch(async (action) => {
    if (action !== 'cancel') {
      pendingConflict.value = null
      return
    }
    // 仍在当前城市核对：带 force_city 重发同一张图/同一段文字
    const city = form.city || ''
    importing.value = true
    try {
      let res
      if (lastImportFile.value) {
        res = await importImage(lastImportFile.value, city, city)
      } else if (lastImportText.value) {
        res = await importText(lastImportText.value, city, city)
      }
      if (res && res.data) {
        applyImportData(res.data)
        // 重查结束后再开核对框；若又冒出新冲突则交给 applyImportData 处理
        if (!res.data.city_conflict) showImport.value = true
      }
    } catch (err) {
      ElMessage.error(err.message || '核对失败，请重试')
    } finally {
      importing.value = false
    }
  })
}

async function runImport() {
  const text = importTextValue.value.trim()
  if (!text) {
    ElMessage.warning('先粘贴一段攻略或行程文字')
    return
  }
  importing.value = true
  try {
    lastImportText.value = text
    lastImportFile.value = null
    const res = await importText(text, form.city || '')
    applyImportData(res && res.data ? res.data : null)
  } catch (e) {
    ElMessage.error(e.message || '导入失败，请稍后重试')
  } finally {
    importing.value = false
  }
}

// ---- 攻略图导入 ----
// 上限 8MB 与后端一致；超大图本地就拦掉，省一次网络往返
const IMPORT_IMAGE_MAX = 8 * 1024 * 1024
async function handleImageSelect(file) {
  if (!file) return
  importImageName.value = file.name
  importing.value = true
  try {
    lastImportFile.value = file
    lastImportText.value = ''
    const res = await importImage(file, form.city || '')
    applyImportData(res && res.data ? res.data : null)
    // 城市冲突时**先只问城市**，等用户选完再打开核对框；
    // 同时打开会让用户在还没决定城市时，就把另一座城市的地点加进必去。
    if (res && res.data && !res.data.city_conflict) showImport.value = true
    const n = (res?.data?.image_places || []).length
    if (n) ElMessage.success(`从图片里提取到 ${n} 个地点`)
  } catch (err) {
    ElMessage.error(err.message || '识图失败，请改用粘贴文字')
  } finally {
    importing.value = false
  }
}

function toggleImportPick(name) {
  const i = importPicked.value.indexOf(name)
  if (i > -1) importPicked.value.splice(i, 1)
  else importPicked.value.push(name)
}

function applyImport() {
  if (!importPicked.value.length) {
    ElMessage.warning('还没有勾选要加入的地点')
    return
  }
  const merged = [...form.must_go]
  importPicked.value.forEach((n) => { if (!merged.includes(n)) merged.push(n) })
  form.must_go = merged
  ElMessage.success(`已加入必去：${importPicked.value.length} 个地点`)
  showImport.value = false
}

function removePlace(field, name) {
  form[field] = (form[field] || []).filter((n) => n !== name)
}

function validate() {  if (!form.city) {
    ElMessage.warning('请选择目的地')
    return false
  }
  if (!form.days || form.days < minDays.value || form.days > maxDays.value) {
    ElMessage.warning(`天数需在 ${minDays.value}-${maxDays.value} 天之间`)
    return false
  }
  return true
}

function handleSubmit() {
  if (!validate()) return
  const tripId = Date.now().toString()
  localTrips.value = saveLocalTrip({
    id: tripId,
    title: `${form.origin}出发·${form.city}${form.days}日游`,
    city: form.city,
    days: form.days,
    date: new Date().toLocaleDateString('zh-CN'),
    createdAt: '刚刚',
  })
  router.push({
    path: '/timeline',
    query: {
      city: form.city,
      origin: form.origin,
      days: form.days,
      tags: form.tags.join(','),
      profile: form.profile,
      // 出行偏好也要传：否则在偏好面板里选的节奏/时间在这条路径下静默失效
      companions: (form.companions || []).join(','),
      pace: form.pace,
      day_start: form.day_start,
      day_end: form.day_end,
      trip_id: tripId,
      // 同移动端：must_go / avoid 是硬约束，漏传会让解析出的必去景点静默失效
      must_go: form.must_go && form.must_go.length ? form.must_go.join(',') : undefined,
      avoid: form.avoid && form.avoid.length ? form.avoid.join(',') : undefined,
    },
  })
}

function openTrip(trip) {
  if (trip.historyId) {
    router.push({ path: '/timeline', query: { history_id: String(trip.historyId) } })
    return
  }
  router.push({
    path: '/timeline',
    query: { city: trip.city, days: trip.days, trip_id: trip.id },
  })
}

function applyHot(name) {
  form.city = name
}

onMounted(async () => {
  draft.restore()
  // 叙事首屏的照片：独立异步，失败也不影响表单（hero 保持 null 即不渲染）
  await loadLocal()
  try {
    const meta = await getMeta()
    if (meta?.tags?.length) tagOptions.value = meta.tags
    if (meta?.profiles?.length) profileOptions.value = meta.profiles
    if (meta?.max_days) maxDays.value = Number(meta.max_days)
    if (meta?.min_days) minDays.value = Number(meta.min_days)
    if (meta?.cities?.length) allCities.value = meta.cities
    // 热门目的地是 {name, percent} 的展示结构，不是城市名数组，这里不覆盖
  } catch (e) {
    console.warn('拉取 meta 失败，使用默认选项', e)
  }
  await ensureInitialized()
  await loadLocal()
  await loadServerTrips()
})

watch(isLoggedIn, () => {
  loadLocal()
  loadServerTrips()
})
</script>

<template>
  <div class="pc-home">
    <!-- 叙事首屏：真实高德照片 + 衬线大标题。
         **只在拿到真实照片时渲染** —— 没有照片就不放占位图，
         宁可少一个章节，也不要用 AI 生成的假图充数。 -->
    <StorySection
      v-if="hero"
      :image="hero.image"
      :image-alt="hero.imageAlt"
      :numeral="hero.numeral"
      :kicker="hero.kicker"
      :title="hero.title"
      :text="hero.text"
      :cue="hero.cue"
      class="home-hero"
      @skip="scrollToForm"
    />

    <div class="home-layout">
      <!-- 左：规划表单 -->
      <div class="col-left">
        <!-- 图片导入：与移动端共用同一个组件（纯 HTML + 令牌，不依赖 Element）。
             「一句话规划」已整体移除，自然语言入口移交 AI 助手（/agent）。 -->
        <ImageImportCard
          :loading="importing"
          @select="handleImageSelect"
          @error="(msg) => ElMessage.warning(msg)"
        />

      <div ref="formCard" class="pc-card">
        <h3 class="pc-section-title"><el-icon color="var(--app-brand)"><Promotion /></el-icon>行程参数</h3>
        <el-form label-position="top">
          <div class="form-grid">
            <div class="form-group-label"><span>基本信息</span></div>
            <el-form-item label="出发地">
              <el-input v-model="form.origin" placeholder="如：当涂" clearable />
            </el-form-item>
            <el-form-item label="目的地">
              <el-select v-model="form.city" placeholder="选择城市" filterable style="width: 100%">
                <el-option v-for="c in allCities" :key="c" :label="c" :value="c" />
              </el-select>
            </el-form-item>
            <el-form-item label="天数">
              <el-input-number v-model="form.days" :min="minDays" :max="maxDays" style="width: 100%" />
            </el-form-item>
          </div>

          <!-- 把 7 个控件分成两段：基本信息（必填）与偏好微调（可选）。
               原先 9 个 el-form-item 平铺，用户扫下来没有停顿点，
               不知道哪些是必须的、哪些可以不管。 -->
          <div class="form-group-label">
            <span>偏好微调</span>
            <span class="form-group-hint">以下都可留空，留空按默认规则规划</span>
          </div>

          <el-form-item label="兴趣偏好">
            <el-checkbox-group v-model="form.tags">
              <el-checkbox-button v-for="t in tagOptions" :key="t" :value="t">
                {{ t }}
              </el-checkbox-button>
            </el-checkbox-group>
          </el-form-item>

          <!-- profile 是**出行风格**（悠闲/经典/尽览），不是节奏。
               先前这里标成「出行节奏」是错的：真正的节奏由下面的 pace 表达。
               两者分开后语义才清楚——花多少钱 ≠ 一天排多满。 -->
          <el-form-item label="出行风格">
            <el-radio-group v-model="form.profile">
              <el-radio-button v-for="p in profileOptions" :key="p" :value="p">
                {{ p }}
              </el-radio-button>
            </el-radio-group>
          </el-form-item>

          <el-form-item label="同行伙伴">
            <el-checkbox-group :model-value="form.companions" @change="v => form.companions = v">
              <el-checkbox-button v-for="c in companionOptions" :key="c" :value="c">
                {{ c }}
              </el-checkbox-button>
            </el-checkbox-group>
            <span class="form-hint">带老人会放慢节奏并避开费体力的项目</span>
          </el-form-item>

          <el-form-item label="行程节奏">
            <!-- 不能用 el-radio-group 的 @change 实现"再点一次取消"：
                 change 只在值**真的变化**时触发，点击已选中的单选按钮
                 不会产生 change（单选语义上不允许"一个都不选"）。
                 改为监听每个按钮的 click，由 togglePace 自己判断。 -->
            <el-radio-group :model-value="form.pace">
              <el-radio-button
                v-for="p in paceOptions"
                :key="p"
                :value="p"
                @click="togglePace(p)"
              >
                {{ p }}
              </el-radio-button>
            </el-radio-group>
            <span class="form-hint">决定一天排几个点、每站待多久；再点一次可取消</span>
          </el-form-item>

          <el-form-item label="时间安排">
            <el-radio-group :model-value="timeKey">
              <el-radio-button
                v-for="opt in timeOptions"
                :key="opt.label"
                :value="opt.label"
                @click="toggleTime(opt)"
              >
                {{ opt.label }}
              </el-radio-button>
            </el-radio-group>
            <span class="form-hint">不选则默认 09:00–21:00；再点一次可取消</span>
          </el-form-item>

          <el-button type="primary" size="large" class="plan-btn" @click="handleSubmit">
            开始规划旅程
          </el-button>
        </el-form>
        <!-- 必去 / 避开：必须可见可删，否则解析结果无法核对 -->
        <div v-if="(form.must_go || []).length || (form.avoid || []).length" class="place-block">
          <div v-if="(form.must_go || []).length" class="receipt-line">
            <el-tag type="danger" size="small" effect="plain">必去</el-tag>
            <span class="place-chips">
              <el-tag
                v-for="name in form.must_go"
                :key="name"
                class="place-chip must"
                closable
                size="small"
                @close="removePlace('must_go', name)"
              >{{ name }}</el-tag>
            </span>
          </div>
          <div v-if="(form.avoid || []).length" class="receipt-line">
            <el-tag size="small" effect="plain" type="info">避开</el-tag>
            <span class="place-chips">
              <el-tag
                v-for="name in form.avoid"
                :key="name"
                class="place-chip avoid"
                closable
                size="small"
                type="info"
                @close="removePlace('avoid', name)"
              >{{ name }}</el-tag>
            </span>
          </div>
          <div class="place-note">这些地点会作为硬约束参与规划：不会被预算或评分排序挤掉。</div>
        </div>

        <!-- 导入线路/地点 -->
        <div class="import-entry" @click="openImport">
          <div class="import-entry-left">
            <div class="import-entry-title">
              <el-icon color="var(--app-brand)"><Download /></el-icon>导入线路/地点
            </div>
            <div class="import-entry-desc">粘贴攻略文字，自动识别地点并逐一到高德校验</div>
          </div>
          <el-button type="primary" plain size="small">导入</el-button>
        </div>
      </div>
    </div>

    <!-- 右：历史 + 热门 -->
    <div class="col-right">
      <div class="pc-card">
        <div class="right-head">
          <h3 class="pc-section-title" style="margin: 0">
            <el-icon color="var(--app-brand)"><Clock /></el-icon>
            {{ isLoggedIn ? '我的行程' : '本机线路' }}
          </h3>
          <el-button
            link
            type="primary"
            @click="router.push(isLoggedIn ? '/history' : '/login')"
          >
            查看全部
          </el-button>
        </div>

        <div v-loading="tripLoading" class="trip-list">
          <el-empty v-if="!trips.length" description="还没有行程，先规划一条吧" :image-size="80" />
          <div v-for="trip in trips.slice(0, 6)" :key="trip.id" class="trip-item" @click="openTrip(trip)">
            <div class="trip-main">
              <div class="trip-title">{{ trip.title }}</div>
              <div class="trip-meta">
                <span><el-icon><Location /></el-icon>{{ trip.city }}</span>
                <span><el-icon><Clock /></el-icon>{{ trip.days }} 天</span>
              </div>
            </div>
            <div class="trip-side">
              <div class="trip-date">{{ trip.date }}</div>
              <el-tag v-if="trip.spot_count" size="small" effect="plain">
                {{ trip.spot_count }} 个安排
              </el-tag>
            </div>
          </div>
        </div>
      </div>

      <div class="pc-card">
        <h3 class="pc-section-title"><el-icon color="var(--app-brand)"><Promotion /></el-icon>热门目的地</h3>
        <div class="hot-list">
          <div v-for="d in hotDestinations" :key="d.name" class="hot-item" @click="applyHot(d.name)">
            <span class="hot-name">{{ d.name }}</span>
            <el-progress :percentage="d.percent" :show-text="false" :stroke-width="6" />
            <span class="hot-percent">{{ d.percent }}%</span>
          </div>
        </div>
      </div>
    </div>

    
<!-- 导入弹层：粘贴文字 → 识别 → 勾选加入必去
         必须放在根节点内部：Home.vue 若出现多个根节点（fragment），
         配合路由的异步组件会触发 Vue 内部 locateNonHydratedAsyncRoot 报错、
         整页渲染不出来。 -->
    <el-dialog v-model="showImport" title="导入线路/地点" width="620px" top="6vh">
    <div class="import-desc">
      粘贴攻略、聊天记录或行程单，系统会从中识别地点，并到高德逐个核对是否真实存在。
    </div>

    <el-input
      v-model="importTextValue"
      type="textarea"
      :rows="6"
      maxlength="6000"
      resize="none"
      placeholder="例如：D1 上午去拙政园，中午在平江路吃饭，下午逛苏州博物馆，晚上到山塘街看夜景"
    />
    <div class="import-toolbar">
      <el-button type="primary" :loading="importing" @click="runImport">
        {{ importing ? '正在识别并核对…' : '识别地点' }}
      </el-button>
      <span v-if="form.city" class="pc-muted">将在「{{ form.city }}」范围内核对</span>
      <span v-else class="pc-muted">未选择目的地，识别结果无法核对</span>
    </div>

    <div v-if="importResult" class="import-result">
      <template v-if="importResult.places.length">
        <div v-if="importResult.matched_count" class="import-group">
          <div class="import-group-title ok">
            已核对存在（{{ importResult.matched_count }}）· 点击可取消/勾选
          </div>
          <div class="import-chips">
            <el-tag
              v-for="p in importResult.places.filter((x) => x.matched)"
              :key="p.query"
              class="import-chip"
              :type="importPicked.includes(p.poi.name) ? 'success' : 'info'"
              :effect="importPicked.includes(p.poi.name) ? 'dark' : 'plain'"
              @click="toggleImportPick(p.poi.name)"
            >
              {{ p.poi.name }}<span v-if="p.poi.rating"> {{ p.poi.rating }}分</span>
            </el-tag>
          </div>
        </div>

        <div v-if="importResult.unmatched_count" class="import-group">
          <div class="import-group-title bad">
            未匹配到（{{ importResult.unmatched_count }}）· 不会加入行程
          </div>
          <div v-for="p in importResult.places.filter((x) => !x.matched)" :key="p.query" class="import-miss">
            <span class="import-miss-name">{{ p.query }}</span>
            <span class="import-miss-reason">{{ p.reason }}</span>
          </div>
        </div>

        <div v-for="(n, i) in importResult.notes" :key="i" class="import-note">{{ n }}</div>
      </template>
      <el-empty v-else description="没有识别到地点，试试粘贴更完整的行程文字" :image-size="60" />
    </div>

    <template #footer>
      <el-button @click="showImport = false">取消</el-button>
      <el-button
        type="primary"
        :disabled="!importPicked.length"
        @click="applyImport"
      >
        把 {{ importPicked.length }} 个地点加入必去
      </el-button>
    </template>
  </el-dialog>
    </div>
  </div>
</template>

<style scoped>
.home-layout {
  display: grid;
  grid-template-columns: minmax(0, 1.35fr) minmax(340px, 1fr);
  gap: 20px;
  align-items: start;
  max-width: 1440px;
  margin: 0 auto;
}
.col-left,
.col-right {
  display: flex;
  flex-direction: column;
  gap: 20px;
  min-width: 0;
}
.nl-actions {
  margin-top: 14px;
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}
.nl-tip {
  margin-left: auto;
}
.nl-receipt {
  margin-top: 14px;
  padding-top: 12px;
  border-top: 1px dashed var(--app-border);
  display: flex;
  flex-direction: column;
  gap: 8px;
}
/* 必去 / 避开 的地点 chips */
.place-block {
  margin-top: 12px;
  padding-top: 12px;
  border-top: 1px dashed var(--app-border);
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.place-chips { display: flex; flex-wrap: wrap; gap: 6px; }
.place-chip.avoid { text-decoration: line-through; }
.place-note { font-size: 11px; color: var(--app-text-secondary); line-height: 1.6; }

/* 导入入口 */
.import-entry {
  margin-top: 14px;
  padding: 12px 14px;
  border: 1px dashed var(--app-border);
  border-radius: 10px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  cursor: pointer;
  transition: all 0.15s;
}
.import-entry:hover { border-color: var(--app-brand); background: var(--app-bg-subtle); }
.import-entry-title {
  display: flex; align-items: center; gap: 6px;
  font-size: 14px; font-weight: 600; color: var(--app-text);
}
.import-entry-desc { margin-top: 4px; font-size: 12px; color: var(--app-text-secondary); }

/* 导入弹层 */
.import-desc { margin-bottom: 10px; font-size: 12px; color: var(--app-text-secondary); line-height: 1.6; }
.import-image-bar {
  display: flex;
  align-items: center;
  gap: 10px;
  margin: 10px 0 12px;
}
.import-image-input { display: none; }

.import-toolbar { margin-top: 12px; display: flex; align-items: center; gap: 12px; }
.import-result { margin-top: 16px; display: flex; flex-direction: column; gap: 14px; }
.import-group-title { font-size: 12px; font-weight: 600; margin-bottom: 8px; }
.import-group-title.ok { color: var(--app-brand-hover); }
.import-group-title.bad { color: var(--app-warning); }
.import-chips { display: flex; flex-wrap: wrap; gap: 8px; }
.import-chip { cursor: pointer; }
.import-miss {
  display: flex; align-items: baseline; gap: 8px;
  padding: 7px 10px; margin-bottom: 6px; border-radius: 8px;
  background: var(--app-warning-soft); font-size: 12px;
}
.import-miss-name { color: var(--app-text); font-weight: 600; flex-shrink: 0; }
.import-miss-reason { color: var(--app-warning); line-height: 1.5; }
.import-note { font-size: 11px; color: var(--app-text-secondary); line-height: 1.6; }
.receipt-line {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  font-size: 13px;
  color: var(--app-text-secondary);
  line-height: 1.6;
}
.receipt-line .el-tag {
  flex-shrink: 0;
}
.warn-text {
  color: var(--app-warning);
}
.form-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 0 18px;
}
.plan-btn {
  width: 100%;
  margin-top: 6px;
  background: var(--pc-gradient);
  border: none;
}
.right-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 12px;
}
.trip-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
  min-height: 60px;
}
.trip-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 12px 14px;
  border-radius: 8px;
  border: 1px solid var(--app-bg-subtle);
  cursor: pointer;
  transition: all 0.2s;
}
.trip-item:hover {
  border-color: var(--app-brand-soft);
  background: var(--app-bg-subtle);
  transform: translateY(-1px);
}
.trip-main {
  min-width: 0;
}
.trip-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--app-text);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.trip-meta {
  margin-top: 6px;
  display: flex;
  gap: 14px;
  font-size: 12px;
  color: var(--app-text-secondary);
}
.trip-meta span {
  display: inline-flex;
  align-items: center;
  gap: 4px;
}
.trip-side {
  flex-shrink: 0;
  text-align: right;
}
.trip-date {
  font-size: 12px;
  color: var(--app-text-secondary);
  margin-bottom: 6px;
}
.hot-list {
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.hot-item {
  display: grid;
  grid-template-columns: 52px 1fr 42px;
  align-items: center;
  gap: 10px;
  cursor: pointer;
}
.hot-name {
  font-size: 14px;
  color: var(--app-text);
}
.hot-percent {
  font-size: 12px;
  color: var(--app-text-secondary);
  text-align: right;
}

.form-hint{margin-left:10px;font-size:12px;color:var(--app-text-tertiary)}
/* 表单分组标签：给平铺的控件加停顿点，让"必填"与"可选"一眼分得开。
   **必须横跨整行**（grid-column: 1 / -1）——form-grid 是两列网格，
   不跨行的话标签会占掉第一格，把「出发地」挤到右边与它并排。 */
.form-group-label {
  grid-column: 1 / -1;
  display: flex; align-items: baseline; gap: 10px;
  margin: 6px 0 10px; padding-bottom: 6px;
  border-bottom: 1px solid var(--app-border);
  font-size: 12.5px; font-weight: 600; color: var(--app-text-secondary);
}
.form-group-label:not(:first-child) { margin-top: 20px; }
.form-group-hint { font-size: 11.5px; font-weight: 400; color: var(--app-text-tertiary); }
/* 叙事首屏：占满视口宽度，与下方两栏内容留一点呼吸 */
.home-hero { margin-bottom: var(--app-space-xl); }
.pc-home { max-width: 1440px; margin: 0 auto; padding: 0 var(--app-space-lg) var(--app-space-2xl); }
.home-hero :deep(.story-body) { padding-bottom: var(--app-space-3xl, 64px); }

/* 隐藏的文件输入：由按钮代理点击 */
.hidden-file { display: none; }</style>
