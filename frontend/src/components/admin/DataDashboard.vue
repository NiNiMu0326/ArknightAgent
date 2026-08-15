<template>
  <div class="tab-content">
    <div class="section-header">
      <h2 class="section-title">Data Dashboard</h2>
    </div>

    <!-- Stats Row -->
    <div class="stats-row" v-if="stats">
      <div class="stat-card">
        <div class="stat-label">OPERATORS</div>
        <div class="stat-value">{{ stats.operators }}<span class="stat-unit">条</span></div>
        <div class="stat-sub">干员数据</div>
      </div>
      <div class="stat-card">
        <div class="stat-label">STORIES</div>
        <div class="stat-value">{{ stats.stories }}<span class="stat-unit">篇</span></div>
        <div class="stat-sub">故事文本</div>
      </div>
      <div class="stat-card">
        <div class="stat-label">KNOWLEDGES</div>
        <div class="stat-value">{{ stats.knowledge }}<span class="stat-unit">条</span></div>
        <div class="stat-sub">知识条目</div>
      </div>
    </div>

    <div class="grid-2">
      <!-- Graph Stats -->
      <div class="panel">
        <div class="panel-header">
          <h3>知识图谱</h3>
        </div>
        <div class="panel-body">
          <div class="graph-stats-grid" v-if="graphData">
            <div class="graph-stat-mini">
              <div class="graph-stat-value">{{ entityCount }}</div>
              <div class="graph-stat-label">节点</div>
            </div>
            <div class="graph-stat-mini">
              <div class="graph-stat-value">{{ graphData.relations?.length || 0 }}</div>
              <div class="graph-stat-label">边</div>
            </div>
            <div class="graph-stat-mini">
              <div class="graph-stat-value">{{ relationTypesCount }}</div>
              <div class="graph-stat-label">关系类型</div>
            </div>
          </div>
          <div class="graph-stats-grid" v-else>
            <div class="graph-stat-mini"><div class="graph-stat-value">--</div><div class="graph-stat-label">节点</div></div>
            <div class="graph-stat-mini"><div class="graph-stat-value">--</div><div class="graph-stat-label">边</div></div>
            <div class="graph-stat-mini"><div class="graph-stat-value">--</div><div class="graph-stat-label">关系类型</div></div>
          </div>

          <div class="relation-type-section" v-if="pagedRelations.length > 0">
            <div class="relation-section-header">
              <div class="relation-section-left">
                <h4>关系类型</h4>
                <div class="relation-pagination" v-if="allRelationTypes.length > 5">
                  <button class="btn btn-small" :disabled="relationPage === 1" @click="relationPage--">&lt;</button>
                  <span class="relation-page-info">{{ relationPage }} / {{ totalRelationPages }}</span>
                  <button class="btn btn-small" :disabled="relationPage >= totalRelationPages" @click="relationPage++">&gt;</button>
                </div>
              </div>
            </div>
            <div class="relation-chart">
              <div class="vertical-bar-chart">
                <div v-for="item in pagedRelations" :key="item.type" class="vertical-bar-item clickable" @click="openRelationDetail(item.type)">
                  <div class="vertical-bar-value">{{ item.count }}</div>
                  <div class="vertical-bar-track">
                    <div class="vertical-bar-fill" :style="{ height: (pageMaxCount > 0 ? (item.count / pageMaxCount * 100) : 0) + '%' }"></div>
                  </div>
                  <div class="vertical-bar-label" :title="item.type">{{ item.type.length > 6 ? item.type.slice(0, 6) + '..' : item.type }}</div>
                </div>
              </div>
            </div>
          </div>
          <div v-else class="empty-state" style="padding: 40px;">
            <div class="empty-state-title">暂无关系数据</div>
          </div>
        </div>
      </div>

      <!-- Collection Stats -->
      <div class="panel">
        <div class="panel-header">
          <h3>数据分布</h3>
        </div>
        <div class="panel-body">
          <div class="pie-chart-container" v-if="stats">
            <div class="pie-chart" :style="{ background: pieChartGradient }"></div>
            <div class="pie-legend">
              <div class="pie-legend-item">
                <span class="pie-legend-color" style="background: #00e5c7"></span>
                <span class="pie-legend-name">Operators</span>
                <span class="pie-legend-value">{{ stats.operators }}</span>
              </div>
              <div class="pie-legend-item">
                <span class="pie-legend-color" style="background: #ff6b9d"></span>
                <span class="pie-legend-name">Stories</span>
                <span class="pie-legend-value">{{ stats.stories }}</span>
              </div>
              <div class="pie-legend-item">
                <span class="pie-legend-color" style="background: #7c5cff"></span>
                <span class="pie-legend-name">Knowledge</span>
                <span class="pie-legend-value">{{ stats.knowledge }}</span>
              </div>
            </div>
          </div>
          <div v-else class="empty-state" style="padding: 40px;">
            <div class="empty-state-title">暂无数据</div>
          </div>
        </div>
      </div>
    </div>

    <!-- 关系详情弹窗 -->
    <div class="modal-overlay" :class="{ active: showRelationDetailModal }" @click.self="showRelationDetailModal = false">
      <div class="modal-content relation-detail-modal">
        <div class="modal-header">
          <h2>{{ relationDetailType }}</h2>
          <span class="relation-detail-count">{{ relationDetailItems.length }} 条关系</span>
          <button class="modal-close" @click="showRelationDetailModal = false">&times;</button>
        </div>
        <div class="modal-body">
          <input type="text" class="input" v-model="relationDetailSearch" placeholder="搜索实体..." style="margin-bottom: var(--spacing-md);">
          <div class="relation-detail-list">
            <div v-for="(item, idx) in filteredRelationDetailItems" :key="idx" class="relation-detail-item" :class="{ expanded: item.expanded }" @click="item.expanded = !item.expanded">
              <div class="relation-detail-triple">
                <span class="relation-detail-entity">{{ item.source }}</span>
                <span class="relation-detail-arrow">&rarr;</span>
                <span class="relation-detail-rel">{{ item.relation }}</span>
                <span class="relation-detail-arrow">&rarr;</span>
                <span class="relation-detail-entity">{{ item.target }}</span>
                <span class="relation-detail-expand">{{ item.expanded ? '▲' : '▼' }}</span>
              </div>
              <div class="relation-detail-desc" v-if="item.expanded && item.description">
                {{ item.description }}
              </div>
            </div>
            <div v-if="filteredRelationDetailItems.length === 0" class="empty-state" style="padding: 20px;">
              <div class="empty-state-title">无匹配结果</div>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, watch, onMounted } from 'vue'
