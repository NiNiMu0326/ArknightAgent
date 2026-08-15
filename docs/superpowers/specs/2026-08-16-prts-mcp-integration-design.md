# PRTS-MCP 接入与 Agent 工具面扩展设计

- 日期：2026-08-16
- 状态：设计已获用户确认，待实现计划
- 目标项目：ARKNIGHTS Agent（Agentic RAG 问答系统）

## 1. 背景与目标

项目当前 Agent 已有 4 个工具：

1. `arknights_rag_search` —— 知识库混合检索（FAISS + BM25 → RRF → Cross-Encoder → Parent Document）
2. `arknights_graphrag_search` —— 实体关系图谱查询
3. `web_search` —— 网络搜索（Tavily + DuckDuckGo）
4. `arknights_structured_query` —— SQLite 干员/敌人基础数值查询

缺口是**关卡、出怪、敌人关卡级数值、物品材料、立绘**等结构化游戏数据。本次接入 [prts-mcp 2.7.0](https://github.com/3aKHP/prts-mcp)（Python 实现），通过通用 MCP Bridge 把外部 MCP 工具动态注册进现有 `ToolRegistry`，并同步调整前端展示与快速问题模板。

目标：

- Agent 具备“关卡/出怪/材料/立绘”四类新能力。
- 实现可复用的通用 MCP 客户端能力（工具发现 → 白名单 → Schema 转换 → 动态注册）。
- MCP 不可用时优雅降级，不影响现有功能与 CI。
- 快速问题从 5 个固定模板改为 4 个能力分类的随机模板池，作为 Agent 工具路由的能力导览。

## 2. 非目标（第一批不做）

- 不注册干员档案、语音、剧情类 MCP 工具（与本地 RAG 重复）。
- 不做立绘本地持久化/后端图片代理（使用 MCP 自带 256MB LRU 内存缓存）。
- 不做 MCP 运行时自动重连（挂了下一次进程重启恢复）。
- 不引入 Skill 机制、Memory、MultiAgent。
- 不把图片 base64 注入 LLM 上下文。

## 3. 决策记录

| 决策点 | 结论 |
|---|---|
| 接入范围 | 7 个 MCP 工具：`search_prts`、`get_stage_info`、`get_stage_enemies`、`get_enemy_info`、`list_items`、`get_item_info`、`operator_artwork` |
| 接入方式 | 通用 MCP Bridge：动态发现 → 白名单过滤 → OpenAI Function Schema 转换 → 注册 `ToolRegistry` |
| 传输方式 | Python stdio 子进程（`prts-mcp` console script） |
| 可用性 | 可选依赖：连接失败 Agent 照常启动，只注册本地 4 个工具；`/status` 暴露 MCP 状态 |
| 服务器运行环境 | 生产服务器当前为 Python 3.8.10，`prts-mcp`/`mcp` 需 ≥3.10。采用 uv + Python 3.11 项目专属 `.venv`，systemd 改用 `.venv/bin/python` 启动，CI 与生产统一为 3.11 |
| 立绘数据源 | `LOCAL_IMAGE=false`（PRTS MediaWiki 按需下载）+ `PRTS_IMAGE_CACHE=true`（MCP 内置 256MB LRU） |
| 图片展示 | MCP 返回的 base64 `ImageContent` 只进前端 SSE，不进 LLM；默认强制 `variant=preview` |
| 输出通道 | `PRTS_OUTPUT_CHANNEL=both`（文本 + `structuredContent`） |
| 快速问题 | 4 个分类模板池：RAG / GraphRAG / 结构化查询 / PRTS-MCP，每类随机抽 1 个，共 4 个按钮 |

## 4. 现状要点（实现时必须考虑）

- `backend/agent/tools.py`：`TOOL_SCHEMAS` 是模块级静态列表，`ToolRegistry.get_schemas()` 直接返回该列表。
- `backend/agent/sessions.py` 的 `add_tool_result()` 会把整个 result dict `json.dumps` 后写入 LLM 的 tool 消息；因此图片 base64 绝不能出现在返回给 `registry.execute()` 的同一对象中进入会话。
- `backend/agent/core.py` 的 `execute_tool()` 对结果统一做 `_sanitize_unicode()` 后返回，SSE `tool_call_result` 直接携带该 result；前端 `ChatView.vue` 对每个工具名硬编码了不同的结果渲染分支，未知工具落入 `<pre>`。
- `backend/main.py` 的 `/quick-questions` 当前固定生成 5 类问题（关系/技能/故事/敌人/别名），带 5 分钟缓存与前一批标签去重。
- `frontend/src/stores/quickQuestions.js` 只是缓存数组，渲染在 `ChatView.vue` 的 `.quick-actions` 区域。
- CI 为干净 checkout，无 `data/`、无真实 API Key；新增 MCP 必须在 CI 中可关闭。

## 5. 架构与数据流

```text
FastAPI lifespan
    └─> McpClientManager.start()
          ├─ spawn prts-mcp (stdio, 10s 超时)
          ├─ list_tools()
          ├─ MCP_ALLOWLIST 过滤（7 个）
          └─ MCP JSON Schema -> OpenAI function schema
                 └─> ToolRegistry.register_mcp_tools()

Agent 循环 tool_call:
    ToolRegistry.execute(mcp_tool_name, args)
       └─> McpClientManager.call_tool(name, args)
             └─> 拆分为 ToolResultPayload:
                   llm_content   -> session.add_tool_result()（无图片，JSON 上限 ~12KB）
                   display       -> SSE tool_call_result（结构化上限 ~50KB）
                   images[]      -> SSE 展示数据（base64 data URL，上限 1 张、preview 变体）
```

## 6. 组件设计

### 6.1 配置（`backend/config.py`）

新增：

- `PRTS_MCP_ENABLED`（默认 `true`）：总开关，CI 设为 `false`。
- `PRTS_MCP_COMMAND`（默认 `prts-mcp`）：子进程命令。
- `PRTS_MCP_CONNECT_TIMEOUT`（默认 `10` 秒）。
- **不设置 `GAMEDATA_PATH`**：prts-mcp 检测到该环境变量会禁用 auto-sync；保持默认用户数据目录（服务器为 `/root/.local/share/prts-mcp/gamedata`），让其后台自动同步。
- 固定传入子进程的 env：`PRTS_OUTPUT_CHANNEL=both`、`LOCAL_IMAGE=false`、`PRTS_IMAGE_CACHE=true`、`IMAGES_ENABLED=true`。

### 6.2 `backend/agent/mcp_client.py`（新增）

- `MCP_ALLOWLIST: set[str]`，内容为第 3 节的 7 个工具名。
- `McpClientManager`：
  - `start()` / `close()`；`connected`、`available_tools` 状态属性。
  - 使用 `mcp.client.stdio.stdio_client` 维持长连接；所有 `call_tool()` 经 `asyncio.Semaphore(1)` 串行化。
  - `list_tools()` 返回原始 MCP `Tool` 列表。
  - `call_tool(name, arguments)` 返回标准化结果。
- `convert_mcp_tool_to_openai_schema(tool) -> dict`（纯函数）：
  - 输入 MCP `Tool`；输出 `{"type":"function","function":{"name","description","parameters"}}`。
  - 对 `operator_artwork`：description 追加“图片只展示不用于文字推理”；执行层默认 `variant="preview"`。
- `extract_mcp_result(call_result) -> McpToolResult`（纯函数）：
  - 收集 `TextContent` 文本与 `structuredContent`。
  - 收集 `ImageContent` → `{mime, data_b64, label}`；最多 1 张；超过 `MAX_IMAGE_B64_CHARS`（约 400KB base64）时丢弃并附加提示文本。
  - `llm_content`：文本 + 截断后的 structured JSON（上限 `LLM_RESULT_MAX_CHARS=12000`），**不含图片**。
  - `display`：完整结构化数据（上限 `DISPLAY_RESULT_MAX_CHARS=50000`）+ `images`。

### 6.3 `backend/agent/tool_result.py`（新增）

- `ToolResultPayload` 数据类：`llm_content`、`display`。
- `execute_tool()` 若得到该包装则：
  - `session.add_tool_result(tc.id, payload.llm_content)`
  - SSE 与 trace summary 使用 `payload.display`
- 现有工具仍返回原始 list/dict，行为不变。

### 6.4 `backend/agent/tools.py` 改造

- `ToolRegistry` 增加 `register_schema(name, schema)` 与动态 schema 存储；`get_schemas()` 返回静态 + 动态 schema。
- `get_tool_registry()` 保持惰性单例；MCP 注册由 `main.py` lifespan 显式调用，避免 import 副作用。

### 6.5 `backend/main.py` 改造

- lifespan 中：若 `PRTS_MCP_ENABLED`，启动 `McpClientManager`、`register_mcp_tools()`；失败记 warning 并继续。
- shutdown 时关闭 manager。
- `/status` 增加 `mcp: {enabled, connected, tool_count, error}`。
- 保持现有 4 个工具与全部端点行为不变。

### 6.6 提示词（`backend/agent/prompts.py`）

新增工具路由规则：

- 关卡详情/关卡出怪/关卡内敌人数值 → `get_stage_info` / `get_stage_enemies` / `get_enemy_info`
- 材料用途、掉落、获取途径 → `list_items` / `get_item_info`
- 名称或词条不确定时先用 `search_prts` 解析
- 干员立绘/时装 → `operator_artwork`
- 关系查询仍走 GraphRAG；数值比较/排序仍走 `arknights_structured_query`；剧情设定走 RAG

### 6.7 前端（`ChatView.vue` / `api.js`）

- `api.js` 的 SSE `tool_call_result` 处理不变（result 已带展示数据）。
- `ChatView.vue`：
  - 新增 MCP 工具名集合与中文名/图标映射（`getToolDisplayName` / `getToolIcon`）。
  - 新增通用 `prts_mcp` 工具卡片渲染分支：
    - `display.structured`：优先表格；无规则结构时键值对/`<pre>` 兜底。
    - `display.images`：`data:image/...;base64,...` 缩略图，点击新窗口查看大图。
  - 非 MCP 工具渲染分支不变。
- 不修改 markdown 正文渲染；回答正文若未来引用图片 URL，现有 `markdown-body img` 样式已支持。

### 6.8 快速问题（`backend/main.py` + 前端）

- 后端定义 4 个模板分类，每类 3–6 个模板：

| 分类 | 模板示例 | 预期工具路由 |
|---|---|---|
| `rag` | 现有技能/故事/别名/敌人介绍模板 | `arknights_rag_search` |
| `graph` | 现有干员关系模板 | `arknights_graphrag_search` |
| `structured` | “精二满级攻击力 > 800 的六星近卫有哪些”“领袖级敌人按血量排序” | `arknights_structured_query` |
| `prts_mcp` | “某关卡出怪顺序”“某材料在哪刷”“某干员有哪些立绘” | prts-mcp 工具 |

- 每次生成：每类随机抽 1 个模板，标签与上一批去重；返回 4 个问题。
- 数据缺失时保留现有静态 fallback；缓存 5 分钟与 `refresh=true` 行为不变。
- 前端只消费数组，除按钮数量外无需改动 store；样式保持横向滚动。

## 7. 错误处理

| 场景 | 行为 |
|---|---|
| 启动连接超时/失败 | 不注册 MCP 工具，Agent 用 4 个本地工具继续服务；`/status` 标注 unavailable |
| 运行中 MCP 调用异常 | 返回 `{"error": "...", "hint": "可改用 RAG/web_search"}` 给 LLM，Agent 可自行改道 |
| prts-mcp 数据首次同步未完成 | 其自身返回“数据同步中/未找到”文本，按普通工具结果交给 LLM，可稍后重试 |
| 图片过大 | 丢弃 base64，display 保留元数据与“图片过大已省略”提示 |
| CI/无网络环境 | `PRTS_MCP_ENABLED=false`，无子进程、无 schema 变化 |

## 8. 测试

- 纯函数单测：
  - `convert_mcp_tool_to_openai_schema`（含 `operator_artwork` variant 默认处理）
  - `extract_mcp_result`（文本/结构化/图片分离、截断、超大图片丢弃）
  - 白名单过滤
  - 快速问题模板池抽取与去重
- 单元测试 `ToolRegistry` 动态注册 schema。
- `ToolResultPayload` 分流测试：`execute_tool` 路径保证 llm_content 不进图片。
- 集成冒烟（本地手动，不进 CI）：启用 MCP 后 `/status` connected；提问关卡/材料/立绘各一次，确认 SSE 与前端卡片。
- CI：`PRTS_MCP_ENABLED=false`，现有 578 个测试全部通过，且 MCP 相关纯函数测试正常执行。

## 9. 依赖与部署

- `backend/requirements.txt` 增加 `prts-mcp==2.7.0`（其依赖含 `mcp==2.0.0`）。
- 生产服务器 Python 3.8 不满足要求：一次性用 uv 安装 Python 3.11 并创建 `/srv/projects/arknights-rag/.venv`，重装 `backend/requirements.txt`；`/etc/systemd/system/arknights-rag.service` 的 `ExecStart` 改为 `.venv/bin/python -m uvicorn ...`；`.venv/` 加入 `.gitignore`。
- CI 的 deploy job 在 `git reset --hard` 后增加 `uv pip install -r backend/requirements.txt`，保证新增 Python 依赖在服务器生效。
- 服务器首次启动会由 prts-mcp 自动同步敌方/关卡/物品数据到 root 用户默认数据目录（`/root/.local/share/prts-mcp/`），后续增量更新；部署后做一次 smoke test。
- 前端构建流程不变。

## 10. 验收标准

1. 本地开启 MCP 后，`/status` 显示 7 个 MCP 工具已注册。
2. 提问“1-7 关卡出怪”类问题，Agent 能自主调用 `get_stage_enemies`/`get_enemy_info` 并给出基于结构化数据的回答；前端工具卡片展示表格结果。
3. 提问“阿米娅立绘”，Agent 调用 `operator_artwork`，前端卡片显示缩略图，且 LLM 上下文无 base64。
4. 关闭/断开 MCP 后，Agent 仍可回答原有知识库问题。
5. CI（MCP 关闭）全绿。
6. `/quick-questions` 返回 4 个问题，分别覆盖 4 个能力分类，连续两次刷新模板/标签不同。
7. 现有工具与全部 API 行为不回归。

## 11. 风险与备注

- `prts-mcp` 固定依赖 `mcp==2.0.0`，与项目其他依赖冲突时需优先调整项目侧版本策略。
- 首次数据同步耗时取决于服务器带宽；不阻塞启动（同步由子进程后台线程执行）。
- MCP 工具返回结构随 prts-mcp 版本可能变化；`extract_mcp_result` 需对缺字段保持容错。

## 12. 后续可扩展（本次不做）

- MCP 运行时自动重连与工具热加载。
- 基于工具面变宽引入 Skill/渐进式披露机制。
- 若将来做“根据玩家干员池配队”，再引入用户 box 持久化与 Memory。
