"""
Tests for backend.agent.context_engine:
turn splitting, compression trigger, compressed context shape,
incremental rolling summary, SQLite persistence, and context log insert.
"""
import asyncio
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import backend.db as db
from backend.agent.context_engine import (
    CONTEXT_COMPRESSION_MIN_TURNS,
    CONTEXT_COMPRESSION_TOKEN_BUDGET,
    CONTEXT_KEEP_FIRST_TURNS,
    CONTEXT_KEEP_RECENT_TURNS,
    build_compressed_messages,
    can_build_compressed,
    estimate_messages_tokens,
    estimate_tokens,
    log_context_snapshot,
    should_compress,
    split_turns,
    update_rolling_summary,
)
from backend.agent.prompts import SYSTEM_PROMPT, build_messages
from backend.agent.sessions import Session, SessionManager
from backend.api.deepseek import ToolCall


def make_session(turn_count, include_tool_pairs=False):
    """Create a session with `turn_count` simple user+assistant turns."""
    s = Session(session_id="test-session")
    for i in range(1, turn_count + 1):
        s.add_message("user", f"用户问题{i}")
        if include_tool_pairs and i == 1:
            tc = ToolCall(id=f"call_first_{i}", name="arknights_rag_search", arguments='{"q":"x"}')
            s.add_assistant_tool_calls([tc], content="查询中")
            s.add_tool_result(tc.id, "查询结果")
        else:
            s.add_message("assistant", f"回答{i}")
    return s


class TestEstimateTokens:
    def test_cjk_chars_count_one_token(self):
        assert estimate_tokens("银灰") == 2

    def test_ascii_chars_count_four_per_token(self):
        assert estimate_tokens("abcd") == 1
        assert estimate_tokens("abcdefgh") == 2

    def test_empty_string(self):
        assert estimate_tokens("") == 0

    def test_messages_estimate_includes_nested_tool_calls(self):
        s = Session(session_id="x")
        s.add_message("user", "银灰")
        tc = ToolCall(id="c1", name="tool", arguments='{"q":"银灰"}')
        s.add_assistant_tool_calls([tc], content="查询")
        s.add_tool_result("c1", "结果")
        # Must count both assistant content, tool_calls arguments, and tool result.
        assert estimate_messages_tokens(s.messages) > 0


class TestSplitTurns:
    def test_groups_messages_after_user(self):
        s = Session(session_id="x")
        s.add_message("user", "u1")
        s.add_message("assistant", "a1")
        s.add_message("tool", "r1")
        s.add_message("user", "u2")
        s.add_message("assistant", "a2")
        turns = split_turns(s.messages)
        assert len(turns) == 2
        assert len(turns[0]) == 3
        assert len(turns[1]) == 2

    def test_no_user_messages_returns_one_turn(self):
        turns = split_turns([{"role": "assistant", "content": "a"}])
        assert len(turns) == 1
        assert turns[0][0]["role"] == "assistant"

    def test_leading_non_user_merged_into_first_turn(self):
        turns = split_turns([
            {"role": "system", "content": "notice"},
            {"role": "user", "content": "u1"},
            {"role": "assistant", "content": "a1"},
        ])
        assert len(turns) == 1
        assert turns[0][0]["role"] == "system"
        assert turns[0][1]["role"] == "user"


class TestCompressionTrigger:
    def test_off_below_or_equal_min_turns(self):
        s = make_session(CONTEXT_COMPRESSION_MIN_TURNS)
        assert not should_compress(s)

    def test_on_when_turns_exceed_min(self):
        s = make_session(CONTEXT_COMPRESSION_MIN_TURNS + 1)
        assert should_compress(s)

    def test_on_when_tokens_exceed_budget_even_with_few_turns(self):
        s = make_session(2)
        big = "很" * (CONTEXT_COMPRESSION_TOKEN_BUDGET + 100)
        s.messages.append({"role": "user", "content": big})
        assert should_compress(s)

    def test_can_build_without_summary_when_no_middle(self):
        # 3 turns -> first+recent overlap, no middle -> summary not needed.
        s = make_session(3)
        assert should_compress(s) is False  # small by default
        s.messages.append({"role": "user", "content": "很" * (CONTEXT_COMPRESSION_TOKEN_BUDGET + 1)})
        assert should_compress(s)
        assert can_build_compressed(s)


