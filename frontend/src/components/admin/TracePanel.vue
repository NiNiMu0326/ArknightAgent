<template>
  <div class="tab-content">
    <div class="section-header">
      <h2 class="section-title">观测追踪</h2>
      <div class="section-header-right">
        <span class="traces-total" v-if="traceSource === 'langfuse' && lfTotal > 0">LangFuse · {{ lfTotal }} 条</span>
        <span class="traces-total" v-else-if="traceSource === 'local' && tracesTotal > 0">本地 · {{ tracesTotal }} 条</span>
        <button v-if="traceSource === 'local' && tracesTotal > 0"
                class="btn btn-small btn-export"
                @click="exportAll()"
                :disabled="exportingTraces"
                title="导出全部 Trace 为 JSON">
          导出全部
        </button>
        <button class="btn btn-small" @click="refreshTraces()" :disabled="loadingTraces || loadingLangfuse">
          {{ (loadingTraces || loadingLangfuse) ? '加载中...' : '刷新' }}
        </button>
      </div>
    </div>

    <!-- 子 Tab 切换：LangFuse / 本地 -->
    <div class="traces-subtabs">
      <button class="traces-subtab"
              :class="{ active: traceSource === 'langfuse' }"
              @click="switchTraceSource('langfuse')"
              :disabled="!tracesLangfuse?.enabled"
              :title="!tracesLangfuse?.enabled ? 'LangFuse 未配置' : ''">
        LangFuse
        <span class="traces-subtab-badge" v-if="tracesLangfuse?.enabled">已连接</span>
        <span class="traces-subtab-badge off" v-else>未配置</span>
      </button>
      <button class="traces-subtab"
              :class="{ active: traceSource === 'local' }"
              @click="switchTraceSource('local')">
        本地记录
      </button>
    </div>

    <!-- ═══ LangFuse 视图 ═══ -->
    <template v-if="traceSource === 'langfuse'">
      <!-- LangFuse 未配置 -->
      <div class="panel" v-if="!tracesLangfuse?.enabled">
        <div class="panel-body">
          <div class="empty-state" style="padding: 60px;">
            <div class="empty-state-title">LangFuse 未配置</div>
            <div class="empty-state-desc">在 <code>backend/.env</code> 中设置 LANGFUSE_PUBLIC_KEY 和 LANGFUSE_SECRET_KEY</div>
          </div>
        </div>
      </div>

      <!-- LangFuse 错误 -->
      <div class="panel" v-else-if="lfError">
        <div class="panel-body">
          <div class="empty-state" style="padding: 40px;">
            <div class="empty-state-title" style="color: #ff6b9d;">连接失败</div>
            <div class="empty-state-desc">{{ lfError }}</div>
            <button class="btn btn-small" style="margin-top: 12px;" @click="loadLangfuseTraces()">重试</button>
          </div>
        </div>
      </div>

      <!-- LangFuse trace 列表 -->
      <div class="traces-container" v-else-if="lfTraces.length > 0">
        <div class="traces-table-wrap">
          <table class="traces-table">
            <thead>
              <tr>
                <th class="col-time">时间</th>
                <th class="col-msg">Trace 名称</th>
                <th class="col-msg">输入</th>
                <th class="col-latency">耗时</th>
                <th class="col-msg">输出</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="t in lfTraces" :key="t.id"
                  class="trace-row"
                  :class="{ expanded: lfExpandedId === t.id }"
                  @click="toggleLfDetail(t)">
                <td class="col-time" :title="formatAbsoluteTime(t.timestamp)">{{ formatRelativeTime(t.timestamp) }}</td>
                <td class="col-msg" :title="t.name">{{ t.name || '-' }}</td>
                <td class="col-msg" :title="t.input">{{ truncateText(t.input, 60) }}</td>
                <td class="col-latency">{{ formatLatencySeconds(t.latency) }}</td>
                <td class="col-msg" :title="t.output">{{ truncateText(t.output, 60) }}</td>
              </tr>
            </tbody>
          </table>
        </div>

        <!-- 分页 -->
        <div class="traces-pagination" v-if="lfTotalPages > 1">
          <button class="btn btn-small" :disabled="lfPage <= 1" @click="goLfPage(lfPage - 1)">&lt;</button>
          <span>{{ lfPage }} / {{ lfTotalPages }}</span>
          <button class="btn btn-small" :disabled="lfPage >= lfTotalPages" @click="goLfPage(lfPage + 1)">&gt;</button>
        </div>
      </div>

      <!-- 空状态 -->
      <div class="panel" v-else-if="!loadingLangfuse">
        <div class="panel-body">
          <div class="empty-state" style="padding: 60px;">
            <div class="empty-state-title">LangFuse 中暂无 Trace</div>
            <div class="empty-state-desc">发起一次 Agent 对话后，Trace 会自动上报到 LangFuse</div>
          </div>
        </div>
      </div>

      <!-- 加载中 -->
      <div class="panel" v-else>
        <div class="panel-body">
          <div class="empty-state" style="padding: 60px;">
            <div class="empty-state-title">加载中...</div>
          </div>
        </div>
      </div>

      <!-- LangFuse Trace 详情 -->
      <div class="trace-detail-panel" v-if="lfExpandedId">
        <template v-if="lfDetail">
          <div class="trace-detail-header">
            <h3>{{ lfDetail.name || 'Trace 详情' }}</h3>
            <div class="trace-detail-actions">
              <a v-if="lfDetailUrl" class="btn btn-small btn-export" :href="lfDetailUrl" target="_blank" rel="noopener" title="在 LangFuse 控制台中查看完整 Trace">↗ 在 LangFuse 中打开</a>
              <button class="modal-close" @click="closeLfDetail">&times;</button>
            </div>
          </div>
          <div class="trace-detail-grid">
            <div class="trace-detail-item">
              <span class="trace-detail-label">Trace ID</span>
              <span class="trace-detail-value mono copyable" :title="'点击复制'" @click="copyText(lfDetail.id)">{{ lfDetail.id }}</span>
            </div>
            <div class="trace-detail-item">
              <span class="trace-detail-label">会话 ID</span>
              <span class="trace-detail-value mono copyable" :title="'点击复制'" @click="copyText(lfDetail.sessionId)">{{ lfDetail.sessionId || '-' }}</span>
            </div>
            <div class="trace-detail-item">
              <span class="trace-detail-label">耗时</span>
              <span class="trace-detail-value">{{ formatLatencySeconds(lfDetail.latency) }}</span>
            </div>
          </div>

          <!-- Spans -->
          <div class="trace-tool-chain" v-if="lfDetail.spans && lfDetail.spans.length > 0">
            <h4>Spans ({{ lfDetail.spans.length }})</h4>
            <div class="tool-chain-list">
              <div v-for="(s, sIdx) in lfDetail.spans" :key="s.id" class="tool-chain-step tool_call" :class="{ expanded: expandedLfSpans.has(sIdx) }">
                <div class="tool-chain-body">
                  <div class="tool-chain-line" @click="toggleLfSpan(sIdx)">
                    <span class="tool-chain-name">{{ s.name }}</span>
                    <span class="tool-chain-latency">{{ formatLatencySeconds(s.latency) }}</span>
                    <span class="tool-chain-content">{{ truncateText(s.output || s.input, 150) }}</span>
                    <span class="tool-chain-expand">{{ expandedLfSpans.has(sIdx) ? '▲' : '▼' }}</span>
                  </div>
                  <div class="tool-chain-full" v-if="expandedLfSpans.has(sIdx)">
                    <div v-if="s.input"><span class="tool-chain-full-label">输入</span><pre>{{ s.input }}</pre></div>
                    <div v-if="s.output"><span class="tool-chain-full-label">输出</span><pre>{{ s.output }}</pre></div>
                    <div v-if="s.metadata && Object.keys(s.metadata).length"><span class="tool-chain-full-label">Metadata</span><pre>{{ prettyJson(s.metadata) }}</pre></div>
                  </div>
                </div>
              </div>
            </div>
          </div>

          <!-- Generations -->
          <div class="trace-tool-chain" v-if="lfDetail.generations && lfDetail.generations.length > 0">
            <h4>LLM Generations ({{ lfDetail.generations.length }})</h4>
            <div class="tool-chain-list">
              <div v-for="(g, gIdx) in lfDetail.generations" :key="g.id" class="tool-chain-step tool_result" :class="{ expanded: expandedLfGens.has(gIdx) }">
                <div class="tool-chain-body">
                  <div class="tool-chain-line" @click="toggleLfGen(gIdx)">
                    <span class="tool-chain-name">{{ g.name }}</span>
                    <span class="tool-chain-badge call">{{ g.model }}</span>
                    <span class="tool-chain-latency">{{ formatLatencySeconds(g.latency) }}</span>
                    <span class="tool-chain-tokens" v-if="g.usage">{{ g.usage?.input || 0 }}→{{ g.usage?.output || 0 }} tokens</span>
                    <span class="tool-chain-content">{{ truncateText(g.output, 150) }}</span>
                    <span class="tool-chain-expand">{{ expandedLfGens.has(gIdx) ? '▲' : '▼' }}</span>
                  </div>
                  <div class="tool-chain-full" v-if="expandedLfGens.has(gIdx)">
                    <div v-if="g.output"><span class="tool-chain-full-label">输出</span><pre>{{ g.output }}</pre></div>
                  </div>
                </div>
              </div>
            </div>
          </div>

          <div class="trace-detail-user-msg" v-if="lfDetail.input">
            <span class="trace-detail-label">输入</span>
            <p>{{ lfDetail.input }}</p>
          </div>
          <div class="trace-detail-user-msg" v-if="lfDetail.output">
            <span class="trace-detail-label">输出</span>
            <p>{{ lfDetail.output }}</p>
          </div>
        </template>
        <div v-else class="trace-detail-loading">加载详情中...</div>
      </div>
    </template>

    <!-- ═══ 本地记录视图 ═══ -->
    <template v-if="traceSource === 'local'">
      <!-- 聚合统计卡片 -->
      <div class="trace-summary-row" v-if="summary">
        <div class="trace-summary-card">
          <div class="ts-value">{{ summary.total }}</div>
          <div class="ts-label">总 Traces</div>
        </div>
        <div class="trace-summary-card">
          <div class="ts-value">{{ summary.today }}</div>
          <div class="ts-label">今日新增</div>
        </div>
        <div class="trace-summary-card">
          <div class="ts-value" :class="{ 'ts-error': summary.error_rate > 0 }">{{ (summary.error_rate * 100).toFixed(1) }}%</div>
          <div class="ts-label">错误率</div>
        </div>
        <div class="trace-summary-card">
          <div class="ts-value">{{ formatLatency(summary.avg_time_ms) }}</div>
          <div class="ts-label">平均耗时</div>
        </div>
        <div class="trace-summary-card">
          <div class="ts-value">{{ formatTokens(Math.round(summary.avg_tokens)) }}</div>
          <div class="ts-label">平均 Token</div>
        </div>
        <div class="trace-summary-card">
          <div class="ts-value">{{ summary.total_tool_calls }}</div>
          <div class="ts-label">工具调用总数</div>
        </div>
      </div>

      <!-- 筛选栏 -->
      <div class="traces-filters">
        <select class="input select traces-filter-select" v-model="filterStatus" @change="applyFilters">
          <option value="">全部状态</option>
          <option value="success">成功</option>
          <option value="error">错误</option>
          <option value="loop_detected">循环</option>
          <option value="max_rounds">超轮次</option>
        </select>
        <select class="input select traces-filter-select" v-model="filterModel" @change="applyFilters">
          <option value="">全部模型</option>
          <option v-for="m in modelOptions" :key="m.model_id" :value="m.model_id">{{ m.model_id }} ({{ m.count }})</option>
        </select>
        <input type="text" class="input traces-filter-input" v-model="filterKeyword" placeholder="搜索用户问题..." @input="debouncedApplyFilters">
        <button v-if="hasActiveFilters" class="btn btn-small" @click="resetFilters">清除</button>
      </div>

      <!-- 工具栏 -->
      <div class="traces-toolbar" v-if="tracesList.length > 0">
        <div class="traces-toolbar-left">
          <label class="traces-check-all">
            <input type="checkbox" :checked="isAllSelected" @change="toggleSelectAll">
            <span>{{ isAllSelected ? '取消全选' : '全选本页' }}</span>
          </label>
          <button v-if="selectedTraceIds.size > 0" class="btn btn-small" @click="clearSelection()">清空</button>
          <span v-if="selectedTraceIds.size > 0" class="traces-selected-count">已选 {{ selectedTraceIds.size }} 条</span>
        </div>
        <div class="traces-toolbar-right">
          <button class="btn btn-small btn-danger"
                  :disabled="selectedTraceIds.size === 0 || deletingTraces"
                  @click="deleteSelectedTraces()">
            {{ deletingTraces ? '删除中...' : `删除选中 (${selectedTraceIds.size})` }}
          </button>
          <button class="btn btn-small btn-export"
                  :disabled="selectedTraceIds.size === 0 || exportingTraces"
                  @click="exportSelectedTraces()">
            {{ exportingTraces ? '导出中...' : `导出选中 (${selectedTraceIds.size})` }}
          </button>
        </div>
      </div>

      <!-- 加载错误 -->
      <div class="panel" v-if="tracesError">
        <div class="panel-body">
          <div class="empty-state" style="padding: 40px;">
            <div class="empty-state-title" style="color: #ff6b9d;">加载失败</div>
            <div class="empty-state-desc">{{ tracesError }}</div>
            <button class="btn btn-small" style="margin-top: 12px;" @click="loadTraces()">重试</button>
          </div>
        </div>
      </div>

      <div class="traces-container" v-else-if="tracesList.length > 0">
        <div class="traces-table-wrap">
          <table class="traces-table">
            <thead>
              <tr>
                <th class="col-cb"><input type="checkbox" :checked="isAllSelected" @change="toggleSelectAll"></th>
                <th class="col-time">时间</th>
                <th class="col-msg">用户问题</th>
                <th class="col-model">模型</th>
                <th class="col-rounds">轮次</th>
                <th class="col-tools">工具</th>
                <th class="col-tokens">Token</th>
                <th class="col-latency">耗时</th>
                <th class="col-status">状态</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="t in tracesList" :key="t.id"
                  class="trace-row"
                  :class="{ expanded: expandedTraceId === t.id, [statusClass(t.status)]: true }"
                  @click="toggleTraceDetail(t)">
                <td class="col-cb" @click.stop>
                  <input type="checkbox" :checked="selectedTraceIds.has(t.id)" @change="toggleSelectTrace(t.id)">
                </td>
                <td class="col-time" :title="formatAbsoluteTime(t.created_at, true)">{{ formatRelativeTime(t.created_at, true) }}</td>
                <td class="col-msg" :title="t.user_message">{{ truncateText(t.user_message, 50) }}</td>
                <td class="col-model">{{ t.model_id }}</td>
                <td class="col-rounds">{{ t.total_rounds }}</td>
                <td class="col-tools">{{ t.total_tool_calls }}</td>
                <td class="col-tokens">{{ formatTokens(t.total_tokens) }}</td>
                <td class="col-latency">{{ formatLatency(t.total_time_ms) }}</td>
                <td class="col-status"><span class="status-badge" :class="statusClass(t.status)">{{ statusLabel(t.status) }}</span></td>
              </tr>
            </tbody>
          </table>
        </div>
        <div class="traces-pagination" v-if="tracesTotalPages > 1">
          <button class="btn btn-small" :disabled="tracesPage <= 1" @click="goToPage(tracesPage - 1)">&lt;</button>
          <span>{{ tracesPage }} / {{ tracesTotalPages }}</span>
          <button class="btn btn-small" :disabled="tracesPage >= tracesTotalPages" @click="goToPage(tracesPage + 1)">&gt;</button>
        </div>
      </div>
      <div class="panel" v-else-if="!loadingTraces">
        <div class="panel-body">
          <div class="empty-state" style="padding: 60px;">
            <div class="empty-state-title">{{ hasActiveFilters ? '无匹配的记录' : '暂无本地记录' }}</div>
            <div class="empty-state-desc">{{ hasActiveFilters ? '尝试调整筛选条件' : '发起一次 Agent 对话后，追踪数据会自动出现在这里' }}</div>
          </div>
        </div>
      </div>
      <div class="panel" v-else>
        <div class="panel-body">
          <div class="empty-state" style="padding: 60px;">
            <div class="empty-state-title">加载中...</div>
          </div>
        </div>
      </div>

      <!-- 本地 Trace 详情 -->
      <div class="trace-detail-panel" v-if="expandedTraceId">
        <template v-if="expandedTrace">
          <div class="trace-detail-header">
            <h3>Trace #{{ expandedTrace.id }} 详情</h3>
            <div class="trace-detail-actions">
              <button class="btn btn-small btn-export" @click="exportSingle(expandedTrace.id)" title="导出该 Trace 为 JSON">导出</button>
              <button class="modal-close" @click="closeTraceDetail">&times;</button>
            </div>
          </div>
          <div class="trace-detail-grid">
            <div class="trace-detail-item">
              <span class="trace-detail-label">会话 ID</span>
              <span class="trace-detail-value mono copyable" title="点击复制" @click="copyText(expandedTrace.session_id)">{{ expandedTrace.session_id }}</span>
            </div>
            <div class="trace-detail-item">
              <span class="trace-detail-label">模型</span>
              <span class="trace-detail-value">{{ expandedTrace.model_id }}</span>
            </div>
            <div class="trace-detail-item">
              <span class="trace-detail-label">总轮次</span>
              <span class="trace-detail-value">{{ expandedTrace.total_rounds }}</span>
            </div>
            <div class="trace-detail-item">
              <span class="trace-detail-label">LLM 调用</span>
              <span class="trace-detail-value">{{ expandedTrace.total_llm_calls }}</span>
            </div>
            <div class="trace-detail-item">
              <span class="trace-detail-label">工具调用</span>
              <span class="trace-detail-value">{{ expandedTrace.total_tool_calls }}</span>
            </div>
            <div class="trace-detail-item">
              <span class="trace-detail-label">总 Token</span>
              <span class="trace-detail-value">{{ formatTokens(expandedTrace.total_tokens) }}</span>
            </div>
            <div class="trace-detail-item">
              <span class="trace-detail-label">总耗时</span>
              <span class="trace-detail-value">{{ formatLatency(expandedTrace.total_time_ms) }}</span>
            </div>
            <div class="trace-detail-item">
              <span class="trace-detail-label">回答长度</span>
              <span class="trace-detail-value">{{ expandedTrace.answer_length }} 字符</span>
            </div>
            <div class="trace-detail-item">
              <span class="trace-detail-label">状态</span>
              <span class="trace-detail-value"><span class="status-badge" :class="statusClass(expandedTrace.status)">{{ statusLabel(expandedTrace.status) }}</span></span>
            </div>
            <div class="trace-detail-item" v-if="expandedTrace.error">
              <span class="trace-detail-label">错误信息</span>
              <span class="trace-detail-value error-text">{{ expandedTrace.error }}</span>
            </div>
          </div>
          <div class="trace-detail-user-msg">
            <span class="trace-detail-label">用户问题</span>
            <p>{{ expandedTrace.user_message }}</p>
          </div>
          <div class="trace-detail-user-msg" v-if="expandedTrace.answer">
            <span class="trace-detail-label">最终回答</span>
            <p class="trace-answer">{{ expandedTrace.answer }}</p>
          </div>
          <div class="trace-tool-chain" v-if="pairedToolChain.length > 0">
            <h4>工具调用链 ({{ pairedToolChain.length }} 步，点击展开完整内容)</h4>
            <div class="tool-chain-list">
              <div v-for="(step, idx) in pairedToolChain" :key="idx"
                   class="tool-chain-step"
                   :class="{ expanded: expandedSteps.has(idx) }">
                <span class="tool-chain-idx">{{ idx + 1 }}</span>
                <div class="tool-chain-body">
                  <template v-if="step.call">
                    <div class="tool-chain-line tool_call" @click="toggleStep(idx)">
                      <span class="tool-chain-badge call">调用</span>
                      <span class="tool-chain-name">{{ step.call.name }}</span>
                      <span class="tool-chain-args" :title="step.call.arguments">{{ truncateText(step.call.arguments, 120) }}</span>
                      <span class="tool-chain-expand">{{ expandedSteps.has(idx) ? '▲' : '▼' }}</span>
                    </div>
                    <div class="tool-chain-full" v-if="expandedSteps.has(idx)">
                      <span class="tool-chain-full-label">参数</span>
                      <pre>{{ prettyJson(step.call.arguments) }}</pre>
                    </div>
                  </template>
                  <template v-if="step.result">
                    <div class="tool-chain-line tool_result" @click="toggleStep(idx)">
                      <span class="tool-chain-badge result">结果</span>
                      <span class="tool-chain-content">{{ truncateText(step.result.content, 200) }}</span>
                    </div>
                    <div class="tool-chain-full" v-if="expandedSteps.has(idx)">
                      <span class="tool-chain-full-label">返回内容</span>
                      <pre>{{ step.result.content }}</pre>
                    </div>
                  </template>
                </div>
              </div>
            </div>
          </div>
          <div class="trace-tool-chain" v-else>
            <p class="text-dim">该会话已过期或无工具调用，工具调用详情不可用</p>
          </div>
        </template>
        <div v-else class="trace-detail-loading">加载详情中...</div>
      </div>
    </template>

    <!-- 确认弹窗 -->
    <div class="modal-overlay" :class="{ active: showConfirmModal }" @click.self="confirmCancel">
      <div class="modal-content modal-sm">
        <div class="modal-header">
          <h2>{{ confirmTitle }}</h2>
          <button class="modal-close" @click="confirmCancel">&times;</button>
        </div>
        <div class="modal-body">
          <p>{{ confirmMessage }}</p>
        </div>
        <div class="modal-footer">
          <button class="btn btn-secondary" @click="confirmCancel">取消</button>
          <button class="btn btn-primary" @click="confirmOk">确定</button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onDeactivated, onActivated, onUnmounted } from 'vue'
