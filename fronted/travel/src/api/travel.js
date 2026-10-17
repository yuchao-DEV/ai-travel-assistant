import request from '@/utils/request'

// 元数据：GET /api/meta —— 返回可选标签、profile 列表、高德状态
export function getMeta() {
  return request.get('/api/meta')
}

// 算法引擎行程规划：POST /api/plan
// 入参 PlanRequestDTO: { city, days, tags[], must_go[], avoid[], profile, ... }
// 注：budget 已随预算系统移除（执行规格 §0.1），后端不再接受该字段。
// 返回 ItineraryVO
export function getPlan(params) {
  return request.post('/api/plan', {
    city: params.city,
    days: Number(params.days),
    tags: params.tags || [],
    must_go: params.must_go || [],
    avoid: params.avoid || [],
    profile: params.profile || '经典',
  })
}

// 旧版 LLM 直调 getRecommend() 已随后端路由一并下线：
// 该接口让大模型直接编造整份行程且无法校验，与「算法决策、LLM 只负责表达」冲突。
// 需要行程请用 getPlan()（POST /api/plan）。

// 自然语言需求解析：POST /api/parse
// 入参 ParseRequestDTO: { text, defaults? }
// 出参 ParsedPlanVO: { request, inferred[], assumed[], notes[], source, raw_text }
// 只做抽取，不产生行程；解析出的 request 再交给 getPlan 由算法引擎规划
export function parsePlan(text, defaults) {
  return request.post('/api/parse', {
    text,
    defaults: defaults || undefined,
  })
}

// 导入线路/地点：POST /api/import/text
// 从一段文字里抽取地点，**逐个经高德反查验证**；未匹配的会明确返回，不会被静默丢弃
export function importText(text, city) {
  return request.post('/api/import/text', { text, city: city || undefined })
}

// 旅行灵感流：GET /api/inspiration?city=&limit=
// 刻意不调用大模型、不做路线计算，因此能在行程生成的等待期立刻返回内容。
// limit 取足够大，保证各分类都有卡片可翻（否则标签数字会大于实际可看到的张数）
export function getInspiration(city, limit = 120) {
  return request.get('/api/inspiration', { params: { city, limit } })
}

// 城际交通估算：GET /api/intercity?origin=&dest=
// 注意：班次数据（铁路/航班）未接入，返回的是真实地理编码 + 直线距离 + 驾车耗时估算
export function getIntercity(origin, dest) {
  return request.get('/api/intercity', { params: { origin, dest } })
}

// 行程路线缩略图：GET /api/map/thumbnail
// 后端代理高德静态地图（Key 不外泄），返回 PNG 的 blob URL；失败时返回 null
export async function getRouteThumbnail(days, width = 750, height = 420) {
  const points = []
  const daySizes = []
  for (const day of days || []) {
    const items = (day.items || []).filter((it) => it.lat && it.lng)
    if (!items.length) continue
    daySizes.push(items.length)
    for (const it of items) points.push(`${it.lat},${it.lng}`)
  }
  if (!points.length) return null
  try {
    const blob = await request.get('/api/map/thumbnail', {
      params: { points: points.join(';'), day_sizes: daySizes.join(','), width, height },
      responseType: 'blob',
      // 204 时 axios 走成功分支但 body 为空，需要在下方判空
      validateStatus: (s) => s === 200 || s === 204,
    })
    if (!blob || blob.size === 0) return null
    return URL.createObjectURL(blob)
  } catch (e) {
    console.warn('路线缩略图获取失败（不影响其他功能）', e)
    return null
  }
}
// 导入线路/地点（图片版）：POST /api/import/image
//
// 与 importText 是**同一条后端管线**，只是输入从"文字"换成"攻略图"：
//   图片 →（视觉模型）→ 城市 + 地点名 →（importer.import_text）→ 高德逐个校验
// 所以返回结构与 /api/import/text 完全一致，前端可以共用一套结果渲染。
export function importImage(file, city) {
  const fd = new FormData()
  fd.append('file', file)
  return request.post('/api/import/image', fd, {
    params: city ? { city } : undefined,
    headers: { 'Content-Type': 'multipart/form-data' },
    timeout: 180000,   // 识图 + 高德逐个校验，比文字导入慢得多
  })
}
