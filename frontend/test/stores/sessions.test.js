/**
 * Tests for sessions store: useSessionStore.
 * Usage: cd frontend && npx vitest run test/stores/sessions.test.js
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'

// Mock localStorage
const store_data = {}
global.localStorage = {
  getItem: vi.fn((key) => store_data[key] ?? null),
  setItem: vi.fn((key, value) => { store_data[key] = value }),
  removeItem: vi.fn((key) => { delete store_data[key] }),
}

let mockIsLoggedIn = false

// Mock api
vi.mock('../../src/api', () => ({
  api: {
    listConversations: vi.fn().mockResolvedValue({ conversations: [] }),
    getConversationMessages: vi.fn(),
    syncConversations: vi.fn(),
    deleteConversation: vi.fn(),
    renameConversation: vi.fn(),
    createAgentSession: vi.fn().mockResolvedValue({ session_id: 'backend-sess-1' }),
    deleteAgentSession: vi.fn(),
  },
}))

// Mock auth store
vi.mock('../../src/stores/auth', () => ({
  useAuthStore: vi.fn(() => ({
    isLoggedIn: mockIsLoggedIn,
  })),
}))

import { useSessionStore } from '../../src/stores/sessions'
import { api } from '../../src/api'

describe('useSessionStore', () => {
  beforeEach(() => {
    Object.keys(store_data).forEach(k => delete store_data[k])
    mockIsLoggedIn = false
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  describe('createNewSession', () => {
    it('creates a new empty session', async () => {
      // The store auto-creates a session on init via loadSessions().
      // We get the store and then call createNewSession explicitly.
      const store = useSessionStore()
      // Store has already auto-created one empty session
      // createNewSession should create a second distinct one
      const firstLen = Object.keys(store.sessions).length
      const id = await store.createNewSession()
      expect(id).toBeTruthy()
      expect(id).toMatch(/^session_\d+/)
      expect(Object.keys(store.sessions).length).toBeGreaterThanOrEqual(firstLen)
      expect(store.sessions[id].isEmpty).toBe(true)
    })

    it('creates backend session too', async () => {
      const store = useSessionStore()
      // Manually create new session
      store.currentSessionId = null
      const id = await store.createNewSession()
      expect(api.createAgentSession).toHaveBeenCalled()
      expect(store.backendSessionIds[id]).toBe('backend-sess-1')
    })
  })

  describe('addMessage', () => {
    it('adds user message and sets session name', () => {
      const store = useSessionStore()
      store.currentSessionId = 'test-session'
      store.sessions['test-session'] = {
        id: 'test-session',
        name: '',
        messages: [],
        createdAt: Date.now(),
        updatedAt: Date.now(),
      }

      store.addMessage('user', 'Hello world')

      const session = store.sessions['test-session']
      expect(session.messages.length).toBe(1)
      expect(session.messages[0].role).toBe('user')
      expect(session.messages[0].content).toBe('Hello world')
      expect(session.name).toBe('Hello ...')
    })

    it('removes isEmpty flag on first user message', () => {
      const store = useSessionStore()
      store.currentSessionId = 'test-session'
      store.sessions['test-session'] = {
        id: 'test-session',
        name: '',
        messages: [],
        createdAt: Date.now(),
        updatedAt: Date.now(),
        isEmpty: true,
      }

      store.addMessage('user', 'Hi')
      expect(store.sessions['test-session'].isEmpty).toBeUndefined()
    })
  })

  describe('addThinkingMessage', () => {
    it('adds a thinking message', () => {
      const store = useSessionStore()
      store.currentSessionId = 'test-session'
      store.sessions['test-session'] = {
        id: 'test-session',
        name: 'Test',
        messages: [],
        createdAt: Date.now(),
        updatedAt: Date.now(),
      }

      store.addThinkingMessage(1, 'reasoning content', 1500)
      const msg = store.sessions['test-session'].messages[0]
      expect(msg.role).toBe('thinking')
      expect(msg.round).toBe(1)
      expect(msg.content).toBe('reasoning content')
      expect(msg.time_ms).toBe(1500)
    })
  })

  describe('addToolCallMessage', () => {
    it('adds tool call message', () => {
      const store = useSessionStore()
      store.currentSessionId = 'test-session'
      store.sessions['test-session'] = {
        id: 'test-session',
        name: 'Test',
        messages: [],
        createdAt: Date.now(),
        updatedAt: Date.now(),
      }

      const calls = [{ id: 'c1', name: 'search', arguments_summary: 'query: test' }]
      store.addToolCallMessage(calls, 2)
      const msg = store.sessions['test-session'].messages[0]
      expect(msg.role).toBe('tool_call')
      expect(msg.round).toBe(2)
      expect(msg.calls).toEqual(calls)
    })
  })

  describe('updateToolCallResult', () => {
    it('updates result for matching tool call', () => {
      const store = useSessionStore()
      store.currentSessionId = 'test-session'
      store.sessions['test-session'] = {
        id: 'test-session',
        name: 'Test',
        messages: [
          {
            role: 'tool_call',
            round: 1,
            calls: [{ id: 'c1', name: 'search' }],
            timestamp: Date.now(),
          },
        ],
        createdAt: Date.now(),
        updatedAt: Date.now(),
      }

      store.updateToolCallResult('c1', {
        summary: 'found 3 items',
        time_ms: 150,
        result: [{ id: 1 }],
      })

      const msg = store.sessions['test-session'].messages[0]
      expect(msg.results).toBeDefined()
      expect(msg.results['c1'].summary).toBe('found 3 items')
      expect(msg.results['c1'].data).toEqual([{ id: 1 }])
    })

    it('does nothing if session does not exist', () => {
      const store = useSessionStore()
      store.currentSessionId = 'nonexistent'
      // Should not throw
      store.updateToolCallResult('c1', { summary: 'ok' })
    })
  })

  describe('deleteSession', () => {
    it('removes session and backend mapping', async () => {
      const store = useSessionStore()
      // Clear auto-created sessions
      store.sessions = {}
      store.currentSessionId = 's1'

      store.sessions['s1'] = {
        id: 's1',
        name: 'Test',
        messages: [{ role: 'user', content: 'hi', timestamp: Date.now() }],
        createdAt: Date.now(),
        updatedAt: Date.now(),
      }
      store.sessions['s2'] = {
        id: 's2',
        name: 'Test2',
        messages: [],
        createdAt: Date.now(),
        updatedAt: Date.now(),
      }
      store.backendSessionIds['s1'] = 'backend-1'

      await store.deleteSession('s1')
      expect(store.sessions['s1']).toBeUndefined()
      expect(store.backendSessionIds['s1']).toBeUndefined()
      // Falls back to remaining session
      expect(store.currentSessionId).toBe('s2')
    })
  })

  describe('deleteSession 与服务端同步的竞态（T15）', () => {
    // 服务端 POST /conversations/sync 只做 upsert、从不删除：
    // 只要有一份「还含被删会话」的快照在 DELETE 落地之后（或删除在途期间）发出，
    // 被删会话就会被永久复活。修复 = 删除期间用 deletingSessionIds 把该会话从同步快照里剔除。
    afterEach(() => {
      vi.useRealTimers()
      api.deleteConversation.mockReset()
      api.deleteAgentSession.mockReset()
      api.syncConversations.mockReset()
    })

    function deferred() {
      let resolve
      const promise = new Promise(r => { resolve = r })
      return { promise, resolve }
    }

    /** 预置两个非空会话，并把 s1 标成"有后端 agent 会话"，让 deleteSession 有完整的 await 链 */
    function seedSessions(store) {
      store.sessions = {}
      store.currentSessionId = 's1'
      store.sessions['s1'] = {
        id: 's1', name: '待删会话', createdAt: 1000, updatedAt: 2000,
        messages: [{ role: 'user', content: '要被删掉的问题', timestamp: 2000 }],
      }
      store.sessions['s2'] = {
        id: 's2', name: '保留会话', createdAt: 1000, updatedAt: 3000,
        messages: [{ role: 'user', content: '要保留的问题', timestamp: 3000 }],
      }
      store.backendSessionIds['s1'] = 'backend-1'
    }

    function syncedSessionIds() {
      return api.syncConversations.mock.calls.flatMap(([payload]) =>
        (payload || []).map(c => c.session_id)
      )
    }

    it('删除在途 + 去抖窗口内并发 saveSessions：同步 payload 不含被删会话', async () => {
      vi.useFakeTimers()
      mockIsLoggedIn = true // saveSessions 只在登录态才排服务端同步
      const store = useSessionStore()
      await vi.advanceTimersByTimeAsync(0) // 等 loadSessions()/createNewSession() 的微任务落地

      seedSessions(store)
      const deleteApi = deferred()
      api.deleteConversation.mockReturnValue(deleteApi.promise) // 删除挂起，制造在途窗口
      api.deleteAgentSession.mockResolvedValue({})
      api.syncConversations.mockResolvedValue({})

      const deleting = store.deleteSession('s1')
      await vi.advanceTimersByTimeAsync(0) // 让 deleteSession 走到 await deleteConversation

      // 并发路径：流式回调 / 其它消息操作在删除在途期间调用 saveSessions()
      store.saveSessions()
      // 去抖窗口 800ms 到期：旧实现会把「仍含 s1」的快照 upsert 回服务端
      await vi.advanceTimersByTimeAsync(800)

      expect(api.syncConversations).toHaveBeenCalled()
      expect(syncedSessionIds()).toContain('s2')
      expect(syncedSessionIds()).not.toContain('s1')

      deleteApi.resolve({})
      await vi.advanceTimersByTimeAsync(0)
      await deleting
      // 删除落地后的收尾同步同样不能带上 s1
      await vi.advanceTimersByTimeAsync(800)
      expect(store.sessions['s1']).toBeUndefined()
      expect(syncedSessionIds()).not.toContain('s1')
    })

    it('删除完成后 deletingSessionIds 被解除，同名会话后续仍能正常同步', async () => {
      // 反向约束：剔除逻辑必须只作用于"删除在途"窗口，不能把会话永久排除在同步之外。
      vi.useFakeTimers()
      mockIsLoggedIn = true
      const store = useSessionStore()
      await vi.advanceTimersByTimeAsync(0)

      seedSessions(store)
      api.deleteConversation.mockResolvedValue({})
      api.deleteAgentSession.mockResolvedValue({})
      api.syncConversations.mockResolvedValue({})

      await store.deleteSession('s1')
      await vi.advanceTimersByTimeAsync(800)
      expect(syncedSessionIds()).not.toContain('s1')

      // 删除结束后 id 可被复用（新建同名会话）→ 必须能同步上去
      api.syncConversations.mockClear()
      store.sessions['s1'] = {
        id: 's1', name: '同 id 新会话', createdAt: 1000, updatedAt: 4000,
        messages: [{ role: 'user', content: '新内容', timestamp: 4000 }],
      }
      store.saveSessions()
      await vi.advanceTimersByTimeAsync(800)
      expect(syncedSessionIds()).toContain('s1')
    })
  })

  describe('sessionList', () => {
    it('excludes empty sessions and sorts by updatedAt', () => {
      const store = useSessionStore()
      store.sessions = {}
      store.sessions['s1'] = {
        id: 's1', name: 'A', messages: [{ role: 'user', content: 'x', timestamp: 100 }],
        createdAt: 100, updatedAt: 300,
      }
      store.sessions['s2'] = {
        id: 's2', name: '', messages: [], createdAt: 200, updatedAt: 200, isEmpty: true,
      }
      store.sessions['s3'] = {
        id: 's3', name: 'B', messages: [{ role: 'user', content: 'y', timestamp: 300 }],
        createdAt: 300, updatedAt: 600,
      }

      const list = store.sessionList
      expect(list.length).toBe(2)
      expect(list[0].id).toBe('s3')
      expect(list[1].id).toBe('s1')
    })
  })
})
