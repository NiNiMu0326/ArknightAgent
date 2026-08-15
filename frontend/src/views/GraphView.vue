<template>
  <div class="graph-page">
    <!-- Graph Container -->
    <div id="kg-graph" ref="graphContainer"></div>

    <!-- Legend Panel -->
    <div class="kg-legend-panel" v-if="currentNodeTypes.length > 0">
      <div class="kg-legend-panel-title">图例</div>
      <div class="kg-legend-panel-content">
        <div v-for="type in currentNodeTypes" :key="type" class="kg-legend-item">
          <span class="kg-legend-color" :style="{ background: nodeColors[type] || nodeColors.default }"></span>
          <span>{{ type }}</span>
        </div>
        <div class="kg-legend-item">
          <span class="kg-legend-border kg-legend-border-selected"></span>
          <span>选中节点</span>
        </div>
      </div>
    </div>

    <!-- Edge Info Panel -->
    <div class="kg-edge-info" :class="{ active: controller.selectedEdge.value }">
      <div class="kg-edge-info-header">
        <span class="kg-edge-info-title">关系详情</span>
        <button class="kg-edge-info-close" @click="controller.selectedEdge.value = null">X</button>
      </div>
      <div class="kg-edge-info-body" v-if="controller.selectedEdge.value">
        <div class="kg-edge-info-item">
          <span class="kg-edge-info-label">起点</span>
          <span class="kg-edge-info-value">{{ controller.selectedEdge.value.source }}</span>
        </div>
        <div class="kg-edge-info-item">
          <span class="kg-edge-info-label">关系</span>
          <span class="kg-edge-info-value kg-edge-relation">{{ controller.selectedEdge.value.relation }}</span>
        </div>
        <div class="kg-edge-info-item">
          <span class="kg-edge-info-label">终点</span>
          <span class="kg-edge-info-value">{{ controller.selectedEdge.value.target }}</span>
        </div>
        <div class="kg-edge-info-item kg-edge-info-desc" v-if="controller.selectedEdge.value.description">
          <span class="kg-edge-info-label">描述</span>
          <span class="kg-edge-info-value">{{ controller.selectedEdge.value.description }}</span>
        </div>
      </div>
    </div>

    <!-- Zoom Controls -->
    <div class="kg-zoom-controls">
      <button class="kg-zoom-btn" @click="zoomIn">+</button>
      <span class="kg-zoom-level">{{ zoomLevel }}%</span>
      <button class="kg-zoom-btn" @click="zoomOut">-</button>
      <button class="kg-zoom-btn" @click="fitView">F</button>
    </div>

    <!-- Loading State -->
    <div class="kg-loading" v-if="controller.loading.value">
      <div class="kg-loading-spinner"></div>
      <div class="kg-loading-text">加载中...</div>
    </div>

    <!-- Empty State -->
    <div class="kg-empty" v-if="!controller.loading.value && controller.selectedNodes.value.length === 0">
      <div class="kg-empty-title">选择节点查看知识图谱</div>
      <div class="kg-empty-hint">在左侧搜索或点击图上节点</div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted, onUnmounted, onActivated, watch } from 'vue'
import cytoscape from 'cytoscape'
import { useGraphController } from '../composables/useGraphController'
import { countEntities } from '../composables/useGraphController'
import { useSettingsStore } from '../stores/settings'

const controller = useGraphController()
const settingsStore = useSettingsStore()

const graphContainer = ref(null)
const zoomLevel = ref(100)
const currentNodeTypes = ref([])

let cy = null

// Node colors
const nodeColors = {
  '干员': '#00e5cc',
  '敌人': '#ff4757',
  '物品': '#ffca28',
  '地点': '#9b59b6',
  '组织': '#3498db',
  '种族': '#e67e22',
  '职业': '#1abc9c',
  '特性': '#95a5a6',
  '阶段': '#34495e',
  'default': '#8ba3a0'
}

// 从 CSS 变量读取主题色，让图谱文字/标签底随深浅主题切换
function cssVar(name, fallback) {
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim()
  return v || fallback
}

