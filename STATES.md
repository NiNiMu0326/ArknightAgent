# STATES.md — OCR 评审修复状态账本

> 由 Planner（主 Agent）维护，记录 OCR 全量扫描 **336 条**意见的处置状态。
> 仓库：`D:\Agent\ARKNIGHTSAgent` ｜ 基线 commit：`dc24da4` ｜ 最近更新：2026-09-19
> 扫描报告：`output/ocr-review-report.md` ｜ 原始意见：`output/ocr-scan-result.json` / `.csv`
> **改动尚未提交**（按用户约定：提交前需用户确认）。当前工作区：42 个文件变更（含 5 个测试文件同步）。

## 状态词表

| 状态 | 含义 |
|------|------|
| 待修复 | 已立项，尚未开工 |
| 修复中 | 修复子 Agent 正在处理 |
| 已修复 | 修复完成，等待 Review 子 Agent 复核 |
| 已复核 | 独立 Review 通过（含返工后通过），闭环 |
| 复核驳回 | Review 发现问题，已打回（本账本用「已复核」记录返工后的最终态） |
| 误诊 | 经核实不是 bug（附实证证据） |
| 不修 | 确认为问题但明确决定不改（附理由） |
| 未修复 | 本轮未纳入任何修复组，留待后续 |

## 全局进度（终版）

| 批次 | 任务数 | 已复核 | 未修复 | 误诊 | 不修 |
|------|--------|--------|--------|------|------|
| P0 | 6 | 6 | 0 | 0 | 0 |
| P1 | 15 | 15 | 0 | 0 | 0 |
| P2 | 16 | 16 | 0 | 0 | 0 |
| **合计** | **37** | **37** | **0** | 0 | 0 |

> OCR 原始意见 **336** 条 → 立项修复 **37** 条（P0 6 / P1 15 / P2 16）→ **已全部闭环 37 条**，其余 **299** 条为未立项意见。

## 验证基线（每一轮结束都实测）

| 阶段 | 后端 `pytest test/ -q -p no:deepeval` | 前端 `cd frontend; npm test` |
|------|------------------------------------------|--------------------------------|
| 修复前基线 | 695 passed / 7 skipped | 118 passed |
| P0+P1 修复后 | 686 passed / **9 failed**（全部为「测试写死旧行为」） | 118 passed |
| 测试同步 + 返工后 | **700 passed / 7 skipped / 0 failed** | 118 passed |
| P2 完成后（终版） | **700 passed / 7 skipped / 0 failed** | **118 passed** |

> 通过数 695→700 是因为测试同步时新增了 5 条守护用例（匿名 401、JWT fail-closed、越权 403、graphrag 失败返回 None、关键字字面量放行）。
> 测试命令必须带 `-p no:deepeval`：本机 deepeval 以 editable 方式安装在已不存在的路径 `D:\Agent\X\deepeval`，不加会在 pytest 启动阶段崩溃。

## 执行过程（四轮）

| 轮次 | 内容 | 参与 Agent | 结果 |
|------|------|-----------|------|
| 规划 | OCR 336 条 → 立项 37 条，按文件不冲突划 8 组 | Planner | `output/fix-briefs/group-*.md` |
| 第 1 轮 | P0+P1 共 21 条并行修复 | 8 个修复 Agent | 报告 + 自测证据 |
| 第 2 轮 | 测试同步（9 条）+ 四路独立审查 | 1 测试 Agent + 4 审查 Agent | 打回 5 项（T01/T07/T04/T03/T15） |
| 第 3 轮 | 5 项打回返工 | 5 个修复 Agent | 全部修复并自证 |
| 第 4 轮 | 剩余 P2（T22–T37 除已做项）+ 复核追加 4 小项 | 5 个修复 Agent | 报告 + 自测证据 |

## 任务清单