import { api } from '../../api'

const stats = ref(null)
const graphData = ref(null)

onMounted(() => {
  loadStats()
  loadGraphData()
})

async function loadStats() {
  try {
    stats.value = await api.getStats()
  } catch (e) {
    stats.value = null
  }
}

async function loadGraphData() {
  try {
    graphData.value = await api.getGraphData()
  } catch (e) {
    graphData.value = null
  }
}

// Computed properties for dashboard
const entityCount = computed(() => {
  const entities = graphData.value?.entities
  if (!entities) return 0
  if (typeof entities === 'object' && !Array.isArray(entities)) {
    return Object.values(entities).flat().length
  }
  return Array.isArray(entities) ? entities.length : 0
})

const relationTypesCount = computed(() => {
  if (!graphData.value?.relations) return 0
  const types = new Set(graphData.value.relations.map(r => r.relation))
  return types.size
})

const allRelationTypes = computed(() => {
  if (!graphData.value?.relations) return []
  const relationCounts = {}
  graphData.value.relations.forEach(r => {
    relationCounts[r.relation] = (relationCounts[r.relation] || 0) + 1
  })
  const sorted = Object.entries(relationCounts)
    .sort((a, b) => b[1] - a[1])
  const maxCount = sorted.length > 0 ? sorted[0][1] : 0
  return sorted.map(([type, count]) => ({ type, count, maxCount }))
})

