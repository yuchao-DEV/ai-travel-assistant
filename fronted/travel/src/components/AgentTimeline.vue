<template>
  <div class="agent-timeline">
    <!-- 过程时间线：让用户看见"助手到底做了什么" -->
    <div v-if="steps.length" class="tl-steps">
      <div v-for="(step, i) in steps" :key="i" class="tl-step">
        <!-- 思考 -->
        <div v-if="step.kind === 'thought'" class="tl-thought">
          <span class="tl-dot thought"></span>
          <span class="tl-thought-text">{{ step.text }}</span>
        </div>

        <!-- 工具卡片：默认折叠，点开看参数与结果 -->
        <div v-else-if="step.kind === 'tool'" class="tl-tool">
          <div class="tl-tool-head" @click="toggle(step)">
            <span class="tl-dot" :class="dotClass(step)"></span>
            <span class="tl-tool-name">{{ toolLabel(step.name) }}</span>
            <span v-if="step.name && step.name !== toolLabel(step.name)" class="tl-tool-raw">
              {{ step.name }}
            </span>
            <span v-if="step.pending" class="tl-tool-status pending">执行中…</span>
            <span v-else-if="step.ok" class="tl-tool-status ok">
              {{ step.summary || '完成' }}
            </span>
            <!-- 需要登录是权限门，不是故障：不该显示成红色错误 -->
            <span v-else-if="isGate(step)" class="tl-tool-status gate">
              {{ step.error || '需要登录' }}
            </span>
            <span v-else class="tl-tool-status bad">
              {{ step.error || step.code || '失败' }}
            </span>
            <span v-if="step.elapsedMs" class="tl-tool-ms">{{ step.elapsedMs }}ms</span>
            <span class="tl-caret" :class="{ open: step.expanded }">›</span>
          </div>
          <div v-if="step.expanded" class="tl-tool-body">
            <div class="tl-kv">
              <span class="tl-k">调用参数</span>
              <pre class="tl-code">{{ pretty(step.args) }}</pre>
            </div>
            <div v-if="step.summary" class="tl-kv">
              <span class="tl-k">结果摘要</span>
              <span class="tl-v">{{ step.summary }}</span>
            </div>
            <div v-if="step.error" class="tl-kv">
              <span class="tl-k">错误</span>
              <span class="tl-v bad">{{ step.error }}<template v-if="step.code">（{{ step.code }}）</template></span>
            </div>
          </div>
        </div>

        <!-- 知识来源 -->
        <div v-else-if="step.kind === 'knowledge'" class="tl-knowledge">
          <div class="tl-knowledge-head">
            <span class="tl-dot knowledge"></span>
            <span>知识库命中 {{ (step.sources || []).length }} 条</span>
            <span v-if="!step.vectorReady" class="tl-badge">仅关键词检索</span>
          </div>
          <!-- 没命中要明说，不能让用户以为"查过了什么都没问题" -->
          <div v-if="!(step.sources || []).length" class="tl-empty">
            知识库中没有匹配的内容——回答里涉及的规则性信息请以官方公告为准
          </div>
          <ul v-else class="tl-sources">
            <li v-for="(src, si) in step.sources" :key="si">
              <span class="tl-src-title">{{ src.title }}</span>
              <span v-if="src.heading" class="tl-src-h">{{ src.heading }}</span>
            </li>
          </ul>
        </div>
      </div>
    </div>

    <!-- 状态条 -->
    <div v-if="statusText && !answer" class="tl-status">
      <span class="tl-spinner"></span>{{ statusText }}
    </div>

    <!-- 最终答复（流式） -->
    <!-- AI 生成内容必须标注（UX 规范 [高] AI Interaction｜Disclaimer）：
         用户有权知道这段文字是模型写的，以及事实性信息该看哪里。 -->
    <div v-if="answer" class="tl-answer-wrap">
      <div class="tl-answer-head">
        <span class="tl-answer-badge">AI 生成</span>
        <span class="tl-answer-hint">
          文字由大模型撰写，可能不准确；事实性信息请以上方工具调用与知识来源为准
        </span>
      </div>
      <!-- 用 v-html 渲染「轻度 Markdown」：模型爱写 **加粗**，
           纯文本渲染会露出星号（线上出现过 "1. **儿子多大？**"）。
           安全性由 formatAnswer 保证：**先转义 HTML 实体再打标签**，
           所以模型输出里的 <script> 之类会变成字面量。 -->
      <div class="tl-answer" v-html="formattedAnswer"></div>
    </div>

    <!-- 错误 -->
    <div v-if="error" class="tl-error">
      <span>{{ error }}</span>
      <button type="button" class="tl-retry" @click="$emit('retry')">重试</button>
    </div>

    <!-- 兜底说明：走的是确定性流程时要告诉用户 -->
    <div v-if="fallbackNote" class="tl-fallback">{{ fallbackNote }}</div>
  </div>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  steps: { type: Array, default: () => [] },
  answer: { type: String, default: '' },
  statusText: { type: String, default: '' },
  error: { type: String, default: '' },
  doneReason: { type: String, default: '' },
})
defineEmits(['retry'])

