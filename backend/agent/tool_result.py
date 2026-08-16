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
