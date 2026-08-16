<template>
  <div class="chat-page">
    <div class="chat-main">
      <div class="chat-panel">
        <div class="chat-body" :class="{ 'has-messages': hasMessages }">
        <div class="chat-messages" ref="messagesContainer" @click="handleSourceClick" @scroll.passive="handleMessagesScroll">
          <div v-if="!hasMessages" class="empty-state">
            <svg class="empty-state-icon" viewBox="0 0 24 24" width="52" height="52" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
              <path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z"/>
              <path d="M8.5 10.5h7M8.5 13.5h4" opacity="0.6"/>
            </svg>
            <div class="empty-state-title">准备就绪</div>
            <div class="empty-state-desc">向我询问关于明日方舟干员、剧情和游戏知识的问题</div>
          </div>
          <div v-else>
            <div
              v-for="(msg, idx) in sessionStore.currentSession?.messages"
              :key="`${msg.role || 'pending'}-${idx}`"
              class="chat-message"
              :class="msg.role"
              v-memo="[msg, expandedProcesses.length, expandedTools.length, expandedThinking.length, sessionStore.currentSession?.messages?.length, isLoading, editingIdx]"
            >
              <!-- User message -->
              <template v-if="msg.role === 'user'">
                <div class="chat-bubble">
                  <div class="chat-role">You</div>
                  <div v-if="editingIdx === idx" class="chat-edit-box" @click.stop>
                    <textarea
                      class="chat-edit-input"
                      v-model="editingText"
                      rows="1"
                      @keydown.enter.exact.prevent="saveEdit(idx)"
                      @keydown.esc.prevent="cancelEdit"
                    ></textarea>
                    <div class="chat-edit-actions">
                      <button class="chat-edit-btn cancel" @click="cancelEdit">取消</button>
                      <button class="chat-edit-btn save" @click="saveEdit(idx)">保存并发送</button>
                    </div>
                  </div>
                  <div v-else class="chat-text">{{ msg.content }}</div>
                </div>
                <div class="chat-msg-footer">
                  <span v-if="msg.versions && msg.versions.length > 1" class="version-pager">
                    <button
                      class="version-btn"
                      :disabled="(msg.activeVersion ?? msg.versions.length - 1) <= 0"
                      @click="switchVersion(idx, -1)"
                      title="上一版本"
                    >
                      <svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="15 18 9 12 15 6"/></svg>
                    </button>
                    <span class="version-label">{{ (msg.activeVersion ?? msg.versions.length - 1) + 1 }}/{{ msg.versions.length }}</span>
                    <button
                      class="version-btn"
                      :disabled="(msg.activeVersion ?? msg.versions.length - 1) >= msg.versions.length - 1"
                      @click="switchVersion(idx, 1)"
                      title="下一版本"
                    >
                      <svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 18 15 12 9 6"/></svg>
                    </button>
                  </span>
                  <button class="msg-action-btn" @click="startEdit(idx, msg.content)" title="编辑">
                    <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2"><path d="M11 4H4a2 2 0 00-2 2v14a2 2 0 002 2h14a2 2 0 002-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 013 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>
                  </button>
                  <button class="msg-action-btn" @click="copyMessage(msg.content)" title="复制">
                    <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 01-2-2V4a2 2 0 012-2h9a2 2 0 012 2v1"/></svg>
                  </button>
                  <div class="chat-time">{{ formatTime(new Date(msg.timestamp)) }}</div>
                </div>
              </template>

              <!-- Assistant message -->
              <template v-else-if="msg.role === 'assistant'">
                <div class="chat-bubble">
                  <div class="chat-role">Arknights RAG</div>
                  <div class="chat-text markdown-body" v-html="renderMessageWithSources(msg.content, msg.sources)"></div>
                  <div
                    class="answer-image-gallery"
                    v-if="getAnswerImages(sessionStore.currentSession?.messages, idx).length"
                  >
                    <button
                      v-for="(img, imgIdx) in getAnswerImages(sessionStore.currentSession?.messages, idx)"
                      :key="imgIdx"
                      type="button"
                      class="answer-image-item"
                      @click.stop="openImageZoom(img)"
                      :title="`点击放大：${img.label || '图片'}`"
                    >
                      <img :src="img.data_url" :alt="img.label || '立绘'" loading="lazy" />
                      <span class="answer-image-label">{{ img.label || '立绘' }}</span>
                    </button>
                  </div>
                </div>
                <div class="chat-msg-footer">
                  <button
                    v-if="idx === sessionStore.currentSession.messages.length - 1 && !isLoading"
                    class="msg-action-btn"
                    @click="regenerateLast"
                    title="重新生成"
                  >
                    <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M23 4v6h-6"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/></svg>
                  </button>
                  <button class="msg-action-btn" @click="copyMessage(msg.content)" title="复制回答">
                    <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 01-2-2V4a2 2 0 012-2h9a2 2 0 012 2v1"/></svg>
                  </button>
                  <div class="chat-time">{{ formatTime(new Date(msg.timestamp)) }}</div>
                </div>
              </template>

              <!-- Thinking display (inside collapsed agent-process card) -->
              <template v-else-if="msg.role === 'thinking'">
                <div v-if="isProcessStart(idx)" class="process-card" :class="{ 'is-expanded': isProcessExpanded(idx) }">
                  <div class="process-card-header" @click="toggleProcessCard(idx)">
                    <span class="process-card-icon">⚙️</span>
                    <span class="process-card-title">{{ getProcessTitle(idx) }}</span>
                    <span class="process-card-time" v-if="getProcessElapsed(idx) > 0">{{ formatTimeMs(getProcessElapsed(idx)) }}</span>
                    <span class="process-card-toggle">{{ isProcessExpanded(idx) ? '收起' : '展开' }}</span>
                  </div>
                </div>
                <div v-if="isProcessExpanded(idx)" class="thinking-card process-detail" @click="handleThinkingClick(idx)">
                  <div class="thinking-card-header">
                    <span class="thinking-card-round">Round {{ msg.round }}</span>
                    <span class="thinking-card-label">思考过程</span>
                    <span class="thinking-card-time" v-if="msg.time_ms">{{ formatTimeMs(msg.time_ms) }}</span>
                  </div>
                  <div class="thinking-card-preview" v-if="!expandedThinking.includes(idx)">
                    {{ msg.content.length > 60 ? msg.content.substring(0, 60) + '...' : msg.content }}
                  </div>
                  <div class="thinking-card-content" v-if="expandedThinking.includes(idx)">{{ msg.content }}</div>
                </div>
              </template>

              <!-- Tool call display (inside collapsed agent-process card) -->
              <template v-else-if="msg.role === 'tool_call'">
                <div v-if="isProcessStart(idx)" class="process-card" :class="{ 'is-expanded': isProcessExpanded(idx) }">
                  <div class="process-card-header" @click="toggleProcessCard(idx)">
                    <span class="process-card-icon">⚙️</span>
                    <span class="process-card-title">{{ getProcessTitle(idx) }}</span>
                    <span class="process-card-time" v-if="getProcessElapsed(idx) > 0">{{ formatTimeMs(getProcessElapsed(idx)) }}</span>
                    <span class="process-card-toggle">{{ isProcessExpanded(idx) ? '收起' : '展开' }}</span>
                  </div>
                </div>
                <div v-if="isProcessExpanded(idx)" class="tool-call-card process-detail">
                  <div class="tool-call-header">
                    <span class="tool-call-round">Round {{ msg.round }}</span>
                    <span class="tool-call-count">{{ msg.calls?.length || 0 }} tools</span>
                  </div>
                  <div class="tool-call-list">
                    <div
                      v-for="call in msg.calls"
                      :key="call.id"
                      :ref="el => { if (el) { toolItemRefs[call.id] = el } else { delete toolItemRefs[call.id] } }"
                      class="tool-call-item"
                      :class="{ 'has-result': msg.results?.[call.id], 'is-expanded': expandedTools.includes(call.id), 'is-interrupted': msg.results?.[call.id]?.interrupted }"
                      @click="handleToolItemClick(call.id, $event)"
                    >
                      <div class="tool-call-name-row">
                        <div class="tool-call-name">
                          <span class="tool-icon">{{ getToolIcon(call.name) }}</span>
                          {{ getToolDisplayName(call.name) }}
                        </div>
                        <div class="tool-call-meta">
                          <span class="tool-call-args" v-if="call.arguments_summary">{{ call.arguments_summary }}</span>
                          <span class="tool-result-time" v-if="msg.results?.[call.id] && !msg.results[call.id].interrupted">{{ Math.round(msg.results[call.id].time_ms) }}ms</span>
                        </div>
                      </div>
                      <div class="tool-result-summary" :class="{ 'is-interrupted-text': msg.results?.[call.id]?.interrupted }" v-if="msg.results?.[call.id] && !expandedTools.includes(call.id)">
                        {{ msg.results[call.id].summary }}
                      </div>
                      <div class="tool-call-pending" v-if="!msg.results?.[call.id]">
                        <span class="pending-dot"></span> 执行中 {{ formatElapsed(nowTs - msg.timestamp) }}
                      </div>
                      <div class="tool-result-detail" v-if="msg.results?.[call.id] && expandedTools.includes(call.id)">
                        <div class="tool-detail-summary">{{ msg.results[call.id].summary }}</div>
                        <div class="tool-detail-content" v-if="msg.results[call.id].data">
                          <template v-if="call.name === 'arknights_rag_search'">
                            <div v-for="(doc, i) in (Array.isArray(msg.results[call.id].data) ? msg.results[call.id].data : [])" :key="i" class="tool-detail-doc">
                              <div class="tool-detail-doc-header">
                                <span class="tool-detail-doc-source">{{ doc.source || 'unknown' }}</span>
                                <span class="tool-detail-doc-score">{{ doc.score ? doc.score.toFixed(4) : '' }}</span>
                              </div>
                              <div class="tool-detail-doc-content">{{ doc.content || doc.error || '' }}</div>
                            </div>
                          </template>
                          <template v-else-if="call.name === 'arknights_graphrag_search'">
                            <div class="tool-detail-graph">
                              <template v-if="msg.results[call.id].data.mode === 'path'">
                                <div class="tool-detail-graph-path">
                                  路径: <span v-for="(node, i) in msg.results[call.id].data.path" :key="i"><span class="graph-node">{{ node }}</span><span v-if="i < msg.results[call.id].data.path.length - 1" class="graph-arrow"> → </span></span>
                                </div>
                                <div v-for="(edge, i) in msg.results[call.id].data.edges" :key="i" class="tool-detail-graph-edge">
                                  <span class="graph-node">{{ edge.from }}</span>
                                  <span class="graph-relation">--{{ edge.relation }}--></span>
                                  <span class="graph-node">{{ edge.to }}</span>
                                  <div v-if="edge.description" class="graph-edge-desc">{{ edge.description }}</div>
                                </div>
                              </template>
                              <template v-else-if="msg.results[call.id].data.mode === 'neighbors'">
                                <div class="tool-detail-graph-entity">实体: {{ msg.results[call.id].data.entity }}</div>
                                <!-- Outgoing relations -->
                                <template v-if="msg.results[call.id].data.relations?.outgoing?.length">
                                  <div class="graph-direction-label">→ 出边</div>
                                  <div v-for="(rel, i) in msg.results[call.id].data.relations.outgoing" :key="'out'+i" class="tool-detail-graph-edge">
                                    <span class="graph-node">{{ msg.results[call.id].data.entity }}</span>
                                    <span class="graph-relation">--{{ rel.relation }}--></span>
                                    <span class="graph-node">{{ rel.entity }}</span>
                                    <div v-if="rel.description" class="graph-edge-desc">{{ rel.description }}</div>
                                  </div>
                                </template>
                                <!-- Incoming relations -->
                                <template v-if="msg.results[call.id].data.relations?.incoming?.length">
                                  <div class="graph-direction-label">← 入边</div>
                                  <div v-for="(rel, i) in msg.results[call.id].data.relations.incoming" :key="'in'+i" class="tool-detail-graph-edge">
                                    <span class="graph-node">{{ rel.entity }}</span>
                                    <span class="graph-relation">--{{ rel.relation }}--></span>
                                    <span class="graph-node">{{ msg.results[call.id].data.entity }}</span>
                                    <div v-if="rel.description" class="graph-edge-desc">{{ rel.description }}</div>
                                  </div>
                                </template>
                              </template>
                              <template v-else>
                                <pre>{{ formatToolResult(msg.results[call.id].data) }}</pre>
                              </template>
                            </div>
                          </template>
                          <template v-else-if="call.name === 'web_search'">
                            <div v-for="(item, i) in (Array.isArray(msg.results[call.id].data) ? msg.results[call.id].data : [])" :key="i" class="tool-detail-web">
                              <div class="tool-detail-web-title">
                                <a v-if="item.url" :href="item.url" target="_blank" rel="noopener">{{ item.title || `结果 ${i+1}` }}</a>
                                <span v-else>{{ item.title || `结果 ${i+1}` }}</span>
                              </div>
                              <div class="tool-detail-web-content">{{ item.content || item.message || item.error || '' }}</div>
                            </div>
                          </template>
                          <template v-else-if="call.name === 'arknights_structured_query'">
                            <div class="tool-detail-structured">
                              <div class="tool-detail-sql" v-if="msg.results[call.id].data.sql">
                                <code>{{ msg.results[call.id].data.sql }}</code>
                              </div>
                              <div class="tool-detail-error" v-if="msg.results[call.id].data.error">
                                {{ msg.results[call.id].data.error }}
                              </div>
                              <div class="tool-detail-table-wrapper" v-if="msg.results[call.id].data.rows?.length > 0">
                                <table class="tool-detail-table">
                                  <thead>
                                    <tr>
                                      <th v-for="col in msg.results[call.id].data.columns" :key="col">{{ col }}</th>
                                    </tr>
                                  </thead>
                                  <tbody>
                                    <tr v-for="(row, ri) in msg.results[call.id].data.rows" :key="ri">
                                      <td v-for="col in msg.results[call.id].data.columns" :key="col">{{ row[col] }}</td>
                                    </tr>
                                  </tbody>
                                </table>
                                <div class="tool-detail-row-count">{{ msg.results[call.id].data.row_count }} 行结果</div>
                              </div>
                              <div class="tool-detail-empty" v-if="msg.results[call.id].data.rows?.length === 0 && !msg.results[call.id].data.error">
                                无匹配结果
                              </div>
                            </div>
                          </template>
                          <template v-else-if="isMcpTool(call.name)">
                            <div class="tool-detail-mcp">
                              <div
                                class="tool-detail-mcp-text"
                                v-if="normalizeMcpDisplay(msg.results[call.id].data).text"
                              >
                                {{ normalizeMcpDisplay(msg.results[call.id].data).text }}
                              </div>
                              <div
                                class="tool-detail-table-wrapper"
                                v-if="normalizeMcpDisplay(msg.results[call.id].data).table"
                              >
                                <table class="tool-detail-table">
                                  <thead>
                                    <tr>
                                      <th v-for="col in normalizeMcpDisplay(msg.results[call.id].data).table.columns" :key="col">{{ col }}</th>
                                    </tr>
                                  </thead>
                                  <tbody>
                                    <tr v-for="(row, ri) in normalizeMcpDisplay(msg.results[call.id].data).table.rows" :key="ri">
                                      <td v-for="col in normalizeMcpDisplay(msg.results[call.id].data).table.columns" :key="col">{{ row[col] }}</td>
                                    </tr>
                                  </tbody>
                                </table>
                              </div>
                              <div
                                class="tool-detail-mcp-json"
                                v-else-if="normalizeMcpDisplay(msg.results[call.id].data).json"
                              >
                                <pre>{{ normalizeMcpDisplay(msg.results[call.id].data).json }}</pre>
                              </div>
                              <div
                                class="tool-detail-mcp-images"
                                v-if="normalizeMcpDisplay(msg.results[call.id].data).images.length"
                              >
                                <a
                                  v-for="(img, i) in normalizeMcpDisplay(msg.results[call.id].data).images"
                                  :key="i"
                                  :href="img.data_url"
                                  target="_blank"
                                  rel="noopener"
                                  class="tool-detail-image-link"
                                >
                                  <img
                                    :src="img.data_url"
                                    :alt="img.label || '立绘'"
                                    class="tool-detail-image"
                                    loading="lazy"
                                  />
                                </a>
                              </div>
                              <div
                                class="tool-detail-error"
                                v-if="msg.results[call.id].data?.error"
                              >
                                {{ msg.results[call.id].data.error }}
                              </div>
                            </div>
                          </template>
                          <template v-else>
                            <pre>{{ formatToolResult(msg.results[call.id].data) }}</pre>
                          </template>
                        </div>
                        <div class="tool-detail-empty" v-else>无详细数据</div>
                      </div>
                    </div>
                  </div>
                </div>
              </template>
            </div>
          </div>

          <div v-if="isLoading">
            <div class="thinking-card is-streaming" v-if="currentThinking" @click="handleThinkingClick('current')">
              <div class="thinking-card-header">
                <span class="thinking-card-round">Round {{ currentRound }}</span>
                <span class="thinking-card-label">思考过程</span>
                <span class="thinking-card-time" v-if="currentThinkingTimeMs">{{ formatTimeMs(currentThinkingTimeMs) }}</span>
              </div>
              <div class="thinking-card-preview" v-if="!expandedThinking.includes('current')">
                {{ currentThinking.length > 60 ? currentThinking.substring(0, 60) + '...' : currentThinking }}
              </div>
              <div class="thinking-card-content" v-if="expandedThinking.includes('current')">{{ currentThinking }}</div>
            </div>
            <div class="chat-message assistant" v-if="currentAnswer">
              <div class="chat-bubble">
                <div class="chat-role">Arknights RAG</div>
                <div class="current-answer markdown-body is-streaming" v-html="renderMessageWithSources(currentAnswer, currentAnswerSources)"></div>
              </div>
            </div>
            <div class="chat-message assistant" v-if="!currentAnswer && !currentThinking">
              <div class="chat-bubble">
                <div class="chat-role">Arknights RAG</div>
                <div class="typing-indicator">
                  <span></span><span></span><span></span>
                </div>
              </div>
            </div>
          </div>

          <!-- Pending message queue -->
          <div v-if="messageQueue.length > 0" class="pending-messages">
            <div class="pending-header">
              <span class="pending-badge">{{ messageQueue.length }}</span>
              <span class="pending-label">消息队列</span>
              <button class="pending-clear" @click="clearMessageQueue" title="清空队列">✕</button>
            </div>
            <div v-for="(msg, idx) in messageQueue" :key="`${msg.role || 'pending'}-${idx}`" class="pending-message">
              <span class="pending-idx">{{ idx + 1 }}</span>
              <span class="pending-text">{{ msg }}</span>
              <button class="pending-action" @click="editQueuedMessage(idx)" title="编辑">
                <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2"><path d="M11 4H4a2 2 0 00-2 2v14a2 2 0 002 2h14a2 2 0 002-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 013 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>
              </button>
              <button class="pending-action pending-delete" @click="deleteQueuedMessage(idx)" title="删除">
                <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 01-2 2H7a2 2 0 01-2-2V6m3 0V4a2 2 0 012-2h4a2 2 0 012 2v2"/></svg>
              </button>
            </div>
          </div>
        </div>

        <transition name="fade">
          <button
            v-if="!userAtBottom && hasMessages"
            class="back-to-bottom"
            :class="{ 'has-new': hasNewContent }"
            @click="jumpToBottom"
            title="回到底部"
          >
            <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"/></svg>
            <span v-if="hasNewContent" class="new-content-label">新内容</span>
          </button>
        </transition>
        </div>

        <div class="chat-input-area">
          <form class="chat-form" @submit.prevent="sendMessage">
            <div class="chat-input-wrapper">
              <textarea
                class="input chat-input"
                v-model="inputText"
                placeholder="询问关于明日方舟的问题..."
                rows="1"
                @keydown.enter.exact.prevent="sendMessage"
                @input="autoResize"
              ></textarea>
            </div>
          <button
            type="button"
            class="chat-submit"
            :class="{ 'is-stop': showStop }"
            :title="showStop ? '停止生成' : '发送'"
            @click="showStop ? stopGeneration() : sendMessage()"
          >
            <svg v-if="showStop" viewBox="0 0 24 24" width="16" height="16" fill="currentColor"><rect x="6" y="6" width="12" height="12" rx="2"/></svg>
            <svg v-else viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.5">
              <path d="M22 2L11 13"/>
              <path d="M22 2l-7 20-4-9-9-4 20-7z"/>
            </svg>
          </button>
          </form>

          <div class="quick-actions">
            <div class="quick-questions">
              <button
                v-for="(action, idx) in quickQuestionsStore.quickActions"
                :key="`qa-${idx}`"
                class="quick-action"
                @click="applyQuickAction(action.question)"
                :title="action.question"
              >
                {{ action.label }}
              </button>
            </div>
            <button class="quick-action refresh refresh-fixed" @click="refreshQuickActions" title="刷新问题" aria-label="刷新问题">
              <svg class="refresh-icon" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
                <path d="M23 4v6h-6"/>
                <path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/>
              </svg>
            </button>
          </div>
        </div>
      </div>
    </div>

    <!-- 图片放大灯箱 -->
    <div v-if="zoomImage" class="image-lightbox" @click.self="closeImageZoom">
      <div class="image-lightbox-box">
        <img :src="zoomImage.data_url" :alt="zoomImage.label || '立绘'" class="image-lightbox-img" />
        <div class="image-lightbox-label" v-if="zoomImage.label">{{ zoomImage.label }}</div>
        <button type="button" class="image-lightbox-close" @click="closeImageZoom" aria-label="关闭预览">×</button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted, onUnmounted, onActivated, onDeactivated, watch, nextTick } from 'vue'
