<template>
  <section class="story-section" :class="`story-${variant}`" :style="sectionStyle">
    <!-- 跳过叙事：视差/动效不适的用户要能直达内容 -->
    <button
      v-if="skippable"
      type="button"
      class="story-skip"
      @click="$emit('skip')"
    >跳过叙述</button>

    <img
      v-if="image"
      ref="bgEl"
      class="story-bg"
      :src="image"
      :alt="imageAlt || ''"
      loading="lazy"
      decoding="async"
    />
    <!-- 压暗层：保证白字在任意照片上都能达到 4.5:1。
         照片亮度不可控（樱花/雪景/天空都可能很亮），没有这层就必然撞对比度。 -->
    <div v-if="image && variant === 'dark'" class="story-scrim"></div>

    <div class="story-body" :ref="bodyEl">
      <div class="story-reveal" :class="{ 'is-in': revealed }">
        <div v-if="numeral" class="story-num">{{ numeral }}</div>
        <div v-if="kicker" class="story-kicker">{{ kicker }}</div>
        <h2 v-if="title" class="story-title">{{ title }}</h2>
        <p v-if="text" class="story-text">{{ text }}</p>
        <div class="story-rule"></div>
        <div v-if="cue" class="story-scroll-cue">{{ cue }}</div>
        <slot />
      </div>
    </div>
  </section>
</template>

<script setup>
/**
 * 叙事滚动的章节块（Scrolltelling）。
 *
 * **三条硬约束**（都来自技能库对 parallax-storytelling 的判定）：
 *
 * 1. **默认可见**。`.story-reveal` 只有在
 *    `prefers-reduced-motion: no-preference` 时才初始透明；
 *    JS 未运行 / IntersectionObserver 不可用 / 用户关了动效，
 *    内容都必须照常显示 —— 否则等于白屏。
 * 2. **JS 只写一个 CSS 变量**。视差位移通过 `--story-shift` 交给 CSS，
 *    不在每一帧里改 `transform` 字符串（那样会强制重排）。
 * 3. **必须能跳过**。叙事对"我只想快点看行程"的用户是障碍。
 */
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'

const props = defineProps({
  /** 背景照片 URL（来自高德 POI.photos） */
  image: { type: String, default: '' },
  imageAlt: { type: String, default: '' },
  /** 章节序号，如 '01' */
  numeral: { type: String, default: '' },
  /** 上方小字，如城市名 */
  kicker: { type: String, default: '' },
  /** 衬线大标题 */
  title: { type: String, default: '' },
  /** 正文（行程用每日导语） */
  text: { type: String, default: '' },
  /** 底部滚动提示 */
  cue: { type: String, default: '' },
  /** 是否显示"跳过叙述" */
  skippable: { type: Boolean, default: false },
  /** 视差强度倍率，1 = 默认 */
  parallax: { type: Number, default: 1 },
  /**
   * 视觉变体：dark = 深色全幅影院感（整屏 hero）；
   *          light = 浅色编辑排版（与浅色工具面板并存）
   */
  variant: { type: String, default: 'dark' },
})

defineEmits(['skip'])

const bgEl = ref(null)
const bodyEl = ref(null)
const revealed = ref(false)

const sectionStyle = computed(() => ({
  '--app-story-min-h': '78vh',
  '--parallax-factor': String(props.parallax),
}))

let observer = null
let onScroll = null
let raf = 0

function prefersReducedMotion() {
  return typeof window !== 'undefined'
    && window.matchMedia
    && window.matchMedia('(prefers-reduced-motion: reduce)').matches
}

onMounted(() => {
  // ---- 揭示：用 IntersectionObserver，不用 scroll 事件 ----
  if (typeof IntersectionObserver === 'function' && bodyEl.value) {
    observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (entry.isIntersecting) {
            revealed.value = true
            observer.disconnect()
            observer = null
          }
        }
      },
      { threshold: 0.18 },
    )
    observer.observe(bodyEl.value)
  } else {
    // 没有 IO 也要显示内容——这正是"默认可见"的意义
    revealed.value = true
  }

  // ---- 视差：只在"支持动效且用户没关"时挂 scroll ----
  // 用 passive + rAF 节流，避免阻塞滚动主线程
  // 浅色编辑排版不做视差：它会打断阅读，与安静的调子相反
  if (!prefersReducedMotion() && props.variant === 'dark') {
    onScroll = () => {
      if (raf) return
      raf = requestAnimationFrame(() => {
        raf = 0
        const el = bgEl.value
        if (!el) return
        const rect = el.parentElement?.getBoundingClientRect()
        if (!rect) return
        // 元素相对视口中心的偏移，乘倍率得到位移像素
        const shift = (rect.top - window.innerHeight / 2)
          * -0.08 * props.parallax
        el.style.setProperty('--story-shift', shift.toFixed(2))
      })
    }
    window.addEventListener('scroll', onScroll, { passive: true })
    onScroll()
  }
})

onBeforeUnmount(() => {
  if (observer) { observer.disconnect(); observer = null }
  if (onScroll) { window.removeEventListener('scroll', onScroll); onScroll = null }
  if (raf) { cancelAnimationFrame(raf); raf = 0 }
})
</script>
