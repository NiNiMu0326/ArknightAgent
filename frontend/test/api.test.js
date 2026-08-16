/**
 * Tests for frontend/src/api.js: auth headers, REST endpoints,
 * agentChat SSE stream parsing, and utility functions.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { api, debounce, escapeHtml, formatTime } from '../src/api.js'

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
