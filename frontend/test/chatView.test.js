/**
 * Tests for frontend/src/views/ChatView.vue —— 「引用链接」渲染与点击的安全回归测试（T02）。
 *
 * 防什么回归：
 *   renderMessageWithSources() 里的引用链接（<span class="source-link" ...>）是在
 *   renderMarkdown() 的 DOMPurify 消毒「之后」才拼进 HTML 的，处于消毒覆盖范围之外。
 *   而 api.js 的 escapeHtml 走 textContent→innerHTML，只转义 & < >，**不转义双引号**，
 *   一旦属性值（data-collection / data-url / title）里出现一个 " 就能闭合属性，
 *   注入 onmouseover/onerror 之类的内联事件（属性注入 XSS）。
 *   修复 = ChatView 本地 escapeAttr（补齐 & " ' < >）+ 拼装后二次 DOMPurify 消毒
 *          + handleSourceClick 的 http(s) 协议白名单。
 *
 * 测试路径：挂载真实组件（真实 v-html / 真实 renderMarkdown / 真实 DOMPurify /
 * 真实 escapeHtml），断言真实 DOM，而不是复刻一份实现来测。
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { mount } from '@vue/test-utils'

// ============================================================
// store 替身：ChatView 在 setup 期就会调用这些 store
// ============================================================
const mocks = vi.hoisted(() => ({
  sessionStore: {
    currentSession: null,
    currentSessionId: 's1',
    sessions: {},
    backendSessionIds: {},
    finalizePendingToolCalls: vi.fn(),
  },
  quickQuestionsStore: {
    hasInitialized: true, // 置 true 跳过 mount 时的快捷问题请求
    isLoading: false,
    quickActions: [],
    markAsInitialized: vi.fn(),
    setLoading: vi.fn(),
    setQuickActions: vi.fn(),
  },
  settingsStore: { currentModel: '' },
  sourceDrawerStore: { open: vi.fn() },
  toastStore: { show: vi.fn() },
}))

vi.mock('../src/stores/sessions', () => ({ useSessionStore: () => mocks.sessionStore }))
vi.mock('../src/stores/quickQuestions', () => ({ useQuickQuestionsStore: () => mocks.quickQuestionsStore }))
vi.mock('../src/stores/settings', () => ({ useSettingsStore: () => mocks.settingsStore }))
vi.mock('../src/stores/sourceDrawer', () => ({ useSourceDrawerStore: () => mocks.sourceDrawerStore }))
vi.mock('../src/stores/toast', () => ({ useToastStore: () => mocks.toastStore }))

// 注意：**不 mock** frontend/src/api.js —— 被测的 escapeHtml 必须是真的，
// 否则「escapeHtml 不转义双引号」这个前提就被替身掩盖了。
import ChatView from '../src/views/ChatView.vue'

/** 挂载 ChatView，并让它渲染一条带引用来源的 assistant 消息 */
function mountAssistant(content, sources) {
  mocks.sessionStore.currentSession = {
    id: 's1',
    name: '测试会话',
    messages: [{ role: 'assistant', content, sources, timestamp: Date.now() }],
  }
  return mount(ChatView)
}

/**
 * 断言渲染结果里没有任何内联事件属性（on*）。
 * 不能用 `html().not.toContain('onmouseover')`：转义后的属性值里仍会以纯文本形式
 * 出现 "onmouseover" 字样，那种断言会误报。
 */
function expectNoInlineEventAttrs(wrapper) {
  const offenders = []
  wrapper.element.querySelectorAll('*').forEach(el => {
    for (const attr of Array.from(el.attributes)) {
      if (/^on/i.test(attr.name)) offenders.push(`${el.tagName}[${attr.name}]`)
    }
  })
  expect(offenders).toEqual([])
}

beforeEach(() => {
  vi.clearAllMocks()
  // jsdom 没有 ResizeObserver；ChatView 的 onMounted 会用到
  global.ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  }
})

afterEach(() => {
  vi.restoreAllMocks()
  delete window.__xss
})

// ============================================================
// 1. 属性注入：恶意引号不能闭合属性
// ============================================================

describe('引用链接属性注入（T02）', () => {
  it('网络来源 title 中的双引号不会闭合属性、注入 onmouseover', () => {
    // title 完全来自网络搜索结果（Tavily / DuckDuckGo），不可信
    const hostileTitle = '正常标题" onmouseover="window.__xss=1'
    const wrapper = mountAssistant('参考网页 (web)', [
      { source_id: 'web', url: 'https://example.com/a', title: hostileTitle },
    ])

    const link = wrapper.find('.source-link-web')
    expect(link.exists()).toBe(true)
    // 属性值必须原样完整保留（没有被截断成 "正常标题"）
    expect(link.attributes('title')).toBe(hostileTitle)
    // 不能出现注入出来的内联事件属性
    expect(link.attributes('onmouseover')).toBeUndefined()
    expectNoInlineEventAttrs(wrapper)
    expect(window.__xss).toBeUndefined()
    wrapper.unmount()
  })

  it('chunk 引用的 collection 含引号/尖括号时不会闭合属性（data-chunk-id / data-collection 完整保留）', () => {
    // collection 来自 answer_done 的 sources（后端数据），chunk_id 来自 LLM 输出，
    // 两者都可能带引号；这里用「属性值必须原样往返」来同时挡住转义不足与过度转义。
    const hostileCollection = `op" onmouseover="window.__xss=1" data-x="' <b>&</b>`
    const wrapper = mountAssistant('引用结论 (operators_0103_02)', [
      { chunk_id: 'operators_0103_02', collection: hostileCollection },
    ])

    const link = wrapper.find('.source-link')
    expect(link.exists()).toBe(true)
    expect(link.attributes('data-chunk-id')).toBe('operators_0103_02')
    expect(link.attributes('data-collection')).toBe(hostileCollection)
    expect(link.attributes('onmouseover')).toBeUndefined()
    expectNoInlineEventAttrs(wrapper)
    wrapper.unmount()
  })

  it('data-url 含引号时不会闭合属性', () => {
    const hostileUrl = 'https://example.com/?q=" onmouseover="window.__xss=1'
    const wrapper = mountAssistant('参考网页 (web)', [
      { source_id: 'web', url: hostileUrl, title: '网页来源' },
    ])

    const link = wrapper.find('.source-link-web')
    expect(link.exists()).toBe(true)
    expect(link.attributes('data-url')).toBe(hostileUrl)
    expectNoInlineEventAttrs(wrapper)
    wrapper.unmount()
  })
})

