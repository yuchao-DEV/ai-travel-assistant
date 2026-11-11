<template>
  <!-- 使用说明（帮助中心）。
       双端共用一个实现：移动端是底部弹层，宽屏是居中卡片。
       纯 HTML + 令牌，不依赖 Vant / Element，保证两端口径与样式一致。 -->
  <teleport to="body">
    <transition name="help-fade">
      <div
        v-if="modelValue"
        class="help-overlay"
        @click.self="close"
        @keydown.esc="close"
      >
        <div
          ref="panelEl"
          class="help-panel"
          role="dialog"
          aria-modal="true"
          aria-labelledby="help-title"
        >
          <div class="help-head">
            <h2 id="help-title" class="help-title">怎么用</h2>
            <button
              ref="closeEl"
              type="button"
              class="help-close"
              aria-label="关闭使用说明"
              @click="close"
            >
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor"
                   stroke-width="1.8" stroke-linecap="round">
                <path d="M6 6l12 12M18 6L6 18" />
              </svg>
            </button>
          </div>

          <div class="help-body">
            <section v-for="item in items" :key="item.k" class="help-item">
              <div class="help-item-title">{{ item.k }}</div>
              <p class="help-item-text">{{ item.v }}</p>
            </section>
          </div>

          <div class="help-foot">
            <button type="button" class="help-ghost" @click="$emit('restart-guide')">
              重新看首页引导
            </button>
            <span class="help-version">按 Esc 或点空白处关闭</span>
          </div>
        </div>
      </div>
    </transition>
  </teleport>
</template>

<script setup>
import { nextTick, onBeforeUnmount, ref, watch } from 'vue'

const props = defineProps({
  modelValue: { type: Boolean, default: false },
})
const emit = defineEmits(['update:modelValue', 'restart-guide'])

const panelEl = ref(null)
const closeEl = ref(null)
let lastFocused = null

/**
 * 四条内容必须**都是系统真能做到的**。
 * 这里写过的每一句都能在代码里找到对应实现，不写"即将支持"的承诺。
 */
const items = [
  {
    k: '我能做什么',
    v: '一句话说出城市、天数和偏好，直接生成可执行的行程。景点、顺序、时间由算法引擎排定，大模型只负责理解需求与写文案——所以给出的行程是可解释的，不是编的。',
  },
  {
    k: '为什么要点时间',
    v: '一次规划约 20–40 秒：要真实查询景点与营业时间、算清两两之间的距离与车程、再按时间容量排出顺序。同一座城市第二次规划会命中缓存，明显更快。',
  },
  {
    k: '数据从哪来',
    v: '景点与坐标来自高德地图；预约规则、闭馆日、票价口径来自本地知识库。没有可靠票价的会标「门票待确认」，不显示数字——宁可留白也不编。',
  },
  {
    k: '不满意怎么改',
    v: '直接接着说就行：「太满了」「换成自然向」「把博物院挪到第一天」「带老人别排太满」。这些会被解析成参数调整，再重新排一版。',
  },
  {
    k: '不登录能用吗',
    v: '能。行程规划不需要登录，只是历史记录只存在这台设备上；登录后会同步到账号，换设备也能回看。',
  },
]

function close() {
  emit('update:modelValue', false)
}

function onKeydown(e) {
  if (e.key === 'Escape') close()
}

watch(
  () => props.modelValue,
  async (open) => {
    if (open) {
      lastFocused = document.activeElement
      // 打开时锁住背景滚动，避免移动端"两层都能滚"
      document.body.style.overflow = 'hidden'
      document.addEventListener('keydown', onKeydown)
      await nextTick()
      closeEl.value?.focus()
    } else {
      document.body.style.overflow = ''
      document.removeEventListener('keydown', onKeydown)
      // 关闭后焦点回到触发按钮，键盘用户不会丢失位置
      if (lastFocused && typeof lastFocused.focus === 'function') {
        lastFocused.focus()
      }
      lastFocused = null
    }
  },
)