import { useSessionStore } from '../stores/sessions'
import { useQuickQuestionsStore } from '../stores/quickQuestions'
import { useSettingsStore } from '../stores/settings'
import { useSourceDrawerStore } from '../stores/sourceDrawer'
import { api, formatTime, escapeHtml } from '../api'
import { renderMarkdown } from '../utils/markdown'
import {
  isMcpTool,
  getToolIcon as resolveToolIcon,
  getToolDisplayName as resolveToolDisplayName,
  summarizeMcpToolArgs,
  normalizeMcpDisplay,
} from '../utils/toolMeta'
import { useToastStore } from '../stores/toast'

const sessionStore = useSessionStore()
const quickQuestionsStore = useQuickQuestionsStore()
const settingsStore = useSettingsStore()
const sourceDrawerStore = useSourceDrawerStore()
const toastStore = useToastStore()

const inputText = ref('')
const isLoading = ref(false)
const currentAnswer = ref('')
const expandedTools = ref([])
const expandedThinking = ref([])
const expandedProcesses = ref([])
const zoomImage = ref(null)

const toolItemRefs = reactive({})
const currentRound = ref(0)
const currentThinking = ref('')
const currentThinkingTimeMs = ref(0)
const currentAnswerSources = ref(null)
const thinkingStartTime = ref(0)
const messagesContainer = ref(null)
const messageQueue = ref([])
const abortController = ref(null)
const originalSessionId = ref(null)

