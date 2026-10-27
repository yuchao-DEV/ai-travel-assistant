#!/usr/bin/env node
/**
 * 前端静态检查：抓「引用了不存在的标识符」这一类构建期不会报错的错误。
 *
 * ## 为什么需要它
 *
 * 项目是纯 JS（没有 TypeScript），打包器对 `<script setup>` 里的自由变量
 * 一律当作全局引用，**构建通过、运行时才 ReferenceError**。
 * 如果这行还落在 `try` 之前，错误会被静默吞掉，界面表现为"点了没反应"。
 *
 * 真实踩过的三个例子：
 *   - `pc/views/Home.vue`     用了 `importImageName`，但桌面端没声明
 *     → 点「选择图片」选完图毫无反应（按钮不变、无请求、无提示）
 *   - `views/Timeline.vue`    用了 `savedToAccount`，但移动端没声明
 *     → 整页显示「网络异常」
 *   - 模板里绑定了不存在的处理函数 → 点击无任何反馈
 *
 * ## 检查什么
 *
 * ① `xxx.value` —— ref/computed 误用未声明变量（高频、且必错）
 * ② 模板里调用的函数 —— `@click="foo()"` 里 `foo` 必须声明
 *
 * ## 不做的事
 *
 * 不做完整类型检查（那要上 vue-tsc）。这里只针对"会静默失败"的那一类，
 * 宁可漏报也不要误报——误报会让人直接关掉这个检查。
 *
 * 用法：
 *   node scripts/check-refs.mjs          # 检查 src/，有问题退出码 1
 *   node scripts/check-refs.mjs --json   # 机器可读输出
 */
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join, relative, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..')
const SRC = join(ROOT, 'src')

/** 运行时/语言内置，不算项目标识符 */
const GLOBALS = new Set([
  'window', 'document', 'console', 'Math', 'JSON', 'Object', 'Array', 'Number',
  'String', 'Boolean', 'Date', 'Promise', 'Set', 'Map', 'WeakMap', 'Reflect',
  'location', 'history', 'sessionStorage', 'localStorage', 'navigator',
  'performance', 'setTimeout', 'clearTimeout', 'setInterval', 'clearInterval',
  'requestAnimationFrame', 'cancelAnimationFrame', 'Intl', 'URL', 'URLSearchParams',
  'FormData', 'File', 'FileReader', 'Blob', 'DataTransfer', 'fetch', 'Error',
  'RegExp', 'Proxy', 'Symbol', 'structuredClone', 'queueMicrotask',
  'encodeURIComponent', 'decodeURIComponent', 'parseInt', 'parseFloat', 'isNaN',
  'undefined', 'null', 'true', 'false', 'this', 'arguments', 'await', 'async',
  'if', 'else', 'return', 'typeof', 'instanceof', 'new', 'delete', 'void', 'in', 'of',
  // Vue 模板内置
  '$event', '$refs', '$attrs', '$slots', '$emit', '$props',
])

function walk(dir) {
  const out = []
  for (const name of readdirSync(dir)) {
    const p = join(dir, name)
    if (statSync(p).isDirectory()) out.push(...walk(p))
    else if (/\.(vue|js)$/.test(name)) out.push(p)
  }
  return out
}

