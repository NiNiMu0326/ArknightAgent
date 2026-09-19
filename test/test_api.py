"""
Integration tests for FastAPI routes using TestClient.
Usage: cd test && python -m pytest test_api.py -v
"""
import sys
import os
import json
import asyncio
import pytest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

os.environ.setdefault("JWT_SECRET", "test-secret-key-for-integration-tests")

from httpx import AsyncClient, ASGITransport
from backend.main import (
    app,
    MAX_SYNC_CONVERSATIONS,
    MAX_SYNC_MESSAGES_PER_CONVERSATION,
    MAX_SYNC_CONTENT_CHARS,
)


async def _request(method, path, **kwargs):
    """Helper to make an async request."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        if method == "GET":
            return await ac.get(path, **kwargs)
        elif method == "POST":
            return await ac.post(path, **kwargs)
        elif method == "DELETE":
            return await ac.delete(path, **kwargs)

def run_async(coro):
    return asyncio.run(coro)


def _unique(prefix):
    """Timestamp-suffixed account name, avoiding duplicate-account errors across runs."""
    return f"{prefix}{int(__import__('time').time() * 1000) % 1000000}"


def _register_and_login(prefix):
    """Register (falling back to login) via the real auth endpoints.

    Returns the ``Authorization`` header dict for the freshly created user.
    ``/agent/*`` endpoints require a logged-in user (session ownership is keyed
    by user id), so tests must go through the same register/login flow as
    ``TestAuthEndpoints.test_me_with_token`` instead of calling them anonymously.
    """
    account = _unique(prefix)
    reg = run_async(_request("POST", "/auth/register", json={
        "account": account,
        "username": "AgentUser",
        "password": "Abc12345",
    }))
    if reg.status_code == 200:
        data = reg.json()
    else:
        login_resp = run_async(_request("POST", "/auth/login", json={
            "account": account,
            "password": "Abc12345",
        }))
        assert login_resp.status_code == 200, login_resp.text
        data = login_resp.json()
    token = data.get("token", "")
    assert token, "Should have obtained a token"
    return {"Authorization": f"Bearer {token}"}


class TestHealthCheck:
    def test_root(self):
        resp = run_async(_request("GET", "/api"))
        assert resp.status_code == 200
        data = resp.json()
        assert "message" in data

    def test_health(self):
        resp = run_async(_request("GET", "/health"))
        assert resp.status_code == 200

    def test_status(self):
        resp = run_async(_request("GET", "/status"))
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, dict)


class TestAuthEndpoints:
    # Use timestamp suffix to avoid duplicate account errors across test runs
    def _unique(self, prefix):
        return _unique(prefix)

    def test_register_valid(self):
        resp = run_async(_request("POST", "/auth/register", json={
            "account": self._unique("test"),
            "username": "TestUser",
            "password": "Abc12345"
        }))
        assert resp.status_code in (200, 400)

    def test_register_duplicate(self):
        acc = self._unique("dup")
        run_async(_request("POST", "/auth/register", json={
            "account": acc,
            "username": "Dup",
            "password": "Abc12345"
        }))
        resp = run_async(_request("POST", "/auth/register", json={
            "account": acc,
            "username": "Dup2",
            "password": "Abc12345"
        }))
        assert resp.status_code in (400, 409)

    def test_register_invalid_password(self):
        resp = run_async(_request("POST", "/auth/register", json={
            "account": self._unique("pwtest"),
            "username": "User",
            "password": "short"
        }))
        assert resp.status_code == 400

    def test_login_success(self):
        acc = self._unique("login")
        run_async(_request("POST", "/auth/register", json={
            "account": acc,
            "username": "LoginUser",
            "password": "Abc12345"
        }))
        resp = run_async(_request("POST", "/auth/login", json={
            "account": acc,
            "password": "Abc12345"
        }))
        assert resp.status_code == 200
        data = resp.json()
        assert "token" in data

    def test_login_wrong_password(self):
        acc = self._unique("badpw")
        run_async(_request("POST", "/auth/register", json={
            "account": acc,
            "username": "BadPw",
            "password": "Abc12345"
        }))
        resp = run_async(_request("POST", "/auth/login", json={
            "account": acc,
            "password": "WrongPass1"
        }))
        assert resp.status_code == 401

    def test_me_unauthorized(self):
        resp = run_async(_request("GET", "/auth/me"))
        assert resp.status_code == 401

    def test_me_with_token(self):
        acc = self._unique("me")
        reg = run_async(_request("POST", "/auth/register", json={
            "account": acc,
            "username": "MeUser",
            "password": "Abc12345"
        }))
        if reg.status_code != 200:
            login_resp = run_async(_request("POST", "/auth/login", json={
                "account": acc,
                "password": "Abc12345"
            }))
            token = login_resp.json().get("token", "")
        else:
            token = reg.json().get("token", "")
        assert token, "Should have obtained a token"
        resp = run_async(_request("GET", "/auth/me", headers={"Authorization": f"Bearer {token}"}))
        assert resp.status_code == 200
        data = resp.json()
        assert data["user"]["account"] == acc


class TestDataEndpoints:
    def test_knowledge_graph(self):
        resp = run_async(_request("GET", "/knowledge-graph"))
        assert resp.status_code == 200
        data = resp.json()
        assert "entities" in data
        assert "relations" in data

    def test_stats(self):
        resp = run_async(_request("GET", "/stats"))
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, dict)

    def test_quick_questions(self):
        resp = run_async(_request("GET", "/quick-questions"))
        assert resp.status_code == 200
        data = resp.json()
        assert "questions" in data
        assert len(data["questions"]) == 9
        categories = {q["category"] for q in data["questions"]}
        assert categories == {"rag", "graph", "structured", "prts_mcp"}
        rag_types = {q["type"] for q in data["questions"] if q["category"] == "rag"}
        assert rag_types == {"skill", "story", "enemy", "alias"}

    def test_status_has_mcp_info(self):
        resp = run_async(_request("GET", "/status"))
        assert resp.status_code == 200
        mcp = resp.json()["mcp"]
        assert mcp["enabled"] is False  # conftest 关闭
        assert mcp["connected"] is False

    def test_status_mcp_connected_uses_registered_count(self, monkeypatch):
        import backend.main as main_module

        class FakeManager:
            connected = True
            last_error = ""
            tools = list(range(24))

        monkeypatch.setattr(main_module.config, "PRTS_MCP_ENABLED", True)
        monkeypatch.setattr(main_module, "_mcp_manager", FakeManager())
        monkeypatch.setattr(main_module, "_mcp_registered_count", 7)
        resp = run_async(_request("GET", "/status"))
        assert resp.json()["mcp"]["tool_count"] == 7

    def test_status_mcp_failure_keeps_error(self, monkeypatch):
        import backend.main as main_module

        class FakeManager:
            connected = False
            last_error = "MCP 子进程启动失败: spawn boom"

        monkeypatch.setattr(main_module.config, "PRTS_MCP_ENABLED", True)
        monkeypatch.setattr(main_module, "_mcp_manager", FakeManager())
        monkeypatch.setattr(main_module, "_mcp_registered_count", 0)
        resp = run_async(_request("GET", "/status"))
        assert resp.json()["mcp"]["error"] == "MCP 子进程启动失败: spawn boom"


class TestAgentEndpoints:
    def test_create_session(self):
        auth = _register_and_login("agsess")
        resp = run_async(_request("POST", "/agent/session", headers=auth))
        assert resp.status_code == 200
        data = resp.json()
        assert "session_id" in data

    def test_create_session_requires_auth(self):
        """Anonymous callers must be rejected: a session is owned by its creator."""
        resp = run_async(_request("POST", "/agent/session"))
        assert resp.status_code == 401

    def test_delete_session(self):
        auth = _register_and_login("agdel")
        create = run_async(_request("POST", "/agent/session", headers=auth))
        assert create.status_code == 200
        sid = create.json()["session_id"]
        resp = run_async(_request("DELETE", f"/agent/session/{sid}", headers=auth))
        assert resp.status_code == 200

    def test_delete_nonexistent_session(self):
        """Deleting an unknown id is a no-op for a logged-in user (idempotent)."""
        auth = _register_and_login("agmiss")
        resp = run_async(_request("DELETE", "/agent/session/nonexistent", headers=auth))
        assert resp.status_code == 200

    def test_delete_session_requires_auth(self):
        resp = run_async(_request("DELETE", "/agent/session/nonexistent"))
        assert resp.status_code == 401

    def test_models_list(self):
        resp = run_async(_request("GET", "/agent/models"))
        assert resp.status_code == 200
        data = resp.json()
        assert "models" in data
        assert len(data["models"]) > 0

    def test_agent_stats(self):
        resp = run_async(_request("GET", "/agent/stats"))
        assert resp.status_code == 200
        data = resp.json()
        assert "active_sessions" in data


# ============================================================
# T33: /conversations/sync 入参上限与批内去重
# ============================================================

@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    """把 backend.db.DB_PATH 重定向到 tmp_path，避免污染仓库 data/ 下的真实库。

    backend.main 以 ``from backend.db import get_db, init_db`` 导入函数对象，而函数体
    读的是 backend.db 模块的全局 DB_PATH，因此 patch 模块属性即可让所有路由走临时库。
    """
    import backend.db as db_module

    test_db = tmp_path / "sync_test.db"
    monkeypatch.setattr(db_module, "DB_PATH", test_db)
    run_async(db_module.init_db())
    return test_db


FRONTEND_TS = "2024-05-01T08:00:00.000Z"


def _uid(prefix):
    """唯一 session_id，避免重跑时与旧数据/其他用例相互干扰。"""
    import uuid
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def _sync_message(role, content, ts=FRONTEND_TS, metadata=None):
    """字段形状与 frontend/src/stores/sessions.js 的 _serializeSessionsForSync 一致。"""
    return {
        "role": role,
        "content": content,
        "metadata": {"timestamp": 1714550400000} if metadata is None else metadata,
        "created_at": ts,
    }


def _sync_conversation(session_id, messages, name="新会话"):
    return {
        "session_id": session_id,
        "name": name,
        "created_at": FRONTEND_TS,
        "updated_at": FRONTEND_TS,
        "messages": messages,
    }


def _sync_payload(session_id, messages, name="新会话"):
    return {"conversations": [_sync_conversation(session_id, messages, name)]}


def _sync(payload, auth):
    return run_async(_request("POST", "/conversations/sync", json=payload, headers=auth))


def _messages_of(session_id, auth):
    resp = run_async(_request("GET", f"/conversations/{session_id}/messages", headers=auth))
    assert resp.status_code == 200, resp.text
    return resp.json()["messages"]


def _conversations_of(auth):
    resp = run_async(_request("GET", "/conversations", headers=auth))
    assert resp.status_code == 200, resp.text
    return resp.json()["conversations"]


class TestConversationsSync:
    """防的回归：/conversations/sync 是「本地全量会话 upsert」的入口，旧实现
    没有任何 payload 上限（超大 body 长时间占用 DB 连接 = DoS），且逐条 INSERT
    导致同一批里重复的消息被写多份（刷新后聊天记录出现重复气泡）。"""

    def test_happy_path_persists_conversation_and_messages(self, isolated_db):
        auth = _register_and_login("sync")
        sid = _uid("sync-ok")
        payload = _sync_payload(sid, [
            _sync_message("user", "银灰的技能是什么？"),
            _sync_message("assistant", "银灰的技能是……",
                          ts="2024-05-01T08:00:05.000Z",
                          metadata={"timestamp": 1714550405000, "round": 1}),
        ], name="银灰技能")

        resp = _sync(payload, auth)
        assert resp.status_code == 200, resp.text
        assert resp.json() == {"status": "ok"}

        assert sid in [c["session_id"] for c in _conversations_of(auth)]
        messages = _messages_of(sid, auth)
        assert [m["role"] for m in messages] == ["user", "assistant"]
        assert messages[0]["content"] == "银灰的技能是什么？"
        assert messages[1]["metadata"]["round"] == 1      # metadata JSON 往返未丢失

    def test_duplicate_messages_within_one_batch_inserted_once(self, isolated_db):
        """同一批里 (role, content, created_at) 完全相同的消息只落库一条。"""
        auth = _register_and_login("sync")
        sid = _uid("sync-dup")
        dup = _sync_message("user", "重复的提问")
        payload = _sync_payload(sid, [
            dup, dict(dup), dict(dup),
            _sync_message("assistant", "回答", ts="2024-05-01T08:00:05.000Z"),
        ])

        assert _sync(payload, auth).status_code == 200
        messages = _messages_of(sid, auth)
        assert len(messages) == 2, [m["content"] for m in messages]

    def test_resync_updates_name_without_duplicating_messages(self, isolated_db):
        """重复同步（前端每次改动都会全量 upsert）不得让消息翻倍，改名要生效。"""
        auth = _register_and_login("sync")
        sid = _uid("sync-twice")
        messages = [
            _sync_message("user", "问题"),
            _sync_message("assistant", "回答", ts="2024-05-01T08:00:05.000Z"),
        ]

        assert _sync(_sync_payload(sid, messages, name="旧名字"), auth).status_code == 200
        assert _sync(_sync_payload(sid, messages, name="新名字"), auth).status_code == 200

        convs = {c["session_id"]: c for c in _conversations_of(auth)}
        assert convs[sid]["name"] == "新名字"
        assert len(_messages_of(sid, auth)) == 2

    def test_too_many_conversations_rejected_before_any_write(self, isolated_db):
        auth = _register_and_login("sync")
        payload = {"conversations": [
            _sync_conversation(_uid(f"many{i}"), [_sync_message("user", "hi")])
            for i in range(MAX_SYNC_CONVERSATIONS + 1)
        ]}
        assert _sync(payload, auth).status_code == 422
        assert _conversations_of(auth) == []          # 校验发生在写库之前

    def test_too_many_messages_in_one_conversation_rejected(self, isolated_db):
        auth = _register_and_login("sync")
        sid = _uid("sync-many-msgs")
        messages = [_sync_message("user", f"消息{i}") for i in range(
            MAX_SYNC_MESSAGES_PER_CONVERSATION + 1
        )]
        assert _sync(_sync_payload(sid, messages), auth).status_code == 422
        assert _conversations_of(auth) == []

    def test_oversized_message_content_rejected(self, isolated_db):
        auth = _register_and_login("sync")
        sid = _uid("sync-big")
        payload = _sync_payload(sid, [
            _sync_message("user", "x" * (MAX_SYNC_CONTENT_CHARS + 1)),
        ])
        assert _sync(payload, auth).status_code == 422
        assert _conversations_of(auth) == []

    def test_content_exactly_at_limit_is_accepted(self, isolated_db):
        """边界值必须放行（上限是「大于」才拒绝），避免约束被写成过紧的 >= 。"""
        auth = _register_and_login("sync")
        sid = _uid("sync-limit")
        content = "x" * MAX_SYNC_CONTENT_CHARS
        assert _sync(_sync_payload(sid, [_sync_message("user", content)]), auth).status_code == 200
        assert len(_messages_of(sid, auth)[0]["content"]) == MAX_SYNC_CONTENT_CHARS

    def test_sync_requires_login(self, isolated_db):
        resp = run_async(_request("POST", "/conversations/sync",
                                  json=_sync_payload(_uid("anon"), [])))
        assert resp.status_code == 401

    def test_oversized_body_rejected_with_413(self, isolated_db, monkeypatch):
        """Content-Length 预检：请求体超过 MAX_SYNC_BODY_BYTES 时返回 413 且不写库。

        真实上限是 64MB（构造不现实），因此把模块常量压到 1KB 来验证预检分支。
        """
        import backend.main as main_module

        monkeypatch.setattr(main_module, "MAX_SYNC_BODY_BYTES", 1024)
        auth = _register_and_login("sync")
        payload = _sync_payload(_uid("sync-body"), [_sync_message("user", "y" * 2000)])

        resp = _sync(payload, auth)
        assert resp.status_code == 413, resp.text
        assert _conversations_of(auth) == []
