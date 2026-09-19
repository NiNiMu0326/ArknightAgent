/* ================================================
   ARKNIGHTS AGENT - API CLIENT (Vue version)
   ================================================ */

// API base URL - using relative path for Vite proxy
// For development, requests are proxied to http://localhost:8888
// For production, set VITE_API_BASE environment variable
const API_BASE = import.meta.env.VITE_API_BASE || ''

/**
 * 获取认证请求头
 * @param {boolean} withJson - 是否添加 Content-Type: application/json
 * @returns {Object} 请求头对象
 */
function getAuthHeaders(withJson = false) {
  const token = localStorage.getItem('arknights_rag_token')
  const headers = {}
  if (token) {
    headers['Authorization'] = `Bearer ${token}`
  }
  if (withJson) {
    headers['Content-Type'] = 'application/json'
  }
  return headers
}

/**
 * 从失败响应中提取可读的错误信息。
 *
 * 统一替换各接口里 `await response.json()` 的样板：
 * - 响应不是 JSON（网关 HTML 错误页、空响应体）时回退到 fallback，不再抛 SyntaxError；
 * - FastAPI 的 422 校验错误 detail 是数组/对象，序列化后再展示，避免出现 [object Object]；
 * - 不回退到跳转/刷新（401 只抛出错误，由调用方决定如何提示），避免刷新循环。
 *
 * @param {Response} response - fetch 返回的响应
 * @param {string} fallback - 无法提取服务端信息时使用的兜底文案
 * @returns {Promise<string>}
 */
export async function extractErrorDetail(response, fallback) {
  let detail
  try {
    const body = await response.json()
    detail = body?.detail ?? body?.message ?? body?.error
  } catch {
    detail = undefined // 非 JSON 响应体：走下面的兜底文案
  }
  if (typeof detail === 'string' && detail.trim()) return detail
  // 空串/纯空白串视为「无可用信息」，直接走兜底文案（否则 JSON.stringify('') 会返回字面量 ""）
  if (detail !== undefined && detail !== null && !(typeof detail === 'string' && !detail.trim())) {
    try {
      return JSON.stringify(detail)
    } catch {
      // 无法序列化时退回兜底文案
    }
  }
  const status = response?.status
  return status ? `${fallback}（HTTP ${status}）` : fallback
}

