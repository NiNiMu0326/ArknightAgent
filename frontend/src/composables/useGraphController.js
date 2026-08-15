import { ref, reactive } from 'vue'
import { api } from '../api'

// Helper: flatten entities dict to array for search/display
function flattenEntities(entities) {
  if (!entities) return []
  // New dict format: {"干员": ["银灰", ...], "组织": ["罗德岛", ...]}
  if (typeof entities === 'object' && !Array.isArray(entities)) {
    return Object.entries(entities).flatMap(([type, names]) =>
      (names || []).map(name => ({ entity: name, type }))
    )
  }
  // Old array format fallback
  return Array.isArray(entities) ? entities : []
}

// Helper: count total entities in dict format
function countEntities(entities) {
  if (!entities) return 0
  if (typeof entities === 'object' && !Array.isArray(entities)) {
    return Object.values(entities).reduce((sum, arr) => sum + (arr?.length || 0), 0)
  }
  return Array.isArray(entities) ? entities.length : 0
}

// Helper: check if entities data is empty
function isEntitiesEmpty(entities) {
  return !entities || countEntities(entities) === 0
}

// Singleton state shared between AppSidebar and GraphView
const graphData = ref({ entities: {}, relations: [] })
const selectedNodes = ref([])
const selectedRelations = ref([])
// null/未启用：显示全部关系；用户点过关系 chip 后启用筛选，
// 此时 selectedRelations 为空数组表示“不显示任何关系”
const relationFilterActive = ref(false)
const neighborLevel = ref(1)
const searchQuery = ref('')
const searchResults = ref([])
const searchFocused = ref(false)
const relSearchQuery = ref('')
const relSearchResults = ref([])
const relSearchFocused = ref(false)
const availableRelations = ref([])
const hoveredRelation = ref(null)
const loading = ref(false)
const selectedEdge = ref(null)
const stats = reactive({ nodes: 0, edges: 0 })
const currentStats = reactive({ nodes: 0, edges: 0 })

// Relation colors for highlighting
const relationColors = {}
const colorPalette = [
  '#00e5cc', '#ff4757', '#ffca28', '#7c4dff', '#00bcd4',
  '#ff6b9d', '#ffd93d', '#6bcb77', '#4d96ff', '#ff8c42'
]

export function useGraphController() {
  // Load graph data
  async function loadGraphData() {
    try {
      loading.value = true
      const data = await api.getGraphData()
      graphData.value = data
      prepareRelationColors()
      updateStats()
    } catch (error) {
      console.error('Failed to load graph data:', error)
    } finally {
      loading.value = false
    }
  }

  function prepareRelationColors() {
    const relationTypes = new Set()
    ;(graphData.value.relations || []).forEach(r => {
      relationTypes.add(r.relation)
    })
    let colorIndex = 0
    relationTypes.forEach(type => {
      relationColors[type] = colorPalette[colorIndex % colorPalette.length]
      colorIndex++
    })
  }

  function updateStats() {
    stats.nodes = countEntities(graphData.value.entities)
    stats.edges = graphData.value.relations?.length || 0
  }

  function updateCurrentStats(nodes, edges) {
    currentStats.nodes = nodes
    currentStats.edges = edges
  }

  // Node selection
  function addNodeSelection(nodeId) {
    if (!selectedNodes.value.includes(nodeId)) {
      selectedNodes.value = [...selectedNodes.value, nodeId]
    }
    searchQuery.value = ''
    searchResults.value = []
    searchFocused.value = false
    updateAvailableRelations()
  }

  function removeNodeSelection(nodeId) {
    selectedNodes.value = selectedNodes.value.filter(id => id !== nodeId)
    updateAvailableRelations()
  }

  function clearSelection() {
    selectedNodes.value = []
    selectedRelations.value = []
    relationFilterActive.value = false
    availableRelations.value = []
    selectedEdge.value = null
  }

  // Search
  function handleSearch(query) {
    searchQuery.value = query
    const entities = flattenEntities(graphData.value.entities)
    if (!query || query.length < 1) {
      searchResults.value = [...entities].sort((a, b) => a.entity.localeCompare(b.entity))
    } else {
      const q = query.toLowerCase()
      searchResults.value = entities
        .filter(e => e.entity.toLowerCase().includes(q))
        .sort((a, b) => a.entity.localeCompare(b.entity))
    }
  }

  function onSearchFocus() {
    searchFocused.value = true
    if (!searchQuery.value.trim()) {
      searchResults.value = flattenEntities(graphData.value.entities).sort((a, b) => a.entity.localeCompare(b.entity))
    }
  }

  function onSearchBlur() {
    searchFocused.value = false
    // 清空搜索结果，避免失去焦点后下拉框仍然显示
    searchResults.value = []
  }

  // Relation search
  function handleRelSearch(query) {
    relSearchQuery.value = query
    const relationTypes = Object.keys(relationColors).sort((a, b) => a.localeCompare(b))

    if (!query || query.length < 1) {
      relSearchResults.value = relationTypes.slice(0, 50)
    } else {
      const q = query.toLowerCase()
      relSearchResults.value = relationTypes.filter(type =>
        type.toLowerCase().includes(q)
      ).slice(0, 50)
    }
  }

  function onRelSearchFocus() {
    relSearchFocused.value = true
    if (!relSearchQuery.value.trim()) {
      relSearchResults.value = Object.keys(relationColors).sort((a, b) => a.localeCompare(b)).slice(0, 50)
    }
  }

  function onRelSearchBlur() {
    relSearchFocused.value = false
    // 清空关系搜索结果，避免失去焦点后下拉框仍然显示
    relSearchResults.value = []
  }

  function toggleRelationSelection(rel) {
    const idx = selectedRelations.value.indexOf(rel)
    if (idx >= 0) {
      selectedRelations.value.splice(idx, 1)
    } else {
      selectedRelations.value.push(rel)
    }
    relationFilterActive.value = true
  }

  function updateAvailableRelations() {
    if (selectedNodes.value.length === 0) {
      availableRelations.value = []
      relationFilterActive.value = false
      return
    }
    const nodeSet = new Set(selectedNodes.value)
    const relationTypes = new Set()
    ;(graphData.value.relations || []).forEach(r => {
      if (nodeSet.has(r.source) || nodeSet.has(r.target)) {
        relationTypes.add(r.relation)
      }
    })
    availableRelations.value = Array.from(relationTypes).sort((a, b) => a.localeCompare(b))
    // 选点变化时默认显示全部关系，重置筛选开关
    relationFilterActive.value = false
    selectedRelations.value = [...availableRelations.value]
  }

  // Level
  function setNeighborLevel(level) {
    neighborLevel.value = level
  }

  return {
    // State
    graphData,
    selectedNodes,
    selectedRelations,
    relationFilterActive,
    neighborLevel,
    searchQuery,
    searchResults,
    searchFocused,
    relSearchQuery,
    relSearchResults,
    relSearchFocused,
    availableRelations,
    hoveredRelation,
    loading,
    selectedEdge,
    stats,
    currentStats,
    relationColors,
    // Actions
    loadGraphData,
    updateCurrentStats,
    addNodeSelection,
    removeNodeSelection,
    clearSelection,
    handleSearch,
    onSearchFocus,
    onSearchBlur,
    handleRelSearch,
    onRelSearchFocus,
    onRelSearchBlur,
    toggleRelationSelection,
    updateAvailableRelations,
    setNeighborLevel
  }
}

export { flattenEntities, countEntities, isEntitiesEmpty }