// ============================================================
// 2. 正常引用：不能因为过度转义而破坏功能
// ============================================================

describe('正常引用链接的属性值保持完整', () => {
  it('chunk 引用与网络引用的 data-* / title 原样可用', () => {
    const url = 'https://prts.wiki/w/能天使?q=1&lang=zh'
    const wrapper = mountAssistant('结论见 (operators_0103_02) 与 (web)', [
      { chunk_id: 'operators_0103_02', collection: 'operators' },
      { source_id: 'web', url, title: 'PRTS 能天使' },
    ])

    const links = wrapper.findAll('.source-link')
    expect(links.length).toBe(2)

    const chunkLink = links[0]
    expect(chunkLink.classes()).toContain('source-link')
    expect(chunkLink.classes()).not.toContain('source-link-web')
    expect(chunkLink.attributes('data-chunk-id')).toBe('operators_0103_02')
    // collection 取自 sources，而不是按前缀猜出来的
    expect(chunkLink.attributes('data-collection')).toBe('operators')

    const webLink = links[1]
    expect(webLink.classes()).toContain('source-link-web')
    // & 必须往返一致（转义成 &amp; 后再解析回来仍是 &），否则点击时 URL 会坏掉
    expect(webLink.attributes('data-url')).toBe(url)
    expect(webLink.attributes('title')).toBe('PRTS 能天使')
    wrapper.unmount()
  })

  it('没有 title 的网络来源回退到默认文案', () => {
    const wrapper = mountAssistant('参考网页 (web)', [
      { source_id: 'web', url: 'https://example.com/a' },
    ])
    expect(wrapper.find('.source-link-web').attributes('title')).toBe('网页来源')
    wrapper.unmount()
  })
})

// ============================================================
// 3. handleSourceClick 的协议白名单
// ============================================================

describe('handleSourceClick 协议白名单', () => {
  it('拒绝 javascript:/data:/vbscript: 及其大小写变体，放行 http(s)', async () => {
    const urls = [
      'javascript:alert(1)',
      'JavaScript:alert(1)',
      'data:text/html,alert(1)',
      'vbscript:msgbox(1)',
      'https://prts.wiki/w/能天使',
    ]
    const openSpy = vi.spyOn(window, 'open').mockImplementation(() => null)
    const wrapper = mountAssistant(
      '来源 (web) (web) (web) (web) (web)',
      urls.map(url => ({ source_id: 'web', url }))
    )

    const links = wrapper.findAll('.source-link-web')
    expect(links.length).toBe(urls.length)
    // 先确认恶意 URL 真的被渲染到了元素上（否则下面的断言会因「属性被消毒掉」而假通过）
    urls.forEach((url, i) => {
      expect(links[i].attributes('data-url')).toBe(url)
    })

    for (let i = 0; i < urls.length - 1; i++) {
      await links[i].trigger('click')
      expect(openSpy).not.toHaveBeenCalled()
    }

    await links[urls.length - 1].trigger('click')
    expect(openSpy).toHaveBeenCalledTimes(1)
    expect(openSpy).toHaveBeenCalledWith(urls[urls.length - 1], '_blank', 'noopener,noreferrer')
    wrapper.unmount()
  })

  it('前导空格/大写 HTTPS 形式：白名单按原始属性值判断，不做宽松匹配', async () => {
    // 说明：DOMPurify 会把属性值首尾空白 trim 掉，所以「前导空格的 javascript:」
    // 无法通过渲染路径到达 DOM；这里直接给渲染出的元素设属性，专门验证
    // handleSourceClick 自身的白名单判断（不 trim、不做宽松前缀匹配）。
    const openSpy = vi.spyOn(window, 'open').mockImplementation(() => null)
    const wrapper = mountAssistant('来源 (web)', [
      { source_id: 'web', url: 'https://example.com/a' },
    ])
    const link = wrapper.find('.source-link-web')

    for (const hostile of [' javascript:alert(1)', '\tjavascript:alert(1)', 'JAVASCRIPT:alert(1)', 'ftp://example.com/x']) {
      link.element.setAttribute('data-url', hostile)
      await link.trigger('click')
      expect(openSpy).not.toHaveBeenCalled()
    }

    // 大写 HTTPS 仍然放行（白名单是大小写不敏感的 http/https）
    link.element.setAttribute('data-url', 'HTTPS://example.com/b')
    await link.trigger('click')
    expect(openSpy).toHaveBeenCalledWith('HTTPS://example.com/b', '_blank', 'noopener,noreferrer')
    wrapper.unmount()
  })

  it('点击 chunk 引用打开原文抽屉', async () => {
    const wrapper = mountAssistant('结论见 (operators_0103_02)', [
      { chunk_id: 'operators_0103_02', collection: 'operators' },
    ])
    await wrapper.find('.source-link').trigger('click')
    expect(mocks.sourceDrawerStore.open).toHaveBeenCalledWith({
      chunk_id: 'operators_0103_02',
      collection: 'operators',
    })
    wrapper.unmount()
  })
})