export const api = {
  // ===== Auth APIs =====

  async register(account, username, password) {
    const response = await fetch(`${API_BASE}/auth/register`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ account, username, password })
    })
    if (!response.ok) {
      throw new Error(await extractErrorDetail(response, '注册失败'))
    }
    return response.json()
  },

  async login(account, password) {
    const response = await fetch(`${API_BASE}/auth/login`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ account, password })
    })
    if (!response.ok) {
      throw new Error(await extractErrorDetail(response, '登录失败'))
    }
    return response.json()
  },

  async getMe() {
    const response = await fetch(`${API_BASE}/auth/me`, {
      headers: getAuthHeaders()
    })
    if (!response.ok) throw new Error('未登录')
    return response.json()
  },

  async changePassword(oldPassword, newPassword) {
    const response = await fetch(`${API_BASE}/auth/change-password`, {
      method: 'POST',
      headers: getAuthHeaders(true),
      body: JSON.stringify({ old_password: oldPassword, new_password: newPassword })
    })
    if (!response.ok) {
      throw new Error(await extractErrorDetail(response, '修改密码失败'))
    }
    return response.json()
  },

  // ===== Conversation APIs =====

  async listConversations() {
    const response = await fetch(`${API_BASE}/conversations`, {
      headers: getAuthHeaders()
    })
    if (!response.ok) throw new Error('获取会话列表失败')
    return response.json()
  },

  async getConversationMessages(sessionId) {
    const response = await fetch(`${API_BASE}/conversations/${encodeURIComponent(sessionId)}/messages`, {
      headers: getAuthHeaders()
    })
    if (!response.ok) throw new Error('获取消息失败')
    return response.json()
  },

  async syncConversations(conversations) {
    const response = await fetch(`${API_BASE}/conversations/sync`, {
      method: 'POST',
      headers: getAuthHeaders(true),
      body: JSON.stringify({ conversations })
    })
    if (!response.ok) throw new Error('同步会话失败')
    return response.json()
  },

  async deleteConversation(sessionId) {
    const response = await fetch(`${API_BASE}/conversations/${encodeURIComponent(sessionId)}`, {
      method: 'DELETE',
      headers: getAuthHeaders()
    })
    if (!response.ok) throw new Error('删除会话失败')
    return response.json()
  },

  async renameConversation(sessionId, name) {
    const response = await fetch(`${API_BASE}/conversations/${encodeURIComponent(sessionId)}/rename`, {
      method: 'PUT',
      headers: getAuthHeaders(true),
      body: JSON.stringify({ name })
    })
    if (!response.ok) throw new Error('重命名失败')
    return response.json()
  },

  async getStatus() {
    const response = await fetch(`${API_BASE}/status`)
    if (!response.ok) throw new Error('获取服务状态失败')
    return response.json()
  },

  async getChunks(collection = 'operators') {
    const response = await fetch(`${API_BASE}/chunks/${encodeURIComponent(collection)}`)
    if (!response.ok) throw new Error('获取切块列表失败')
    return response.json()
  },

  async getChunk(collection, filename) {
    const response = await fetch(`${API_BASE}/chunks/${encodeURIComponent(collection)}/${encodeURIComponent(filename)}`)
    if (!response.ok) throw new Error('获取切块详情失败')
    return response.json()
  },

  async getGraphData() {
    const response = await fetch(`${API_BASE}/knowledge-graph`)
    if (!response.ok) throw new Error('获取图谱数据失败')
    return response.json()
  },

  async getStats() {
    const response = await fetch(`${API_BASE}/stats`)
    if (!response.ok) throw new Error('获取统计数据失败')
    return response.json()
  },

  async getOperators() {
    const response = await fetch(`${API_BASE}/operators`)
    if (!response.ok) {
      throw new Error(await extractErrorDetail(response, 'Failed to get operators'))
    }
    return response.json()
  },

  async getCharacters() {
    const response = await fetch(`${API_BASE}/characters`)
    if (!response.ok) {
      throw new Error(await extractErrorDetail(response, 'Failed to get characters'))
    }
    return response.json()
  },

  async getStories() {
    const response = await fetch(`${API_BASE}/stories`)
    if (!response.ok) {
      throw new Error(await extractErrorDetail(response, 'Failed to get stories'))
    }
    return response.json()
  },

  async getTraces(page = 1, limit = 20, filters = {}) {
    const params = new URLSearchParams({ page, limit })
    if (filters.status) params.set('status', filters.status)
    if (filters.modelId) params.set('model_id', filters.modelId)
    if (filters.q) params.set('q', filters.q)
    const response = await fetch(`${API_BASE}/agent/traces?${params}`, {
      headers: getAuthHeaders(),
    })
    if (!response.ok) throw new Error('获取 trace 列表失败')
    return response.json()
  },

  async getTraceSummary() {
    const response = await fetch(`${API_BASE}/agent/traces/summary`, {
      headers: getAuthHeaders(),
    })
    if (!response.ok) throw new Error('获取 trace 统计失败')
    return response.json()
  },

  async getTraceDetail(traceId) {
    const response = await fetch(`${API_BASE}/agent/traces/${encodeURIComponent(traceId)}`, {
      headers: getAuthHeaders(),
    })
    if (!response.ok) throw new Error('获取 trace 详情失败')
    return response.json()
  },

  async deleteTraces(traceIds) {
    const response = await fetch(`${API_BASE}/agent/traces`, {
      method: 'DELETE',
      headers: getAuthHeaders(true),
      body: JSON.stringify({ trace_ids: traceIds }),
    })
    if (!response.ok) throw new Error('删除失败')
    return response.json()
  },

  /**
   * 导出 traces 为 JSON 文件 Blob。传 ids 数组导出选中项，不传导出全部。
   */
  async exportTraces(traceIds = null) {
    let response
    if (traceIds && traceIds.length > 0) {
      response = await fetch(`${API_BASE}/agent/traces/export`, {
        method: 'POST',
        headers: getAuthHeaders(true),
        body: JSON.stringify({ trace_ids: traceIds }),
      })
    } else {
      response = await fetch(`${API_BASE}/agent/traces/export`, {
        headers: getAuthHeaders(),
      })
    }
    if (!response.ok) throw new Error('导出失败')
    return response.blob()
  },

  async exportSingleTrace(traceId) {
    const response = await fetch(`${API_BASE}/agent/traces/${encodeURIComponent(traceId)}/export`, {
      headers: getAuthHeaders(),
    })
    if (!response.ok) throw new Error('导出失败')
    return response.blob()
  },

  // LangFuse traces (proxied)
  async getLangfuseTraces(page = 1, limit = 20) {
    const response = await fetch(`${API_BASE}/agent/traces/langfuse?page=${page}&limit=${limit}`, {
      headers: getAuthHeaders(),
    })
    if (!response.ok) throw new Error('获取 LangFuse trace 列表失败')
    return response.json()
  },

  async getLangfuseTraceDetail(traceId) {
    const response = await fetch(`${API_BASE}/agent/traces/langfuse/${encodeURIComponent(traceId)}`, {
      headers: getAuthHeaders(),
    })
    if (!response.ok) throw new Error('获取 LangFuse trace 详情失败')
    return response.json()
  },

  async getQuickQuestions(refresh = false) {
    const url = refresh ? `${API_BASE}/quick-questions?refresh=true` : `${API_BASE}/quick-questions`
    const response = await fetch(url)
    if (!response.ok) {
      throw new Error(await extractErrorDetail(response, 'Failed to get quick questions'))
    }
    return response.json()
  },

  // ===== Agent APIs =====

  async createAgentSession() {
    const response = await fetch(`${API_BASE}/agent/session`, {
      method: 'POST',
      headers: getAuthHeaders(true),
    })
    if (!response.ok) throw new Error('Failed to create session')
    return response.json()
  },

  async deleteAgentSession(sessionId) {
    const response = await fetch(`${API_BASE}/agent/session/${encodeURIComponent(sessionId)}`, {
      method: 'DELETE',
      headers: getAuthHeaders(),
    })
    if (!response.ok) throw new Error('Failed to delete session')
    return response.json()
  },

  async getModels() {
    const response = await fetch(`${API_BASE}/agent/models`, {
      headers: getAuthHeaders(),
    })
    if (!response.ok) throw new Error('Failed to get models')
    return response.json()
  },

  async getAgentSessionMessages(sessionId) {
    const response = await fetch(`${API_BASE}/agent/session/${encodeURIComponent(sessionId)}/messages`, {
      headers: getAuthHeaders(),
    })
    if (!response.ok) throw new Error('Failed to get messages')
    return response.json()
  },

  /**
   * Agent chat with SSE streaming.
   * @param {string} sessionId - Backend session ID
   * @param {string} message - User message
   * @param {function} onThinkingStart - Callback for thinking_start event
   * @param {function} onThinkingDelta - Callback for thinking_delta event
   * @param {function} onThinkingDone - Callback for thinking_done event (complete reasoning content)
   * @param {function} onToolCallsStart - Callback for tool_calls_start event
   * @param {function} onToolExecuting - Callback for tool_executing event
   * @param {function} onToolCallResult - Callback for tool_call_result event
   * @param {function} onAnswerDelta - Callback for answer_delta event
   * @param {function} onAnswerDone - Callback for answer_done event
   * @param {function} onError - Callback for error event
   * @param {AbortSignal} signal - AbortController signal
   * @returns {Promise<void>}
   */
  async agentChat({ sessionId, message, model, onNewSessionId, onThinkingStart, onThinkingDelta, onThinkingDone, onToolCallsStart, onToolExecuting, onToolCallResult, onAnswerDelta, onAnswerDone, onError, signal }) {
    const body = { session_id: sessionId, message }
    if (model) body.model = model

    const response = await fetch(`${API_BASE}/agent/chat`, {
      method: 'POST',
      headers: getAuthHeaders(true),
      body: JSON.stringify(body),
      signal,
    })

    // Check if server auto-created a new session (X-New-Session-Id header)
    const newSid = response.headers.get('X-New-Session-Id')
    if (newSid && newSid.trim()) {
      onNewSessionId?.(newSid.trim())
    }

    if (!response.ok) {
      throw new Error(await extractErrorDetail(response, 'Agent chat failed'))
    }

    // 204/无响应体时 body 为 null，直接 getReader() 会抛 TypeError
    if (!response.body) {
      throw new Error('响应体为空，无法读取流式数据')
    }

    const reader = response.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''
    // 后端在正常结束/出错时会发 answer_done/error 终态事件；
    // 若流在两者都未出现的情况下 EOF（网络断开、进程退出），按错误处理，
    // 让调用方保存 partial 并提示用户，而不是静默成功。
    let receivedTerminal = false
    const callbacks = {
      onNewSessionId,
      onThinkingStart,
      onThinkingDelta,
      onThinkingDone,
      onToolCallsStart,
      onToolExecuting,
      onToolCallResult,
      onAnswerDelta,
      onAnswerDone: (event) => { receivedTerminal = true; onAnswerDone?.(event) },
      onError: (event) => { receivedTerminal = true; onError?.(event) },
    }

    const parseLine = (line) => {
      const trimmed = line.trim()
      // 兼容标准 SSE `data:` 与后端 `data: `（带空格）两种格式
      if (!trimmed.startsWith('data:')) return
      const jsonStr = trimmed.slice(5).trim()
      if (!jsonStr) return

      try {
        const event = JSON.parse(jsonStr)
        _dispatchSSEEvent(event, callbacks)
      } catch (e) {
        console.warn('Failed to parse SSE event:', jsonStr, e)
      }
    }

    let streamEnded = false
    try {
      while (true) {
        const { done, value } = await reader.read()
        if (done) break

        buffer += decoder.decode(value, { stream: true })
        const lines = buffer.split('\n')
        buffer = lines.pop() || ''

        for (const line of lines) parseLine(line)
      }

      // Flush multi-byte UTF-8 sequences held by the decoder, then process the
      // remaining partial line (if any) after the stream ends.
      buffer += decoder.decode()
      if (buffer.trim()) parseLine(buffer)
      streamEnded = true
    } finally {
      // 回调抛异常或外部 abort 时读取循环会中途退出：取消未读完的流并释放 reader，
      // 否则连接与 ReadableStream 得不到及时释放（长时间停留页面会累积未释放的流）。
      if (!streamEnded) {
        try {
          await reader.cancel()
        } catch {
          // 流已关闭/已取消，忽略
        }
      }
      try {
        reader.releaseLock()
      } catch {
        // 仍有挂起的读取时 releaseLock 会抛错，忽略即可
      }
    }

    if (!receivedTerminal) {
      throw new Error('连接中断：响应流未正常结束，请重试')
    }
  }
}

