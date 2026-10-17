<script setup>
/**
 * 桌面端「我的行程」：分页表格 + 查看 + 删除。
 * 复用移动端同一套 /api/history/* 接口与鉴权。
 */
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Delete, Refresh, View } from '@element-plus/icons-vue'

import { deleteHistory, getHistoryList } from '@/api/history'
import { useAuth } from '@/composables/useAuth'

const router = useRouter()
const { isLoggedIn, ensureInitialized } = useAuth()

const rows = ref([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(10)
const loading = ref(false)

function formatDate(iso) {
  if (!iso) return ''
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return iso
  const pad = (n) => String(n).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`
}

async function load() {
  if (!isLoggedIn.value) return
  loading.value = true
  try {
    const res = await getHistoryList(page.value, pageSize.value)
    const data = res?.data || {}
    rows.value = (data.items || []).map((item) => ({
      ...item,
      dateText: formatDate(item.created_at),
    }))
    total.value = data.total || 0
  } catch (e) {
    ElMessage.error(e.message || '加载失败')
  } finally {
    loading.value = false
  }
}

function onPageChange(p) {
  page.value = p
  load()
}

function onSizeChange(size) {
  pageSize.value = size
  page.value = 1
  load()
}

function openTrip(row) {
  router.push({ path: '/timeline', query: { history_id: String(row.id) } })
}

async function onDelete(row) {
  try {
    await ElMessageBox.confirm(
      `确定删除「${row.title || row.city}」吗？删除后不可恢复。`,
      '删除行程',
      { confirmButtonText: '删除', cancelButtonText: '取消', type: 'warning' }
    )
  } catch {
    return
  }
  try {
    await deleteHistory(row.id)
    ElMessage.success('已删除')
    // 删掉当前页最后一条时回退一页
    if (rows.value.length === 1 && page.value > 1) page.value -= 1
    await load()
  } catch (e) {
    ElMessage.error(e.message || '删除失败')
  }
}

onMounted(async () => {
  await ensureInitialized()
  await load()
})
</script>

<template>
  <div class="history-page">
    <div class="pc-card">
      <div class="head">
        <div>
          <h2 class="page-title">我的行程</h2>
          <div class="pc-muted">共 {{ total }} 条，点击「查看」可在桌面端回看完整行程</div>
        </div>
        <el-button :icon="Refresh" :loading="loading" @click="load">刷新</el-button>
      </div>

      <el-table v-loading="loading" :data="rows" style="width: 100%" stripe>
        <el-table-column prop="title" label="行程标题" min-width="200" show-overflow-tooltip>
          <template #default="{ row }">
            <span class="cell-strong">{{ row.title || `${row.city}${row.days}日游` }}</span>
          </template>
        </el-table-column>
        <el-table-column label="路线" min-width="150">
          <template #default="{ row }">
            {{ row.origin ? `${row.origin} → ${row.city}` : row.city }}
          </template>
        </el-table-column>
        <el-table-column prop="days" label="天数" width="80" align="center" />
        <el-table-column label="安排" width="90" align="center">
          <template #default="{ row }">{{ row.spot_count }} 个</template>
        </el-table-column>
        <el-table-column label="预算" width="110" align="right">
          <template #default="{ row }">—</template>
        </el-table-column>
        <el-table-column prop="profile" label="节奏" width="90" align="center">
          <template #default="{ row }">
            <el-tag size="small" effect="plain">{{ row.profile }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="生成时间" width="150">
          <template #default="{ row }">
            <span class="pc-muted">{{ row.dateText }}</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="140" align="center" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" :icon="View" @click="openTrip(row)">查看</el-button>
            <el-button link type="danger" :icon="Delete" @click="onDelete(row)">删除</el-button>
          </template>
        </el-table-column>
        <template #empty>
          <el-empty description="还没有保存过行程，先去规划一条吧">
            <el-button type="primary" @click="router.push('/')">去规划</el-button>
          </el-empty>
        </template>
      </el-table>

      <div class="pager">
        <el-pagination
          background
          layout="total, sizes, prev, pager, next"
          :total="total"
          :current-page="page"
          :page-size="pageSize"
          :page-sizes="[10, 20, 50]"
          @current-change="onPageChange"
          @size-change="onSizeChange"
        />
      </div>
    </div>
  </div>
</template>

<style scoped>
.history-page {
  max-width: 1440px;
  margin: 0 auto;
}
.head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  margin-bottom: 18px;
}
.page-title {
  margin: 0 0 6px;
  font-size: 20px;
  font-weight: 700;
}
.cell-strong {
  font-weight: 600;
  color: var(--app-text);
}
.cost {
  color: var(--app-danger);
  font-weight: 600;
}
.pager {
  margin-top: 18px;
  display: flex;
  justify-content: flex-end;
}
</style>