import { api, debounce, downloadBlob } from '../../api'
import { useToastStore } from '../../stores/toast'

const toast = useToastStore()
const PAGE_SIZE = 20

// Trace source switcher
const traceSource = ref('langfuse')  // 'langfuse' | 'local'
const tracesLangfuse = ref(null)     // { enabled, host }

// ── 本地 traces 列表状态 ──
const tracesList = ref([])
const tracesTotal = ref(0)
const tracesPage = ref(1)
const tracesTotalPages = ref(1)
const loadingTraces = ref(false)
const tracesError = ref('')

// 筛选
const filterStatus = ref('')
const filterModel = ref('')
const filterKeyword = ref('')

// 聚合统计
const summary = ref(null)

// 多选
const selectedTraceIds = ref(new Set())
const exportingTraces = ref(false)
const deletingTraces = ref(false)

// 本地详情
const expandedTraceId = ref(null)
const expandedTrace = ref(null)
const expandedSteps = ref(new Set())
let detailRequestSeq = 0  // 竞态守卫：只有最后一次请求允许写入详情

// ── LangFuse 状态 ──
const lfTraces = ref([])
const lfTotal = ref(0)
const lfPage = ref(1)
const lfTotalPages = ref(1)
const lfError = ref('')
const loadingLangfuse = ref(false)
const lfExpandedId = ref(null)
const lfDetail = ref(null)
const expandedLfSpans = ref(new Set())
const expandedLfGens = ref(new Set())
let lfDetailRequestSeq = 0