| 任务 | 批次 | 组 | 状态 | 标题 / 最终结论 |
|------|------|----|------|------------------|
| T01 | P0 | GA | **已复核** | agent 端点鉴权 + 会话/trace 归属（IDOR） |
| T02 | P0 | GB | **已复核** | 属性注入型 XSS（转义发生在消毒之后） |
| T03 | P0 | GC | **已复核** | CI 健康检查吞失败 / action 未钉 SHA / 依赖版本冲突 |
| T04 | P0 | GD | **已复核** | SQL 校验：分号、行注释、表名白名单绕过 |
| T05 | P0 | GA | **已复核** | parent_document 路径穿越 |
| T06 | P0 | GA | **已复核** | decode_jwt 未强制比对 pw_changed_at |
| T07 | P1 | GE | **已复核** | restore_session 竞态 + 绕过会话说上限 |
| T08 | P1 | GE | **已复核** | stage_waves 结构异常仍置 loaded 标志 |
| T09 | P1 | GE | **已复核** | GraphRAG 构建失败仍返回空图 builder |
| T10 | P1 | GE | **已复核** | 图谱构建缺脏数据校验 + 无向视图复用 |
| T11 | P1 | GE | **已复核** | alias_map 错误映射 |
| T12 | P1 | GE | **已复核** | tracing 未用真实 latency_ms |
| T13 | P1 | GF | **已复核** | retrievers 向量命中被静默丢弃 |
| T14 | P1 | GF | **已复核** | FAISS 索引与元数据一致性校验 |
| T15 | P1 | GG | **已复核** | 删除会话被旧去抖 payload 复活 + N+1 串行请求 |
| T16 | P1 | GG | **已复核** | TracePanel 列表无序号守卫 + 静默失败清空 |
| T17 | P1 | GG | **已复核** | graphrag.css 显隐冲突 + 下拉裁剪（裁剪判定不修） |
| T18 | P1 | GH | **已复核** | lore_sync --full 先删本地再拉远程 |
| T19 | P1 | GH | **已复核** | daily_sync entity_relations 整体覆盖 |
| T20 | P1 | GH | **已复核** | scraper 括号不闭合致爬取中断 |
| T21 | P1 | GC | **已复核** | crawl_operator_images 吞异常 + 用长度判成功 |
| T22 | P2 | GE | **已复核** | tracing id 复用 / flush 阻塞 / 持久化丢堆栈 |
| T23 | P2 | GE | **已复核** | async 路径同步阻塞 I/O |
| T24 | P2 | GC | **已复核** | SQL 关键字黑名单误伤字面量 + 连接生命周期 |
| T25 | P2 | GF | **已复核** | 缓存身份不完整 + 前缀键不可靠 |
| T26 | P2 | GF | **已复核** | faiss 增量追加非原子 + 损坏当空集合 |
| T27 | P2 | GA | **已复核** | get_tool_registry 惰性初始化竞态 |
| T28 | P2 | GA | **已复核** | deepseek SSE <think/> 泄漏 + kwargs 覆盖 |
| T29 | P2 | GB | **已复核** | ChunkBrowser 深链/切换/过滤导航 |
| T30 | P2 | GB | **已复核** | ChatView 渲染性能（计时/归一化/流式） |
| T31 | P2 | GB | **已复核** | AppSidebar 声明顺序 + 健康检查超时 |
| T32 | P2 | GF | **已复核** | parent_document 缓存竞态与负缓存 |
| T33 | P2 | GC | **已复核** | sync_conversations 无上限（N+1 + DoS） |
| T34 | P2 | GC | **已复核** | api.js 缺 ok 校验 / 编码 / 流释放 |
| T35 | P2 | GC | **已复核** | quick_questions 批内重复 + toolMeta 健壮性 |
| T36 | P2 | GC | **已复核** | MediaWiki 分页截断 + 数值强转 + 重复项 |
| T37 | P2 | GH | **已复核** | jobtracker 日期解析静默清空 + os 导入 |

## 逐条结论与证据摘要

### T37 ｜ P2 ｜ 已复核 ｜ jobtracker 日期解析静默清空 + os 导入

- 涉及文件：jobtracker/tracker.py、backend/auth.py、backend/lc/reranker.py
- 结论：parse_date 失败即报错（不再静默清空日期）；import os 上移；另修 reranker api_key repr 泄露（实测 pydantic v2 Field(repr=False) 在 v1 模型上失效，改显式 __repr__）与 relevance_score 为 None 时 TypeError

## 未修复项（0 条）

原本被 Planner 排除在修复组之外的两条 P2 已在收尾轮补齐并验证，账本归零：

| 任务 | 位置 | 问题 | 最终结论 |
|------|------|------|----------|
| T27 | `backend/agent/tools.py:130-208` | `get_tool_registry()` check-then-act 惰性初始化，并发首次调用可能拿到「非 None 但未注册工具」的实例 | **已修复**：`threading.Lock` 双重检查 + 先局部注册、最后原子发布；`register_schema` 拒绝与内置工具重名；`get_schemas` 返回 deepcopy。实测修复前 8 线程并发首调出现「实例数=2、executor=0、缺 5 个默认工具」，修复后实例唯一且 executor 齐全；20 轮 × 8 线程压力无异常 |
| T28 | `backend/api/deepseek.py:56,104,121-151,201,264` | ① `<think/>` 自闭合标签被 SSE 分片截断时泄漏进正文；② `payload.update(kwargs)` 覆盖协议字段（`stream=False` 会静默返回空内容） | **已修复**：残留前缀集扩为 `('<think/', '<think ', '<think')` 且大小写无关；新增 `_merge_extra_params` 丢弃冲突键并告警。实测修复前 `['<think/','>']` 分片泄漏标签文本，修复后不泄漏且正常文本未误伤；`stream=False` 不再静默返回空 |

## 复核追加修复（不在原 37 条立项内，由审查 Agent 发现）

