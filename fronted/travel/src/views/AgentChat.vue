<template>
  <div class="agent-page">
    <van-nav-bar title="AI 旅行助手" left-arrow @click-left="goBack" />

    <div class="agent-body">
      <!-- 历史对话（只保留本轮之外的最近几条，避免页面无限长） -->
      <!-- 历史对话：必须按 role 区分左右。
           旧代码把 assistant 的答复也写成 class="bubble user"，
           结果助手的整段回答显示在绿色用户气泡里，看起来像用户自己说的。 -->
      <div v-for="(turn, i) in history" :key="'h' + i" class="turn">
        <div
          class="bubble"
          :class="turn.role === 'assistant' ? 'ai' : 'user'"
        >
          {{ turn.content }}
        </div>
      </div>

      <!-- 本轮：用户提问 -->
      <div v-if="lastMessage" class="turn">
        <div class="bubble user">{{ lastMessage }}</div>
      </div>

      <!-- 本轮：智能体的过程 + 答复 -->
      <div v-if="lastMessage || steps.length || answer" class="turn">
        <AgentTimeline
          :steps="steps"
          :answer="answer"
          :status-text="statusText"
          :error="error"
          :done-reason="doneReason"
          @retry="retry"
        />
        <!-- 行程卡：真正的产出物，独立展示 -->
        <div v-if="itinerary" class="itinerary-card" @click="openItinerary">
          <div class="ic-head">
            <span class="ic-city">{{ itinerary.city }}</span>
            <span class="ic-days">{{ itinerary.plan?.length || 0 }} 天</span>
          </div>
          <div class="ic-meta">
            共 {{ (itinerary.plan || []).length }} 天 · {{ (itinerary.plan || []).reduce((n, d) => n + (d.items || []).length, 0) }} 个安排
          </div>
          <div class="ic-more">查看完整行程 ›</div>
        </div>

        <!-- 结果闭环：告诉用户"不满意怎么继续"。
             这不是装饰文案——这些说法真的会被解析成参数调整并重排一版。 -->
        <div v-if="itinerary" class="result-hint">
          <span class="rh-title">不满意可以直接说</span>
          <div class="rh-examples">
            <button
              v-for="s in adjustExamples"
              :key="s"
              type="button"
              class="rh-chip"
              :disabled="running"
              @click="quickAdjust(s)"
            >{{ s }}</button>
          </div>
        </div>
      </div>

      <!-- 空状态：不能只有两行字。
           用户打开这里最需要知道的是"我能问什么"，所以给可点的示例；
           示例必须是**系统真能做到的事**——旧文案里的「预算 2500」在
           预算系统移除后已经不成立，一并换掉。 -->
      <div v-if="!lastMessage && !steps.length" class="empty">
        <div class="empty-kicker">AI TRAVEL · 对话式规划</div>
        <h2 class="empty-title">
          想怎么玩，<span class="empty-serif">直接说</span>
        </h2>
        <p class="empty-hint">
          我会理解你的需求，但景点、顺序、时间仍由算法排定——
          所以给出的行程是可解释的，不是编的。
        </p>

        <div class="empty-examples">
          <button
            v-for="ex in examples"
            :key="ex"
            type="button"
            class="example-chip"
            :disabled="running"
            @click="useExample(ex)"
          >
            {{ ex }}
          </button>
        </div>

        <div class="empty-abilities">
          <div class="ability-title">我能帮你</div>
          <ul class="ability-list">
            <li>按兴趣和时间排出可执行的每日行程</li>
            <li>算清景点间的距离、车程与开放时间冲突</li>
            <li>记住你的偏好（同行人、饮食忌口、节奏）</li>
          </ul>
        </div>
      </div>
    </div>

    <!-- 输入区 -->
    <div ref="inputWrapEl" class="agent-input">
      <van-field
        v-model="draft"
        placeholder="说说你的旅行想法…"
        rows="1"
        autosize
        type="textarea"
        maxlength="500"
        :disabled="running"
        @keyup.enter.exact.prevent="onSend"
      />
      <van-button
        v-if="!running"
        type="primary"
        size="small"
        round
        :disabled="!draft.trim()"
        @click="onSend"
      >发送</van-button>
      <van-button v-else type="danger" size="small" round plain @click="stop">停止</van-button>
    </div>

    <!-- 首访小气泡：指向输入区 -->
    <BubbleTip
      v-model="showAgentTip"
      :target="() => inputWrapEl"
      :max-width="250"
      placement="top"
      @close="closeAgentTip"
    >
      <p>
        把想法写下来就行，不用讲究格式。<br />
        比如「杭州三天，带老人，别排太满」——说得越具体，排得越准。
      </p>
    </BubbleTip>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAgentStream } from '@/composables/useAgentStream'