// 自动刷新：组件存活期间始终每 10 秒静默刷新一次
let autoRefreshTimer = null

// ── 生命周期 ──

onMounted(() => {
  // 首次进入，先静默加载本地 traces 获取 langfuse 状态，再按来源加载
  loadTracesSilent()
  startAutoRefresh()
})

onDeactivated(stopAutoRefresh)
onUnmounted(stopAutoRefresh)

// keep-alive 切走再切回后重启定时器（stopAutoRefresh 只在 deactivated 执行）
onActivated(startAutoRefresh)

function startAutoRefresh() {
  if (autoRefreshTimer) return
  autoRefreshTimer = setInterval(() => refreshTraces(true), 10000)
}

function stopAutoRefresh() {
  if (autoRefreshTimer) {
    clearInterval(autoRefreshTimer)
    autoRefreshTimer = null
  }
}

// ── 本地 traces ──

async function loadTraces(silent = false) {
  if (!silent) {
    loadingTraces.value = true
    tracesError.value = ''
  }
  try {
    const result = await api.getTraces(tracesPage.value, PAGE_SIZE, {
      status: filterStatus.value,
      modelId: filterModel.value,
      q: filterKeyword.value.trim(),
    })
    if (result.error) throw new Error(result.error)
    tracesList.value = result.traces || []
    tracesTotal.value = result.total || 0
    tracesTotalPages.value = Math.ceil(tracesTotal.value / PAGE_SIZE) || 1
    tracesLangfuse.value = { enabled: result.langfuse_enabled, host: result.langfuse_host }
    tracesError.value = ''
    // 删除/筛选后当前页可能超出范围，回退到最后一页
    if (tracesPage.value > tracesTotalPages.value) {
      tracesPage.value = tracesTotalPages.value
      if (!silent) loadingTraces.value = false
      return loadTraces(silent)
    }
  } catch (e) {
    if (!silent) tracesError.value = e.message || '加载失败'
  }
  if (!silent) loadingTraces.value = false
}

