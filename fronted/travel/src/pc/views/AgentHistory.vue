<template>
  <div class="pc-agent-history">
    <el-card shadow="never">
      <template #header>
        <div class="pah-header">
          <span class="pah-title">AI 会话记录</span>
          <el-button text type="primary" @click="load">刷新</el-button>
        </div>
      </template>

      <el-alert v-if="error" :title="error" type="error" show-icon :closable="false" />

      <el-table v-else :data="sessions" v-loading="loading" stripe
                empty-text="还没有会话记录（和 AI 助手聊过的对话会保存在这里，需要登录）">
        <el-table-column prop="title" label="会话" min-width="220" />
        <el-table-column label="消息数" width="90">
          <template #default="{ row }">{{ row.message_count || 0 }}</template>
        </el-table-column>
        <el-table-column label="更新时间" width="170">
          <template #default="{ row }">
            {{ (row.updated_at || '').replace('T', ' ').slice(0, 16) }}
          </template>
        </el-table-column>
        <el-table-column label="操作" width="240" fixed="right">
          <template #default="{ row }">
            <el-button text type="primary" size="small" @click="open(row)">查看</el-button>
            <el-button text type="primary" size="small" @click="resume(row)">继续对话</el-button>
            <el-popconfirm title="删除后不可恢复，确认删除？" @confirm="remove(row)">
              <template #reference>
                <el-button text type="danger" size="small">删除</el-button>
              </template>
            </el-popconfirm>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <!-- 详情抽屉：完整消息 + 工具调用时间线 -->
    <el-drawer v-model="showDetail" :title="detail?.title || '会话详情'" size="52%">
      <div v-if="detailLoading" class="pah-status">加载中…</div>
      <template v-else-if="detail">
        <div class="pah-section">对话</div>
        <div v-for="(m, i) in detail.messages" :key="'m' + i" class="pah-msg">
          <span class="pah-role" :class="m.role">{{ m.role === 'user' ? '你' : '助手' }}</span>
          <span class="pah-content">{{ m.content }}</span>
        </div>

        <div class="pah-section">
          工具调用时间线（{{ (detail.tool_calls || []).length }} 次）
        </div>
        <div v-if="!(detail.tool_calls || []).length" class="pah-status">
          本次会话没有工具调用
        </div>
        <div v-for="(c, i) in detail.tool_calls" :key="'t' + i" class="pah-call">
          <div class="pah-call-head">
            <el-tag size="small" type="info">第 {{ c.round }} 轮</el-tag>
            <span class="pah-call-name">{{ c.tool_name }}</span>
            <el-tag size="small" :type="c.result_ok ? 'success' : 'danger'">
              {{ c.result_ok ? '成功' : (c.error || c.code || '失败') }}
            </el-tag>
            <span class="pah-call-ms">{{ c.elapsed_ms }}ms</span>
            <el-tag v-if="c.is_repeat" size="small" type="warning">重复调用</el-tag>
          </div>
          <pre class="pah-call-args">{{ c.arguments }}</pre>
        </div>
      </template>
      <div class="pah-resume">
        <el-button type="primary" round @click="resume(detail)">继续对话</el-button>
        <span class="pah-resume-tip">带着这段上下文回到 AI 助手接着问</span>
      </div>
    </el-drawer>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { deleteSession, getSession, getSessions } from '@/api/agent'

const router = useRouter()

const sessions = ref([])
const loading = ref(true)
const error = ref('')
const showDetail = ref(false)
const detailLoading = ref(false)
const detail = ref(null)

async function load() {
  loading.value = true
  error.value = ''
  try {
    const res = await getSessions()
    sessions.value = res?.data?.items || []
  } catch (err) {
    error.value = err?.message || '加载失败，请稍后重试'
  } finally {
    loading.value = false
  }
}

async function open(row) {
  showDetail.value = true
  detailLoading.value = true
  detail.value = null
  try {
    const res = await getSession(row.id)
    detail.value = res?.data || null
  } catch (err) {
    ElMessage.error(err?.message || '加载会话失败')
    showDetail.value = false
  } finally {
    detailLoading.value = false
  }
}

/** 带着这个会话回到 AI 助手接着聊（AgentChat 会按 session_id 恢复上下文） */
function resume(row) {
  const id = row?.id || row?.session_id
  if (!id) return
  router.push({ path: '/agent', query: { session_id: id } })
}

async function remove(row) {
  try {
    await deleteSession(row.id)
    ElMessage.success('已删除')
    await load()
  } catch (err) {
    ElMessage.error(err?.message || '删除失败')
  }
}

onMounted(load)
</script>

<style scoped>
.pc-agent-history :deep(.el-card) { border-radius: 12px; }
.pah-header { display: flex; align-items: center; justify-content: space-between; }
.pah-title { font-size: 15px; font-weight: 600; color: var(--app-text); }
.pah-status { padding: 20px; text-align: center; color: var(--app-text-tertiary); font-size: 13px; }
.pah-section { margin: 16px 0 8px; font-size: 13px; font-weight: 600; color: var(--app-text); }
.pah-msg { margin-bottom: 10px; font-size: 13px; line-height: 1.7; }
.pah-role { display: inline-block; margin-right: 6px; font-size: 11px; padding: 1px 6px; border-radius: 8px; }
.pah-role.user { background: var(--app-brand-soft); color: var(--app-brand); }
.pah-role.assistant { background: var(--app-surface-muted); color: var(--app-success); }
.pah-content { color: var(--app-text); white-space: pre-wrap; }
.pah-call { border: 1px solid var(--app-border); border-radius: 8px; padding: 8px 10px; margin-bottom: 8px; }
.pah-call-head { display: flex; align-items: center; gap: 8px; font-size: 12px; flex-wrap: wrap; }
.pah-call-name { font-weight: 600; color: var(--app-text); }
.pah-call-ms { color: var(--app-text-tertiary); }
.pah-call-args {
  margin: 6px 0 0; padding: 6px 8px; background: var(--app-bg-subtle); border-radius: 6px;
  font-size: 11px; color: var(--app-text-secondary); white-space: pre-wrap; word-break: break-all;
  max-height: 140px; overflow: auto;
}
.pah-resume {
  display: flex;
  align-items: center;
  gap: 12px;
  padding-top: 16px;
  border-top: 1px solid var(--app-hairline);
}
.pah-resume-tip {
  font-size: 12.5px;
  color: var(--app-text-tertiary);
}
</style>
