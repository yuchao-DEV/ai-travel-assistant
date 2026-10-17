/**
 * 桌面端入口（Element Plus）。
 *
 * 与移动端（/src/main.js，Vant）并存于同一个 Vite 工程：
 * - 访问 /      → 移动端 H5
 * - 访问 /pc/   → 桌面端 Web
 * 两端共用 src/api、src/utils、src/composables，避免接口与鉴权逻辑走偏。
 */
import { createApp } from 'vue'

import ElementPlus from 'element-plus'
import 'element-plus/dist/index.css'
// 设计令牌必须在组件库样式**之后**导入，才能覆盖 --el-* 变量
import '../styles/tokens.css'
import zhCn from 'element-plus/es/locale/lang/zh-cn'

import PcApp from './App.vue'
import pcRouter from './router'
import './styles.css'

const app = createApp(PcApp)

// 按需在组件里 import 图标，这里不全局注册，避免把整套图标打进包里
app.use(ElementPlus, { locale: zhCn })
app.use(pcRouter)
app.mount('#pc-app')
