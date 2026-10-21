import { getToken } from '@/utils/token'
import request from '@/utils/request'

/**
 * Agent 会话 API。
 *
 * `agentChat` 不走 `request`（axios）：axios 会缓冲整个响应，
 * 拿不到流式增量。这里用 fetch + ReadableStream 手写 SSE 解析，
 * 零新增依赖。
 */

function authHeader() {
  const token = getToken()
  return token ? { Authorization: `Bearer ${token}` } : {}
}

/**
 * 发起一次 Agent 会话，逐帧回调事件。
 *
 * 两个易错点（都会导致"偶尔乱码/丢帧"，且很难复现）：
 *
 * 1. **跨 chunk 的半帧必须留在 buffer 里**。网络分片是按字节切的，
 *    一个 `data: {...}\n\n` 完全可能被切成两次 read。直接对每次 read
 *    的结果做 JSON.parse 会解析失败并丢帧。
 * 2. **TextDecoder 必须带 `{ stream: true }`**。中文是多字节的，
 *    不带这个参数时，被切断的多字节字符会被解码成乱码（"�"）。
 *
 * @param {{message: string, session_id?: string, history?: Array}} payload
 * @param {{onEvent?: Function, signal?: AbortSignal}} options
 * @returns {Promise<void>}
 */
export async function agentChat(payload, { onEvent, signal } = {}) {
  const resp = await fetch('/api/agent/chat', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Accept: 'text/event-stream',
      ...authHeader(),
    },
    body: JSON.stringify(payload),
    signal,
  })
  if (!resp.ok) {
    let message = `请求失败（HTTP ${resp.status}）`
    if (resp.status === 401) message = '登录状态已失效，请重新登录'
    throw new Error(message)
  }
  if (!resp.body) {
    throw new Error('当前浏览器不支持流式响应')
  }

  const reader = resp.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    // SSE 以空行分帧；不足一帧的尾巴留在 buffer 里等下一片
    let idx
    while ((idx = buffer.indexOf('\n\n')) !== -1) {
      const frame = buffer.slice(0, idx)
      buffer = buffer.slice(idx + 2)
      const line = frame.split('\n').find((l) => l.startsWith('data:'))
      if (!line) continue
      try {
        onEvent?.(JSON.parse(line.slice(5).trim()))
      } catch {
        // 坏帧忽略：宁可少渲染一个事件，也不要整轮对话崩掉
      }
    }
  }
}

export function getSessions(page = 1, pageSize = 20) {
  return request.get('/api/agent/sessions', { params: { page, page_size: pageSize } })
}

export function getSession(sessionId) {
  return request.get(`/api/agent/session/${sessionId}`)
}

export function deleteSession(sessionId) {
  return request.delete(`/api/agent/session/${sessionId}`)
}

export function getProfile() {
  return request.get('/api/agent/profile')
}

export function clearProfile() {
  return request.delete('/api/agent/profile')
}
