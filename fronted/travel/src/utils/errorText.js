/**
 * 把后端/网络错误翻成「用户能行动」的话。
 *
 * ## 为什么需要
 *
 * 原来的兜底文案是「网络异常，请确认后端服务已启动」——那是**给开发看的**。
 * 普通用户看到这句话只能愣在那里：既不知道自己能不能重试，也不知道"后端"
 * 是什么。评委在部署环境里一旦遇到接口不通，看到的就是这句话。
 *
 * 这里按 `code / status` 分档，每档返回：
 *   - `title`  一句话说清发生了什么（不出现技术名词）
 *   - `desc`   可选补充（什么时候能好、怎么自己确认）
 *   - `action` 该做什么：'retry' | 'login' | 'home' | 'help' | ''
 *   - `tone`   'error' | 'warning'，决定配色
 *
 * 判定顺序很重要：先看业务码，再看 HTTP 状态，最后才是网络错误兜底。
 */

const ACTIONS = {
  retry: '重试',
  login: '去登录',
  home: '回首页',
  help: '看使用说明',
}

function pickAction(action) {
  return action ? { action, actionLabel: ACTIONS[action] || '继续' } : { action: '', actionLabel: '' }
}

/**
 * @param {any} err 任意错误（axios 错误、ApiError、字符串）
 * @param {{ context?: 'plan' | 'history' | 'agent' | 'import' | 'generic' }} [opts]
 */
export function describeError(err, opts = {}) {
  const context = opts.context || 'generic'

  // ---- 1. 业务码 ----
  const code = Number(err?.code ?? err?.response?.data?.code ?? 0)
  const status = Number(err?.status ?? err?.response?.status ?? 0)
  const serverMsg = String(err?.response?.data?.message || err?.message || '').trim()

  if (code === 401 || status === 401) {
    return {
      title: '需要先登录',
      desc: '登录后才能查看账号下的行程与会话记录；行程本身不登录也能规划。',
      tone: 'warning',
      ...pickAction('login'),
    }
  }
  if (code === 404 || status === 404) {
    return {
      title: '这条记录不存在',
      desc: '可能已被删除，或者链接不完整。',
      tone: 'warning',
      ...pickAction('home'),
    }
  }
  if (code === 429 || status === 429) {
    return {
      title: '操作太频繁了',
      desc: serverMsg || '稍等一分钟再试，接口有频率限制。',
      tone: 'warning',
      ...pickAction('retry'),
    }
  }

  // ---- 网关/网络类必须排在通用 5xx 之前 ----
  //
  // 502/503/504 是"连不到后端"（本地静态服务或 nginx 抛的），不是后端自身崩溃。
  // 若先按 5xx 处理，用户会看到「服务出了点问题」——既不准确也不告诉他检查后端。
  const isGateway = status === 502 || status === 503 || status === 504
  const isNetwork =
    isGateway ||
    err?.isNetworkError === true ||
    /network error|failed to fetch|econnrefused|load failed|net::/i.test(serverMsg)

  if (isNetwork) {
    return {
      title: '连不上服务',
      desc: '浏览器没能连到后端接口。如果你是在本地部署，确认后端已在 8080 端口启动。',
      tone: 'error',
      ...pickAction('retry'),
    }
  }

  if (status >= 500 || code >= 500) {
    return {
      title: '服务出了点问题',
      desc: '这次是服务端异常，不是你操作的问题。可以稍后重试，或先看看使用说明。',
      tone: 'error',
      ...pickAction('retry'),
    }
  }

  // ---- 3. 业务层可读消息：直接用，但不再附加技术建议 ----
  if (serverMsg && !/^(request failed|network)/i.test(serverMsg)) {
    const byContext = {
      plan: '行程没生成出来，可以换一组条件再试。',
      history: '这条行程没能读出来。',
      agent: '这一轮对话没完成，可以直接重发一次。',
      import: '识别没成功，可以改用粘贴文字。',
      generic: '可以重试一次。',
    }
    return {
      title: '没能完成',
      desc: serverMsg,
      tone: 'error',
      ...pickAction(context === 'agent' || context === 'plan' || context === 'import' ? 'retry' : 'home'),
    }
  }

  // ---- 4. 真正未知 ----
  return {
    title: '没能完成',
    desc: context === 'plan'
      ? '行程生成失败。可能是目的地数据不足或网络不稳定，可以重试或换座城市。'
      : '这次操作没成功，可以重试一次。',
    tone: 'error',
    ...pickAction('retry'),
  }
}
