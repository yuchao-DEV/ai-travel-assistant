<template>
  <!-- 桌面端的分步引导（黑蒙层气泡）。
       为什么不用 Element Plus 的 el-tour：默认样式是「白盒子 + 小灰字 + 默认蓝按钮」，
       和这个项目的森系 + 衬线刊头完全不是一套语言，装上去就是"库的默认脸"。
       自己实现能控制：序号、衬线标题、进度、跳过、以及高亮框的圆角与留白。 -->
  <teleport to="body">
    <transition name="st-fade">
      <div v-if="open" class="st-root">
        <!-- 蒙层：用四块矩形拼出"洞"，比 SVG mask 更好控制圆角与动画 -->
        <div class="st-mask" @click="onMaskClick"></div>

        <!-- 高亮框（仅描边 + 柔和光晕，不高亮填充，避免遮住目标内容） -->
        <div class="st-hole" :style="holeStyle">
          <span class="st-hole-ring"></span>
        </div>

        <!-- 步骤卡 -->
        <div
          ref="cardEl"
          class="st-card"
          :class="`is-${actualPlacement}`"
          :style="cardStyle"
          role="dialog"
          aria-modal="true"
          :aria-label="current.title"
        >
          <span class="st-arrow" aria-hidden="true"></span>

          <div class="st-head">
            <span class="st-step">
              <b>{{ String(index + 1).padStart(2, '0') }}</b>
              <i>/ {{ String(steps.length).padStart(2, '0') }}</i>
            </span>
            <button type="button" class="st-skip" @click="finish('skip')">跳过引导</button>
          </div>

          <h3 class="st-title">{{ current.title }}</h3>
          <p class="st-desc">{{ current.desc }}</p>
          <p v-if="current.hint" class="st-hint">{{ current.hint }}</p>

          <div class="st-foot">
            <div class="st-dots" aria-hidden="true">
              <span v-for="(s, i) in steps" :key="s.title" class="dot" :class="{ active: i === index }"></span>
            </div>
            <div class="st-btns">
              <button v-if="index > 0" type="button" class="st-ghost" @click="prev">上一步</button>
              <button type="button" class="st-primary" @click="next">
                {{ index === steps.length - 1 ? '开始使用' : '下一步' }}
              </button>
            </div>
          </div>
        </div>
      </div>
    </transition>
  </teleport>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'

const props = defineProps({
  modelValue: { type: Boolean, default: false },
  /** [{ target: CSS 选择器, title, desc, hint? }] */
  steps: { type: Array, default: () => [] },
})
const emit = defineEmits(['update:modelValue', 'finish'])

const open = ref(false)
const index = ref(0)
const cardEl = ref(null)
const hole = ref({ top: 0, left: 0, width: 0, height: 0 })
const cardPos = ref({ top: 0, left: 0 })
const actualPlacement = ref('bottom')

const current = computed(() => props.steps[index.value] || { title: '', desc: '' })

const PAD = 8 // 高亮框相对目标外扩的留白
const CARD_W = 340
const CARD_GAP = 14

const holeStyle = computed(() => ({
  top: `${Math.round(hole.value.top)}px`,
  left: `${Math.round(hole.value.left)}px`,
  width: `${Math.round(hole.value.width)}px`,
  height: `${Math.round(hole.value.height)}px`,
}))

const cardStyle = computed(() => ({
  top: `${Math.round(cardPos.value.top)}px`,
  left: `${Math.round(cardPos.value.left)}px`,
  width: `${CARD_W}px`,
}))

function targetEl() {
  const sel = current.value && current.value.target
  return sel ? document.querySelector(sel) : null
}

function measure() {
  const el = targetEl()
  const card = cardEl.value
  if (!el) return
  const r = el.getBoundingClientRect()
  hole.value = {
    top: r.top - PAD,
    left: r.left - PAD,
    width: r.width + PAD * 2,
    height: r.height + PAD * 2,
  }

  const cardH = card ? card.offsetHeight : 200
  const vh = window.innerHeight
  const vw = window.innerWidth
  const below = vh - (r.bottom + PAD)
  const above = r.top - PAD
  const placement = below >= cardH + CARD_GAP || below >= above ? 'bottom' : 'top'
  actualPlacement.value = placement

  let top = placement === 'bottom'
    ? r.bottom + PAD + CARD_GAP
    : r.top - PAD - CARD_GAP - cardH
  let left = r.left + r.width / 2 - CARD_W / 2

  left = Math.max(16, Math.min(left, vw - CARD_W - 16))
  top = Math.max(16, Math.min(top, vh - cardH - 16))
  cardPos.value = { top, left }
}

