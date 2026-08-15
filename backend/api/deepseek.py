"""
DeepSeek Official API client for LLM chat.
Uses DeepSeek's official API instead of SiliconFlow for better response speed.
"""

import json
import logging
import re as _re
from typing import List, Dict, Any

from backend import config

# Event types yielded by chat_with_tools_stream
STREAM_EVENT_THINKING_DELTA = "thinking_delta"
STREAM_EVENT_CONTENT_DELTA = "content_delta"
STREAM_EVENT_TOOL_CALLS = "tool_calls"
STREAM_EVENT_DONE = "done"

# Chunk size for streaming: split large text into small pieces (~token-level)
# so frontend renders smoothly instead of getting one big block.
STREAM_CHUNK_SIZE = 8

logger = logging.getLogger(__name__)


class ToolCall:
    """Represents a single tool call from the model."""
    def __init__(self, id: str, name: str, arguments: str):
        self.id = id
        self.name = name
        self.arguments = arguments  # JSON string

    def to_dict(self) -> Dict:
        return {
            "id": self.id,
            "type": "function",
            "function": {
                "name": self.name,
                "arguments": self.arguments,
            }
        }


# ===== Think-tag stream parser =====
# Some models embed reasoning inside <think...>...</think> tags
# within the content field. This parser separates those tags from real content
# in a streaming fashion, emitting each fragment as (type, text).

_THINK_OPEN_RE = _re.compile(r'<think[^>]*>', _re.IGNORECASE)
_THINK_CLOSE_RE = _re.compile(r'</think\s*>', _re.IGNORECASE)
_THINK_SELFCLOSE_RE = _re.compile(r'<think\s*/>', _re.IGNORECASE)


class ThinkTagParser:
    """Streaming parser that separates <think>...</think> blocks from plain content."""

    def __init__(self):
        self._buf = ""
        self._in_tag = False

    def feed(self, chunk: str):
        """Feed a chunk; yields ('think', text) or ('content', text) fragments."""
        self._buf += chunk
        while self._buf:
            if self._in_tag:
                m = _THINK_CLOSE_RE.search(self._buf)
                if m is not None:
                    if m.start() > 0:
                        yield ('think', self._buf[:m.start()])
                    self._buf = self._buf[m.end():]
                    self._in_tag = False
                else:
                    # No closing tag yet; hold back suffix in case </think is split
                    keep = _partial_suffix_len(self._buf, '</think', 7)
                    if keep > 0:
                        if len(self._buf) > keep:
                            yield ('think', self._buf[:-keep])
                        self._buf = self._buf[-keep:]
                    else:
                        yield ('think', self._buf)
                        self._buf = ""
                    break
            else:
                # Check for self-closing <think/> first
                sm = _THINK_SELFCLOSE_RE.search(self._buf)
                if sm is not None:
                    if sm.start() > 0:
                        yield ('content', self._buf[:sm.start()])
                    self._buf = self._buf[sm.end():]
                    continue

                om = _THINK_OPEN_RE.search(self._buf)
                if om is not None:
                    if om.start() > 0:
                        yield ('content', self._buf[:om.start()])
                    self._buf = self._buf[om.end():]
                    self._in_tag = True
                else:
                    keep = _partial_suffix_len(self._buf, '<think', 6)
                    if keep > 0:
                        if len(self._buf) > keep:
                            yield ('content', self._buf[:-keep])
                        self._buf = self._buf[-keep:]
                    else:
                        yield ('content', self._buf)
                        self._buf = ""
                    break

    def flush(self):
        """Flush remaining buffer after stream ends."""
        if self._buf:
            yield ('think' if self._in_tag else 'content', self._buf)
            self._buf = ""


def _partial_suffix_len(s: str, prefix: str, max_len: int) -> int:
    """If s ends with a prefix of `prefix`, return its length; else 0."""
    for n in range(min(len(s), max_len), 0, -1):
        if prefix[:n] == s[-n:]:
            return n
    return 0