const hasMessages = computed(() => !!(sessionStore.currentSession && sessionStore.currentSession.messages?.length > 0))
const hasNewContent = ref(false)
// 生成中且输入框为空时才显示停止按钮；输入框有内容时保持发送按钮（点击进消息队列）
const showStop = computed(() => isLoading.value && !inputText.value.trim())
// 用户消息行内编辑状态
const editingIdx = ref(-1)
const editingText = ref('')
// Ticker for live "executing ... Xs" elapsed display on pending tool calls
const nowTs = ref(Date.now())
let elapsedTickerId = null
// 每次 startAgentStream 递增；用于防止旧后台流结束时覆盖新流的 UI 状态/AbortController
let streamGeneration = 0

function startElapsedTicker() {
  if (elapsedTickerId !== null) return
  elapsedTickerId = setInterval(() => { nowTs.value = Date.now() }, 200)
}

function stopElapsedTicker() {
  if (elapsedTickerId !== null) {
    clearInterval(elapsedTickerId)
    elapsedTickerId = null
  }
}

function formatElapsed(ms) {
  if (!ms || ms < 0) return ''
  return `${(ms / 1000).toFixed(1)}s`
}

// Initialize on mount
onMounted(() => {
  console.log('[ChatView] mounted, currentSession:', sessionStore.currentSession?.id)

  // 只初始化一次快速问题
  if (!quickQuestionsStore.hasInitialized) {
    console.log('[ChatView] First time initializing quick actions')

    // 初始化快速问题（加载真实数据）
    loadQuickQuestionsData()
    console.log('[ChatView] started loading quick actions data')

    quickQuestionsStore.markAsInitialized()
  } else {
    console.log('[ChatView] Quick actions already initialized, skipping')
  }

  // 清扫历史会话里残留的执行中工具调用（例如上次页面在工具执行中被关闭）
  sessionStore.finalizePendingToolCalls()

  // 页面加载时滚动到底部
  nextTick(() => scrollToBottom())
  // 浏览器可能在加载后恢复上次的滚动位置（且 0→0 时不触发 scroll 事件），
  // 延迟同步一次 userAtBottom，保证"回到底部"按钮状态与实际位置一致
  setTimeout(() => handleMessagesScroll(), 300)
})

// Component deactivated (switched to another page) — keep request running in background
onDeactivated(() => {
  console.log('[ChatView] deactivated, request continues in background')
})

// Component reactivated (switched back) — restore UI state
onActivated(() => {
  console.log('[ChatView] activated, isLoading:', isLoading.value)
  nextTick(() => scrollToBottom())
})

// Only abort on true unmount (e.g. HMR, app destroy)
onUnmounted(() => {
  console.log('[ChatView] unmounted')
  stopElapsedTicker()
})

// Watch for session changes to update lastResult
watch(() => sessionStore.currentSessionId, (newId, oldId) => {
  console.log('[ChatView] session changed from', oldId, 'to', newId)

  // 只有真正切换到不同会话时才清理流式输出状态
  if (newId !== oldId) {
    // 如果是从null到有效ID，可能是初始加载，不中止请求
    if (oldId !== null) {
      // Save in-progress content to the OLD session, then clear UI for the new session.
      // The SSE stream keeps running — its callbacks target the old sessionId, so the
      // final answer will land in the correct session even after the user switches away.
      if (isLoading.value && currentThinking.value) {
        const thinkTime = thinkingStartTime.value ? Date.now() - thinkingStartTime.value : 0
        sessionStore.addThinkingMessageTo(oldId, currentRound.value, currentThinking.value, Math.round(thinkTime))
      }
      // Save partial answer with a marker so onAnswerDone can replace it
      if (isLoading.value && currentAnswer.value) {
        sessionStore.addMessageTo(oldId, 'assistant', currentAnswer.value + ' [回答中...]', { _partial: true })
      }
      // Clear display refs but do NOT abort — the stream continues in background
      isLoading.value = false
      currentAnswer.value = ''
      currentAnswerSources.value = null
      currentThinking.value = ''
      currentThinkingTimeMs.value = 0
    }
  }

  // 切换会话时滚动到底部
  nextTick(() => scrollToBottom())

  // 如果是新建的会话（消息为空），刷新快速问题
  if (!sessionStore.currentSession || sessionStore.currentSession.messages?.length === 0) {
    console.log('[ChatView] New session detected, refreshing quick actions')
    refreshQuickActions()
  }
})


function handleThinkingClick(idx) {
  const selection = window.getSelection()
  // Allow expansion during streaming (idx === 'current') even with tiny selections
  if (idx !== 'current' && selection && selection.toString().length > 3) return
  toggleThinking(idx)
}

function handleToolItemClick(toolCallId, event) {
  // If user selected text, don't toggle (they were trying to copy)
  const selection = window.getSelection()
  if (selection && selection.toString().length > 0) return
  toggleToolResult(toolCallId)
}

function toggleToolResult(toolCallId) {
  const i = expandedTools.value.indexOf(toolCallId)
  if (i > -1) {
    expandedTools.value.splice(i, 1)
    nextTick(() => {
      const el = toolItemRefs[toolCallId]
      if (el) el.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
    })
  } else {
    expandedTools.value.push(toolCallId)
  }
}

function formatTimeMs(ms) {
  if (!ms || ms <= 0) return ''
  return `${Math.round(ms)}ms`
}

// ===== Agent 过程折叠卡片（思考 + 工具调用整体折叠为一个「工具调用 N 轮」卡片） =====
function isProcessRole(role) {
  return role === 'thinking' || role === 'tool_call'
}

function getProcessStartIndex(idx) {
  const messages = sessionStore.currentSession?.messages || []
  let start = idx
  while (start > 0 && isProcessRole(messages[start - 1]?.role)) start--
  return start
}

function getProcessMessages(idx) {
  const messages = sessionStore.currentSession?.messages || []
  const start = getProcessStartIndex(idx)
  const group = []
  for (let i = start; i < messages.length; i++) {
    if (!isProcessRole(messages[i]?.role)) break
    group.push(messages[i])
  }
  return group
}

function isProcessStart(idx) {
  const messages = sessionStore.currentSession?.messages || []
  return idx === 0 || !isProcessRole(messages[idx - 1]?.role)
}

function isProcessExpanded(idx) {
  return expandedProcesses.value.includes(`proc-${getProcessStartIndex(idx)}`)
}

function getProcessTitle(idx) {
  const group = getProcessMessages(idx)
  const toolRounds = group.filter(m => m.role === 'tool_call').length
  return toolRounds > 0 ? `工具调用 · ${toolRounds} 轮` : '思考过程'
}

function getProcessElapsed(idx) {
  const group = getProcessMessages(idx)
  let total = 0
  for (const m of group) {
    if (m.role === 'thinking') {
      total += m.time_ms || 0
    } else if (m.role === 'tool_call' && m.results) {
      for (const call of (m.calls || [])) {
        total += m.results[call.id]?.time_ms || 0
      }
    }
  }
  return total
}

function toggleProcessCard(idx) {
  const id = `proc-${getProcessStartIndex(idx)}`
  const i = expandedProcesses.value.indexOf(id)
  if (i > -1) {
    expandedProcesses.value.splice(i, 1)
    // 收起整张过程卡片时，把内部已展开的思考/工具也一并复位，下次打开仍是默认折叠
    const start = getProcessStartIndex(idx)
    const group = getProcessMessages(idx)
    const thinkingIdxs = new Set(
      group.map((m, offset) => m.role === 'thinking' ? start + offset : -1).filter(v => v >= 0)
    )
    const toolIds = new Set(
      group.flatMap(m => m.role === 'tool_call' ? (m.calls || []).map(c => c.id) : [])
    )
    expandedThinking.value = expandedThinking.value.filter(
      v => v === 'current' || !thinkingIdxs.has(v)
    )
    expandedTools.value = expandedTools.value.filter(v => !toolIds.has(v))
  } else {
    expandedProcesses.value.push(id)
  }
}

function formatToolResult(data) {
  if (data === null || data === undefined) return '无数据'
  if (typeof data === 'object') return JSON.stringify(data, null, 2)
  return String(data)
}

const MAX_ANSWER_IMAGES = 8

