<template>
  <!-- 图片导入卡：双端共用同一份实现。
       刻意用**纯 HTML + 设计令牌**而不是 Vant/Element 组件：
       两端组件库不同（Vant 移动端 / Element Plus 桌面端），
       写一份纯样式组件才能真正做到"同一处改动双端生效"。
       之前「一句话规划」的图片入口只改了一端就是这个问题。 -->
  <section class="gcard img-import">
    <div class="ii-row">
      <span class="ii-icon" aria-hidden="true">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor"
             stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">
          <rect x="3" y="4" width="18" height="16" rx="3" />
          <circle cx="8.5" cy="9.5" r="1.6" />
          <path d="M21 16l-4.5-4.5L9 19" />
        </svg>
      </span>
      <div class="ii-body">
        <div class="ii-title">上传攻略图</div>
        <div class="ii-desc">从攻略截图里提取地点，逐个核对是否真实存在</div>
      </div>
      <button
        type="button"
        class="ii-btn"
        :disabled="loading"
        :aria-busy="loading ? 'true' : 'false'"
        @click="pick"
      >
        {{ loading ? '识别中…' : '选择图片' }}
      </button>
    </div>
    <input
      ref="fileEl"
      type="file"
      accept="image/jpeg,image/png,image/webp"
      class="ii-file"
      @change="onChange"
    />
    <div class="ii-meta">支持 JPG / PNG / WebP，单张不超过 {{ maxMb }}MB</div>
  </section>
</template>

<script setup>
import { ref } from 'vue'

const props = defineProps({
  /** 处理中（父组件持有请求状态） */
  loading: { type: Boolean, default: false },
  maxMb: { type: Number, default: 8 },
})
const emit = defineEmits(['select', 'error'])

const fileEl = ref(null)
const MAX_BYTES = props.maxMb * 1024 * 1024

function pick() {
  const el = fileEl.value
  if (!el) return
  el.value = ''          // 允许重复选同一张图
  el.click()
}

function onChange(e) {
  const file = (e.target.files || [])[0]
  if (!file) return
  if (!/^image\//.test(file.type)) {
    emit('error', '请选择图片文件（JPG / PNG / WebP）')
    return
  }
  if (file.size > MAX_BYTES) {
    emit('error', `图片超过 ${props.maxMb}MB，请压缩后重试`)
    return
  }
  emit('select', file)
}

defineExpose({ reset: () => { if (fileEl.value) fileEl.value.value = '' } })
</script>

<style scoped>
.img-import {
  padding: 16px 16px 12px;
  border-radius: var(--app-radius-lg);
  background: var(--app-surface);
  border: 1px solid var(--app-hairline);
  box-shadow: var(--app-elev-1);
}
.ii-row {
  display: flex;
  align-items: center;
  gap: 12px;
}
.ii-icon {
  flex-shrink: 0;
  width: 38px;
  height: 38px;
  border-radius: var(--app-radius-md);
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--app-brand-soft);
  color: var(--app-brand);
}
.ii-icon svg { width: 20px; height: 20px; }
.ii-body { flex: 1; min-width: 0; }
.ii-title {
  font-size: 14.5px;
  font-weight: 600;
  color: var(--app-text);
  letter-spacing: 0.01em;
}
.ii-desc {
  margin-top: 3px;
  font-size: 12px;
  line-height: 1.55;
  color: var(--app-text-tertiary);
}
.ii-btn {
  flex-shrink: 0;
  padding: 8px 16px;
  border-radius: var(--app-radius-pill);
  border: 1px solid var(--app-brand);
  background: transparent;
  color: var(--app-brand);
  font-size: 13px;
  font-weight: 500;
  font-family: inherit;
  cursor: pointer;
  transition: background var(--app-dur-base) var(--app-ease-out),
              transform var(--app-dur-fast) var(--app-ease-out);
  min-height: 36px;
}
.ii-btn:hover:not(:disabled) { background: var(--app-brand-soft); }
.ii-btn:active:not(:disabled) { transform: scale(var(--app-press-scale)); }
.ii-btn:disabled { opacity: 0.55; cursor: default; }

.ii-file { display: none; }

.ii-meta {
  margin-top: 10px;
  padding-top: 10px;
  border-top: 1px solid var(--app-hairline);
  font-size: 11px;
  color: var(--app-text-tertiary);
  letter-spacing: 0.01em;
}
</style>