/** 去掉注释与字符串，避免误判（保留代码结构） */
function stripNoise(code) {
  return code
    .replace(/\/\*[\s\S]*?\*\//g, ' ')
    .replace(/(^|[^:])\/\/[^\n]*/g, '$1 ')
    .replace(/`(?:\\[\s\S]|[^`\\])*`/g, '``')
    .replace(/'(?:\\[\s\S]|[^'\\])*'/g, "''")
    .replace(/"(?:\\[\s\S]|[^"\\])*"/g, '""')
}

/** 收集模板里"局部声明"的标识符：v-for 循环变量、插槽作用域、defineProps 的 prop 名 */
function templateLocals(tpl, script) {
  const locals = new Set()

  // v-for="(a, b) in list" / v-for="item in list" / v-for="(v, k, i) of obj"
  for (const m of tpl.matchAll(/v-for\s*=\s*"([^"]*)"/g)) {
    const expr = m[1]
    const inIdx = expr.search(/\s+(?:in|of)\s+/)
    if (inIdx < 0) continue
    const head = expr.slice(0, inIdx).trim().replace(/^\(|\)$/g, '')
    for (const part of head.split(',')) {
      const name = part.trim().split(/\s*[:=]/)[0].trim()
      if (/^[A-Za-z_$][\w$]*$/.test(name)) locals.add(name)
    }
  }

  // 插槽作用域：v-slot:name="slotProps" / #default="{ item, index }"
  for (const m of tpl.matchAll(/(?:v-slot[\w.:-]*|#[\w.-]+)\s*=\s*"([^"]*)"/g)) {
    const expr = m[1].trim()
    if (/^[A-Za-z_$][\w$]*$/.test(expr)) {
      locals.add(expr)
    } else {
      for (const inner of expr.matchAll(/([A-Za-z_$][\w$]*)\s*(?=[,}:]|$)/g)) {
        locals.add(inner[1])
      }
    }
  }

  // defineProps 的键名（props 在模板里可直接用）
  const propsBlock = script.match(/defineProps\s*\(\s*[{[]([\s\S]*?)[}\]]\s*\)/)
  if (propsBlock) {
    for (const m of propsBlock[1].matchAll(/(?:^|,)\s*([A-Za-z_$][\w$]*)\s*(?=[:,])/g)) {
      locals.add(m[1])
    }
    for (const m of propsBlock[1].matchAll(/["']([A-Za-z_$][\w$]*)["']/g)) {
      locals.add(m[1])
    }
  }
  // const props = defineProps(...) 之后用 props.xxx 的写法
  const propsVar = script.match(/\b(?:const|let)\s+([A-Za-z_$][\w$]*)\s*=\s*defineProps/)
  if (propsVar) locals.add(propsVar[1])

  return locals
}
function isDeclared(name, script) {
  const n = name.replace(/[$]/g, '\\$&')
  const patterns = [
    new RegExp(`\\b(?:const|let|var)\\s+${n}\\b`),
    new RegExp(`\\b(?:const|let|var)\\s*[{[]\\s*[^}\\]]*\\b${n}\\b`),
    new RegExp(`\\bfunction\\s+${n}\\b`),
    new RegExp(`\\bclass\\s+${n}\\b`),
    new RegExp(`\\bimport\\s+${n}\\b`),
    new RegExp(`\\bimport\\s*{[^}]*\\b${n}\\b`),
    new RegExp(`\\bimport\\s+\\*\\s+as\\s+${n}\\b`),
    // 函数参数（形如 (name) 或 , name,）
    new RegExp(`[(,]\\s*${n}\\s*[,)]`),
  ]
  return patterns.some((re) => re.test(script))
}

const problems = []

for (const file of walk(SRC)) {
  const raw = readFileSync(file, 'utf8')
  const rel = relative(SRC, file).replace(/\\/g, '/')

  const scriptMatch = raw.match(/<script[^>]*>([\s\S]*?)<\/script>/)
  const script = scriptMatch ? stripNoise(scriptMatch[1]) : stripNoise(raw)

  // ---------- ① xxx.value ----------
  for (const m of script.matchAll(/\b([A-Za-z_$][\w$]*)\.value\b/g)) {
    const name = m[1]
    if (GLOBALS.has(name) || name === 'props') continue
    if (!isDeclared(name, script)) {
      problems.push({
        file: rel,
        kind: 'undeclared-ref',
        name,
        hint: `\`${name}.value\` 但脚本里没有声明 ${name}`,
      })
    }
  }

  // ---------- ①b 脚本里用了未声明的"常用对象" ----------
  //
  // 真实踩过：`pc/views/AgentHistory.vue` 里写了 `router.push(...)`，
  // 但既没 `import { useRouter }` 也没 `const router = useRouter()` ——
  // 构建不报错，点按钮时 ReferenceError，界面毫无反应。
  //
  // 这里只盯高风险对象（路由/状态），并且只在"整个文件都没声明过"时报告，
  // 避免大面积误报。
  {
    const WATCHED = new Set(['router', 'route', 'store', 'sessionId', 'agentTripToken'])
    for (const name of WATCHED) {
      const used = new RegExp(`(^|[^.\\w$])${name}\\s*\\.`).test(script)
      // .value 用法已由 ① 覆盖；这里补"当对象用"的情况（router.push 等）
      if (!used) continue
      if (isDeclared(name, script)) continue
      problems.push({
        file: rel,
        kind: 'undeclared-script-object',
        name,
        hint: `脚本里用了 \`${name}.…\`，但本文件没有声明它（忘了 import 或 useRouter()？）`,
      })
    }
  }

  // ---------- ② 模板里引用的函数/处理函数 ----------
  const templateMatch = raw.match(/<template>([\s\S]*)<\/template>/)
  if (templateMatch) {
    // **必须用原始模板**：stripNoise 会把属性值的引号一起替换掉，
    // 导致 v-for="c in cities" 变成 v-for=""，循环变量就提取不到了。
    const tplRaw = templateMatch[1]
    // v-for 循环变量 / 插槽作用域 / defineProps 的 prop 名，在模板里可直接用
    const locals = templateLocals(tplRaw, script)
    // 只取属性值与插值里的表达式，避免把标签名/文案当代码
    const exprs = []
    for (const m of tplRaw.matchAll(/(?:@[\w.:-]+|:[\w.-]+|v-if|v-else-if|v-show|v-for|v-model[.\w:-]*)\s*=\s*"([^"]*)"/g)) {
      exprs.push(m[1])
    }
    for (const m of tplRaw.matchAll(/\{\{([^}]*)\}\}/g)) exprs.push(m[1])

    const seen = new Set()
    const report = (name) => {
      if (GLOBALS.has(name) || seen.has(name)) return
      seen.add(name)
      if (!isDeclared(name, script) && !locals.has(name)) {
        problems.push({
          file: rel,
          kind: 'undeclared-template-ref',
          name,
          hint: `模板引用了 \`${name}\`，但脚本里没有声明`,
        })
      }
    }

    for (const expr of exprs) {
      const e = stripNoise(expr).trim()
      // (a) 纯标识符：@click="doSomething"  —— 漏声明时点击毫无反应
      if (/^[A-Za-z_$][\w$]*$/.test(e)) {
        report(e)
        continue
      }
      // (b) 以标识符开头的调用：@click="foo(1)"
      const callHead = e.match(/^([A-Za-z_$][\w$]*)\s*\(/)
      if (callHead) report(callHead[1])
      // (c) 表达式内部的所有裸函数调用（排除 obj.fn()）
      for (const m of e.matchAll(/(^|[^.\w$])([A-Za-z_$][\w$]*)\s*\(/g)) {
        report(m[2])
      }
    }

    // ---------- ③ 模板里写 xxx.value（ref 在模板中自动解包，多写 .value 必错） ----------
    const refNames = new Set()
    for (const m of script.matchAll(/\b(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:ref|computed|shallowRef|reactive)\s*\(/g)) {
      refNames.add(m[1])
    }
    const tplNoNoise = stripNoise(tplRaw)
    for (const m of tplNoNoise.matchAll(/\b([A-Za-z_$][\w$]*)\.value\b/g)) {
      if (refNames.has(m[1])) {
        problems.push({
          file: rel,
          kind: 'template-dot-value',
          name: m[1],
          hint: `模板里写了 \`${m[1]}.value\`；模板对 ref 自动解包，应直接写 \`${m[1]}\``,
        })
      }
    }
  }
}

const asJson = process.argv.includes('--json')
if (asJson) {
  console.log(JSON.stringify(problems, null, 2))
} else if (problems.length) {
  console.error(`\n✗ 发现 ${problems.length} 处未声明的引用（运行时会 ReferenceError）：\n`)
  for (const p of problems) {
    console.error(`  ${p.file}`)
    console.error(`    ${p.hint}\n`)
  }
  console.error('  提示：这类错误构建不会报错，但运行时会静默失败。')
  console.error('        如果是照抄另一端的代码，检查那端有的 ref/函数这边是否也声明了。\n')
} else {
  console.log('✓ check-refs: 未发现未声明的引用')
}

process.exit(problems.length ? 1 : 0)