// 收集回答前这一轮工具调用返回的图片（立绘），按调用顺序展示在最终回答里
function getAnswerImages(messages, assistantIdx) {
  if (!Array.isArray(messages) || assistantIdx == null) return []
  const images = []
  const seen = new Set()
  for (let i = assistantIdx - 1; i >= 0; i--) {
    const msg = messages[i]
    if (!msg) continue
    if (msg.role === 'user' || msg.role === 'assistant') break
    if (msg.role !== 'tool_call') continue
    for (const call of (msg.calls || [])) {
      const data = msg.results?.[call.id]?.data
      const list = Array.isArray(data?.images) ? data.images : []
      for (const img of list) {
        if (img?.data_url && !seen.has(img.data_url)) {
          seen.add(img.data_url)
          images.unshift(img)
          if (images.length >= MAX_ANSWER_IMAGES) return images
        }
      }
    }
  }
  return images
}

function openImageZoom(img) {
  zoomImage.value = img || null
}

function closeImageZoom() {
  zoomImage.value = null
}

function renderMessageWithSources(content, messageSources) {
  if (!content) return ''

  // Markdown 渲染 + DOMPurify 消毒（替代原先纯文本转义，支持富文本回答）
  let html = renderMarkdown(content)

  // Build a lookup from chunk_id → source data for web URLs
  const sourceByChunkId = {}
  const webSources = []
  if (messageSources && Array.isArray(messageSources)) {
    for (const s of messageSources) {
      if (s.chunk_id) {
        sourceByChunkId[s.chunk_id] = s
      } else if (s.source_id === 'web' && s.url) {
        webSources.push(s)
      }
    }
  }
  let webIndex = 0

  // Parse and link source citations
  // Match chunk_id patterns like (operators_0103_02) or (enemies_json_1587)
  html = html.replace(
    /\(([a-z]+_[a-z0-9_]+)\)/g,
    (match, chunkId) => {
      // Use collection from structured sources if available, else infer from prefix
      let collection = inferCollection(chunkId, sourceByChunkId)

      return `(<span class="source-link" data-chunk-id="${escapeHtml(chunkId)}" data-collection="${escapeHtml(collection)}" title="点击查看原文">${escapeHtml(chunkId)}</span>)`
    }
  )

  // Make web source references clickable with actual URLs when available
  html = html.replace(
    /\(web\)/g,
    () => {
      const ws = webSources[webIndex]
      webIndex++
      if (ws && ws.url) {
        return `(<span class="source-link source-link-web" data-url="${escapeHtml(ws.url)}" data-source-id="web" title="${escapeHtml(ws.title || '网页来源')}">网页来源</span>)`
      }
      return '(<span class="source-link source-link-web" data-source-id="web">网页来源</span>)'
    }
  )

  return html
}

function handleSourceClick(event) {
  const link = event.target.closest('.source-link')
  if (!link) return
  event.preventDefault()

  const url = link.dataset.url
  if (url) {
    window.open(url, '_blank', 'noopener,noreferrer')
    return
  }

  const chunkId = link.dataset.chunkId
  const collection = link.dataset.collection
  if (chunkId && collection) {
    sourceDrawerStore.open({ chunk_id: chunkId, collection })
  }
}

function inferCollection(chunkId, sourceLookup) {
  if (!chunkId) return 'knowledge'
  // First, check structured sources from answer_done for the correct collection
  if (sourceLookup && sourceLookup[chunkId] && sourceLookup[chunkId].collection) {
    return sourceLookup[chunkId].collection
  }
  // Knowledge collection (.txt files) — check specific prefixes FIRST
  // to avoid being matched by broader prefixes below
  if (chunkId.startsWith('operators_json_')) return 'knowledge'
  if (chunkId.startsWith('enemies_json_')) return 'knowledge'
  if (chunkId.startsWith('enemies_summary_')) return 'knowledge'
  if (chunkId.startsWith('operators_summary_')) return 'knowledge'
  if (chunkId.startsWith('char_summary_')) return 'knowledge'
  if (chunkId.startsWith('story_summary_')) return 'knowledge'
  if (chunkId.startsWith('knowledge_')) return 'knowledge'
  if (chunkId.startsWith('gameplay_')) return 'knowledge'
  if (chunkId.startsWith('memes_')) return 'knowledge'
  // Operators and stories (.md files)
  if (chunkId.startsWith('operators_')) return 'operators'
  if (chunkId.startsWith('stories_')) return 'stories'
  return 'knowledge'  // default
}

function toggleThinking(idx) {
  const i = expandedThinking.value.indexOf(idx)
  if (i > -1) {
    expandedThinking.value.splice(i, 1)
  } else {
    expandedThinking.value.push(idx)
  }
}

/**
 * Extract <think/> or <think ...>...</think > content from text.
 * Some models embed reasoning inside <think/> tags in the content field.
 * Returns { text: cleanedText, thinking: extractedThinking }
 */
function extractThinkContent(text) {
  if (!text) return { text: '', thinking: '' }
  let thinking = ''
  let cleaned = text
  // Match <think ...>...</think > (including self-closing <think/>)
  // Use global regex to handle multiple think blocks
  const thinkRegex = /<think[^>]*>([\s\S]*?)<\/think\s*>/gi
  let match
  while ((match = thinkRegex.exec(text)) !== null) {
    thinking += match[1].trim()
  }
  cleaned = text.replace(thinkRegex, '').trim()
  // Also handle self-closing <think/> or <think /> with no content
  cleaned = cleaned.replace(/<think\s*\/>/gi, '').trim()
  return { text: cleaned, thinking }
}

function clearMessageQueue() {
  messageQueue.value = []
}

function deleteQueuedMessage(idx) {
  messageQueue.value.splice(idx, 1)
}

function editQueuedMessage(idx) {
  inputText.value = messageQueue.value[idx]
  messageQueue.value.splice(idx, 1)
  nextTick(() => {
    const textarea = document.querySelector('.chat-input')
    if (textarea) {
      textarea.focus()
      autoResize({ target: textarea })
    }
  })
}

function autoResize(e) {
  const textarea = e.target
  const content = textarea.value.trim()
  if (!content) {
    textarea.style.height = 'auto'
    textarea.style.overflowY = 'hidden'
  } else {
    textarea.style.height = 'auto'
    textarea.style.height = Math.min(textarea.scrollHeight, 150) + 'px'
    textarea.style.overflowY = textarea.scrollHeight > 150 ? 'auto' : 'hidden'
  }
}

async function sendMessage() {
  const content = inputText.value.trim()
  if (!content) return

  // If already loading, add to queue (max 20 messages)
  if (isLoading.value) {
    if (messageQueue.value.length < 20) {
      messageQueue.value.push(content)
      inputText.value = ''
    }
    return
  }

  inputText.value = ''
  // Reset textarea height
  nextTick(() => {
    const textarea = document.querySelector('.chat-input')
    if (textarea) {
      textarea.style.height = 'auto'
      textarea.style.overflowY = 'hidden'
    }
  })

  sessionStore.addMessage('user', content)
  await startAgentStream(content)
}

