# Arknights Agent

基于明日方舟数据集的 AI Agent 智能问答系统。Agent 通过 DeepSeek Function Calling 自主决定检索路径，内置 5 个本地工具，并通过 MCP Bridge 接入 7 个 PRTS MCP 工具，支持多工具并行调用、SSE 流式输出、JWT 认证与 SQLite 会话持久化。

> 对外品牌统一为 **Arknights Agent**；仓库/源码中的历史标识（如 `arknights_rag_search`、`RAG_ARKNIGHTS`）保持不变。

## 功能特性

- **AI Agent 自主决策**：LLM 通过 Function Calling 自主选择工具、并行执行、判断信息充足性，最多 15 轮工具调用；带循环检测、重复工具调用提醒、同一会话并发锁与提示词注入防护
- **多 LLM 模型支持**：通过 `llm_factory` 统一调度，当前接入 DeepSeek-V4-Flash，可扩展更多模型
- **知识库检索**：FAISS 向量 + BM25 关键词混合检索 → RRF 融合 → Cross-Encoder 重排 → Parent Document 扩展；`top_k` 默认 3
- **知识图谱查询（GraphRAG）**：NetworkX 有向图，支持单实体邻居查询和双实体最短路径查找
- **结构化数值查询**：只读 SQLite 查询干员/敌人数值，支持比较、排序、统计
- **出怪顺序查询**：`arknights_stage_waves` 解析 prts-mcp 同步的官方关卡 level JSON，按含 SPAWN 的 fragments 返回用户可见波次与刷怪顺序
- **网络搜索**：Tavily API + DuckDuckGo 兜底，补充外部实时信息
- **PRTS MCP 工具**：官方 mcp SDK stdio 子进程接入，7 个白名单工具，连接失败自动降级；立绘图片进入前端展示层，不发送给 LLM
- **快捷问题**：`/quick-questions` 每次返回 9 个不重复问题，覆盖关系、技能、故事、敌人、别名、结构化、立绘、出怪、材料掉落
- **用户认证**：注册、登录、JWT 令牌认证，会话持久化到 SQLite
- **SSE 流式输出**：实时显示 Agent 思考过程、工具执行状态、流式回答生成；支持会话续期通知
- **前端体验**：快捷问题横向滚动 + 左右箭头 + 固定刷新；思考/工具调用合并为过程卡片，流式时自动展开、回答后折叠；立绘以回答图片画廊展示并支持灯箱缩放
- **可观测性**：本地 traces 与 LangFuse 可选接入，提供列表、详情、导出与汇总端点

## 系统要求

- **Python**：3.11+
- **Node.js**：18+

## 快速开始

### 1. 克隆代码

```bash
git clone https://github.com/NiNiMu0326/RAG_ARKNIGHTS.git
cd RAG_ARKNIGHTS
```

### 2. 后端

```bash
cd backend
pip install -r requirements.txt
cp .env.example .env
# 编辑 .env 填入 API Key
```

必需环境变量：