const relationPage = ref(1)
const totalRelationPages = computed(() => Math.ceil(allRelationTypes.value.length / 5) || 1)

// 数据变少时夹紧页码，避免出现空分页（如 2 / 1）
watch(totalRelationPages, (total) => {
  if (relationPage.value > total) relationPage.value = total
})

const pagedRelations = computed(() => {
  const start = (relationPage.value - 1) * 5
  const items = allRelationTypes.value.slice(start, start + 5)
  // 始终使用全局最大值（第一页第一个）作为分母，保证各页柱状图比例一致
  const globalMax = allRelationTypes.value.length > 0 ? allRelationTypes.value[0].count : 0
  return items.map(item => ({ ...item, maxCount: globalMax }))
})

const pageMaxCount = computed(() => {
  // 始终使用全局最大值（第一页第一个）作为分母
  if (allRelationTypes.value.length === 0) return 0
  return allRelationTypes.value[0].count
})

// Relation detail modal
const showRelationDetailModal = ref(false)
const relationDetailType = ref('')
const relationDetailItems = ref([])
const relationDetailSearch = ref('')

function openRelationDetail(type) {
  relationDetailType.value = type
  relationDetailSearch.value = ''
  const items = (graphData.value?.relations || [])
    .filter(r => r.relation === type)
    .map(r => ({
      source: r.source || r.head || '',
      target: r.target || r.tail || '',
      relation: r.relation,
      description: r.description || r.desc || '',
      expanded: false
    }))
  relationDetailItems.value = items
  showRelationDetailModal.value = true
}

const filteredRelationDetailItems = computed(() => {
  if (!relationDetailSearch.value.trim()) return relationDetailItems.value
  const q = relationDetailSearch.value.toLowerCase()
  return relationDetailItems.value.filter(item =>
    item.source.toLowerCase().includes(q) ||
    item.target.toLowerCase().includes(q) ||
    item.description.toLowerCase().includes(q)
  )
})

const pieChartGradient = computed(() => {
  if (!stats.value) return 'conic-gradient(#ccc 0deg 360deg)'
  const data = [
    { name: 'Operators', value: stats.value.operators || 0, color: '#00e5c7' },
    { name: 'Stories', value: stats.value.stories || 0, color: '#ff6b9d' },
    { name: 'Knowledge', value: stats.value.knowledge || 0, color: '#7c5cff' }
  ]
  const total = data.reduce((sum, d) => sum + d.value, 0)
  if (total === 0) return 'conic-gradient(#ccc 0deg 360deg)'
  const gradients = data.map((d, i) => {
    const percent = (d.value / total * 100)
    const prevPercent = data.slice(0, i).reduce((sum, d) => sum + (d.value / total * 100), 0)
    return `${d.color} ${prevPercent}% ${prevPercent + percent}%`
  }).join(', ')
  return `conic-gradient(${gradients})`
})
</script>

<style scoped>
.tab-content { display: flex; flex-direction: column; flex: 1; min-height: 0; }
.section-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: var(--spacing-lg); }
.section-title { font-family: var(--font-display); font-size: 1.1rem; color: var(--text-primary); text-transform: uppercase; }
.btn-small { padding: var(--spacing-xs) var(--spacing-sm); font-size: 0.8rem; }
.empty-state { padding: var(--spacing-xl); text-align: center; color: var(--text-dim); }

