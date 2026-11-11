<template>
  <!-- 首次出结果后的「这条行程怎么来的」弹窗。
       图里第 3 种「弹窗引导」：干扰强、信息量中等，只在关键时刻用一次。
       和其他引导最大的不同：**内容取自真实数据**（首条地点的 score_breakdown），
       不是画三条示意图——这样"可解释"才是能验证的。 -->
  <teleport to="body">
    <transition name="exp-fade">
      <div v-if="modelValue" class="exp-overlay" @click.self="close">
        <div class="exp-panel" role="dialog" aria-modal="true" aria-labelledby="exp-title">
          <div class="exp-head">
            <h2 id="exp-title" class="exp-title">这条行程是怎么来的</h2>
            <button type="button" class="exp-close" aria-label="关闭" @click="close">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor"
                   stroke-width="1.8" stroke-linecap="round">
                <path d="M6 6l12 12M18 6L6 18" />
              </svg>
            </button>
          </div>

          <div class="exp-body">
            <p class="exp-lead">
              景点、顺序、时间都由算法引擎排定，大模型只负责理解需求与写文案。
              每个地点的分数由三个分量加权得出：
            </p>

            <!-- 真实样例：取行程里第一条带评分分量的地点 -->
            <div v-if="sample" class="exp-sample">
              <div class="exp-sample-head">
                <span class="exp-sample-name">{{ sample.name }}</span>
                <span class="exp-sample-tag">Day 1 · 第 1 站</span>
              </div>
              <div v-for="d in sample.bars" :key="d.key" class="exp-bar-row">
                <span class="exp-bar-label">{{ d.label }}</span>
                <span class="exp-bar-track" role="img" :aria-label="`${d.label} ${d.pct}%`">
                  <span class="exp-bar-fill" :class="`k-${d.key}`" :style="{ width: d.pct + '%' }"></span>
                </span>
                <span class="exp-bar-pct">{{ d.pct }}%</span>
              </div>
              <div class="exp-bar-note">{{ sampleNote }}</div>
            </div>
            <div v-else class="exp-sample exp-sample--empty">
              这一版行程没有返回评分分量（可能是降级数据），所以这里不展示柱状图。
            </div>

            <ul class="exp-facts">
              <li><strong>兴趣</strong>：与你选择的偏好标签的匹配程度</li>
              <li><strong>口碑</strong>：高德真实评分归一化（4.0 → 0，4.9 → 0.9）</li>
              <li><strong>绕路</strong>：相对当天活动中心的偏移，越低越顺路</li>
            </ul>

            <p v-if="hasUnconfirmedCost" class="exp-warn">
              行程里有地点标注了「门票待确认」：我们没拿到可靠票价数据，
              宁可留白也不编一个数字。
            </p>
          </div>

          <div class="exp-foot">
            <button type="button" class="exp-ghost" @click="$emit('details')">看行程详情</button>
            <button type="button" class="exp-primary" @click="close">看懂了</button>
          </div>
        </div>
      </div>
    </transition>
  </teleport>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  modelValue: { type: Boolean, default: false },
  /** ItineraryVO（算法产出） */
  itinerary: { type: Object, default: null },
})
const emit = defineEmits(['update:modelValue', 'details'])

function close() {
  emit('update:modelValue', false)
}

/** 取第一天第一条带 score_breakdown 的地点作为真实样例 */
const sample = computed(() => {
  const days = props.itinerary?.days || []
  for (const d of days) {
    for (const it of d.items || []) {
      const b = it?.score_breakdown
      if (!b) continue
      const pct = (v) => Math.max(0, Math.min(100, Math.round((Number(v) || 0) * 100)))
      return {
        name: it.name,
        bars: [
          { key: 'interest', label: '兴趣', pct: pct(b.interest) },
          { key: 'rating', label: '口碑', pct: pct(b.rating) },
          // 绕路是"成本"：数值越低越顺路，所以展示的是"绕路程度"，不是"绕路得分"
          { key: 'detour', label: '绕路', pct: pct(b.detour) },
        ],
      }
    }
  }
  return null
})

const sampleNote = computed(() =>
  '「绕路」是成本项：占比越低越顺路；前两项越高越匹配。'
)

const hasUnconfirmedCost = computed(() => {
  const days = props.itinerary?.days || []
  for (const d of days) {
    for (const it of d.items || []) {
      if (it?.cost_source === 'reserved') return true
    }
  }
  return false
})
</script>

<style scoped>
.exp-overlay {
  position: fixed;
  inset: 0;
  z-index: 2900;
  display: flex;
  align-items: flex-end;
  justify-content: center;
  background: rgba(20, 28, 24, 0.46);
  padding: 0;
}
@media (min-width: 640px) {
  .exp-overlay {
    align-items: center;
    padding: 24px;
  }
}

.exp-panel {
  width: 100%;
  max-width: 520px;
  max-height: 88vh;
  display: flex;
  flex-direction: column;
  background: var(--app-surface);
  border-radius: 20px 20px 0 0;
  padding-bottom: env(safe-area-inset-bottom);
  box-shadow: 0 -12px 40px rgba(0, 0, 0, 0.18);
}
@media (min-width: 640px) {
  .exp-panel {
    border-radius: var(--app-radius-lg, 16px);
    padding-bottom: 0;
    box-shadow: 0 24px 60px rgba(0, 0, 0, 0.2);
  }
}

