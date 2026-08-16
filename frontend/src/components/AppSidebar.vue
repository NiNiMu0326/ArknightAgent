<template>
  <aside class="sidebar">
    <div class="sidebar-header">
      <div class="sidebar-logo">
        <span class="logo-icon">A</span>
        <div class="logo-text">
          <span class="logo-title">Arknights</span>
          <span class="logo-subtitle">Agent System</span>
        </div>
      </div>
    </div>

    <nav class="sidebar-nav">
      <router-link to="/chat" class="nav-item" :class="{ active: $route.path === '/chat' }" @click="closeMobileMenu">
        <span class="nav-icon">Q</span>
        <span class="nav-text">问答助手</span>
      </router-link>
      <router-link to="/admin" class="nav-item" :class="{ active: $route.path === '/admin' }" @click="closeMobileMenu">
        <span class="nav-icon">G</span>
        <span class="nav-text">管理后台</span>
      </router-link>
      <router-link to="/graph" class="nav-item" :class="{ active: $route.path === '/graph' }" @click="closeMobileMenu">
        <span class="nav-icon">N</span>
        <span class="nav-text">知识图谱</span>
      </router-link>
    </nav>

    <!-- Graph Controls - Only show on /graph route -->
    <div class="sidebar-graph-controls" v-if="showGraphControls">
      <!-- Search and Selected Nodes -->
      <div class="sidebar-section">
        <div class="sidebar-section-title">搜索节点</div>
        <div class="kg-search-box">
          <input type="text" v-model="gc.searchQuery.value" placeholder="输入干员名称..." @input="gc.handleSearch(gc.searchQuery.value)" @focus="gc.onSearchFocus" @blur="gc.onSearchBlur">
          <div class="kg-search-results" v-show="gc.searchFocused.value || gc.searchResults.value.length">
            <div v-for="result in gc.searchResults.value" :key="result.entity" class="kg-search-result-item" @mousedown="gc.addNodeSelection(result.entity)">
              <span class="kg-result-name">{{ result.entity }}</span>
              <span class="kg-result-type">{{ result.type || '干员' }}</span>
            </div>
            <div v-if="gc.searchResults.value.length === 0" class="kg-search-result-item kg-no-results">无结果</div>
          </div>
        </div>
        <div class="kg-selected-nodes" v-if="gc.selectedNodes.value.length > 0">
          <div class="kg-selected-header">
            <span>已选</span>
            <span class="kg-selected-count">{{ gc.selectedNodes.value.length }}</span>
          </div>
          <div v-for="nodeId in gc.selectedNodes.value" :key="nodeId" class="kg-selected-node">
            <span class="kg-selected-node-name">{{ nodeId }}</span>
            <button class="kg-selected-node-remove" @click="gc.removeNodeSelection(nodeId)">X</button>
          </div>
        </div>
        <div class="kg-stats-inline">
          <span>节点 <span class="kg-stat-num">{{ gc.currentStats.nodes }}</span></span>
          <span>边 <span class="kg-stat-num">{{ gc.currentStats.edges }}</span></span>
        </div>
      </div>

      <!-- Level Control -->
      <div class="sidebar-section">
        <div class="sidebar-section-title">邻居层级</div>
        <div class="kg-level-control">
          <button class="kg-level-btn" :class="{ active: gc.neighborLevel.value === 1 }" @click="gc.setNeighborLevel(1)">1度</button>
          <button class="kg-level-btn" :class="{ active: gc.neighborLevel.value === 2 }" @click="gc.setNeighborLevel(2)">2度</button>
        </div>
      </div>

      <!-- Relation Type Filter -->
      <div class="sidebar-section" v-if="gc.availableRelations.value.length">
        <div class="sidebar-section-title">关系类型筛选</div>
        <div class="kg-relation-chips">
          <div
            v-for="rel in gc.availableRelations.value"
            :key="rel"
            class="kg-relation-chip"
            :class="{ active: gc.selectedRelations.value.includes(rel) }"
            @click="gc.toggleRelationSelection(rel)"
            @mouseenter="gc.hoveredRelation.value = rel"
            @mouseleave="gc.hoveredRelation.value = null"
          >
            {{ rel }}
          </div>
        </div>
      </div>

      <div class="sidebar-section">
        <button class="kg-reset-btn" @click="gc.clearSelection">
          <span>R</span> 重置选择
        </button>
      </div>
    </div>

    <div class="session-manager" :class="{ expanded: sessionExpanded }" v-if="showSessionManager">
      <div class="session-manager-header" @click="sessionExpanded = !sessionExpanded">
        <span class="session-manager-title">
          <span class="nav-icon">S</span>
          <span class="nav-text">会话管理</span>
        </span>
        <span class="session-manager-toggle">▼</span>
      </div>
      <div class="session-manager-body">
        <div class="sidebar-session-list">
          <div
            v-for="session in sessionStore.sessionList"
            :key="session.id"
            class="sidebar-session-item"
            :class="{ active: session.id === sessionStore.currentSessionId }"
            @click="sessionStore.switchSession(session.id)"
          >
            <div class="sidebar-session-info">
              <span class="sidebar-session-name">{{ session.name }}</span>
              <span class="sidebar-session-time">{{ formatTime(new Date(session.updatedAt)) }}</span>
            </div>
            <div class="sidebar-session-actions">
              <button class="sidebar-session-action-btn" @click.stop="promptRename(session.id)" title="重命名">R</button>
              <button class="sidebar-session-action-btn delete" @click.stop="confirmDelete(session.id)" title="删除">X</button>
            </div>
          </div>
        </div>
        <div class="session-manager-actions">
          <button class="btn btn-primary w-full" @click="sessionStore.createNewSession()">
            <span>+</span> 新建会话
          </button>
        </div>
      </div>
    </div>

    <div class="sidebar-footer">
      <div class="sidebar-status">
        <span class="status-dot" :class="{ offline: !systemOnline }"></span>
        <span class="status-text">{{ systemOnline ? 'System Online' : 'System Offline' }}</span>
      </div>
    </div>

    <div class="modal-overlay" :class="{ active: showRenameModal }" @click.self="showRenameModal = false">
      <div class="modal-content modal-sm">
        <div class="modal-header">
          <h2>重命名会话</h2>
          <button class="modal-close" @click="showRenameModal = false">&times;</button>
        </div>
        <div class="modal-body">
          <input type="text" class="modal-input" v-model="renameInput" placeholder="输入新名称..." @keydown.enter="confirmRename">
        </div>
        <div class="modal-footer">
          <button class="btn btn-secondary" @click="showRenameModal = false">取消</button>
          <button class="btn btn-primary" @click="confirmRename">确定</button>
        </div>
      </div>
    </div>

    <div class="modal-overlay" :class="{ active: showDeleteModal }" @click.self="showDeleteModal = false">
      <div class="modal-content modal-sm">
        <div class="modal-body text-left">
          <h2>删除会话</h2>
          <p class="modal-text">确定删除会话 "<strong>{{ deleteTargetName }}</strong>" 吗？此操作不可撤销。</p>
          <div class="modal-footer-btns">
            <button class="btn btn-secondary" @click="showDeleteModal = false">取消</button>
            <button class="btn btn-danger" @click="executeDelete">删除</button>
          </div>
        </div>
      </div>
    </div>
  </aside>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'