import { getSession } from '@/api/agent'
import { stashAgentTrip } from '@/utils/agentTrip'
import AgentTimeline from '@/components/AgentTimeline.vue'
import BubbleTip from '@/components/BubbleTip.vue'
import {
  GUIDE_IDS, claimGuide, hasSeenGuide, markGuideSeen, releaseGuide,
} from '@/utils/guide'

const router = useRouter()
const route = useRoute()
const { steps, answer, itinerary, itineraryFull, statusText, running, error, doneReason, sessionId, send, stop } =
  useAgentStream()

const draft = ref('')
const lastMessage = ref('')
const history = ref([])

// 空状态的可点示例。
//
// 挑选标准：**每一句都是系统真能做到的**。
//   - 不提"预算"，因为预算系统已移除（选点按时间与兴趣，不按钱）
//   - 带老人/带小孩 → 真的会减少每日景点数（COMPANION_SPOT_DELTA）
//   - 「两天」「三天」 → 真的影响聚类与每日容量
//   - 「避开人多的」→ 会走 avoid 与评分排序
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

/**
 * 生成行程后的「不满意怎么改」快捷语。
 *
 * 每一条都是**真的会被解析并重新排一版**的说法：
 *   - 「太满了」        → 走 pace=宽松 / 减少每日选点
 *   - 「换成自然向」    → 走 tags=[自然] 重排
 *   - 「别排这么紧」    → 同上，慢节奏
 *   - 「加一天」        → 改 days
 * 写在这里是为了让用户知道系统支持"用嘴改"，而不是只能从头再填一遍表单。
 */
const adjustExamples = ['太满了', '换成自然向', '别排这么紧', '加一天']

async function quickAdjust(text) {
  if (running.value) return
  if (lastMessage.value) {
    history.value.push({ role: 'user', content: lastMessage.value })
    if (answer.value) history.value.push({ role: 'assistant', content: answer.value })
  }
  lastMessage.value = text
  await send(text, historyForApi.value)
}

// 多轮上下文：只带最近的对话，避免 system 之外的 token 无限增长
const historyForApi = computed(() =>
  history.value.slice(-6).map((t) => ({ role: t.role || 'user', content: t.content || '' })),
)

function goBack() {
  router.back()
}

async function onSend() {
  const text = draft.value.trim()
  if (!text || running.value) return
  // 上一轮的问题进入 history（保留上下文）
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
    // 最后一条是助手答复 → 当作"当前这一轮"，其余进历史
    const last = msgs[msgs.length - 1]
    const head = last && last.role === 'assistant' ? msgs.slice(0, -1) : msgs
    history.value = head.map((m) => ({ role: m.role, content: m.content }))
    if (last && last.role === 'assistant') {
      const prev = head[head.length - 1]
      lastMessage.value = prev && prev.role === 'user' ? prev.content : ''
      if (lastMessage.value) history.value = head.slice(0, -1).map((m) => ({ role: m.role, content: m.content }))
      answer.value = last.content
    }
    sessionId.value = sid
  } catch (e) {
    // 会话可能被删了：不打断使用，正常开新会话即可
    console.warn('恢复会话失败，按新会话处理', e)
  }
}

onMounted(resumeFromHistory)

// 首访小气泡：只说一句"其实可以随便说"，比再加一段说明更有效
const showAgentTip = ref(false)
const inputWrapEl = ref(null)
let agentTipTimer = null

onMounted(() => {
  if (hasSeenGuide(GUIDE_IDS.agentTip)) return
  agentTipTimer = setTimeout(() => {
    if (lastMessage.value || steps.value.length) return
    if (!claimGuide(GUIDE_IDS.agentTip)) return
    showAgentTip.value = true
  }, 1600)
})

