<template>
  <div class="pc-agent">
    <div class="pa-main">
      <!-- 会话区 -->
      <div class="pa-thread">
        <div class="pa-thread-inner">
          <!-- 历史对话：必须按 role 区分左右（旧代码全写成 user 气泡，
               助手的整段回答显示在用户气泡里，看起来像用户自己说的） -->
          <div v-for="(turn, i) in history" :key="'h' + i" class="pa-turn">
            <div
              class="pa-bubble"
              :class="turn.role === 'assistant' ? 'ai' : 'user'"
            >
              {{ turn.content }}
            </div>
          </div>

          <div v-if="lastMessage" class="pa-turn">
            <div class="pa-bubble user">{{ lastMessage }}</div>
          </div>

          <div v-if="lastMessage || steps.length || answer" class="pa-turn">
            <AgentTimeline
              :steps="steps"
              :answer="answer"
              :status-text="statusText"
              :error="error"
              :done-reason="doneReason"
              @retry="retry"
            />
          </div>

          <!-- 空状态：不能只有两行字飘在中间。
               桌面端屏幕更宽，留白会被放大成"页面没做完"的感觉，
               所以给可点的示例 + 能力说明，把空白填成引导。 -->
          <div v-if="!lastMessage && !steps.length" class="pa-empty">
            <div class="pa-empty-kicker">AI TRAVEL · 对话式规划</div>
            <h2 class="pa-empty-title">
              想怎么玩，<em class="pa-empty-serif">直接说</em>
            </h2>
            <p class="pa-empty-hint">
              我会理解你的需求，但景点、顺序、时间仍由算法排定——
              所以给出的行程是可解释的，不是编的。
            </p>

            <div class="pa-examples">
              <button
                v-for="ex in examples"
                :key="ex"
                type="button"
                class="pa-example"
                :disabled="running"
                @click="useExample(ex)"
              >
                <span class="pa-example-arrow">→</span>
                <span>{{ ex }}</span>
              </button>
            </div>

            <div class="pa-abilities">
              <div class="pa-ability-title">我能帮你</div>
              <ul>
                <li>按兴趣和时间排出可执行的每日行程</li>
                <li>算清景点间的距离、车程与开放时间冲突</li>
                <li>记住你的偏好（同行人、饮食忌口、节奏）</li>
              </ul>
            </div>
          </div>
        </div>
      </div>

      <!-- 输入区 -->
      <div class="pa-input">
        <el-input
          v-model="draft"
          type="textarea"
          :rows="2"
          resize="none"
          maxlength="500"
          show-word-limit
          placeholder="说说你的旅行想法…（Enter 发送，Shift+Enter 换行）"
          :disabled="running"
          @keydown.enter.exact.prevent="onSend"
        />
        <el-button v-if="!running" type="primary" :disabled="!draft.trim()" @click="onSend">
          发送
        </el-button>
        <el-button v-else type="danger" plain @click="stop">停止</el-button>
      </div>
    </div>

    <!-- 右侧：产出与画像 -->
    <aside class="pa-side">
      <div class="pa-card">
        <div class="pa-card-title">本次行程</div>
        <template v-if="itinerary">
          <div class="pa-itin-head">
            <span class="pa-itin-city">{{ itinerary.city }}</span>
            <span class="pa-itin-days">{{ itinerary.plan?.length || 0 }} 天</span>
          </div>
          <div class="pa-itin-cost">
            共 {{ (itinerary.plan || []).length }} 天 · {{ (itinerary.plan || []).reduce((n, d) => n + (d.items || []).length, 0) }} 个安排
          </div>
          <el-button text type="primary" @click="openTimeline">在时间线中查看 ›</el-button>
        </template>
        <div v-else class="pa-card-empty">还没有生成行程</div>
      </div>

      <!-- 画像更新提示：让用户知道系统"记住了什么" -->
      <div class="pa-card" v-if="profileUpdate">
        <div class="pa-card-title">已更新你的偏好画像</div>
        <ul class="pa-profile">
          <li v-if="profileUpdate.pace">节奏倾向：{{ profileUpdate.pace }}</li>
          <li v-if="(profileUpdate.companions || []).length">
            同行人：{{ profileUpdate.companions.join('、') }}
          </li>
          <li v-if="(profileUpdate.dietary || []).length">
            饮食：{{ profileUpdate.dietary.join('、') }}
          </li>
          <li>置信度：{{ (profileUpdate.confidence ?? 0).toFixed(2) }}</li>
        </ul>
        <div class="pa-tip">画像只作参考，你本次说的要求一律优先</div>
      </div>
    </aside>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAgentStream } from '@/composables/useAgentStream'