// Cytoscape styles（每次 init 时基于当前主题重新计算）
function buildCytoscapeStyles() {
  const labelColor = cssVar('--text-primary', '#ffffff')
  const labelBg = cssVar('--bg-panel', '#1a1a2e')
  // 选中描边需与画布背景（--bg-deep）形成对比，故用主文本色
  const selectedBorder = labelColor
  const nodeStyles = [
    { selector: 'node', style: { 'label': 'data(label)', 'text-valign': 'bottom', 'text-margin-y': 10, 'font-size': '14px', 'font-weight': '600', 'color': labelColor, 'text-background-color': labelBg, 'text-background-opacity': 0.9, 'text-background-padding': '4px', 'text-background-shape': 'roundrectangle', 'background-color': '#00e5cc', 'width': 'data(size)', 'height': 'data(size)', 'border-width': 3, 'border-color': '#00e5cc', 'opacity': 0.9 } },
    { selector: 'node:selected', style: { 'border-width': 4, 'border-color': selectedBorder, 'opacity': 1 } },
    { selector: 'node.selected-node', style: { 'border-width': 4, 'border-color': selectedBorder, 'opacity': 1, 'z-index': 10 } },
    { selector: 'node.root-node', style: { 'border-width': 5, 'border-color': '#ffd700', 'opacity': 1, 'z-index': 20 } },
    { selector: 'node.neighbor', style: { 'border-color': '#ffc107', 'border-width': 2 } }
  ]
  const edgeStyles = [
    { selector: 'edge', style: { 'width': 2, 'line-color': '#3a3a5a', 'target-arrow-color': '#3a3a5a', 'target-arrow-shape': 'triangle', 'curve-style': 'bezier', 'opacity': 0.4 } },
    { selector: 'edge.highlighted', style: { 'width': 3, 'opacity': 1, 'z-index': 5 } },
    { selector: 'edge.selected-relation', style: { 'width': 4, 'opacity': 1, 'z-index': 10, 'line-color': '#ffd700' } },
    { selector: 'edge.relation-hovered', style: { 'width': 4, 'opacity': 1, 'z-index': 15 } },
    { selector: 'edge.relation-dimmed', style: { 'opacity': 0.08 } }
  ]
  return [...nodeStyles, ...edgeStyles]
}

// ============ Init ============
function initGraph() {
  if (!graphContainer.value) return

  cy = cytoscape({
    container: graphContainer.value,
    style: buildCytoscapeStyles(),
    layout: { name: 'circle', padding: 50 },
    wheelSensitivity: 0.5,
    minZoom: 0.3,
    maxZoom: 3
  })

  // Click node
  cy.on('tap', 'node', (evt) => {
    const nodeId = evt.target.id()
    toggleNodeSelection(nodeId)
  })

  // Click edge
  cy.on('tap', 'edge', (evt) => {
    const edge = evt.target
    showEdgeInfo(edge)
  })

  // Click background
  cy.on('tap', (evt) => {
    if (evt.target === cy) {
      controller.selectedEdge.value = null
    }
  })

  // Zoom event
  cy.on('zoom', () => {
    zoomLevel.value = Math.round((cy.zoom() || 1) * 100)
  })
}

// ============ Node Selection ============
function toggleNodeSelection(nodeId) {
  const nodes = controller.selectedNodes.value
  if (nodes.includes(nodeId)) {
    controller.removeNodeSelection(nodeId)
  } else {
    controller.addNodeSelection(nodeId)
  }
}

function showEdgeInfo(edge) {
  const source = edge.source().id()
  const target = edge.target().id()
  const relation = edge.data('relation')
  const description = edge.data('description') || ''

  controller.selectedEdge.value = { source, target, relation, description }
}

// ============ Graph Update ============
function updateGraph() {
  if (!cy || !controller.graphData.value) return

  const { graphData, selectedNodes, selectedRelations, neighborLevel } = controller

  // Show empty state if no selection
  if (selectedNodes.value.length === 0) {
    cy.elements().remove()
    controller.updateCurrentStats(0, 0)
    currentNodeTypes.value = []
    return
  }

  // Build subgraph
  const subgraph = buildSubgraph()

  // Update cytoscape
  cy.elements().remove()
  if (subgraph.nodes.length > 0) {
    cy.add(subgraph.nodes)
    cy.add(subgraph.edges)

    // Run layout
    const layout = cy.layout({
      name: 'cose',
      animate: true,
      animationDuration: 500,
      padding: 80,
      nodeRepulsion: 8000,
      idealEdgeLength: 120,
      edgeElasticity: 100
    })
    layout.run()

    applyNodeStyles()
    applyEdgeStyles()
    applyEdgeHoverStyles()
    controller.updateCurrentStats(subgraph.nodes.length, subgraph.edges.length)
    currentNodeTypes.value = [...new Set(subgraph.nodes.map(n => n.data.type))]
  }

  controller.selectedEdge.value = null
}

