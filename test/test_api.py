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
from backend.main import app


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
        return f"{prefix}{int(__import__('time').time() * 1000) % 1000000}"

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
        assert len(data["questions"]) == 4
        categories = {q["category"] for q in data["questions"]}
        assert categories == {"rag", "graph", "structured", "prts_mcp"}

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
        resp = run_async(_request("POST", "/agent/session"))
        assert resp.status_code == 200
        data = resp.json()
        assert "session_id" in data

    def test_delete_session(self):
        create = run_async(_request("POST", "/agent/session"))
        sid = create.json()["session_id"]
        resp = run_async(_request("DELETE", f"/agent/session/{sid}"))
        assert resp.status_code == 200

    def test_delete_nonexistent_session(self):
        resp = run_async(_request("DELETE", "/agent/session/nonexistent"))
        assert resp.status_code == 200

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
