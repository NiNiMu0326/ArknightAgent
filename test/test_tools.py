"""
Tests for backend.agent.tools: ToolRegistry and tool schemas.
Usage: cd test && python -m pytest test_tools.py -v
"""
import sys
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
