"""
Tests for backend.agent.tools: ToolRegistry and tool schemas.
Usage: cd test && python -m pytest test_tools.py -v
"""
import sys
import time
import pytest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from backend.agent.tools import (
    TOOL_SCHEMAS,
    ToolRegistry,
    get_tool_registry,
    _register_default_tools,
)


# ============================================================
# TOOL_SCHEMAS validation
# ============================================================

class TestToolSchemas:
    """Verify the structure and content of tool schema definitions."""

    def test_tools_registered(self):
        """There should be exactly 5 tool schemas (rag, graphrag, web, stage_waves, structured)."""
        assert len(TOOL_SCHEMAS) == 5

    def test_stage_waves_schema(self):
        names = [s["function"]["name"] for s in TOOL_SCHEMAS]
        assert "arknights_stage_waves" in names
        schema = next(s for s in TOOL_SCHEMAS if s["function"]["name"] == "arknights_stage_waves")
        params = schema["function"]["parameters"]
        assert "stage_code" in params["properties"]
        assert params["required"] == ["stage_code"]

    def test_rag_search_schema(self):
        """arknights_rag_search schema should have correct structure."""
        schema = TOOL_SCHEMAS[0]
        assert schema["type"] == "function"
        fn = schema["function"]
        assert fn["name"] == "arknights_rag_search"
        assert "description" in fn
        params = fn["parameters"]
        assert params["type"] == "object"
        assert "query" in params["properties"]
        assert "top_k" in params["properties"]
        assert params["required"] == ["query"]
        assert params["additionalProperties"] is False

    def test_graphrag_search_schema(self):
        """arknights_graphrag_search schema should have correct structure."""
        schema = TOOL_SCHEMAS[1]
        assert schema["type"] == "function"
        fn = schema["function"]
        assert fn["name"] == "arknights_graphrag_search"
        assert "description" in fn
        params = fn["parameters"]
        assert params["type"] == "object"
        assert "entity" in params["properties"]
        assert "entity1" in params["properties"]
        assert "entity2" in params["properties"]
        assert "required" not in params  # all optional
        assert params["additionalProperties"] is False

    def test_web_search_schema(self):
        """web_search schema should have correct structure."""
        schema = TOOL_SCHEMAS[2]
        assert schema["type"] == "function"
        fn = schema["function"]
        assert fn["name"] == "web_search"
        assert "description" in fn
        params = fn["parameters"]
        assert params["type"] == "object"
        assert "query" in params["properties"]
        assert params["required"] == ["query"]
        assert params["additionalProperties"] is False

    def test_all_schemas_have_unique_names(self):
        """All tool schema names should be unique."""
        names = [s["function"]["name"] for s in TOOL_SCHEMAS]
        assert len(names) == len(set(names))

    def test_all_schemas_have_descriptions(self):
        """All tool schemas should have non-empty descriptions."""
        for s in TOOL_SCHEMAS:
            desc = s["function"]["description"]
            assert desc, f"Tool {s['function']['name']} has empty description"


# ============================================================
# ToolRegistry unit tests
# ============================================================

class TestToolRegistry:
    """Test ToolRegistry registration and execution."""

    def test_register_and_get_schemas(self):
        registry = ToolRegistry()
        schemas = registry.get_schemas()
        assert schemas is not TOOL_SCHEMAS  # 返回新列表，不暴露静态列表引用
        assert schemas == TOOL_SCHEMAS  # 空 registry 时无动态 schema

    def test_register_and_execute(self):
        registry = ToolRegistry()

        async def fake_tool(args, session_id=""):
            return {"result": "ok", "input": args}

        registry.register("my_tool", fake_tool)

    def test_register_and_execute_async(self):
        registry = ToolRegistry()
        results = []

        async def fake_tool(args, session_id=""):
            results.append((args, session_id))
            return "done"

        registry.register("test_tool", fake_tool)

    def test_execute_unknown_tool(self):
        registry = ToolRegistry()

        async def _test():
            with pytest.raises(ValueError, match="Unknown tool: nonexistent"):
                await registry.execute("nonexistent", {})

        import asyncio
        asyncio.run(_test())

    def test_execute_success(self):
        registry = ToolRegistry()

        async def echo(args, session_id=""):
            return args

        registry.register("echo", echo)

        async def _test():
            result = await registry.execute("echo", {"key": "value"}, session_id="s1")
            assert result == {"key": "value"}

        import asyncio
        asyncio.run(_test())

    def test_execute_passes_session_id(self):
        registry = ToolRegistry()
        captured = []

        async def tracker(args, session_id=""):
            captured.append(session_id)
            return args

        registry.register("tracker", tracker)

        async def _test():
            await registry.execute("tracker", {}, session_id="my-session-1")
            assert captured == ["my-session-1"]

        import asyncio
        asyncio.run(_test())

    def test_multiple_registrations(self):
        registry = ToolRegistry()

        async def fn1(args, session_id=""):
            return 1

        async def fn2(args, session_id=""):
            return 2

        registry.register("a", fn1)
        registry.register("b", fn2)

        async def _test():
            assert await registry.execute("a", {}) == 1
            assert await registry.execute("b", {}) == 2

        import asyncio
        asyncio.run(_test())

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

    def test_register_schema_rejects_malformed_schema(self):
        registry = ToolRegistry()
        with pytest.raises(TypeError):
            registry.register_schema(None)
        with pytest.raises(TypeError):
            registry.register_schema({"function": "not-a-dict"})
        with pytest.raises(ValueError):
            registry.register_schema({"function": {"name": ""}})


