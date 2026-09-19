"""
T01「会话 / trace 归属」安全修复的永久回归测试。

背景：T01 的第一轮修复把归属只放在**进程内内存**里，独立审查打回——服务重启
（每次 push 到 master 都会自动部署重启）后进程内归属表为空，任何登录用户只要
先发一个请求，就能把别人的会话/trace 认领成自己的（IDOR）。

本文件钉住修复后的语义：

1. 归属必须持久化（``agent_session_store.user_id`` / ``traces.user_id``）；
2. 重启（清空进程内归属缓存 + 换一个空的内存 SessionManager + 重跑 ``init_db()``）
   之后，越权访问依然被拒，**且不返回属主的任何数据**；
3. 归属未知（NULL / 查不到）且**已有历史消息**的会话、归属为 NULL 的 trace
   一律拒绝，不做「先到先得」式认领（fail-closed）；
4. 属主本人不受影响：重启后仍能读自己的会话并续聊，且复用同一个 session_id
   （响应头不出现 ``X-New-Session-Id``）；
5. ``session_id`` 超长（>128）由请求校验挡在业务逻辑之前返回 422。

所有 HTTP 调用都真实走 ``/auth/register`` + ``/auth/login`` 拿 token；
数据库通过 monkeypatch ``backend.db.DB_PATH`` 重定向到 ``tmp_path``，
绝不读写 ``data/`` 下的真实数据库；``agent_loop`` 一律替换为假实现，
避免任何真实 LLM / 外部 API 调用。
"""
import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import List, Optional

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
os.environ.setdefault("JWT_SECRET", "test-secret-key-for-agent-ownership")

from httpx import ASGITransport, AsyncClient

import backend.db as db
import backend.main as main_module
from backend.agent.sessions import SessionManager

_PASSWORD = "Abc12345"
# 属主的历史消息内容：任何越权响应体里都不允许出现，用来证明「拿不到别人的数据」
_SECRET = "SECRET-历史消息-仅属主可见"
_LONG_SID = "s" * (main_module.MAX_SESSION_ID_LENGTH + 1)


def _run(coro):
    return asyncio.run(coro)


class _User:
    """一个已登录用户：Authorization 头 + 数据库里的 user id。"""

    def __init__(self, headers: dict, user_id: int):
        self.headers = headers
        self.user_id = user_id


