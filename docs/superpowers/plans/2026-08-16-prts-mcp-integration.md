# PRTS-MCP 接入与 Agent 工具面扩展 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 ARKNIGHTS Agent 接入 prts-mcp（7 个白名单工具），实现通用 MCP Bridge、前端 MCP 结果/立绘展示，并把快速问题改成 4 类能力模板池。

**Architecture:** 后端新增 `McpClientManager`（stdio 子进程 + 官方 `mcp` SDK），通过白名单把 MCP 工具 schema 动态转成 OpenAI Function Calling 格式注册进现有 `ToolRegistry`；工具结果用 `ToolResultPayload` 分离 LLM 上下文与前端展示，图片只进前端。MCP 为可选依赖，连接失败优雅降级。

**Tech Stack:** Python 3.11（生产 venv）/ FastAPI / `mcp==2.0.0` / `prts-mcp==2.7.0` / Vue 3 / Vitest。

**Spec:** `docs/superpowers/specs/2026-08-16-prts-mcp-integration-design.md`

**重要约束：**
- AGENTS.md 要求每次 `git commit` 前征得用户同意；本计划中的 Commit 步骤均需先向用户确认。
- 修改现有符号前先运行 GitNexus 影响分析；出现 HIGH/CRITICAL 风险先停下报告用户。
- 本计划不得 push；push 到 master 会自动部署，需用户明确授权。
- 现有工作区有用户未提交改动（`backend/.env.example`、`backend/evaluation/rag_eval.py`、`resume_text.txt`），所有 git 操作只 add 本计划列出的文件。

---

## 文件结构

| 文件 | 动作 | 职责 |
|---|---|---|
| `backend/config.py` | 修改 | PRTS_MCP 开关/命令/超时配置 |
| `backend/.env.example` | 修改 | 新增 PRTS_MCP 配置示例 |
| `test/conftest.py` | 修改 | 测试环境默认关闭 MCP |
| `backend/agent/tool_result.py` | 新建 | LLM/前端结果分离包装 |
| `backend/agent/core.py` | 修改 | 识别 `ToolResultPayload` 并分流 |
| `backend/agent/tools.py` | 修改 | `ToolRegistry` 支持动态 schema |
| `backend/agent/mcp_client.py` | 新建 | MCP 客户端、schema 转换、结果拆分 |
| `backend/main.py` | 修改 | lifespan 启停 MCP、`/status`、快速问题模板池 |
| `backend/agent/prompts.py` | 修改 | 新工具路由规则 |
| `backend/quick_questions.py` | 新建 | 4 类快速问题模板池 |
| `backend/requirements.txt` | 修改 | 增加 `prts-mcp==2.7.0` |
| `test/test_config.py`、`test/test_tools.py`、`test/test_core.py`、`test/test_prompts.py`、`test/test_api.py` | 修改 | 覆盖新行为 |
| `test/test_mcp_client.py`、`test/test_quick_questions.py`、`test/test_mcp_integration.py` | 新建 | 纯函数/模板池/本地集成测试 |
| `frontend/src/utils/toolMeta.js` | 新建 | MCP 工具元数据与展示数据归一化（纯函数） |
| `frontend/test/toolMeta.test.js` | 新建 | 前端纯函数测试 |
| `frontend/src/views/ChatView.vue` | 修改 | MCP 工具卡片渲染、名称/图标、fallback 快速问题 |
| `.gitignore` | 修改 | 忽略 `.venv/` |
| `.github/workflows/ci-cd.yml` | 修改 | deploy job 安装 Python 依赖 |
| `docs/superpowers/specs/...` | 已提交 | 设计文档（已含 Python 3.11 决策） |

---

## Task 0: 影响分析

- [ ] **Step 1: 分析待修改符号的上游影响**

Run（在仓库根目录，逐条执行）：

```powershell
npx --no-install gitnexus impact -d upstream --include-tests ToolRegistry
npx --no-install gitnexus impact -d upstream --include-tests execute_tool
npx --no-install gitnexus impact -d upstream --include-tests add_tool_result
npx --no-install gitnexus impact -d upstream --include-tests lifespan
npx --no-install gitnexus impact -d upstream --include-tests get_quick_questions
npx --no-install gitnexus impact -d upstream --include-tests SYSTEM_PROMPT
```

Expected: 每个结果里没有 `CRITICAL` 或 `HIGH`。若有，停止并把结果报告用户，不得继续编辑。

- [ ] **Step 2: 记录当前基线**

```powershell
git status --short
git log --oneline -3
```

Expected: 工作区仅包含已知的用户未提交文件 + 本计划新增文件；基线提交为 `2151fbe`。

---

## Task 1: 配置项 + 测试环境开关

**Files:**
- Modify: `backend/config.py:12-16`
- Modify: `backend/.env.example`
- Modify: `test/conftest.py:11-13`
- Modify: `test/test_config.py`

- [ ] **Step 1: 写失败测试**

在 `test/test_config.py` 末尾追加：

```python
# ============================================================
# PRTS MCP 配置
# ============================================================

class TestPrtsMcpConfig:
    """PRTS MCP 配置项（测试环境由 conftest 关闭）。"""

    def test_prts_mcp_disabled_in_tests(self):
        assert config.PRTS_MCP_ENABLED is False

    def test_prts_mcp_command_default(self):
        assert config.PRTS_MCP_COMMAND == "prts-mcp"

    def test_prts_mcp_connect_timeout_default(self):
        assert config.PRTS_MCP_CONNECT_TIMEOUT == 10.0
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest test/test_config.py::TestPrtsMcpConfig -v`

Expected: FAIL，`AttributeError: module 'backend.config' has no attribute 'PRTS_MCP_ENABLED'`

- [ ] **Step 3: 修改 conftest 先关 MCP**

`test/conftest.py` 中 `os.environ.setdefault("JWT_SECRET", ...)` 后新增一行：

```python
os.environ.setdefault("PRTS_MCP_ENABLED", "false")
```

- [ ] **Step 4: 实现配置**

在 `backend/config.py` 的 `DATA_DIR = BASE_DIR / "data"` 之后插入：

```python
def _env_bool(name: str, default: bool) -> bool:
    """Parse a boolean environment variable (1/true/yes/on)."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")

# PRTS MCP（外部 MCP 工具接入，可选依赖）
PRTS_MCP_ENABLED = _env_bool("PRTS_MCP_ENABLED", True)
PRTS_MCP_COMMAND = os.environ.get("PRTS_MCP_COMMAND", "prts-mcp")
PRTS_MCP_CONNECT_TIMEOUT = float(os.environ.get("PRTS_MCP_CONNECT_TIMEOUT", "10"))
```

- [ ] **Step 5: 更新 .env.example**

在 `backend/.env.example` 的 `# 后端端口` 块后追加：

```
# PRTS MCP（外部 MCP 工具，可选依赖；false 时完全跳过连接）
PRTS_MCP_ENABLED=true
PRTS_MCP_COMMAND=prts-mcp
PRTS_MCP_CONNECT_TIMEOUT=10
```

- [ ] **Step 6: 运行测试确认通过**

Run: `python -m pytest test/test_config.py -v`

Expected: 全部 PASS（注意 conftest 使 `PRTS_MCP_ENABLED is False`）。

- [ ] **Step 7: 提交（先征得用户同意）**

注意：`backend/.env.example` 在本计划开始前就有用户未提交改动。提交前把 `git diff backend/.env.example` 给用户看；用户同意一并提交才 add 该文件，否则只提交其余三个文件。

```bash
git add backend/config.py test/conftest.py test/test_config.py
git commit -m "feat: 新增 PRTS MCP 配置项，测试环境默认关闭 MCP"
```

（若用户同意一并提交 `.env.example`，commit message 末尾追加说明该文件含既有改动。）

---

## Task 2: ToolResultPayload + core 分流

**Files:**
- Create: `backend/agent/tool_result.py`
- Modify: `backend/agent/core.py:13-16, 289-319, 565-611`
- Test: `test/test_core.py`

- [ ] **Step 1: 写失败测试**

在 `test/test_core.py` 文件头部 import 区增加：

```python
import asyncio
from backend.agent.tool_result import ToolResultPayload
from backend.agent.tools import ToolRegistry
from backend.agent.core import execute_tool
```

在文件末尾追加：