# ============================================================
# Global registry (get_tool_registry)
# ============================================================

class TestGlobalRegistry:
    """Test the global singleton registry."""

    def test_global_registry_is_singleton(self):
        r1 = get_tool_registry()
        r2 = get_tool_registry()
        assert r1 is r2

    def test_global_registry_has_three_tools(self):
        registry = get_tool_registry()
        # Can execute all three tools (they'll fail at import but schema exists)
        schemas = registry.get_schemas()
        names = [s["function"]["name"] for s in schemas]
        assert "arknights_rag_search" in names
        assert "arknights_graphrag_search" in names
        assert "web_search" in names

    def test_register_default_tools(self):
        """Test _register_default_tools populates registry correctly."""
        registry = ToolRegistry()
        _register_default_tools(registry)
        # The tools should have been registered (they point to real implementations)
        assert registry._tools.get("arknights_rag_search") is not None
        assert registry._tools.get("arknights_graphrag_search") is not None
        assert registry._tools.get("web_search") is not None


# ============================================================
# ToolRegistry edge cases
# ============================================================

class TestToolRegistryEdgeCases:
    """Boundary and error handling tests for ToolRegistry."""

    def test_execute_empty_args(self):
        registry = ToolRegistry()

        async def fn(args, session_id=""):
            return args

        registry.register("fn", fn)

        async def _test():
            result = await registry.execute("fn", {}, session_id="")
            assert result == {}

        import asyncio
        asyncio.run(_test())

    def test_register_overwrite(self):
        """Registering the same name twice should overwrite."""
        registry = ToolRegistry()

        async def fn1(args, session_id=""):
            return 1

        async def fn2(args, session_id=""):
            return 2

        registry.register("x", fn1)
        registry.register("x", fn2)

        async def _test():
            assert await registry.execute("x", {}) == 2

        import asyncio
        asyncio.run(_test())

    def test_executor_exception_propagation(self):
        """Exceptions from executors should propagate."""
        registry = ToolRegistry()

        async def broken(args, session_id=""):
            raise RuntimeError("tool error")

        registry.register("broken", broken)

        async def _test():
            with pytest.raises(RuntimeError, match="tool error"):
                await registry.execute("broken", {})

        import asyncio
        asyncio.run(_test())


# ============================================================
# T27-①: 并发首次调用 get_tool_registry()
# ============================================================

