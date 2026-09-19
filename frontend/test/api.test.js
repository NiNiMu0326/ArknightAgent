/**
 * Tests for frontend/src/api.js: auth headers, REST endpoints,
 * agentChat SSE stream parsing, and utility functions.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { api, debounce, escapeHtml, extractErrorDetail, formatTime } from '../src/api.js'

function sseResponse(events, { ok = true, status = 200, headers = {}, jsonBody = {} } = {}) {
  const text = events.map(e => `data: ${JSON.stringify(e)}\n\n`).join('')
  const stream = new ReadableStream({
    start(controller) {
      controller.enqueue(new TextEncoder().encode(text))
      controller.close()
    }
  })
  return {
    ok,
    status,
    headers: new Headers(headers),
    json: async () => jsonBody,
    body: stream,
  }
}

function jsonResponse(data, { ok = true, status = 200 } = {}) {
  return { ok, status, headers: new Headers(), json: async () => data }
}

beforeEach(() => {
  localStorage.clear()
})

afterEach(() => {
  vi.restoreAllMocks()
})

// ============================================================
// Auth headers & basic REST endpoints
// ============================================================

describe('auth headers', () => {
  it('omits Authorization when no token stored', async () => {
    global.fetch = vi.fn().mockResolvedValue(jsonResponse({ id: 1 }))
    await api.getMe()
    const headers = fetch.mock.calls[0][1].headers
    expect(headers.Authorization).toBeUndefined()
  })

  it('includes Bearer token when stored', async () => {
    localStorage.setItem('arknights_rag_token', 'my-jwt')
    global.fetch = vi.fn().mockResolvedValue(jsonResponse({ id: 1 }))
    await api.getMe()
    const headers = fetch.mock.calls[0][1].headers
    expect(headers.Authorization).toBe('Bearer my-jwt')
  })
})

describe('REST endpoints', () => {
  it('login returns parsed json on success', async () => {
    global.fetch = vi.fn().mockResolvedValue(jsonResponse({ token: 't', user: { account: 'a' } }))
    const result = await api.login('acc', 'pw')
    expect(result.token).toBe('t')
    const [url, opts] = fetch.mock.calls[0]
    expect(url).toBe('/auth/login')
    expect(JSON.parse(opts.body)).toEqual({ account: 'acc', password: 'pw' })
  })

  it('login throws server detail on failure', async () => {
    global.fetch = vi.fn().mockResolvedValue(
      jsonResponse({ detail: '账号或密码错误' }, { ok: false, status: 401 })
    )
    await expect(api.login('a', 'b')).rejects.toThrow('账号或密码错误')
  })

  it('register throws server detail on failure', async () => {
    global.fetch = vi.fn().mockResolvedValue(
      jsonResponse({ detail: '账号已存在' }, { ok: false, status: 400 })
    )
    await expect(api.register('a', 'u', 'p')).rejects.toThrow('账号已存在')
  })

  it('deleteConversation throws on failure', async () => {
    global.fetch = vi.fn().mockResolvedValue(jsonResponse({}, { ok: false, status: 500 }))
    await expect(api.deleteConversation('sid')).rejects.toThrow('删除会话失败')
  })

  it('getQuickQuestions appends refresh param', async () => {
    global.fetch = vi.fn().mockResolvedValue(jsonResponse({ questions: [] }))
    await api.getQuickQuestions(true)
    expect(fetch.mock.calls[0][0]).toBe('/quick-questions?refresh=true')
    await api.getQuickQuestions()
    expect(fetch.mock.calls[1][0]).toBe('/quick-questions')
  })

  it('createAgentSession posts to /agent/session', async () => {
    global.fetch = vi.fn().mockResolvedValue(jsonResponse({ session_id: 's1' }))
    const result = await api.createAgentSession()
    expect(result.session_id).toBe('s1')
    expect(fetch.mock.calls[0][0]).toBe('/agent/session')
    expect(fetch.mock.calls[0][1].method).toBe('POST')
  })
})

// ============================================================
// agentChat SSE streaming
// ============================================================

describe('agentChat', () => {
  it('dispatches each SSE event type to its callback', async () => {
    const events = [
      { type: 'thinking_start', round: 1 },
      { type: 'thinking_delta', content: '思考中' },
      { type: 'thinking_done', reasoning_content: '完整思考' },
      { type: 'tool_calls_start', round: 1, tool_calls: [] },
      { type: 'tool_executing', tool_call_id: 'c1', tool_name: 'web_search' },
      { type: 'tool_call_result', tool_call_id: 'c1', result: [] },
      { type: 'answer_delta', delta: '答' },
      { type: 'answer_done', answer: '答案' },
    ]
    global.fetch = vi.fn().mockResolvedValue(sseResponse(events))

    const cb = {
      onThinkingStart: vi.fn(), onThinkingDelta: vi.fn(), onThinkingDone: vi.fn(),
      onToolCallsStart: vi.fn(), onToolExecuting: vi.fn(), onToolCallResult: vi.fn(),
      onAnswerDelta: vi.fn(), onAnswerDone: vi.fn(), onError: vi.fn(),
    }
    await api.agentChat({ sessionId: 's1', message: 'hi', ...cb })

    expect(cb.onThinkingStart).toHaveBeenCalledWith(events[0])
    expect(cb.onThinkingDelta).toHaveBeenCalledWith(events[1])
    expect(cb.onThinkingDone).toHaveBeenCalledWith(events[2])
    expect(cb.onToolCallsStart).toHaveBeenCalledWith(events[3])
    expect(cb.onToolExecuting).toHaveBeenCalledWith(events[4])
    expect(cb.onToolCallResult).toHaveBeenCalledWith(events[5])
    expect(cb.onAnswerDelta).toHaveBeenCalledWith(events[6])
    expect(cb.onAnswerDone).toHaveBeenCalledWith(events[7])
    expect(cb.onError).not.toHaveBeenCalled()
  })

  it('dispatches error events to onError', async () => {
    global.fetch = vi.fn().mockResolvedValue(sseResponse([{ type: 'error', message: '炸了' }]))
    const onError = vi.fn()
    await api.agentChat({ sessionId: 's1', message: 'hi', onError })
    expect(onError).toHaveBeenCalledWith({ type: 'error', message: '炸了' })
  })

  it('handles session_renewed event via onNewSessionId', async () => {
    global.fetch = vi.fn().mockResolvedValue(sseResponse([
      { type: 'session_renewed', session_id: 'new-sid' },
      { type: 'answer_done', answer: 'x' },
    ]))
    const onNewSessionId = vi.fn()
    await api.agentChat({ sessionId: 'old', message: 'hi', onNewSessionId })
    expect(onNewSessionId).toHaveBeenCalledWith('new-sid')
  })

  it('reads X-New-Session-Id response header', async () => {
    global.fetch = vi.fn().mockResolvedValue(
      sseResponse([{ type: 'answer_done', answer: 'x' }], { headers: { 'X-New-Session-Id': 'header-sid' } })
    )
    const onNewSessionId = vi.fn()
    await api.agentChat({ sessionId: 'old', message: 'hi', onNewSessionId })
    expect(onNewSessionId).toHaveBeenCalledWith('header-sid')
  })

  it('throws with server detail on HTTP error', async () => {
    global.fetch = vi.fn().mockResolvedValue(
      sseResponse([], { ok: false, status: 500, jsonBody: { detail: '会话已过期' } })
    )
    await expect(api.agentChat({ sessionId: 's', message: 'm' })).rejects.toThrow('会话已过期')
  })

  it('parses events split across network chunks', async () => {
    const full = `data: {"type":"answer_delta","delta":"你` +
      `好"}\n\ndata: {"type":"answer_done","answer":"你好"}\n\n`
    const bytes = new TextEncoder().encode(full)
    const mid = Math.floor(bytes.length / 2)
    const stream = new ReadableStream({
      start(controller) {
        controller.enqueue(bytes.slice(0, mid))
        controller.enqueue(bytes.slice(mid))
        controller.close()
      }
    })
    global.fetch = vi.fn().mockResolvedValue({
      ok: true, status: 200, headers: new Headers(), json: async () => ({}), body: stream,
    })
    const onAnswerDelta = vi.fn()
    const onAnswerDone = vi.fn()
    await api.agentChat({ sessionId: 's', message: 'm', onAnswerDelta, onAnswerDone })
    expect(onAnswerDelta).toHaveBeenCalledTimes(1)
    expect(onAnswerDone).toHaveBeenCalledWith({ type: 'answer_done', answer: '你好' })
  })

  it('skips malformed SSE lines without crashing', async () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
    const text = 'data: {broken json\n\ndata: {"type":"answer_done","answer":"ok"}\n\n'
    const stream = new ReadableStream({
      start(controller) {
        controller.enqueue(new TextEncoder().encode(text))
        controller.close()
      }
    })
    global.fetch = vi.fn().mockResolvedValue({
      ok: true, status: 200, headers: new Headers(), json: async () => ({}), body: stream,
    })
    const onAnswerDone = vi.fn()
    await api.agentChat({ sessionId: 's', message: 'm', onAnswerDone })
    expect(onAnswerDone).toHaveBeenCalledTimes(1)
    expect(warn).toHaveBeenCalled()
  })

  it('throws when stream ends without a terminal event', async () => {
    global.fetch = vi.fn().mockResolvedValue(
      sseResponse([{ type: 'answer_delta', delta: '还没说完' }])
    )
    const onAnswerDelta = vi.fn()
    await expect(
      api.agentChat({ sessionId: 's', message: 'm', onAnswerDelta })
    ).rejects.toThrow('响应流未正常结束')
    expect(onAnswerDelta).toHaveBeenCalled()
  })

  it('sends model in request body when provided', async () => {
    global.fetch = vi.fn().mockResolvedValue(sseResponse([{ type: 'answer_done', answer: 'x' }]))
    await api.agentChat({ sessionId: 's', message: 'm', model: 'deepseek-v4-flash' })
    const body = JSON.parse(fetch.mock.calls[0][1].body)
    expect(body).toEqual({ session_id: 's', message: 'm', model: 'deepseek-v4-flash' })
  })
})

// ============================================================
// Error extraction & non-2xx handling (T34)
// ============================================================

/** 非 JSON 响应体（网关 HTML 错误页 / 空响应体）：json() 抛 SyntaxError */
function nonJsonResponse(status = 502) {
  return {
    ok: false,
    status,
    headers: new Headers(),
    json: async () => { throw new SyntaxError('Unexpected token < in JSON') },
    text: async () => '<html>502 Bad Gateway</html>',
  }
}