```python
# ============================================================
# execute_tool 的 ToolResultPayload 分流
# ============================================================

class TestExecuteToolResultPayload:
    """MCP 等工具返回 ToolResultPayload 时，LLM 内容与展示内容分离。"""

    def test_payload_fields_are_preserved(self):
        registry = ToolRegistry()

        async def fake_tool(args, session_id=""):
            return ToolResultPayload(
                llm_content={"summary": "no image"},
                display={"summary": "no image", "images": ["img1"]},
            )

        registry.register("fake_mcp_tool", fake_tool)
        tc = ToolCall(id="c1", name="fake_mcp_tool", arguments='{"q":"x"}')
        result = asyncio.run(execute_tool(registry, tc))

        assert isinstance(result, ToolResultPayload)
        assert result.llm_content == {"summary": "no image"}
        assert result.display == {"summary": "no image", "images": ["img1"]}

    def test_plain_result_stays_unchanged(self):
        registry = ToolRegistry()

        async def fake_tool(args, session_id=""):
            return {"ok": True}

        registry.register("plain_tool", fake_tool)
        tc = ToolCall(id="c2", name="plain_tool", arguments="{}")
        result = asyncio.run(execute_tool(registry, tc))

        assert result == {"ok": True}
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest test/test_core.py::TestExecuteToolResultPayload -v`

Expected: FAIL，`ModuleNotFoundError: No module named 'backend.agent.tool_result'`

- [ ] **Step 3: 创建 tool_result.py**

`backend/agent/tool_result.py`：

```python
"""
ToolResultPayload: separates LLM-facing content from frontend display data.

MCP tools can return large structured payloads and images. Images and big
payloads must never be serialized into the LLM tool message in
Session.add_tool_result().
"""
from dataclasses import dataclass
from typing import Any


@dataclass
class ToolResultPayload:
    llm_content: Any
    display: Any
```

- [ ] **Step 4: 修改 core.py 识别包装**

`backend/agent/core.py` 顶部 import 区（`from backend.agent.sessions import SessionManager` 附近）新增：

```python
from backend.agent.tool_result import ToolResultPayload
```

`execute_tool()` 中的 sanitize 段替换为：

```python
    try:
        result = await registry.execute(tool_call.name, args, session_id=session_id)
        if isinstance(result, ToolResultPayload):
            result = ToolResultPayload(
                llm_content=_sanitize_unicode(result.llm_content),
                display=_sanitize_unicode(result.display),
            )
        else:
            result = _sanitize_unicode(result)
        logger.info(f"[TOOL EXEC DONE] {tool_call.name} result_type={type(result).__name__}")
        return result
    except Exception as e:
        logger.error(f"[TOOL EXEC FAILED] {tool_call.name}({args}): {e}", exc_info=True)
        return {"error": f"工具执行失败: {str(e)}"}
```

`_agent_loop_unlocked()` 中结果回填循环（原 566-577 行区域）替换为：

```python
        # Record each tool result and notify frontend（严格按 LLM 输出顺序）
        for index, (_, result, elapsed_ms) in enumerate(timed_results):
            tc = tool_calls[index]
            llm_result = result.llm_content if isinstance(result, ToolResultPayload) else result
            display_result = result.display if isinstance(result, ToolResultPayload) else result
            session.add_tool_result(tc.id, llm_result)
            # Log tool result summary
            result_summary = ""
            if isinstance(display_result, list):
                result_summary = f"{len(display_result)} items"
            elif isinstance(display_result, dict):
                result_summary = display_result.get("error", "") or f"keys={list(display_result.keys())[:5]}"
            else:
                result_summary = str(display_result)[:100]
            logger.info(f"[TOOL RESULT] {tc.name} ({elapsed_ms:.0f}ms): {result_summary}")
            yield format_tool_call_result(tc.id, display_result, time_ms=elapsed_ms, tool_name=tc.name)

            # Collect source citations from tool results
            if tc.name == 'arknights_rag_search' and isinstance(llm_result, list):
                for item in llm_result:
                    cid = item.get('chunk_id')
                    coll = item.get('source', '')
                    if cid and cid not in collected_sources:
                        collected_sources[cid] = {
                            'chunk_id': cid,
                            'collection': coll,
                        }
            elif tc.name == 'web_search' and isinstance(llm_result, list):
                for item in llm_result:
                    url = item.get('url', '')
                    if url and url not in collected_sources:
                        collected_sources[url] = {
                            'source_id': 'web',
                            'title': item.get('title', ''),
                            'url': url,
                        }
```

同函数 trace 记录处，`result_summary=_summarize_tool_result(result)` 改为：

```python
                result_summary=_summarize_tool_result(
                    result.display if isinstance(result, ToolResultPayload) else result
                ),
```

- [ ] **Step 5: 运行测试**

Run: `python -m pytest test/test_core.py test/test_tool_implementations.py -v`

Expected: 全部 PASS。

- [ ] **Step 6: 提交（先征得用户同意）**

```bash
git add backend/agent/tool_result.py backend/agent/core.py test/test_core.py
git commit -m "feat: 工具结果区分 LLM 上下文与前端展示数据"
```

---

## Task 3: ToolRegistry 支持动态 schema

**Files:**
- Modify: `backend/agent/tools.py:108-126`
- Test: `test/test_tools.py`

- [ ] **Step 1: 写失败测试**

在 `test/test_tools.py` 的 `TestToolRegistry` 类中追加：

```python
    def test_register_schema_and_get_schemas(self):
        registry = ToolRegistry()
        schema = {
            "type": "function",
            "function": {
                "name": "mcp_test_tool",
                "description": "test",
                "parameters": {"type": "object", "properties": {}},
            },
        }
        registry.register_schema(schema)
        schemas = registry.get_schemas()
        names = [s["function"]["name"] for s in schemas]
        assert names[-1] == "mcp_test_tool"
        assert len(schemas) == len(TOOL_SCHEMAS) + 1

    def test_register_schema_same_name_replaces(self):
        registry = ToolRegistry()
        first = {
            "type": "function",
            "function": {"name": "dup_tool", "description": "old", "parameters": {}},
        }
        second = {
            "type": "function",
            "function": {"name": "dup_tool", "description": "new", "parameters": {}},
        }
        registry.register_schema(first)
        registry.register_schema(second)
        schemas = registry.get_schemas()
        dup = [s for s in schemas if s["function"]["name"] == "dup_tool"]
        assert len(dup) == 1
        assert dup[0]["function"]["description"] == "new"
```

把 `test_register_and_get_schemas` 中这一行：

```python
        assert schemas is TOOL_SCHEMAS  # same object reference
```

改为：

```python
        assert schemas == TOOL_SCHEMAS  # 空 registry 时无动态 schema
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest test/test_tools.py -v`

Expected: 新增两个测试 FAIL（`AttributeError: 'ToolRegistry' object has no attribute 'register_schema'`），其余 PASS。

- [ ] **Step 3: 实现**

`backend/agent/tools.py` 的 `ToolRegistry.__init__` 替换为：

```python
    def __init__(self):
        self._tools: Dict[str, Callable] = {}
        self._dynamic_schemas: List[Dict] = []
```

`register` 之后新增：

```python
    def register_schema(self, schema: Dict):
        """Register an OpenAI Function Calling schema (idempotent by tool name)."""
        name = (schema.get("function") or {}).get("name", "")
        self._dynamic_schemas = [
            s for s in self._dynamic_schemas
            if (s.get("function") or {}).get("name") != name
        ]
        self._dynamic_schemas.append(schema)
        logger.info(f"Registered tool schema: {name}")
```

`get_schemas` 替换为：

```python
    def get_schemas(self) -> List[Dict]:
        """Get all tool schemas for API calls (static + dynamically registered)."""
        return [*TOOL_SCHEMAS, *self._dynamic_schemas]
```

- [ ] **Step 4: 运行测试**

Run: `python -m pytest test/test_tools.py -v`

Expected: 全部 PASS（注意 `test_global_registry_has_three_tools` 仍只查 4 个静态工具，不受影响）。

- [ ] **Step 5: 提交（先征得用户同意）**

```bash
git add backend/agent/tools.py test/test_tools.py
git commit -m "feat: ToolRegistry 支持动态注册 schema"
```

---

## Task 4: MCP 纯函数（schema 转换 + 结果拆分）