class _Api:
    """把 /auth 与 /agent 端点当作黑盒调用的测试 helper（DB 已重定向到 tmp_path）。"""

    # ---------- 认证 ----------

    def login(self, account: str) -> _User:
        """走真实 /auth/register + /auth/login 拿 token（与 test_api.py 同一路径）。"""
        async def _flow():
            transport = ASGITransport(app=main_module.app)
            async with AsyncClient(transport=transport, base_url="http://test") as ac:
                reg = await ac.post("/auth/register", json={
                    "account": account, "username": account, "password": _PASSWORD,
                })
                assert reg.status_code == 200, reg.text
                resp = await ac.post("/auth/login", json={
                    "account": account, "password": _PASSWORD,
                })
                assert resp.status_code == 200, resp.text
                return resp.json()

        data = _run(_flow())
        return _User({"Authorization": f"Bearer {data['token']}"}, data["user"]["id"])

    # ---------- 通用请求 ----------

    def request(self, method: str, path: str, user: Optional[_User] = None, json_body=None):
        """发一次 HTTP 请求；user=None 表示匿名。"""
        async def _req():
            transport = ASGITransport(app=main_module.app)
            async with AsyncClient(transport=transport, base_url="http://test") as ac:
                return await ac.request(
                    method, path,
                    headers=user.headers if user else None,
                    json=json_body,
                )

        return _run(_req())

    # ---------- 会话 ----------

    def create_session(self, user: _User) -> str:
        """用真实 POST /agent/session 建会话（归属随创建登记到持久层）。"""
        resp = self.request("POST", "/agent/session", user)
        assert resp.status_code == 200, resp.text
        return resp.json()["session_id"]

    def append_history(self, session_id: str, messages: List[dict]) -> None:
        """给内存会话追加历史消息并持久化，模拟「这个会话已经有内容了」。"""
        async def _append():
            sm = main_module._session_manager
            session = await sm.get_session(session_id)
            assert session is not None, f"session {session_id} 不在内存里"
            session.messages.extend(messages)
            await sm.persist_session(session)

        _run(_append())

    def insert_unowned_session(self, session_id: str, messages: List[dict]) -> None:
        """直接造一行「归属为 NULL 但有历史消息」的会话（模拟迁移前的历史数据）。"""
        async def _insert():
            conn = await db.get_db()
            try:
                await conn.execute(
                    "INSERT INTO agent_session_store "
                    "(session_id, messages, summary, summary_up_to_turn, last_active, created_at, user_id) "
                    "VALUES (?, ?, '', 0, ?, ?, NULL)",
                    (session_id, json.dumps(messages, ensure_ascii=False), time.time(), time.time()),
                )
                await conn.commit()
            finally:
                await conn.close()

        _run(_insert())

    def persisted_owner(self, session_id: str) -> Optional[int]:
        """读库里的会话属主（None = 无记录或 NULL）。"""
        async def _read():
            conn = await db.get_db()
            try:
                cur = await conn.execute(
                    "SELECT user_id FROM agent_session_store WHERE session_id=?", (session_id,)
                )
                row = await cur.fetchone()
            finally:
                await conn.close()
            return None if row is None or row["user_id"] is None else int(row["user_id"])

        return _run(_read())

    def persisted_messages(self, session_id: str) -> str:
        """读库里会话的原始 messages JSON 字符串。"""
        async def _read():
            conn = await db.get_db()
            try:
                cur = await conn.execute(
                    "SELECT messages FROM agent_session_store WHERE session_id=?", (session_id,)
                )
                row = await cur.fetchone()
            finally:
                await conn.close()
            return "" if row is None else row["messages"]

        return _run(_read())

    # ---------- trace ----------

    def insert_trace(self, session_id: str, user_id: Optional[int], user_message: str = "") -> int:
        """直接插一行 trace（user_id=None 表示归属未知的历史数据），返回 trace id。"""
        async def _insert():
            conn = await db.get_db()
            try:
                cur = await conn.execute(
                    "INSERT INTO traces (session_id, user_message, model_id, total_rounds, status, user_id) "
                    "VALUES (?, ?, 'deepseek-v4-flash', 1, 'success', ?)",
                    (session_id, user_message, user_id),
                )
                await conn.commit()
                return cur.lastrowid
            finally:
                await conn.close()

        return _run(_insert())

    def trace_row(self, trace_id: int) -> Optional[dict]:
        """按 id 读 trace 行；已被删除时返回 None。"""
        async def _read():
            conn = await db.get_db()
            try:
                cur = await conn.execute("SELECT * FROM traces WHERE id=?", (trace_id,))
                row = await cur.fetchone()
            finally:
                await conn.close()
            return None if row is None else dict(row)

        return _run(_read())

    # ---------- 「重启」 ----------

    def restart(self) -> None:
        """模拟服务重启：清空进程内归属缓存 + 全新 SessionManager + 重跑 init_db()。

        这三步分别对应真实重启后消失的三样东西：归属读缓存、内存会话表、
        以及启动时执行的建表/迁移（init_db 幂等，重跑不改数据）。
        """
        self.forget_memory()
        _run(db.init_db())

    def forget_memory(self) -> None:
        """丢掉全部进程内状态（归属缓存 + 内存会话表），保留 SQLite 里的数据。"""
        main_module._session_owner_cache.clear()
        main_module._session_manager = SessionManager()