// 启动一次 Agent 流式对话（消息已存在于会话中，如用户发送/编辑重发/重新生成）
async function startAgentStream(content) {
  const generation = ++streamGeneration
  isLoading.value = true
  hasNewContent.value = false
  startElapsedTicker()
  currentAnswer.value = ''
  pendingAnswerDelta = ''
  currentAnswerSources.value = null
  expandedTools.value = []
  expandedThinking.value = []
  expandedProcesses.value = []
  currentThinking.value = ''
  currentThinkingTimeMs.value = 0
  thinkingStartTime.value = 0
  currentRound.value = 0

  // 本次流的本地状态。切走会话后旧流继续在后台运行时，
  // 其 SSE 回调只更新这些局部变量与旧会话，不再污染新流的全局 UI 状态。
  let streamThinking = ''
  let streamThinkingStart = 0
  let streamRound = 0
  let streamPendingDelta = ''
  const isCurrentStream = () => generation === streamGeneration

  // Capture session ID AFTER addMessage (which may create a new session)
  const streamSessionId = sessionStore.currentSessionId
  originalSessionId.value = streamSessionId
  userAtBottom.value = true
  nextTick(() => scrollToBottom())

  // Create AbortController for cancellation
  if (abortController.value) {
    abortController.value.abort()
  }
  const controller = new AbortController()
  abortController.value = controller

  // Get or create backend session
  let backendSessionId = sessionStore.getBackendSessionId()
  if (!backendSessionId) {
    try {
      const result = await api.createAgentSession()
      backendSessionId = result.session_id
      if (streamSessionId) {
        sessionStore.backendSessionIds[streamSessionId] = backendSessionId
        localStorage.setItem('arknights_rag_backend_sessions', JSON.stringify(sessionStore.backendSessionIds))
      }
    } catch (e) {
      console.error('Failed to create backend session:', e)
      sessionStore.addMessage('assistant', '错误: 无法创建会话，请重试')
      isLoading.value = false
      return
    }
  }

  try {
    await api.agentChat({
      sessionId: backendSessionId,
      message: content,
      model: settingsStore.currentModel || undefined,
      signal: controller.signal,

      onNewSessionId(newSid) {
        // Server auto-created a new session (old one expired)
        console.log('[ChatView] Session expired, server created new session:', newSid)
        backendSessionId = newSid
        if (streamSessionId) {
          sessionStore.backendSessionIds[streamSessionId] = newSid
          localStorage.setItem('arknights_rag_backend_sessions', JSON.stringify(sessionStore.backendSessionIds))
        }
      },

      onThinkingStart(event) {
        streamRound = event.round || streamRound + 1
        streamThinking = ''
        streamThinkingStart = event.timestamp_ms || Date.now()
        if (isCurrentStream()) {
          currentRound.value = streamRound
          currentThinking.value = ''
          currentThinkingTimeMs.value = 0
          thinkingStartTime.value = streamThinkingStart
        }
      },

      onToolCallsStart(event) {
        // Save any accumulated thinking content as a thinking message（始终写入本次流所属会话）
        if (streamThinking) {
          const thinkTime = streamThinkingStart ? Date.now() - streamThinkingStart : 0
          sessionStore.addThinkingMessageTo(streamSessionId, streamRound, streamThinking, Math.round(thinkTime))
          streamThinking = ''
        }
        // Discard any stray answer content (tool round doesn't produce final answer)
        streamPendingDelta = ''
        streamRound = event.round || streamRound + 1
        const calls = event.tool_calls.map(tc => ({
          id: tc.id,
          name: tc.name,
          arguments_summary: summarizeToolArgs(tc.name, tc.arguments),
        }))
        sessionStore.addToolCallMessage(calls, streamRound, streamSessionId)
        if (isCurrentStream()) {
          currentAnswer.value = ''
          pendingAnswerDelta = ''
          currentThinking.value = ''
          currentThinkingTimeMs.value = 0
          currentRound.value = streamRound
        }
      },

      onToolExecuting(event) {
        // A tool has started executing — no auto-scroll
      },

      onToolCallResult(event) {
        sessionStore.updateToolCallResult(event.tool_call_id, {
          summary: event.summary || '完成',
          time_ms: event.time_ms || 0,
          tool_name: event.tool_name || '',
          result: event.result || null,
        }, streamSessionId)
      },

      onAnswerDelta(event) {
        // Backend already parses <think/> tags, so content_delta is pure answer text
        streamPendingDelta += event.delta || ''
        if (isCurrentStream()) {
          pendingAnswerDelta += event.delta || ''
          if (!userAtBottom.value) hasNewContent.value = true
          scheduleAnswerFlush()
        }
      },

      onThinkingDelta(event) {
        streamThinking += event.content || ''
        if (isCurrentStream()) {
          currentThinking.value += event.content || ''
          if (!userAtBottom.value) hasNewContent.value = true
          if (thinkingStartTime.value) {
            currentThinkingTimeMs.value = Date.now() - thinkingStartTime.value
          }
        }
      },

      onThinkingDone(event) {
        // Replace accumulated thinking with complete content from backend
        // This ensures we have the full thinking even if delta streaming was incomplete
        if (event.reasoning_content) {
          streamThinking = event.reasoning_content
          if (isCurrentStream()) {
            currentThinking.value = event.reasoning_content
          }
        }
      },

      onAnswerDone(event) {
        const thinkTime = streamThinkingStart ? Date.now() - streamThinkingStart : 0
        // Save thinking as independent message if present（始终写入本次流所属会话）
        if (streamThinking) {
          sessionStore.addThinkingMessageTo(streamSessionId, streamRound, streamThinking, Math.round(thinkTime))
        }
        // Filter <think/> tags from the final answer
        const rawAnswer = event.answer || (isCurrentStream()
          ? (currentAnswer.value + pendingAnswerDelta)
          : streamPendingDelta)
        const { text: cleanAnswer, thinking: trailingThinking } = extractThinkContent(rawAnswer)
        if (trailingThinking && !streamThinking) {
          sessionStore.addThinkingMessageTo(streamSessionId, streamRound, trailingThinking)
        }
        // Write complete answer; remove any partial answer the session-switch handler
        // may have saved (to avoid duplicate assistant messages)
        const eventSources = event.sources || []
        sessionStore.replaceLastAssistantIfPartial(streamSessionId, cleanAnswer, {
          sources: eventSources,
          metrics: event.metrics || {},
        })
        // 只有当前流才更新展示层状态，避免旧后台流覆盖新会话的 UI
        if (isCurrentStream()) {
          flushPendingDelta()
          currentAnswerSources.value = eventSources
          currentAnswer.value = ''
          pendingAnswerDelta = ''
          currentThinking.value = ''
          currentThinkingTimeMs.value = 0
          thinkingStartTime.value = 0
          // 回答完成后：思考过程与工具调用整体折叠为一个「工具调用 N 轮」卡片
          expandedThinking.value = []
          expandedTools.value = []
          expandedProcesses.value = []
          // Scroll to bottom when answer is complete
          nextTick(() => scrollToBottom())
        }
      },

      onError(event) {
        console.error('Agent error:', event.message)
        // 错误始终写入本次流所属会话；旧后台流不覆盖新会话的展示层状态
        sessionStore.addMessageTo(streamSessionId, 'assistant', `错误: ${event.message || '未知错误'}`)
      },
    })
  } catch (error) {
    // 保存本次流的局部 partial 状态。旧后台流只写自己的会话，不碰当前 UI。
    if (isCurrentStream()) flushPendingDelta()
    const partialThinking = streamThinking
    const partialAnswer = isCurrentStream()
      ? currentAnswer.value
      : streamPendingDelta
    const partialThinkTime = streamThinkingStart ? Date.now() - streamThinkingStart : 0

    if (isCurrentStream()) {
      currentThinking.value = ''
      currentThinkingTimeMs.value = 0
    }

    if (error.name === 'AbortError') {
      console.log('[ChatView] Request aborted')
      if (partialThinking) {
        sessionStore.addThinkingMessageTo(streamSessionId, streamRound, partialThinking, Math.round(partialThinkTime))
      }
      if (partialAnswer) {
        sessionStore.addMessageTo(streamSessionId, 'assistant', partialAnswer + ' [已中断]')
      }
    } else {
      console.error('[ChatView] Agent chat error:', error)
      if (partialThinking) {
        sessionStore.addThinkingMessageTo(streamSessionId, streamRound, partialThinking, 0)
      }
      sessionStore.addMessageTo(streamSessionId, 'assistant', partialAnswer || `错误: ${error.message}`)
    }
  }

  // 流结束（正常完成/出错/被中断）时，把仍未返回结果的工具调用标记为"已中断"，
  // 避免它们永远停留在"执行中..."状态（始终写本次流所属会话）
  sessionStore.finalizePendingToolCalls(streamSessionId)

  // 只有当前活动流才能清理/更新全局 UI 状态与 AbortController；
  // 旧后台流结束不能覆盖新流的 isLoading/stop 按钮等状态。
  if (!isCurrentStream()) return

  abortController.value = null
  isLoading.value = false
  stopElapsedTicker()
  // 无论正常完成还是中断/出错，思考与工具面板默认折叠
  expandedThinking.value = []
  expandedTools.value = []
  expandedProcesses.value = []
  nextTick(() => scrollToBottom())

  // Only process queue if still on the original session
  if (messageQueue.value.length > 0 && sessionStore.currentSessionId === originalSessionId.value) {
    const nextContent = messageQueue.value.shift()
    inputText.value = nextContent
    sendMessage()
  }
}

function summarizeToolArgs(toolName, args) {
  // args is already a parsed object (not a string) from the SSE event
  if (!args || typeof args === 'string') {
    try { args = JSON.parse(args || '{}') } catch { return String(args).substring(0, 80) }
  }
  switch (toolName) {
    case 'arknights_rag_search':
      return `查询: "${args.query || ''}"`
    case 'arknights_graphrag_search':
      return args.entity1 && args.entity2
        ? `关系: ${args.entity1} → ${args.entity2}`
        : `实体: ${args.entity || ''}`
    case 'web_search':
      return `搜索: "${args.query || ''}"`
    case 'arknights_structured_query':
      return `SQL: "${(args.sql || '').substring(0, 60)}"`
    default:
      if (isMcpTool(toolName)) return summarizeMcpToolArgs(toolName, args)
      return JSON.stringify(args).substring(0, 80)
  }
}

function getToolIcon(name) {
  return resolveToolIcon(name)
}

function getToolDisplayName(name) {
  return resolveToolDisplayName(name)
}

async function loadQuickQuestionsData(refresh = false) {
  if (quickQuestionsStore.isLoading) return;

  quickQuestionsStore.setLoading(true);
  try {
    console.log('正在从后端加载快速问题...');
    const res = await api.getQuickQuestions(refresh);
    const questions = res.questions || [];
    if (questions.length > 0) {
      quickQuestionsStore.setQuickActions(questions);
      console.log('快速问题加载完成:', questions);
    } else {
      throw new Error('Empty questions');
    }
  } catch (error) {
    console.error('加载快速问题失败:', error);
    // fallback
    const fallbackActions = [
      { label: '银灰技能', question: '银灰的技能是什么？', type: 'skill', category: 'rag' },
      { label: '陈/史尔特尔', question: '陈和史尔特尔的关系', type: 'relation', category: 'graph' },
      { label: '高攻击近卫', question: '哪些六星近卫的精二满级攻击力大于800？', type: 'structured', category: 'structured' },
      { label: '1-7出怪', question: '1-7关卡的出怪顺序是什么？', type: 'stage', category: 'prts_mcp' }
    ];
    quickQuestionsStore.setQuickActions(fallbackActions);
  } finally {
    quickQuestionsStore.setLoading(false);
  }
}

async function refreshQuickActions(event) {
  console.log('[ChatView] refreshQuickActions called')
  // Play rotation animation on the clicked button (page has two refresh icons,
  // and one of them may be hidden, so don't rely on document.querySelector)
  const trigger = event?.currentTarget
  const refreshIcon = trigger
    ? trigger.querySelector('.refresh-icon')
    : document.querySelector('.refresh-icon')
  if (refreshIcon) {
    refreshIcon.classList.remove('rotating');
    void refreshIcon.offsetWidth;
    refreshIcon.classList.add('rotating');
    setTimeout(() => {
      refreshIcon.classList.remove('rotating');
    }, 600);
  }
  await loadQuickQuestionsData(true)
}

let _scrollRafId = null
function scrollToBottom(smooth = false) {
  if (_scrollRafId !== null) return
  _scrollRafId = requestAnimationFrame(() => {
    _scrollRafId = null
    const el = messagesContainer.value
    if (el) {
      if (smooth) {
        el.scrollTo({ top: el.scrollHeight, behavior: 'smooth' })
      } else {
        el.scrollTop = el.scrollHeight
      }
    }
  })
}

function jumpToBottom() {
  userAtBottom.value = true
  hasNewContent.value = false
  scrollToBottom(true)
}

// ===== 流式渲染优化：rAF 批量刷新 + 智能跟随滚动 =====
const userAtBottom = ref(true)
let pendingAnswerDelta = ''
let answerFlushScheduled = false

function flushPendingDelta() {
  if (pendingAnswerDelta) {
    currentAnswer.value += pendingAnswerDelta
    pendingAnswerDelta = ''
  }
}

function scheduleAnswerFlush() {
  if (answerFlushScheduled) return
  answerFlushScheduled = true
  requestAnimationFrame(() => {
    answerFlushScheduled = false
    flushPendingDelta()
    // 仅当用户位于底部附近时才自动跟随滚动，上翻阅读时不打断
    if (userAtBottom.value) scrollToBottom()
  })
}

function handleMessagesScroll() {
  const el = messagesContainer.value
  if (!el) return
  const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 60
  userAtBottom.value = atBottom
  if (atBottom) hasNewContent.value = false
}

// 重新生成：回滚到最后一条用户消息之前，然后以其内容重新发起对话
function regenerateLast() {
  const session = sessionStore.currentSession
  if (!session || isLoading.value) return
  const msgs = session.messages
  let userIdx = -1
  for (let i = msgs.length - 1; i >= 0; i--) {
    if (msgs[i].role === 'user') { userIdx = i; break }
  }
  if (userIdx === -1) return
  const content = msgs[userIdx].content
  // truncateMessages 的 fromIndex 是包含该索引的，+1 才能保留要重发的 user 消息
  sessionStore.truncateMessages(session.id, userIdx + 1)
  startAgentStream(content)
}

