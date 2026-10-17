<script setup>
/**
 * 旅行灵感卡片流。
 *
 * 两个地方共用同一个组件：
 * 1. 行程生成中的等待页——规划要 20–40 秒，用真实地点内容填住空窗期；
 * 2. 行程页「旅行灵感」Tab。
 *
 * 卡片只展示高德返回的真实字段（照片 / 评分 / 人均消费 / 商圈 / 营业时间），
 * 没有任何新闻式文案——那属于 UGC/编辑内容，本项目没有数据源，硬编就是编造。
 */
import { computed, ref } from 'vue'

const props = defineProps({
  items: { type: Array, default: () => [] },
  categories: { type: Array, default: () => [] },
  added: { type: Array, default: () => [] },   // 已加入必去的地点名
  loading: { type: Boolean, default: false },
})
const emit = defineEmits(['add'])

const activeCategory = ref('全部')

// 分类计数**只从真正拿到的卡片里算**——这样标签上的数字必然等于点进去能看到的张数。
// 之前用的是接口返回的"池子总数"，而过滤又是在这 24 张卡片里做的，
// 于是出现过"标签写美食 25、点进去只有 6 张"，甚至"美食 25 > 全部 24"。
const pills = computed(() => {
  const counter = {}
  props.items.forEach((it) => {
    counter[it.category] = (counter[it.category] || 0) + 1
  })
  // 顺序优先沿用后端给的类目顺序，其余按数量降序补齐
  const order = (props.categories || []).map((c) => c.key)
  const keys = Object.keys(counter).sort((a, b) => {
    const ia = order.indexOf(a); const ib = order.indexOf(b)
    if (ia !== -1 && ib !== -1) return ia - ib
    if (ia !== -1) return -1
    if (ib !== -1) return 1
    return counter[b] - counter[a]
  })
  return [{ key: '全部', count: props.items.length }]
    .concat(keys.map((k) => ({ key: k, count: counter[k] })))
})

const shown = computed(() => {
  if (activeCategory.value === '全部') return props.items
  return props.items.filter((it) => it.category === activeCategory.value)
})

const isAdded = (name) => props.added.includes(name)
const photoOf = (item) => (item.photos && item.photos.length ? item.photos[0] : '')
const fallbackText = (name) => (name || '地').slice(0, 1)
// 费用来源角标四档：real=高德真实人均；third_party=知识库真实票价；
// average=仅有优待票/付费服务时按均价算；reserved=无数据 → 不显示数字
const COST_LABELS = { real: '人均', third_party: '票价', average: '均价' }
const costLabel = (item) => COST_LABELS[item.cost_source] || ''
const showCost = (item) => item.cost_source !== 'reserved'
const costText = (item) => {
  if (item.cost_source === 'reserved') return '门票待确认'
  if (item.cost_source === 'third_party' && item.cost === 0) return '免费'
  return `${costLabel(item)}¥${item.cost}`
}
</script>

<template>
  <div class="insp-wrap">
    <div class="insp-head">
      <div class="insp-title">灵感 · 值得去的地方</div>
      <div class="insp-sub">来自高德真实数据：照片、评分、人均消费、营业时间</div>
    </div>

    <div v-if="pills.length > 1" class="insp-pills">
      <div
        v-for="p in pills"
        :key="p.key"
        class="insp-pill"
        :class="{ active: activeCategory === p.key }"
        @click="activeCategory = p.key"
      >
        {{ p.key }}<span class="insp-pill-count">{{ p.count }}</span>
      </div>
    </div>

    <div v-if="loading" class="insp-card-row">
      <div v-for="n in 3" :key="n" class="insp-card skeleton">
        <div class="insp-skel-img"></div>
        <div class="insp-skel-line"></div>
        <div class="insp-skel-line short"></div>
      </div>
    </div>

    <div v-else-if="shown.length" class="insp-card-row">
      <div v-for="(item, i) in shown" :key="item.name + i" class="insp-card">
        <div class="insp-img-box">
          <img v-if="photoOf(item)" :src="photoOf(item)" alt="" loading="lazy" />
          <div v-else class="insp-img-fallback">{{ fallbackText(item.name) }}</div>
          <span class="insp-cat">{{ item.category }}</span>
        </div>
        <div class="insp-body">
          <div class="insp-name">{{ item.name }}</div>
          <div class="insp-meta">
            <span v-if="item.rating" class="insp-rating">{{ item.rating }} 分</span>
            <span v-if="item.business_area" class="insp-area">{{ item.business_area }}</span>
          </div>
          <div v-if="item.scenic_level" class="insp-level">{{ item.scenic_level }}</div>
          <div v-else-if="item.open_hours" class="insp-hours">{{ item.open_hours }}</div>
          <div class="insp-foot">
            <!-- reserved 不显示数字（会让用户以为是真的票价），只显示「门票待确认」 -->
            <span v-if="!showCost(item)" class="insp-cost muted">门票待确认</span>
            <span v-else-if="item.cost > 0" class="insp-cost">{{ costText(item) }}</span>
            <span v-else class="insp-cost free">免费</span>
            <button
              class="insp-add"
              :class="{ added: isAdded(item.name) }"
              :disabled="isAdded(item.name)"
              @click.stop="emit('add', item.name)"
            >
              {{ isAdded(item.name) ? '已加入' : '＋ 加进行程' }}
            </button>
          </div>
        </div>
      </div>
    </div>

    <div v-else class="insp-empty">
      该目的地暂无可用灵感数据（高德未返回地点或服务暂不可用）
    </div>
  </div>