@pytest.fixture
def api(tmp_path, monkeypatch):
    """把 SQLite 重定向到 tmp_path，装上干净的内存 SessionManager 与假 agent_loop。

    假 agent_loop：既让 /agent/chat 的 SSE 能正常结束，也保证任何走到真实
    agent 循环的（越权）请求都不会真的去调外部 LLM API。
    """
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test_agent_ownership.db")
    _run(db.init_db())
    main_module._session_owner_cache.clear()
    monkeypatch.setattr(main_module, "_session_manager", SessionManager())

    async def _dummy_agent_loop(session_id, user_message, session_manager, model_id=None, max_rounds=15):
        yield 'data: {"type":"answer_done","answer":"ok"}\n\n'

    monkeypatch.setattr(main_module, "agent_loop", _dummy_agent_loop)
    return _Api()


# ============================================================
# 1. 重启后仍拒绝越权（T01 被审查打回的那条）
# ============================================================

class TestOwnershipSurvivesRestart:
    def test_other_user_denied_after_restart(self, api):
        """防回归：归属只在内存里的旧实现，重启后 B 能靠先发请求认领 A 的会话。

        重启是常态（push 到 master 即部署重启），所以必须证明重启后越权仍被拒，
        且三个入口（读消息 / 续聊 / 删除）都不返回、不破坏 A 的数据。
        """
        owner = api.login("restartowner")
        other = api.login("restartother")

        sid = api.create_session(owner)
        api.append_history(sid, [
            {"role": "user", "content": _SECRET},
            {"role": "assistant", "content": "属主的旧回答"},
        ])

        api.restart()

        # 读消息：403/404 皆可，但绝不能返回 A 的数据
        resp = api.request("GET", f"/agent/session/{sid}/messages", other)
        assert resp.status_code in (403, 404), resp.text
        assert _SECRET not in resp.text

        # 续聊：不能借 session_id 把自己的消息写进 A 的会话
        resp = api.request("POST", "/agent/chat", other, {"session_id": sid, "message": "越权续聊"})
        assert resp.status_code in (403, 404), resp.text
        assert _SECRET not in resp.text

        # 调试 trace：同样不能拿到 A 的工具调用链
        resp = api.request("GET", f"/agent/debug/trace?session_id={sid}", other)
        assert resp.status_code in (403, 404), resp.text
        assert _SECRET not in resp.text

        # 删除：不能删掉 A 的会话
        resp = api.request("DELETE", f"/agent/session/{sid}", other)
        assert resp.status_code in (403, 404), resp.text

        # 归属未被改写、历史消息未被覆盖、A 自己依旧可读
        assert api.persisted_owner(sid) == owner.user_id
        assert _SECRET in api.persisted_messages(sid)
        resp = api.request("GET", f"/agent/session/{sid}/messages", owner)
        assert resp.status_code == 200, resp.text
        assert _SECRET in resp.text

    def test_owner_still_reads_and_continues_after_restart(self, api):
        """反向保护：修复不能「过度拒绝」——重启后属主本人必须还能读和续聊。

        同时钉住「复用同一个 session_id」：响应头出现 X-New-Session-Id 说明实现
        把 A 的旧会话当成不存在、另建了新会话（A 的历史消息会丢）。
        """
        owner = api.login("restartself")
        sid = api.create_session(owner)
        api.append_history(sid, [
            {"role": "user", "content": _SECRET},
            {"role": "assistant", "content": "属主的旧回答"},
        ])

        api.restart()

        resp = api.request("GET", f"/agent/session/{sid}/messages", owner)
        assert resp.status_code == 200, resp.text
        contents = [m["content"] for m in resp.json()["messages"]]
        assert _SECRET in contents
        assert "属主的旧回答" in contents

        resp = api.request("POST", "/agent/chat", owner, {"session_id": sid, "message": "属主续聊"})
        assert resp.status_code == 200, resp.text
        assert "X-New-Session-Id" not in resp.headers
        assert "answer_done" in resp.text

        assert api.persisted_owner(sid) == owner.user_id