function stopGeneration() {
  abortController.value?.abort()
}

// ============ 用户消息编辑与版本分支 ============
function startEdit(idx, content) {
  if (isLoading.value) return
  editingIdx.value = idx
  editingText.value = content
  nextTick(() => {
    const textarea = document.querySelector('.chat-edit-input')
    if (textarea) {
      textarea.focus()
      textarea.style.height = 'auto'
      textarea.style.height = Math.min(textarea.scrollHeight, 200) + 'px'
      // 光标移到末尾
      const len = textarea.value.length
      textarea.setSelectionRange(len, len)
    }
  })
}

function cancelEdit() {
  editingIdx.value = -1
  editingText.value = ''
}

async function saveEdit(idx) {
  const session = sessionStore.currentSession
  const newContent = editingText.value.trim()
  cancelEdit()
  if (!session || !newContent || isLoading.value) return
  const msg = session.messages[idx]
  if (!msg || msg.role !== 'user') return
  // 内容没变则不产生任何操作
  if (newContent === msg.content) return
  // 创建分支：旧版本（含后续对话）存为历史版本，新版本作为最新分支并重发
  sessionStore.branchUserMessage(session.id, idx, newContent)
  await startAgentStream(newContent)
}

function switchVersion(idx, delta) {
  const session = sessionStore.currentSession
  if (!session || isLoading.value) return
  sessionStore.switchUserVersion(session.id, idx, delta)
  nextTick(() => scrollToBottom())
}

async function copyMessage(content) {
  try {
    await navigator.clipboard.writeText(content)
    toastStore.show('已复制到剪贴板')
  } catch {
    toastStore.show('复制失败', 'error')
  }
}

function applyQuickAction(question) {
  inputText.value = question
  nextTick(() => {
    const textarea = document.querySelector('.chat-input')
    if (textarea) {
      textarea.focus()
      autoResize({ target: textarea })
    }
  })
}
</script>