class DeepSeekClient:
    """Client for DeepSeek official API with connection pooling and retry logic."""

    def __init__(self, api_key: str = None, base_url: str = None, model: str = None):
        """
        Initialize DeepSeek client.

        Args:
            api_key: DeepSeek API key. If not provided, loads from config.DEEPSEEK_API_KEY.
            base_url: API base URL. If not provided, loads from config.DEEPSEEK_BASE_URL.
            model: Model name. If not provided, loads from config.DEEPSEEK_LLM_MODEL.
        """
        self.api_key = api_key or config.DEEPSEEK_API_KEY
        if not self.api_key:
            raise ValueError("DeepSeek API key must be provided or set in DEEPSEEK_API_KEY environment variable.")
        self.base_url = base_url or config.DEEPSEEK_BASE_URL
        self.model = model or config.DEEPSEEK_LLM_MODEL
        self.disable_thinking = False  # Set True for models where deep thinking is overkill

    async def chat_with_tools_stream(
        self,
        messages: List[Dict[str, Any]],
        tools: List[Dict] = None,
        model: str = None,
        temperature: float = 0.3,
        **kwargs,
    ):
        """Stream chat completion with tool support.

        Yields event dicts:
          {"type": "thinking_delta", "content": "..."}   — reasoning content chunk
          {"type": "content_delta", "delta": "..."}      — answer content chunk
          {"type": "tool_calls", "tool_calls": [...], "content": "...", "reasoning_content": "..."}
          {"type": "done", "content": "...", "reasoning_content": "...", "finish_reason": "..."}

        This uses httpx with streaming so each SSE chunk is parsed immediately.
        """
        import httpx

        model = model or self.model
        url = f"{self.base_url}/chat/completions"

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }

        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "stream": True,
        }

        if tools:
            payload["tools"] = tools

        if self.disable_thinking:
            payload["thinking"] = {"type": "disabled"}

        payload.update(kwargs)

        logger.info(f"[API STREAM CALL] POST {url} model={model} messages={len(messages)} tools={len(tools) if tools else 0}")

        # Accumulators
        reasoning_content_parts = []
        content_parts = []
        tool_calls_map = {}  # index -> {id, name, arguments_str}
        finish_reason = ""
        usage_info = None  # token usage from final chunk

        # Streaming parser for <think/> tags embedded in content
        tag_parser = ThinkTagParser()

        async with httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=10.0), proxy=None, trust_env=False) as client:
            async with client.stream("POST", url, headers=headers, json=payload) as resp:
                if resp.status_code != 200:
                    body = await resp.aread()
                    try:
                        err_data = json.loads(body)
                        err_msg = err_data.get("error", {}).get("message", body.decode()[:300])
                    except Exception:
                        err_msg = body.decode()[:300]
                    logger.error(f"[API STREAM ERROR] {resp.status_code}: {err_msg}")
                    raise Exception(f"{resp.status_code} Error: {err_msg}")

                async for line in resp.aiter_lines():
                    line = line.strip()
                    if not line or not line.startswith("data: "):
                        continue
                    data_str = line[6:]  # strip "data: "
                    if data_str == "[DONE]":
                        break

                    try:
                        chunk = json.loads(data_str)
                    except json.JSONDecodeError:
                        continue

                    choices = chunk.get("choices", [])
                    # Capture token usage from any chunk that has it (typically last chunk)
                    if chunk.get("usage"):
                        usage_info = chunk["usage"]
                    if not choices:
                        continue

                    delta = choices[0].get("delta", {})
                    choice_finish = choices[0].get("finish_reason")

                    if choice_finish:
                        finish_reason = choice_finish

                    # Reasoning content (DeepSeek reasoner models)
                    reasoning_delta = delta.get("reasoning_content")
                    if reasoning_delta:
                        reasoning_content_parts.append(reasoning_delta)
                        for i in range(0, len(reasoning_delta), STREAM_CHUNK_SIZE):
                            yield {"type": STREAM_EVENT_THINKING_DELTA, "content": reasoning_delta[i:i+STREAM_CHUNK_SIZE]}

                    # Content delta — parse <think/> tags if present
                    content_delta = delta.get("content")
                    if content_delta:
                        for frag_type, frag_text in tag_parser.feed(content_delta):
                            if frag_type == 'think':
                                reasoning_content_parts.append(frag_text)
                                for i in range(0, len(frag_text), STREAM_CHUNK_SIZE):
                                    yield {"type": STREAM_EVENT_THINKING_DELTA, "content": frag_text[i:i+STREAM_CHUNK_SIZE]}
                            else:
                                content_parts.append(frag_text)
                                for i in range(0, len(frag_text), STREAM_CHUNK_SIZE):
                                    yield {"type": STREAM_EVENT_CONTENT_DELTA, "delta": frag_text[i:i+STREAM_CHUNK_SIZE]}

                    # Tool calls delta
                    tc_deltas = delta.get("tool_calls")
                    if tc_deltas:
                        for tc_delta in tc_deltas:
                            idx = tc_delta.get("index", 0)
                            if idx not in tool_calls_map:
                                tool_calls_map[idx] = {
                                    "id": "",
                                    "name": "",
                                    "arguments_str": "",
                                }
                            entry = tool_calls_map[idx]
                            if tc_delta.get("id"):
                                entry["id"] = tc_delta["id"]
                            fn = tc_delta.get("function", {})
                            if fn.get("name"):
                                entry["name"] = fn["name"]
                            if fn.get("arguments"):
                                entry["arguments_str"] += fn["arguments"]

        # Flush any remaining parser buffer after stream ends
        for frag_type, frag_text in tag_parser.flush():
            if frag_type == 'think':
                reasoning_content_parts.append(frag_text)
                for i in range(0, len(frag_text), STREAM_CHUNK_SIZE):
                    yield {"type": STREAM_EVENT_THINKING_DELTA, "content": frag_text[i:i+STREAM_CHUNK_SIZE]}
            else:
                content_parts.append(frag_text)
                for i in range(0, len(frag_text), STREAM_CHUNK_SIZE):
                    yield {"type": STREAM_EVENT_CONTENT_DELTA, "delta": frag_text[i:i+STREAM_CHUNK_SIZE]}

        # Build final results
        full_content = "".join(content_parts)
        full_reasoning = "".join(reasoning_content_parts)

        # Build tool_calls list if any
        tool_calls_list = None
        if tool_calls_map:
            tool_calls_list = []
            for idx in sorted(tool_calls_map.keys()):
                entry = tool_calls_map[idx]
                tool_calls_list.append(ToolCall(
                    id=entry["id"],
                    name=entry["name"],
                    arguments=entry["arguments_str"],
                ))

        if tool_calls_list:
            yield {
                "type": STREAM_EVENT_TOOL_CALLS,
                "tool_calls": tool_calls_list,
                "content": full_content,
                "reasoning_content": full_reasoning,
                "usage": usage_info,
            }
        else:
            yield {
                "type": STREAM_EVENT_DONE,
                "content": full_content,
                "reasoning_content": full_reasoning,
                "finish_reason": finish_reason,
                "usage": usage_info,
            }
