/**
 * MCP 工具元数据与展示数据归一化（纯函数，便于单测）。
 */

export const MCP_TOOL_NAMES = new Set([
  'search_prts',
  'get_stage_info',
  'get_stage_enemies',
  'get_enemy_info',
  'list_items',
  'get_item_info',
  'operator_artwork',
])

export const MCP_TOOL_PREFIX = 'mcp__'

export function normalizeMcpToolName(name) {
  if (typeof name === 'string' && name.startsWith(MCP_TOOL_PREFIX)) {
    return name.slice(MCP_TOOL_PREFIX.length)
  }
  return name
}

export const TOOL_ICONS = {
  arknights_rag_search: '📚',
  arknights_graphrag_search: '🕸️',
  web_search: '🌐',
  arknights_structured_query: '📊',
  arknights_stage_waves: '🌊',
  search_prts: '🔎',
  get_stage_info: '🗺️',
  get_stage_enemies: '⚔️',
  get_enemy_info: '👾',
  list_items: '📦',
  get_item_info: '🧪',
  operator_artwork: '🖼️',
}

export const TOOL_DISPLAY_NAMES = {
  arknights_rag_search: '知识库检索',
  arknights_graphrag_search: '图谱查询',
  web_search: '网络搜索',
  arknights_structured_query: '结构化查询',
  arknights_stage_waves: '出怪顺序',
  search_prts: 'PRTS 搜索',
  get_stage_info: '关卡详情',
  get_stage_enemies: '关卡出怪',
  get_enemy_info: '敌人详情',
  list_items: '物品列表',
  get_item_info: '物品详情',
  operator_artwork: '干员立绘',
}

export function isMcpTool(name) {
  return MCP_TOOL_NAMES.has(normalizeMcpToolName(name))
}

/**
 * 只认自有属性：避免 'constructor' / 'toString' 这类键命中原型链上的函数。
 */
function lookup(map, key) {
  return typeof key === 'string' && Object.prototype.hasOwnProperty.call(map, key)
    ? map[key]
    : undefined
}

/**
 * JSON.stringify 的异常兜底：循环引用、BigInt 等无法序列化时返回可读字符串，
 * 而不是让整个工具结果渲染抛异常。
 */
function safeStringify(value, indent) {
  try {
    return JSON.stringify(value, null, indent)
  } catch {
    try {
      return String(value)
    } catch {
      return ''
    }
  }
}

export function getToolIcon(name) {
  return lookup(TOOL_ICONS, normalizeMcpToolName(name)) || '🔧'
}

export function getToolDisplayName(name) {
  const normalized = normalizeMcpToolName(name)
  const mapped = lookup(TOOL_DISPLAY_NAMES, normalized)
  if (mapped) return mapped
  // 未命中映射表时回退到工具名；非字符串（undefined/null/对象）收敛为可读文本，
  // 否则界面会渲染出 "undefined" 或 "[object Object]"
  if (typeof normalized === 'string') return normalized
  if (typeof normalized === 'number') return String(normalized)
  return ''
}

export function summarizeMcpToolArgs(toolName, args) {
  if (!args || typeof args !== 'object') return ''
  const normalizedName = normalizeMcpToolName(toolName)
  switch (normalizedName) {
    case 'search_prts':
      return `搜索: "${args.query || ''}"`
    case 'get_stage_info':
      return `关卡: ${args.stage_id || ''}`
    case 'get_stage_enemies':
      return `出怪: ${args.stage_id || ''}`
    case 'get_enemy_info':
      return `敌人: ${args.name || ''}${args.stage_id ? ` @ ${args.stage_id}` : ''}`
    case 'list_items':
      return `物品: ${args.category || '全部'}`
    case 'get_item_info':
      return `物品: ${args.name || ''}`
    case 'operator_artwork':
      return `${args.operator_name || ''} ${args.action === 'get' ? '获取立绘' : '立绘列表'}`
    default: {
      // 截断处补省略号，避免展示出被切断的无效 JSON
      const json = safeStringify(args)
      return json.length > 80 ? `${json.slice(0, 80)}…` : json
    }
  }
}

export function normalizeMcpDisplay(display) {
  if (!display || typeof display !== 'object') {
    return { text: '', table: null, json: '', images: [] }
  }

  // 调用方会直接用 img.data_url 作为 href/src，缺 data_url 的非法元素会产生坏图/空链接
  const images = Array.isArray(display.images)
    ? display.images.filter((img) => img && typeof img === 'object' && typeof img.data_url === 'string' && img.data_url)
    : []
  const structured = display.structured
  const isRowObject = (item) => !!item && typeof item === 'object'
  let rows = null

  if (Array.isArray(structured) && structured.length > 0 && structured.every(isRowObject)) {
    rows = structured
  } else if (structured && typeof structured === 'object' && Array.isArray(structured.rows)) {
    // rows 里的 null/基本类型要先过滤：Object.keys(null) 会抛 TypeError 打断整个展示层
    const validRows = structured.rows.filter(isRowObject)
    rows = validRows.length > 0 ? validRows : null
  }

  let table = null
  if (rows && rows.length > 0) {
    const columnSet = []
    // 列扫描范围与输出行保持一致（都是前 50 行），否则只在第 21~50 行出现的列会丢
    for (const row of rows.slice(0, 50)) {
      for (const key of Object.keys(row)) {
        if (!columnSet.includes(key)) columnSet.push(key)
      }
    }
    table = { columns: columnSet.slice(0, 8), rows: rows.slice(0, 50) }
  }

  let json = ''
  if (structured !== null && structured !== undefined) {
    json = safeStringify(structured, 2)
  }

  return { text: display.text || '', table, json, images }
}
