<template>
  <!-- 小气泡引导：轻量、一次性、就地指向某个元素。
       纯 HTML + 令牌 + teleport，双端共用。
       定位自己算（不用 popper），因为只需要"下方优先、放不下翻到上方、左右夹在视口内"，
       引一个定位库不值得。 -->
  <teleport to="body">
    <transition name="bt-fade">
      <div
        v-if="open"
        ref="tipEl"
        class="bubble-tip"
        :class="`is-${actualPlacement}`"
        :style="styleObj"
        role="tooltip"
        :aria-label="ariaLabel"
      >
        <span class="bt-arrow" aria-hidden="true"></span>
        <div class="bt-body">
          <div v-if="title" class="bt-title">{{ title }}</div>
          <slot />
        </div>
        <div v-if="showClose" class="bt-foot">
          <button type="button" class="bt-ok" @click="close">{{ closeLabel }}</button>
        </div>
      </div>
    </transition>
  </teleport>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'

const props = defineProps({
  modelValue: { type: Boolean, default: false },
  /** 指向的元素：传函数以避免模板里 ref 还没就绪 */
  target: { type: Function, default: null },
  /** auto | top | bottom */
  placement: { type: String, default: 'auto' },
  /** 可选标题：加一行小衬线标题，避免小气泡看起来只是一段"黑底文字" */
  title: { type: String, default: '' },
  /** 目标不在视口内时是否先滚动过去（默认 true；关闭后气泡会飘在屏外） */
  autoScroll: { type: Boolean, default: true },
  maxWidth: { type: Number, default: 260 },
  showClose: { type: Boolean, default: true },
  closeLabel: { type: String, default: '知道了' },
  ariaLabel: { type: String, default: '引导提示' },
})
const emit = defineEmits(['update:modelValue', 'close'])

const tipEl = ref(null)
const open = ref(false)
const actualPlacement = ref('bottom')
const pos = ref({ top: 0, left: 0 })

// 注意必须用 computed：`:style="posStyle"` 传函数引用不会被执行，
// 结果是气泡留在文档流里（top 变成页面高度），看起来像"没显示"。
const styleObj = computed(() => ({
  top: `${Math.round(pos.value.top)}px`,
  left: `${Math.round(pos.value.left)}px`,
  maxWidth: `${props.maxWidth}px`,
}))

function el() {
  const v = props.target
  if (!v) return null
  const node = typeof v === 'function' ? v() : v
  return node && node.getBoundingClientRect ? node : null
}

function place() {
  const node = el()
  const tip = tipEl.value
  if (!node || !tip) return
  const r = node.getBoundingClientRect()
  const tw = tip.offsetWidth || props.maxWidth
  const th = tip.offsetHeight || 90
  const vh = window.innerHeight
  const vw = window.innerWidth
  const GAP = 12
  const MARGIN = 8

  // 上下选择：优先下方；下方放不下且上方更宽裕时翻到上方
  let placement = props.placement
  if (placement === 'auto') {
    const below = vh - r.bottom
    placement = below >= th + GAP || below >= r.top ? 'bottom' : 'top'
  }
  actualPlacement.value = placement

  let top = placement === 'bottom' ? r.bottom + GAP : r.top - th - GAP
  let left = r.left + r.width / 2 - tw / 2

  // 夹在视口内
  left = Math.max(MARGIN, Math.min(left, vw - tw - MARGIN))
  top = Math.max(MARGIN, Math.min(top, vh - th - MARGIN))

  pos.value = { top, left }
}

let raf = 0
function placeSoon() {
  cancelAnimationFrame(raf)
  raf = requestAnimationFrame(place)
}

async function show() {
  open.value = true
  await nextTick()
  // 目标可能还在首屏下方（桌面首页的叙事大图占满一屏）：
  // 那时气泡会飘在大图上、指向屏幕外，比不弹更糟。先把目标滚进视口再定位。
  if (props.autoScroll) await scrollTargetIntoView()
  place()
  window.addEventListener('scroll', placeSoon, true)
  window.addEventListener('resize', placeSoon)
  document.addEventListener('keydown', onKey)
}

/**
 * 把目标滚到舒适位置（视口 38% 处）。
 * 桌面端的滚动在 `.el-main` 容器里，所以自己找可滚动祖先，而不是 window.scrollTo。
 */