async function loadSummary() {
  try {
    summary.value = await api.getTraceSummary()
  } catch (e) {
    // 统计失败不阻塞主流程
  }
}

const modelOptions = computed(() => summary.value?.by_model || [])

const hasActiveFilters = computed(() =>
  !!(filterStatus.value || filterModel.value || filterKeyword.value.trim())
)

function applyFilters() {
  tracesPage.value = 1
  clearSelection()
  closeTraceDetail()
  loadTraces()
}

const debouncedApplyFilters = debounce(() => applyFilters(), 300)

function resetFilters() {
  filterStatus.value = ''
  filterModel.value = ''
  filterKeyword.value = ''
  applyFilters()
}

function goToPage(p) {
  if (p < 1 || p > tracesTotalPages.value || p === tracesPage.value) return
  tracesPage.value = p
  clearSelection()
  closeTraceDetail()
  loadTraces()
}

// ── 本地详情 ──

async function toggleTraceDetail(t) {
  if (expandedTraceId.value === t.id) {
    closeTraceDetail()
    return
  }
  expandedTraceId.value = t.id
  expandedSteps.value = new Set()
  // 列表项已有缓存的 tool_trace 时直接用
  if (t.tool_trace) {
    expandedTrace.value = t
    return
  }
  const seq = ++detailRequestSeq
  expandedTrace.value = null
  try {
    const detail = await api.getTraceDetail(t.id)
    if (seq !== detailRequestSeq) return  // 已切到别的行/已关闭
    expandedTrace.value = detail
    const idx = tracesList.value.findIndex(tr => tr.id === t.id)
    if (idx >= 0) tracesList.value[idx] = { ...tracesList.value[idx], ...detail }
  } catch (e) {
    if (seq !== detailRequestSeq) return
    expandedTrace.value = { ...t, tool_trace: [], answer: '' }
  }
}