function buildSubgraph() {
  const { graphData, selectedNodes, selectedRelations, neighborLevel } = controller
  const nodes = new Map()
  const edges = []
  const allNeighborIds = new Set()
  const relationFilter = controller.relationFilterActive.value
    ? new Set(selectedRelations.value)
    : null

  for (let level = 0; level < neighborLevel.value; level++) {
    const currentLevelNodes = level === 0 ? new Set(selectedNodes.value) : allNeighborIds
    const nextLevelNodes = new Set()

    currentLevelNodes.forEach(nodeId => {
      ;(graphData.value.relations || []).forEach(r => {
        // Skip if relation filter is active and this relation is not selected
        if (relationFilter && !relationFilter.has(r.relation)) return

        if (r.source === nodeId) {
          nextLevelNodes.add(r.target)
        } else if (r.target === nodeId) {
          nextLevelNodes.add(r.source)
        } else {
          return
        }
        edges.push({
          group: 'edges',
          data: {
            id: `edge-${r.source}-${r.target}-${r.relation}`,
            source: r.source,
            target: r.target,
            relation: r.relation,
            description: r.description || ''
          }
        })
      })
    })

    currentLevelNodes.forEach(nodeId => allNeighborIds.add(nodeId))
    nextLevelNodes.forEach(nodeId => allNeighborIds.add(nodeId))
  }

  // Dedupe edges
  const uniqueEdges = []
  const seenEdges = new Set()
  edges.forEach(e => {
    const edgeId = `${e.data.source}-${e.data.target}-${e.data.relation}`
    if (!seenEdges.has(edgeId)) {
      seenEdges.add(edgeId)
      uniqueEdges.push(e)
    }
  })

  // If relation filter active, remove orphaned nodes (not connected to selected nodes via allowed relations)
  let finalNodeIds = allNeighborIds
  if (relationFilter) {
    const selectedSet = new Set(selectedNodes.value)
    const connectedNodes = new Set(selectedNodes.value)
    uniqueEdges.forEach(e => {
      connectedNodes.add(e.data.source)
      connectedNodes.add(e.data.target)
    })
    finalNodeIds = connectedNodes
  }

  // Build nodes
  const entitiesDict = graphData.value.entities || {}
  finalNodeIds.forEach(nodeId => {
    // Look up type from entities dict: {"干员": ["银灰", ...], ...}
    let type = '干员'
    if (typeof entitiesDict === 'object' && !Array.isArray(entitiesDict)) {
      for (const [entityType, names] of Object.entries(entitiesDict)) {
        if (names.includes(nodeId)) {
          type = entityType
          break
        }
      }
    }
    const connections = uniqueEdges.filter(
      r => r.data.source === nodeId || r.data.target === nodeId
    ).length
    const size = Math.min(60, Math.max(30, 25 + connections * 3))

    nodes.set(nodeId, {
      group: 'nodes',
      data: {
        id: nodeId,
        label: nodeId,
        type: type,
        size: size
      }
    })
  })

  // Sort by degree descending
  const sortedNodes = Array.from(nodes.values()).sort((a, b) => b.data.size - a.data.size)

  return { nodes: sortedNodes, edges: uniqueEdges }
}

function applyNodeStyles() {
  const { selectedNodes } = controller

  cy.nodes().forEach(node => {
    const nodeId = node.id()
    const type = node.data('type')
    const color = nodeColors[type] || nodeColors.default

    if (selectedNodes.value.includes(nodeId)) {
      node.addClass('root-node')
      node.style('background-color', color)
    } else {
      node.removeClass('root-node')
      node.addClass('selected-node')
      node.style('background-color', color)
    }
  })
}

function applyEdgeStyles() {
  const { selectedRelations, relationColors } = controller

  cy.edges().forEach(edge => {
    const relation = edge.data('relation')
    const isHighlighted = selectedRelations.value.includes(relation)

    if (controller.relationFilterActive.value) {
      if (isHighlighted) {
        edge.addClass('selected-relation')
        edge.style('line-color', relationColors[relation] || '#00e5cc')
      } else {
        edge.removeClass('selected-relation')
        edge.style('line-color', '#3a3a5a')
        edge.style('opacity', 0.15)
      }
    } else {
      edge.removeClass('selected-relation')
      edge.style('line-color', relationColors[relation] || '#00e5cc')
      edge.style('opacity', 0.5)
    }
  })
}