import { useRoute } from 'vue-router'
import { useSessionStore } from '../stores/sessions'
import { useAuthStore } from '../stores/auth'
import { useGraphController, isEntitiesEmpty } from '../composables/useGraphController'
import { api, formatTime } from '../api'

const route = useRoute()
const sessionStore = useSessionStore()
const authStore = useAuthStore()
const gc = useGraphController()

// 通知 App.vue 关闭移动端侧边栏（由其 sidebarOpen 状态统一驱动 DOM，
// 不要直接操作 DOM class，否则 Vue 状态与实际显示会失步）
function closeMobileMenu() {
  window.dispatchEvent(new CustomEvent('close-mobile-sidebar'))
}

// Auto-close mobile sidebar on route change
watch(() => route.path, () => {
  closeMobileMenu()
})

const sessionExpanded = ref(true)
const showRenameModal = ref(false)
const renameInput = ref('')
const renameTargetId = ref(null)
const showDeleteModal = ref(false)
const deleteTargetId = ref(null)
const deleteTargetName = ref('')

// Show session manager only on chat page
const showSessionManager = computed(() => route.path === '/chat')

// Show graph controls only on graph page
const showGraphControls = computed(() => route.path === '/graph')

// Load graph data when graph page is shown
watch(showGraphControls, (show) => {
  if (show && isEntitiesEmpty(gc.graphData.value.entities)) {
    gc.loadGraphData()
  }
  if (show) {
    gc.updateAvailableRelations()
  }
}, { immediate: true })

watch(() => gc.selectedNodes.value, () => {
  gc.updateAvailableRelations()
})  // No deep: true needed - selectedNodes uses array replacement, not in-place mutation

// 切换会话时自动展开会话管理器
watch(() => sessionStore.currentSessionId, () => {
  sessionExpanded.value = true
})

const _authChangedHandler = async () => {
  if (authStore.isLoggedIn) {
    await sessionStore.mergeLocalToServer()
  } else {
    await sessionStore.loadSessions()
  }
}

