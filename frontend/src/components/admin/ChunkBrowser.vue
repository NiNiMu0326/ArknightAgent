<template>
  <div class="tab-content">
    <div class="section-header">
      <h2 class="section-title">Chunk Browser</h2>
    </div>
    <div class="chunk-browser">
      <div class="chunk-list">
        <div class="chunk-list-header">
          <div class="form-group">
            <select class="input select" v-model="chunkCollection" @change="loadChunks">
              <option value="operators">Operators</option>
              <option value="stories">Stories</option>
              <option value="knowledge">Knowledge</option>
            </select>
          </div>
          <div class="form-group">
            <input type="text" class="input" v-model="chunkSearch" placeholder="搜索文档..." @input="debouncedSearch">
          </div>
        </div>
        <div class="chunk-list-body">
          <div v-if="loadingChunks && chunks.length === 0" class="chunk-list-hint">加载中...</div>
          <template v-else>
            <div
              v-for="c in displayedChunks"
              :key="c.filename"
              class="chunk-item"
              :class="{ active: selectedChunk?.filename === c.filename }"
              @click="selectChunk(c)"
            >
              <div class="chunk-item-title">{{ c.name }}</div>
              <div class="chunk-item-meta">
                <span>{{ c.char_count }} 字符</span>
                <span>{{ c.tokens }} tokens</span>
              </div>
            </div>
            <div v-if="displayedChunks.length === 0" class="chunk-list-hint">无匹配文档</div>
          </template>
        </div>
      </div>
      <div class="chunk-preview">
        <div class="chunk-preview-header">
          <span class="chunk-preview-title">{{ loadingChunks ? '加载中...' : (selectedChunk?.name || '选择一个文档') }}</span>
          <div class="chunk-preview-stats" v-if="selectedChunk && !loadingChunks">
            <span>{{ selectedChunk.char_count }} 字符</span>
            <span>{{ selectedChunk.lines }} 行</span>
            <span>{{ selectedChunk.tokens }} tokens</span>
          </div>
          <div class="chunk-nav-inline" v-if="displayedChunks.length && !loadingChunks">
            <button class="btn btn-small" @click="navigateChunk(-1)">&lt;</button>
            <input type="number" class="chunk-nav-input" v-model="chunkNavInput" min="1" :max="displayedChunks.length" @keypress.enter="jumpToChunk">
            <span class="chunk-nav-info">/ {{ displayedChunks.length }}</span>
            <button class="btn btn-small" @click="navigateChunk(1)">&gt;</button>
          </div>
          <div class="chunk-nav-inline" v-else-if="loadingChunks">
            <span class="chunk-nav-info">加载中...</span>
          </div>
        </div>
        <div class="chunk-preview-content">{{ loadingContent ? '加载中...' : (selectedChunkContent || '选择一个文档查看内容') }}</div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onActivated, watch } from 'vue'
import { api, debounce } from '../../api'

const props = defineProps({
  initialCollection: { type: String, default: 'operators' },
  initialChunk: { type: String, default: '' },
})

const chunkCollection = ref(props.initialCollection)
const chunks = ref([])
const selectedChunk = ref(null)
const selectedChunkContent = ref('')
const chunkSearch = ref('')
const searchQuery = ref('')
const loadingChunks = ref(false)
const loadingContent = ref(false)
const chunkNavInput = ref(1)

// 请求序号：快速切换集合/文档时，旧请求返回不能覆盖新状态
let chunksRequestSeq = 0
let contentRequestSeq = 0

// 集合白名单：本组件可能被其它页面复用，防御性校验不依赖调用方（AdminView 也做了白名单）
const COLLECTIONS = ['operators', 'stories', 'knowledge']
function normalizeCollection(collection) {
  return COLLECTIONS.includes(collection) ? collection : COLLECTIONS[0]
}