function applyEdgeHoverStyles() {
  if (!cy) return
  const { hoveredRelation, relationColors } = controller

  cy.edges().forEach(edge => {
    edge.removeClass('relation-hovered relation-dimmed')
    if (!hoveredRelation.value) return

    const relation = edge.data('relation')
    if (relation === hoveredRelation.value) {
      edge.addClass('relation-hovered')
      edge.style('line-color', relationColors[relation] || '#00e5cc')
    } else {
      edge.addClass('relation-dimmed')
    }
  })
}

// ============ Zoom ============
// zoomLevel 由 initGraph 中的 cy 'zoom' 事件统一更新，此处无需手动赋值
function zoomIn() {
  if (cy) cy.zoom(cy.zoom() * 1.2)
}

function zoomOut() {
  if (cy) cy.zoom(cy.zoom() * 0.8)
}

function fitView() {
  if (cy) cy.fit(undefined, 50)
}

// ============ Lifecycle ============
onMounted(async () => {
  initGraph()
  await controller.loadGraphData()
  updateGraph()
})

// keep-alive 恢复时重建 cytoscape（DOM 已重建）
onActivated(() => {
  if (cy) {
    cy.destroy()
    cy = null
  }
  initGraph()
  updateGraph()
})

onUnmounted(() => {
  if (cy) {
    cy.destroy()
    cy = null
  }
})

// Watch for changes
watch(() => controller.selectedNodes.value, (newVal) => {
  updateGraph()
})  // No deep: true needed - selectedNodes uses array replacement, not in-place mutation
watch(() => controller.neighborLevel.value, updateGraph)
watch(() => controller.selectedRelations.value, updateGraph, { deep: true })  // Needs deep: true - uses .splice()/.push()
watch(() => controller.hoveredRelation.value, applyEdgeHoverStyles)
watch(() => controller.graphData.value, () => {
  updateGraph()
})  // Re-render when graph data finishes loading (e.g. sidebar-triggered async load after activation)

// 主题切换时重建图谱，让节点标签/描边颜色适配新主题
watch(() => settingsStore.theme, () => {
  if (cy) {
    cy.destroy()
    cy = null
  }
  initGraph()
  updateGraph()
})
</script>

<style scoped>
.graph-page {
  position: relative;
  width: 100%;
  flex: 1;
  min-height: 0;
  background: var(--bg-deep);
}

#kg-graph {
  width: 100%;
  height: 100%;
  position: absolute;
  top: 0;
  left: 0;
}

/* Legend Panel */
.kg-legend-panel {
  position: absolute;
  top: var(--spacing-lg);
  right: var(--spacing-lg);
  background: var(--bg-card);
  border: 1px solid var(--border-color);
  border-radius: var(--radius-md);
  padding: var(--spacing-sm);
  max-width: 220px;
  z-index: 50;
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.3);
}

.kg-legend-panel-title {
  font-size: 0.75rem;
  color: var(--text-dim);
  text-transform: uppercase;
  letter-spacing: 0.05em;
  margin-bottom: var(--spacing-xs);
  font-weight: 600;
}

.kg-legend-panel-content {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 4px;
}

.kg-legend-item {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 0.7rem;
  color: var(--text-secondary);
  padding: 2px 0;
}

.kg-legend-color {
  width: 14px;
  height: 14px;
  border-radius: 3px;
  flex-shrink: 0;
}

.kg-legend-border {
  width: 14px;
  height: 14px;
  border-radius: 3px;
  background: var(--bg-dark);
  border: 3px solid;
  flex-shrink: 0;
}

.kg-legend-border-selected {
  border-color: #ffd700;
}

.kg-legend-border-neighbor {
  border-color: #ffc107;
}

/* Edge Info Panel */
.kg-edge-info {
  position: absolute;
  top: var(--spacing-lg);
  left: var(--spacing-lg);
  width: 187px;
  background: var(--bg-card);
  border: 1px solid var(--border-color);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-md);
  opacity: 0;
  visibility: hidden;
  transform: translateY(-10px);
  transition: all var(--transition-normal);
  z-index: 50;
  pointer-events: none;
}