function closeTraceDetail() {
  expandedTraceId.value = null
  expandedTrace.value = null
  detailRequestSeq++  // 使进行中的请求失效
}

// 工具调用与结果按 tool_call_id 配对
const pairedToolChain = computed(() => {
  const steps = expandedTrace.value?.tool_trace || []
  const callIds = new Set(steps.filter(s => s.type === 'tool_call' && s.id).map(s => s.id))
  const resultByCallId = {}
  steps.forEach(s => {
    if (s.type === 'tool_result' && s.tool_call_id) resultByCallId[s.tool_call_id] = s
  })
  const pairs = []
  steps.forEach(s => {
    if (s.type === 'tool_call') {
      pairs.push({ call: s, result: (s.id && resultByCallId[s.id]) || null })
    } else if (s.type === 'tool_result' && (!s.tool_call_id || !callIds.has(s.tool_call_id))) {
      pairs.push({ call: null, result: s })
    }
  })
  return pairs
})

function toggleStep(idx) {
  const next = new Set(expandedSteps.value)
  if (next.has(idx)) next.delete(idx)
  else next.add(idx)
  expandedSteps.value = next
}

// ── 多选（全选仅作用于当前页）──

const isAllSelected = computed(() =>
  tracesList.value.length > 0 && tracesList.value.every(t => selectedTraceIds.value.has(t.id))
)

function toggleSelectAll() {
  const next = new Set(selectedTraceIds.value)
  if (isAllSelected.value) {
    tracesList.value.forEach(t => next.delete(t.id))
  } else {
    tracesList.value.forEach(t => next.add(t.id))
  }
  selectedTraceIds.value = next
}

function toggleSelectTrace(id) {
  const next = new Set(selectedTraceIds.value)
  if (next.has(id)) next.delete(id)
  else next.add(id)
  selectedTraceIds.value = next
}

function clearSelection() {
  selectedTraceIds.value = new Set()
}

// ── 导出 / 删除 ──

async function exportAll() {
  exportingTraces.value = true
  try {
    const blob = await api.exportTraces()
    downloadBlob(blob, 'arknights_traces.json')
    toast.show('导出成功', 'info')
  } catch (e) {
    toast.show(e.message || '导出失败', 'error')
  }
  exportingTraces.value = false
}

async function exportSelectedTraces() {
  if (selectedTraceIds.value.size === 0) return
  exportingTraces.value = true
  try {
    const blob = await api.exportTraces(Array.from(selectedTraceIds.value))
    downloadBlob(blob, 'arknights_traces.json')
    toast.show('导出成功', 'info')
  } catch (e) {
    toast.show(e.message || '导出失败', 'error')
  }
  exportingTraces.value = false
}

async function exportSingle(traceId) {
  try {
    const blob = await api.exportSingleTrace(traceId)
    downloadBlob(blob, `trace_${traceId}.json`)
    toast.show('导出成功', 'info')
  } catch (e) {
    toast.show(e.message || '导出失败', 'error')
  }
}

async function deleteSelectedTraces() {
  if (selectedTraceIds.value.size === 0) return
  const count = selectedTraceIds.value.size
  const ok = await showConfirm('删除确认', `确定删除选中的 ${count} 条 Trace 记录吗？此操作不可撤销。`)
  if (!ok) return
  deletingTraces.value = true
  try {
    // 记录当前页被删掉的条数，整页删光且不在第一页时回退一页
    const removedOnPage = tracesList.value.filter(t => selectedTraceIds.value.has(t.id)).length
    const result = await api.deleteTraces(Array.from(selectedTraceIds.value))
    toast.show(`已删除 ${result.deleted ?? count} 条记录`, 'info')
    clearSelection()
    closeTraceDetail()
    if (removedOnPage >= tracesList.value.length && tracesPage.value > 1) {
      tracesPage.value--
    }
    await loadTraces()
    loadSummary()
  } catch (e) {
    toast.show(e.message || '删除失败', 'error')
  }
  deletingTraces.value = false
}

// ── LangFuse ──

async function loadLangfuseTraces(silent = false) {
  if (!silent) {
    loadingLangfuse.value = true
    lfError.value = ''
  }
  try {
    const result = await api.getLangfuseTraces(lfPage.value, PAGE_SIZE)
    if (result.error) {
      lfError.value = result.error
      lfTraces.value = []
      lfTotal.value = 0
    } else {
      lfTraces.value = result.traces || []
      lfTotal.value = result.total || 0
      lfTotalPages.value = Math.ceil(lfTotal.value / PAGE_SIZE) || 1
    }
  } catch (e) {
    if (!silent) {
      lfError.value = '无法连接到 LangFuse: ' + e.message
      lfTraces.value = []
    }
  }
  if (!silent) loadingLangfuse.value = false
}

function goLfPage(p) {
  if (p < 1 || p > lfTotalPages.value || p === lfPage.value) return
  lfPage.value = p
  closeLfDetail()
  loadLangfuseTraces()
}

async function toggleLfDetail(t) {
  if (lfExpandedId.value === t.id) {
    closeLfDetail()
    return
  }
  lfExpandedId.value = t.id
  expandedLfSpans.value = new Set()
  expandedLfGens.value = new Set()
  const seq = ++lfDetailRequestSeq
  lfDetail.value = null
  try {
    const detail = await api.getLangfuseTraceDetail(t.id)
    if (seq !== lfDetailRequestSeq) return
    lfDetail.value = detail
  } catch (e) {
    if (seq !== lfDetailRequestSeq) return
    lfDetail.value = { ...t, spans: [], generations: [] }
  }
}