let raf = 0
function measureSoon() {
  cancelAnimationFrame(raf)
  raf = requestAnimationFrame(measure)
}

/**
 * 把目标滚到"舒服的位置"。
 *
 * 不用 scrollIntoView：桌面端的滚动发生在 `.el-main` 容器里，
 * 而且 `block: 'center'` 会让首屏的叙事大图残留一条深色边缘露在内容上方，
 * 看起来像一条黑带（截图里非常明显）。这里自己算 scrollTop，
 * 把目标放在视口 34% 高度处——比居中多滚一点，正好把首屏大图完全送出视口。
 */
function scrollTargetIntoView(el) {
  const rect = el.getBoundingClientRect()
  const vh = window.innerHeight
  if (rect.top >= 96 && rect.bottom <= vh - 160) return

  let scroller = null
  let p = el.parentElement
  while (p) {
    const s = getComputedStyle(p)
    if (/(auto|scroll)/.test(s.overflowY) && p.scrollHeight > p.clientHeight + 4) {
      scroller = p
      break
    }
    p = p.parentElement
  }
  const desiredTop = Math.round(vh * 0.34)
  const delta = rect.top - desiredTop
  const opts = { behavior: 'smooth' }
  if (scroller) {
    scroller.scrollTo({ top: scroller.scrollTop + delta, ...opts })
  } else {
    window.scrollTo({ top: window.scrollY + delta, ...opts })
  }
}

async function showStep(i) {
  index.value = Math.max(0, Math.min(i, props.steps.length - 1))
  await nextTick()
  const el = targetEl()
  if (el) {
    scrollTargetIntoView(el)
    // smooth 滚动没有回调，等落位后再量（这段时间卡片先用上一帧位置，不会跳错）
    await new Promise((res) => setTimeout(res, 360))
  }
  measure()
  measureSoon()
}

function next() {
  if (index.value >= props.steps.length - 1) return finish('done')
  showStep(index.value + 1)
}

function prev() {
  showStep(index.value - 1)
}

function finish(reason) {
  open.value = false
  emit('update:modelValue', false)
  emit('finish', reason)
}

function onMaskClick() {
  // 点蒙层不关闭：误触会让用户丢失进度；只有「跳过」和「开始使用」能离开
}

function onKey(e) {
  if (e.key === 'Escape') finish('skip')
  else if (e.key === 'ArrowRight') next()
  else if (e.key === 'ArrowLeft') prev()
}

watch(
  () => props.modelValue,
  async (v) => {
    open.value = v
    if (v) {
      document.addEventListener('keydown', onKey)
      window.addEventListener('resize', measureSoon)
      window.addEventListener('scroll', measureSoon, true)
      await showStep(0)
    } else {
      document.removeEventListener('keydown', onKey)
      window.removeEventListener('resize', measureSoon)
      window.removeEventListener('scroll', measureSoon, true)
    }
  },
  { immediate: true },
)

onBeforeUnmount(() => {
  document.removeEventListener('keydown', onKey)
  window.removeEventListener('resize', measureSoon)
  window.removeEventListener('scroll', measureSoon, true)
})
</script>

<style scoped>
.st-root {
  position: fixed;
  inset: 0;
  z-index: 2700;
}

/* 全屏透明层：只负责挡住页面交互（避免引导期间误点表单） */
.st-mask {
  position: absolute;
  inset: 0;
  background: transparent;
}

/* 高亮框：用超大 spread 的投影把"洞**之外**"压暗，
   洞内保持原始亮度——这才是聚光灯（spotlight）的正确做法。
   早先版本在整层盖了一层半透明底，结果是目标也被压暗，等于没高亮。 */