**Files:**
- Modify: `backend/requirements.txt`（依赖先行，否则测试无法 import mcp）
- Create: `backend/agent/mcp_client.py`（本任务先写纯函数与常量，Task 5 补 manager）
- Test: `test/test_mcp_client.py`

- [ ] **Step 0: 添加并安装依赖**

`backend/requirements.txt` 末尾新增：

```
prts-mcp==2.7.0
```

Run:

```powershell
python -m pip install -r backend/requirements.txt
```

Expected: 成功安装 `prts-mcp 2.7.0`、`mcp 2.0.0` 及其依赖（本机 Python 3.12，满足 ≥3.10）。

- [ ] **Step 1: 写失败测试**

创建 `test/test_mcp_client.py`：

```python
"""
Tests for backend.agent.mcp_client: schema conversion, allowlist,
result extraction (text/structured/images).
"""
import sys
import json
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent.parent))

from backend.agent.mcp_client import (
    MCP_ALLOWLIST,
    convert_mcp_tool_to_openai_schema,
    extract_mcp_result,
    register_mcp_tools,
    make_mcp_executor,
)
from backend.agent.tool_result import ToolResultPayload


def make_tool(name, description, input_schema):
    return SimpleNamespace(name=name, description=description, input_schema=input_schema)


def make_call_result(content, structured=None):
    return SimpleNamespace(content=content, structured_content=structured)


class TestAllowlist:
    def test_seven_tools_allowed(self):
        assert MCP_ALLOWLIST == {
            "search_prts", "get_stage_info", "get_stage_enemies",
            "get_enemy_info", "list_items", "get_item_info", "operator_artwork",
        }


class TestConvertSchema:
    def test_basic_conversion(self):
        tool = make_tool("get_item_info", "查询材料", {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        })
        schema = convert_mcp_tool_to_openai_schema(tool)
        assert schema["type"] == "function"
        assert schema["function"]["name"] == "get_item_info"
        assert schema["function"]["parameters"]["required"] == ["name"]

    def test_operator_artwork_gets_preview_default(self):
        tool = make_tool("operator_artwork", "查询立绘", {
            "type": "object",
            "properties": {
                "operator_name": {"type": "string"},
                "action": {"type": "string"},
                "variant": {"type": "string"},
            },
        })
        schema = convert_mcp_tool_to_openai_schema(tool)
        variant = schema["function"]["parameters"]["properties"]["variant"]
        assert variant["default"] == "preview"
        assert "图片只用于前端展示" in schema["function"]["description"]

    def test_input_schema_is_copied_not_mutated(self):
        input_schema = {"type": "object", "properties": {}}
        tool = make_tool("operator_artwork", "查询立绘", input_schema)
        convert_mcp_tool_to_openai_schema(tool)
        assert "variant" not in input_schema["properties"]


class TestExtractMcpResult:
    def test_text_structured_and_image_split(self):
        raw = make_call_result(
            content=[
                SimpleNamespace(type="text", text="**阿米娅**"),
                SimpleNamespace(type="image", mime_type="image/png", data="QUJD"),
            ],
            structured={"operator_name": "阿米娅", "total": 1},
        )
        payload = extract_mcp_result(raw, "operator_artwork")
        assert isinstance(payload, ToolResultPayload)
        assert "阿米娅" in payload.llm_content
        assert "QUJD" not in payload.llm_content
        assert payload.display["images"][0]["data_url"] == "data:image/png;base64,QUJD"
        assert payload.display["structured"]["operator_name"] == "阿米娅"

    def test_oversized_image_is_dropped(self):
        big = "A" * 400_001
        raw = make_call_result(
            content=[SimpleNamespace(type="image", mime_type="image/png", data=big)],
            structured=None,
        )
        payload = extract_mcp_result(raw, "operator_artwork")
        assert payload.display["images"] == []
        assert "图片过大" in payload.llm_content

    def test_oversized_structured_is_truncated_for_display(self):
        raw = make_call_result(
            content=[SimpleNamespace(type="text", text="ok")],
            structured={"x": "长" * 60_000},
        )
        payload = extract_mcp_result(raw, "list_items")
        assert payload.display["structured"]["_truncated"] is True
        assert "长" * 60_000 not in payload.llm_content


class TestRegisterMcpTools:
    class FakeRegistry:
        def __init__(self):
            self.schemas = []
            self.executors = {}

        def register_schema(self, schema):
            self.schemas.append(schema)

        def register(self, name, executor):
            self.executors[name] = executor

    def test_only_allowlisted_tools_registered(self):
        manager_tools = [
            make_tool("get_item_info", "d", {"type": "object"}),
            make_tool("get_operator_archives", "d", {"type": "object"}),
        ]
        registry = self.FakeRegistry()
        count = register_mcp_tools(registry, SimpleNamespace(tools=manager_tools))
        assert count == 1
        assert "get_item_info" in registry.executors
        assert "get_operator_archives" not in registry.executors

    def test_executor_forces_artwork_preview(self):
        manager_tools = [make_tool("operator_artwork", "d", {"type": "object"})]
        registry = self.FakeRegistry()
        register_mcp_tools(registry, SimpleNamespace(tools=manager_tools))

        import asyncio

        class FakeManager:
            def __init__(self):
                self.called_args = None

            async def call_tool(self, name, arguments):
                self.called_args = arguments
                return make_call_result(
                    content=[SimpleNamespace(type="text", text="ok")],
                    structured=None,
                )

        fake = FakeManager()
        executor = make_mcp_executor(fake, "operator_artwork")
        payload = asyncio.run(executor({"action": "get", "operator_name": "阿米娅"}))
        assert fake.called_args["variant"] == "preview"
        assert payload.llm_content == "ok"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest test/test_mcp_client.py -v`

Expected: FAIL，`ModuleNotFoundError: No module named 'backend.agent.mcp_client'`

- [ ] **Step 3: 创建 mcp_client.py（纯函数部分）**

创建 `backend/agent/mcp_client.py`：

