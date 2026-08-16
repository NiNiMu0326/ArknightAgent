"""
Tests for backend.agent.mcp_client: schema conversion, allowlist,
result extraction (text/structured/images).
"""
import sys
import json
import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from unittest.mock import AsyncMock

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
        big = "A" * 1_600_001
        raw = make_call_result(
            content=[SimpleNamespace(type="image", mime_type="image/png", data=big)],
            structured=None,
        )
        payload = extract_mcp_result(raw, "operator_artwork")
        assert payload.display["images"] == []
        assert "图片过大" in payload.llm_content

    def test_only_first_image_kept(self):
        raw = make_call_result(
            content=[
                SimpleNamespace(type="image", mime_type="image/png", data="QUJD"),
                SimpleNamespace(type="image", mime_type="image/png", data="RUZH"),
            ],
            structured=None,
        )
        payload = extract_mcp_result(raw, "operator_artwork")
        assert len(payload.display["images"]) == 1
        assert "多余图片已省略" in payload.llm_content

    def test_oversized_structured_is_truncated_for_display(self):
        raw = make_call_result(
            content=[SimpleNamespace(type="text", text="ok")],
            structured={"x": "长" * 60_000},
        )
        payload = extract_mcp_result(raw, "list_items")
        assert payload.display["structured"]["_truncated"] is True
        assert "长" * 60_000 not in payload.llm_content

    def test_long_text_content_is_truncated_for_llm_and_display(self):
        long_text = "源石" * 60_000
        raw = make_call_result(
            content=[SimpleNamespace(type="text", text=long_text)],
            structured=None,
        )
        payload = extract_mcp_result(raw, "search_prts")
        # LLM 与前端展示都不应携带 12 万字符的原始文本
        assert len(payload.llm_content) < 20_000
        assert len(payload.display["text"]) < 60_000
        assert "已截断" in payload.llm_content
        assert long_text not in payload.llm_content


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
        assert "mcp__get_item_info" in registry.executors
        assert "get_item_info" not in registry.executors
        assert "get_operator_archives" not in registry.executors
        assert registry.schemas[0]["function"]["name"] == "mcp__get_item_info"

    def test_prefixed_executor_calls_mcp_manager_with_original_name(self):
        class FakeManager:
            def __init__(self):
                self.tools = [make_tool("get_item_info", "d", {"type": "object"})]
                self.called_name = None

            async def call_tool(self, name, arguments):
                self.called_name = name
                return make_call_result(
                    content=[SimpleNamespace(type="text", text="ok")],
                    structured=None,
                )

        fake = FakeManager()
        registry = self.FakeRegistry()
        register_mcp_tools(registry, fake)

        executor = registry.executors["mcp__get_item_info"]
        payload = asyncio.run(executor({"name": "源岩"}))
        assert fake.called_name == "get_item_info"
        assert payload.llm_content == "ok"

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

    def test_executor_falls_back_to_preview_when_large_fails(self):
        class FakeManager:
            def __init__(self):
                self.calls = []

            async def call_tool(self, name, arguments):
                self.calls.append(dict(arguments))
                if arguments.get("variant") == "large":
                    return make_call_result(
                        content=[SimpleNamespace(
                            type="text",
                            text="下载图片失败：image exceeds 1048576 byte cap",
                        )],
                        structured=None,
                    )
                return make_call_result(
                    content=[
                        SimpleNamespace(type="text", text="ok preview"),
                        SimpleNamespace(type="image", mime_type="image/png", data="QUJD"),
                    ],
                    structured=None,
                )

        fake = FakeManager()
        executor = make_mcp_executor(fake, "operator_artwork")
        payload = asyncio.run(executor({
            "action": "get",
            "operator_name": "陈",
            "artwork_id": "立绘_陈_2.png",
            "variant": "large",
        }))
        assert [c["variant"] for c in fake.calls] == ["large", "preview"]
        assert payload.display["images"][0]["data_url"] == "data:image/png;base64,QUJD"

    def test_executor_falls_back_to_preview_when_large_raises(self):
        class FakeManager:
            def __init__(self):
                self.calls = []

            async def call_tool(self, name, arguments):
                self.calls.append(dict(arguments))
                if arguments.get("variant") == "large":
                    raise RuntimeError("image exceeds 1048576 byte cap")
                return make_call_result(
                    content=[
                        SimpleNamespace(type="text", text="ok preview"),
                        SimpleNamespace(type="image", mime_type="image/png", data="QUJD"),
                    ],
                    structured=None,
                )

        fake = FakeManager()
        executor = make_mcp_executor(fake, "operator_artwork")
        payload = asyncio.run(executor({
            "action": "get",
            "operator_name": "陈",
            "artwork_id": "立绘_陈_2.png",
            "variant": "large",
        }))
        assert [c["variant"] for c in fake.calls] == ["large", "preview"]
        assert payload.display["images"][0]["data_url"] == "data:image/png;base64,QUJD"


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


