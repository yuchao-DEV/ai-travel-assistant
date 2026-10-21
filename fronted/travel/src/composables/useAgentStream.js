import { ref, shallowRef } from 'vue'
import { agentChat } from '@/api/agent'

/**
 * Agent 事件流 composable：把 SSE 的十种事件收成一个可渲染的状态。
 *
 * 双端共用（移动端 AgentChat / 桌面端 AgentChat 都用它），
 * 所以这里**不引入任何 UI 库**，只维护数据。
 *
 * 设计要点：
 * - `steps` 是**过程时间线**（工具卡片 + 思考 + 知识来源），按到达顺序累积；
 * - `answer` 是最终答复的流式拼接（chunk 事件累加）；
 * - `itinerary` 单独存，前端用它渲染行程卡；
 * - 中断（AbortController）只改变 `status`，**不清空已有内容**——
 *   用户点了停止，看到的应该是已经跑出来的东西，而不是一片空白。
 */
export function useAgentStream() {
  const steps = ref([])
  const answer = ref('')
  const itinerary = shallowRef(null)
  const statusText = ref('')
  const running = ref(false)
  const error = ref('')
  const doneReason = ref('')
  const profileUpdate = shallowRef(null)
  const sessionId = ref('')

  let controller = null

  function reset() {
    steps.value = []
    answer.value = ''
    itinerary.value = null
    statusText.value = ''
    error.value = ''
    doneReason.value = ''
    profileUpdate.value = null
  }

  /** 一个工具卡片：开始与结束两个事件配对到同一个对象上。 */
  function findStep(callId) {
    return steps.value.find((s) => s.kind === 'tool' && s.callId === callId)
  }

  function handleEvent(evt) {
    if (!evt || !evt.type) return
    switch (evt.type) {
      case 'status':
        statusText.value = evt.text || ''
        break

      case 'thought':
        if (evt.text) steps.value.push({ kind: 'thought', text: evt.text })
        break

      case 'tool_start':
        steps.value.push({
          kind: 'tool',
          callId: evt.call_id,
          name: evt.name,
          args: evt.args || {},
          round: evt.round || 0,
          pending: true,
          ok: null,
          summary: '',
          error: '',
          elapsedMs: 0,
          expanded: false,
        })
        break

      case 'tool_end': {
        const step = findStep(evt.call_id)
        if (step) {
          step.pending = false
          step.ok = evt.ok !== false
          step.summary = evt.summary || ''
          step.error = evt.error || ''
          step.code = evt.code || ''
          step.elapsedMs = evt.elapsed_ms || 0
        }
        break
      }

      case 'knowledge':
        steps.value.push({
          kind: 'knowledge',
          sources: evt.sources || [],
          vectorReady: !!evt.vector_ready,
        })
        break

      case 'itinerary':
        itinerary.value = evt.itinerary || null
        break

      case 'profile_update':
        profileUpdate.value = evt.profile || null
        break

      case 'chunk':
        answer.value += evt.text || ''
        break

      case 'error':
        error.value = evt.message || '发生未知错误'
        break

      case 'done':
        doneReason.value = evt.reason || ''
        statusText.value = ''
        break

      default:
        break
    }
  }

  /**
   * 发起一轮对话。
   * @param {string} message
   * @param {Array} history 最近几轮对话（`{role, content}`）
   */
  async function send(message, history = []) {
    if (running.value) return
    reset()
    running.value = true
    controller = new AbortController()
    statusText.value = '正在连接…'
    try {
      await agentChat(
        { message, session_id: sessionId.value, history },
        { onEvent: handleEvent, signal: controller.signal },
      )
    } catch (err) {
      // 用户主动中断不算错误
      if (err?.name === 'AbortError') {
        statusText.value = ''
        error.value = ''
        doneReason.value = 'aborted'
      } else {
        error.value = err?.message || '请求失败，请重试'
        statusText.value = ''
      }
    } finally {
      running.value = false
      controller = null
    }
  }

  /** 中断当前会话。**不清空已有内容**——用户该看到已经跑出来的部分。 */
  function stop() {
    if (controller) controller.abort()
  }

  return {
    steps,
    answer,
    itinerary,
    statusText,
    running,
    error,
    doneReason,
    profileUpdate,
    sessionId,
    send,
    stop,
    reset,
  }
}