.stats-row { display: grid; grid-template-columns: repeat(3, 1fr); gap: var(--spacing-lg); margin-bottom: var(--spacing-xl); }
.stat-card { background: linear-gradient(135deg, var(--bg-panel) 0%, var(--bg-card) 100%); border: 1px solid var(--border-color); border-radius: var(--radius-lg); padding: var(--spacing-lg); position: relative; overflow: hidden; transition: all var(--transition-fast); }
.stat-card::before { content: ''; position: absolute; top: 0; left: 0; right: 0; height: 3px; background: linear-gradient(90deg, var(--color-primary) 0%, transparent 100%); }
.stat-card:hover { border-color: var(--color-primary-dim); transform: translateY(-2px); box-shadow: 0 8px 30px rgba(0,0,0,0.3); }
.stat-label { font-size: 0.85rem; color: var(--text-secondary); text-transform: uppercase; letter-spacing: 0.1em; }
.stat-value { font-family: var(--font-display); font-size: 2rem; color: var(--color-primary); text-shadow: 0 0 20px var(--color-primary-glow); }
.stat-unit { font-family: var(--font-display); font-size: 2rem; color: var(--color-primary); text-shadow: 0 0 20px var(--color-primary-glow); margin-left: 2px; }
.stat-sub { font-size: 0.8rem; color: var(--text-secondary); margin-top: 2px; }

/* Dashboard styles */
.grid-2 { display: grid; grid-template-columns: 1fr 1fr; gap: var(--spacing-xl); margin-top: var(--spacing-xl); }
.graph-stats-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: var(--spacing-md); margin-bottom: var(--spacing-lg); }
.graph-stat-mini { background: var(--bg-card); border: 1px solid var(--border-color); border-radius: var(--radius-md); padding: var(--spacing-md); text-align: center; }
.graph-stat-value { font-family: var(--font-display); font-size: 1.5rem; color: var(--color-primary); }
.graph-stat-label { font-size: 0.75rem; color: var(--text-secondary); margin-top: var(--spacing-xs); }
.relation-type-section h4 { font-size: 0.85rem; color: var(--text-secondary); margin: 0; }
.relation-section-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: var(--spacing-md); }
.relation-section-left { display: flex; align-items: center; gap: var(--spacing-md); }
.relation-pagination { display: flex; align-items: center; gap: var(--spacing-xs); }
.relation-page-info { font-family: var(--font-mono); font-size: 0.8rem; color: var(--text-dim); }
.vertical-bar-item.clickable { cursor: pointer; transition: transform var(--transition-fast); }
.vertical-bar-item.clickable:hover { transform: scale(1.05); }
.vertical-bar-item.clickable:hover .vertical-bar-fill { box-shadow: 0 0 10px var(--color-primary-glow); }

