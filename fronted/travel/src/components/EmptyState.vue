<template>
  <!-- 双端共用的空状态。
       刻意用纯 HTML + 设计令牌：Vant 与 Element 的空态组件样式差异大，
       各写一套会走形；而"没有数据时说什么"这句话必须是同一套口径。 -->
  <div class="empty-state" :class="[`is-${size}`, `tone-${tone}`]" role="status">
    <div class="es-icon" aria-hidden="true">
      <slot name="icon">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor"
             stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round">
          <rect x="3.5" y="5" width="17" height="14" rx="3" />
          <path d="M8 15.5l3-3 2.2 2.2 2.3-2.7 2.5 3.5" />
          <circle cx="9" cy="9.6" r="1.1" />
        </svg>
      </slot>
    </div>

    <p class="es-title">{{ title }}</p>
    <p v-if="desc" class="es-desc">{{ desc }}</p>

    <div v-if="actionLabel || $slots.actions" class="es-actions">
      <slot name="actions">
        <button type="button" class="es-btn is-primary" @click="$emit('action')">
          {{ actionLabel }}
        </button>
        <button
          v-if="secondaryLabel"
          type="button"
          class="es-btn"
          @click="$emit('secondary')"
        >
          {{ secondaryLabel }}
        </button>
      </slot>
    </div>
  </div>
</template>

<script setup>
defineProps({
  title: { type: String, required: true },
  desc: { type: String, default: '' },
  /** primary 按钮文案；为空则不显示按钮 */
  actionLabel: { type: String, default: '' },
  /** 次要按钮文案（如「看使用说明」） */
  secondaryLabel: { type: String, default: '' },
  /** compact 用在卡片内，default 用在整页 */
  size: { type: String, default: 'default' },
  /** error 用暖红提示，warning 用琥珀，neutral 用常规灰绿 */
  tone: { type: String, default: 'neutral' },
})
defineEmits(['action', 'secondary'])
</script>

<style scoped>
.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  text-align: center;
  gap: 10px;
  padding: 40px 20px;
}
.empty-state.is-compact {
  padding: 22px 12px;
  gap: 8px;
}

.es-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 52px;
  height: 52px;
  border-radius: 16px;
  color: var(--app-brand-hover);
  background: var(--app-brand-soft, rgba(66, 127, 98, 0.12));
}
.is-compact .es-icon {
  width: 40px;
  height: 40px;
  border-radius: 12px;
}
.es-icon :deep(svg) {
  width: 24px;
  height: 24px;
}
.is-compact .es-icon :deep(svg) {
  width: 19px;
  height: 19px;
}

.tone-error .es-icon {
  color: var(--app-danger, #b8523f);
  background: var(--app-danger-soft, rgba(184, 82, 63, 0.12));
}
.tone-warning .es-icon {
  color: var(--app-warning, #a86a20);
  background: var(--app-warning-soft, rgba(168, 106, 32, 0.14));
}

.es-title {
  margin: 0;
  font-size: 15px;
  font-weight: 650;
  color: var(--app-text);
  letter-spacing: 0.01em;
}
.is-compact .es-title {
  font-size: 13.5px;
}
.es-desc {
  margin: 0;
  max-width: 34em;
  font-size: 13px;
  line-height: 1.75;
  color: var(--app-text-secondary);
}
.is-compact .es-desc {
  font-size: 12.5px;
}

.es-actions {
  display: flex;
  flex-wrap: wrap;
  justify-content: center;
  gap: 10px;
  margin-top: 6px;
}

.es-btn {
  appearance: none;
  border: 1px solid var(--app-hairline, rgba(0, 0, 0, 0.08));
  background: var(--app-surface);
  color: var(--app-text);
  font: inherit;
  font-size: 13px;
  padding: 9px 18px;
  border-radius: 999px;
  cursor: pointer;
  transition: transform 0.16s var(--app-ease-out, ease-out),
              box-shadow 0.16s var(--app-ease-out, ease-out),
              background 0.16s var(--app-ease-out, ease-out);
}
.es-btn:hover {
  box-shadow: var(--app-elev-2);
}
.es-btn:active {
  transform: scale(0.97);
}
.es-btn:focus-visible {
  outline: 2px solid var(--app-brand);
  outline-offset: 2px;
}
.es-btn.is-primary {
  border-color: transparent;
  background: var(--app-brand);
  color: #fff;
  font-weight: 600;
}
.es-btn.is-primary:hover {
  background: var(--app-brand-hover);
}

@media (prefers-reduced-motion: reduce) {
  .es-btn {
    transition: none;
  }
  .es-btn:active {
    transform: none;
  }
}
</style>