class _FakeCM:
    def __init__(self, enter_result=None, enter_error=None, exit_order=None):
        self.enter_result = enter_result
        self.enter_error = enter_error
        self.exit_order = exit_order
        self.entered = 0
        self.exited = 0

    async def __aenter__(self):
        self.entered += 1
        if self.enter_error is not None:
            raise self.enter_error
        return self.enter_result

    async def __aexit__(self, *exc_info):
        self.exited += 1
        if self.exit_order is not None:
            self.exit_order.append(self)


class _FakeSession:
    def __init__(self, tools=None):
        self.tools = tools or []
        self.initialize = AsyncMock()
        self.list_tools = AsyncMock(return_value=SimpleNamespace(tools=self.tools))
        self.call_tool = AsyncMock()


class TestMcpClientManagerLifecycle:
    def _manager(self, **kwargs):
        from backend.agent.mcp_client import McpClientManager
        return McpClientManager(command="fake-prts-mcp", **kwargs)

    def test_spawn_failure_sets_last_error_and_closes_transport(self, monkeypatch):
        from backend.agent import mcp_client
        transport = _FakeCM(enter_error=RuntimeError("spawn boom"))
        monkeypatch.setattr(mcp_client, "stdio_client", lambda params: transport)

        manager = self._manager(connect_timeout=2)

        async def _test():
            with pytest.raises(RuntimeError, match="子进程启动失败"):
                await manager.start()
            assert "spawn boom" in manager.last_error
            assert manager.connected is False
            assert transport.exited == 1

        asyncio.run(_test())

    def test_session_failure_closes_session_then_transport(self, monkeypatch):
        from backend.agent import mcp_client
        order = []
        transport = _FakeCM(enter_result=("read", "write"), exit_order=order)
        session_cm = _FakeCM(enter_error=RuntimeError("session boom"), exit_order=order)
        monkeypatch.setattr(mcp_client, "stdio_client", lambda params: transport)
        monkeypatch.setattr(mcp_client, "ClientSession", lambda read, write: session_cm)

        manager = self._manager(connect_timeout=2)

        async def _test():
            with pytest.raises(RuntimeError, match="会话初始化失败"):
                await manager.start()
            assert order == [session_cm, transport]
            assert "session boom" in manager.last_error

        asyncio.run(_test())

    def test_start_close_double_close_lifecycle(self, monkeypatch):
        from backend.agent import mcp_client
        order = []
        transport = _FakeCM(enter_result=("read", "write"), exit_order=order)
        session = _FakeSession(tools=[SimpleNamespace(name="get_item_info")])
        session_cm = _FakeCM(enter_result=session, exit_order=order)
        monkeypatch.setattr(mcp_client, "stdio_client", lambda params: transport)
        monkeypatch.setattr(mcp_client, "ClientSession", lambda read, write: session_cm)

        manager = self._manager()

        async def _test():
            await manager.start()
            assert manager.connected is True
            assert manager.last_error == ""
            assert [getattr(t, "name", "") for t in manager.tools] == ["get_item_info"]
            await manager.close()
            assert manager.connected is False
            assert order == [session_cm, transport]
            await manager.close()
            assert session_cm.exited == 1
            assert transport.exited == 1

        asyncio.run(_test())

    def test_call_tool_timeout_raises_and_records_error(self):
        manager = self._manager(call_timeout=0.05)
        session = _FakeSession()
        async def slow_call(name, arguments):
            await asyncio.sleep(1)
            return SimpleNamespace(content=[], structured_content=None)
        session.call_tool.side_effect = slow_call
        manager._session = session
        manager.connected = True

        async def _test():
            with pytest.raises(RuntimeError, match="调用超时"):
                await manager.call_tool("get_item_info", {"name": "源岩"})
            assert "调用超时" in manager.last_error

        asyncio.run(_test())

    def test_concurrent_start_opens_transport_once(self, monkeypatch):
        from backend.agent import mcp_client
        counters = {"transport": 0, "session": 0}
        transport = _FakeCM(enter_result=("read", "write"))
        session_cm = _FakeCM(enter_result=_FakeSession(tools=[SimpleNamespace(name="t")]))

        def make_transport(params):
            counters["transport"] += 1
            return transport

        def make_session(read, write):
            counters["session"] += 1
            return session_cm

        monkeypatch.setattr(mcp_client, "stdio_client", make_transport)
        monkeypatch.setattr(mcp_client, "ClientSession", make_session)
        manager = self._manager()

        async def _test():
            await asyncio.gather(manager.start(), manager.start())
            assert counters == {"transport": 1, "session": 1}
            await manager.close()

        asyncio.run(_test())

    def test_close_during_start_waits_then_cleans(self, monkeypatch):
        from backend.agent import mcp_client
        init_started = asyncio.Event()
        init_release = asyncio.Event()
        session = _FakeSession(tools=[SimpleNamespace(name="t")])

        async def slow_init():
            init_started.set()
            await init_release.wait()

        session.initialize = AsyncMock(side_effect=slow_init)
        transport = _FakeCM(enter_result=("read", "write"))
        session_cm = _FakeCM(enter_result=session)
        monkeypatch.setattr(mcp_client, "stdio_client", lambda params: transport)
        monkeypatch.setattr(mcp_client, "ClientSession", lambda read, write: session_cm)
        manager = self._manager()

        async def _test():
            start_task = asyncio.create_task(manager.start())
            await asyncio.wait_for(init_started.wait(), timeout=2)
            close_task = asyncio.create_task(manager.close())
            await asyncio.sleep(0.05)
            assert not close_task.done(), "close 应等待 start 完成"
            init_release.set()
            await asyncio.gather(start_task, close_task)
            assert manager.connected is False
            assert session_cm.exited == 1
            assert transport.exited == 1

        asyncio.run(_test())

    def test_close_waits_for_inflight_call(self):
        call_started = asyncio.Event()
        call_release = asyncio.Event()
        session = _FakeSession()

        async def slow_call(name, arguments):
            call_started.set()
            await call_release.wait()
            return SimpleNamespace(content=[], structured_content=None)

        session.call_tool.side_effect = slow_call
        manager = self._manager(call_timeout=5)
        manager._session = session
        manager.connected = True

        async def _test():
            call_task = asyncio.create_task(manager.call_tool("get_item_info", {"name": "x"}))
            await asyncio.wait_for(call_started.wait(), timeout=2)
            close_task = asyncio.create_task(manager.close())
            await asyncio.sleep(0.05)
            assert not close_task.done(), "close 应等待 in-flight call"
            call_release.set()
            await call_task
            await close_task
            assert manager.connected is False

        asyncio.run(_test())

    def test_start_cancellation_cleans_up_partial_session(self, monkeypatch):
        from backend.agent import mcp_client
        init_started = asyncio.Event()
        session = _FakeSession()

        async def never_finish_init():
            init_started.set()
            await asyncio.sleep(10)

        session.initialize = AsyncMock(side_effect=never_finish_init)
        transport = _FakeCM(enter_result=("read", "write"))
        session_cm = _FakeCM(enter_result=session)
        monkeypatch.setattr(mcp_client, "stdio_client", lambda params: transport)
        monkeypatch.setattr(mcp_client, "ClientSession", lambda read, write: session_cm)
        manager = self._manager()

        async def _test():
            start_task = asyncio.create_task(manager.start())
            await asyncio.wait_for(init_started.wait(), timeout=2)
            start_task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await start_task
            assert session_cm.exited == 1
            assert transport.exited == 1
            assert manager.connected is False

        asyncio.run(_test())