// 首次加载只允许发生一次：KeepAlive 下组件首次挂载时 onMounted 与 onActivated 都会触发，
// 若 onActivated 再调用 loadChunks()，它会递增 chunksRequestSeq，把 onMounted 的深链请求
// 判为过期丢弃（?chunk=xxx 失效、退化成选中第一项），没有深链时也会多发一次重复请求。
// 这里「谁先跑谁负责」，后到者直接跳过。
let initialLoadStarted = false

function runInitialLoad() {
  if (initialLoadStarted) return
  initialLoadStarted = true
  if (props.initialChunk) {
    loadChunksForCollection(props.initialCollection, props.initialChunk)
  } else {
    loadChunks()
  }
}

// 文档列表内联过滤：搜索框输入直接筛选下方常驻列表
const displayedChunks = computed(() => {
  const q = searchQuery.value.trim().toLowerCase()
  if (!q) return chunks.value
  return chunks.value.filter(c => (c.name || c.filename || '').toLowerCase().includes(q))
})

onMounted(() => {
  runInitialLoad()
})

// keep-alive 缓存后再次激活时重新加载chunks（仅在数据为空时加载，避免重置用户选择）
onActivated(() => {
  // 首次激活交由 onMounted（或本分支兜底）完成，不能重复发起请求
  if (!initialLoadStarted) {
    runInitialLoad()
    return
  }
  if (chunks.value.length === 0 && !loadingChunks.value) loadChunks()
})

// 路由 query 变化（如从图谱页跳转指定 chunk）时响应。
// KeepAlive 缓存下组件不会重建，所以「只有 collection 变化、没有 chunk 参数」时同样要重载，
// 否则列表与下拉框会和 URL 不一致。
watch(() => [props.initialCollection, props.initialChunk], ([collection, chunk]) => {
  if (!COLLECTIONS.includes(collection)) return
  if (chunk) {
    loadChunksForCollection(collection, chunk)
    return
  }
  // 仅集合变化时按新集合重载（loadChunks 会清空选中态与搜索条件）；
  // 集合没变则保持现状，不破坏 KeepAlive 保留浏览状态的初衷
  if (collection !== chunkCollection.value) {
    chunkCollection.value = collection
    loadChunks()
  }
})

// chunks 当前属于哪个集合：请求失败时据此判断旧列表能否沿用
const loadedCollection = ref(null)

function resetSelection() {
  selectedChunk.value = null
  selectedChunkContent.value = ''
  chunkNavInput.value = 1
}

async function loadChunks() {
  const seq = ++chunksRequestSeq
  const collection = normalizeCollection(chunkCollection.value)
  loadingChunks.value = true
  chunkSearch.value = ''
  searchQuery.value = ''
  try {
    const newChunks = await api.getChunks(collection)
    if (seq !== chunksRequestSeq) return
    // 新数据到了才替换，避免中间空白
    chunks.value = newChunks
    loadedCollection.value = collection
    if (newChunks.length > 0) {
      // 选中第一个，内容在后台异步加载
      selectChunk(newChunks[0], collection)
    } else {
      resetSelection()
    }
  } catch (e) {
    if (seq !== chunksRequestSeq) return
    console.error('Failed to load chunks:', e)
    // 加载失败时同一集合的旧列表可以保留（避免网络抖动清空），
    // 但已经切到别的集合还沿用旧列表的话，点击条目会用新集合请求旧 filename（404），必须清空
    if (loadedCollection.value !== collection) {
      chunks.value = []
      resetSelection()
    }
  } finally {
    if (seq === chunksRequestSeq) loadingChunks.value = false
  }
}