import { getSession } from '@/api/agent'
import { stashAgentTrip } from '@/utils/agentTrip'
import AgentTimeline from '@/components/AgentTimeline.vue'

const router = useRouter()
const route = useRoute()
const {
  steps, answer, itinerary, itineraryFull, statusText, running, error, doneReason, profileUpdate, sessionId, send, stop,
} = useAgentStream()

const draft = ref('')
const lastMessage = ref('')
const history = ref([])

// 空状态的可点示例。**每一句都必须是系统真能做到的**：
//   - 不再提"预算"——预算系统已移除，选点按时间与兴趣，不按钱
//   - 「带老人」真的会减少每日景点数（COMPANION_SPOT_DELTA["老人"]）
//   - 「节奏慢一点」真的会放大每站停留时间（PACE_DURATION_SCALE）
//   - 「两天/三天」真的影响聚类数与每日时间容量
const examples = [
  '苏州两天，喜欢园林和历史，节奏慢一点',
  '杭州三天，带老人，别排太满',
  '成都两天，主要是吃，别太赶',
  '南京一天，只看博物馆，走路少一点',
]

/** 点示例 = 填进输入框并直接发送，省一次点击 */
async function useExample(text) {
  if (running.value) return
  draft.value = text
  await onSend()
}

const historyForApi = computed(() =>
  history.value.slice(-6).map((t) => ({ role: t.role || 'user', content: t.content || '' })),
)

async function onSend() {
  const text = draft.value.trim()
  if (!text || running.value) return
  if (lastMessage.value) {
    history.value.push({ role: 'user', content: lastMessage.value })
    if (answer.value) history.value.push({ role: 'assistant', content: answer.value })
  }
  lastMessage.value = text
  draft.value = ''
  await send(text, historyForApi.value)
}

function retry() {
  if (!lastMessage.value) return
  send(lastMessage.value, historyForApi.value)
}

// ---- 从「AI 会话记录」继续对话 ----
//
// 记录页跳过来时 URL 带 session_id：把历史消息读回来铺成气泡，
// 并把 sessionId 设成它，这样下一句话会续在同一条会话（服务端按 id 追加）。
const resumeSessionId = computed(() => String(route.query.session_id || ''))

async function resumeFromHistory() {
  const sid = resumeSessionId.value
  if (!sid) return
  try {
    const res = await getSession(sid)
    const data = res?.data
    const msgs = (data && data.messages) || []
    if (!msgs.length) return
    const last = msgs[msgs.length - 1]
    const head = last && last.role === 'assistant' ? msgs.slice(0, -1) : msgs
    if (last && last.role === 'assistant') {
      const prev = head[head.length - 1]
      const isPaired = prev && prev.role === 'user'
      lastMessage.value = isPaired ? prev.content : ''
      history.value = (isPaired ? head.slice(0, -1) : head).map((m) => ({ role: m.role, content: m.content }))
      answer.value = last.content
    } else {
      history.value = head.map((m) => ({ role: m.role, content: m.content }))
    }
    sessionId.value = sid
  } catch (e) {
    // 会话可能已被删除：不打断使用，正常开新会话即可
    console.warn('恢复会话失败，按新会话处理', e)
  }
}

onMounted(resumeFromHistory)

function openTimeline() {
  router.push({
    path: '/timeline',
    query: {
      city: itinerary.value?.city,
      days: itinerary.value?.plan?.length || 3,
      // 规划入参必须一起带过去：Timeline 全部从 URL query 读，
      // 漏了 origin 就退回默认「当涂」，漏了 tags 行程就会和助手给的不一致。
      // 把完整行程暂存，时间线直接渲染，不再重新规划
      agent_trip: stashAgentTrip(itineraryFull.value) || undefined,
      origin: itinerary.value?.origin || undefined,
      tags: (itinerary.value?.tags || []).join(',') || undefined,
      must_go: (itinerary.value?.must_go || []).join(',') || undefined,
      avoid: (itinerary.value?.avoid || []).join(',') || undefined,
      profile: itinerary.value?.profile || undefined,
    },
  })
}
</script>

<style scoped>
.pc-agent { display: flex; gap: 16px; height: calc(100vh - 96px); }
.pa-main { flex: 1; display: flex; flex-direction: column; min-width: 0; }
.pa-thread { flex: 1; overflow-y: auto; background: var(--app-surface); border-radius: 12px; padding: 16px; }
.pa-thread-inner { max-width: 780px; }