class TestCompressedContextShape:
    def _build_compressed(self, turn_count):
        s = make_session(turn_count)
        s.summary = "中间对话摘要"
        return build_messages(s)

    def test_first_and_recent_turns_plus_summary(self):
        messages = self._build_compressed(9)
        roles = [m["role"] for m in messages]
        assert roles[0] == "system"
        assert messages[0]["content"] == SYSTEM_PROMPT
        # first turn in full
        assert messages[1]["role"] == "user"
        assert messages[1]["content"] == "用户问题1"
        assert messages[2]["role"] == "assistant"
        assert messages[2]["content"] == "回答1"
        # one system summary message
        summary_msgs = [m for m in messages if m["role"] == "system" and "滚动摘要" in m["content"]]
        assert len(summary_msgs) == 1
        # last 3 turns in full
        last_user_contents = [m["content"] for m in messages if m["role"] == "user"]
        assert last_user_contents == [
            "用户问题1", "用户问题7", "用户问题8", "用户问题9",
        ]

    def test_full_first_turn_includes_tool_pair(self):
        s = make_session(9, include_tool_pairs=True)
        s.summary = "摘要"
        messages = build_messages(s)
        # First turn has assistant tool_calls + tool result, both present.
        first_turn_roles = [m["role"] for m in messages[1:4]]
        assert first_turn_roles == ["user", "assistant", "tool"]
        assert messages[2]["tool_calls"][0]["id"] == "call_first_1"
        assert messages[3]["tool_call_id"] == "call_first_1"

    def test_no_orphan_tool_pairs_in_full_blocks(self):
        s = Session(session_id="orphan-test")
        # Turn 1: valid pair
        tc1 = ToolCall(id="valid_1", name="tool", arguments="{}")
        s.add_message("user", "u1")
        s.add_assistant_tool_calls([tc1], content="calling")
        s.add_tool_result("valid_1", "ok")
        # Turns 2..8 plain
        for i in range(2, 9):
            s.add_message("user", f"u{i}")
            s.add_message("assistant", f"a{i}")
        # Turn 9 (recent): orphan assistant call + orphan tool result
        tc2 = ToolCall(id="orphan_call", name="tool", arguments="{}")
        s.add_message("user", "u9")
        s.add_assistant_tool_calls([tc2], content="orphan")
        s.add_tool_result("orphan_result", "orphan result")
        s.summary = "摘要"

        messages = build_messages(s)
        assistant_tc_ids = {
            tc["id"]
            for m in messages if m.get("role") == "assistant" and m.get("tool_calls")
            for tc in m["tool_calls"]
        }
        tool_result_ids = {
            m["tool_call_id"]
            for m in messages if m.get("role") == "tool" and m.get("tool_call_id")
        }
        assert assistant_tc_ids == tool_result_ids
        assert "orphan_call" not in assistant_tc_ids
        assert "orphan_result" not in tool_result_ids
        # The valid pair remains.
        assert "valid_1" in assistant_tc_ids

    def test_summary_failure_falls_back_to_uncompressed(self):
        s = make_session(9)
        # Compression triggered by turns, but no summary -> safe fallback.
        assert should_compress(s)
        assert not can_build_compressed(s)
        messages = build_messages(s)
        assert len(messages) <= 21  # system + at most 20 messages
        assert not any("滚动摘要" in m.get("content", "") for m in messages)


class FakeSummaryClient:
    """Fake DeepSeek client exposing non-streaming chat_completion."""

    def __init__(self, result="合并后的摘要", fail=False):
        self.result = result
        self.fail = fail
        self.calls = []
        self.last_messages = None

    async def chat_completion(self, messages, model=None, temperature=0.3, **kwargs):
        self.calls.append({"messages": messages, "model": model, "temperature": temperature})
        self.last_messages = messages
        if self.fail:
            raise ConnectionError("summary api down")
        return self.result


class TestRollingSummary:
    def test_incremental_update_success(self):
        s = make_session(9)
        client = FakeSummaryClient("简明摘要")
        updated = asyncio.run(update_rolling_summary(s, client, "deepseek-v4-flash"))
        assert updated is True
        assert s.summary == "简明摘要"
        assert s.summary_up_to_turn == 9 - CONTEXT_KEEP_RECENT_TURNS
        assert client.last_messages is not None
        joined = json.dumps(client.last_messages, ensure_ascii=False)
        assert "新增对话片段" in joined

    def test_incremental_update_only_new_middle_turns(self):
        s = make_session(9)
        client = FakeSummaryClient("摘要1")
        asyncio.run(update_rolling_summary(s, client, "model"))
        assert s.summary_up_to_turn == 6

        # Add turn 10 -> only turn 7 becomes newly summarized.
        s.add_message("user", "用户问题10")
        s.add_message("assistant", "回答10")
        client2 = FakeSummaryClient("摘要2")
        updated = asyncio.run(update_rolling_summary(s, client2, "model"))
        assert updated is True
        assert s.summary_up_to_turn == 7
        prompt_text = json.dumps(client2.last_messages, ensure_ascii=False)
        assert "用户问题7" in prompt_text
        assert "用户问题1" not in prompt_text

    def test_failure_returns_false_and_preserves_summary(self):
        s = make_session(9)
        client = FakeSummaryClient(fail=True)
        updated = asyncio.run(update_rolling_summary(s, client, "model"))
        assert updated is False
        assert s.summary == ""
        assert s.summary_up_to_turn == 0

    def test_no_middle_does_not_call_summary(self):
        # 3 turns + huge token count -> compress by tokens but no middle window.
        s = make_session(3)
        s.messages.append({"role": "user", "content": "很" * (CONTEXT_COMPRESSION_TOKEN_BUDGET + 1)})
        client = FakeSummaryClient()
        updated = asyncio.run(update_rolling_summary(s, client, "model"))
        assert updated is False
        assert client.calls == []