</template>

<style scoped>
.insp-wrap { padding: 4px 0 8px; }
.insp-head { padding: 0 16px 10px; }
.insp-title { font-size: 15px; font-weight: 600; color: var(--app-text); }
.insp-sub { margin-top: 4px; font-size: 11px; color: var(--app-text-secondary); line-height: 1.5; }

.insp-pills { display: flex; gap: 8px; overflow-x: auto; padding: 0 16px 12px; scrollbar-width: none; }
.insp-pills::-webkit-scrollbar { display: none; }
.insp-pill {
  flex-shrink: 0; display: flex; align-items: center; gap: 4px;
  padding: 5px 12px; border-radius: 15px; background: var(--app-bg-subtle);
  font-size: 13px; color: var(--app-text-secondary);
}
.insp-pill.active { background: var(--app-text); color: var(--app-surface); }
.insp-pill-count { font-size: 10px; opacity: .6; }

.insp-card-row {
  display: flex; gap: 12px; overflow-x: auto; padding: 0 16px 14px;
  scroll-snap-type: x mandatory; scrollbar-width: none;
}
.insp-card-row::-webkit-scrollbar { display: none; }
.insp-card {
  flex-shrink: 0; width: 208px; border-radius: 14px; overflow: hidden;
  background: var(--app-surface); box-shadow: 0 2px 10px rgba(0, 0, 0, .06);
  scroll-snap-align: start; display: flex; flex-direction: column;
}

/* 桌面端：**每行两个、纵向滚动**，不用手机端那种横向滑动。
   理由：桌面有滚轮和更大的可视区，横向滑动既有隐藏滚动条的问题，
   也让"浏览一堆地点"变成需要拖拽的动作。纵向网格更符合桌面习惯。 */
@media (min-width: 1024px) {
  .insp-card-row {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 14px;
    overflow-x: visible;
    overflow-y: visible;
    scroll-snap-type: none;
    padding-bottom: 4px;
  }
  .insp-card {
    width: auto;      /* 交给 grid 决定宽度 */
    min-width: 0;     /* 防止长名称把格子撑破 */
  }
  .insp-img-box { height: 150px; }
}
.insp-img-box { position: relative; width: 100%; height: 128px; background: var(--app-surface-muted); }
.insp-img-box img { width: 100%; height: 100%; object-fit: cover; display: block; }
/* 无真实照片时用首字占位，不用生成图冒充实景 */
.insp-img-fallback {
  width: 100%; height: 100%; display: flex; align-items: center; justify-content: center;
  font-size: 34px; font-weight: 700; color: var(--app-surface);
  background: linear-gradient(135deg, var(--app-accent-soft), var(--app-accent-bright));
}
.insp-cat {
  position: absolute; left: 8px; top: 8px; padding: 2px 7px; border-radius: 4px;
  background: rgba(0, 0, 0, .55); color: var(--app-surface); font-size: 10px;
}
.insp-body { flex: 1; display: flex; flex-direction: column; padding: 10px 12px 12px; }
.insp-name {
  font-size: 14px; font-weight: 600; color: var(--app-text); line-height: 1.35;
  display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden;
}
.insp-meta { margin-top: 5px; display: flex; align-items: center; gap: 8px; font-size: 11px; color: var(--app-text-secondary); }
.insp-rating { color: var(--app-warning); font-weight: 600; }
.insp-level { margin-top: 4px; font-size: 11px; color: var(--app-brand); }
.insp-hours {
  margin-top: 4px; font-size: 11px; color: var(--app-text-secondary); line-height: 1.4;
  display: -webkit-box; -webkit-line-clamp: 1; -webkit-box-orient: vertical; overflow: hidden;
}
.insp-foot { margin-top: auto; padding-top: 10px; display: flex; align-items: center; justify-content: space-between; gap: 8px; }
.insp-cost { font-size: 12px; color: var(--app-danger); font-weight: 600; }
.insp-cost.free { color: var(--app-brand-hover); }
.insp-add {
  border: none; border-radius: 14px; padding: 5px 11px; font-size: 12px;
  background: var(--app-text); color: var(--app-surface); cursor: pointer; white-space: nowrap;
}
.insp-add.added { background: var(--app-success-soft); color: var(--app-brand-hover); }

.insp-empty { padding: 22px 16px 30px; text-align: center; font-size: 12px; color: var(--app-text-secondary); line-height: 1.6; }

/* 骨架屏：等待期间避免白屏跳动 */
.skeleton { box-shadow: none; background: var(--app-surface); }
.insp-skel-img { height: 128px; background: linear-gradient(90deg, var(--app-surface-muted) 25%, var(--app-border) 37%, var(--app-surface-muted) 63%); background-size: 400% 100%; animation: inspShimmer 1.3s ease infinite; }
.insp-skel-line { height: 12px; margin: 12px 12px 0; border-radius: 4px; background: var(--app-surface-muted); }
.insp-skel-line.short { width: 55%; margin-top: 8px; }
@keyframes inspShimmer { 0% { background-position: 100% 50%; } 100% { background-position: 0 50%; } }

.cost-note{display:block;margin-top:4px;font-size:11px;color:var(--app-text-tertiary);line-height:1.6}
</style>