- `SILICONFLOW_API_KEY` - 向量嵌入 + 重排 + 默认 LLM（[siliconflow.cn](https://siliconflow.cn)）
- `JWT_SECRET` - JWT 签名密钥，生成方式：`python -c "import secrets; print(secrets.token_hex(32))"`

可选：

- `DEEPSEEK_API_KEY_2` - DeepSeek 官方模型
- `TAVILY_API_KEY` - 网络搜索（不填则使用 DuckDuckGo 兜底）
- `PRTS_MCP_ENABLED` - 是否启用 PRTS MCP 工具（默认 true）
- `PRTS_MCP_COMMAND` - MCP 子进程命令（默认 `prts-mcp`）
- `PRTS_MCP_CONNECT_TIMEOUT` / `PRTS_MCP_CALL_TIMEOUT` - 连接/调用超时（秒）
- `PRTS_MCP_DATA_DIR` - prts-mcp 数据目录（可选；用于出怪顺序和快捷问题读取 stage_table/levels）
- `PORT` - 后端端口，默认 8100
- `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` / `LANGFUSE_HOST` - LangFuse 可观测性（可选）

### 3. 前端

```bash
cd frontend
npm install
```

### 4. 构建索引

```bash
python backend/data/chunker.py           # 文本切块
python backend/data/bm25_index.py        # BM25 索引
python backend/build_faiss_index.py      # FAISS 向量索引
```

### 5. 启动

```bash
# 后端（默认端口 8100）
cd backend && uvicorn main:app --host 0.0.0.0 --port 8100

# 前端开发（端口 5300，通过 Vite 代理转发 API 请求）
cd frontend && npm run dev
```

访问 <http://localhost:5300>

## Agent 架构

```
用户消息 → 构建消息上下文 → LLM Function Calling → 选出工具 → 并行执行 → 结果注入消息
                                                      ↓ 无工具调用
                                                流式输出最终回答
```

Agent 自主循环：每轮 LLM 返回工具调用时并行执行，结果加入消息历史继续下一轮，直到模型认为信息充足或达到 15 轮上限。同一会话的并发请求会通过每会话锁串行化，避免消息历史交错。

**本地工具（5 个）：**

| 工具 | 功能 | 内部流程 |
| --- | --- | --- |
| `arknights_rag_search` | 知识库检索 | FAISS + BM25 → RRF 融合 → Cross-Encoder 重排 → Parent Document 扩展；`top_k` 默认 3，上限 20 |
| `arknights_graphrag_search` | 知识图谱查询 | 单实体邻居 / 双实体最短路径（3 跳内） |
| `arknights_structured_query` | 结构化数值查询 | 只读 SQLite：干员/敌人数值比较、排序、统计 |
| `arknights_stage_waves` | 出怪顺序 | 读取 prts-mcp 同步的 `stage_table.json` + 关卡 level JSON，把每个含 SPAWN 的 fragment 作为一个用户可见波次 |
| `web_search` | 网络搜索 | Tavily + DuckDuckGo，会话内 URL 去重 |

`arknights_stage_waves` 输出 `stage_code / stage_id / stage_name / total_waves / waves[{wave, wave_pre_delay, spawns[{order, enemy_id, enemy_name, count, pre_delay, interval, route_index}]}]`，结果最多 60 波 / 400 个 spawn，超出时返回截断说明。

**PRTS MCP 白名单工具（7 个）：**

| 工具 | 说明 |
| --- | --- |
| `search_prts` | PRTS Wiki 综合搜索 |
| `get_stage_info` | 关卡详情 |
| `get_stage_enemies` | 关卡敌人列表与总览 |
| `get_enemy_info` | 敌人详情 |
| `list_items` | 物品列表 |
| `get_item_info` | 物品详情 |
| `operator_artwork` | 干员立绘；`variant` 默认 `preview`，`large/original` 失败时自动降级 `preview` 重试 |

MCP 不可用时 Agent 自动使用本地工具继续服务。MCP 结果拆分为 `llm_content` 与 `display` 两部分：图片以 base64 data URL 放入 `display.images` 供前端渲染，LLM 文本与展示文本分别截断，图片从不进入 LLM 上下文。

**安全机制：** 最大 15 轮硬限制、循环检测（最近 3 轮相同 tool_calls）、重复工具调用 3/5/8 次逐步提醒、提示词注入检测与清理、每会话并发锁。

## 项目结构

```
.
├── backend/
│   ├── main.py                  # FastAPI 主应用，所有路由定义
│   ├── config.py                # 全局配置（API Keys、模型参数、路径）
│   ├── db.py                    # SQLite 数据库（aiosqlite）
│   ├── auth.py                  # JWT 用户认证
│   ├── quick_questions.py       # 快速问题模板池（9 类能力导览）
│   ├── requirements.txt
│   ├── agent/                   # Agent 核心
│   │   ├── core.py              # Agent 主循环（SSE、并行 FC、循环检测、注入防护）
│   │   ├── tools.py             # 本地工具 Schema 定义 + ToolRegistry
│   │   ├── mcp_client.py        # 通用 MCP stdio 客户端 + 白名单注册 + 结果拆分
│   │   ├── tool_result.py       # 工具结果：LLM 上下文 / 前端展示分离
│   │   ├── tool_implementations.py  # 本地工具实现 + BM25/GraphBuilder 懒加载单例
│   │   ├── structured_query.py  # 结构化 SQL 查询实现
│   │   ├── stage_waves.py       # 出怪顺序解析（fragment → 波次）
│   │   ├── sessions.py          # 会话管理（TTL 3600s、LRU、每会话锁）
│   │   └── prompts.py           # 系统提示词 + 消息上下文构建
│   ├── api/                     # API 客户端封装
│   │   ├── deepseek.py          # OpenAI 兼容客户端（Chat + FC + 流式）
│   │   ├── llm_factory.py       # 多 Provider LLM 工厂
│   │   ├── siliconflow.py       # SiliconFlow API（嵌入 + 重排）
│   │   ├── web_search.py        # 网络搜索（Tavily + DuckDuckGo）
│   │   └── base.py              # 公共 HTTP 客户端工具（重试 + 连接池）
│   ├── rag/                     # RAG 底层基础设施
│   │   ├── retrievers.py        # 多通道检索（FAISS + BM25 + RRF），5h 缓存
│   │   ├── parent_document.py   # Parent Document 扩展（LRU 缓存）
│   │   ├── alias_map.py         # 干员别名映射
│   │   └── graphrag/            # 知识图谱
│   │       ├── builder.py       # 图谱构建（NetworkX DiGraph）
│   │       ├── extractor.py     # 实体关系提取
│   │       └── query.py         # 图谱查询（单例）
│   ├── lc/                      # LangChain 封装
│   │   ├── embeddings.py        # LangChain Embeddings 封装
│   │   └── reranker.py          # LangChain Reranker 封装
│   ├── observability/
│   │   └── tracing.py           # 本地 traces + LangFuse 追踪
│   ├── storage/
│   │   └── faiss_client.py      # FAISS 向量索引封装
│   └── data/
│       ├── chunker.py           # 文本切块脚本
│       ├── bm25_index.py        # BM25 索引构建脚本
│       └── sync_structured_db.py # 结构化查询 SQLite 同步
├── frontend/
│   └── src/
│       ├── views/
│       │   ├── ChatView.vue     # 问答界面（SSE 流式 + 过程卡片 + 画廊灯箱 + 快捷问题滚动）
│       │   ├── AdminView.vue    # 管理面板（Chunk 浏览器 + 数据仪表板 + traces）
│       │   └── GraphView.vue    # 知识图谱可视化（Cytoscape.js 交互）
│       ├── components/
│       │   ├── AppSidebar.vue   # 侧边栏（导航 + 会话管理 + 图谱控制）
│       │   ├── AppHeader.vue    # 顶部栏
│       │   ├── AuthModal.vue    # 登录/注册弹窗
│       │   ├── SettingsModal.vue # 设置弹窗（账户/主题/模型）
│       │   ├── SourceDrawer.vue  # 回答来源抽屉
│       │   ├── Toast.vue        # 通知提示
│       │   └── admin/           # 管理子组件（ChunkBrowser / DataDashboard / TracePanel）
│       ├── stores/              # Pinia 状态管理
│       │   ├── sessions.js      # 会话管理
│       │   ├── auth.js          # 认证状态
│       │   ├── settings.js      # 主题/模型设置
│       │   ├── quickQuestions.js # 快捷问题缓存
│       │   ├── sourceDrawer.js  # 来源抽屉状态
│       │   └── toast.js         # 通知状态
│       ├── composables/
│       │   └── useGraphController.js  # 图谱控制器（单例，跨组件共享）
│       ├── router/index.js      # 前端路由
│       ├── utils/
│       │   ├── toolMeta.js      # 工具展示名/图标/参数摘要/MCP 展示归一化
│       │   └── markdown.js      # Markdown 渲染
│       └── api.js               # API 客户端（含 Agent SSE 流式调用）
├── data/                        # 原始数据集（JSON/Markdown）
├── chunks/                      # 文本切块输出
├── faiss_index/                 # FAISS 向量索引持久化
├── Scripts/                     # 辅助脚本（爬虫、数据同步等）
```

## API 端点

完整路由以 `backend/main.py` 为准。以下为当前主要端点：

### 基础

| 方法 | 路径 | 描述 |
| --- | --- | --- |
| GET | `/api` | API 根信息 |
| GET | `/health` | 健康检查 |
| GET | `/status` | 配置状态（模型、API 可用性、MCP 状态） |
| GET | `/stats` | 数据统计（干员数、故事数、知识数、图谱边数） |
| GET | `/chunks/{collection}` | 指定集合的切块列表 |
| GET | `/chunks/{collection}/{filename}` | 单个切块详情 |
| GET | `/knowledge-graph` | 知识图谱完整数据（entities + relations） |

### Agent

| 方法 | 路径 | 描述 |
| --- | --- | --- |
| POST | `/agent/chat` | Agent SSE 流式对话（核心端点） |
| POST | `/agent/session` | 创建会话，返回 session_id |
| GET | `/agent/session/{id}/messages` | 获取会话消息历史 |
| DELETE | `/agent/session/{id}` | 删除会话（traces 保留） |
| GET | `/agent/models` | 可用 LLM 模型列表 |
| GET | `/agent/stats` | 会话统计 |
| GET | `/agent/debug/trace` | Agent 工具调用追踪（调试用） |
| GET | `/agent/traces` | 本地 traces 分页列表 |
| GET | `/agent/traces/summary` | traces 聚合统计 |
| GET | `/agent/traces/{trace_id}` | 单条 trace 详情 |
| GET | `/agent/traces/export` | 导出全部 traces |
| POST | `/agent/traces/export` | 导出选中 traces |
| DELETE | `/agent/traces` | 删除选中 traces |
| GET | `/agent/traces/{trace_id}/export` | 导出单条 trace |
| GET | `/agent/traces/langfuse` | LangFuse traces 代理 |
| GET | `/agent/traces/langfuse/{trace_id}` | LangFuse 单条 trace 代理 |

### 认证

| 方法 | 路径 | 描述 |
| --- | --- | --- |
| POST | `/auth/register` | 注册（username, account, password） |
| POST | `/auth/login` | 登录（account, password），返回 JWT token |
| GET | `/auth/me` | 当前用户信息 |
| POST | `/auth/change-password` | 修改密码（旧 token 失效） |

### 会话管理

| 方法 | 路径 | 描述 |
| --- | --- | --- |
| GET | `/conversations` | 用户会话列表 |
| GET | `/conversations/{id}/messages` | 会话消息 |
| POST | `/conversations/sync` | 同步本地会话到服务端 |
| DELETE | `/conversations/{id}` | 删除会话 |
| PUT | `/conversations/{id}/rename` | 重命名会话 |

### 数据

| 方法 | 路径 | 描述 |
| --- | --- | --- |
| GET | `/quick-questions` | 快捷问题列表（9 类，每类 1 条） |
| GET | `/operators` | 干员列表 |
| GET | `/characters` | 角色列表 |
| GET | `/stories` | 故事列表 |

## 技术栈

| 组件 | 技术 |
| --- | --- |
| 后端框架 | FastAPI + Uvicorn |
| Agent LLM | DeepSeek-V4-Flash（通过 llm_factory 统一调度） |
| 向量数据库 | FAISS |
| 嵌入模型 | Pro/BAAI/bge-m3（SiliconFlow） |
| 重排模型 | BAAI/bge-reranker-v2-m3（SiliconFlow） |
| 中文分词 | jieba（BM25 索引构建） |
| 网络搜索 | Tavily + DuckDuckGo |
| MCP | prts-mcp（官方 mcp SDK stdio 客户端） |
| 知识图谱 | NetworkX DiGraph |
| 数据库 | SQLite（aiosqlite） |
| 前端 | Vue.js 3 + Vite + Pinia |
| 图谱可视化 | Cytoscape.js |

## 缓存策略

| 缓存 | TTL | 说明 |
| --- | --- | --- |
| Agent 会话 | 3600s | 最大 1000 会话，LRU 驱逐 |
| 混合检索结果 | 5 小时 | FAISS + BM25 RRF 融合结果缓存 |
| Parent Document | 5 小时 | LRU 缓存，最大 100 条 |
| BM25 索引 | 懒加载 | 首次召回时构建，线程安全 |
| 知识图谱 | 懒加载 | 单例，线程安全 |
| 出怪顺序数据 | 懒加载 | stage_table / enemy_handbook / levels 路径缓存，加载失败下次重试 |

## SSE 事件类型

Agent 流式对话使用以下 SSE 事件，按时间顺序：

| 事件 | 描述 |
| --- | --- |
| `thinking_start` | Agent 开始新一轮思考（含 round、timestamp_ms） |
| `thinking_delta` | 思考增量内容 |
| `thinking_done` | 本轮思考结束（含完整 reasoning_content，前端用于替换 partial） |
| `tool_calls_start` | 模型决定调用工具（含 round、tool_calls 列表） |
| `tool_executing` | 单个工具开始执行（含 tool_call_id、tool_name） |
| `tool_call_result` | 工具执行完成（含 tool_call_id、tool_name、summary、time_ms、result） |
| `answer_delta` | 回答增量内容（流式） |
| `answer_done` | 回答完成（含 answer、metrics、sources） |
| `error` | 错误信息 |
| `session_renewed` | 会话过期后自动创建新会话（含新 session_id） |

前端会把同一轮/相邻的 thinking + tool_call 消息合并为“工具调用 · N 轮”过程卡片：生成中自动展开，`answer_done` 后整体折叠；内部思考与工具详情默认折叠。

## 数据来源

本项目使用的明日方舟领域数据包括：

| 数据集 | 内容 | 来源 |
| --- | --- | --- |
| 干员数据 | 属性、技能、天赋、档案等 | PRTS Wiki 爬取 |
| 敌人数据 | 敌人名称、属性、能力描述 | PRTS Wiki 爬取 |
| 剧情故事 | 活动剧情、干员档案文本 | 游戏内文本提取 |
| 游戏知识 | 玩法机制、干员外号、游戏梗、敌人外号等 | Agent WebSearch 互联网收集整理 |
| 实体关系 | 干员/组织/地点/事件之间的关系 | 从干员档案和剧情中手工提取 |
| 关卡出怪 | 关卡敌人、出怪波次、刷怪顺序 | prts-mcp 同步的官方 `stage_table.json` + `gamedata-levels` 关卡 JSON |

> 出怪顺序（`arknights_stage_waves`）按关卡 level JSON 的 `fragments` 划分：每个包含 `SPAWN` 动作的 fragment 作为一个用户可见波次，STORY 等无怪 fragment 不编号。结果截断上限为 60 波 / 400 spawns，并附带 truncation 说明。
>
> 注意：`data/`、`chunks/`、`faiss_index/` 目录不在 Git 仓库中。提交时请附带这三个目录的 zip 包，或按上方“构建索引”步骤重新生成。

## 测试

```bash
# 后端（从仓库根目录运行）
python -m pytest test -q

# 前端（从 frontend 目录运行）
npm test -- --run
```

当前分支已验证：后端 702 collected（695 passed / 7 skipped），前端 118 passed，`npm run build` 成功。

## RAG 评测

基于 [RAGAS](https://docs.ragas.io/) 对 `arknights_rag_search` 的检索质量做量化评估，评测集与评测历史均在版本控制内。

```bash
# 跑评测（默认 LLM 指标：context_precision / context_recall）
python backend/evaluation/rag_eval.py

# 同时生成回答，加测 faithfulness / answer_relevancy
python backend/evaluation/rag_eval.py --with-answer --tag "基线"

# 换检索参数做对照实验
python backend/evaluation/rag_eval.py --top-k 8 --tag "top_n=8"
python backend/evaluation/rag_eval.py --search-mode precise --tag "precise"
```

### 评测集

`backend/evaluation/test_cases.json` — **105 条**，覆盖 5 类问题：

| 类别 | 条数 | 说明 |
| --- | --- | --- |
| operator_info | 63 | 干员星级/职业/面板/技能/天赋 |
| relationship | 21 | 干员与组织、角色之间的关系 |
| enemy_info | 16 | 敌人级别/攻击类型/属性/背景 |
| story_character | 4 | 剧情事件与角色经历 |
| basic_stats | 1 | 全局统计口径 |

难度分布：easy 42 / medium 56 / hard 7。

**用例的 ground_truth 全部由数据源派生**，可用 `gen_test_cases.py` 重新生成：

```bash
# 从 data/ 与知识图谱重新生成候选（答案自动派生，不手工编写）
python backend/evaluation/gen_test_cases.py --out /tmp/candidates.json

# 逐条走真实混合检索链路，验证答案确实可被召回（不可召回的用例对评测无意义）
python backend/evaluation/verify_test_cases.py --candidates /tmp/candidates.json --concurrency 2 --out /tmp/verify.json

# 合并验证结果并写入 test_cases.json
python backend/evaluation/merge_test_cases.py --verify /tmp/verify.json --recheck "..."
```

> 注意：`verify_test_cases.py` 并发调 SiliconFlow 重排接口会触发 429 限流，
> 导致用例被误判为「不可召回」（表现为 0 命中）。脚本已内置退避重试，
> 若仍出现整条 0 命中，请把并发降到 1~2 复检，不要直接判定为检索失败。

### 评测历史

每轮评测结果写入 `backend/evaluation/results/`（CSV + JSON），并在 `eval_history.jsonl` 追加一行汇总，便于对比不同参数配置的效果。

**当前基线（105 条 / balanced / top_k=5）**：

| 指标 | 得分 |
| --- | --- |
| context_precision | 0.846 |
| context_recall | 0.937 |
| faithfulness | 0.985 |
| answer_relevancy | 0.847 |

分类别看，`operator_info` 最强（faithfulness 0.995），`enemy_info` 的 answer_relevancy 偏低（0.741），`story_character` 的 context_recall 只有 0.750 —— 剧情类问题的检索召回是最明确的短板。

> **踩坑记录：回答生成的两个静默失败模式**
>
> 1. **`max_tokens` 是「思考 + 可见回答」的总预算。** `deepseek-v4-flash` 是思考模型，
>    复杂问题（剧情、跨文档关系）的 `reasoning_content` 可能吃掉全部预算，导致
>    `finish_reason=length` 且 `content` 为空字符串。原先设为 1024 时，
>    这类问题**稳定**生成失败（重试也没用），现改为 8192 并显式检测 `finish_reason`。
> 2. **生成失败时回退成 `ground_truth` 作答会让 faithfulness / answer_relevancy 虚高**
>    （回答与参考答案完全一致）。`build_dataset` 现在会打印警告并列出受影响的用例。
>
> 针对已跑完的批次，可用 `fix_eval_cases.py` 只重算问题用例，无需整轮重跑：
>
> ```bash
> python backend/evaluation/fix_eval_cases.py \
>     --result backend/evaluation/results/rag_eval_xxx.csv \
>     --out    backend/evaluation/results/rag_eval_xxx_fixed.csv
> ```

## 复现示例

以下问题可用于验证系统功能：

1. **干员属性查询**：“银灰的攻击力是多少？” → RAG precise 模式
2. **角色关系推理**：“特蕾西娅和阿米娅是什么关系？” → 知识图谱路径查询
3. **剧情内容检索**：“乌萨斯的孩子们讲了什么故事？” → RAG semantic 模式
4. **并行工具调用**：“阿米娅是什么种族，她和博士什么关系？” → RAG + GraphRAG 并行
5. **网络搜索兜底**：“明日方舟最新联动活动是什么？” → WebSearch 补充
6. **结构化查询**：“哪些六星近卫的精二满级攻击力大于800？” → 结构化 SQL
7. **出怪顺序**：“1-7关卡的出怪顺序是什么？” → `arknights_stage_waves`

## 部署

- **GitHub Actions**：push 到 `master` 自动触发 CI/CD（`test` → `deploy`）；`test` 失败则不会部署，`workflow_dispatch` 可手动触发。
- **feature 分支**：不会自动部署，由人工手动部署到生产环境。
- 生产环境：`https://ninimu.top:14606`（TLS），后端 uvicorn 端口 8889，由 Nginx 代理；服务由 systemd 管理（`arknights-rag`）。
- 手动部署 feature 分支的通用步骤（不含任何密钥）：

```bash
ssh root@<SERVER_HOST> -p <SERVER_PORT>
cd /srv/projects/arknights-rag
git fetch origin feature/prts-mcp
git reset --hard origin/feature/prts-mcp
export PATH="$HOME/.local/bin:$PATH"
uv pip install --python .venv/bin/python -r backend/requirements.txt
cd frontend
npm install
npm run build
mkdir -p /var/www/arknights
rsync -a --delete /srv/projects/arknights-rag/frontend/dist/ /var/www/arknights/
cd ..
systemctl restart arknights-rag
curl -fsS http://localhost:8889/health
```

- 若服务器直连 GitHub 不稳定，可在服务器全局 gitconfig 配置 gh-proxy 镜像 `url."https://gh-proxy.com/https://github.com/".insteadOf "https://github.com/"`；排查时注意 `git pull` 报错若包含 `gh-proxy.com` 域名，通常是镜像服务问题而非仓库或部署流程问题。

MIT License