describe('extractErrorDetail', () => {
  it('非 JSON 响应体回退到 fallback 并带上 HTTP 状态码，而不是抛 SyntaxError', async () => {
    // 防的回归：直接 await response.json() 会在网关 HTML 错误页上抛 SyntaxError，
    // 调用方只能看到 "Unexpected token <"，看不到状态码。
    await expect(extractErrorDetail(nonJsonResponse(502), '获取统计数据失败'))
      .resolves.toBe('获取统计数据失败（HTTP 502）')
  })

  it('FastAPI 的 detail 数组（422 校验错误）序列化成可读 JSON', async () => {
    // 防的回归：detail 是数组/对象时直接模板拼接会显示 [object Object]
    const detail = [{ loc: ['body', 'account'], msg: 'field required', type: 'value_error.missing' }]
    const body = { detail }
    await expect(extractErrorDetail(jsonResponse(body, { ok: false, status: 422 }), '注册失败'))
      .resolves.toBe(JSON.stringify(detail))
  })

  it('detail 是对象时同样给出可读信息', async () => {
    const detail = { code: 'rate_limited', retry_after: 30 }
    const out = await extractErrorDetail(jsonResponse({ detail }, { ok: false, status: 429 }), '登录失败')
    expect(out).toBe(JSON.stringify(detail))
    expect(out).not.toContain('[object Object]')
  })

  it('优先取 detail，其次 message / error', async () => {
    await expect(extractErrorDetail(jsonResponse({ detail: 'D' }, { ok: false, status: 400 }), 'X'))
      .resolves.toBe('D')
    await expect(extractErrorDetail(jsonResponse({ message: 'M' }, { ok: false, status: 400 }), 'X'))
      .resolves.toBe('M')
    await expect(extractErrorDetail(jsonResponse({ error: 'E' }, { ok: false, status: 400 }), 'X'))
      .resolves.toBe('E')
  })

  it('响应体里没有可用信息时回退到 fallback（带状态码）', async () => {
    await expect(extractErrorDetail(jsonResponse({}, { ok: false, status: 500 }), '同步会话失败'))
      .resolves.toBe('同步会话失败（HTTP 500）')
    await expect(extractErrorDetail(jsonResponse(null, { ok: false, status: 401 }), '未登录'))
      .resolves.toBe('未登录（HTTP 401）')
    // 没有 status 时至少给出 fallback 文案
    await expect(extractErrorDetail({ json: async () => ({}) }, '服务不可用'))
      .resolves.toBe('服务不可用')
  })
})