.st-hole {
  position: absolute;
  border-radius: 14px;
  box-shadow: 0 0 0 100vmax rgba(18, 26, 22, 0.42);
  transition: top 0.24s var(--app-ease-out, ease-out), left 0.24s var(--app-ease-out, ease-out),
              width 0.24s var(--app-ease-out, ease-out), height 0.24s var(--app-ease-out, ease-out);
  pointer-events: none;
}
.st-hole-ring {
  position: absolute;
  inset: -1px;
  border-radius: 15px;
  border: 1.5px solid var(--app-brand-bright, #488a6c);
  box-shadow: 0 0 0 4px rgba(72, 138, 108, 0.18), 0 0 24px rgba(72, 138, 108, 0.22);
}

.st-card {
  position: absolute;
  background: var(--app-surface);
  border-radius: 16px;
  padding: 18px 20px 16px;
  box-shadow: 0 24px 60px rgba(12, 20, 17, 0.26);
  border: 1px solid var(--app-hairline);
  transition: top 0.24s var(--app-ease-out, ease-out), left 0.24s var(--app-ease-out, ease-out);
}
.st-arrow {
  position: absolute;
  width: 12px;
  height: 12px;
  background: var(--app-surface);
  border-left: 1px solid var(--app-hairline);
  border-top: 1px solid var(--app-hairline);
  transform: rotate(45deg);
}
.is-bottom .st-arrow {
  top: -7px;
  left: 32px;
}
.is-top .st-arrow {
  bottom: -7px;
  left: 32px;
  transform: rotate(225deg);
}

.st-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 10px;
}
.st-step {
  font-family: var(--app-font-serif, 'Songti SC', serif);
  color: var(--app-text-tertiary);
  font-size: 12px;
  letter-spacing: 0.06em;
}
.st-step b {
  font-size: 17px;
  font-weight: 600;
  font-style: italic;
  color: var(--app-brand);
  margin-right: 3px;
}
.st-step i {
  font-style: normal;
  font-size: 11.5px;
}
.st-skip {
  appearance: none;
  border: none;
  background: transparent;
  color: var(--app-text-tertiary);
  font: inherit;
  font-size: 12px;
  padding: 4px 2px;
  cursor: pointer;
  text-decoration: underline;
  text-underline-offset: 3px;
}
.st-skip:hover {
  color: var(--app-text-secondary);
}
.st-skip:focus-visible {
  outline: 2px solid var(--app-brand);
  outline-offset: 2px;
}

.st-title {
  margin: 0 0 8px;
  font-family: var(--app-font-serif, 'Songti SC', serif);
  font-size: 18px;
  font-weight: 700;
  line-height: 1.4;
  color: var(--app-text);
  letter-spacing: -0.01em;
}
.st-desc {
  margin: 0;
  font-size: 13px;
  line-height: 1.8;
  color: var(--app-text-secondary);
}
.st-hint {
  margin: 9px 0 0;
  padding: 7px 10px;
  border-radius: 8px;
  background: var(--app-brand-soft, rgba(66, 127, 98, 0.09));
  font-size: 12px;
  line-height: 1.65;
  color: var(--app-brand-hover);
}

.st-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: 16px;
  gap: 12px;
}
.st-dots {
  display: flex;
  gap: 5px;
}
.dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--app-hairline-strong, rgba(0, 0, 0, 0.16));
}
.dot.active {
  width: 16px;
  border-radius: 3px;
  background: var(--app-brand);
  transition: width 0.2s var(--app-ease-out, ease-out);
}
.st-btns {
  display: flex;
  align-items: center;
  gap: 8px;
}
.st-ghost {
  appearance: none;
  border: 1px solid var(--app-hairline-strong, rgba(0, 0, 0, 0.12));
  background: transparent;
  color: var(--app-text);
  font: inherit;
  font-size: 13px;
  padding: 8px 16px;
  border-radius: 999px;
  cursor: pointer;
}
.st-primary {
  appearance: none;
  border: none;
  background: var(--app-brand);
  color: #fff;
  font: inherit;
  font-size: 13px;
  font-weight: 600;
  padding: 9px 20px;
  border-radius: 999px;
  cursor: pointer;
}
.st-primary:hover {
  background: var(--app-brand-hover);
}
.st-ghost:hover {
  background: var(--app-brand-soft, rgba(66, 127, 98, 0.08));
}
.st-ghost:focus-visible,
.st-primary:focus-visible {
  outline: 2px solid var(--app-brand);
  outline-offset: 2px;
}

.st-fade-enter-active,
.st-fade-leave-active {
  transition: opacity 0.2s var(--app-ease-out, ease-out);
}
.st-fade-enter-from,
.st-fade-leave-to {
  opacity: 0;
}

@media (prefers-reduced-motion: reduce) {
  .st-fade-enter-active,
  .st-fade-leave-active,
  .st-hole,
  .st-card,
  .dot.active {
    transition: none;
  }
}
</style>