function closeLfDetail() {
  lfExpandedId.value = null
  lfDetail.value = null
  lfDetailRequestSeq++
}

const lfDetailUrl = computed(() => {
  const host = tracesLangfuse.value?.host
  if (!host || !lfDetail.value?.id) return ''
  return `${host.replace(/\/$/, '')}/trace/${lfDetail.value.id}`
})

function toggleLfSpan(idx) {
  const next = new Set(expandedLfSpans.value)
  if (next.has(idx)) next.delete(idx)
  else next.add(idx)
  expandedLfSpans.value = next
}

function toggleLfGen(idx) {
  const next = new Set(expandedLfGens.value)
  if (next.has(idx)) next.delete(idx)
  else next.add(idx)
  expandedLfGens.value = next
}

// ── 来源切换 / 刷新 ──

function switchTraceSource(source) {
  traceSource.value = source
  if (source === 'langfuse' && lfTraces.value.length === 0 && tracesLangfuse.value?.enabled) {
    loadLangfuseTraces()
  }
  if (source === 'local') {
    if (tracesList.value.length === 0) loadTraces()
    if (!summary.value) loadSummary()
  }
}

function refreshTraces(silent = false) {
  if (traceSource.value === 'langfuse') {
    loadLangfuseTraces(silent)
  } else {
    loadTraces(silent)
    loadSummary()
  }
}

async function loadTracesSilent() {
  // 静默加载本地 traces 获取 langfuse 状态（不显示加载态）
  try {
    const result = await api.getTraces(1, 1)
    tracesLangfuse.value = { enabled: result.langfuse_enabled, host: result.langfuse_host }
    // 自动选择合适的来源
    if (tracesLangfuse.value?.enabled) {
      traceSource.value = 'langfuse'
      loadLangfuseTraces()
    } else {
      traceSource.value = 'local'
      loadTraces()
      loadSummary()
    }
  } catch (e) {
    traceSource.value = 'local'
    loadTraces()
    loadSummary()
  }
}

// ── 工具函数 ──

async function copyText(text) {
  if (!text) return
  try {
    await navigator.clipboard.writeText(text)
    toast.show('已复制到剪贴板', 'info')
  } catch (e) {
    toast.show('复制失败', 'error')
  }
}

function truncateText(text, maxLen) {
  if (!text) return ''
  const str = String(text)
  return str.length > maxLen ? str.slice(0, maxLen) + '...' : str
}

function prettyJson(text) {
  if (!text) return ''
  if (typeof text === 'object') return JSON.stringify(text, null, 2)
  try {
    return JSON.stringify(JSON.parse(text), null, 2)
  } catch {
    return String(text)
  }
}

/**
 * 相对时间格式化。utcSuffix=true 表示输入是 SQLite datetime('now') 产生的
 * 无时区 UTC 字符串，需要补 'Z' 再解析。
 */
function formatRelativeTime(ts, utcSuffix = false) {
  if (!ts) return '-'
  const d = new Date(utcSuffix ? ts + 'Z' : ts)
  const now = new Date()
  const diffMs = now - d
  const diffMin = Math.floor(diffMs / 60000)
  if (diffMin < 1) return '刚刚'
  if (diffMin < 60) return `${diffMin} 分钟前`
  const diffHour = Math.floor(diffMin / 60)
  if (diffHour < 24) return `${diffHour} 小时前`
  const diffDay = Math.floor(diffHour / 24)
  if (diffDay < 7) return `${diffDay} 天前`
  return d.toLocaleString('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
}

function formatAbsoluteTime(ts, utcSuffix = false) {
  if (!ts) return ''
  return new Date(utcSuffix ? ts + 'Z' : ts).toLocaleString('zh-CN')
}

function formatTokens(n) {
  if (!n) return '0'
  if (n >= 1000) return (n / 1000).toFixed(1) + 'k'
  return n.toString()
}

function formatLatency(ms) {
  if (!ms) return '-'
  if (ms < 1000) return Math.round(ms) + 'ms'
  return (ms / 1000).toFixed(1) + 's'
}

/** LangFuse API 返回的 latency 单位是秒，转换为 ms 后复用 formatLatency */
function formatLatencySeconds(s) {
  if (!s && s !== 0) return '-'
  return formatLatency(s * 1000)
}

function statusClass(status) {
  if (status === 'success') return 'status-ok'
  if (status === 'error') return 'status-err'
  return 'status-warn'
}

function statusLabel(status) {
  if (status === 'success') return '成功'
  if (status === 'error') return '错误'
  if (status === 'loop_detected') return '循环'
  if (status === 'max_rounds') return '超轮次'
  return status || '-'
}

// ── 确认弹窗 ──

const showConfirmModal = ref(false)
const confirmTitle = ref('确认')
const confirmMessage = ref('')
const confirmResolve = ref(null)

function showConfirm(title, message) {
  return new Promise((resolve) => {
    confirmTitle.value = title
    confirmMessage.value = message
    showConfirmModal.value = true
    confirmResolve.value = resolve
  })
}

function confirmOk() {
  showConfirmModal.value = false
  if (confirmResolve.value) {
    confirmResolve.value(true)
    confirmResolve.value = null
  }
}

function confirmCancel() {
  showConfirmModal.value = false
  if (confirmResolve.value) {
    confirmResolve.value(false)
    confirmResolve.value = null
  }
}
</script>

<style scoped>
.tab-content { display: flex; flex-direction: column; flex: 1; min-height: 0; }
.section-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: var(--spacing-lg); }
.section-title { font-family: var(--font-display); font-size: 1.1rem; color: var(--text-primary); text-transform: uppercase; }
.btn-small { padding: var(--spacing-xs) var(--spacing-sm); font-size: 0.8rem; }
.btn-export { background: var(--bg-card); border-color: var(--color-primary-dim); color: var(--color-primary); text-decoration: none; display: inline-flex; align-items: center; }
.btn-export:hover { background: var(--color-primary-glow); }
.empty-state { padding: var(--spacing-xl); text-align: center; color: var(--text-dim); }
.empty-state-desc { margin-top: var(--spacing-sm); font-size: 0.85rem; color: var(--text-dim); }
.text-dim { color: var(--text-dim); font-size: 0.85rem; }

.section-header-right { display: flex; align-items: center; gap: var(--spacing-md); }
.traces-total { font-size: 0.85rem; color: var(--text-dim); }

