"""
Session management for AgenticRAG.
In-memory session store with TTL cleanup and SQLite persistence.
"""

import json
import time
import uuid
import asyncio
import logging
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
        self._session_locks: Dict[str, asyncio.Lock] = {}
        self._max_sessions = max_sessions
        self._ttl = ttl_seconds
        self._lock = asyncio.Lock()
        self._cleanup_interval = 300  # Clean up every 5 minutes
        self._last_cleanup = time.time()

    async def create_session(self) -> str:
        """Create a new session and return its ID."""
        # Periodic cleanup
        await self._maybe_cleanup()

        async with self._lock:
            # Evict least-recently-active if at capacity
            if len(self._sessions) >= self._max_sessions:
                oldest_id = min(self._sessions, key=lambda k: self._sessions[k].last_active)
                del self._sessions[oldest_id]
                self._session_locks.pop(oldest_id, None)
                self._evict_web_search_seen(oldest_id)
                logger.info(f"[SESSION] Evicted oldest session: {oldest_id}")

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
                del self._sessions[session_id]
                self._session_locks.pop(session_id, None)
                self._evict_web_search_seen(session_id)
                logger.warning(f"[SESSION] Expired: {session_id} (idle={idle:.0f}s, ttl={self._ttl}s)")
                return None

            session.last_active = time.time()
            logger.debug(f"[SESSION] Found: {session_id} (idle={idle:.0f}s, messages={len(session.messages)})")
            return session

    async def get_session_lock(self, session_id: str) -> asyncio.Lock:
        """Return a per-session lock used to serialize concurrent agent requests."""
        async with self._lock:
            lock = self._session_locks.get(session_id)
            if lock is None:
                lock = asyncio.Lock()
                self._session_locks[session_id] = lock
            return lock

    async def delete_session(self, session_id: str):
        """Delete a session from memory and the persistent session store."""
        async with self._lock:
            self._sessions.pop(session_id, None)
            self._session_locks.pop(session_id, None)
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
                    "INSERT OR REPLACE INTO agent_session_store "
                    "(session_id, messages, summary, summary_up_to_turn, last_active, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        session.session_id,
                        messages_json,
                        session.summary,
                        session.summary_up_to_turn,
                        session.last_active,
                        session.created_at,
                    ),
                )
                await db.commit()
            finally:
                await db.close()
        except Exception as exc:
            logger.warning(f"[SESSION] Failed to persist session {session.session_id}: {exc}")

    async def restore_session(self, session_id: str) -> Optional[Session]:
        """Restore a session from SQLite into the in-memory store.

        Returns None when no row exists.  The restored session is reused with
        the same session id and does not go through TTL expiry immediately.
        """
        async with self._lock:
            existing = self._sessions.get(session_id)
            if existing is not None:
                return existing

        try:
            from backend.db import get_db
            db = await get_db()
            try:
                cursor = await db.execute(
                    "SELECT session_id, messages, summary, summary_up_to_turn, "
                    "last_active, created_at FROM agent_session_store WHERE session_id=?",
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

        session = Session(
            session_id=row["session_id"],
            created_at=float(row["created_at"] or time.time()),
            last_active=time.time(),
            messages=messages,
            summary=row["summary"] or "",
            summary_up_to_turn=int(row["summary_up_to_turn"] or 0),
        )

        async with self._lock:
            self._sessions[session_id] = session
            self._session_locks.setdefault(session_id, asyncio.Lock())

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

        self._last_cleanup = now
        expired = []
        async with self._lock:
            for sid, session in self._sessions.items():
                if now - session.last_active > self._ttl:
                    expired.append(sid)
            for sid in expired:
                del self._sessions[sid]
                self._session_locks.pop(sid, None)

        if expired:
            for sid in expired:
                self._evict_web_search_seen(sid)
            logger.info(f"Cleaned up {len(expired)} expired sessions")

    @staticmethod
    def _evict_web_search_seen(session_id: str):
        """Remove web search dedup state for a session."""
        try:
            from backend.agent.tool_implementations import clear_web_search_seen
            clear_web_search_seen(session_id)
        except ImportError:
            pass

    async def get_active_count(self) -> int:
        """Return number of active sessions."""
        async with self._lock:
            return len(self._sessions)