.exp-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 18px 20px 10px;
}
.exp-title {
  margin: 0;
  font-family: var(--app-font-serif, 'Songti SC', serif);
  font-size: 19px;
  font-weight: 700;
  color: var(--app-text);
}
.exp-close {
  appearance: none;
  border: none;
  background: transparent;
  width: 32px;
  height: 32px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border-radius: 50%;
  color: var(--app-text-tertiary);
  cursor: pointer;
}
.exp-close:hover {
  background: var(--app-brand-soft, rgba(66, 127, 98, 0.1));
}
.exp-close:focus-visible {
  outline: 2px solid var(--app-brand);
  outline-offset: 2px;
}
.exp-close svg {
  width: 16px;
  height: 16px;
}

.exp-body {
  overflow-y: auto;
  padding: 0 20px 4px;
}
.exp-lead {
  margin: 0 0 14px;
  font-size: 13px;
  line-height: 1.8;
  color: var(--app-text-secondary);
}

.exp-sample {
  border: 1px solid var(--app-hairline);
  border-radius: 12px;
  padding: 13px 14px;
  background: var(--app-bg, #f4f9f5);
}
.exp-sample--empty {
  font-size: 12.5px;
  line-height: 1.7;
  color: var(--app-text-tertiary);
}
.exp-sample-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 10px;
  margin-bottom: 11px;
}
.exp-sample-name {
  font-size: 14px;
  font-weight: 650;
  color: var(--app-text);
}
.exp-sample-tag {
  font-size: 11px;
  color: var(--app-text-tertiary);
}

.exp-bar-row {
  display: flex;
  align-items: center;
  gap: 9px;
  margin-bottom: 9px;
}
.exp-bar-label {
  width: 30px;
  flex-shrink: 0;
  font-size: 12px;
  color: var(--app-text-secondary);
}
.exp-bar-track {
  flex: 1;
  height: 6px;
  border-radius: 3px;
  background: var(--app-hairline);
  overflow: hidden;
}
.exp-bar-fill {
  display: block;
  height: 100%;
  border-radius: 3px;
  background: var(--app-brand);
  transition: width 0.5s var(--app-ease-out, ease-out);
}
.exp-bar-fill.k-rating {
  background: var(--app-brand-hover, #35664e);
}
.exp-bar-fill.k-detour {
  background: var(--app-warning, #a86a20);
}
.exp-bar-pct {
  width: 36px;
  text-align: right;
  font-size: 11.5px;
  color: var(--app-text-tertiary);
  font-variant-numeric: tabular-nums;
}
.exp-bar-note {
  margin-top: 4px;
  font-size: 11.5px;
  line-height: 1.6;
  color: var(--app-text-tertiary);
}

.exp-facts {
  margin: 16px 0 0;
  padding: 0;
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 7px;
}
.exp-facts li {
  font-size: 12.5px;
  line-height: 1.7;
  color: var(--app-text-secondary);
}
.exp-facts strong {
  color: var(--app-text);
  font-weight: 650;
}

.exp-warn {
  margin: 14px 0 0;
  padding: 10px 12px;
  border-radius: 10px;
  background: var(--app-warning-soft, rgba(168, 106, 32, 0.12));
  font-size: 12.5px;
  line-height: 1.7;
  color: var(--app-warning, #8a5715);
}

.exp-foot {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
  padding: 14px 20px 18px;
  border-top: 1px solid var(--app-hairline);
  margin-top: 12px;
}
.exp-ghost {
  appearance: none;
  border: 1px solid var(--app-hairline-strong, rgba(0, 0, 0, 0.12));
  background: transparent;
  color: var(--app-text);
  font: inherit;
  font-size: 13px;
  padding: 10px 18px;
  border-radius: 999px;
  cursor: pointer;
}
.exp-primary {
  appearance: none;
  border: none;
  background: var(--app-brand);
  color: #fff;
  font: inherit;
  font-size: 13px;
  font-weight: 600;
  padding: 10px 22px;
  border-radius: 999px;
  cursor: pointer;
}
.exp-primary:hover {
  background: var(--app-brand-hover);
}
.exp-ghost:hover {
  background: var(--app-brand-soft, rgba(66, 127, 98, 0.08));
}
.exp-ghost:focus-visible,
.exp-primary:focus-visible {
  outline: 2px solid var(--app-brand);
  outline-offset: 2px;
}

.exp-fade-enter-active,
.exp-fade-leave-active {
  transition: opacity 0.2s var(--app-ease-out, ease-out);
}
.exp-fade-enter-active .exp-panel,
.exp-fade-leave-active .exp-panel {
  transition: transform 0.24s var(--app-ease-out, ease-out);
}
.exp-fade-enter-from,
.exp-fade-leave-to {
  opacity: 0;
}
.exp-fade-enter-from .exp-panel,
.exp-fade-leave-to .exp-panel {
  transform: translateY(16px);
}

@media (prefers-reduced-motion: reduce) {
  .exp-fade-enter-active,
  .exp-fade-leave-active,
  .exp-fade-enter-active .exp-panel,
  .exp-fade-leave-active .exp-panel,
  .exp-bar-fill {
    transition: none;
  }
}
</style>