async function loadChunksForCollection(collection, targetChunk) {
  // 入口统一校验集合（组件为公共组件，不依赖调用方白名单），并与下拉框保持同步
  collection = normalizeCollection(collection)
  if (chunkCollection.value !== collection) chunkCollection.value = collection
  const seq = ++chunksRequestSeq
  loadingChunks.value = true
  chunkSearch.value = ''
  searchQuery.value = ''
  try {
    const newChunks = await api.getChunks(collection)
    if (seq !== chunksRequestSeq) return
    chunks.value = newChunks
    loadedCollection.value = collection
    // Extract filename part from chunk_id like "operators_char_103_angel" -> "char_103_angel"
    const filenamePart = targetChunk.replace(/^(operators|stories|knowledge)_/, '')
    const found = newChunks.find(c =>
      c.filename.includes(filenamePart) ||
      c.filename === filenamePart + '.md' ||
      c.name === filenamePart
    )
    if (found) {
      selectChunk(found, collection)
    } else if (newChunks.length > 0) {
      selectChunk(newChunks[0], collection)
    } else {
      // 目标集合为空：清空选中态，否则右侧会继续显示上一个集合的文档，
      // 后续导航还会用新 collection 去请求旧 filename 导致「加载失败」
      resetSelection()
    }
  } catch (e) {
    if (seq !== chunksRequestSeq) return
    console.error('Failed to load chunks for direct nav:', e)
    // 深链请求失败：列表若不属于目标集合同样要清空，避免后续跨集合请求（404）
    if (loadedCollection.value !== collection) {
      chunks.value = []
      resetSelection()
    }
  } finally {
    if (seq === chunksRequestSeq) loadingChunks.value = false
  }
}

async function selectChunk(chunk, collection = chunkCollection.value) {
  const seq = ++contentRequestSeq
  selectedChunk.value = chunk
  loadingContent.value = true
  try {
    const result = await api.getChunk(collection, chunk.filename)
    if (seq !== contentRequestSeq) return
    selectedChunkContent.value = result.content
  } catch (e) {
    if (seq !== contentRequestSeq) return
    selectedChunkContent.value = '加载失败'
  } finally {
    if (seq === contentRequestSeq) loadingContent.value = false
  }
  const idx = displayedChunks.value.findIndex(c => c.filename === chunk.filename)
  if (idx >= 0) chunkNavInput.value = idx + 1
}

// 列表渲染用过滤后的 displayedChunks，导航（上/下一条、序号跳转、序号显示）也必须基于同一列表，
// 否则搜索过滤生效时会跳到列表里看不到的条目、序号总数与列表长度对不上。
function navigateChunk(dir) {
  const list = displayedChunks.value
  if (list.length === 0) return
  const idx = list.findIndex(c => c.filename === selectedChunk.value?.filename)
  // 当前选中项被搜索过滤掉（idx === -1）时：向后落到第一项，向前落到最后一项
  const newIdx = idx < 0
    ? (dir > 0 ? 0 : list.length - 1)
    : Math.max(0, Math.min(list.length - 1, idx + dir))
  if (list[newIdx]) selectChunk(list[newIdx])
}

function jumpToChunk() {
  const list = displayedChunks.value
  if (list.length === 0) return
  const n = Number(chunkNavInput.value)
  const idx = Math.max(0, Math.min(list.length - 1, (Number.isFinite(n) && n > 0 ? n : 1) - 1))
  if (list[idx]) selectChunk(list[idx])
}

// 搜索条件变化会改变可见列表的序号：把导航序号同步为选中项在可见列表中的位置，
// 选中项被过滤掉时夹到可见范围内，避免出现「5 / 2」这种越界序号
watch(displayedChunks, (list) => {
  if (list.length === 0) return
  const idx = selectedChunk.value
    ? list.findIndex(c => c.filename === selectedChunk.value.filename)
    : -1
  const next = idx >= 0
    ? idx + 1
    : Math.max(1, Math.min(list.length, Number(chunkNavInput.value) || 1))
  if (chunkNavInput.value !== next) chunkNavInput.value = next
})

const debouncedSearch = debounce(() => {
  searchQuery.value = chunkSearch.value
}, 200)
</script>

