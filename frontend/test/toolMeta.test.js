/**
 * Tests for frontend/src/utils/toolMeta.js
 * Usage: cd frontend && npx vitest run test/toolMeta.test.js
 */
import { describe, it, expect } from 'vitest'
import {
  MCP_TOOL_NAMES,
  isMcpTool,
  getToolIcon,
  getToolDisplayName,
  summarizeMcpToolArgs,
  normalizeMcpDisplay,
} from '../src/utils/toolMeta.js'

describe('MCP tool metadata', () => {
  it('recognizes the seven allowlisted MCP tools', () => {
    expect(MCP_TOOL_NAMES.size).toBe(7)
    expect(isMcpTool('get_stage_enemies')).toBe(true)
    expect(isMcpTool('operator_artwork')).toBe(true)
    expect(isMcpTool('arknights_rag_search')).toBe(false)
  })

  it('resolves MCP display names and icons', () => {
    expect(getToolDisplayName('get_stage_enemies')).toBe('关卡出怪')
    expect(getToolDisplayName('operator_artwork')).toBe('干员立绘')
    expect(getToolIcon('get_item_info')).toBe('🧪')
    expect(getToolIcon('unknown_tool')).toBe('🔧')
  })

  it('summarizes MCP tool args', () => {
    expect(summarizeMcpToolArgs('search_prts', { query: '霜星' })).toBe('搜索: "霜星"')
    expect(summarizeMcpToolArgs('get_stage_enemies', { stage_id: 'main_01-07' })).toBe('出怪: main_01-07')
    expect(summarizeMcpToolArgs('get_enemy_info', { name: '霜星', stage_id: 'main_01-07' })).toBe('敌人: 霜星 @ main_01-07')
    expect(summarizeMcpToolArgs('get_item_info', { name: '源岩' })).toBe('物品: 源岩')
    expect(summarizeMcpToolArgs('operator_artwork', { operator_name: '阿米娅', action: 'list' })).toBe('阿米娅 立绘列表')
  })
})

describe('normalizeMcpDisplay', () => {
  it('normalizes a rows payload into a table view', () => {
    const view = normalizeMcpDisplay({
      text: 'ok',
      structured: { rows: [{ name: '霜星', hp: 1000 }], columns: ['name', 'hp'] },
      images: [],
    })
    expect(view.text).toBe('ok')
    expect(view.table.columns).toEqual(['name', 'hp'])
    expect(view.table.rows).toEqual([{ name: '霜星', hp: 1000 }])
  })

  it('normalizes an array payload into a table view', () => {
    const view = normalizeMcpDisplay({ structured: [{ a: 1 }, { a: 2 }] })
    expect(view.table.columns).toEqual(['a'])
    expect(view.table.rows.length).toBe(2)
  })

  it('falls back to JSON for non-table structures', () => {
    const view = normalizeMcpDisplay({ structured: { nested: { deep: true } } })
    expect(view.table).toBeNull()
    expect(view.json).toContain('deep')
  })

  it('keeps images untouched', () => {
    const view = normalizeMcpDisplay({
      structured: null,
      images: [{ data_url: 'data:image/png;base64,AAAA', mime: 'image/png', label: 'x' }],
    })
    expect(view.images.length).toBe(1)
  })
})
