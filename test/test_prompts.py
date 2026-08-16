"""
Tests for backend.agent.prompts: SYSTEM_PROMPT + build_messages.
Usage: cd test && python -m pytest test_prompts.py -v
"""
import sys
import pytest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from backend.agent.prompts import SYSTEM_PROMPT, build_messages
from backend.agent.sessions import Session
from backend.api.deepseek import ToolCall


# ============================================================
# SYSTEM_PROMPT validation
# ============================================================

class TestSystemPrompt:
    """Verify the system prompt structure and content requirements."""

    def test_prompt_is_non_empty(self):
        assert SYSTEM_PROMPT, "SYSTEM_PROMPT should not be empty"
        assert len(SYSTEM_PROMPT) > 200

    def test_prompt_contains_tool_names(self):
        """System prompt should reference all three tool names."""
        assert "arknights_rag_search" in SYSTEM_PROMPT
        assert "arknights_graphrag_search" in SYSTEM_PROMPT
        assert "web_search" in SYSTEM_PROMPT

    def test_prompt_contains_search_modes(self):
        """Should mention search_mode options."""
        assert "precise" in SYSTEM_PROMPT
        assert "semantic" in SYSTEM_PROMPT
        assert "balanced" in SYSTEM_PROMPT

    def test_prompt_contains_mcp_tool_names(self):
        assert "get_stage_enemies" in SYSTEM_PROMPT
        assert "get_item_info" in SYSTEM_PROMPT
        assert "operator_artwork" in SYSTEM_PROMPT
        assert "search_prts" in SYSTEM_PROMPT

    def test_prompt_contains_safety_constraints(self):
        """Should contain safety rules."""
        assert "不要编造" in SYSTEM_PROMPT

    def test_prompt_contains_answer_structure(self):
        """Should contain answer format requirements."""
        assert "数值查询" in SYSTEM_PROMPT
        assert "剧情查询" in SYSTEM_PROMPT
        assert "干员查询" in SYSTEM_PROMPT

    def test_prompt_contains_special_rules(self):
        """Should contain special rules section."""
        assert "特殊规则" in SYSTEM_PROMPT

    def test_prompt_contains_examples(self):
        """Should contain example Q&A pairs."""
        assert "示例 1" in SYSTEM_PROMPT
        assert "示例 2" in SYSTEM_PROMPT
        assert "示例 3" in SYSTEM_PROMPT

    def test_prompt_contains_thinking_rules(self):
        """Should constrain thinking verbosity."""
        assert "思考准则" in SYSTEM_PROMPT
        assert "1-3句" in SYSTEM_PROMPT


# ============================================================
# build_messages
# ============================================================

class TestBuildMessages:
    """Test message list construction for LLM API calls."""

    def test_returns_list_starting_with_system(self):
        s = Session(session_id="test-1")
        messages = build_messages(s)
        assert isinstance(messages, list)
        assert len(messages) >= 1
        assert messages[0]["role"] == "system"
        assert messages[0]["content"] == SYSTEM_PROMPT

    def test_includes_session_messages(self):
        s = Session(session_id="test-1")
        s.add_message("user", "银灰的攻击力是多少？")
        s.add_message("assistant", "银灰的基础攻击力为 793。")
        messages = build_messages(s)
        assert len(messages) == 3  # system + user + assistant
        assert messages[1]["role"] == "user"
        assert messages[2]["role"] == "assistant"

    def test_empty_session_returns_only_system(self):
        s = Session(session_id="test-1")
        messages = build_messages(s)
        assert len(messages) == 1
        assert messages[0]["role"] == "system"

    def test_respects_max_messages(self):
        """Session get_context_messages defaults to max_messages=20."""
        s = Session(session_id="test-1")
        for i in range(30):
            s.add_message("user", f"msg{i}")
        messages = build_messages(s)
        # 1 system + at most 20 context messages
        assert len(messages) <= 21
        assert messages[0]["role"] == "system"

    def test_tool_call_messages_included(self):
        s = Session(session_id="test-1")
        tc = ToolCall(id="call_1", name="arknights_rag_search", arguments='{"q":"银灰"}')
        s.add_assistant_tool_calls([tc], content="查询中")
        s.add_tool_result("call_1", {"result": "银灰数据"})
        s.add_message("assistant", "银灰的攻击力是793。")
        messages = build_messages(s)
        # system + assistant(tool_call) + tool_result + assistant(answer) = 4
        roles = [m["role"] for m in messages]
        assert roles == ["system", "assistant", "tool", "assistant"]