<style scoped>
.chat-page { display: flex; flex-direction: column; flex: 1; min-height: 0; }
.chat-main { flex: 1; display: flex; overflow: hidden; }
.chat-panel { flex: 1; display: flex; flex-direction: column; min-width: 0; }
.chat-body { position: relative; flex: 1; min-height: 0; display: flex; flex-direction: column; }
.chat-messages { flex: 1; overflow-y: auto; overflow-x: hidden; padding: var(--spacing-lg); min-height: 0; overscroll-behavior: contain; }
/* 宽屏下将消息列限宽居中，缩短阅读视线移动距离 */
.chat-messages > * { max-width: 1000px; width: 100%; margin-left: auto; margin-right: auto; }
.chat-input-area { padding: var(--spacing-md); background: var(--bg-panel); border-top: 1px solid var(--border-color); }
.chat-form { display: flex; gap: var(--spacing-md); align-items: flex-end; }
.chat-input-wrapper { flex: 1; position: relative; }
.chat-input { width: 100%; padding: var(--spacing-md); resize: none; min-height: 50px; max-height: 150px; box-sizing: border-box; overflow-y: hidden; -webkit-overflow-scrolling: auto; overscroll-behavior: contain; }
.chat-submit { width: 44px; height: 44px; padding: 0; margin-bottom: 3px; background: linear-gradient(135deg, var(--color-primary) 0%, var(--color-primary-dim) 100%); border: none; border-radius: 12px; color: var(--bg-deep); cursor: pointer; transition: all var(--transition-fast); display: flex; align-items: center; justify-content: center; flex-shrink: 0; }
.chat-submit:hover { transform: scale(1.05); box-shadow: 0 0 20px var(--color-primary-glow); }
.chat-submit:active { transform: scale(0.95); }
.chat-submit:disabled { opacity: 0.5; cursor: not-allowed; transform: none; }
.chat-submit.is-stop { background: var(--bg-dark); border: 1px solid var(--color-danger); color: var(--color-danger); }
.chat-submit.is-stop:hover { background: var(--color-danger); color: #fff; box-shadow: 0 0 16px rgba(255, 71, 87, 0.4); }

/* 回到底部悬浮按钮 */
.back-to-bottom { position: absolute; bottom: var(--spacing-md); left: 50%; transform: translateX(-50%); z-index: 20; display: flex; align-items: center; gap: 6px; padding: 8px 14px; background: var(--bg-panel); border: 1px solid var(--border-color); border-radius: 20px; color: var(--text-secondary); cursor: pointer; box-shadow: var(--shadow-md); transition: all var(--transition-fast); }
.back-to-bottom:hover { border-color: var(--color-primary); color: var(--color-primary); transform: translateX(-50%) translateY(-2px); }
.back-to-bottom.has-new { border-color: var(--color-primary-dim); color: var(--color-primary); }
.new-content-label { font-size: 0.75rem; font-weight: 600; }
.fade-enter-active, .fade-leave-active { transition: opacity var(--transition-normal), transform var(--transition-normal); }
.fade-enter-from, .fade-leave-to { opacity: 0; transform: translateX(-50%) translateY(8px); }


/* 快捷问题统一位于输入框下方；空状态不再显示居中的重复按钮组 */

/* Mobile: hide sidebar, full-screen chat */
@media (max-width: 768px) {
  .chat-input-area { padding: var(--spacing-md); padding-bottom: calc(var(--spacing-md) + env(safe-area-inset-bottom)); }
  .chat-input { font-size: 16px; min-height: 44px; padding: 10px 12px; }
  .chat-messages { padding: var(--spacing-md); }
  /* 快捷问题统一放到输入框下方单行 */
  /* 输入框下方始终显示：问题区可横滑，刷新键固定在最右不随内容滚动 */
  .chat-body:not(.has-messages) .chat-input-area .quick-actions { display: flex; }
  .chat-input-area .quick-actions { gap: 0; flex-wrap: nowrap; margin-top: var(--spacing-sm); padding: 0; }
  .chat-input-area .quick-questions { display: flex; flex: 1 1 auto; min-width: 0; flex-wrap: nowrap; gap: var(--spacing-xs); overflow-x: auto; padding-bottom: 2px; padding-right: var(--spacing-xs); margin-right: var(--spacing-xs); border-right: 1px solid var(--border-color); scrollbar-width: none; overscroll-behavior-x: contain; }
  .chat-input-area .quick-questions::-webkit-scrollbar { display: none; }
  .chat-input-area .quick-action { font-size: 0.75rem; padding: var(--spacing-xs) var(--spacing-sm); flex-shrink: 0; }
  .chat-message { max-width: 92%; }
  .chat-bubble { padding: var(--spacing-sm) var(--spacing-md); }
  .thinking-card, .tool-call-card { max-width: 95%; }
  /* 窄屏下编辑输入框取消 320px 最小宽度，避免撑破用户气泡 */
  .chat-edit-input { min-width: 0; }
}
.empty-state { display: flex; flex-direction: column; align-items: center; justify-content: center; text-align: center; padding: var(--spacing-xl); margin: auto; }
.empty-state-icon { color: var(--text-dim); margin-bottom: var(--spacing-md); }
.empty-state-title { font-family: var(--font-display); font-size: 1.25rem; color: var(--text-secondary); margin-bottom: var(--spacing-sm); }
.empty-state-desc { font-size: 0.9rem; color: var(--text-dim); max-width: 300px; }
.quick-actions { display: flex; flex-wrap: wrap; gap: var(--spacing-sm); margin-top: var(--spacing-md); padding: 0 var(--spacing-sm); }
/* Desktop: wrapper disappears so question buttons participate directly in .quick-actions layout */
.quick-questions { display: contents; }
.quick-action { flex: 0 1 auto; min-width: 0; padding: var(--spacing-xs) var(--spacing-md); background: var(--bg-panel); border: 1px solid var(--border-color); border-radius: var(--radius-lg); color: var(--text-secondary); font-size: 0.8rem; cursor: pointer; transition: all var(--transition-fast); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.quick-action:hover { border-color: var(--color-primary-dim); color: var(--color-primary); }
.quick-action.refresh:hover { border-color: var(--color-primary); }
.refresh-fixed { margin-left: auto; flex-shrink: 0; }
.quick-action.refresh {
  background: var(--bg-panel);
  color: var(--color-primary);
  padding: var(--spacing-xs);
  border-color: var(--color-primary-dim);
  display: flex;
  align-items: center;
  justify-content: center;
  min-width: 38px;
  min-height: 38px;
}
.quick-action.refresh:focus, .quick-action.refresh:active { outline: none; background: var(--bg-panel); }
.refresh-icon {
  transition: transform var(--transition-fast);
  width: 18px;
  height: 18px;
}
.quick-action.refresh:hover .refresh-icon {
  transform: rotate(180deg);
}
.refresh-icon.rotating {
  animation: rotate360 0.6s ease-out;
}
.chat-message { max-width: 85%; margin-bottom: var(--spacing-md); animation: fadeSlideIn 0.3s ease-out; }
.chat-message.user { margin-left: auto; }
.chat-message.assistant { margin-right: auto; }
.chat-bubble { padding: var(--spacing-md) var(--spacing-lg); border-radius: var(--radius-lg); position: relative; }
.chat-message.user .chat-bubble { background: linear-gradient(135deg, var(--color-primary) 0%, var(--color-primary-dim) 100%); color: var(--bg-deep); border-bottom-right-radius: var(--radius-sm); }
.chat-message.assistant .chat-bubble { background: var(--bg-panel); border: 1px solid var(--border-color); border-bottom-left-radius: var(--radius-sm); }
.chat-role { font-size: 0.75rem; font-weight: 600; text-transform: uppercase; letter-spacing: 0.1em; margin-bottom: var(--spacing-xs); opacity: 0.7; }
.chat-text { line-height: 1.6; white-space: pre-wrap; overflow-wrap: anywhere; }
/* 最终回答中的立绘图片画廊 */
.answer-image-gallery { display: flex; flex-wrap: wrap; gap: var(--spacing-sm); margin-top: var(--spacing-md); }
.answer-image-item { display: flex; flex-direction: column; align-items: center; gap: var(--spacing-xs); padding: 0; background: var(--bg-dark); border: 1px solid var(--border-color); border-radius: var(--radius-sm); cursor: zoom-in; overflow: hidden; transition: border-color var(--transition-fast), transform var(--transition-fast); }
.answer-image-item:hover { border-color: var(--color-primary-dim); transform: translateY(-2px); }
.answer-image-item img { display: block; max-height: 220px; max-width: 160px; object-fit: contain; }
.answer-image-label { width: 100%; max-width: 160px; font-size: 0.68rem; color: var(--text-dim); text-align: center; padding: 2px 4px 6px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
/* 图片放大灯箱 */
.image-lightbox { position: fixed; inset: 0; z-index: 2000; display: flex; align-items: center; justify-content: center; background: rgba(4, 6, 10, 0.88); backdrop-filter: blur(4px); animation: fadeSlideIn 0.15s ease-out; }
.image-lightbox-box { position: relative; display: flex; flex-direction: column; align-items: center; gap: var(--spacing-sm); max-width: 94vw; max-height: 94vh; }
.image-lightbox-img { max-width: 92vw; max-height: 84vh; object-fit: contain; border-radius: var(--radius-md); box-shadow: var(--shadow-lg, 0 12px 40px rgba(0, 0, 0, 0.5)); background: #000; }
.image-lightbox-label { font-size: 0.85rem; color: var(--text-secondary); text-align: center; }
.image-lightbox-close { position: absolute; top: -14px; right: -14px; width: 34px; height: 34px; border-radius: 50%; border: 1px solid var(--border-color); background: var(--bg-panel); color: var(--text-secondary); font-size: 18px; line-height: 1; cursor: pointer; z-index: 1; }
.image-lightbox-close:hover { color: #fff; border-color: var(--color-primary-dim); }
.chat-time { font-size: 0.7rem; opacity: 0.5; margin-top: var(--spacing-xs); text-align: right; }
/* Agent 过程折叠卡片：一次展示「工具调用 N 轮」，点击展开全部思考与工具详情 */
.process-card { max-width: 85%; margin-bottom: var(--spacing-md); margin-right: auto; background: var(--bg-card); border: 1px solid var(--border-color); border-radius: var(--radius-md); animation: fadeSlideIn 0.3s ease-out; }
.process-card-header { display: flex; align-items: center; gap: var(--spacing-sm); padding: var(--spacing-sm) var(--spacing-md); cursor: pointer; user-select: none; border-radius: var(--radius-md); transition: border-color var(--transition-fast), background var(--transition-fast); }
.process-card-header:hover { border-color: var(--color-primary-dim); background: var(--bg-panel); }
.process-card-icon { font-size: 0.85rem; }
.process-card-title { font-size: 0.8rem; font-weight: 600; color: var(--text-secondary); }
.process-card-time { margin-left: auto; font-size: 0.7rem; color: var(--text-dim); font-family: var(--font-mono); }
.process-card-toggle { font-size: 0.7rem; color: var(--text-dim); flex-shrink: 0; }
.process-card.is-expanded .process-card-header { border-bottom: 1px solid var(--border-color); border-bottom-left-radius: 0; border-bottom-right-radius: 0; }
.process-detail { margin-top: 0; margin-left: 14px; border-left: 3px solid var(--border-color); border-top-left-radius: 0; border-bottom-left-radius: 0; }

/* Thinking card (clickable whole card to expand/collapse) */
.thinking-card { background: var(--bg-card); border: 1px solid var(--border-color); border-radius: var(--radius-md); padding: var(--spacing-sm) var(--spacing-md); max-width: 85%; margin-bottom: var(--spacing-md); animation: fadeSlideIn 0.3s ease-out; margin-right: auto; cursor: pointer; transition: border-color var(--transition-fast); }
.thinking-card:hover { border-color: var(--color-primary-dim); }
.thinking-card-header { display: flex; align-items: center; gap: var(--spacing-sm); user-select: none; }
.thinking-card-round { font-size: 0.7rem; font-weight: 600; text-transform: uppercase; letter-spacing: 0.1em; color: var(--text-dim); }
.thinking-card-label { font-size: 0.7rem; font-weight: 600; text-transform: uppercase; letter-spacing: 0.1em; color: var(--text-dim); font-style: italic; }
.thinking-card-time { margin-left: auto; font-size: 0.65rem; color: var(--text-dim); font-family: var(--font-mono); }
.thinking-card-preview { font-size: 0.72rem; color: var(--text-dim); padding: var(--spacing-xs) 0 0 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 100%; cursor: text; user-select: text; }
.thinking-card-content { font-size: 0.75rem; color: var(--text-dim); background: var(--bg-dark); border: 1px dashed var(--border-color); border-radius: var(--radius-sm); padding: var(--spacing-sm); margin-top: var(--spacing-xs); max-height: 200px; overflow-y: auto; white-space: pre-wrap; line-height: 1.5; word-break: break-word; cursor: text; user-select: text; animation: fadeSlideIn 0.2s ease-out; }
/* 流式生成中的打字光标 */
.thinking-card.is-streaming .thinking-card-preview::after,
.thinking-card.is-streaming .thinking-card-content::after,
.current-answer.is-streaming::after { content: '▍'; color: var(--color-primary); animation: cursorBlink 1s steps(1) infinite; margin-left: 1px; }
@keyframes cursorBlink { 0%, 50% { opacity: 1; } 51%, 100% { opacity: 0; } }
.typing-indicator { display: flex; gap: 4px; padding: var(--spacing-md); }
.typing-indicator span { width: 8px; height: 8px; background: var(--text-secondary); border-radius: 50%; animation: typingBounce 1.4s infinite ease-in-out; }
.typing-indicator span:nth-child(1) { animation-delay: -0.32s; }
.typing-indicator span:nth-child(2) { animation-delay: -0.16s; }
.typing-indicator span:nth-child(3) { animation-delay: 0s; }
@keyframes typingBounce { 0%, 80%, 100% { transform: scale(0.6); opacity: 0.4; } 40% { transform: scale(1); opacity: 1; } }
.current-answer { white-space: pre-wrap; word-break: break-word; line-height: 1.6; }
@keyframes fadeSlideIn { from { opacity: 0; transform: translateY(10px); } to { opacity: 1; transform: translateY(0); } }
@keyframes rotate360 { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }
.pending-messages { display: flex; flex-direction: column; gap: var(--spacing-xs); padding: var(--spacing-sm) var(--spacing-md); background: var(--bg-panel); border: 1px dashed var(--border-color); border-radius: var(--radius-md); margin-bottom: var(--spacing-md); }
.pending-header { display: flex; align-items: center; gap: var(--spacing-sm); padding-bottom: var(--spacing-xs); border-bottom: 1px solid var(--border-color); margin-bottom: 2px; }
.pending-badge { font-size: 0.65rem; font-weight: 700; color: var(--color-primary); background: var(--bg-dark); border: 1px solid var(--color-primary-dim); border-radius: 8px; padding: 0 6px; line-height: 18px; }
.pending-label { font-size: 0.75rem; font-weight: 600; color: var(--text-dim); text-transform: uppercase; letter-spacing: 0.05em; flex: 1; }
.pending-clear { background: none; border: none; color: var(--text-dim); font-size: 0.7rem; cursor: pointer; padding: 2px 6px; border-radius: 4px; transition: color var(--transition-fast); }
.pending-clear:hover { color: var(--text-secondary); }
.pending-message { display: flex; align-items: center; gap: var(--spacing-sm); padding: var(--spacing-xs) var(--spacing-sm); border-radius: var(--radius-sm); background: var(--bg-dark); }
.pending-idx { font-size: 0.65rem; color: var(--text-dim); font-weight: 600; min-width: 16px; font-family: var(--font-mono); opacity: 0.6; }
.pending-text { font-size: 0.82rem; color: var(--text-secondary); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; flex: 1; }
.pending-action { background: none; border: none; color: var(--text-dim); cursor: pointer; padding: 4px; border-radius: 4px; display: flex; align-items: center; opacity: 0; transition: opacity var(--transition-fast), color var(--transition-fast); }
.pending-message:hover .pending-action { opacity: 1; }
.pending-action:hover { color: var(--text-secondary); }
.pending-delete:hover { color: #e74c3c; }
/* Tool call display */
.tool-call-card { background: var(--bg-card); border: 1px solid var(--border-color); border-radius: var(--radius-md); padding: var(--spacing-md); max-width: 85%; margin-bottom: var(--spacing-md); animation: fadeSlideIn 0.3s ease-out; margin-right: auto; }
.tool-call-header { display: flex; align-items: center; gap: var(--spacing-sm); margin-bottom: var(--spacing-sm); }
.tool-call-round { font-size: 0.7rem; font-weight: 600; text-transform: uppercase; letter-spacing: 0.1em; color: var(--text-dim); }
.tool-call-count { font-size: 0.7rem; font-weight: 600; text-transform: uppercase; letter-spacing: 0.1em; color: var(--text-dim); }
.tool-call-list { display: flex; flex-direction: column; gap: var(--spacing-xs); }
.tool-call-item { display: flex; flex-direction: column; gap: 2px; padding: var(--spacing-xs) var(--spacing-sm); background: var(--bg-panel); border-radius: var(--radius-sm); border-left: 3px solid var(--text-dim); transition: border-color var(--transition-fast); cursor: pointer; position: relative; }
.tool-call-item.has-result { border-left-color: var(--color-primary); }
.tool-call-item.is-interrupted { border-left-color: var(--status-medium); }
.tool-result-summary.is-interrupted-text { color: var(--status-medium); }
.tool-call-name-row { display: flex; justify-content: space-between; align-items: center; gap: var(--spacing-sm); }
.tool-call-name { font-size: 0.8rem; font-weight: 600; text-transform: uppercase; letter-spacing: 0.1em; color: var(--text-dim); display: flex; align-items: center; gap: var(--spacing-xs); flex-shrink: 0; }
.tool-icon { font-size: 0.85rem; }
.tool-call-meta { display: flex; align-items: center; gap: var(--spacing-sm); flex-wrap: wrap; justify-content: flex-end; }
.tool-call-args { font-size: 0.7rem; color: var(--text-dim); }
.tool-result-time { font-size: 0.65rem; color: var(--text-dim); font-family: var(--font-mono); flex-shrink: 0; }
.tool-result-summary { font-size: 0.7rem; color: var(--text-dim); padding-left: 22px; }
.tool-result-detail { padding: var(--spacing-sm) 0 0 22px; position: relative; cursor: text; user-select: text; animation: fadeSlideIn 0.2s ease-out; }
.tool-result-detail pre { background: var(--bg-dark); border: 1px solid var(--border-color); border-radius: var(--radius-sm); padding: var(--spacing-sm); font-size: 0.7rem; color: var(--text-secondary); overflow-x: auto; white-space: pre-wrap; word-break: break-all; max-height: 300px; overflow-y: auto; margin: 0; }
.tool-call-pending { font-size: 0.7rem; color: var(--text-dim); padding-left: 22px; display: flex; align-items: center; gap: 4px; }
.pending-dot { width: 6px; height: 6px; border-radius: 50%; background: var(--text-dim); animation: pendingPulse 1s infinite ease-in-out; }
@keyframes pendingPulse { 0%, 100% { opacity: 0.3; } 50% { opacity: 1; } }

/* Tool detail expanded content */
.tool-call-item.is-expanded { border-left-color: var(--color-primary); }
.tool-detail-summary { font-size: 0.75rem; color: var(--text-secondary); margin-bottom: var(--spacing-sm); padding-bottom: var(--spacing-xs); border-bottom: 1px dashed var(--border-color); }
.tool-detail-content { display: flex; flex-direction: column; gap: var(--spacing-sm); margin-top: var(--spacing-xs); }
.tool-detail-empty { font-size: 0.75rem; color: var(--text-dim); font-style: italic; }

/* RAG search docs */
.tool-detail-doc { background: var(--bg-dark); border: 1px solid var(--border-color); border-radius: var(--radius-sm); padding: var(--spacing-sm); }
.tool-detail-doc-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: var(--spacing-xs); }
.tool-detail-doc-source { font-size: 0.7rem; color: var(--text-secondary); font-weight: 500; max-width: 70%; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.tool-detail-doc-score { font-size: 0.65rem; color: var(--text-dim); font-family: var(--font-mono); background: var(--bg-panel); padding: 1px 6px; border-radius: 4px; }
.tool-detail-doc-content { font-size: 0.72rem; color: var(--text-secondary); line-height: 1.5; max-height: 160px; overflow-y: auto; white-space: pre-wrap; word-break: break-word; }

/* GraphRAG results */
.tool-detail-graph { display: flex; flex-direction: column; gap: var(--spacing-sm); }
.tool-detail-graph-path { font-size: 0.75rem; color: var(--text-secondary); padding: var(--spacing-xs) var(--spacing-sm); background: var(--bg-dark); border-radius: var(--radius-sm); }
.tool-detail-graph-edge { font-size: 0.72rem; color: var(--text-secondary); padding: var(--spacing-xs) var(--spacing-sm); background: var(--bg-dark); border-radius: var(--radius-sm); }
.graph-node { color: var(--text-primary); font-weight: 500; }
.graph-relation { color: var(--text-secondary); font-style: italic; margin: 0 4px; }
.graph-arrow { color: var(--text-secondary); margin: 0 2px; }
.graph-edge-desc { font-size: 0.65rem; color: var(--text-secondary); margin-top: 2px; padding-left: 8px; font-style: italic; }
.graph-direction-label { font-size: 0.7rem; color: var(--text-dim); font-weight: 500; margin-top: var(--spacing-xs); padding: 2px 0; }
.tool-detail-graph-entity { font-size: 0.75rem; color: var(--text-primary); font-weight: 500; margin-bottom: var(--spacing-xs); }

/* Web search results */
.tool-detail-web { background: var(--bg-dark); border: 1px solid var(--border-color); border-radius: var(--radius-sm); padding: var(--spacing-sm); }
.tool-detail-web-title { font-size: 0.78rem; margin-bottom: var(--spacing-xs); }
.tool-detail-web-title a { color: var(--text-secondary); text-decoration: none; font-weight: 500; }
.tool-detail-web-title a:hover { text-decoration: underline; }
.tool-detail-web-content { font-size: 0.72rem; color: var(--text-secondary); line-height: 1.5; max-height: 120px; overflow-y: auto; white-space: pre-wrap; word-break: break-word; }

/* Source citation links — in global (unscoped) style because v-html bypasses scoped CSS */

/* Structured query results */
.tool-detail-structured { display: flex; flex-direction: column; gap: var(--spacing-sm); }
.tool-detail-sql { font-size: 0.72rem; margin-bottom: var(--spacing-xs); }
.tool-detail-sql code { background: var(--bg-deep); color: var(--color-primary); padding: 2px 8px; border-radius: 4px; font-family: var(--font-mono); word-break: break-all; font-size: 0.7rem; }
.tool-detail-error { font-size: 0.75rem; color: var(--color-error, #ff6b6b); }
.tool-detail-table-wrapper { overflow-x: auto; max-height: 300px; overflow-y: auto; }
.tool-detail-table { width: 100%; border-collapse: collapse; font-size: 0.72rem; }
.tool-detail-table th { background: var(--bg-dark); color: var(--text-secondary); font-weight: 600; padding: 4px 8px; text-align: left; white-space: nowrap; position: sticky; top: 0; z-index: 1; }
.tool-detail-table td { padding: 3px 8px; border-bottom: 1px solid var(--border-color); color: var(--text-secondary); white-space: nowrap; }
.tool-detail-table tr:hover td { background: var(--bg-dark); }
.tool-detail-row-count { font-size: 0.7rem; color: var(--text-dim); margin-top: var(--spacing-xs); text-align: right; }

/* PRTS-MCP results */
.tool-detail-mcp { display: flex; flex-direction: column; gap: var(--spacing-sm); }
.tool-detail-mcp-text { font-size: 0.72rem; color: var(--text-secondary); line-height: 1.5; white-space: pre-wrap; word-break: break-word; }
.tool-detail-mcp-images { display: flex; flex-wrap: wrap; gap: var(--spacing-sm); }
.tool-detail-image-link { display: block; }
.tool-detail-image { max-height: 240px; max-width: 160px; border-radius: var(--radius-sm); border: 1px solid var(--border-color); object-fit: contain; background: var(--bg-deep); }

/* 用户消息行内编辑 */
.chat-edit-box { display: flex; flex-direction: column; gap: var(--spacing-sm); }
.chat-edit-input { width: 100%; min-width: 320px; resize: none; overflow-y: hidden; background: rgba(0, 0, 0, 0.15); border: 1px solid rgba(0, 0, 0, 0.25); border-radius: var(--radius-sm); padding: var(--spacing-sm); color: inherit; font-family: inherit; font-size: inherit; line-height: 1.6; outline: none; }
.chat-edit-input:focus { border-color: rgba(0, 0, 0, 0.4); }
.chat-edit-actions { display: flex; justify-content: flex-end; gap: var(--spacing-sm); }
.chat-edit-btn { padding: var(--spacing-xs) var(--spacing-md); border-radius: var(--radius-sm); font-size: 0.8rem; cursor: pointer; transition: all var(--transition-fast); border: 1px solid rgba(0, 0, 0, 0.3); }
.chat-edit-btn.cancel { background: transparent; color: inherit; }
.chat-edit-btn.cancel:hover { background: rgba(0, 0, 0, 0.12); }
.chat-edit-btn.save { background: rgba(0, 0, 0, 0.75); color: var(--color-primary); border-color: transparent; }
.chat-edit-btn.save:hover { opacity: 0.85; }

/* 版本分支切换器 */
.version-pager { display: flex; align-items: center; gap: 2px; font-family: var(--font-mono); font-size: 0.72rem; color: var(--text-dim); user-select: none; }
.version-btn { background: none; border: none; color: var(--text-dim); cursor: pointer; padding: 2px; border-radius: 4px; display: flex; align-items: center; transition: color var(--transition-fast), background var(--transition-fast); }
.version-btn:hover:not(:disabled) { color: var(--color-primary); background: var(--color-primary-glow); }
.version-btn:disabled { opacity: 0.35; cursor: default; }
.version-label { padding: 0 2px; }

/* Message footer actions (copy etc.) */
.chat-msg-footer { display: flex; align-items: center; justify-content: flex-end; gap: var(--spacing-xs); margin-top: var(--spacing-xs); }
.chat-msg-footer .chat-time { margin-top: 0; }
.msg-action-btn { background: none; border: none; color: var(--text-dim); cursor: pointer; padding: 3px; border-radius: 4px; display: flex; align-items: center; opacity: 0; transition: opacity var(--transition-fast), color var(--transition-fast); }
.chat-message:hover .msg-action-btn { opacity: 1; }
.msg-action-btn:hover { color: var(--color-primary); }
@media (hover: none) { .msg-action-btn { opacity: 0.6; } .pending-action { opacity: 0.7; } }
</style>

<style>
/* Source citation links — unscoped so v-html content gets styled */
.source-link {
  color: #58a6ff;
  text-decoration: underline;
  text-underline-offset: 3px;
  text-decoration-thickness: 1.5px;
  cursor: pointer;
  transition: all 150ms ease;
}
.source-link:hover {
  color: #79c0ff;
  text-decoration-color: #79c0ff;
}
.source-link-web {
  color: #58a6ff;
  text-decoration-style: dotted;
}
.source-tag-web {
  color: #8ba3a0;
  font-style: italic;
  font-size: 0.9em;
}

/* Markdown rendering in assistant messages (unscoped: v-html bypasses scoped CSS) */
.chat-message.assistant .chat-text.markdown-body,
.chat-bubble .current-answer.markdown-body {
  white-space: normal;
}
.markdown-body p { margin: 0 0 8px 0; }
.markdown-body p:last-child { margin-bottom: 0; }
.markdown-body ul, .markdown-body ol { margin: 4px 0 8px 0; padding-left: 22px; }
.markdown-body li { margin: 2px 0; }
.markdown-body > *:first-child { margin-top: 0; }
.markdown-body code { background: var(--bg-dark); border: 1px solid var(--border-color); padding: 1px 5px; border-radius: 4px; font-family: var(--font-mono); font-size: 0.85em; }
.markdown-body pre { background: var(--bg-dark); border: 1px solid var(--border-color); border-radius: var(--radius-md); padding: 10px 12px; overflow-x: auto; max-width: 100%; margin: 8px 0; }
.markdown-body pre code { background: none; border: none; padding: 0; font-size: 0.78rem; }
/* display:block 让表格自身可横向滚动，而不是撑破气泡 */
.markdown-body table { display: block; max-width: 100%; overflow-x: auto; border-collapse: collapse; margin: 8px 0; font-size: 0.85rem; }
.markdown-body img { max-width: 100%; height: auto; border-radius: var(--radius-sm); }
.markdown-body th, .markdown-body td { border: 1px solid var(--border-color); padding: 4px 10px; text-align: left; }
.markdown-body th { background: var(--bg-dark); }
.markdown-body blockquote { border-left: 3px solid var(--color-primary-dim); margin: 8px 0; padding: 2px 12px; color: var(--text-secondary); }
.markdown-body h1, .markdown-body h2, .markdown-body h3, .markdown-body h4 { font-size: 1rem; margin: 12px 0 6px 0; }
.markdown-body a { color: #58a6ff; }
.markdown-body hr { border: none; border-top: 1px solid var(--border-color); margin: 12px 0; }
</style>