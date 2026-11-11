<template>
  <!-- 首次进入的说明卡（移动端用它代替浮层引导）。
       为什么不用高亮浮层：390px 宽 + 底部 50px tabbar，气泡很容易把表单压掉一半，
       而"新手引导"是最容易做成花架子的一环。一条可关闭的说明卡同样讲清了路径。 -->
  <section v-if="visible" class="guide-notice" role="region" aria-label="使用步骤">
    <div class="gn-head">
      <span class="gn-kicker">三步出行程</span>
      <button type="button" class="gn-close" aria-label="关闭使用引导" @click="dismiss">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor"
             stroke-width="1.8" stroke-linecap="round">
          <path d="M6 6l12 12M18 6L6 18" />
        </svg>
      </button>
    </div>

    <ol class="gn-steps">
      <li v-for="(s, i) in steps" :key="s.k" class="gn-step">
        <span class="gn-idx" aria-hidden="true">{{ i + 1 }}</span>
        <span class="gn-text">
          <span class="gn-title">{{ s.k }}</span>
          <span class="gn-desc">{{ s.v }}</span>
        </span>
      </li>
    </ol>

    <button type="button" class="gn-ok" @click="dismiss">知道了，开始规划</button>
  </section>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { GUIDE_IDS, hasSeenGuide, markGuideSeen } from '@/utils/guide'

const visible = ref(false)

const steps = [
  { k: '选目的地', v: '点「想去哪里」，也可以挑下面一张热门卡片' },
  { k: '定天数和偏好', v: '天数决定排几个点；偏好可以都留空，按默认规则走' },
  { k: '生成行程', v: '20–40 秒，会真实查景点、算距离、排营业时间' },
]

onMounted(() => {
  // 只在这台设备第一次进入时出现；点关闭即写入标记，之后不再打扰
  visible.value = !hasSeenGuide(GUIDE_IDS.homeNotice)
})

function dismiss() {
  visible.value = false
  markGuideSeen(GUIDE_IDS.homeNotice)
}

/** 供父组件「重新看引导」调用 */
function show() {
  visible.value = true
}

defineExpose({ show })
</script>

<style scoped>
.guide-notice {
  margin-bottom: 14px;
  padding: 14px 14px 12px;
  border-radius: var(--app-radius-lg, 14px);
  background: linear-gradient(
    180deg,
    var(--app-brand-soft, rgba(66, 127, 98, 0.1)),
    var(--app-surface) 72%
  );
  border: 1px solid var(--app-hairline);
  box-shadow: var(--app-elev-1);
}

.gn-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 10px;
}
.gn-kicker {
  font-size: 11px;
  letter-spacing: 0.14em;
  font-weight: 700;
  color: var(--app-brand-hover);
}
.gn-close {
  appearance: none;
  border: none;
  background: transparent;
  width: 26px;
  height: 26px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border-radius: 50%;
  color: var(--app-text-tertiary);
  cursor: pointer;
}
.gn-close:hover {
  background: rgba(0, 0, 0, 0.05);
}
.gn-close:focus-visible {
  outline: 2px solid var(--app-brand);
  outline-offset: 2px;
}
.gn-close svg {
  width: 13px;
  height: 13px;
}

.gn-steps {
  list-style: none;
  margin: 0 0 12px;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 9px;
}
.gn-step {
  display: flex;
  align-items: flex-start;
  gap: 10px;
}
.gn-idx {
  flex-shrink: 0;
  width: 19px;
  height: 19px;
  margin-top: 1px;
  border-radius: 50%;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-size: 11px;
  font-weight: 700;
  color: #fff;
  background: var(--app-brand);
}
.gn-text {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}
.gn-title {
  font-size: 13px;
  font-weight: 650;
  color: var(--app-text);
}
.gn-desc {
  font-size: 12px;
  line-height: 1.6;
  color: var(--app-text-secondary);
}

.gn-ok {
  width: 100%;
  appearance: none;
  border: 1px solid var(--app-brand);
  background: transparent;
  color: var(--app-brand-hover);
  font: inherit;
  font-size: 13px;
  font-weight: 600;
  padding: 9px 0;
  border-radius: 10px;
  cursor: pointer;
  transition: background 0.16s var(--app-ease-out, ease-out);
}
.gn-ok:hover {
  background: var(--app-brand-soft, rgba(66, 127, 98, 0.1));
}
.gn-ok:active {
  transform: scale(0.99);
}
.gn-ok:focus-visible {
  outline: 2px solid var(--app-brand);
  outline-offset: 2px;
}

@media (prefers-reduced-motion: reduce) {
  .gn-ok {
    transition: none;
  }
  .gn-ok:active {
    transform: none;
  }
}
</style>
