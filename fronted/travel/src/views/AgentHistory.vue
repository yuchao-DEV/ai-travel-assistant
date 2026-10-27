<template>
  <div class="agent-history">
    <van-nav-bar title="AI 会话记录" left-arrow @click-left="goBack" />

    <div v-if="loading" class="ah-status">加载中…</div>
    <div v-else-if="error" class="ah-status error">{{ error }}</div>
    <div v-else-if="!sessions.length" class="ah-empty">
      <div class="ah-empty-title">还没有会话记录</div>
      <div class="ah-empty-hint">和 AI 助手聊过的对话会保存在这里（需要登录）</div>
      <van-button type="primary" size="small" round @click="goChat">去聊聊</van-button>
    </div>

    <template v-else>
      <van-cell-group v-for="s in sessions" :key="s.id" :border="false" class="ah-group">
        <van-cell :title="s.title || '未命名会话'" :label="label(s)" is-link @click="open(s)" />
      </van-cell-group>
    </template>

    <!-- 会话详情：完整时间线回放 -->
    <van-popup v-model:show="showDetail" position="bottom" round
               :style="{ height: '86%' }" @closed="detail = null">
      <div class="ah-detail">
        <div class="ah-detail-head">
          <span class="ah-detail-title">{{ detail?.title || '会话详情' }}</span>
          <van-icon name="cross" @click="showDetail = false" />
        </div>
        <div class="ah-detail-body">
          <div v-if="detailLoading" class="ah-status">加载中…</div>
          <template v-else-if="detail">
            <div v-for="(m, i) in detail.messages" :key="'m' + i" class="ah-msg">
              <span class="ah-role" :class="m.role">{{ m.role === 'user' ? '你' : '助手' }}</span>
              <span class="ah-content">{{ m.content }}</span>
            </div>

            <div class="ah-section-title">工具调用时间线（{{ (detail.tool_calls || []).length }} 次）</div>
            <div v-if="!(detail.tool_calls || []).length" class="ah-status">本次会话没有工具调用</div>
            <div v-for="(c, i) in detail.tool_calls" :key="'t' + i" class="ah-call">
              <div class="ah-call-head">
                <span class="ah-call-round">第 {{ c.round }} 轮</span>
                <span class="ah-call-name">{{ c.tool_name }}</span>
                <span class="ah-call-ok" :class="{ bad: !c.result_ok }">
                  {{ c.result_ok ? '成功' : (c.error || c.code || '失败') }}
                </span>
                <span class="ah-call-ms">{{ c.elapsed_ms }}ms</span>
                <span v-if="c.is_repeat" class="ah-call-repeat">重复调用</span>
              </div>
              <pre class="ah-call-args">{{ c.arguments }}</pre>
            </div>
          </template>
        </div>
        <div class="ah-detail-foot">
          <van-button type="primary" size="small" round block @click="resume">
            继续对话
          </van-button>
          <van-button type="danger" plain size="small" round block @click="remove">
            删除这条会话
          </van-button>
        </div>
      </div>
    </van-popup>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { showConfirmDialog, showToast } from 'vant'
import { deleteSession, getSession, getSessions } from '@/api/agent'

const router = useRouter()
const sessions = ref([])
const loading = ref(true)
const error = ref('')
const showDetail = ref(false)
const detailLoading = ref(false)
const detail = ref(null)

function label(s) {
  const when = (s.updated_at || '').replace('T', ' ').slice(0, 16)
  return `${when} · ${s.message_count || 0} 条消息`
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    const res = await getSessions()
    sessions.value = res?.data?.items || []
  } catch (err) {
    // 未登录时后端会 401；如实提示而不是假装"没有记录"
    error.value = err?.message || '加载失败，请稍后重试'
  } finally {
    loading.value = false
  }
}

async function open(s) {
  showDetail.value = true
  detailLoading.value = true
  detail.value = null
  try {
    const res = await getSession(s.id)
    detail.value = res?.data || null
  } catch (err) {
    showToast(err?.message || '加载会话失败')
    showDetail.value = false
  } finally {
    detailLoading.value = false
  }
}

/** 带着这个会话回到 AI 助手接着聊 */
function resume() {
  const id = detail.value?.session_id
  if (!id) return
  showDetail.value = false
  router.push({ path: '/agent', query: { session_id: id } })
}

async function remove() {
  if (!detail.value) return
  try {
    await showConfirmDialog({ title: '确认删除', message: '删除后不可恢复' })
  } catch {
    return
  }
  try {
    await deleteSession(detail.value.session_id)
    showDetail.value = false
    await load()
  } catch (err) {
    showToast(err?.message || '删除失败')
  }
}

function goBack() {
  router.back()
}
function goChat() {
  router.push('/agent')
}

onMounted(load)
</script>

<style scoped>
.agent-history { min-height: 100vh; background: var(--app-bg-subtle); padding-bottom: 20px; }
.ah-group { margin-bottom: 8px; }
.ah-status { padding: 24px; text-align: center; color: var(--app-text-tertiary); font-size: 13px; }
.ah-status.error { color: var(--app-danger); }
.ah-empty { padding: 70px 24px; text-align: center; }
.ah-empty-title { font-size: 15px; font-weight: 600; color: var(--app-text); }
.ah-empty-hint { margin: 8px 0 16px; font-size: 12.5px; color: var(--app-text-tertiary); line-height: 1.8; }

.ah-detail { display: flex; flex-direction: column; height: 100%; }
.ah-detail-head {
  display: flex; align-items: center; justify-content: space-between;
  padding: 14px 16px; border-bottom: 1px solid var(--app-border);
}
.ah-detail-title { font-size: 15px; font-weight: 600; color: var(--app-text); }
.ah-detail-body { flex: 1; overflow-y: auto; padding: 12px 16px; }
.ah-detail-foot { padding: 10px 16px calc(10px + env(safe-area-inset-bottom)); border-top: 1px solid var(--app-border); display: flex; flex-direction: column; gap: 8px; }

.ah-msg { margin-bottom: 10px; font-size: 13px; line-height: 1.7; }
.ah-role { display: inline-block; margin-right: 6px; font-size: 11px; padding: 1px 6px; border-radius: 8px; }
.ah-role.user { background: var(--app-brand-soft); color: var(--app-brand); }
.ah-role.assistant { background: var(--app-surface-muted); color: var(--app-success); }
.ah-content { color: var(--app-text); white-space: pre-wrap; }

.ah-section-title { margin: 16px 0 8px; font-size: 12.5px; font-weight: 600; color: var(--app-text); }
.ah-call { background: var(--app-surface); border: 1px solid var(--app-border); border-radius: 8px; padding: 8px 10px; margin-bottom: 8px; }
.ah-call-head { display: flex; align-items: center; gap: 8px; font-size: 12px; flex-wrap: wrap; }
.ah-call-round { color: var(--app-text-tertiary); }
.ah-call-name { font-weight: 600; color: var(--app-text); }
.ah-call-ok { color: var(--app-success); }
.ah-call-ok.bad { color: var(--app-danger); }
.ah-call-ms { color: var(--app-text-tertiary); }
.ah-call-repeat { color: var(--app-warning); }
.ah-call-args {
  margin: 6px 0 0; padding: 6px 8px; background: var(--app-bg-subtle); border-radius: 6px;
  font-size: 11px; color: var(--app-text-secondary); white-space: pre-wrap; word-break: break-all;
  max-height: 120px; overflow: auto;
}
</style>
