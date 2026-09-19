"""
Session management for AgenticRAG.
In-memory session store with TTL cleanup and SQLite persistence.
"""

import json
import time
import uuid
import asyncio
import logging
import weakref
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any

logger = logging.getLogger(__name__)


def clean_messages_for_llm(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Clean a message list for LLM API consumption.

    Strips non-standard fields (prefixed with ``_``) and removes orphaned
    tool_calls/tool_results that may occur when a streaming request is
    interrupted mid-way.  Original tool_call IDs are kept intact for valid
    assistant/tool pairs.
    """
    messages = messages or []

    # ===== Pre-pass: identify valid tool_call IDs =====
    assistant_tc_ids = set()
    for msg in messages:
        if msg.get("role") == "assistant" and msg.get("tool_calls"):
            for tc in msg["tool_calls"]:
                if tc.get("id"):
                    assistant_tc_ids.add(tc["id"])

    result_tc_ids = set()
    for msg in messages:
        if msg.get("role") == "tool" and msg.get("tool_call_id"):
            result_tc_ids.add(msg["tool_call_id"])

    # Find orphaned IDs (exist in one side but not the other)
    orphan_assistant_ids = assistant_tc_ids - result_tc_ids
    orphan_result_ids = result_tc_ids - assistant_tc_ids

    if orphan_assistant_ids or orphan_result_ids:
        logger.warning(
            f"[SESSION] Orphaned tool IDs detected: "
            f"assistant_without_result={orphan_assistant_ids}, "
            f"result_without_assistant={orphan_result_ids}. "
            f"Cleaning up (likely from interrupted request)."
        )

    # ===== Clean pass: remove orphaned entries, keep original IDs =====
    clean = []
    for msg in messages:
        clean_msg = {k: v for k, v in msg.items() if not k.startswith("_")}

        if clean_msg.get("role") == "assistant" and clean_msg.get("tool_calls"):
            # Filter out orphaned tool_calls
            remaining_tcs = [
                tc for tc in clean_msg["tool_calls"]
                if tc.get("id", "") not in orphan_assistant_ids
            ]
            if remaining_tcs:
                clean_msg["tool_calls"] = remaining_tcs
            else:
                # All tool_calls were orphaned — downgrade to plain assistant message
                del clean_msg["tool_calls"]

        if clean_msg.get("role") == "tool" and clean_msg.get("tool_call_id"):
            if clean_msg["tool_call_id"] in orphan_result_ids:
                logger.debug(f"[SESSION] Dropping orphaned tool result for id={clean_msg['tool_call_id']}")
                continue  # Skip this message entirely

        clean.append(clean_msg)

    return clean


@dataclass
class Session:
    """A conversation session with full message history."""
    session_id: str
    created_at: float = field(default_factory=time.time)
    last_active: float = field(default_factory=time.time)
    messages: List[Dict[str, Any]] = field(default_factory=list)
    summary: str = ""
    summary_up_to_turn: int = 0
    # 会话属主（T01）：随 agent_session_store.user_id 一起持久化，
    # None 表示「归属未知」（历史数据或尚未绑定），读取侧按拒绝处理。
    owner_id: Optional[int] = None

    def add_message(self, role: str, content: str = "", **kwargs):
        """Add a message to the session history."""
        msg = {"role": role, "content": content, **kwargs}
        self.messages.append(msg)

    def add_assistant_tool_calls(self, tool_calls: list, content: str = "", reasoning_content: str = ""):
        """Add an assistant message with tool_calls.

        The reasoning_content is stored in the message for DeepSeek V4 Flash API compatibility.
        """
        tc_list = []
        for tc in tool_calls:
            tc_list.append({
                "id": tc.id,
                "type": "function",
                "function": {
                    "name": tc.name,
                    "arguments": tc.arguments,
                }
            })
        msg = {
            "role": "assistant",
            "content": content,
            "tool_calls": tc_list,
        }
        # Store reasoning_content (needs to be passed back to DeepSeek V4 Flash API)
        if reasoning_content:
            msg["reasoning_content"] = reasoning_content
        self.messages.append(msg)

    def add_tool_result(self, tool_call_id: str, result: Any):
        """Add a tool result message."""
        # Serialize result to string if not already
        if isinstance(result, str):
            content = result
        else:
            try:
                content = json.dumps(result, ensure_ascii=False)
            except (TypeError, ValueError):
                content = str(result)
        
        self.messages.append({
            "role": "tool",
            "tool_call_id": tool_call_id,
            "content": content,
        })

    def get_context_messages(self, max_messages: int = 20) -> List[Dict]:
        """Get recent N messages as context for LLM.

        Strips any non-standard fields (prefixed with _) before sending to the API.

        Keeps original tool_call_ids intact — providers require the
        exact IDs they generated in previous turns to match tool results.

        Handles orphaned tool_calls/tool_results that may occur when a streaming
        request is interrupted mid-way (e.g. client aborts while tools are executing).
        """
        messages = self.messages[-max_messages:]
        return clean_messages_for_llm(messages)


class SessionManager:
    """In-memory session store with TTL-based cleanup."""

    def __init__(self, max_sessions: int = 1000, ttl_seconds: int = 3600):
        self._sessions: Dict[str, Session] = {}
        # 每会话锁。**不变量：同一个 session_id 在同一时刻至多只有一把「可用」的锁。**
        #
        # 用 WeakValueDictionary 而不是普通 dict：管理器自己*不*持有强引用，
        # 锁对象的存活期严格等于「还有调用方可能使用它」的存活期 —— 调用方无论
        # 处在「已 acquire 的临界区」还是「刚取出、尚未 acquire」的窗口，都必然
        # 持有这把锁的强引用（否则它连 acquire/release 都调不到）。
        #   * 只要还有人持有 → 条目活着 → get_session_lock 必然返回同一对象，互斥成立；
        #   * 所有引用都消失 → 条目自动被回收 → 字典有界（条目数 ≤ 真正在用的锁数），
        #     因此不再需要、也**绝不允许**任何「主动回收锁」的逻辑。
        # 旧实现（_prune_stale_locks_locked）会在容量超限时删掉「已取出未 acquire」
        # 的锁，让同一会话出现两把锁、两个协程同时进入临界区（T07 复审缺陷 1）。
        self._session_locks: "weakref.WeakValueDictionary[str, asyncio.Lock]" = (
            weakref.WeakValueDictionary()
        )
        self._max_sessions = max_sessions
        self._ttl = ttl_seconds
        self._lock = asyncio.Lock()
        self._cleanup_interval = 300  # Clean up every 5 minutes
        self._last_cleanup = time.time()
        self._lock_pressure_logged = False  # 锁表超限的 warning 去重（见 _note_lock_pressure_locked）

    def _is_in_use_locked(self, session_id: str) -> bool:
        """该会话是否仍被并发请求持有。调用方必须持有 ``self._lock``。

        判定依据是「这把锁的对象是否还活着」：管理器只保留弱引用，条目存活
        ⇔ 仍有调用方拿着它。已 acquire（临界区中）与「刚取出、尚未 acquire」
        这两种情况都算在用，都不允许把会话从 ``_sessions`` 里删掉。
        """
        return session_id in self._session_locks

    def _is_lock_held_locked(self, session_id: str) -> bool:
        """该会话的锁是否正处在 acquire 状态（有人正在临界区里）。"""
        lock = self._session_locks.get(session_id)
        return lock is not None and lock.locked()

    def _evict_oldest_locked(self) -> Optional[str]:
        """驱逐最久未活动、且**可以安全丢弃**的会话。调用方必须持有 ``self._lock``。

        返回被驱逐的 session_id；没有可驱逐对象时返回 None。

        「可以安全丢弃」= 该会话的锁已经没人持有。**绝不驱逐正在使用的会话**：
        ``agent_loop`` 整轮都持锁（backend/agent/core.py:714），中途把它从内存
        删掉会让收尾的 ``get_session`` 返回 None，这一轮消息再也不会落库
        （core.py:729）；随后 ``restore_session`` 还会为同一个 id 造出第二个
        ``Session`` 对象，第一个对象上追加的消息全部变成孤儿（复审缺陷 2/3）。

        两级候选：
        1. 锁对象已消失的会话（完全空闲）—— 优先按 LRU 驱逐；
        2. 兜底：锁对象虽存活但*未* acquire、且会话已过 TTL 的会话（被遗弃的
           取锁窗口），避免这类残留让内存里的会话长期超过 ``max_sessions``。
        两级都为空说明所有会话都在使用中：此时不强行驱逐，宁可让
        ``_sessions`` 短暂超过 ``max_sessions``，也不能丢消息。
        """
        now = time.time()
        idle = [sid for sid in self._sessions if not self._is_in_use_locked(sid)]
        if not idle:
            # 兜底：锁虽还在（有人取出过），但既没 acquire、会话又已过 TTL，
            # 属于被遗弃的取锁窗口，可以回收，避免内存会话表长期超限。
            idle = [
                sid for sid in self._sessions
                if not self._is_lock_held_locked(sid)
                and self._is_in_use_locked(sid)
                and now - self._sessions[sid].last_active > self._ttl
            ]
        if not idle:
            logger.warning(
                "[SESSION] At capacity (%d/%d) but every session is in use; "
                "skipping eviction to avoid dropping in-flight messages",
                len(self._sessions), self._max_sessions,
            )
            return None

        oldest_id = min(idle, key=lambda k: self._sessions[k].last_active)
        del self._sessions[oldest_id]
        # 注意：这里不删 ``_session_locks[oldest_id]`` —— 目标会话的锁已无人
        # 持有（条目早已自动回收），而删除活锁会破坏「同 id 只有一把锁」的不变量。
        self._evict_web_search_seen(oldest_id)
        logger.info(f"[SESSION] Evicted oldest idle session: {oldest_id}")
        return oldest_id

    async def create_session(self) -> str:
        """Create a new session and return its ID."""
        # Periodic cleanup
        await self._maybe_cleanup()

        async with self._lock:
            # Evict least-recently-active *idle* session if at capacity
            # (在用会话不会被驱逐，见 _evict_oldest_locked)
            if len(self._sessions) >= self._max_sessions:
                self._evict_oldest_locked()

            session_id = str(uuid.uuid4())
            self._sessions[session_id] = Session(session_id=session_id)
            logger.info(f"[SESSION] Created: {session_id} (total: {len(self._sessions)})")
            return session_id

    async def get_session(self, session_id: str) -> Optional[Session]:
        """Get a session by ID. Returns None if not found or expired."""
        async with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                logger.warning(f"[SESSION] Not found: {session_id}")
                return None

            # Sliding TTL: expire based on last activity, not creation time,
            # so an actively-used session is never killed mid-conversation
            idle = time.time() - session.last_active
            if idle > self._ttl:
                if self._is_in_use_locked(session_id):
                    # 正在被使用的会话不做过期回收：请求还没结束，中途删掉会让
                    # 这一轮消息丢失（core.py:729 的收尾 get_session 会拿到 None）。
                    # 这里只刷新活跃时间，等锁释放后再按 TTL 正常过期。
                    logger.warning(
                        "[SESSION] Idle %.0fs exceeds ttl=%ss but session is in use, "
                        "deferring expiry: %s", idle, self._ttl, session_id,
                    )
                else:
                    del self._sessions[session_id]
                    self._evict_web_search_seen(session_id)
                    logger.warning(f"[SESSION] Expired: {session_id} (idle={idle:.0f}s, ttl={self._ttl}s)")
                    return None

            session.last_active = time.time()
            logger.debug(f"[SESSION] Found: {session_id} (idle={idle:.0f}s, messages={len(session.messages)})")
            return session

    async def get_session_lock(self, session_id: str) -> asyncio.Lock:
        """Return a per-session lock used to serialize concurrent agent requests.

        不变量：**同一个 session_id 在同一时刻至多只有一把「可用」的锁**，
        因此并发请求必然拿到同一个对象、互斥成立（见 ``__init__`` 的说明）。

        锁按 session_id 缓存在 ``WeakValueDictionary`` 中：只要还有调用方持有
        这把锁（已 acquire 的临界区，或刚取出、尚未 acquire 的窗口），条目就
        还在，这里必定命中同一对象；全部引用消失后条目自动回收，字典因此有界
        （条目数 ≤ 真正在用的锁数），不需要任何容量驱动的回收。
        这里**绝不**主动删锁：删掉一把「已取出未 acquire」的锁会让同一会话
        出现两把锁、两个协程同时进入临界区（正是 T07 复审缺陷 1）。
        """
        async with self._lock:
            lock = self._session_locks.get(session_id)
            if lock is None:
                lock = asyncio.Lock()
                self._session_locks[session_id] = lock
                self._note_lock_pressure_locked()
            return lock

    def _note_lock_pressure_locked(self) -> None:
        """观测锁表规模。调用方必须持有 ``self._lock``。

        锁的唯一存活条件是「还有调用方持有」，因此正常情况下条目数 ≈ 并发
        请求数，远低于 ``max_sessions``。一旦长期超过，说明有调用方用完不释放
        引用（泄漏）—— 这时**不能**强行回收（回收 = 同一会话出现两把锁），
        只能把它暴露出来，所以这里记一条 warning（跨过阈值时只记一次）。
        """
        over = len(self._session_locks) > self._max_sessions
        if over and not self._lock_pressure_logged:
            self._lock_pressure_logged = True
            logger.warning(
                "[SESSION] %d live session locks exceed max_sessions=%d; "
                "a caller is probably holding lock references after use",
                len(self._session_locks), self._max_sessions,
            )
        elif not over:
            self._lock_pressure_logged = False

    async def delete_session(self, session_id: str):
        """Delete a session from memory and the persistent session store."""
        async with self._lock:
            self._sessions.pop(session_id, None)
            # 不删 `_session_locks[session_id]`：若还有请求正在使用这把锁，
            # 删掉条目会让新请求拿到一把新锁、与该请求同时进入临界区。
            # 无人持有的条目由 WeakValueDictionary 自动回收。
            logger.info(f"Deleted session: {session_id}")
        self._evict_web_search_seen(session_id)

        try:
            from backend.db import get_db
            db = await get_db()
            try:
                await db.execute(
                    "DELETE FROM agent_session_store WHERE session_id=?",
                    (session_id,),
                )
                await db.commit()
            finally:
                await db.close()
        except Exception as exc:
            logger.warning(f"[SESSION] Failed to delete persisted session {session_id}: {exc}")

    async def persist_session(self, session: Session):
        """Upsert the full Session into the SQLite agent_session_store.

        The complete JSON transcript is persisted; nothing is truncated.
        Failures are logged and swallowed so persistence never breaks a request.

        ``user_id`` 冲突时保留库中已有的归属（``COALESCE``）：归属只能从
        NULL 变为某个用户，永不被后来的写入改写。
        """
        try:
            messages_json = json.dumps(session.messages, ensure_ascii=False, default=str)
        except Exception as exc:
            logger.warning(f"[SESSION] Failed to serialize session {session.session_id}: {exc}")
            return

        try:
            from backend.db import get_db
            db = await get_db()
            try:
                await db.execute(
                    "INSERT INTO agent_session_store "
                    "(session_id, messages, summary, summary_up_to_turn, last_active, created_at, user_id) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(session_id) DO UPDATE SET "
                    "messages=excluded.messages, summary=excluded.summary, "
                    "summary_up_to_turn=excluded.summary_up_to_turn, "
                    "last_active=excluded.last_active, created_at=excluded.created_at, "
                    "user_id=COALESCE(agent_session_store.user_id, excluded.user_id)",
                    (
                        session.session_id,
                        messages_json,
                        session.summary,
                        session.summary_up_to_turn,
                        session.last_active,
                        session.created_at,
                        session.owner_id,
                    ),
                )
                await db.commit()
            finally:
                await db.close()
        except Exception as exc:
            logger.warning(f"[SESSION] Failed to persist session {session.session_id}: {exc}")

    async def get_persisted_owner(self, session_id: str) -> Optional[int]:
        """读取持久化的会话属主；无记录或归属未知（NULL）时返回 None。"""
        try:
            from backend.db import get_db
            db = await get_db()
            try:
                cursor = await db.execute(
                    "SELECT user_id FROM agent_session_store WHERE session_id=?",
                    (session_id,),
                )
                row = await cursor.fetchone()
            finally:
                await db.close()
        except Exception as exc:
            logger.warning(f"[SESSION] Failed to read owner of {session_id}: {exc}")
            return None

        if row is None or row["user_id"] is None:
            return None
        try:
            return int(row["user_id"])
        except (TypeError, ValueError):
            return None

    async def bind_persisted_owner(self, session_id: str, user_id: int) -> bool:
        """把会话归属写入持久层，返回「写入后库中归属是否等于 user_id」。

        只允许从「无归属」变为「有归属」：已归属他人的会话不会被改写
        （``COALESCE`` 保留原值），因此这是幂等的、不可抢占的绑定。
        会话尚无持久化行时插入一条空会话行，这样同一次请求里随后写入的
        trace 也能按 session 查到属主。

        与模块内其它持久化方法不同，这里**不吞异常**：调用方需要区分
        「归属被他人占用」（返回 False）与「库暂时不可用」（抛异常，
        调用方可退化为进程内绑定，重启后该会话退回归属未知 → 拒绝访问）。
        """
        now = time.time()
        from backend.db import get_db
        db = await get_db()
        try:
            await db.execute(
                "INSERT INTO agent_session_store "
                "(session_id, messages, summary, summary_up_to_turn, last_active, created_at, user_id) "
                "VALUES (?, '[]', '', 0, ?, ?, ?) "
                "ON CONFLICT(session_id) DO UPDATE SET "
                "user_id=COALESCE(agent_session_store.user_id, excluded.user_id)",
                (session_id, now, now, user_id),
            )
            await db.commit()
            cursor = await db.execute(
                "SELECT user_id FROM agent_session_store WHERE session_id=?",
                (session_id,),
            )
            row = await cursor.fetchone()
        finally:
            await db.close()

        return bool(row) and row["user_id"] == user_id

    async def restore_session(self, session_id: str) -> Optional[Session]:
        """Restore a session from SQLite into the in-memory store.

        Returns None when no row exists.  The restored session is reused with
        the same session id and does not go through TTL expiry immediately.

        The in-memory store is re-checked *inside* the lock right before the
        write: ``/agent/chat`` and ``/agent/session/{id}/messages`` may restore
        the same id concurrently, and without that check the second writer
        would replace the first ``Session`` object — the first caller would then
        keep appending to an orphaned object and its messages would be lost.
        The write path also honours ``max_sessions`` (LRU eviction) so restoring
        historical ids cannot grow the store without bound; if every session is
        currently in use the eviction is skipped instead of dropping in-flight
        state (see ``_evict_oldest_locked``).
        """
        async with self._lock:
            existing = self._sessions.get(session_id)
            if existing is not None:
                return existing

        # 读库放在锁外，避免慢查询阻塞其它会话操作
        try:
            from backend.db import get_db
            db = await get_db()
            try:
                cursor = await db.execute(
                    "SELECT session_id, messages, summary, summary_up_to_turn, "
                    "last_active, created_at, user_id FROM agent_session_store WHERE session_id=?",
                    (session_id,),
                )
                row = await cursor.fetchone()
            finally:
                await db.close()
        except Exception as exc:
            logger.warning(f"[SESSION] Failed to restore session {session_id}: {exc}")
            return None

        if row is None:
            logger.info(f"[SESSION] No persisted session found: {session_id}")
            return None

        try:
            messages = json.loads(row["messages"])
        except Exception as exc:
            logger.warning(f"[SESSION] Corrupt persisted messages for {session_id}: {exc}")
            return None

        try:
            owner_id = int(row["user_id"]) if row["user_id"] is not None else None
        except (TypeError, ValueError, IndexError):
            owner_id = None

        session = Session(
            session_id=row["session_id"],
            created_at=float(row["created_at"] or time.time()),
            last_active=time.time(),
            messages=messages,
            summary=row["summary"] or "",
            summary_up_to_turn=int(row["summary_up_to_turn"] or 0),
            owner_id=owner_id,
        )

        async with self._lock:
            # 二次确认：并发恢复可能已经写入同一个 session_id，直接复用先写入的
            # 对象，避免后写者覆盖先写者造成消息丢失（check-then-act 竞态）。
            existing = self._sessions.get(session_id)
            if existing is not None:
                logger.info(f"[SESSION] Concurrent restore, reusing in-memory session: {session_id}")
                return existing

            # 恢复路径同样受 max_sessions 约束（复用 create_session 的 LRU 驱逐；
            # 若所有会话都在使用中，驱逐会被跳过而不会丢在途消息）
            if len(self._sessions) >= self._max_sessions:
                self._evict_oldest_locked()

            self._sessions[session_id] = session
            # 这里不预建锁：WeakValueDictionary 中无人持有的锁会被立刻回收，
            # 预建没有意义；get_session_lock 会按需创建并复用同一把锁。

        logger.info(
            "[SESSION] Restored from SQLite: %s (messages=%d, summary_up_to_turn=%d)",
            session_id, len(messages), session.summary_up_to_turn,
        )
        return session

    async def _maybe_cleanup(self):
        """Periodically clean up expired sessions."""
        now = time.time()
        if now - self._last_cleanup < self._cleanup_interval:
            return

        expired = []
        async with self._lock:
            # 判断与赋值都在锁内，避免并发调用重复执行清理
            if now - self._last_cleanup < self._cleanup_interval:
                return
            self._last_cleanup = now
            for sid, session in self._sessions.items():
                # 在用会话不清理（与 get_session 的 TTL 判断一致）：
                # 删掉正在处理中的会话会让这一轮消息丢失。
                if now - session.last_active > self._ttl and not self._is_in_use_locked(sid):
                    expired.append(sid)
            for sid in expired:
                del self._sessions[sid]

        if expired:
            for sid in expired:
                self._evict_web_search_seen(sid)
            logger.info(f"Cleaned up {len(expired)} expired sessions")

    @staticmethod
    def _evict_web_search_seen(session_id: str):
        """Remove web search dedup state for a session.

        尽力而为的清理动作：任何异常都不能冒泡出去（它在 ``self._lock``
        临界区内被调用，抛出会让 get_session/create_session 整体失败）。
        """
        try:
            from backend.agent.tool_implementations import clear_web_search_seen
            clear_web_search_seen(session_id)
        except ImportError:
            pass
        except Exception as exc:
            logger.warning(f"[SESSION] Failed to clear web search state for {session_id}: {exc}")

    async def get_active_count(self) -> int:
        """Return number of active sessions."""
        async with self._lock:
            return len(self._sessions)