onMounted(() => {
  // Load data if on graph page and data not loaded
  if (showGraphControls.value && isEntitiesEmpty(gc.graphData.value.entities)) {
    gc.loadGraphData()
  }
  // Listen for auth changes to sync sessions
  window.addEventListener('auth-changed', _authChangedHandler)
  checkSystemHealth()
  healthTimerId = setInterval(checkSystemHealth, 30000)
})

onUnmounted(() => {
  window.removeEventListener('auth-changed', _authChangedHandler)
  if (healthTimerId !== null) {
    clearInterval(healthTimerId)
    healthTimerId = null
  }
})

// 底部状态点：基于后端 /status 的真实健康检查，每 30s 轮询
const systemOnline = ref(true)
let healthTimerId = null

async function checkSystemHealth() {
  try {
    const res = await api.getStatus()
    systemOnline.value = !!res
  } catch {
    systemOnline.value = false
  }
}

function promptRename(sessionId) {
  renameTargetId.value = sessionId
  renameInput.value = sessionStore.sessions[sessionId]?.name || ''
  showRenameModal.value = true
}

function confirmRename() {
  if (renameInput.value.trim() && renameTargetId.value) {
    sessionStore.renameSession(renameTargetId.value, renameInput.value.trim())
  }
  showRenameModal.value = false
}

function confirmDelete(sessionId) {
  deleteTargetName.value = sessionStore.sessions[sessionId]?.name || '该会话'
  deleteTargetId.value = sessionId
  showDeleteModal.value = true
}

function executeDelete() {
  if (deleteTargetId.value) {
    sessionStore.deleteSession(deleteTargetId.value)
  }
  showDeleteModal.value = false
  deleteTargetId.value = null
  deleteTargetName.value = ''
}

// 删除弹窗打开时，按 Enter 触发删除
function onDeleteModalKeydown(e) {
  if (e.key === 'Enter') {
    e.preventDefault()
    executeDelete()
  }
}

watch(showDeleteModal, (val) => {
  if (val) {
    document.addEventListener('keydown', onDeleteModalKeydown)
  } else {
    document.removeEventListener('keydown', onDeleteModalKeydown)
  }
})
</script>

<style scoped>
/* Graph Controls Styles */
.sidebar-graph-controls {
  flex: 1;
  overflow-y: auto;
  padding: var(--spacing-md);
  border-top: 1px solid var(--border-color);
}

.sidebar-section {
  margin-bottom: var(--spacing-md);
}

.sidebar-section-title {
  font-size: 0.75rem;
  color: var(--text-dim);
  text-transform: uppercase;
  letter-spacing: 0.05em;
  margin-bottom: var(--spacing-sm);
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.kg-search-box {
  position: relative;
}

.kg-search-box input {
  width: 100%;
  padding: var(--spacing-xs) var(--spacing-md);
  background: var(--bg-dark);
  border: 1px solid var(--border-color);
  border-radius: var(--radius-md);
  color: var(--text-primary);
  font-size: 0.85rem;
}

.kg-search-box input:focus {
  outline: none;
  border-color: var(--color-primary);
}

.kg-search-results {
  position: absolute;
  top: 100%;
  left: 0;
  width: 100%;
  background: var(--bg-card);
  border: 1px solid var(--border-color);
  border-radius: var(--radius-md);
  max-height: 200px;
  overflow-y: auto;
  z-index: 200;
  box-shadow: 0 8px 32px rgba(0, 0, 0, 0.4);
  display: block;
}


.kg-search-result-item {
  padding: var(--spacing-sm) var(--spacing-md);
  cursor: pointer;
  display: flex;
  justify-content: space-between;
  align-items: center;
  transition: background var(--transition-fast);
  font-size: 0.85rem;
}

.kg-search-result-item:hover {
  background: var(--bg-dark);
}

.kg-search-result-item.kg-no-results {
  color: var(--text-dim);
  cursor: default;
}

.kg-result-name {
  color: var(--text-primary);
  font-size: 0.85rem;
}

.kg-result-type {
  color: var(--text-dim);
  font-size: 0.7rem;
}

.kg-selected-nodes {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-xs);
  max-height: 200px;
  overflow-y: auto;
  margin-top: var(--spacing-sm);
}

.kg-selected-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-size: 0.7rem;
  color: var(--text-dim);
  margin-bottom: var(--spacing-xs);
}

.kg-empty-hint {
  color: var(--text-dim);
  font-size: 0.8rem;
  text-align: center;
  padding: var(--spacing-xs);
}