class TestPersistence:
    def _init_temp_db(self, tmp_path, monkeypatch):
        monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
        asyncio.run(db.init_db())

    def test_persist_and_restore_round_trip(self, tmp_path, monkeypatch):
        self._init_temp_db(tmp_path, monkeypatch)
        s = Session(
            session_id="persist-1",
            messages=[
                {"role": "user", "content": "你好"},
                {"role": "assistant", "content": "你好，有什么可以帮你？"},
            ],
            summary="已有摘要",
            summary_up_to_turn=5,
        )
        sm = SessionManager()
        asyncio.run(sm.persist_session(s))

        sm2 = SessionManager()
        restored = asyncio.run(sm2.restore_session("persist-1"))
        assert restored is not None
        assert restored.session_id == "persist-1"
        assert restored.messages == s.messages
        assert restored.summary == "已有摘要"
        assert restored.summary_up_to_turn == 5

    def test_restore_missing_returns_none(self, tmp_path, monkeypatch):
        self._init_temp_db(tmp_path, monkeypatch)
        sm = SessionManager()
        assert asyncio.run(sm.restore_session("missing")) is None

    def test_delete_removes_persisted_row(self, tmp_path, monkeypatch):
        self._init_temp_db(tmp_path, monkeypatch)
        s = Session(session_id="delete-me", messages=[{"role": "user", "content": "hi"}])
        sm = SessionManager()
        asyncio.run(sm.persist_session(s))
        asyncio.run(sm.delete_session("delete-me"))
        sm2 = SessionManager()
        assert asyncio.run(sm2.restore_session("delete-me")) is None


class TestAgentChatRestore:
    def _init_temp_db(self, tmp_path, monkeypatch):
        monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
        asyncio.run(db.init_db())

    def test_restored_session_reuses_id_without_new_header(self, tmp_path, monkeypatch):
        import backend.main as main_module
        from httpx import AsyncClient, ASGITransport

        self._init_temp_db(tmp_path, monkeypatch)
        sm = SessionManager()
        s = Session(
            session_id="api-restore",
            messages=[
                {"role": "user", "content": "旧问题"},
                {"role": "assistant", "content": "旧回答"},
            ],
        )
        asyncio.run(sm.persist_session(s))
        monkeypatch.setattr(main_module, "_session_manager", sm)

        async def dummy_agent_loop(session_id, user_message, session_manager, model_id=None, max_rounds=15):
            yield "data: {\"type\":\"answer_done\",\"answer\":\"ok\"}\n\n"

        monkeypatch.setattr(main_module, "agent_loop", dummy_agent_loop)

        async def _post():
            transport = ASGITransport(app=main_module.app)
            async with AsyncClient(transport=transport, base_url="http://test") as ac:
                return await ac.post(
                    "/agent/chat",
                    json={"session_id": "api-restore", "message": "新问题"},
                )

        resp = asyncio.run(_post())
        assert resp.status_code == 200
        assert "X-New-Session-Id" not in resp.headers
        # Consume the SSE body so the streaming response is fully finalized.
        assert "answer_done" in resp.text

        async def check():
            session = await sm.get_session("api-restore")
            return session is not None and len(session.messages) == 2

        assert asyncio.run(check()) is True


class TestContextLog:
    def _init_temp_db(self, tmp_path, monkeypatch):
        monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
        asyncio.run(db.init_db())

    async def _count_rows(self):
        conn = await db.get_db()
        try:
            cur = await conn.execute("SELECT COUNT(*) FROM agent_context_logs")
            row = await cur.fetchone()
            return row[0]
        finally:
            await conn.close()

    def test_insert_context_snapshot(self, tmp_path, monkeypatch):
        self._init_temp_db(tmp_path, monkeypatch)
        messages = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "你好"},
        ]
        asyncio.run(log_context_snapshot(
            session_id="log-1", turn_no=1, messages=messages,
            estimated_tokens=10, compressed=False,
        ))
        assert asyncio.run(self._count_rows()) == 1

    def test_retention_caps_at_100(self, tmp_path, monkeypatch):
        self._init_temp_db(tmp_path, monkeypatch)
        for i in range(105):
            asyncio.run(log_context_snapshot(
                session_id="retain-1", turn_no=i + 1,
                messages=[{"role": "user", "content": f"q{i}"}],
                estimated_tokens=i, compressed=False,
            ))
        assert asyncio.run(self._count_rows()) == 100