function _dispatchSSEEvent(event, callbacks) {
  switch (event.type) {
    case 'session_renewed': callbacks.onNewSessionId?.(event.session_id); break
    case 'thinking_start': callbacks.onThinkingStart?.(event); break
    case 'thinking_delta': callbacks.onThinkingDelta?.(event); break
    case 'thinking_done': callbacks.onThinkingDone?.(event); break
    case 'tool_calls_start': callbacks.onToolCallsStart?.(event); break
    case 'tool_executing': callbacks.onToolExecuting?.(event); break
    case 'tool_call_result': callbacks.onToolCallResult?.(event); break
    case 'answer_delta': callbacks.onAnswerDelta?.(event); break
    case 'answer_done': callbacks.onAnswerDone?.(event); break
    case 'error': callbacks.onError?.(event); break
  }
}

export function formatTime(date) {
  return new Intl.DateTimeFormat('zh-CN', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit'
  }).format(date)
}

/**
 * 触发浏览器下载一个 Blob 文件
 */
export function downloadBlob(blob, filename) {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.rel = 'noopener'
  a.style.display = 'none'
  // <a> 需挂载到文档上，Safari 下未挂载的点击不会触发下载
  document.body.appendChild(a)
  a.click()
  a.remove()
  // 立即 revoke 会让部分浏览器（Firefox/Safari）中断下载，延迟释放对象 URL
  setTimeout(() => URL.revokeObjectURL(url), 60000)
}

export function debounce(fn, delay = 300) {
  let timeout
  return (...args) => {
    clearTimeout(timeout)
    timeout = setTimeout(() => fn(...args), delay)
  }
}

export function escapeHtml(str) {
  if (!str) return ''
  const div = document.createElement('div')
  div.textContent = str
  return div.innerHTML
}