# ============================================================
# 2. 归属未知 + 有历史消息 → 不可认领（不做「先到先得」）
# ============================================================

class TestUnownedSessionCannotBeClaimed:
    def test_unowned_nonempty_session_rejected_for_everyone(self, api):
        """防回归：旧实现把「内存里没记录」当成「无主可认领」，于是任何人先请求即成属主。

        归属为 NULL 的历史会话只要已经有消息，就必须一律拒绝（fail-closed），
        且拒绝路径不能顺手把 user_id 写进库。
        """
        first = api.login("claimfirst")
        second = api.login("claimsecond")

        sid = "legacy-unowned-session"
        api.insert_unowned_session(sid, [
            {"role": "user", "content": _SECRET},
            {"role": "assistant", "content": "历史回答"},
        ])

        for idx, user in enumerate((first, second)):
            if idx > 0:
                # 第二个用户前丢掉内存态：同时覆盖「内存命中」与「只从库里恢复」两条路径
                api.forget_memory()

            resp = api.request("GET", f"/agent/session/{sid}/messages", user)
            assert resp.status_code in (403, 404), resp.text
            assert _SECRET not in resp.text

            resp = api.request("POST", "/agent/chat", user, {"session_id": sid, "message": "认领尝试"})
            assert resp.status_code in (403, 404), resp.text
            assert _SECRET not in resp.text

            resp = api.request("DELETE", f"/agent/session/{sid}", user)
            assert resp.status_code in (403, 404), resp.text

        # 谁都没能把它认领走，历史消息也还在
        assert api.persisted_owner(sid) is None
        assert _SECRET in api.persisted_messages(sid)


# ============================================================
# 3. trace 归属：他人不可见 / 不可删，归属未知对所有人不可见
# ============================================================