```python
"""
Generic MCP stdio client + PRTS tool bridge.

- spawns prts-mcp as a stdio subprocess via the official mcp SDK
- filters discovered tools through MCP_ALLOWLIST
- converts MCP JSON schemas to OpenAI Function Calling schemas
- splits call results into ToolResultPayload (images never reach the LLM)
"""

import asyncio
import copy
import json
import logging
from typing import Any, Dict, List, Optional

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from backend.agent.tool_result import ToolResultPayload

logger = logging.getLogger(__name__)

MCP_ALLOWLIST = frozenset({
    "search_prts",
    "get_stage_info",
    "get_stage_enemies",
    "get_enemy_info",
    "list_items",
    "get_item_info",
    "operator_artwork",
})

LLM_RESULT_MAX_CHARS = 12_000
DISPLAY_RESULT_MAX_CHARS = 50_000
MAX_IMAGE_B64_CHARS = 400_000


def _attr(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _truncate_text(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars - 40] + "\n...(已截断)"


def _truncate_json_for_display(value: Any, max_chars: int) -> Any:
    text = _json_text(value)
    if len(text) <= max_chars:
        return value
    return {
        "_truncated": True,
        "_message": f"结果过大，已截断至约 {max_chars} 字符；请缩小查询范围",
        "_preview": text[:max_chars - 120],
    }


def convert_mcp_tool_to_openai_schema(tool: Any) -> Dict[str, Any]:
    """Convert one MCP Tool to an OpenAI Function Calling schema dict."""
    name = _attr(tool, "name") or ""
    description = _attr(tool, "description") or f"外部 MCP 工具 {name}"
    raw_schema = _attr(tool, "input_schema", {})
    if hasattr(raw_schema, "model_dump"):
        schema = raw_schema.model_dump()
    elif isinstance(raw_schema, dict):
        schema = copy.deepcopy(raw_schema)
    else:
        schema = {"type": "object", "properties": {}}

    if name == "operator_artwork":
        description = (
            f"{description}\n"
            "先用 action=list 获取 artwork_id，再用 action=get 获取一张图片；"
            "图片只用于前端展示，不要基于图片内容推理。"
        )
        props = schema.setdefault("properties", {})
        variant = props.setdefault("variant", {})
        variant["type"] = "string"
        variant["enum"] = ["preview", "large"]
        variant["default"] = "preview"
        variant["description"] = "图片变体：preview=256px（默认，推荐），large=1024px"

    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": schema,
        },
    }


def extract_mcp_result(call_result: Any, tool_name: str = "") -> ToolResultPayload:
    """Split an MCP CallToolResult into LLM content and frontend display."""
    text_parts: List[str] = []
    images: List[Dict[str, str]] = []
    content = _attr(call_result, "content", None) or []

    for item in content:
        item_type = _attr(item, "type", None)
        if item_type == "text":
            text_parts.append(_attr(item, "text", "") or "")
        elif item_type == "image":
            data = _attr(item, "data", "") or ""
            mime = _attr(item, "mime_type", "") or "image/png"
            if data and len(data) <= MAX_IMAGE_B64_CHARS:
                images.append({
                    "label": tool_name,
                    "mime": mime,
                    "data_url": f"data:{mime};base64,{data}",
                })
            else:
                text_parts.append(f"[图片过大已省略，base64 长度 {len(data)}]")

    text = "\n\n".join(part for part in text_parts if part)
    structured = _attr(call_result, "structured_content", None)
    display_structured = (
        structured if structured is None
        else _truncate_json_for_display(structured, DISPLAY_RESULT_MAX_CHARS)
    )

    llm_parts = [text or "(工具返回空文本)"]
    if structured is not None:
        llm_parts.append(
            "structuredContent:\n" + _truncate_text(_json_text(structured), LLM_RESULT_MAX_CHARS)
        )

    return ToolResultPayload(
        llm_content="\n\n".join(llm_parts),
        display={
            "text": text,
            "structured": display_structured,
            "images": images,
        },
    )


def make_mcp_executor(manager: "McpClientManager", tool_name: str):
    """Create a ToolRegistry-compatible executor for one MCP tool."""
    async def _execute(arguments: Dict[str, Any], session_id: str = "") -> ToolResultPayload:
        call_args = dict(arguments or {})
        if (
            tool_name == "operator_artwork"
            and call_args.get("action") == "get"
            and "variant" not in call_args
        ):
            call_args["variant"] = "preview"
        try:
            raw = await manager.call_tool(tool_name, call_args)
        except Exception as exc:
            logger.error(f"[MCP] tool {tool_name} failed: {exc}", exc_info=True)
            return ToolResultPayload(
                llm_content=json.dumps(
                    {
                        "error": f"MCP 工具调用失败: {exc}",
                        "hint": "可改用 arknights_rag_search 或 web_search",
                    },
                    ensure_ascii=False,
                ),
                display={
                    "error": f"MCP 工具调用失败: {exc}",
                    "structured": None,
                    "images": [],
                },
            )
        return extract_mcp_result(raw, tool_name)

    return _execute


def register_mcp_tools(registry, manager: "McpClientManager") -> int:
    """Register allowlisted MCP tools (schemas + executors) into a ToolRegistry."""
    registered = 0
    for tool in manager.tools:
        name = _attr(tool, "name", "")
        if name not in MCP_ALLOWLIST:
            continue
        schema = convert_mcp_tool_to_openai_schema(tool)
        registry.register_schema(schema)
        registry.register(name, make_mcp_executor(manager, name))
        registered += 1
    logger.info(f"[MCP] registered {registered} prts-mcp tools")
    return registered


class McpClientManager:
    """Long-lived MCP stdio client (full implementation in Task 5)."""

    def __init__(self, command: str, env: Optional[Dict[str, str]] = None, connect_timeout: float = 10.0):
        self.command = command
        self.env = env or {}
        self.connect_timeout = connect_timeout
        self.connected = False
        self.tools: List[Any] = []
        self.last_error = ""
        self._params = StdioServerParameters(command=command, env=self.env)
        self._transport_cm = None
        self._session_cm = None
        self._session = None
        self._call_lock = asyncio.Semaphore(1)

    async def start(self) -> None:
        raise NotImplementedError("Task 5 实现")

    async def close(self) -> None:
        raise NotImplementedError("Task 5 实现")

    async def call_tool(self, name: str, arguments: Dict[str, Any]) -> Any:
        raise NotImplementedError("Task 5 实现")
```

- [ ] **Step 4: 运行测试**

Run: `python -m pytest test/test_mcp_client.py -v`

Expected: 除 manager 外全部 PASS（本任务没有调用 `start`/`call_tool`；`make_mcp_executor` 测试用 FakeManager，不经过真实 manager）。

- [ ] **Step 5: 提交（先征得用户同意）**

```bash
git add backend/requirements.txt backend/agent/mcp_client.py test/test_mcp_client.py
git commit -m "feat: 新增 MCP 依赖与 schema 转换/结果拆分纯函数"
```

---

## Task 5: McpClientManager 完整实现

**Files:**
- Modify: `backend/agent/mcp_client.py`（替换 Task 4 中的 manager 占位实现）
- Test: `test/test_mcp_client.py`（追加失败路径测试）

- [ ] **Step 1: 追加失败测试**

`test/test_mcp_client.py` 顶部追加 import：

```python
import pytest
import asyncio
```

文件末尾追加：

```python
class TestMcpClientManagerFailures:
    def test_call_tool_when_disconnected_raises(self):
        from backend.agent.mcp_client import McpClientManager

        manager = McpClientManager(command="definitely-not-a-real-command-xyz")

        async def _test():
            with pytest.raises(RuntimeError, match="未连接"):
                await manager.call_tool("get_item_info", {"name": "源岩"})

        asyncio.run(_test())

    def test_close_never_started_is_noop(self):
        from backend.agent.mcp_client import McpClientManager

        manager = McpClientManager(command="definitely-not-a-real-command-xyz")

        async def _test():
            await manager.close()
            assert manager.connected is False

        asyncio.run(_test())
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest test/test_mcp_client.py::TestMcpClientManagerFailures -v`

Expected: FAIL（`call_tool` 抛 `NotImplementedError` 而不是 `RuntimeError`）。

- [ ] **Step 3: 实现 manager**

替换 `backend/agent/mcp_client.py` 中 `McpClientManager` 类的三个方法为：

```python
    async def start(self) -> None:
        if self.connected:
            return

        transport_cm = stdio_client(self._params)
        try:
            read_stream, write_stream = await asyncio.wait_for(
                transport_cm.__aenter__(), timeout=self.connect_timeout
            )
        except Exception as exc:
            try:
                await transport_cm.__aexit__(None, None, None)
            except Exception:
                pass
            self.last_error = str(exc)
            raise RuntimeError(f"MCP 子进程启动失败: {exc}") from exc

        session_cm = ClientSession(read_stream, write_stream)
        try:
            session = await asyncio.wait_for(
                session_cm.__aenter__(), timeout=self.connect_timeout
            )
            await asyncio.wait_for(session.initialize(), timeout=self.connect_timeout)
            tools_result = await asyncio.wait_for(
                session.list_tools(), timeout=self.connect_timeout
            )
        except Exception as exc:
            try:
                await session_cm.__aexit__(None, None, None)
            except Exception:
                pass
            try:
                await transport_cm.__aexit__(None, None, None)
            except Exception:
                pass
            self.last_error = str(exc)
            raise RuntimeError(f"MCP 会话初始化失败: {exc}") from exc

        self._transport_cm = transport_cm
        self._session_cm = session_cm
        self._session = session
        self.tools = list(_attr(tools_result, "tools", []) or [])
        self.connected = True
        logger.info(f"[MCP] connected to {self.command}, {len(self.tools)} tools discovered")

    async def close(self) -> None:
        if self._session_cm is not None:
            try:
                await self._session_cm.__aexit__(None, None, None)
            except Exception as exc:
                logger.warning(f"[MCP] session close failed: {exc}")
            self._session_cm = None
        if self._transport_cm is not None:
            try:
                await self._transport_cm.__aexit__(None, None, None)
            except Exception as exc:
                logger.warning(f"[MCP] transport close failed: {exc}")
            self._transport_cm = None
        self._session = None
        self.connected = False

    async def call_tool(self, name: str, arguments: Dict[str, Any]) -> Any:
        async with self._call_lock:
            if not self.connected or self._session is None:
                raise RuntimeError(f"MCP 客户端未连接，无法调用 {name}")
            return await self._session.call_tool(name, arguments or {})
```

- [ ] **Step 4: 运行测试**

Run: `python -m pytest test/test_mcp_client.py -v`

Expected: 全部 PASS。

- [ ] **Step 5: 提交（先征得用户同意）**

