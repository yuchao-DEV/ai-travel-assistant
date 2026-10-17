/**
 * 高德地图 JS API 2.0 加载器
 * - Web端 Key
 * - 安全密钥 securityJsCode（2021.12 后申请的 Key 必须配置）
 * - AMap.Visual 可视化插件
 *
 * **密钥不写死在代码里**（本仓库是公开的）。运行时从 Vite 环境变量读取：
 *
 *   本地开发：复制 .env.example 为 .env.local 并填写（.env.local 已被 git 忽略）
 *   构建部署：在 CI / 构建环境注入同名环境变量
 *
 * 只有 `VITE_` 前缀的变量会被 Vite 暴露给前端代码——这是 Vite 的安全设计，
 * 用来避免把后端密钥误打进前端产物。
 */

/** Web 端 JS Key */
const AMAP_KEY = import.meta.env.VITE_AMAP_KEY || ''
/** 安全密钥（必须在 JS API 脚本加载前注入 window._AMapSecurityConfig） */
const AMAP_SECURITY_CODE = import.meta.env.VITE_AMAP_SECURITY_CODE || ''
/** 数据可视化平台项目 ID（可选；留空则跳过可视化图层，基础地图与路线照常显示） */
export const AMAP_VISUAL_ID = import.meta.env.VITE_AMAP_VISUAL_ID || ''

let loadPromise = null

/**
 * 动态加载高德 JS API（全局只加载一次）
 * @returns {Promise<typeof AMap>}
 */
export function loadAmap() {
  if (window.AMap) return Promise.resolve(window.AMap)
  if (loadPromise) return loadPromise

  // 缺 Key 时给出**明确可操作**的错误，而不是让地图静默白屏
  if (!AMAP_KEY) {
    return Promise.reject(new Error(
      '未配置高德 Web端(JS API) Key。'
      + '请把 fronted/travel/.env.example 复制为 .env.local，并填写 VITE_AMAP_KEY 与 VITE_AMAP_SECURITY_CODE。'
    ))
  }

  loadPromise = new Promise((resolve, reject) => {
    // 安全密钥配置必须在脚本加载之前设置；未配置时不要注入空对象，
    // 否则高德会以"安全密钥无效"为由拒绝加载
    if (AMAP_SECURITY_CODE) {
      window._AMapSecurityConfig = {
        securityJsCode: AMAP_SECURITY_CODE,
      }
    }

    const script = document.createElement('script')
    script.type = 'text/javascript'
    script.async = true
    script.src = `https://webapi.amap.com/maps?v=2.0&key=${AMAP_KEY}&plugin=AMap.Visual`
    script.onload = () => {
      if (window.AMap) resolve(window.AMap)
      else {
        loadPromise = null
        reject(new Error('AMap loaded but global AMap missing'))
      }
    }
    script.onerror = (e) => {
      loadPromise = null
      reject(e)
    }
    document.head.appendChild(script)
  })

  return loadPromise
}
