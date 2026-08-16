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

# 注册到 LLM/ToolRegistry 时统一加前缀，隔离外部 MCP 工具与本地工具命名空间；
# MCP 子进程调用仍使用原始名称，因为 prts-mcp 不感知此前缀。
MCP_TOOL_PREFIX = "mcp__"


def prefixed_mcp_tool_name(name: str) -> str:
    return MCP_TOOL_PREFIX + name

LLM_RESULT_MAX_CHARS = 12_000
DISPLAY_RESULT_MAX_CHARS = 50_000
# large=1024px 立绘 base64 实测约 600KB；original 可能数 MB，仍会丢弃。
MAX_IMAGE_B64_CHARS = 1_600_000


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

    structured = _attr(call_result, "structured_content", None)
    image_label = tool_name
    if isinstance(structured, dict) and structured.get("label"):
        image_label = str(structured["label"])

    for item in content:
        item_type = _attr(item, "type", None)
        if item_type == "text":
            text_parts.append(_attr(item, "text", "") or "")
        elif item_type == "image":
            data = _attr(item, "data", "") or ""
            mime = _attr(item, "mime_type", "") or "image/png"
            if len(images) >= 1:
                text_parts.append("[多余图片已省略]")
            elif data and len(data) <= MAX_IMAGE_B64_CHARS:
                images.append({
                    "label": image_label,
                    "mime": mime,
                    "data_url": f"data:{mime};base64,{data}",
                })
            else:
                text_parts.append(f"[图片过大已省略，base64 长度 {len(data)}]")

    raw_text = "\n\n".join(part for part in text_parts if part)
    text = _truncate_text(raw_text, DISPLAY_RESULT_MAX_CHARS)
    display_structured = (
        structured if structured is None
        else _truncate_json_for_display(structured, DISPLAY_RESULT_MAX_CHARS)
    )

    llm_parts = [_truncate_text(raw_text, LLM_RESULT_MAX_CHARS) or "(工具返回空文本)"]
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
            # prts-mcp 对大图也可能直接抛协议异常（不返回文本错误），
            # 与文本错误路径一致：非 preview 时自动降级 preview 重试一次。
            if (
                tool_name == "operator_artwork"
                and call_args.get("action") == "get"
                and call_args.get("variant") != "preview"
            ):
                logger.info(
                    f"[MCP] operator_artwork variant={call_args.get('variant')} 调用异常，降级 preview 重试"
                )
                call_args["variant"] = "preview"
                try:
                    raw = await manager.call_tool(tool_name, call_args)
                    retry_payload = extract_mcp_result(raw, tool_name)
                    if retry_payload.display.get("images"):
                        return retry_payload
                except Exception as retry_exc:
                    logger.warning(f"[MCP] operator_artwork preview 重试失败: {retry_exc}")
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

        payload = extract_mcp_result(raw, tool_name)

        # prts-mcp 下载 large/original 时可能因单图超过 1MB 上限失败；
        # 自动降级为 preview 重试一次，保证用户始终能看到图。
        if (
            tool_name == "operator_artwork"
            and call_args.get("action") == "get"
            and call_args.get("variant") != "preview"
            and not payload.display.get("images")
        ):
            text = payload.llm_content or ""
            if any(hint in text for hint in ("下载图片失败", "exceeds", "图片过大")):
                logger.info(
                    f"[MCP] operator_artwork variant={call_args.get('variant')} 失败，降级 preview 重试"
                )
                call_args["variant"] = "preview"
                try:
                    raw = await manager.call_tool(tool_name, call_args)
                    retry_payload = extract_mcp_result(raw, tool_name)
                    if retry_payload.display.get("images"):
                        return retry_payload
                except Exception as exc:
                    logger.warning(f"[MCP] operator_artwork preview 重试失败: {exc}")

        return payload

    return _execute


def register_mcp_tools(registry, manager: "McpClientManager") -> int:
    """Register allowlisted MCP tools (schemas + executors) into a ToolRegistry.

    LLM-facing names are prefixed with ``mcp__`` so external MCP tools can never
    collide with local tools, while the MCP subprocess is still called with the
    original name (``manager.call_tool`` receives the unprefixed tool name).
    """
    registered = 0
    for tool in manager.tools:
        name = _attr(tool, "name", "")
        if name not in MCP_ALLOWLIST:
            continue
        registered_name = prefixed_mcp_tool_name(name)
        # convert_mcp_tool_to_openai_schema still sees the original name so
        # operator_artwork's description/property mutations stay keyed correctly.
        schema = convert_mcp_tool_to_openai_schema(tool)
        schema["function"]["name"] = registered_name
        registry.register_schema(schema)
        registry.register(registered_name, make_mcp_executor(manager, name))
        registered += 1
    logger.info(f"[MCP] registered {registered} prts-mcp tools")
    return registered