.kg-edge-info.active {
  opacity: 1;
  visibility: visible;
  transform: translateY(0);
  pointer-events: auto;
  transition: all var(--transition-normal);
}

.kg-edge-info-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: var(--spacing-md);
  border-bottom: 1px solid var(--border-color);
}

.kg-edge-info-title {
  font-size: 0.85rem;
  font-weight: 500;
  color: var(--text-primary);
}

.kg-edge-info-close {
  width: 24px;
  height: 24px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: transparent;
  border: none;
  border-radius: var(--radius-sm);
  color: var(--text-dim);
  font-size: 0.8rem;
  cursor: pointer;
  transition: all var(--transition-fast);
}

.kg-edge-info-close:hover {
  background: var(--bg-dark);
  color: var(--text-primary);
}

.kg-edge-info-body {
  padding: var(--spacing-md);
  user-select: text;
  cursor: text;
}

.kg-edge-info-item {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  padding: var(--spacing-sm) 0;
  border-bottom: 1px solid var(--border-color);
}

.kg-edge-info-item:last-child {
  border-bottom: none;
}

.kg-edge-info-label {
  font-size: 0.75rem;
  color: var(--text-dim);
  flex-shrink: 0;
}

.kg-edge-info-value {
  font-size: 0.8rem;
  color: var(--text-primary);
  text-align: right;
  word-break: break-word;
}

.kg-edge-relation {
  padding: 2px 8px;
  background: rgba(0, 229, 204, 0.1);
  border: 1px solid var(--color-primary);
  border-radius: var(--radius-sm);
  color: var(--color-primary) !important;
}

.kg-edge-info-desc .kg-edge-info-value {
  color: var(--text-secondary);
  font-size: 0.75rem;
  text-align: left;
  margin-top: var(--spacing-xs);
}

/* Zoom Controls */
.kg-zoom-controls {
  position: absolute;
  bottom: var(--spacing-lg);
  right: var(--spacing-lg);
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: var(--spacing-xs);
  background: var(--bg-card);
  border: 1px solid var(--border-color);
  border-radius: var(--radius-md);
  padding: var(--spacing-xs);
}

.kg-zoom-btn {
  width: 32px;
  height: 32px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: transparent;
  border: none;
  border-radius: var(--radius-sm);
  color: var(--text-secondary);
  font-size: 1rem;
  cursor: pointer;
  transition: all var(--transition-fast);
}

.kg-zoom-btn:hover {
  background: var(--bg-dark);
  color: var(--color-primary);
}

.kg-zoom-level {
  font-family: var(--font-mono);
  font-size: 0.7rem;
  color: var(--text-dim);
  padding: var(--spacing-xs);
}

/* Loading State */
.kg-loading {
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  background: var(--bg-deep);
  gap: var(--spacing-md);
}

.kg-loading-spinner {
  width: 40px;
  height: 40px;
  border: 3px solid var(--border-color);
  border-top-color: var(--color-primary);
  border-radius: 50%;
  animation: kg-spin 1s linear infinite;
}

@keyframes kg-spin {
  to { transform: rotate(360deg); }
}

.kg-loading-text {
  color: var(--text-dim);
  font-size: 0.9rem;
}

/* Empty State */
.kg-empty {
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: var(--spacing-md);
}

.kg-empty-title {
  font-size: 1.2rem;
  color: var(--text-secondary);
}

.kg-empty-hint {
  font-size: 0.85rem;
  color: var(--text-dim);
}

/* Mobile responsive */
@media (max-width: 768px) {
  .kg-zoom-controls {
    bottom: calc(var(--spacing-md) + env(safe-area-inset-bottom));
    right: var(--spacing-md);
  }
  .kg-zoom-btn {
    width: 40px;
    height: 40px;
    font-size: 1.1rem;
  }
  .kg-edge-info {
    top: var(--spacing-md);
    left: var(--spacing-md);
    right: var(--spacing-md);
    width: auto;
    max-width: none;
  }
  .kg-empty-title {
    font-size: 1rem;
    padding: 0 var(--spacing-lg);
    text-align: center;
  }
  .kg-empty-hint {
    padding: 0 var(--spacing-lg);
    text-align: center;
  }
}
</style>