class TestGlobalRegistryConcurrency:
    """防的回归：旧实现先 `_registry = ToolRegistry()` 再注册默认工具，
    并发首次调用时其他线程会看到「非 None 但零 executor」的半成品，
    execute() 直接抛 Unknown tool —— 表现为服务启动初期随机丢工具。
    """

    REQUIRED_TOOLS = (
        "arknights_rag_search",
        "arknights_graphrag_search",
        "web_search",
        "arknights_structured_query",
        "arknights_stage_waves",
    )

    def test_concurrent_first_call_returns_one_fully_initialized_registry(self, monkeypatch):
        import asyncio
        import threading

        import backend.agent.tools as tools_module

        # 强制走「首次初始化」路径，并把初始化窗口拉长，保证并发线程必然撞上
        monkeypatch.setattr(tools_module, "_registry", None)
        real_register = tools_module._register_default_tools

        def slow_register(registry):
            time.sleep(0.2)
            real_register(registry)

        monkeypatch.setattr(tools_module, "_register_default_tools", slow_register)

        results, errors = [], []
        barrier = threading.Barrier(8, timeout=10)

        def worker():
            try:
                barrier.wait()
                registry = get_tool_registry()
                # 拿到实例后「立刻」自检：半成品只在初始化窗口内可见，
                # 等所有线程 join 完再看 _tools 已经太晚（注册表是同一个可变对象）
                missing = tuple(n for n in self.REQUIRED_TOOLS if registry._tools.get(n) is None)
                results.append((registry, missing))
            except Exception as exc:      # noqa: BLE001 - 线程内异常带回主线程断言
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        assert errors == []
        assert len(results) == 8
        assert [missing for _, missing in results] == [()] * 8, \
            f"并发首次调用拿到了半成品注册表（缺 executor）：{results}"
        registries = [r for r, _ in results]
        assert len({id(r) for r in registries}) == 1, "并发首次调用拿到了不同的注册表实例"

        registry = registries[0]
        for name in self.REQUIRED_TOOLS:
            assert registry._tools.get(name) is not None, f"半成品注册表：缺少 executor {name}"

        # 拿到的实例必须真的能执行（而不是只有 _tools 字典恰好非空）
        result = asyncio.run(registry.execute("arknights_structured_query", {"sql": ""}))
        assert isinstance(result, dict)


# ============================================================
# T27-②③: register_schema / get_schemas 的内部状态隔离
# ============================================================

class TestToolRegistryIsolation:
    def test_register_schema_rejects_builtin_tool_name(self):
        """防的回归：MCP 动态 schema 与内置工具重名后，get_schemas() 会返回两个
        同名 function，DeepSeek/OpenAI 接口直接 400，整轮对话不可用。"""
        registry = ToolRegistry()
        schema = {
            "type": "function",
            "function": {
                "name": "arknights_rag_search",
                "description": "冒名顶替",
                "parameters": {"type": "object", "properties": {}},
            },
        }

        with pytest.raises(ValueError, match="重名"):
            registry.register_schema(schema)

        # 拒绝后内部状态必须完全不变（不能留下半个 schema）
        assert registry._dynamic_schemas == []
        assert len(registry.get_schemas()) == len(TOOL_SCHEMAS)

    @pytest.mark.parametrize("builtin_name", [s["function"]["name"] for s in TOOL_SCHEMAS])
    def test_register_schema_rejects_every_builtin_name(self, builtin_name):
        registry = ToolRegistry()
        with pytest.raises(ValueError):
            registry.register_schema({"function": {"name": builtin_name}})

    def test_get_schemas_returns_deep_copy(self):
        """防的回归：调用方就地改写返回值（补 id、规范化 parameters）会污染模块级
        TOOL_SCHEMAS，后续所有请求都带上被改坏的 schema。"""
        registry = ToolRegistry()
        registry.register_schema({
            "type": "function",
            "function": {"name": "mcp__extra", "description": "original",
                         "parameters": {"type": "object", "properties": {}}},
        })

        schemas = registry.get_schemas()
        schemas[0]["function"]["name"] = "hacked"
        schemas[0]["function"]["parameters"]["properties"]["query"] = {"type": "integer"}
        schemas[-1]["function"]["description"] = "hacked"
        schemas.append({"type": "function", "function": {"name": "injected"}})

        fresh = registry.get_schemas()
        assert fresh[0]["function"]["name"] == "arknights_rag_search"
        assert fresh[0]["function"]["parameters"]["properties"]["query"]["type"] == "string"
        assert [s["function"]["name"] for s in fresh if s["function"]["name"] == "mcp__extra"]
        assert fresh[-1]["function"]["description"] == "original"
        assert len(fresh) == len(TOOL_SCHEMAS) + 1
        # 模块级静态 schema 也未被污染
        assert TOOL_SCHEMAS[0]["function"]["name"] == "arknights_rag_search"
        assert "query" in TOOL_SCHEMAS[0]["function"]["parameters"]["properties"]
        assert TOOL_SCHEMAS[0]["function"]["parameters"]["properties"]["query"]["type"] == "string"
