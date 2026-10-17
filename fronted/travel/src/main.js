import { createApp } from 'vue'
import Vant from 'vant'
import 'vant/lib/index.css'
// 设计令牌必须在组件库样式**之后**导入，才能覆盖 --van-* 变量
import './styles/tokens.css'
import router from './router'
import App from './App.vue'

const app = createApp(App)
app.use(Vant)
app.use(router)
app.mount('#app')