describe('非 2xx 响应必须抛错，不能静默返回错误体（T34）', () => {
  // 防的回归：这些接口原来直接 `return response.json()` 不校验 response.ok，
  // 401/500 时调用方拿到的是错误体（或难以理解的 JSON 异常），页面表现为静默失败。
  const cases = [
    ['getStatus', () => api.getStatus(), '获取服务状态失败'],
    ['getChunks', () => api.getChunks('operators'), '获取切块列表失败'],
    ['getChunk', () => api.getChunk('operators', 'a.json'), '获取切块详情失败'],
    ['getGraphData', () => api.getGraphData(), '获取图谱数据失败'],
    ['getStats', () => api.getStats(), '获取统计数据失败'],
    ['getTraces', () => api.getTraces(), '获取 trace 列表失败'],
    ['getTraceSummary', () => api.getTraceSummary(), '获取 trace 统计失败'],
    ['getTraceDetail', () => api.getTraceDetail('t1'), '获取 trace 详情失败'],
    ['getLangfuseTraces', () => api.getLangfuseTraces(), '获取 LangFuse trace 列表失败'],
    ['getLangfuseTraceDetail', () => api.getLangfuseTraceDetail('t1'), '获取 LangFuse trace 详情失败'],
    ['deleteTraces', () => api.deleteTraces(['t1']), '删除失败'],
  ]

  for (const [name, call, message] of cases) {
    it(`${name} 在 HTTP 500 时抛错且不解析响应体`, async () => {
      const json = vi.fn(async () => ({ detail: '服务器炸了' }))
      global.fetch = vi.fn().mockResolvedValue({
        ok: false, status: 500, headers: new Headers(), json, blob: vi.fn(),
      })
      await expect(call()).rejects.toThrow(message)
      expect(json).not.toHaveBeenCalled() // 没有把错误体当成正常返回值
    })
  }

  it('exportTraces / exportSingleTrace 在非 2xx 时抛错且不读 blob', async () => {
    const blob = vi.fn()
    global.fetch = vi.fn().mockResolvedValue({
      ok: false, status: 500, headers: new Headers(), json: vi.fn(), blob,
    })
    await expect(api.exportTraces()).rejects.toThrow('导出失败')
    await expect(api.exportTraces(['t1'])).rejects.toThrow('导出失败')
    await expect(api.exportSingleTrace('t1')).rejects.toThrow('导出失败')
    expect(blob).not.toHaveBeenCalled()
  })

  it('getTraces 正常路径仍组装分页与筛选参数', async () => {
    global.fetch = vi.fn().mockResolvedValue(jsonResponse({ traces: [], total: 0 }))
    const result = await api.getTraces(2, 50, { status: 'error', modelId: 'deepseek-v4-flash', q: '能天使' })
    expect(result).toEqual({ traces: [], total: 0 })
    const params = new URLSearchParams(fetch.mock.calls[0][0].split('?')[1])
    expect(params.get('page')).toBe('2')
    expect(params.get('limit')).toBe('50')
    expect(params.get('status')).toBe('error')
    expect(params.get('model_id')).toBe('deepseek-v4-flash')
    expect(params.get('q')).toBe('能天使')
  })
})