<style scoped>
.tab-content { display: flex; flex-direction: column; flex: 1; min-height: 0; }
.section-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: var(--spacing-lg); }
.section-title { font-family: var(--font-display); font-size: 1.1rem; color: var(--text-primary); text-transform: uppercase; }
.btn-small { padding: var(--spacing-xs) var(--spacing-sm); font-size: 0.8rem; }

.chunk-browser { position: relative; display: grid; grid-template-columns: 300px 1fr; gap: var(--spacing-lg); }
.chunk-list { position: absolute; top: 0; left: 0; bottom: 0; width: 300px; background: var(--bg-panel); border: 1px solid var(--border-color); border-radius: var(--radius-lg); overflow: hidden; display: flex; flex-direction: column; }
.chunk-list-header { padding: var(--spacing-md); border-bottom: 1px solid var(--border-color); background: var(--bg-card); }
.chunk-list-header .form-group { margin-bottom: var(--spacing-sm); }
.chunk-list-header .form-group:last-child { margin-bottom: 0; }
.chunk-list-hint { padding: var(--spacing-xl); text-align: center; color: var(--text-dim); font-size: 0.85rem; }
.chunk-list-body { flex: 1; overflow-y: auto; min-height: 0; }
.chunk-item { padding: var(--spacing-md); border-bottom: 1px solid var(--border-color); cursor: pointer; transition: all var(--transition-fast); }
.chunk-item:hover { background: var(--bg-panel-hover); }
.chunk-item.active { background: var(--color-primary-glow); border-left: 3px solid var(--color-primary); }
.chunk-item-title { font-family: var(--font-mono); font-size: 0.85rem; color: var(--text-primary); margin-bottom: var(--spacing-xs); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.chunk-item-meta { display: flex; gap: var(--spacing-md); font-size: 0.75rem; color: var(--text-dim); }
.chunk-preview { grid-column: 2; background: var(--bg-panel); border: 1px solid var(--border-color); border-radius: var(--radius-lg); display: flex; flex-direction: column; overflow: hidden; min-height: 0; }
.chunk-preview-header { padding: var(--spacing-md) var(--spacing-lg); border-bottom: 1px solid var(--border-color); background: var(--bg-card); display: flex; flex-wrap: wrap; align-items: center; gap: var(--spacing-sm); }
.chunk-preview-title { font-family: var(--font-mono); font-size: 1rem; color: var(--color-primary); }
.chunk-preview-stats { display: flex; gap: var(--spacing-lg); font-size: 0.8rem; color: var(--text-secondary); }
.chunk-nav-inline { display: flex; align-items: center; gap: var(--spacing-xs); margin-left: auto; }
.chunk-nav-info { font-size: 0.85rem; color: var(--text-secondary); padding: 0 var(--spacing-xs); }
.chunk-nav-input { width: 60px; padding: var(--spacing-xs); background: var(--bg-dark); border: 1px solid var(--border-color); border-radius: var(--radius-sm); color: var(--text-primary); font-size: 0.85rem; text-align: center; }
.chunk-nav-input:focus { outline: none; border-color: var(--color-primary); }
.chunk-preview-content { padding: var(--spacing-lg); min-height: 200px; background: var(--bg-dark); font-size: 0.9rem; line-height: 1.8; white-space: pre-wrap; word-break: break-all; }

@media (max-width: 768px) {
  .chunk-browser { grid-template-columns: 1fr; }
  .chunk-list { position: static; width: auto; }
  .chunk-list-body { max-height: 40vh; }
  .chunk-preview { grid-column: auto; min-height: 400px; }
  .chunk-preview-header { padding: var(--spacing-sm) var(--spacing-md); }
  .chunk-preview-title { font-size: 0.85rem; word-break: break-all; }
  .chunk-preview-stats { gap: var(--spacing-sm); font-size: 0.75rem; }
  .chunk-nav-inline { margin-left: 0; width: 100%; justify-content: flex-end; }
}
</style>