onBeforeUnmount(() => {
  document.body.style.overflow = ''
  document.removeEventListener('keydown', onKeydown)
})
</script>

<style scoped>
.help-overlay {
  position: fixed;
  inset: 0;
  z-index: 3000;
  display: flex;
  align-items: flex-end;
  justify-content: center;
  background: rgba(20, 28, 24, 0.44);
  backdrop-filter: blur(2px);
  padding: 0;
}
@media (min-width: 640px) {
  .help-overlay {
    align-items: center;
    padding: 24px;
  }
}

.help-panel {
  width: 100%;
  max-width: 560px;
  max-height: 86vh;
  display: flex;
  flex-direction: column;
  background: var(--app-surface);
  border-radius: 20px 20px 0 0;
  box-shadow: var(--app-elev-3, 0 24px 60px rgba(0, 0, 0, 0.2));
  padding-bottom: env(safe-area-inset-bottom);
}
@media (min-width: 640px) {
  .help-panel {
    border-radius: var(--app-radius-lg, 16px);
    padding-bottom: 0;
  }
}

.help-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 18px 20px 12px;
  border-bottom: 1px solid var(--app-hairline);
}
.help-title {
  margin: 0;
  font-family: var(--app-font-serif, 'Songti SC', serif);
  font-size: 19px;
  font-weight: 700;
  color: var(--app-text);
  letter-spacing: 0.02em;
}
.help-close {
  appearance: none;
  border: none;
  background: transparent;
  width: 34px;
  height: 34px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border-radius: 50%;
  color: var(--app-text-tertiary);
  cursor: pointer;
}
.help-close:hover {
  background: var(--app-brand-soft, rgba(66, 127, 98, 0.1));
  color: var(--app-brand-hover);
}
.help-close:focus-visible {
  outline: 2px solid var(--app-brand);
  outline-offset: 2px;
}
.help-close svg {
  width: 17px;
  height: 17px;
}

.help-body {
  overflow-y: auto;
  padding: 6px 20px 8px;
  -webkit-overflow-scrolling: touch;
}
.help-item {
  padding: 13px 0;
  border-bottom: 1px dashed var(--app-hairline);
}
.help-item:last-child {
  border-bottom: none;
}
.help-item-title {
  font-size: 13.5px;
  font-weight: 650;
  color: var(--app-brand-hover);
  margin-bottom: 5px;
}
.help-item-text {
  margin: 0;
  font-size: 13px;
  line-height: 1.78;
  color: var(--app-text-secondary);
}

.help-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 12px 20px 16px;
  border-top: 1px solid var(--app-hairline);
}
.help-ghost {
  appearance: none;
  border: none;
  background: transparent;
  padding: 6px 2px;
  font: inherit;
  font-size: 12.5px;
  color: var(--app-brand);
  cursor: pointer;
  text-decoration: underline;
  text-underline-offset: 3px;
}
.help-ghost:hover {
  color: var(--app-brand-hover);
}
.help-version {
  font-size: 11.5px;
  color: var(--app-text-tertiary);
}

.help-fade-enter-active,
.help-fade-leave-active {
  transition: opacity 0.2s var(--app-ease-out, ease-out);
}
.help-fade-enter-active .help-panel,
.help-fade-leave-active .help-panel {
  transition: transform 0.24s var(--app-ease-out, ease-out);
}
.help-fade-enter-from,
.help-fade-leave-to {
  opacity: 0;
}
.help-fade-enter-from .help-panel,
.help-fade-leave-to .help-panel {
  transform: translateY(14px);
}

@media (prefers-reduced-motion: reduce) {
  .help-fade-enter-active,
  .help-fade-leave-active,
  .help-fade-enter-active .help-panel,
  .help-fade-leave-active .help-panel {
    transition: none;
  }
}
</style>