class TestTraceOwnership:
    def test_other_user_cannot_see_export_or_delete_trace(self, api):
        """防回归：trace 列表/详情/导出/删除只要漏掉 user_id 过滤，就是跨用户数据泄漏。"""
        owner = api.login("traceowner")
        other = api.login("traceother")
        trace_id = api.insert_trace("trace-session-a", owner.user_id, _SECRET)

        # 列表：看不到别人的行
        resp = api.request("GET", "/agent/traces", other)
        assert resp.status_code == 200, resp.text
        assert trace_id not in [t["id"] for t in resp.json()["traces"]]
        assert _SECRET not in resp.text

        # 详情 / 单条导出：一律 404（不是 403，避免泄漏「这个 id 存在」）
        assert api.request("GET", f"/agent/traces/{trace_id}", other).status_code == 404
        assert api.request("GET", f"/agent/traces/{trace_id}/export", other).status_code == 404

        # 选中导出 / 全量导出：都不能带上别人的行
        resp = api.request("POST", "/agent/traces/export", other, {"trace_ids": [trace_id]})
        assert resp.status_code == 200, resp.text
        assert resp.json()["total"] == 0
        assert _SECRET not in resp.text

        resp = api.request("GET", "/agent/traces/export", other)
        assert resp.status_code == 200, resp.text
        assert resp.json()["total"] == 0
        assert _SECRET not in resp.text

        # 概览统计也不能把别人的 trace 算进来
        resp = api.request("GET", "/agent/traces/summary", other)
        assert resp.status_code == 200, resp.text
        assert resp.json()["total"] == 0

        # 删除：返回 deleted=0，且行仍在库里
        resp = api.request("DELETE", "/agent/traces", other, {"trace_ids": [trace_id]})
        assert resp.status_code == 200, resp.text
        assert resp.json()["deleted"] == 0
        assert api.trace_row(trace_id) is not None

        # 属主本人四个入口都正常
        resp = api.request("GET", "/agent/traces", owner)
        assert trace_id in [t["id"] for t in resp.json()["traces"]]
        assert api.request("GET", f"/agent/traces/{trace_id}", owner).status_code == 200
        assert api.request("GET", f"/agent/traces/{trace_id}/export", owner).status_code == 200
        resp = api.request("GET", "/agent/traces/export", owner)
        assert resp.json()["total"] == 1

    def test_trace_with_null_owner_hidden_from_everyone(self, api):
        """防回归：迁移前的历史 trace（user_id NULL）归属未知，必须对所有人不可见、不可删。"""
        user_a = api.login("nulltracea")
        user_b = api.login("nulltraceb")
        trace_id = api.insert_trace("trace-session-legacy", None, _SECRET)

        for user in (user_a, user_b):
            resp = api.request("GET", "/agent/traces", user)
            assert resp.status_code == 200, resp.text
            assert trace_id not in [t["id"] for t in resp.json()["traces"]]
            assert _SECRET not in resp.text

            assert api.request("GET", f"/agent/traces/{trace_id}", user).status_code == 404
            assert api.request("GET", f"/agent/traces/{trace_id}/export", user).status_code == 404

            resp = api.request("GET", "/agent/traces/export", user)
            assert resp.json()["total"] == 0
            assert _SECRET not in resp.text

            resp = api.request("DELETE", "/agent/traces", user, {"trace_ids": [trace_id]})
            assert resp.status_code == 200, resp.text
            assert resp.json()["deleted"] == 0

        assert api.trace_row(trace_id) is not None

    def test_trace_endpoints_reject_anonymous(self, api):
        """防回归：trace 端点必须登录才可用（匿名 401），不能靠省略 Authorization 绕过归属过滤。"""
        trace_id = api.insert_trace("trace-session-anon", None, _SECRET)

        assert api.request("GET", "/agent/traces").status_code == 401
        assert api.request("GET", f"/agent/traces/{trace_id}").status_code == 401
        assert api.request("GET", f"/agent/traces/{trace_id}/export").status_code == 401
        assert api.request("GET", "/agent/traces/export").status_code == 401
        assert api.request("GET", "/agent/traces/summary").status_code == 401
        assert api.request("POST", "/agent/traces/export", None, {"trace_ids": [trace_id]}).status_code == 401
        assert api.request("DELETE", "/agent/traces", None, {"trace_ids": [trace_id]}).status_code == 401

        # 会话端点同理：匿名不能读别人的消息，也不能删
        assert api.request("GET", "/agent/session/whatever/messages").status_code == 401
        assert api.request("DELETE", "/agent/session/whatever").status_code == 401
        assert api.request("POST", "/agent/session").status_code == 401


# ============================================================
# 4. session_id 超长（>128）走请求校验 422
# ============================================================

class TestSessionIdLengthGuard:
    def test_overlong_session_id_is_rejected_with_422(self, api):
        """防回归：超长 session_id 必须在进业务/落库之前被 422 挡掉（长度上限是防线之一）。"""
        user = api.login("lengthuser")

        resp = api.request("GET", f"/agent/session/{_LONG_SID}/messages", user)
        assert resp.status_code == 422, resp.text

        resp = api.request("DELETE", f"/agent/session/{_LONG_SID}", user)
        assert resp.status_code == 422, resp.text

        resp = api.request("GET", f"/agent/debug/trace?session_id={_LONG_SID}", user)
        assert resp.status_code == 422, resp.text

        resp = api.request("POST", "/agent/chat", user, {"session_id": _LONG_SID, "message": "hi"})
        assert resp.status_code == 422, resp.text

        # 边界：恰好 128 字符不触发 422（会话不存在 → 404）
        boundary_sid = "s" * main_module.MAX_SESSION_ID_LENGTH
        resp = api.request("GET", f"/agent/session/{boundary_sid}/messages", user)
        assert resp.status_code == 404, resp.text
