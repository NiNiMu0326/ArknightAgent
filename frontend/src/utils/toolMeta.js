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

export const TOOL_ICONS = {
  arknights_rag_search: '📚',
  arknights_graphrag_search: '🕸️',
  web_search: '🌐',
  arknights_structured_query: '📊',
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
  search_prts: 'PRTS 搜索',
  get_stage_info: '关卡详情',
  get_stage_enemies: '关卡出怪',
  get_enemy_info: '敌人详情',
  list_items: '物品列表',
  get_item_info: '物品详情',
  operator_artwork: '干员立绘',
}

export function isMcpTool(name) {
  return MCP_TOOL_NAMES.has(name)
}

export function getToolIcon(name) {
  return TOOL_ICONS[name] || '🔧'
}

export function getToolDisplayName(name) {
  return TOOL_DISPLAY_NAMES[name] || name
}

export function summarizeMcpToolArgs(toolName, args) {
  if (!args || typeof args !== 'object') return ''
  switch (toolName) {
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
    default:
      return JSON.stringify(args).substring(0, 80)
  }
}

export function normalizeMcpDisplay(display) {
  if (!display || typeof display !== 'object') {
    return { text: '', table: null, json: '', images: [] }
  }

  const images = Array.isArray(display.images) ? display.images : []
  const structured = display.structured
  let rows = null

  if (Array.isArray(structured) && structured.length > 0 &&
      structured.every((item) => item && typeof item === 'object')) {
    rows = structured
  } else if (structured && typeof structured === 'object' && Array.isArray(structured.rows)) {
    rows = structured.rows
  }

  let table = null
  if (rows && rows.length > 0) {
    const columnSet = []
    for (const row of rows.slice(0, 20)) {
      for (const key of Object.keys(row)) {
        if (!columnSet.includes(key)) columnSet.push(key)
      }
    }
    table = { columns: columnSet.slice(0, 8), rows: rows.slice(0, 50) }
  }

  let json = ''
  if (structured !== null && structured !== undefined) {
    json = JSON.stringify(structured, null, 2)
  }

  return { text: display.text || '', table, json, images }
}