/**
 * 把模型输出的「轻度 Markdown」转成安全 HTML。
 *
 * 模型习惯用 `**加粗**`（线上真出现过 `1. **儿子多大？** ...`），
 * 直接纯文本渲染会露出星号，很难看。
 *
 * ## 为什么不上 markdown 库
 * 完整渲染器会引入 `##`/表格/链接/图片等一大堆能力，全是 XSS 面；
 * 我们只需要加粗和行内代码。这里**先转义再打标签**：
 * `&`/`<`/`>` 被替换成实体之后，模型输出里的任何标签都只是文本，
 * 后续插入的 `<strong>` 才是唯一能被浏览器解析的标签。
 *
 * 换行不处理——CSS 的 `white-space: pre-wrap` 已经保留了。
 */
const escapeHtml = (s) =>
  String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')

/** 行内标记：**加粗** / `代码`。只在这一层打标签，外层仍然安全。 */
const inlineMarkup = (escaped) => {
  let out = escaped
  out = out.replace(/\*\*([^*\n]+)\*\*/g, '<strong>$1</strong>')
  out = out.replace(/`([^`\n]+)`/g, '<code>$1</code>')
  out = out.replace(/\*\*/g, '')
  return out
}

/**
 * 把模型的答复渲染成有层次的 HTML。
 *
 * ## 为什么不能只做行内加粗
 *
 * 线上真实输出长这样（`Day 2` 和 1/2/3/4 全挤在一起）：
 *   `…很适合慢节奏。 **Day 2（西线）** - 09:00 沧浪亭 … 1. 天气: … 2. 闭馆日: …`
 * 只把 `**` 换成 <strong> 之后仍然是一大坨——**用户读不下去**。
 *
 * 所以按行识别结构，输出语义标签，由 CSS 给排版：
 *   `Day N（…）`      → <h4 class="ans-day">   每天独立成段
 *   `- …`             → <ul><li>              每个停靠点一行
 *   `1. …`            → <ol><li>              每条提醒一行
 *   `【…】` / 短行冒号 → <h5 class="ans-sub">    小标题
 *   空行               → 段落间隔
 * 其余为普通段落。
 *
 * 注意：标签是我们自己拼的，正文已经过 escapeHtml —— XSS 面为零。
 */
const formatAnswer = (raw) => {
  if (!raw) return ''

  // ---- 预处理：模型常把结构挤在一行里，先拆开 ----
  let text = String(raw).replace(/\r\n/g, '\n')

  // ① `…了。Day 1（园林核心区）` —— Day 标题必须独立成行
  text = text.replace(/([^\n])\s*(?=(?:\*\*)?Day\s*\d+\s*[（(])/g, '$1\n')

  // ② `…好逛。 2. 闭馆日：…` —— 把同一行里第 2 条以后的编号项拆到新行
  //    只处理"行内出现 2 个以上编号项"的行，避免误伤 `3.5 小时`（用 (?!\d) 排除小数）
  text = text
    .split('\n')
    .map((line) => {
      const marks = line.match(/\d{1,2}\.(?!\d)\s*(?=\S)/g) || []
      if (marks.length < 2) return line
      // 在每个 `N.` 前断行（保留第一个）
      let count = 0
      return line.replace(/(\s|。|；)(?=\d{1,2}\.(?!\d)\s*\S)/g, (m) => {
        count += 1
        return count === 0 ? m : `${m}\n`
      })
    })
    .join('\n')

  const lines = text.split('\n')

  // ---- 行内装饰：时间用等宽数字，箭头淡化 ----
  const decorate = (escaped) => {
    let out = inlineMarkup(escaped)
    // 时间 09:00 → 品牌色等宽数字
    out = out.replace(/(?<!\d)(\d{1,2}:\d{2})(?!\d)/g,
                      '<span class="ans-time">$1</span>')
    // 箭头淡化
    out = out.replace(/\s*→\s*/g, '<span class="ans-arrow">→</span>')
    // 行首「标签：」加粗（≤6 字，避免把整句加粗）
    out = out.replace(/^([\u4e00-\u9fa5A-Za-z]{1,6})：/, '<strong>$1</strong>：')
    return out
  }

  const html = []
  let listType = null

  const closeList = () => {
    if (listType) {
      html.push(`</${listType}>`)
      listType = null
    }
  }
  const openList = (t) => {
    if (listType !== t) {
      closeList()
      html.push(`<${t}>`)
      listType = t
    }
  }

  for (const rawLine of lines) {
    const e = escapeHtml(rawLine.trim())
    if (!e.trim()) {
      closeList()
      html.push('<div class="ans-gap"></div>')
      continue
    }

    // Day N（…）→ 天标题
    if (/^(?:#{1,3}\s*)?(?:\*\*)?\s*Day\s*\d+/.test(e)) {
      closeList()
      const title = e.replace(/^#{1,3}\s*/, '').replace(/\*\*/g, '')
      html.push(`<h4 class="ans-day">${decorate(title)}</h4>`)
      continue
    }

    // 【…】/「…」 或 以冒号结尾的短行 → 小标题
    if (/^【.+】$/.test(e) || /^「.+」$/.test(e) ||
        (e.length <= 14 && /[：:]$/.test(e))) {
      closeList()
      html.push(`<h5 class="ans-sub">${decorate(e.replace(/^【|】$/g, ''))}</h5>`)
      continue
    }

    // 无序列表
    const ul = e.match(/^[-•·]\s*(.+)$/)
    if (ul) {
      openList('ul')
      html.push(`<li>${decorate(ul[1])}</li>`)
      continue
    }

    // 有序列表
    const ol = e.match(/^(\d+)[.、)]\s*(.+)$/)
    if (ol) {
      openList('ol')
      html.push(`<li>${decorate(ol[2])}</li>`)
      continue
    }

    closeList()
    html.push(`<p>${decorate(e)}</p>`)
  }
  closeList()
  return html.join('')
}

const formattedAnswer = computed(() => formatAnswer(props.answer))

// 工具名的中文说明。**不要编造**：没列到的就原样显示英文名，
// 让用户自己判断，比显示一个猜的中文更诚实。
const TOOL_LABELS = {
  resolve_city: '确认城市',
  get_city_intro: '了解城市',
  get_weather: '查询天气',
  search_poi: '搜索地点',
  get_poi_detail: '查看地点详情',
  plan_itinerary: '生成行程',
  filter_candidates: '筛选候选',
  score_poi: '评估地点',
  optimize_route: '优化路线',
  schedule_day: '安排当日',
  search_knowledge: '检索知识库',
  estimate_intercity: '估算城际',
  recall_history: '回忆历史行程',
  recall_preferences: '读取偏好画像',
}
const toolLabel = (name) => TOOL_LABELS[name] || name || '调用工具'

// 「需要登录」是权限门，不是工具坏了。
// 后端用独立的 code（needs_login / NEEDS_LOGIN），这里据此单独着色，
// 否则用户会看到一个红点以为系统出故障。
const isGate = (step) => {
  const c = String(step.code || '').toLowerCase()
  return !step.ok && (c.includes('need') || c.includes('login') || c.includes('auth'))
}

const dotClass = (step) => {
  if (step.pending) return 'pending'
  if (step.ok) return 'ok'
  return isGate(step) ? 'gate' : 'bad'
}

const toggle = (step) => {
  step.expanded = !step.expanded
}

const pretty = (obj) => {
  try {
    return JSON.stringify(obj ?? {}, null, 2)
  } catch {
    return String(obj)
  }
}

// 走兜底时明确告诉用户当前是"标准流程"而不是智能体
const fallbackNote = computed(() => {
  const map = {
    tools_disabled: '本次使用标准规划流程（智能体模式已关闭）',
    llm_error: '智能体暂时不可用，已改用标准规划流程，结果仍然完整',
    max_rounds: '本次处理步骤较多，已改用标准规划流程完成',
    max_tokens: '本次对话较长，已改用标准规划流程完成',
    max_seconds: '本次处理超时，已改用标准规划流程完成',
    repeat_loop: '检测到重复调用，已改用标准规划流程完成',
    fallback: '已使用标准规划流程完成',
  }
  return map[props.doneReason] || ''
})
</script>

<style scoped>
/* 只用原生 CSS：移动端（Vant）与桌面端（Element Plus）都能直接复用，
   不引入任何 UI 库依赖 —— 沿用 InspirationCards.vue 的做法。 */
.agent-timeline { font-size: 13px; color: var(--app-text); line-height: 1.7; }

.tl-steps { border-left: 2px solid var(--app-border); margin: 4px 0 12px 6px; padding-left: 14px; }
.tl-step { position: relative; margin-bottom: 10px; }

.tl-dot {
  display: inline-block; width: 8px; height: 8px; border-radius: 50%;
  background: var(--app-text-tertiary); margin-right: 8px; vertical-align: middle; flex: 0 0 auto;
}
.tl-dot.thought { background: var(--app-text-secondary); }
.tl-dot.ok { background: var(--app-success); }
.tl-dot.bad { background: var(--app-danger); }
.tl-dot.pending { background: var(--app-warning); animation: tl-pulse 1.1s ease-in-out infinite; }
.tl-dot.knowledge { background: var(--app-brand); }
@keyframes tl-pulse { 0%,100% { opacity: 1 } 50% { opacity: .35 } }

.tl-thought { color: var(--app-text-secondary); font-size: 12.5px; }
.tl-thought-text { vertical-align: middle; }

.tl-tool { background: var(--app-bg-subtle); border: 1px solid var(--app-border); border-radius: 8px; }
.tl-tool-head {
  display: flex; align-items: center; gap: 6px; padding: 7px 10px;
  cursor: pointer; user-select: none; flex-wrap: wrap;
}
.tl-tool-name { font-weight: 600; color: var(--app-text); }
.tl-tool-raw { color: var(--app-text-tertiary); font-size: 11px; }
.tl-tool-status { margin-left: auto; font-size: 12px; color: var(--app-text-secondary); }
.tl-tool-status.ok { color: var(--app-success); }
.tl-tool-status.bad { color: var(--app-danger); }
.tl-tool-status.pending { color: var(--app-warning); }
.tl-tool-ms { font-size: 11px; color: var(--app-text-tertiary); }
.tl-caret { color: var(--app-text-tertiary); transition: transform .15s; }
.tl-caret.open { transform: rotate(90deg); }

.tl-tool-body { padding: 4px 10px 10px; border-top: 1px dashed var(--app-border); }
.tl-kv { margin-top: 6px; }
.tl-k { display: block; font-size: 11.5px; color: var(--app-text-tertiary); }
.tl-v { color: var(--app-text); }
.tl-v.bad { color: var(--app-danger); }
.tl-code {
  margin: 2px 0 0; padding: 8px; background: var(--app-surface-muted); border-radius: 6px;
  font-size: 11.5px; color: var(--app-text-secondary); white-space: pre-wrap; word-break: break-all;
  max-height: 180px; overflow: auto; font-family: ui-monospace, Menlo, Consolas, monospace;
}

.tl-knowledge { background: var(--app-brand-soft); border: 1px solid var(--app-brand-soft); border-radius: 8px; padding: 8px 10px; }
.tl-knowledge-head { display: flex; align-items: center; gap: 6px; font-weight: 600; color: var(--app-text); }
.tl-badge {
  margin-left: auto; font-size: 11px; font-weight: 400;
  color: var(--app-text-secondary); background: var(--app-surface); border: 1px solid var(--app-brand-soft);
  border-radius: 10px; padding: 1px 7px;
}
.tl-sources { margin: 6px 0 0; padding-left: 18px; color: var(--app-text-secondary); }
.tl-src-title { color: var(--app-text); }
.tl-src-h { color: var(--app-text-tertiary); font-size: 11.5px; margin-left: 6px; }
.tl-empty { margin-top: 6px; color: var(--app-text-tertiary); font-size: 12px; }

.tl-status { display: flex; align-items: center; gap: 8px; color: var(--app-text-secondary); font-size: 12.5px; padding: 4px 0; }
.tl-spinner {
  width: 12px; height: 12px; border: 2px solid var(--app-hairline); border-top-color: var(--app-brand);
  border-radius: 50%; animation: tl-spin .8s linear infinite;
}
@keyframes tl-spin { to { transform: rotate(360deg) } }

.tl-answer { color: var(--app-text); font-size: 13.5px; }
.tl-error {
  display: flex; align-items: center; gap: 10px; margin-top: 8px;
  color: var(--app-danger); background: var(--app-danger-soft); border: 1px solid var(--app-danger-soft);
  border-radius: 8px; padding: 8px 10px; font-size: 12.5px;
}
.tl-retry {
  margin-left: auto; border: 1px solid var(--app-danger-soft); background: var(--app-surface); color: var(--app-danger);
  border-radius: 6px; padding: 2px 10px; cursor: pointer; font-size: 12px;
}
.tl-fallback {
  margin-top: 8px; font-size: 12px; color: var(--app-warning);
  background: var(--app-warning-soft); border: 1px solid var(--app-warning-soft); border-radius: 8px; padding: 6px 10px;
}

/* AI 答复标注 */
.tl-answer-wrap { margin-top: 4px; }
.tl-answer-head {
  display: flex; align-items: center; gap: 8px; flex-wrap: wrap;
  margin-bottom: 6px; padding: 5px 8px;
  background: var(--app-warning-soft); border-radius: var(--app-radius-xs);
}
.tl-answer-badge {
  font-size: 11px; font-weight: 600; color: var(--app-warning);
  background: var(--app-surface); border: 1px solid var(--app-warning); border-radius: 999px;
  padding: 1px 7px; flex: 0 0 auto;
}
.tl-answer-hint { font-size: 11.5px; color: var(--app-warning); line-height: 1.5; }

/* 「需要登录」：中性提示色，不用红色（不是故障） */
.tl-dot.gate { background: var(--app-warning); }
.tl-tool-status.gate { color: var(--app-warning); }
/* 轻度 Markdown 渲染后的排版 */
.tl-answer strong {
  font-weight: 600;
  color: var(--app-text);
}
.tl-answer code {
  font-family: var(--app-font-mono);
  font-size: 0.92em;
  padding: 1px 4px;
  border-radius: 4px;
  background: var(--app-surface-muted);
  color: var(--app-text-secondary);
}

/* ==================== 答复正文排版（品牌化） ====================
   让大段答复有**字号 / 字色 / 字体**三级层次：
     天标题   衬线 17px 深墨绿
     小标题   11px 品牌色 宽字距
     正文     14px 深墨 行高 1.85
     时间     等宽数字 品牌色（扫读先命中"几点"）
     箭头     三级灰
*/
.tl-answer {
  font-size: 14px;
  line-height: 1.85;
  color: var(--app-text);
  letter-spacing: 0.005em;
}
.tl-answer p { margin: 0 0 12px; max-width: 62ch; }
.tl-answer p:last-child { margin-bottom: 0; }
.tl-answer .ans-gap { height: 12px; }

.tl-answer h4.ans-day {
  margin: 22px 0 10px;
  padding-left: 12px;
  border-left: 3px solid var(--app-brand);
  font-family: var(--app-font-serif);
  font-size: 17px;
  font-weight: 600;
  line-height: 1.45;
  letter-spacing: 0.01em;
  color: var(--app-brand-hover);
}
.tl-answer h4.ans-day:first-child { margin-top: 2px; }

.tl-answer h5.ans-sub {
  margin: 20px 0 8px;
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.18em;
  color: var(--app-brand);
}

.tl-answer ul { margin: 0 0 12px; padding-left: 2px; list-style: none; }
.tl-answer ul li {
  position: relative; padding-left: 16px; margin-bottom: 8px;
  line-height: 1.8; color: var(--app-text);
}
.tl-answer ul li::before {
  content: ''; position: absolute; left: 0; top: 11px;
  width: 5px; height: 5px; border-radius: 50%;
  background: var(--app-brand); opacity: 0.45;
}

.tl-answer ol {
  margin: 0 0 12px; padding-left: 0; list-style: none;
  counter-reset: ans-step;
}
.tl-answer ol li {
  position: relative; padding-left: 28px; margin-bottom: 10px;
  line-height: 1.8; color: var(--app-text);
  counter-increment: ans-step;
}
.tl-answer ol li::before {
  content: counter(ans-step);
  position: absolute; left: 0; top: 4px;
  width: 19px; height: 19px; border-radius: 50%;
  background: var(--app-brand-soft); color: var(--app-brand-hover);
  font-size: 11px; font-weight: 700;
  font-family: var(--app-font-num);
  font-variant-numeric: tabular-nums;
  display: flex; align-items: center; justify-content: center;
  line-height: 1;
}

.tl-answer .ans-time {
  font-family: var(--app-font-num);
  font-variant-numeric: tabular-nums;
  font-size: 0.94em; font-weight: 500;
  color: var(--app-brand);
  letter-spacing: 0.01em;
}
.tl-answer .ans-arrow {
  color: var(--app-text-tertiary);
  opacity: 0.7; margin: 0 1px; font-size: 0.95em;
}
.tl-answer strong { font-weight: 600; color: var(--app-text); }

@media (min-width: 1024px) {
  .tl-answer { font-size: 14.5px; }
  .tl-answer p { max-width: 68ch; }
}
</style>