async function scrollTargetIntoView() {
  const node = el()
  if (!node) return
  const r = node.getBoundingClientRect()
  const vh = window.innerHeight
  if (r.top >= 96 && r.bottom <= vh - 140) return

  let scroller = null
  let p = node.parentElement
  while (p) {
    const s = getComputedStyle(p)
    if (/(auto|scroll)/.test(s.overflowY) && p.scrollHeight > p.clientHeight + 4) {
      scroller = p
      break
    }
    p = p.parentElement
  }
  const delta = r.top - Math.round(vh * 0.38)
  const smooth = { behavior: 'smooth' }
  if (scroller) scroller.scrollTo({ top: scroller.scrollTop + delta, ...smooth })
  else window.scrollTo({ top: window.scrollY + delta, ...smooth })
  await new Promise((res) => setTimeout(res, 340))
}

function hide() {
  open.value = false
  window.removeEventListener('scroll', placeSoon, true)
  window.removeEventListener('resize', placeSoon)
  document.removeEventListener('keydown', onKey)
}

function close() {
  hide()
  emit('update:modelValue', false)
  emit('close')
}

function onKey(e) {
  if (e.key === 'Escape') close()
}

// 点击气泡外部关闭（但点目标元素本身不关，避免"想操作却把提示关了"）
function onDocClick(e) {
  if (!open.value) return
  const tip = tipEl.value
  const node = el()
  if (tip && tip.contains(e.target)) return
  if (node && node.contains(e.target)) return
  close()
}

watch(
  () => props.modelValue,
  (v) => {
    if (v) {
      show()
      // 延后一拍再挂，避免触发打开的那次点击立刻把它关掉
      setTimeout(() => document.addEventListener('click', onDocClick, true), 0)
    } else {
      document.removeEventListener('click', onDocClick, true)
      hide()
    }
  },
  { immediate: true },
)

onBeforeUnmount(() => {
  document.removeEventListener('click', onDocClick, true)
  hide()
})

defineExpose({ place })
</script>

<style scoped>
.bubble-tip {
  position: fixed;
  z-index: 2600;
  background: var(--app-text, #27312c);
  color: #fff;
  border-radius: 12px;
  padding: 12px 14px;
  box-shadow: 0 12px 32px rgba(0, 0, 0, 0.22);
  font-size: 13px;
  line-height: 1.68;
}
.bt-arrow {
  position: absolute;
  width: 10px;
  height: 10px;
  background: var(--app-text, #27312c);
  transform: rotate(45deg);
}
.is-bottom .bt-arrow {
  top: -5px;
  left: 50%;
  margin-left: -5px;
}
.is-top .bt-arrow {
  bottom: -5px;
  left: 50%;
  margin-left: -5px;
}

.bt-body :deep(p) {
  margin: 0;
}
.bt-body :deep(strong) {
  font-weight: 650;
}
.bt-title {
  font-family: var(--app-font-serif, 'Songti SC', serif);
  font-size: 14px;
  font-weight: 700;
  letter-spacing: 0.01em;
  color: #fff;
  margin-bottom: 6px;
}

.bt-foot {
  display: flex;
  justify-content: flex-end;
  margin-top: 10px;
}
.bt-ok {
  appearance: none;
  border: none;
  background: rgba(255, 255, 255, 0.16);
  color: #fff;
  font: inherit;
  font-size: 12.5px;
  padding: 6px 13px;
  border-radius: 999px;
  cursor: pointer;
}
.bt-ok:hover {
  background: rgba(255, 255, 255, 0.26);
}
.bt-ok:focus-visible {
  outline: 2px solid #fff;
  outline-offset: 2px;
}

.bt-fade-enter-active,
.bt-fade-leave-active {
  transition: opacity 0.18s var(--app-ease-out, ease-out),
              transform 0.18s var(--app-ease-out, ease-out);
}
.bt-fade-enter-from,
.bt-fade-leave-to {
  opacity: 0;
  transform: translateY(4px);
}

@media (prefers-reduced-motion: reduce) {
  .bt-fade-enter-active,
  .bt-fade-leave-active {
    transition: none;
  }
}
</style>