def _describe_error(exc: Exception, prefix: str, timeout: Optional[float] = None) -> str:
    """Build a readable error message, handling empty asyncio.TimeoutError text."""
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        detail = f"连接超时（{timeout}s）" if timeout is not None else "连接超时"
    else:
        detail = str(exc) or type(exc).__name__
    return f"{prefix}: {detail}"


class McpClientManager:
    """Long-lived MCP stdio client with bounded startup and tool calls."""

    def __init__(
        self,
        command: str,
        env: Optional[Dict[str, str]] = None,
        connect_timeout: float = 10.0,
        call_timeout: float = 60.0,
    ):
        self.command = command
        self.env = env or {}
        self.connect_timeout = connect_timeout
        self.call_timeout = call_timeout
        self.connected = False
        self.tools: List[Any] = []
        self.last_error = ""
        self._params = StdioServerParameters(command=command, env=self.env)
        self._transport_cm = None
        self._session_cm = None
        self._session = None
        self._start_lock = asyncio.Lock()
        self._call_lock = asyncio.Lock()

    async def _safe_exit(self, cm) -> None:
        try:
            await cm.__aexit__(None, None, None)
        except Exception as exc:
            logger.warning(f"[MCP] cleanup failed: {exc}")

    async def _cleanup_after_failed_start(self, transport_cm, session_cm) -> None:
        """Clean partial start state even under cancellation (shielded)."""
        cleanup = []
        if session_cm is not None:
            cleanup.append(self._safe_exit(session_cm))
        cleanup.append(self._safe_exit(transport_cm))
        await asyncio.gather(*(asyncio.shield(coro) for coro in cleanup), return_exceptions=True)

    async def _teardown(self) -> None:
        if self._session_cm is not None:
            await self._safe_exit(self._session_cm)
            self._session_cm = None
        if self._transport_cm is not None:
            await self._safe_exit(self._transport_cm)
            self._transport_cm = None
        self._session = None
        self.tools = []
        self.connected = False

    async def start(self) -> None:
        async with self._start_lock:
            if self.connected:
                return

            transport_cm = stdio_client(self._params)
            session_cm = None
            try:
                try:
                    read_stream, write_stream = await asyncio.wait_for(
                        transport_cm.__aenter__(), timeout=self.connect_timeout
                    )
                except Exception as exc:
                    self.last_error = _describe_error(
                        exc, "MCP 子进程启动失败", self.connect_timeout
                    )
                    raise RuntimeError(self.last_error) from exc

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
                    self.last_error = _describe_error(
                        exc, "MCP 会话初始化失败", self.connect_timeout
                    )
                    raise RuntimeError(self.last_error) from exc

                self._transport_cm = transport_cm
                self._session_cm = session_cm
                self._session = session
                self.tools = list(_attr(tools_result, "tools", []) or [])
                self.connected = True
                self.last_error = ""
                logger.info(f"[MCP] connected to {self.command}, {len(self.tools)} tools discovered")
            except BaseException:
                if not self.connected:
                    await self._cleanup_after_failed_start(transport_cm, session_cm)
                raise

    async def close(self) -> None:
        async with self._start_lock:
            async with self._call_lock:
                await self._teardown()

    async def call_tool(self, name: str, arguments: Dict[str, Any]) -> Any:
        async with self._call_lock:
            if not self.connected or self._session is None:
                self.last_error = f"MCP 客户端未连接，无法调用 {name}"
                raise RuntimeError(self.last_error)
            try:
                return await asyncio.wait_for(
                    self._session.call_tool(name, arguments or {}),
                    timeout=self.call_timeout,
                )
            except asyncio.TimeoutError as exc:
                self.last_error = f"MCP 工具调用超时（{self.call_timeout}s）: {name}"
                raise RuntimeError(self.last_error) from exc
            except Exception as exc:
                self.last_error = f"MCP 工具调用失败: {exc}"
                raise