.pa-turn { margin-bottom: 14px; overflow: hidden; }
.pa-bubble {
  display: inline-block; max-width: 78%; padding: 10px 14px; border-radius: 12px;
  font-size: 13.5px; line-height: 1.7; white-space: pre-wrap;
}
.pa-bubble.ai {
  background: var(--app-surface);
  color: var(--app-text);
  border: 1px solid var(--app-hairline);
  border-bottom-left-radius: 4px;
}
.pa-bubble.user { background: var(--app-brand); color: var(--app-surface); border-bottom-right-radius: 4px; float: right; }

.pa-input { display: flex; gap: 10px; align-items: flex-end; margin-top: 12px; }

/* 空状态：桌面端屏幕宽，纯留白会被放大成"页面没做完"，
   所以做成左对齐的编辑式引导块，而不是居中的两行字。 */
.pa-empty {
  padding: 56px 8px 24px;
  text-align: left;
  max-width: 620px;
}
.pa-empty-kicker {
  font-size: 10.5px;
  letter-spacing: 0.22em;
  color: var(--app-brand);
  font-weight: 600;
}
.pa-empty-title {
  margin: 12px 0 0;
  font-size: 28px;
  font-weight: 600;
  line-height: 1.28;
  letter-spacing: -0.01em;
  color: var(--app-text);
}
.pa-empty-serif {
  font-family: var(--app-font-serif);
  font-style: italic;
  font-weight: 500;
  color: var(--app-brand);
  margin: 0 3px;
}
.pa-empty-hint {
  margin: 12px 0 0;
  font-size: 14px;
  line-height: 1.7;
  color: var(--app-text-secondary);
  max-width: 44ch;
}

/* 可点示例 */
.pa-examples {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
  margin-top: 26px;
}
.pa-example {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  text-align: left;
  padding: 14px 16px;
  border-radius: var(--app-radius-md);
  background: var(--app-surface);
  border: 1px solid var(--app-hairline);
  box-shadow: var(--app-elev-1);
  color: var(--app-text);
  font-size: 13.5px;
  line-height: 1.55;
  font-family: inherit;
  cursor: pointer;
  transition: transform var(--app-dur-fast) var(--app-ease-out),
              box-shadow var(--app-dur-base) var(--app-ease-out),
              border-color var(--app-dur-base) var(--app-ease-out);
}
.pa-example:hover:not(:disabled) {
  box-shadow: var(--app-elev-2);
  border-color: var(--app-brand-soft);
}
.pa-example:active:not(:disabled) { transform: scale(var(--app-press-scale)); }
.pa-example:disabled { opacity: 0.5; cursor: default; }
.pa-example-arrow {
  color: var(--app-brand);
  flex-shrink: 0;
  line-height: 1.55;
}

/* 能力说明 */
.pa-abilities {
  margin-top: 26px;
  padding-top: 18px;
  border-top: 1px solid var(--app-hairline);
}
.pa-ability-title {
  font-size: 10.5px;
  letter-spacing: 0.18em;
  color: var(--app-text-tertiary);
}
.pa-abilities ul {
  margin: 10px 0 0;
  padding-left: 18px;
  list-style: none;
}
.pa-abilities li {
  position: relative;
  font-size: 13px;
  line-height: 1.9;
  color: var(--app-text-secondary);
}
.pa-abilities li::before {
  content: '';
  position: absolute;
  left: -15px;
  top: 10px;
  width: 4px;
  height: 4px;
  border-radius: 50%;
  background: var(--app-brand);
  opacity: 0.55;
}
@media (prefers-reduced-motion: reduce) {
  .pa-example, .pa-example:active { transition: none !important; transform: none !important; }
}

.pa-side { width: 320px; flex: 0 0 320px; display: flex; flex-direction: column; gap: 12px; }
.pa-card { background: var(--app-surface); border-radius: 12px; padding: 14px 16px; }
.pa-card-title { font-size: 13px; font-weight: 600; color: var(--app-text); margin-bottom: 10px; }
.pa-card-empty { font-size: 12.5px; color: var(--app-text-tertiary); }
.pa-itin-head { display: flex; align-items: baseline; gap: 8px; }
.pa-itin-city { font-size: 17px; font-weight: 600; color: var(--app-text); }
.pa-itin-days { font-size: 12px; color: var(--app-text-secondary); }
.pa-itin-cost { margin-top: 6px; font-size: 13px; color: var(--app-warning); }
.pa-itin-note { margin-top: 8px; font-size: 11.5px; color: var(--app-text-tertiary); line-height: 1.7; }
.pa-profile { margin: 0; padding-left: 18px; font-size: 12.5px; color: var(--app-text-secondary); line-height: 1.9; }
.pa-tip { margin-top: 8px; font-size: 11.5px; color: var(--app-warning); background: var(--app-warning-soft); border-radius: 6px; padding: 6px 8px; }
</style>