.kg-selected-node {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: var(--spacing-xs) var(--spacing-sm);
  background: rgba(0, 229, 204, 0.1);
  border: 1px solid var(--color-primary-dim);
  border-radius: var(--radius-sm);
  transition: all var(--transition-fast);
}

.kg-selected-node:hover {
  background: rgba(0, 229, 204, 0.2);
  border-color: var(--color-primary);
}

.kg-selected-node-name {
  font-size: 0.8rem;
  color: var(--color-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.kg-selected-node-remove {
  width: 18px;
  height: 18px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: transparent;
  border: 1px solid var(--color-primary-dim);
  border-radius: 50%;
  color: var(--color-primary);
  font-size: 0.6rem;
  cursor: pointer;
  transition: all var(--transition-fast);
  flex-shrink: 0;
}

.kg-selected-node-remove:hover {
  background: var(--color-primary);
  color: var(--bg-deep);
}

.kg-selected-count {
  color: var(--color-primary);
  font-size: 0.7rem;
  font-weight: 600;
}

.kg-stats-inline {
  display: flex;
  gap: var(--spacing-md);
  margin-top: var(--spacing-sm);
  font-size: 0.8rem;
}

.kg-stats-inline span {
  color: var(--text-secondary);
}

.kg-stats-inline .kg-stat-num {
  font-family: var(--font-mono);
  color: var(--color-primary);
}

.kg-level-control {
  display: flex;
  gap: var(--spacing-xs);
}

.kg-level-btn {
  flex: 1;
  padding: var(--spacing-xs) var(--spacing-sm);
  background: var(--bg-dark);
  border: 1px solid var(--border-color);
  border-radius: var(--radius-sm);
  color: var(--text-secondary);
  font-size: 0.8rem;
  cursor: pointer;
  transition: all var(--transition-fast);
}

.kg-level-btn:hover {
  border-color: var(--color-primary-dim);
}

.kg-level-btn.active {
  background: var(--color-primary);
  border-color: var(--color-primary);
  color: var(--bg-deep);
  font-weight: 500;
}

.kg-rel-search-box {
  position: relative;
}

.kg-rel-search-box input {
  width: 100%;
  padding: var(--spacing-sm) var(--spacing-md);
  background: var(--bg-dark);
  border: 1px solid var(--border-color);
  border-radius: var(--radius-md);
  color: var(--text-primary);
  font-size: 0.85rem;
}

.kg-rel-search-box input:focus {
  outline: none;
  border-color: var(--color-primary);
}

.kg-rel-search-results {
  position: absolute;
  top: 100%;
  left: 0;
  width: 100%;
  background: var(--bg-card);
  border: 1px solid var(--border-color);
  border-radius: var(--radius-md);
  max-height: 200px;
  overflow-y: auto;
  z-index: 200;
  box-shadow: 0 8px 32px rgba(0, 0, 0, 0.4);
  display: block;
}

.kg-rel-search-item {
  padding: var(--spacing-sm) var(--spacing-md);
  cursor: pointer;
  display: flex;
  align-items: center;
  gap: var(--spacing-sm);
  transition: background var(--transition-fast);
  font-size: 0.85rem;
  color: var(--text-primary);
}

.kg-rel-search-item:hover {
  background: var(--bg-dark);
}

.kg-rel-search-item.active {
  background: rgba(0, 229, 204, 0.1);
  color: var(--color-primary);
}

.kg-rel-search-item.kg-no-results {
  color: var(--text-dim);
  cursor: default;
}

.kg-relation-chips {
  display: flex;
  flex-wrap: wrap;
  gap: var(--spacing-xs);
}

.kg-relation-chip {
  padding: 2px 8px;
  background: var(--bg-dark);
  border: 1px solid var(--border-color);
  border-radius: 12px;
  font-size: 0.75rem;
  color: var(--text-secondary);
  cursor: pointer;
  transition: all var(--transition-fast);
}

.kg-relation-chip:hover {
  border-color: var(--color-primary-dim);
  color: var(--text-primary);
}

.kg-relation-chip.active {
  background: var(--color-primary);
  border-color: var(--color-primary);
  color: var(--bg-deep);
}

.kg-reset-btn {
  width: 100%;
  padding: var(--spacing-sm) var(--spacing-md);
  background: transparent;
  border: 1px solid var(--border-color);
  border-radius: var(--radius-md);
  color: var(--text-secondary);
  font-size: 0.85rem;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: var(--spacing-sm);
  transition: all var(--transition-fast);
}

.kg-reset-btn:hover {
  border-color: var(--color-primary);
  color: var(--color-primary);
}

.kg-reset-btn span {
  font-size: 0.9rem;
}
</style>
