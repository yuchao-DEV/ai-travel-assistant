<template>
  <!-- 移动端首访的整页引导（3 屏）。
       图里第 4 种「整页引导」：干扰最强、信息量最大，只用于"第一次认识产品"。
       它**不遮挡表单**——是独立一屏，看完才进首页，所以和"浮层压表单"是两回事。 -->
  <teleport to="body">
    <transition name="intro-fade">
      <div v-if="open" class="intro-overlay" role="dialog" aria-modal="true" aria-label="产品介绍">
        <div class="intro-panel">
          <button type="button" class="intro-skip" @click="finish('skip')">跳过</button>

          <div class="intro-slides">
            <section v-for="(s, i) in slides" :key="s.kicker" v-show="index === i" class="intro-slide">
              <div class="intro-visual" :class="`v${i + 1}`" aria-hidden="true">
                <span class="iv-num">{{ String(i + 1).padStart(2, '0') }}</span>
              </div>
              <div class="intro-kicker">{{ s.kicker }}</div>
              <h2 class="intro-title">{{ s.title }}</h2>
              <p class="intro-desc">{{ s.desc }}</p>
              <ul v-if="s.bullets" class="intro-list">
                <li v-for="b in s.bullets" :key="b">{{ b }}</li>
              </ul>
            </section>
          </div>

          <div class="intro-foot">
            <div class="intro-dots" role="tablist" aria-label="引导步骤">
              <button
                v-for="(s, i) in slides"
                :key="s.kicker"
                type="button"
                class="dot"
                :class="{ active: index === i }"
                :aria-label="`第 ${i + 1} 屏`"
                :aria-selected="index === i"
                @click="index = i"
              ></button>
            </div>
            <button type="button" class="intro-next" @click="next">
              {{ index === slides.length - 1 ? '开始使用' : '下一步' }}
            </button>
          </div>
        </div>
      </div>
    </transition>
  </teleport>
</template>

<script setup>
import { onBeforeUnmount, ref, watch } from 'vue'

const props = defineProps({ modelValue: { type: Boolean, default: false } })
const emit = defineEmits(['update:modelValue', 'finish'])

const open = ref(false)
const index = ref(0)

/**
 * 三屏必须讲"系统真做到的事"。
 * 文案里出现的每个能力都能在代码里找到：三维评分、硬约束、门票四档口径。
 */
const slides = [
  {
    kicker: 'AI TRAVEL · 算法驱动',
    title: '每一次出发，都排得出来',
    desc: '说出城市、天数和偏好，系统真实查询景点、算清距离与营业时间，排出一份能照着走的行程。',
    bullets: ['景点与坐标来自高德地图', '顺序与时间由算法引擎排定'],
  },
  {
    kicker: '三步就好',
    title: '选目的地 → 定偏好 → 生成',
    desc: '天数和偏好都可以留空，留空按默认规则排（经典 3 天）。想调节奏再填，不填不影响结果。',
    bullets: ['一次规划约 20–40 秒', '同一城市第二次命中缓存，明显更快'],
  },
  {
    kicker: '两种用法',
    title: '两种入口：填条件，或说一句需求',
    desc: '「规划」适合目标明确时按条件填；「AI 助手」适合只想说一句"杭州三天带老人别排太满"。',
    bullets: ['助手同样由算法出行程，不是编的', '不满意可以直接说「太满了」「换自然向」'],
  },
]

function finish(reason) {
  open.value = false
  emit('update:modelValue', false)
  emit('finish', reason)
}

function next() {
  if (index.value >= slides.length - 1) return finish('done')
  index.value += 1
}

function onKey(e) {
  if (e.key === 'Escape') finish('skip')
  if (e.key === 'ArrowRight') next()
  if (e.key === 'ArrowLeft') index.value = Math.max(0, index.value - 1)
}

watch(
  () => props.modelValue,
  (v) => {
    open.value = v
    if (v) {
      index.value = 0
      document.body.style.overflow = 'hidden'
      document.addEventListener('keydown', onKey)
    } else {
      document.body.style.overflow = ''
      document.removeEventListener('keydown', onKey)
    }
  },
  { immediate: true },
)

onBeforeUnmount(() => {
  document.body.style.overflow = ''
  document.removeEventListener('keydown', onKey)
})
</script>