```bash
git add backend/agent/mcp_client.py test/test_mcp_client.py
git commit -m "feat: 实现 MCP stdio 客户端生命周期与串行调用"
```

---

## Task 6: FastAPI lifespan、/status 与 MCP 注册

**Files:**
- Modify: `backend/main.py:67-70, 103-110, 314-323`
- Modify: `test/test_api.py`

- [ ] **Step 1: 写失败测试**

在 `test/test_api.py` 的 `TestDataEndpoints` 类中追加：

```python
    def test_status_has_mcp_info(self):
        resp = run_async(_request("GET", "/status"))
        assert resp.status_code == 200
        mcp = resp.json()["mcp"]
        assert mcp["enabled"] is False  # conftest 关闭
        assert mcp["connected"] is False
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest test/test_api.py::TestDataEndpoints::test_status_has_mcp_info -v`

Expected: FAIL，`KeyError: 'mcp'`

- [ ] **Step 3: 修改 main.py**

import 区（`from backend.api.llm_factory import ...` 之后）新增：

```python
from backend.agent.tools import get_tool_registry
from backend.agent.mcp_client import McpClientManager, register_mcp_tools
```

`TRACE_RETENTION_DAYS = 30` 之后新增：

```python
_mcp_manager: Optional[McpClientManager] = None


async def _start_prts_mcp():
    """Start prts-mcp and register allowlisted tools. Failure is non-fatal."""
    global _mcp_manager
    if not config.PRTS_MCP_ENABLED:
        logger.info("[MCP] PRTS_MCP_ENABLED=false, skipping MCP startup")
        return

    manager = McpClientManager(
        command=config.PRTS_MCP_COMMAND,
        env={
            "PRTS_OUTPUT_CHANNEL": "both",
            "LOCAL_IMAGE": "false",
            "PRTS_IMAGE_CACHE": "true",
            "IMAGES_ENABLED": "true",
        },
        connect_timeout=config.PRTS_MCP_CONNECT_TIMEOUT,
    )
    try:
        await manager.start()
        count = register_mcp_tools(get_tool_registry(), manager)
        _mcp_manager = manager
        logger.info(f"[MCP] prts-mcp ready: {count} tools registered")
    except Exception as exc:
        logger.warning(f"[MCP] prts-mcp unavailable, continuing without MCP tools: {exc}")
        _mcp_manager = None


async def _stop_prts_mcp():
    global _mcp_manager
    if _mcp_manager is not None:
        await _mcp_manager.close()
        _mcp_manager = None


def _mcp_status() -> dict:
    if not config.PRTS_MCP_ENABLED:
        return {"enabled": False, "connected": False, "tool_count": 0}
    if _mcp_manager is not None and _mcp_manager.connected:
        return {"enabled": True, "connected": True, "tool_count": len(_mcp_manager.tools)}
    error = _mcp_manager.last_error if _mcp_manager is not None else "not started"
    return {"enabled": True, "connected": False, "tool_count": 0, "error": error}
```

`lifespan` 改为：

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    if not config.SILICONFLOW_API_KEY:
        raise RuntimeError("SILICONFLOW_API_KEY 环境变量未设置，拒绝启动。请在 .env 中配置。")
    await init_db()
    retention_task = asyncio.create_task(_trace_retention_loop())
    await _start_prts_mcp()
    yield
    retention_task.cancel()
    await _stop_prts_mcp()
```

`/status` 返回值增加一行：

```python
        "mcp": _mcp_status(),
```

- [ ] **Step 4: 运行测试**

Run: `python -m pytest test/test_api.py test/test_config.py -v`

Expected: 全部 PASS（ASGITransport 不触发 lifespan，不会启动子进程；conftest 已把 MCP 关闭）。

- [ ] **Step 5: 提交（先征得用户同意）**

```bash
git add backend/main.py test/test_api.py
git commit -m "feat: FastAPI lifespan 集成 prts-mcp，/status 暴露 MCP 状态"
```

---

## Task 7: 提示词路由规则

**Files:**
- Modify: `backend/agent/prompts.py:11-16`
- Test: `test/test_prompts.py`

- [ ] **Step 1: 写失败测试**

在 `test/test_prompts.py` 的 `TestSystemPrompt` 类追加：

```python
    def test_prompt_contains_mcp_tool_names(self):
        assert "get_stage_enemies" in SYSTEM_PROMPT
        assert "get_item_info" in SYSTEM_PROMPT
        assert "operator_artwork" in SYSTEM_PROMPT
        assert "search_prts" in SYSTEM_PROMPT
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest test/test_prompts.py::TestSystemPrompt::test_prompt_contains_mcp_tool_names -v`

Expected: FAIL。

- [ ] **Step 3: 修改 SYSTEM_PROMPT**

把 `backend/agent/prompts.py` 中这一行：

```python
- 查数值比较/排序/统计（如"攻击力>700""6星按防御排序"） → arknights_structured_query
```

替换为：

```python
- 查数值比较/排序/统计（如"攻击力>700""6星按防御排序"） → arknights_structured_query
- 查关卡详情/关卡出怪/关卡内敌人属性 → get_stage_info / get_stage_enemies / get_enemy_info
- 查材料用途、掉落、获取途径 → list_items / get_item_info
- 名称或关卡 ID 不确定时 → 先用 search_prts 解析
- 查干员立绘/时装 → operator_artwork（先 action="list" 拿 artwork_id，再 action="get" 取图）
```

- [ ] **Step 4: 运行测试**

Run: `python -m pytest test/test_prompts.py -v`

Expected: 全部 PASS。

- [ ] **Step 5: 提交（先征得用户同意）**

```bash
git add backend/agent/prompts.py test/test_prompts.py
git commit -m "feat: 提示词新增 prts-mcp 工具路由规则"
```

---

## Task 8: 快速问题模板池（后端）

**Files:**
- Create: `backend/quick_questions.py`
- Modify: `backend/main.py:1156-1334`（`get_quick_questions` 与 relation 问题的 category 字段）
- Test: `test/test_quick_questions.py`、`test/test_api.py`

- [ ] **Step 1: 写失败测试**

创建 `test/test_quick_questions.py`：

```python
"""
Tests for backend.quick_questions: capability template pools.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from backend.quick_questions import (
    STRUCTURED_TEMPLATES,
    PRTS_MCP_TEMPLATES,
    RAG_FALLBACK_TEMPLATES,
    pick_template,
    pick_rag_question,
)


class TestTemplatePools:
    def test_four_categories_have_templates(self):
        assert len(STRUCTURED_TEMPLATES) >= 3
        assert len(PRTS_MCP_TEMPLATES) >= 3
        assert len(RAG_FALLBACK_TEMPLATES) >= 1

    def test_all_templates_have_required_fields(self):
        for template in STRUCTURED_TEMPLATES + PRTS_MCP_TEMPLATES + RAG_FALLBACK_TEMPLATES:
            assert template["label"]
            assert template["question"]
            assert template["category"] in {"rag", "graph", "structured", "prts_mcp"}


class TestPickTemplate:
    def test_prefers_non_excluded_label(self, monkeypatch):
        templates = [
            {"label": "a", "question": "qa", "category": "structured"},
            {"label": "b", "question": "qb", "category": "structured"},
        ]
        monkeypatch.setattr("backend.quick_questions.random.choice", lambda seq: seq[0])
        picked = pick_template(templates, {"a"})
        assert picked["label"] == "b"

    def test_falls_back_when_all_excluded(self, monkeypatch):
        templates = [{"label": "a", "question": "qa", "category": "structured"}]
        monkeypatch.setattr("backend.quick_questions.random.choice", lambda seq: seq[0])
        picked = pick_template(templates, {"a"})
        assert picked["label"] == "a"


class TestPickRagQuestion:
    def test_returns_rag_question_with_data(self, monkeypatch):
        monkeypatch.setattr("backend.quick_questions.random.shuffle", lambda seq: None)
        monkeypatch.setattr("backend.quick_questions.random.choice", lambda seq: seq[0])
        q = pick_rag_question(["阿米娅"], [], [], [], set())
        assert q["category"] == "rag"
        assert q["question"] == "阿米娅的技能是什么"

    def test_returns_fallback_without_data(self):
        q = pick_rag_question([], [], [], [], set())
        assert q["category"] == "rag"
        assert q["question"]
```

`test/test_api.py` 的 `test_quick_questions` 替换为：

```python
    def test_quick_questions(self):
        resp = run_async(_request("GET", "/quick-questions"))
        assert resp.status_code == 200
        data = resp.json()
        assert "questions" in data
        assert len(data["questions"]) == 4
        categories = {q["category"] for q in data["questions"]}
        assert categories == {"rag", "graph", "structured", "prts_mcp"}
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest test/test_quick_questions.py test/test_api.py::TestDataEndpoints::test_quick_questions -v`

Expected: FAIL（模块不存在；API 返回 5 个且无 category）。

- [ ] **Step 3: 创建 backend/quick_questions.py**

```python
"""
Quick-question template pools: 4 capability categories.

