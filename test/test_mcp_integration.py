"""
Local integration smoke test for prts-mcp. Skipped by default; run with
PRTS_MCP_INTEGRATION=1 (requires prts-mcp installed and network available).
"""
import asyncio
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


@pytest.mark.skipif(
    os.environ.get("PRTS_MCP_INTEGRATION") != "1",
    reason="PRTS_MCP_INTEGRATION != 1；本测试需要本地 MCP 环境",
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