<style scoped>
.intro-overlay {
  position: fixed;
  inset: 0;
  z-index: 2800;
  display: flex;
  align-items: stretch;
  justify-content: center;
  background: var(--app-bg, #f4f9f5);
}

.intro-panel {
  position: relative;
  width: 100%;
  max-width: 480px;
  display: flex;
  flex-direction: column;
  padding: calc(18px + env(safe-area-inset-top)) 22px calc(20px + env(safe-area-inset-bottom));
}

.intro-skip {
  position: absolute;
  top: 14px;
  right: 16px;
  appearance: none;
  border: 1px solid var(--app-hairline-strong, rgba(0, 0, 0, 0.12));
  background: transparent;
  color: var(--app-text-secondary);
  font: inherit;
  font-size: 12.5px;
  padding: 6px 13px;
  border-radius: 999px;
  cursor: pointer;
}
.intro-skip:focus-visible {
  outline: 2px solid var(--app-brand);
  outline-offset: 2px;
}

.intro-slides {
  flex: 1;
  display: flex;
  align-items: center;
  min-height: 0;
}
.intro-slide {
  width: 100%;
}

/* 视觉区：用大号衬线数字 + 品牌色块，不依赖图片素材 */
.intro-visual {
  position: relative;
  height: 168px;
  border-radius: 20px;
  margin-bottom: 22px;
  overflow: hidden;
  background: linear-gradient(150deg, var(--app-brand-soft, rgba(66, 127, 98, 0.16)), var(--app-surface));
  border: 1px solid var(--app-hairline);
  display: flex;
  align-items: center;
  justify-content: center;
}
.intro-visual.v2 {
  background: linear-gradient(30deg, var(--app-accent-soft, rgba(196, 138, 63, 0.16)), var(--app-surface));
}
.intro-visual.v3 {
  background: linear-gradient(120deg, var(--app-warning-soft, rgba(168, 106, 32, 0.14)), var(--app-surface));
}
.iv-num {
  font-family: var(--app-font-serif, 'Songti SC', serif);
  font-size: 84px;
  font-weight: 600;
  font-style: italic;
  line-height: 1;
  color: var(--app-brand);
  opacity: 0.5;
}

.intro-kicker {
  font-size: 11px;
  letter-spacing: 0.18em;
  color: var(--app-text-tertiary);
  text-transform: uppercase;
  margin-bottom: 10px;
}
.intro-title {
  margin: 0 0 12px;
  font-family: var(--app-font-serif, 'Songti SC', serif);
  font-size: 25px;
  line-height: 1.36;
  font-weight: 700;
  color: var(--app-text);
  letter-spacing: -0.01em;
}
.intro-desc {
  margin: 0;
  font-size: 13.5px;
  line-height: 1.85;
  color: var(--app-text-secondary);
}
.intro-list {
  margin: 16px 0 0;
  padding: 0;
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.intro-list li {
  position: relative;
  padding-left: 16px;
  font-size: 12.5px;
  line-height: 1.7;
  color: var(--app-text-tertiary);
}
.intro-list li::before {
  content: '';
  position: absolute;
  left: 2px;
  top: 8px;
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: var(--app-brand);
  opacity: 0.6;
}

.intro-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 14px;
  padding-top: 18px;
}
.intro-dots {
  display: flex;
  gap: 7px;
}
.dot {
  appearance: none;
  width: 7px;
  height: 7px;
  padding: 0;
  border: none;
  border-radius: 50%;
  background: var(--app-hairline-strong, rgba(0, 0, 0, 0.16));
  cursor: pointer;
  transition: width 0.2s var(--app-ease-out, ease-out), background 0.2s var(--app-ease-out, ease-out);
}
.dot.active {
  width: 20px;
  border-radius: 4px;
  background: var(--app-brand);
}
.intro-next {
  appearance: none;
  border: none;
  background: var(--app-brand);
  color: #fff;
  font: inherit;
  font-size: 14px;
  font-weight: 600;
  padding: 12px 26px;
  border-radius: 999px;
  cursor: pointer;
  transition: background 0.16s var(--app-ease-out, ease-out);
}
.intro-next:hover {
  background: var(--app-brand-hover);
}
.intro-next:active {
  transform: scale(0.98);
}
.intro-next:focus-visible {
  outline: 2px solid var(--app-brand);
  outline-offset: 3px;
}

.intro-fade-enter-active,
.intro-fade-leave-active {
  transition: opacity 0.22s var(--app-ease-out, ease-out);
}
.intro-fade-enter-from,
.intro-fade-leave-to {
  opacity: 0;
}

@media (prefers-reduced-motion: reduce) {
  .intro-fade-enter-active,
  .intro-fade-leave-active,
  .dot,
  .intro-next {
    transition: none;
  }
}
</style>