| 位置 | 问题 | 处置 |
|------|------|------|
| `backend/auth.py:47` | `bcrypt.checkpw` 遇脏 hash 抛 `ValueError`；实测 `$2b$12$tooshort` 抛的是 `PanicException`（非 `Exception` 子类），会击穿 ASGI 中间件终止进程 | 已修：先做 bcrypt 形态预校验 + 捕获异常返 401（实测三条脏 hash 由 500/崩溃 → 401） |
| `backend/auth.py:22` | `USERNAME_PATTERN` 用 `re.DOTALL` 放行换行、`ACCOUNT/PASSWORD_PATTERN` 用 `$` 放行尾随换行 | 已修：去 DOTALL、排除控制字符、`$`→`\\Z`（实测 `"a\\nb"`/`"user\\n"` 由放行 → 拒绝，合法值不受影响） |
| `backend/lc/reranker.py:16` | `api_key` 会被 `repr()` 输出（日志/追踪即泄露密钥）；实测 `Field(repr=False)` 在本类上**无效**（pydantic v2 Field 用在 langchain 的 v1 模型基类） | 已修：显式 `__repr__` + `dict()` 剔除 api_key，属性访问不变 |
| `backend/lc/reranker.py:55` | 客户端返回 None/非列表时 AttributeError；`relevance_score` 为 null 时排序抛 TypeError → 整条 RAG 链路失败 | 已修：返回值校验 + float 归一化 + 失败回退原始检索顺序（实测 None/dict/越界索引均安全回退） |
| `frontend/src/stores/sourceDrawer.js:37` | chunks 路由裸拼未编码（api.js 已编码但该文件遗漏） | 已修：`encodeURIComponent`（实测含 `#`/空格/中文的 chunk_id 由 URL 破坏 → 正确编码） |
| `Scripts/*_log.txt` | 复核 Agent 的验证脚本 import 模块时被 `log()` 追加写入（tracked 文件被污染） | 已还原：`git checkout --` 恢复，工作区无 diff |

## 明确判定为误诊 / 不修的意见（附实证）

| 位置 | OCR/简报结论 | 实证证据 |
|------|--------------|----------|
| `backend/agent/mcp_client.py:90,127` | 用 snake_case 读驼峰字段会导致工具参数 schema 变空 | **误诊**。实测已安装 SDK：`Tool.model_fields` = `input_schema`、`CallToolResult` = `structured_content`、`ImageContent` = `mime_type`，全为 snake_case，代码写法正确。附带发现真问题（`mcp` 未钉版本）已在 T03 修复 |
| `backend/agent/structured_query.py` 子查询绕过 | `FROM (SELECT * FROM sqlite_master)` 不被拦截 | **误诊**。实测该输入被拒绝；真正的绕过是逗号连接与单引号表名，已在 T04 修复 |
| `frontend/src/assets/graphrag.css:70` | `display:none` 因**加载顺序**变化会导致下拉完全不显示 | **因果判断有误**。用 `@vue/compiler-sfc` 编译实测：scoped 规则为 `.kg-search-results[data-v-x]`（特异性 20）恒胜全局（10），与加载顺序无关；删除属「消除双份显隐真相」的等价清理 |
| `AppSidebar` 搜索下拉被滚动容器裁剪 | 应 Teleport 出滚动容器 | **不修**。headless Chrome 命中测试：≥768px 窗口 8/8 项可达（下拉自身本就只显示约 5 条）；视口 <550px 时 Teleport 到 body 同样超出视口，无法修好最坏情况。另 `.kg-rel-search-*` 全仓仅有 CSS、无模板引用（死样式） |
| `Scripts/lore_sync.py` `--full` 帮助文本 | 声明「清空本地后重下所有」与实现不符 | **部分不修**。帮助文本已注明「stories 仍走增量」；未改实现语义（用户可能依赖 stories 增量） |

## 未立项意见

`output/ocr-scan-result.csv` 中的 **299** 条未立项意见保持原样（`low` 级可维护性/死代码/命名/文档一致性为主，以及未立项文件上的 medium 项）。本轮修复过程中，凡与已修文件重叠的低价值项（死代码、硬编码、注释不一致等）已由各修复 Agent 顺带处理了一部分；其余可按需再开一轮。

## 变更日志

| 时间 | 事件 | 说明 |
|------|------|------|
| 2026-09-19 | 立项 | Planner 从 336 条中立项 37 条（P0 6 / P1 15 / P2 16），按文件不冲突划 8 组 |
| 2026-09-19 | 第 1 轮修复 | 8 个 Agent 并行完成 21 条 P0/P1 |
| 2026-09-19 | 复核 | 4 个审查 Agent 独立复核（自建 49+35+… 条验证用例），打回 5 项 |
| 2026-09-19 | 第 3 轮返工 | T01 归属落库、T07 弱引用锁、T04 单引号绕过、T03 lockfile、T15 删除守卫 |
| 2026-09-19 | 第 4 轮 | P2 剩余项 + 复核追加 6 小项 |
| 2026-09-19 | 第 5 轮 | 补齐此前被排除的 T27/T28（并发初始化竞态、SSE 标签泄漏与 kwargs 覆盖协议字段） |
| 2026-09-19 | 终版 | **账本归零：37 条全部已复核，0 条未修复**；后端 700 passed / 前端 118 passed；44 个文件变更待用户确认提交 |