// ============================================================
// Utility functions
// ============================================================

describe('debounce', () => {
  it('delays execution and collapses rapid calls', () => {
    vi.useFakeTimers()
    const fn = vi.fn()
    const debounced = debounce(fn, 100)
    debounced('a')
    debounced('b')
    debounced('c')
    expect(fn).not.toHaveBeenCalled()
    vi.advanceTimersByTime(100)
    expect(fn).toHaveBeenCalledTimes(1)
    expect(fn).toHaveBeenCalledWith('c')
    vi.useRealTimers()
  })
})

describe('escapeHtml', () => {
  it('escapes script tags', () => {
    const out = escapeHtml('<script>alert("xss")</script>')
    expect(out).not.toContain('<script>')
    expect(out).toContain('&lt;script&gt;')
  })

  it('does NOT escape double/single quotes —— 因此不可用于 HTML 属性值', () => {
    // 固化现状（T02 的背景）：escapeHtml 走 textContent→innerHTML，只转义 & < >。
    // 把它的输出拼进属性值（如 data-collection="${escapeHtml(x)}"）时，
    // 一个 " 就能闭合属性并注入 onmouseover —— 它是"文本节点转义"，不是"属性转义"。
    // 属性场景请用 ChatView.vue 的 escapeAttr（覆盖 & " ' < >），见 test/chatView.test.js。
    const out = escapeHtml(`a"b'c`)
    expect(out).toBe(`a"b'c`)
    expect(out).not.toContain('&quot;')
    expect(out).not.toContain('&#39;')
  })

  it('returns empty string for falsy input', () => {
    expect(escapeHtml('')).toBe('')
    expect(escapeHtml(null)).toBe('')
    expect(escapeHtml(undefined)).toBe('')
  })
})

describe('formatTime', () => {
  it('formats a Date into HH:MM:SS string', () => {
    const result = formatTime(new Date(2025, 0, 1, 8, 5, 9))
    expect(result).toMatch(/08:05:09/)
  })
})