/* Relation detail modal */
.relation-detail-modal { width: 600px; max-height: 85vh; }
.relation-detail-count { font-size: 0.8rem; color: var(--text-dim); margin-left: var(--spacing-sm); }
.relation-detail-list { display: flex; flex-direction: column; gap: 4px; max-height: 50vh; overflow-y: auto; }
.relation-detail-item { background: var(--bg-card); border: 1px solid var(--border-color); border-radius: var(--radius-sm); padding: var(--spacing-sm) var(--spacing-md); cursor: pointer; transition: all var(--transition-fast); }
.relation-detail-item:hover { border-color: var(--color-primary-dim); }
.relation-detail-item.expanded { border-color: var(--color-primary); }
.relation-detail-triple { display: flex; align-items: center; gap: var(--spacing-sm); font-size: 0.85rem; flex-wrap: wrap; }
.relation-detail-entity { color: var(--color-primary); font-weight: 500; }
.relation-detail-arrow { color: var(--text-dim); font-size: 0.75rem; }
.relation-detail-rel { color: var(--text-secondary); background: var(--bg-panel); padding: 1px 6px; border-radius: var(--radius-sm); font-size: 0.8rem; }
.relation-detail-expand { margin-left: auto; font-size: 0.7rem; color: var(--text-dim); }
.relation-detail-desc { margin-top: var(--spacing-sm); padding-top: var(--spacing-sm); border-top: 1px solid var(--border-color); font-size: 0.8rem; color: var(--text-secondary); line-height: 1.6; }
.relation-chart { height: 280px; overflow-y: auto; }
.vertical-bar-chart { display: flex; align-items: flex-end; justify-content: flex-start; height: 220px; padding: var(--spacing-md); gap: var(--spacing-sm); }
.vertical-bar-item { display: flex; flex-direction: column; align-items: center; flex: 1; max-width: 60px; height: 100%; }
.vertical-bar-value { font-family: var(--font-mono); font-size: 0.7rem; color: var(--color-primary); margin-bottom: var(--spacing-xs); }
.vertical-bar-track { flex: 1; width: 100%; background: var(--bg-dark); border-radius: var(--radius-sm) var(--radius-sm) 0 0; overflow: hidden; display: flex; align-items: flex-end; }
.vertical-bar-fill { width: 100%; background: linear-gradient(180deg, var(--color-primary) 0%, var(--color-primary-dim) 100%); transition: height var(--transition-normal); border-radius: var(--radius-sm) var(--radius-sm) 0 0; }
.vertical-bar-label { font-size: 0.65rem; color: var(--text-secondary); text-align: center; margin-top: var(--spacing-xs); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 100%; }
.pie-chart-container { display: flex; align-items: center; gap: var(--spacing-xl); }
.pie-chart { width: 180px; height: 180px; border-radius: 50%; flex-shrink: 0; }
.pie-legend { display: flex; flex-direction: column; gap: var(--spacing-sm); }
.pie-legend-item { display: flex; align-items: center; gap: var(--spacing-sm); }
.pie-legend-color { width: 14px; height: 14px; border-radius: 3px; flex-shrink: 0; }
.pie-legend-name { font-size: 0.85rem; color: var(--text-secondary); }
.pie-legend-value { font-family: var(--font-mono); font-size: 0.85rem; color: var(--text-primary); margin-left: auto; }

/* Modal styles */
.modal-overlay { display: flex; position: fixed; top: 0; left: 0; right: 0; bottom: 0; background: rgba(0, 0, 0, 0.7); z-index: 1000; align-items: center; justify-content: center; opacity: 0; visibility: hidden; pointer-events: none; transition: opacity var(--transition-normal), visibility var(--transition-normal); }
.modal-overlay.active { opacity: 1; visibility: visible; pointer-events: auto; }
.modal-content { background: var(--bg-panel); border: 1px solid var(--border-color); border-radius: var(--radius-lg); width: 380px; max-height: 80vh; overflow: hidden; box-shadow: 0 20px 60px rgba(0, 0, 0, 0.5); transform: translateY(12px) scale(0.98); transition: transform var(--transition-normal); }
.modal-overlay.active .modal-content { transform: none; }
.modal-header { display: flex; justify-content: space-between; align-items: center; padding: var(--spacing-lg); border-bottom: 1px solid var(--border-color); }
.modal-header h2 { font-family: var(--font-display); font-size: 1.1rem; color: var(--text-primary); margin: 0; }
.modal-close { background: none; border: none; color: var(--text-dim); font-size: 1.5rem; cursor: pointer; padding: 0; line-height: 1; }
.modal-close:hover { color: var(--text-primary); }
.modal-body { padding: var(--spacing-lg); }

@media (max-width: 768px) {
  .stats-row { grid-template-columns: repeat(3, 1fr); gap: var(--spacing-sm); }
  .stat-card { padding: var(--spacing-md); }
  .stat-value { font-size: 1.2rem; }
  .stat-unit { font-size: 1.2rem; }
  .grid-2 { grid-template-columns: 1fr; }
  .relation-section-header { flex-direction: column; align-items: flex-start; }
  .relation-section-left { flex-wrap: wrap; }
  .relation-detail-modal { width: calc(100vw - 32px) !important; }
  .relation-detail-triple { font-size: 0.8rem; }
  .pie-chart-container { flex-direction: column; gap: var(--spacing-md); }
  .pie-chart { width: 140px; height: 140px; }
}
</style>