/* Sub-tabs */
.traces-subtabs { display: flex; gap: 4px; margin-bottom: var(--spacing-md); background: var(--bg-card); border: 1px solid var(--border-color); border-radius: var(--radius-md); padding: 3px; }
.traces-subtab { flex: 1; padding: var(--spacing-sm) var(--spacing-md); background: transparent; border: none; border-radius: var(--radius-sm); color: var(--text-secondary); font-size: 0.85rem; cursor: pointer; transition: all var(--transition-fast); display: flex; align-items: center; justify-content: center; gap: var(--spacing-sm); }
.traces-subtab:hover:not(:disabled) { background: var(--bg-panel-hover); color: var(--text-primary); }
.traces-subtab.active { background: var(--bg-panel); color: var(--color-primary); box-shadow: 0 1px 3px rgba(0,0,0,0.2); }
.traces-subtab:disabled { opacity: 0.5; cursor: not-allowed; }
.traces-subtab-badge { font-size: 0.65rem; padding: 1px 6px; border-radius: 8px; background: rgba(0,229,199,0.15); color: #00e5c7; }
.traces-subtab-badge.off { background: rgba(255,107,157,0.15); color: #ff6b9d; }

/* Summary cards */
.trace-summary-row { display: grid; grid-template-columns: repeat(6, 1fr); gap: var(--spacing-md); margin-bottom: var(--spacing-md); }
.trace-summary-card { background: var(--bg-panel); border: 1px solid var(--border-color); border-radius: var(--radius-md); padding: var(--spacing-md); text-align: center; }
.ts-value { font-family: var(--font-display); font-size: 1.3rem; color: var(--color-primary); }
.ts-value.ts-error { color: #ff6b9d; }
.ts-label { font-size: 0.72rem; color: var(--text-secondary); margin-top: 2px; }

/* Filters */
.traces-filters { display: flex; gap: var(--spacing-sm); margin-bottom: var(--spacing-sm); align-items: center; }
.traces-filter-select { width: 140px; flex-shrink: 0; }
.traces-filter-input { flex: 1; min-width: 0; }

/* Toolbar */
.col-cb { width: 36px; text-align: center; }
.col-cb input[type="checkbox"] { cursor: pointer; accent-color: var(--color-primary); }
.traces-toolbar { display: flex; justify-content: space-between; align-items: center; padding: var(--spacing-sm) var(--spacing-md); background: var(--bg-card); border: 1px solid var(--border-color); border-radius: var(--radius-md); margin-bottom: var(--spacing-sm); }
.traces-toolbar-left { display: flex; align-items: center; gap: var(--spacing-sm); }
.traces-toolbar-right { display: flex; align-items: center; gap: var(--spacing-sm); }
.traces-check-all { display: flex; align-items: center; gap: var(--spacing-xs); font-size: 0.8rem; color: var(--text-secondary); cursor: pointer; user-select: none; }
.traces-check-all input[type="checkbox"] { cursor: pointer; accent-color: var(--color-primary); }
.traces-selected-count { font-size: 0.78rem; color: var(--color-primary); font-weight: 500; }

/* Table */
.traces-container { display: flex; flex-direction: column; gap: var(--spacing-sm); }
.traces-table-wrap { overflow-x: auto; background: var(--bg-panel); border: 1px solid var(--border-color); border-radius: var(--radius-lg); }
.traces-table { width: 100%; border-collapse: collapse; font-size: 0.82rem; }
.traces-table th { padding: var(--spacing-sm) var(--spacing-md); text-align: left; background: var(--bg-card); color: var(--text-secondary); font-weight: 600; font-size: 0.75rem; text-transform: uppercase; letter-spacing: 0.05em; border-bottom: 1px solid var(--border-color); white-space: nowrap; position: sticky; top: 0; z-index: 1; }
.traces-table td { padding: var(--spacing-sm) var(--spacing-md); border-bottom: 1px solid var(--border-color); color: var(--text-primary); white-space: nowrap; max-width: 200px; overflow: hidden; text-overflow: ellipsis; }
.trace-row { cursor: pointer; transition: background var(--transition-fast); }
.trace-row:hover { background: var(--bg-panel-hover); }
.trace-row.expanded { background: var(--bg-panel-hover); border-left: 3px solid var(--color-primary); }
.trace-row.status-ok { border-left: 3px solid transparent; }
.trace-row.status-ok.expanded { border-left-color: #00e5c7; }
.trace-row.status-err { border-left: 3px solid transparent; }
.trace-row.status-err.expanded { border-left-color: #ff6b9d; }
.trace-row.status-warn { border-left: 3px solid transparent; }
.trace-row.status-warn.expanded { border-left-color: #f0c060; }

.col-time { width: 90px; color: var(--text-dim) !important; font-size: 0.78rem; }
.col-msg { min-width: 160px; }
.col-model { width: 130px; font-family: var(--font-mono); font-size: 0.78rem; color: var(--text-secondary) !important; }
.col-rounds { width: 50px; text-align: center; }
.col-tools { width: 50px; text-align: center; }
.col-tokens { width: 65px; text-align: right; font-family: var(--font-mono); font-size: 0.78rem; }
.col-latency { width: 70px; text-align: right; font-family: var(--font-mono); font-size: 0.78rem; color: var(--color-primary) !important; }
.col-status { width: 70px; text-align: center; }

.status-badge { display: inline-block; padding: 2px 8px; border-radius: 10px; font-size: 0.72rem; font-weight: 600; text-transform: uppercase; }
.status-badge.status-ok { background: rgba(0,229,199,0.15); color: #00e5c7; }
.status-badge.status-err { background: rgba(255,107,157,0.15); color: #ff6b9d; }
.status-badge.status-warn { background: rgba(240,192,96,0.15); color: #f0c060; }

.traces-pagination { display: flex; align-items: center; justify-content: center; gap: var(--spacing-md); padding: var(--spacing-md); color: var(--text-dim); font-family: var(--font-mono); font-size: 0.85rem; }

/* Trace detail panel */
.trace-detail-panel { margin-top: var(--spacing-md); background: var(--bg-panel); border: 1px solid var(--border-color); border-radius: var(--radius-lg); overflow: hidden; }
.trace-detail-header { display: flex; justify-content: space-between; align-items: center; padding: var(--spacing-md) var(--spacing-lg); border-bottom: 1px solid var(--border-color); background: var(--bg-card); }
.trace-detail-header h3 { font-family: var(--font-display); font-size: 1rem; color: var(--color-primary); margin: 0; }
.trace-detail-actions { display: flex; align-items: center; gap: var(--spacing-md); }
.trace-detail-loading { padding: var(--spacing-xl); text-align: center; color: var(--text-dim); font-size: 0.85rem; animation: pulse 1.2s ease-in-out infinite; }
.trace-detail-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 1px; background: var(--border-color); }
.trace-detail-item { display: flex; flex-direction: column; gap: 2px; padding: var(--spacing-sm) var(--spacing-md); background: var(--bg-panel); }
.trace-detail-label { font-size: 0.7rem; color: var(--text-dim); text-transform: uppercase; letter-spacing: 0.05em; }
.trace-detail-value { font-size: 0.85rem; color: var(--text-primary); }
.trace-detail-value.mono { font-family: var(--font-mono); font-size: 0.75rem; color: var(--text-secondary); word-break: break-all; }
.trace-detail-value.mono.copyable { cursor: pointer; }
.trace-detail-value.mono.copyable:hover { color: var(--color-primary); }
.trace-detail-value.error-text { color: #ff6b9d; font-size: 0.8rem; }
.trace-detail-user-msg { padding: var(--spacing-md) var(--spacing-lg); border-top: 1px solid var(--border-color); }
.trace-detail-user-msg p { color: var(--text-secondary); font-size: 0.85rem; line-height: 1.6; margin-top: var(--spacing-xs); white-space: pre-wrap; word-break: break-word; }
.trace-answer { max-height: 240px; overflow-y: auto; }

/* Tool chain */
.trace-tool-chain { padding: var(--spacing-md) var(--spacing-lg); border-top: 1px solid var(--border-color); }
.trace-tool-chain h4 { font-size: 0.85rem; color: var(--text-secondary); margin: 0 0 var(--spacing-sm) 0; }
.tool-chain-list { display: flex; flex-direction: column; gap: 4px; max-height: 400px; overflow-y: auto; }
.tool-chain-step { display: flex; align-items: flex-start; gap: var(--spacing-sm); padding: var(--spacing-xs) var(--spacing-sm); background: var(--bg-card); border-radius: var(--radius-sm); font-size: 0.8rem; border-left: 2px solid var(--border-color); }
.tool-chain-step.expanded { border-left-color: var(--color-primary); }
.tool-chain-body { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 4px; }
.tool-chain-line { display: flex; align-items: center; gap: var(--spacing-sm); cursor: pointer; min-width: 0; }
.tool-chain-line.tool_call { border-left: 2px solid var(--color-primary); padding-left: var(--spacing-xs); }
.tool-chain-line.tool_result { border-left: 2px solid #00e5c7; padding-left: var(--spacing-xs); }
.tool-chain-full { background: var(--bg-dark); border-radius: var(--radius-sm); padding: var(--spacing-sm) var(--spacing-md); margin-left: var(--spacing-xs); }
.tool-chain-full-label { display: block; font-size: 0.68rem; color: var(--text-dim); text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 4px; }
.tool-chain-full pre { margin: 0; font-family: var(--font-mono); font-size: 0.75rem; color: var(--text-secondary); white-space: pre-wrap; word-break: break-word; max-height: 300px; overflow-y: auto; }
.tool-chain-expand { margin-left: auto; font-size: 0.7rem; color: var(--text-dim); flex-shrink: 0; }
.tool-chain-idx { font-family: var(--font-mono); font-size: 0.7rem; color: var(--text-dim); min-width: 20px; text-align: center; flex-shrink: 0; }
.tool-chain-badge { font-size: 0.65rem; padding: 1px 6px; border-radius: 8px; font-weight: 600; text-transform: uppercase; flex-shrink: 0; }
.tool-chain-badge.call { background: rgba(0,229,199,0.15); color: #00e5c7; }
.tool-chain-badge.result { background: rgba(124,92,255,0.15); color: #7c5cff; }
.tool-chain-name { font-weight: 600; color: var(--color-primary); flex-shrink: 0; font-family: var(--font-mono); }
.tool-chain-args { color: var(--text-dim); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-family: var(--font-mono); font-size: 0.75rem; }
.tool-chain-content { color: var(--text-secondary); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.tool-chain-latency { font-family: var(--font-mono); font-size: 0.7rem; color: var(--color-primary); flex-shrink: 0; margin-left: auto; }
.tool-chain-tokens { font-family: var(--font-mono); font-size: 0.7rem; color: var(--text-dim); flex-shrink: 0; }

@keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.5; } }

/* Modal styles */
.modal-overlay { display: flex; position: fixed; top: 0; left: 0; right: 0; bottom: 0; background: rgba(0, 0, 0, 0.7); z-index: 1000; align-items: center; justify-content: center; opacity: 0; visibility: hidden; pointer-events: none; transition: opacity var(--transition-normal), visibility var(--transition-normal); }
.modal-overlay.active { opacity: 1; visibility: visible; pointer-events: auto; }
.modal-content { background: var(--bg-panel); border: 1px solid var(--border-color); border-radius: var(--radius-lg); width: 380px; max-height: 80vh; overflow: hidden; box-shadow: 0 20px 60px rgba(0, 0, 0, 0.5); transform: translateY(12px) scale(0.98); transition: transform var(--transition-normal); }
.modal-overlay.active .modal-content { transform: none; }
.modal-sm { width: 320px; }
.modal-header { display: flex; justify-content: space-between; align-items: center; padding: var(--spacing-lg); border-bottom: 1px solid var(--border-color); }
.modal-header h2 { font-family: var(--font-display); font-size: 1.1rem; color: var(--text-primary); margin: 0; }
.modal-close { background: none; border: none; color: var(--text-dim); font-size: 1.5rem; cursor: pointer; padding: 0; line-height: 1; }
.modal-close:hover { color: var(--text-primary); }
.modal-body { padding: var(--spacing-lg); }
.modal-body p { color: var(--text-secondary); margin: 0; line-height: 1.6; }
.modal-footer { display: flex; justify-content: flex-end; gap: var(--spacing-sm); padding: var(--spacing-lg); border-top: 1px solid var(--border-color); }

@media (max-width: 768px) {
  .traces-table { font-size: 0.75rem; }
  .traces-table th, .traces-table td { padding: var(--spacing-xs) var(--spacing-sm); }
  .trace-detail-grid { grid-template-columns: repeat(2, 1fr); }
  .trace-summary-row { grid-template-columns: repeat(3, 1fr); }
  .col-cb { width: 28px; }
  .col-model, .col-tokens { display: none; }
  .traces-toolbar { flex-wrap: wrap; gap: var(--spacing-xs); }
  .traces-toolbar-right { margin-left: auto; }
  .traces-filters { flex-wrap: wrap; }
  .traces-filter-select { width: calc(50% - var(--spacing-sm)); }
  .traces-filter-input { width: 100%; flex: none; }
}
</style>
