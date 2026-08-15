/* ================================================
   ARKNIGHTS RAG - API CLIENT (Vue version)
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

export const api = {
  // ===== Auth APIs =====

  async register(account, username, password) {
    const response = await fetch(`${API_BASE}/auth/register`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ account, username, password })
    })
    if (!response.ok) {
      const err = await response.json()
      throw new Error(err.detail || '注册失败')
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
      const err = await response.json()
      throw new Error(err.detail || '登录失败')
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
      const err = await response.json()
      throw new Error(err.detail || '修改密码失败')
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
    const response = await fetch(`${API_BASE}/conversations/${sessionId}/messages`, {
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
    const response = await fetch(`${API_BASE}/conversations/${sessionId}`, {
      method: 'DELETE',
      headers: getAuthHeaders()
    })
    if (!response.ok) throw new Error('删除会话失败')
    return response.json()
  },

  async renameConversation(sessionId, name) {
    const response = await fetch(`${API_BASE}/conversations/${sessionId}/rename`, {
      method: 'PUT',
      headers: getAuthHeaders(true),
      body: JSON.stringify({ name })
    })
    if (!response.ok) throw new Error('重命名失败')
    return response.json()
  },

  async getStatus() {
    const response = await fetch(`${API_BASE}/status`)
    return response.json()
  },

  async getChunks(collection = 'operators') {
    const response = await fetch(`${API_BASE}/chunks/${collection}`)
    return response.json()
  },

  async getChunk(collection, filename) {
    const response = await fetch(`${API_BASE}/chunks/${collection}/${filename}`)
    return response.json()
  },

  async getGraphData() {
    const response = await fetch(`${API_BASE}/knowledge-graph`)
    return response.json()
  },

  async getStats() {
    const response = await fetch(`${API_BASE}/stats`)
    return response.json()
  },

  async getOperators() {
    const response = await fetch(`${API_BASE}/operators`)
    if (!response.ok) {
      const err = await response.json()
      throw new Error(err.detail || 'Failed to get operators')
    }
    return response.json()
  },

  async getCharacters() {
    const response = await fetch(`${API_BASE}/characters`)
    if (!response.ok) {
      const err = await response.json()
      throw new Error(err.detail || 'Failed to get characters')
    }
    return response.json()
  },

  async getStories() {
    const response = await fetch(`${API_BASE}/stories`)
    if (!response.ok) {
      const err = await response.json()
      throw new Error(err.detail || 'Failed to get stories')
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
    const response = await fetch(`${API_BASE}/agent/traces/${traceId}`, {
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
    const response = await fetch(`${API_BASE}/agent/traces/${traceId}/export`, {
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
    return response.json()
  },

  async getLangfuseTraceDetail(traceId) {
    const response = await fetch(`${API_BASE}/agent/traces/langfuse/${traceId}`, {
      headers: getAuthHeaders(),
    })
    if (!response.ok) throw new Error('获取 LangFuse trace 详情失败')
    return response.json()
  },

  async getQuickQuestions(refresh = false) {
    const url = refresh ? `${API_BASE}/quick-questions?refresh=true` : `${API_BASE}/quick-questions`
    const response = await fetch(url)
    if (!response.ok) {
      const err = await response.json()
      throw new Error(err.detail || 'Failed to get quick questions')
    }
    return response.json()
  },

  // ===== AgenticRAG APIs =====

  async createAgentSession() {
    const response = await fetch(`${API_BASE}/agent/session`, {
      method: 'POST',
      headers: getAuthHeaders(true),
    })
    if (!response.ok) throw new Error('Failed to create session')
    return response.json()
  },

  async deleteAgentSession(sessionId) {
    const response = await fetch(`${API_BASE}/agent/session/${sessionId}`, {
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
    const response = await fetch(`${API_BASE}/agent/session/${sessionId}/messages`, {
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
      const err = await response.json().catch(() => ({ detail: 'Request failed' }))
      throw new Error(err.detail || 'Agent chat failed')
    }

    const reader = response.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''
    const callbacks = { onNewSessionId, onThinkingStart, onThinkingDelta, onThinkingDone, onToolCallsStart, onToolExecuting, onToolCallResult, onAnswerDelta, onAnswerDone, onError }

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
  a.click()
  URL.revokeObjectURL(url)
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