function closeAgentTip() {
  showAgentTip.value = false
  markGuideSeen(GUIDE_IDS.agentTip)
  releaseGuide(GUIDE_IDS.agentTip)
}


function openItinerary() {
  // 把助手这份**完整行程**暂存，时间线直接渲染，不再重新规划。
  // 之前只传 city/days，时间线会用算法再跑一遍——用户点进去看到的是另一份行程。
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
/* 整页固定成「顶部导航 + 可滚动正文 + 底部输入框」三段，页面本身不滚。
   ## 为什么旧写法会滚走
   1. `height: 100vh` —— 手机浏览器里 100vh 是**包含地址栏**的高度，
      实际可视区更矮，内容溢出 → 整个文档开始滚，导航和输入框被一起拖走。
      改用 `100dvh`（动态视口高度），地址栏收起/展开都会自动适配。
      保留 100vh 作为老浏览器兜底。
   2. `<van-tabbar placeholder>` 渲染的占位 div 是 .agent-page 的**兄弟**，
      额外占掉一条 tabbar 的高度。不减掉它，总高就是 100dvh + 50px，
      文档照样能滚。
   3. flex 子项默认 `min-height: auto`，不加 `min-height: 0` 时
      `.agent-body` 在部分内核里不会产生滚动容器。 */
.agent-page {
  display: flex;
  flex-direction: column;
  height: 100vh;
  height: calc(100dvh - var(--van-tabbar-height, 50px));
  max-height: calc(100dvh - var(--van-tabbar-height, 50px));
  overflow: hidden;
  background: var(--app-bg-subtle);
}
/* 导航条与输入框都不参与压缩 */
.agent-page :deep(.van-nav-bar) { flex-shrink: 0; }
.agent-body {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  -webkit-overflow-scrolling: touch;
  overscroll-behavior: contain;
  padding: 12px 14px 8px;
}

.turn { margin-bottom: 12px; }
.bubble {
  display: inline-block; max-width: 86%; padding: 9px 12px; border-radius: 12px;
  font-size: 13.5px; line-height: 1.65; white-space: pre-wrap;
}
.bubble.ai {
  background: var(--app-surface);
  color: var(--app-text);
  border: 1px solid var(--app-hairline);
  border-bottom-left-radius: 4px;
  float: left;
}
.bubble.user { background: var(--app-brand); color: var(--app-surface); border-bottom-right-radius: 4px; float: right; }
.turn { overflow: hidden; }

.itinerary-card {
  margin-top: 12px; background: var(--app-surface); border-radius: 12px; padding: 12px 14px;
  box-shadow: 0 2px 10px rgba(var(--app-brand-rgb), 0.08); cursor: pointer;
}
.ic-head { display: flex; align-items: baseline; gap: 8px; }
.ic-city { font-size: 16px; font-weight: 600; color: var(--app-text); }
.ic-days { font-size: 12px; color: var(--app-text-secondary); }
.ic-meta { margin-top: 6px; font-size: 13px; color: var(--app-warning); }
.ic-note { margin-top: 6px; font-size: 11.5px; color: var(--app-text-tertiary); line-height: 1.6; }
.ic-more { margin-top: 8px; font-size: 12.5px; color: var(--app-brand); text-align: right; }

.empty { padding: 60px 20px; text-align: center; }
.empty-title { font-size: 16px; font-weight: 600; color: var(--app-text); }
.empty-hint { margin-top: 8px; font-size: 12.5px; color: var(--app-text-tertiary); line-height: 1.8; }

.agent-input {
  display: flex; align-items: flex-end; gap: 8px; padding: 8px 12px;
  background: var(--app-surface); border-top: 1px solid var(--app-border); padding-bottom: calc(8px + env(safe-area-inset-bottom));
  flex-shrink: 0;   /* 输入框永远留在底部，不被正文压缩掉 */
}
.agent-input :deep(.van-field) { flex: 1; background: var(--app-bg-subtle); border-radius: 10px; padding: 6px 10px; }

/* ==================== 空状态（引导区） ==================== */
.empty {
  padding: 34px 22px 20px;
  text-align: left;
}
.empty-kicker {
  font-size: 10px;
  letter-spacing: 0.22em;
  color: var(--app-brand);
  font-weight: 600;
}
.empty-title {
  margin: 10px 0 0;
  font-size: 25px;
  font-weight: 600;
  line-height: 1.28;
  letter-spacing: -0.01em;
  color: var(--app-text);
}
.empty-serif {
  font-family: var(--app-font-serif);
  font-style: italic;
  font-weight: 500;
  color: var(--app-brand);
  margin: 0 2px;
}
.empty-hint {
  margin: 10px 0 0;
  font-size: 13px;
  line-height: 1.75;
  color: var(--app-text-secondary);
  max-width: 34ch;
}

/* 可点示例：比一大段说明更有效——用户直接点就能看到效果 */
.empty-examples {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin-top: 20px;
}
.example-chip {
  width: 100%;
  text-align: left;
  padding: 13px 15px;
  border-radius: var(--app-radius-md);
  background: var(--app-surface);
  border: 1px solid var(--app-hairline);
  box-shadow: var(--app-elev-1);
  color: var(--app-text);
  font-size: 13.5px;
  line-height: 1.5;
  font-family: inherit;
  cursor: pointer;
  transition: transform var(--app-dur-fast) var(--app-ease-out),
              box-shadow var(--app-dur-base) var(--app-ease-out);
}
.example-chip:active:not(:disabled) {
  transform: scale(var(--app-press-scale));
  box-shadow: var(--app-elev-2);
}
.example-chip:disabled { opacity: 0.5; cursor: default; }

/* 结果闭环提示：生成行程后告诉用户"可以接着说" */
.result-hint {
  margin-top: 4px;
  padding: 12px 13px;
  border-radius: var(--app-radius-md);
  background: var(--app-brand-soft, rgba(66, 127, 98, 0.08));
  border: 1px solid var(--app-hairline);
}
.rh-title {
  display: block;
  font-size: 12px;
  font-weight: 650;
  color: var(--app-brand-hover);
  margin-bottom: 9px;
}
.rh-examples {
  display: flex;
  flex-wrap: wrap;
  gap: 7px;
}
.rh-chip {
  appearance: none;
  border: 1px solid var(--app-hairline-strong, rgba(0, 0, 0, 0.1));
  background: var(--app-surface);
  color: var(--app-text-secondary);
  font: inherit;
  font-size: 12.5px;
  padding: 6px 12px;
  border-radius: 999px;
  cursor: pointer;
  transition: background var(--app-dur-fast, 0.16s) var(--app-ease-out, ease-out),
              color var(--app-dur-fast, 0.16s) var(--app-ease-out, ease-out);
}
.rh-chip:active:not(:disabled) {
  transform: scale(var(--app-press-scale, 0.97));
  background: var(--app-brand-soft, rgba(66, 127, 98, 0.12));
  color: var(--app-brand-hover);
}
.rh-chip:disabled { opacity: 0.5; cursor: default; }
.rh-chip:focus-visible {
  outline: 2px solid var(--app-brand);
  outline-offset: 2px;
}

/* 能力说明：让人知道这套系统到底做了什么，而不是"AI 什么都行" */
.empty-abilities {
  margin-top: 22px;
  padding-top: 16px;
  border-top: 1px solid var(--app-hairline);
}
.ability-title {
  font-size: 10.5px;
  letter-spacing: 0.18em;
  color: var(--app-text-tertiary);
}
.ability-list {
  margin: 10px 0 0;
  padding-left: 16px;
  list-style: none;
}
.ability-list li {
  position: relative;
  font-size: 12.5px;
  line-height: 1.9;
  color: var(--app-text-secondary);
}
.ability-list li::before {
  content: '';
  position: absolute;
  left: -14px;
  top: 9px;
  width: 4px;
  height: 4px;
  border-radius: 50%;
  background: var(--app-brand);
  opacity: 0.55;
}

@media (prefers-reduced-motion: reduce) {
  .example-chip, .example-chip:active { transition: none !important; transform: none !important; }
}</style>