Each refresh returns one question per category so the UI doubles as a
capability tour (RAG / GraphRAG / structured query / PRTS-MCP).
"""

import random
from typing import Dict, List, Sequence, Set

STRUCTURED_TEMPLATES = [
    {
        "label": "高攻击六星近卫",
        "question": "哪些六星近卫的精二满级攻击力大于800？",
        "type": "structured",
        "category": "structured",
    },
    {
        "label": "领袖敌人血量榜",
        "question": "领袖级敌人中生命值最高的是谁？",
        "type": "structured",
        "category": "structured",
    },
    {
        "label": "六星重装防御榜",
        "question": "六星重装中精二满级防御力最高的是谁？",
        "type": "structured",
        "category": "structured",
    },
]

PRTS_MCP_TEMPLATES = [
    {
        "label": "1-7出怪顺序",
        "question": "1-7关卡的出怪顺序是什么？",
        "type": "stage",
        "category": "prts_mcp",
    },
    {
        "label": "1-7材料掉落",
        "question": "1-7关卡掉落什么材料？",
        "type": "item",
        "category": "prts_mcp",
    },
    {
        "label": "阿米娅立绘",
        "question": "阿米娅有哪些立绘？",
        "type": "artwork",
        "category": "prts_mcp",
    },
]

RAG_FALLBACK_TEMPLATES = [
    {
        "label": "银灰技能",
        "question": "银灰的技能是什么",
        "type": "skill",
        "category": "rag",
    },
    {
        "label": "乌萨斯的孩子们故事",
        "question": "乌萨斯的孩子们的故事内容",
        "type": "story",
        "category": "rag",
    },
]


def pick_template(templates: Sequence[Dict], exclude_labels: Set[str]) -> Dict:
    """Randomly pick a template whose label was not shown in the previous batch."""
    available = [t for t in templates if t["label"] not in exclude_labels]
    pool = available or list(templates)
    return random.choice(pool)


def pick_rag_question(
    operator_names: List[str],
    story_names: List[str],
    enemy_names: List[str],
    alias_candidates: List[tuple],
    exclude_labels: Set[str],
) -> Dict:
    """Pick one RAG-capability question, rotating across template kinds."""
    kinds = []
    if operator_names:
        kinds.append((
            "skill", operator_names,
            lambda name: f"{name}技能",
            lambda name: f"{name}的技能是什么",
        ))
    if story_names:
        kinds.append((
            "story", story_names,
            lambda name: f"{name}故事",
            lambda name: f"{name}的故事内容",
        ))
    if enemy_names:
        kinds.append((
            "enemy", enemy_names,
            lambda name: f"{name}敌人",
            lambda name: f"{name}的属性和能力是什么",
        ))
    if alias_candidates:
        kinds.append((
            "alias", alias_candidates,
            lambda pair: f"{pair[0]}别名",
            lambda pair: f"{pair[0]}的其他名称有哪些",
        ))

    random.shuffle(kinds)
    for kind, candidates, label_fn, question_fn in kinds:
        for _ in range(20):
            chosen = random.choice(candidates)
            label = label_fn(chosen)
            if label not in exclude_labels:
                return {
                    "label": label,
                    "question": question_fn(chosen),
                    "type": kind,
                    "category": "rag",
                }

    fallback = random.choice(RAG_FALLBACK_TEMPLATES)
    return dict(fallback)
```

- [ ] **Step 4: 修改 main.py 使用模板池**

import 区（`from backend.agent.core import agent_loop` 附近）新增：

```python
from backend.quick_questions import (
    STRUCTURED_TEMPLATES,
    PRTS_MCP_TEMPLATES,
    pick_template,
    pick_rag_question,
)
```

把 `get_quick_questions` 函数体从 `questions = []` 之后的关系/技能/故事/敌人/别名五段逻辑替换为：

关系段：保留原逻辑，但四处 `questions.append({... "type": "relation", ...})` 都增加 `"category": "graph"`（关系成功分支、无关系 fallback 分支、无干员节点 fallback 分支、异常 fallback 分支）。

然后在关系段之后、缓存更新之前，把原来的 2/3/4/5 段整体替换为：

```python
    # ===== RAG 能力：技能/故事/敌人/别名四类模板随机轮换 =====
    rag_question = pick_rag_question(
        _qq_operator_names or [],
        _qq_story_names or [],
        _qq_enemy_names or [],
        _qq_alias_candidates or [],
        exclude_labels,
    )
    questions.append(rag_question)
    exclude_labels.add(rag_question["label"])

    # ===== 结构化查询能力 =====
    structured_question = pick_template(STRUCTURED_TEMPLATES, exclude_labels)
    questions.append(structured_question)
    exclude_labels.add(structured_question["label"])

    # ===== PRTS-MCP 能力 =====
    mcp_question = pick_template(PRTS_MCP_TEMPLATES, exclude_labels)
    questions.append(mcp_question)
    exclude_labels.add(mcp_question["label"])
```

- [ ] **Step 5: 运行测试**

Run: `python -m pytest test/test_quick_questions.py test/test_api.py -v`

Expected: 全部 PASS。

- [ ] **Step 6: 提交（先征得用户同意）**

```bash
git add backend/quick_questions.py backend/main.py test/test_quick_questions.py test/test_api.py
git commit -m "feat: 快速问题改为 4 类能力模板池"
```

---

## Task 9: 前端 MCP 元数据纯函数

**Files:**
- Create: `frontend/src/utils/toolMeta.js`
- Test: `frontend/test/toolMeta.test.js`

- [ ] **Step 1: 写失败测试**

创建 `frontend/test/toolMeta.test.js`：

```js
/**
 * Tests for frontend/src/utils/toolMeta.js
 * Usage: cd frontend && npx vitest run test/toolMeta.test.js
 */
import { describe, it, expect } from 'vitest'
import {
  MCP_TOOL_NAMES,
  isMcpTool,
  getToolIcon,
  getToolDisplayName,
  summarizeMcpToolArgs,
  normalizeMcpDisplay,
} from '../src/utils/toolMeta.js'

describe('MCP tool metadata', () => {
  it('recognizes the seven allowlisted MCP tools', () => {
    expect(MCP_TOOL_NAMES.size).toBe(7)
    expect(isMcpTool('get_stage_enemies')).toBe(true)
    expect(isMcpTool('operator_artwork')).toBe(true)
    expect(isMcpTool('arknights_rag_search')).toBe(false)
  })

  it('resolves MCP display names and icons', () => {
    expect(getToolDisplayName('get_stage_enemies')).toBe('关卡出怪')
    expect(getToolDisplayName('operator_artwork')).toBe('干员立绘')
    expect(getToolIcon('get_item_info')).toBe('🧪')
    expect(getToolIcon('unknown_tool')).toBe('🔧')
  })

  it('summarizes MCP tool args', () => {
    expect(summarizeMcpToolArgs('search_prts', { query: '霜星' })).toBe('搜索: "霜星"')
    expect(summarizeMcpToolArgs('get_stage_enemies', { stage_id: 'main_01-07' })).toBe('出怪: main_01-07')
    expect(summarizeMcpToolArgs('get_enemy_info', { name: '霜星', stage_id: 'main_01-07' })).toBe('敌人: 霜星 @ main_01-07')
    expect(summarizeMcpToolArgs('get_item_info', { name: '源岩' })).toBe('物品: 源岩')
    expect(summarizeMcpToolArgs('operator_artwork', { operator_name: '阿米娅', action: 'list' })).toBe('阿米娅 立绘列表')
  })
})

describe('normalizeMcpDisplay', () => {
  it('normalizes a rows payload into a table view', () => {
    const view = normalizeMcpDisplay({
      text: 'ok',
      structured: { rows: [{ name: '霜星', hp: 1000 }], columns: ['name', 'hp'] },
      images: [],
    })
    expect(view.text).toBe('ok')
    expect(view.table.columns).toEqual(['name', 'hp'])
    expect(view.table.rows).toEqual([{ name: '霜星', hp: 1000 }])
  })

  it('normalizes an array payload into a table view', () => {
    const view = normalizeMcpDisplay({ structured: [{ a: 1 }, { a: 2 }] })
    expect(view.table.columns).toEqual(['a'])
    expect(view.table.rows.length).toBe(2)
  })

  it('falls back to JSON for non-table structures', () => {
    const view = normalizeMcpDisplay({ structured: { nested: { deep: true } } })
    expect(view.table).toBeNull()
    expect(view.json).toContain('deep')
  })

  it('keeps images untouched', () => {
    const view = normalizeMcpDisplay({
      structured: null,
      images: [{ data_url: 'data:image/png;base64,AAAA', mime: 'image/png', label: 'x' }],
    })
    expect(view.images.length).toBe(1)
  })
})
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd frontend; npx vitest run test/toolMeta.test.js`

Expected: FAIL，无法解析 `../src/utils/toolMeta.js`

- [ ] **Step 3: 创建 toolMeta.js**

```js
/**
 * MCP 工具元数据与展示数据归一化（纯函数，便于单测）。
 */

export const MCP_TOOL_NAMES = new Set([
  'search_prts',
  'get_stage_info',
  'get_stage_enemies',
  'get_enemy_info',
  'list_items',
  'get_item_info',
  'operator_artwork',
])

export const TOOL_ICONS = {
  arknights_rag_search: '📚',
  arknights_graphrag_search: '🕸️',
  web_search: '🌐',
  arknights_structured_query: '📊',
  search_prts: '🔎',
  get_stage_info: '🗺️',
  get_stage_enemies: '⚔️',
  get_enemy_info: '👾',
  list_items: '📦',
  get_item_info: '🧪',
  operator_artwork: '🖼️',
}

export const TOOL_DISPLAY_NAMES = {
  arknights_rag_search: '知识库检索',
  arknights_graphrag_search: '图谱查询',
  web_search: '网络搜索',
  arknights_structured_query: '结构化查询',
  search_prts: 'PRTS 搜索',
  get_stage_info: '关卡详情',
  get_stage_enemies: '关卡出怪',
  get_enemy_info: '敌人详情',
  list_items: '物品列表',
  get_item_info: '物品详情',
  operator_artwork: '干员立绘',
}

export function isMcpTool(name) {
  return MCP_TOOL_NAMES.has(name)
}

export function getToolIcon(name) {
  return TOOL_ICONS[name] || '🔧'
}

export function getToolDisplayName(name) {
  return TOOL_DISPLAY_NAMES[name] || name
}

export function summarizeMcpToolArgs(toolName, args) {
  if (!args || typeof args !== 'object') return ''
  switch (toolName) {
    case 'search_prts':
      return `搜索: "${args.query || ''}"`
    case 'get_stage_info':
      return `关卡: ${args.stage_id || ''}`
    case 'get_stage_enemies':
      return `出怪: ${args.stage_id || ''}`
    case 'get_enemy_info':
      return `敌人: ${args.name || ''}${args.stage_id ? ` @ ${args.stage_id}` : ''}`
    case 'list_items':
      return `物品: ${args.category || '全部'}`
    case 'get_item_info':
      return `物品: ${args.name || ''}`
    case 'operator_artwork':
      return `${args.operator_name || ''} ${args.action === 'get' ? '获取立绘' : '立绘列表'}`
    default:
      return JSON.stringify(args).substring(0, 80)
  }
}

export function normalizeMcpDisplay(display) {
  if (!display || typeof display !== 'object') {
    return { text: '', table: null, json: '', images: [] }
  }

  const images = Array.isArray(display.images) ? display.images : []
  const structured = display.structured
  let rows = null

  if (Array.isArray(structured) && structured.length > 0 &&
      structured.every((item) => item && typeof item === 'object')) {
    rows = structured
  } else if (structured && typeof structured === 'object' && Array.isArray(structured.rows)) {
    rows = structured.rows
  }

  let table = null
  if (rows && rows.length > 0) {
    const columnSet = []
    for (const row of rows.slice(0, 20)) {
      for (const key of Object.keys(row)) {
        if (!columnSet.includes(key)) columnSet.push(key)
      }
    }
    table = { columns: columnSet.slice(0, 8), rows: rows.slice(0, 50) }
  }

  let json = ''
  if (structured !== null && structured !== undefined) {
    json = JSON.stringify(structured, null, 2)
  }

  return { text: display.text || '', table, json, images }
}
```

- [ ] **Step 4: 运行测试**

Run: `cd frontend; npx vitest run test/toolMeta.test.js`

Expected: 全部 PASS。

- [ ] **Step 5: 提交（先征得用户同意）**

```bash
git add frontend/src/utils/toolMeta.js frontend/test/toolMeta.test.js
git commit -m "feat: 前端新增 MCP 工具元数据纯函数"
```

---

## Task 10: ChatView 渲染 MCP 工具卡片

**Files:**
- Modify: `frontend/src/views/ChatView.vue:158-250, 382-383, 986-1025, 1043-1050, 1401-1410`
- Test: `frontend/test/toolMeta.test.js`（已覆盖纯函数；本任务跑全量前端测试）

- [ ] **Step 1: 修改 import 与模板**

`ChatView.vue` script import 区新增：

```js
import {
  isMcpTool,
  getToolIcon as resolveToolIcon,
  getToolDisplayName as resolveToolDisplayName,
  summarizeMcpToolArgs,
  normalizeMcpDisplay,
} from '../utils/toolMeta'
```

模板中 `arknights_structured_query` 分支之后、通用 `<pre>` fallback 之前插入：

```html
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
```

- [ ] **Step 2: 替换 summarizeToolArgs / getToolIcon / getToolDisplayName**

`summarizeToolArgs` 的 `default` 分支改为：

```js
    default:
      if (isMcpTool(toolName)) return summarizeMcpToolArgs(toolName, args)
      return JSON.stringify(args).substring(0, 80)
```

`getToolIcon` 整个函数替换为：

```js
function getToolIcon(name) {
  return resolveToolIcon(name)
}
```

`getToolDisplayName` 整个函数替换为：

```js
function getToolDisplayName(name) {
  return resolveToolDisplayName(name)
}
```

- [ ] **Step 3: 更新 fallback 快速问题**

`loadQuickQuestionsData` 的 `fallbackActions` 替换为：

```js
    const fallbackActions = [
      { label: '银灰技能', question: '银灰的技能是什么？', type: 'skill', category: 'rag' },
      { label: '陈/史尔特尔', question: '陈和史尔特尔的关系', type: 'relation', category: 'graph' },
      { label: '高攻击近卫', question: '哪些六星近卫的精二满级攻击力大于800？', type: 'structured', category: 'structured' },
      { label: '1-7出怪', question: '1-7关卡的出怪顺序是什么？', type: 'stage', category: 'prts_mcp' }
    ]
```

- [ ] **Step 4: 增加 CSS**

在 `.tool-detail-row-count` 规则后插入：

```css
/* PRTS-MCP results */
.tool-detail-mcp { display: flex; flex-direction: column; gap: var(--spacing-sm); }
.tool-detail-mcp-text { font-size: 0.72rem; color: var(--text-secondary); line-height: 1.5; white-space: pre-wrap; word-break: break-word; }
.tool-detail-mcp-images { display: flex; flex-wrap: wrap; gap: var(--spacing-sm); }
.tool-detail-image-link { display: block; }
.tool-detail-image { max-height: 240px; max-width: 160px; border-radius: var(--radius-sm); border: 1px solid var(--border-color); object-fit: contain; background: var(--bg-deep); }
```

- [ ] **Step 5: 运行测试与构建**

Run:

```powershell
cd frontend
npx vitest run
npm run build
```

Expected: 全部测试 PASS，build 成功。

- [ ] **Step 6: 提交（先征得用户同意）**

```bash
git add frontend/src/views/ChatView.vue
git commit -m "feat: 前端工具卡片支持 MCP 结构化结果与立绘展示"
```

---

## Task 11: 本地 MCP 集成测试

**Files:**
- Create: `test/test_mcp_integration.py`

- [ ] **Step 1: 创建集成测试（CI 自动跳过）**

创建 `test/test_mcp_integration.py`：

```python
"""
Local integration smoke test for prts-mcp. Skipped by default in CI
(PRTS_MCP_ENABLED=false in conftest).
"""
import asyncio
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


@pytest.mark.skipif(
    os.environ.get("PRTS_MCP_ENABLED") != "true",
    reason="PRTS_MCP_ENABLED != true；本测试需要本地 MCP 环境",
)
def test_prts_mcp_connect_and_call():
    from backend.agent.mcp_client import McpClientManager, MCP_ALLOWLIST, extract_mcp_result

    manager = McpClientManager(
        command="prts-mcp",
        env={
            "PRTS_OUTPUT_CHANNEL": "both",
            "LOCAL_IMAGE": "false",
            "PRTS_IMAGE_CACHE": "true",
            "IMAGES_ENABLED": "true",
        },
        connect_timeout=30,
    )

    async def _run():
        await manager.start()
        try:
            names = {getattr(t, "name", "") for t in manager.tools}
            assert MCP_ALLOWLIST.issubset(names)
            raw = await manager.call_tool("list_items", {"limit": 2})
            payload = extract_mcp_result(raw, "list_items")
            assert payload.llm_content
        finally:
            await manager.close()

    asyncio.run(_run())
```

- [ ] **Step 2: CI 模式运行（应跳过）**

Run: `python -m pytest test/test_mcp_integration.py -v`

Expected: `SKIPPED (PRTS_MCP_ENABLED != true...)`

- [ ] **Step 3: 本地开启 MCP 运行集成测试**

Run（PowerShell）：

```powershell
$env:PRTS_MCP_ENABLED = 'true'
python -m pytest test/test_mcp_integration.py -v
Remove-Item Env:PRTS_MCP_ENABLED
```

Expected: PASS（首次运行 prts-mcp 会后台同步数据；若失败先重试一次，仍失败则截图报告，不要修改测试掩盖问题）。

- [ ] **Step 4: 提交（先征得用户同意）**

```bash
git add test/test_mcp_integration.py
git commit -m "test: 新增 prts-mcp 本地集成冒烟测试"
```

---

## Task 12: 生产服务器 Python 3.11 venv 迁移（用户把关，不要自动执行）

**Files:**
- Modify: `.gitignore`（仓库内）
- Modify: `.github/workflows/ci-cd.yml`（仓库内）
- 服务器文件：`/etc/systemd/system/arknights-rag.service`（服务器上修改，不在仓库）

- [ ] **Step 1: .gitignore 忽略 venv**

`.gitignore` 末尾追加：

```
.venv/
```

- [ ] **Step 2: 在服务器安装 uv 并创建 3.11 venv（一次性手动步骤）**

服务器信息从仓库根目录的 `AGENTS.md`（gitignored）读取。用 paramiko 执行以下等价命令：

```bash
# 1) 备份 systemd unit
cp /etc/systemd/system/arknights-rag.service /etc/systemd/system/arknights-rag.service.bak-20260816

# 2) 安装 uv（GitHub 直连已验证可达；失败则用 gh-proxy 前缀重试）
curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="$HOME/.local/bin:$PATH"
uv --version

# 3) 创建项目专属 3.11 环境并安装依赖（主服务保持运行，装完再切）
cd /srv/projects/arknights-rag
uv python install 3.11
uv venv --python 3.11 .venv
uv pip install --python .venv/bin/python -r backend/requirements.txt

# 4) 依赖导入冒烟（旧服务不受影响）
.venv/bin/python -c "import fastapi, mcp, prts_mcp; print('venv deps ok')"

# 5) 切换 systemd 并重启
sed -i 's#ExecStart=/usr/bin/python3 -m uvicorn#ExecStart=/srv/projects/arknights-rag/.venv/bin/python -m uvicorn#' /etc/systemd/system/arknights-rag.service
systemctl daemon-reload
systemctl restart arknights-rag

# 6) 健康检查
for i in $(seq 1 6); do curl -fsS http://localhost:8889/health && break; sleep 5; done
```

Expected：`uv --version` 有输出；`venv deps ok`；`/health` 返回 `{"status":"healthy"}`。

- [ ] **Step 3: 修改 CI/CD deploy job**

`.github/workflows/ci-cd.yml` 第一个 SSH 步骤的 script 中，`git reset --hard origin/master` 之后、`cd frontend` 之前插入：

```bash
            export PATH="$HOME/.local/bin:$PATH"
            if ! command -v uv >/dev/null 2>&1; then
              curl -LsSf https://astral.sh/uv/install.sh | sh
              export PATH="$HOME/.local/bin:$PATH"
            fi
            if [ ! -x .venv/bin/python ]; then
              uv python install 3.11
              uv venv --python 3.11 .venv
            fi
            uv pip install --python .venv/bin/python -r backend/requirements.txt
```

- [ ] **Step 4: 服务器 MCP 状态冒烟**

重启并等 `/health` 通过后：

```bash
curl -fsS http://localhost:8889/status
```

Expected：`mcp.enabled=true`、`mcp.connected=true`、`mcp.tool_count=7`。若 `connected=false`，查看 `/data/arknights-rag/logs/uvicorn.log` 中的 `[MCP]` 日志后修复；不得带病收尾。

- [ ] **Step 5: 提交并请用户决定是否 push**

```bash
git add .gitignore .github/workflows/ci-cd.yml
git commit -m "deploy: 生产使用 uv Python 3.11 venv，并在部署时安装后端依赖"
```

**Push 前必须得到用户明确同意**（push 即触发生产部署）。用户不同意 push 时，任务到此为止。

---

## Task 13: 全量回归与文档同步

**Files:**
- Modify: `README.md`（工具表、环境变量表）
- Modify: `AGENTS.md`、`CLAUDE.md`（gitignored，需同步更新但不提交）
- Test: 后端 + 前端全量

- [ ] **Step 1: 后端全量测试**

Run: `python -m pytest test/ -q`

Expected: 578 个旧测试 + 新增测试全部 PASS，`test_mcp_integration.py` SKIPPED。

- [ ] **Step 2: 前端全量测试与构建**

Run:

```powershell
cd frontend
npx vitest run
npm run build
```

Expected: 全部 PASS，build 成功。

- [ ] **Step 3: 更新 README 工具与依赖说明**

`README.md` 的三工具/四工具描述改为：本地 4 工具 + prts-mcp 7 个白名单工具；环境变量表增加 `PRTS_MCP_ENABLED`；技术栈增加 `prts-mcp` 与 `mcp`。

- [ ] **Step 4: 同步 AGENTS.md / CLAUDE.md**

两个文件同步更新：工具表增加 7 个 MCP 工具、`backend/agent/mcp_client.py` 与 `backend/quick_questions.py` 文件清单、`.env` 表增加 `PRTS_MCP_*`、服务器章节改为 `.venv` 启动。两文件内容保持一致（除首行标识与 GitNexus 部分）。

- [ ] **Step 5: 更新 GitNexus 索引**

Run: `npx gitnexus analyze`

Expected: 索引更新成功。

- [ ] **Step 6: 提交（先征得用户同意）**

```bash
git add README.md
git commit -m "docs: 同步 PRTS-MCP 接入后的工具、依赖与部署说明"
```

---

## Self-Review（计划作者已执行）

- **Spec coverage**：7 工具白名单/通用 Bridge/优雅降级/图片 preview 且不进 LLM/输出通道 both/前端图片卡片/4 类快速问题/测试与部署/服务器 3.11 迁移均有对应 Task。
- **Placeholder scan**：无 TBD/TODO；Task 12 的服务器凭据有意不写入计划，要求从 gitignored 的 AGENTS.md 读取，避免泄露。
- **Type consistency**：`ToolResultPayload.llm_content/display`、`ToolRegistry.register_schema`、`McpClientManager.start/close/call_tool`、`normalizeMcpDisplay` 的字段在前后 Task 一致。
- **依赖顺序**：`prts-mcp`/`mcp` 在 Task 4 Step 0 即安装，早于所有 import 它的测试；Task 11 只保留集成冒烟测试。
- **已知偏差**：`test/test_tools.py` 中 `test_global_registry_has_three_tools` 名称仍为历史名称，但断言已含第 4 个静态工具；不额外改名以免无谓 churn。
