import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { api } from '../api'
import { useAuthStore } from './auth'

export const useSessionStore = defineStore('sessions', () => {
  const sessions = ref({})
  const currentSessionId = ref(null)
  const lastActiveSessionId = ref(null)
  // Backend agent session ID mapping: frontendSessionId -> backendSessionId
  const backendSessionIds = ref({})

  function _isEmptySession(s) {
    if (!s) return true
    return s.isEmpty || !s.name || s.name.trim() === '' ||
      (s.name === '新会话' && (!s.messages || s.messages.length === 0))
  }

  function _loadFromLocalStorage() {
    const saved = localStorage.getItem('arknights_rag_sessions')
    if (saved) {
      try {
        const parsed = JSON.parse(saved)
        Object.keys(parsed).forEach(id => {
          if (_isEmptySession(parsed[id])) delete parsed[id]
        })
        sessions.value = parsed
      } catch (e) {
        console.warn('Failed to parse sessions from localStorage:', e)
        sessions.value = {}
      }
    }
  }

  async function loadSessions() {
    const authStore = useAuthStore()
    if (authStore.isLoggedIn) {
      try {
        const res = await api.listConversations()
        // 并发拉取各会话消息：逐个 await 是 N+1 串行请求，首屏耗时会随会话数线性增长
        const loaded = await Promise.all(res.conversations.map(async conv => {
          let messages = []
          let loadFailed = false
          try {
            const msgRes = await api.getConversationMessages(conv.session_id)
            messages = msgRes.messages.map(m => ({
              role: m.role,
              content: m.content,
              timestamp: new Date(m.created_at).getTime(),
              ...(m.metadata || {})
            }))
          } catch (e) {
            // 保留失败标记：单个会话拉取失败不能静默当成空会话
            loadFailed = true
            console.warn(`Failed to load messages for ${conv.session_id}:`, e)
          }
          return {
            id: conv.session_id,
            session: {
              id: conv.session_id,
              name: conv.name,
              messages,
              createdAt: new Date(conv.created_at).getTime(),
              updatedAt: new Date(conv.updated_at).getTime(),
              ...(loadFailed ? { messagesLoadFailed: true } : {}),
            }
          }
        }))
        // 按服务端返回顺序写入，避免并发完成顺序影响会话列表顺序
        const newSessions = {}
        loaded.forEach(({ id, session }) => { newSessions[id] = session })
        sessions.value = newSessions
      } catch (e) {
        console.warn('Failed to load sessions from server, falling back to localStorage:', e)
        _loadFromLocalStorage()
      }
    } else {
      _loadFromLocalStorage()
    }

    // Don't auto-select a session — show welcome page on page load
    // Sessions are listed in sidebar for user to click
    if (Object.keys(sessions.value).length === 0) {
      createNewSession()
    }
  }

  function _serializeSessionsForSync(sessionsObj) {
    return Object.values(sessionsObj).map(s => ({
      session_id: s.id,
      name: s.name || '',
      created_at: new Date(s.createdAt).toISOString(),
      updated_at: new Date(s.updatedAt).toISOString(),
      messages: (s.messages || []).map(m => ({
        role: m.role,
        content: m.content,
        metadata: {
          timestamp: m.timestamp,
          ...(m.results ? { results: m.results } : {}),
          ...(m.round ? { round: m.round } : {}),
          ...(m.calls ? { calls: m.calls } : {})
        },
        created_at: new Date(m.timestamp).toISOString(),
      }))
    }))
  }

  // 服务端同步去抖：流式期间 saveSessions 会被频繁调用（每轮思考/工具/回答），
  // 避免每次都全量序列化并请求 /conversations/sync
  let serverSyncTimer = null
  let pendingServerSyncPayload = null
  let pendingServerSyncToken = null  // payload 归属的登录身份，防止登出/换号后旧 payload 落到新账号

  // 撤销尚未触发的去抖同步。删除会话时若旧 payload 仍在窗口内照常发出，
  // 会把刚删掉的会话重新 upsert 回服务端（数据复活），因此删除前必须主动丢弃。
  function cancelPendingServerSync() {
    if (serverSyncTimer) {
      clearTimeout(serverSyncTimer)
      serverSyncTimer = null
    }
    pendingServerSyncPayload = null
    pendingServerSyncToken = null
  }

  // 正在删除中的会话 id。deleteSession 是跨 await 的长流程（删除 agent 会话 + 服务端会话），
  // 期间流式回调等并发路径仍会调用 saveSessions()，而那时被删会话还在 sessions.value 里。
  // 服务端 /conversations/sync 只做 upsert、从不删除，所以只要有一份含被删会话的 payload
  // 在 DELETE 生效之后到达，会话就会被永久复活——仅靠 cancelPendingServerSync() 不够，
  // 去抖计时器可能在 800ms 后、DELETE 落地前就已经触发。这里把这些会话从同步快照里剔除。
  const deletingSessionIds = new Set()

  function _withoutDeletingSessions(sessionsObj) {
    if (deletingSessionIds.size === 0) return sessionsObj
    const filtered = {}
    Object.keys(sessionsObj).forEach(id => {
      if (!deletingSessionIds.has(id)) filtered[id] = sessionsObj[id]
    })
    return filtered
  }

  function scheduleServerSync(payload) {
    const auth = useAuthStore()
    pendingServerSyncPayload = payload
    pendingServerSyncToken = auth.token
    if (serverSyncTimer) return
    serverSyncTimer = setTimeout(async () => {
      serverSyncTimer = null
      const toSync = pendingServerSyncPayload
      const ownerToken = pendingServerSyncToken
      pendingServerSyncPayload = null
      pendingServerSyncToken = null
      if (!toSync) return
      const auth = useAuthStore()
      // 已登出或已切换账号：旧 payload 不再属于当前身份，直接丢弃
      if (!auth.isLoggedIn || auth.token !== ownerToken) return
      try {
        await api.syncConversations(toSync)
      } catch (e) {
        console.warn('Failed to sync sessions to server:', e)
      }
    }, 800)
  }

  async function saveSessions() {
    const toSave = {}
    Object.keys(sessions.value).forEach(id => {
      if (!_isEmptySession(sessions.value[id])) {
        toSave[id] = sessions.value[id]
      }
    })

    // Always save to localStorage as cache
    try {
      localStorage.setItem('arknights_rag_sessions', JSON.stringify(toSave))
      if (currentSessionId.value && !_isEmptySession(sessions.value[currentSessionId.value])) {
        localStorage.setItem('arknights_rag_last_session', currentSessionId.value)
      }
    } catch (e) {
      console.warn('Failed to save sessions to localStorage:', e)
    }

    // Sync to server if logged in（去抖合并高频调用）
    const authStore = useAuthStore()
    if (authStore.isLoggedIn) {
      try {
        // 剔除「删除中」的会话：否则它们的旧快照会被 upsert 回服务端（删除无法撤销 upsert）
        scheduleServerSync(_serializeSessionsForSync(_withoutDeletingSessions(toSave)))
      } catch (e) {
        console.warn('Failed to serialize sessions for server sync:', e)
      }
    }
  }

  async function createNewSession() {
    // If current session is already empty, reuse it instead of creating a new one
    const current = sessions.value[currentSessionId.value]
    if (current && _isEmptySession(current)) {
      return currentSessionId.value
    }

    // Create frontend session with isEmpty flag (won't show in sidebar until first message)
    const id = 'session_' + Date.now() + '_' + Math.random().toString(36).substring(2, 8)
    sessions.value[id] = {
      id,
      name: '',
      messages: [],
      createdAt: Date.now(),
      updatedAt: Date.now(),
      isEmpty: true
    }
    currentSessionId.value = id
    lastActiveSessionId.value = id

    // Create backend agent session
    try {
      const result = await api.createAgentSession()
      backendSessionIds.value[id] = result.session_id
      localStorage.setItem('arknights_rag_backend_sessions', JSON.stringify(backendSessionIds.value))
    } catch (e) {
      console.error('Failed to create backend session:', e)
    }

    saveSessions()
    return id
  }

  function getBackendSessionId(frontendId) {
    return backendSessionIds.value[frontendId || currentSessionId.value]
  }

  async function deleteSession(sessionId) {
    // 删除会话是一段跨两个 await 的临界区，这里要取消**两次**去抖同步：
    //
    // 第一次（此处）：清掉进入删除流程前已经排队的那份快照——它必然还含待删会话，
    // 若照常发出就会把会话 upsert 回服务端。
    cancelPendingServerSync()

    // 兜底标记：窗口期内任何并发 saveSessions() 生成的 payload 都不再带上该会话。
    // 只靠两次 cancelPendingServerSync() 仍不够——去抖计时器可能在 800ms 后、
    // DELETE 落地之前就已经触发（实测删除耗时 1.2s 时会稳定命中），此时 payload 已经发出，
    // 只能靠「快照里根本没有这个会话」来保证服务端不会被 upsert 复活它。
    deletingSessionIds.add(sessionId)
    try {
      // Delete backend agent session
      const backendId = backendSessionIds.value[sessionId]
      if (backendId) {
        try { await api.deleteAgentSession(backendId) } catch (e) { console.warn('Failed to delete backend session:', e) }
        delete backendSessionIds.value[sessionId]
        localStorage.setItem('arknights_rag_backend_sessions', JSON.stringify(backendSessionIds.value))
      }

      // Delete from server if logged in
      const authStore = useAuthStore()
      if (authStore.isLoggedIn) {
        try { await api.deleteConversation(sessionId) } catch (e) { console.warn('Failed to delete conversation from server:', e) }
      }

      // 第二次（所有 await 之后、改动本地状态之前）：上面两个 await 期间并发的
      // saveSessions() 会重新排入一份仍含待删会话的快照，这里再清一次，
      // 确保它不会在本次删除落地之后被发出。
      // 末尾的 saveSessions() 会用删除后的最新全量状态重新排队，不会丢数据。
      cancelPendingServerSync()

      delete sessions.value[sessionId]
      if (currentSessionId.value === sessionId) {
        const remaining = Object.keys(sessions.value).sort(
          (a, b) => sessions.value[b].updatedAt - sessions.value[a].updatedAt
        )
        if (remaining.length > 0) {
          currentSessionId.value = remaining[0]
        } else {
          createNewSession()
        }
      }
      if (lastActiveSessionId.value === sessionId) {
        lastActiveSessionId.value = currentSessionId.value
      }
    } finally {
      // 无论删除是否成功都解除标记，避免该会话被永久排除在同步之外
      deletingSessionIds.delete(sessionId)
    }
    saveSessions()
    return true
  }

  function switchSession(sessionId) {
    if (sessions.value[sessionId]) {
      currentSessionId.value = sessionId
      lastActiveSessionId.value = sessionId
      localStorage.setItem('arknights_rag_last_session', sessionId)
    }
  }

  async function renameSession(sessionId, newName) {
    if (sessions.value[sessionId]) {
      sessions.value[sessionId].name = newName
      sessions.value[sessionId].updatedAt = Date.now()
      // Rename on server if logged in
      const authStore = useAuthStore()
      if (authStore.isLoggedIn) {
        try { await api.renameConversation(sessionId, newName) } catch (e) { console.warn('Failed to rename on server:', e) }
      }
      saveSessions()
    }
  }

  function addMessage(role, content, extra = {}) {
    let targetSessionId = currentSessionId.value

    // If current session doesn't exist, create it
    if (!targetSessionId || !sessions.value[targetSessionId]) {
      targetSessionId = 'session_' + Date.now() + '_' + Math.random().toString(36).substring(2, 8)
      sessions.value[targetSessionId] = {
        id: targetSessionId,
        name: '',
        messages: [],
        createdAt: Date.now(),
        updatedAt: Date.now()
      }
      currentSessionId.value = targetSessionId
    }

    const session = sessions.value[targetSessionId]
    if (session) {
      // Use first user message as session name
      if (role === 'user' && session.messages.length === 0) {
        const trimmed = content.trim()
        if (trimmed.length > 0) {
          session.name = trimmed.length > 6 ? trimmed.substring(0, 6) + '...' : trimmed
        } else {
          session.name = '新会话'
        }
        lastActiveSessionId.value = targetSessionId
        if (session.isEmpty) {
          delete session.isEmpty
        }
      }
      session.messages.push({ role, content, timestamp: Date.now(), ...extra })
      session.updatedAt = Date.now()
      saveSessions()
    }
  }

  function addThinkingMessage(roundNum, content, timeMs = 0) {
    let targetSessionId = currentSessionId.value
    if (!targetSessionId || !sessions.value[targetSessionId]) return

    const session = sessions.value[targetSessionId]
    session.messages.push({
      role: 'thinking',
      round: roundNum,
      content: content,
      timestamp: Date.now(),
      time_ms: timeMs,
    })
    session.updatedAt = Date.now()
    saveSessions()
  }

  // Variants that write to a specific session (used by streaming callbacks so
  // content goes to the correct session even after user switches sessions).
  function addMessageTo(sessionId, role, content, extra = {}) {
    const session = sessions.value[sessionId]
    if (!session) return
    session.messages.push({ role, content, timestamp: Date.now(), ...extra })
    session.updatedAt = Date.now()
    saveSessions()
  }

  // Replace the last assistant message if it was a partial save from session switch,
  // otherwise append normally. Prevents duplicate answers when streaming across session switches.
  function replaceLastAssistantIfPartial(sessionId, content, extra = {}) {
    const session = sessions.value[sessionId]
    if (!session) return
    const msgs = session.messages
    const last = msgs[msgs.length - 1]
    if (last && last.role === 'assistant' && last._partial) {
      msgs[msgs.length - 1] = { role: 'assistant', content, timestamp: Date.now(), ...extra }
    } else {
      msgs.push({ role: 'assistant', content, timestamp: Date.now(), ...extra })
    }
    session.updatedAt = Date.now()
    saveSessions()
  }

  function addThinkingMessageTo(sessionId, roundNum, content, timeMs = 0) {
    const session = sessions.value[sessionId]
    if (!session) return
    session.messages.push({
      role: 'thinking', round: roundNum, content, timestamp: Date.now(), time_ms: timeMs,
    })
    session.updatedAt = Date.now()
    saveSessions()
  }

  function addToolCallMessage(toolCalls, roundNum, sessionId = null) {
    let targetSessionId = sessionId || currentSessionId.value
    if (!targetSessionId || !sessions.value[targetSessionId]) return

    const session = sessions.value[targetSessionId]
    session.messages.push({
      role: 'tool_call',
      round: roundNum,
      calls: toolCalls,
      timestamp: Date.now(),
    })
    session.updatedAt = Date.now()
    saveSessions()
  }

  function updateToolCallResult(toolCallId, result, sessionId = null) {
    let targetSessionId = sessionId || currentSessionId.value
    if (!targetSessionId || !sessions.value[targetSessionId]) return

    const session = sessions.value[targetSessionId]
    const msgIdx = session.messages.findIndex(
      m => m.role === 'tool_call' && m.calls?.some(c => c.id === toolCallId)
    )
    if (msgIdx !== -1) {
      // Replace the message object entirely to guarantee Vue reactivity
      const msg = session.messages[msgIdx]
      const newResults = { ...(msg.results || {}), [toolCallId]: {
        summary: result.summary || '完成',
        time_ms: result.time_ms || 0,
        tool_name: result.tool_name || '',
        data: result.result || null,
      }}
      session.messages.splice(msgIdx, 1, { ...msg, results: newResults })
      saveSessions()
    }
  }

  // Mark all unresolved tool calls as interrupted. Used when a stream is
  // aborted/errored mid-execution, and to sweep stale pending state on load
  // (e.g. page was closed while tools were running).
  function finalizePendingToolCalls(sessionId = null, summary = '已中断') {
    const ids = sessionId ? [sessionId] : Object.keys(sessions.value)
    let changed = false
    for (const id of ids) {
      const session = sessions.value[id]
      if (!session?.messages) continue
      session.messages.forEach((msg, msgIdx) => {
        if (msg.role !== 'tool_call' || !msg.calls) return
        const pending = msg.calls.filter(c => !msg.results?.[c.id])
        if (pending.length === 0) return
        const newResults = { ...(msg.results || {}) }
        pending.forEach(c => {
          newResults[c.id] = { summary, time_ms: 0, tool_name: c.name || '', data: null, interrupted: true }
        })
        session.messages.splice(msgIdx, 1, { ...msg, results: newResults })
        changed = true
      })
    }
    if (changed) saveSessions()
  }

  // Remove all messages from fromIndex onward (used by "regenerate" to roll
  // back to just before the last user message).
  function truncateMessages(sessionId, fromIndex) {
    const session = sessions.value[sessionId]
    if (!session) return
    session.messages.splice(fromIndex)
    session.updatedAt = Date.now()
    saveSessions()
  }

  // 编辑用户消息并创建版本分支：
  // 旧内容 + 后续对话存为当前历史版本，新内容作为最新版本（新分支），截断后续消息
  function branchUserMessage(sessionId, messageIdx, newContent) {
    const session = sessions.value[sessionId]
    if (!session) return
    const msg = session.messages[messageIdx]
    if (!msg || msg.role !== 'user') return
    const followUps = session.messages.slice(messageIdx + 1)
    if (msg.versions && msg.versions.length > 0) {
      // 保存当前活跃分支的现场
      msg.versions[msg.activeVersion ?? msg.versions.length - 1] = { content: msg.content, followUps }
    } else {
      msg.versions = [{ content: msg.content, followUps }]
    }
    msg.versions.push({ content: newContent, followUps: [] })
    msg.activeVersion = msg.versions.length - 1
    // 用新对象替换以触发 v-memo 失效
    const updated = { ...msg, content: newContent, timestamp: Date.now() }
    session.messages.splice(messageIdx, 1, updated)
    session.messages.splice(messageIdx + 1)
    session.updatedAt = Date.now()
    saveSessions()
  }

  // 在编辑分支之间切换：保存当前分支现场，恢复目标分支（含其后续对话）
  function switchUserVersion(sessionId, messageIdx, delta) {
    const session = sessions.value[sessionId]
    if (!session) return
    const msg = session.messages[messageIdx]
    if (!msg?.versions?.length) return
    const cur = msg.activeVersion ?? msg.versions.length - 1
    const next = Math.max(0, Math.min(msg.versions.length - 1, cur + delta))
    if (next === cur) return
    // 保存当前分支现场
    msg.versions[cur] = { content: msg.content, followUps: session.messages.slice(messageIdx + 1) }
    // 恢复目标分支
    const target = msg.versions[next]
    const updated = { ...msg, content: target.content, activeVersion: next }
    session.messages.splice(messageIdx + 1)
    session.messages.splice(messageIdx, 1, updated)
    if (target.followUps?.length) {
      session.messages.splice(messageIdx + 1, 0, ...target.followUps)
    }
    session.updatedAt = Date.now()
    saveSessions()
  }

  async function mergeLocalToServer() {
    const authStore = useAuthStore()
    if (!authStore.isLoggedIn) return

    const toSave = {}
    Object.keys(sessions.value).forEach(id => {
      if (!_isEmptySession(sessions.value[id])) toSave[id] = sessions.value[id]
    })

    if (Object.keys(toSave).length > 0) {
      try {
        // 同样剔除「删除中」的会话，避免合并时把正在删除的会话 upsert 回去
        await api.syncConversations(_serializeSessionsForSync(_withoutDeletingSessions(toSave)))
      } catch (e) {
        console.warn('Failed to merge local sessions to server:', e)
      }
    }

    // Reload from server
    await loadSessions()
  }

  const currentSession = computed(() => sessions.value[currentSessionId.value] || null)
  const sessionList = computed(() =>
    Object.values(sessions.value)
      .filter(s => !s.isEmpty)
      .sort((a, b) => b.updatedAt - a.updatedAt)
  )

  // Load backend session mapping
  const savedBackendSessions = localStorage.getItem('arknights_rag_backend_sessions')
  if (savedBackendSessions) {
    try {
      backendSessionIds.value = JSON.parse(savedBackendSessions)
    } catch (e) {
      backendSessionIds.value = {}
    }
  }

  loadSessions()

  return {
    sessions,
    currentSessionId,
    currentSession,
    sessionList,
    backendSessionIds,
    createNewSession,
    deleteSession,
    switchSession,
    renameSession,
    addMessage,
    addThinkingMessage,
    addMessageTo,
    replaceLastAssistantIfPartial,
    addThinkingMessageTo,
    addToolCallMessage,
    updateToolCallResult,
    finalizePendingToolCalls,
    truncateMessages,
    branchUserMessage,
    switchUserVersion,
    saveSessions,
    getBackendSessionId,
    loadSessions,
    mergeLocalToServer
  }
})
