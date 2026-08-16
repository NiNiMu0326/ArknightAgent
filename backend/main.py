"""
Arknights RAG Backend - FastAPI Server
Provides REST API for the frontend
"""
import asyncio
import sys
import os
import json
import random
import time
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Dict, Any

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Request, Depends, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, field_validator
from starlette.types import ASGIApp, Scope, Receive, Send, Message
import uvicorn

from backend import config  # 必须在 auth 之前导入，以加载 .env

from backend.db import get_db, init_db
from backend.auth import (
    validate_account, validate_username, validate_password,
    hash_password, verify_password, create_jwt, decode_jwt
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("arknights_rag")

# 请求日志中需要脱敏的字段（防止密码/令牌进入日志）
_SENSITIVE_LOG_FIELDS = {
    "password", "old_password", "new_password", "confirm_password",
    "token", "authorization", "api_key",
}


def _redact_sensitive(value: Any) -> Any:
    """递归脱敏请求体，避免敏感字段写入日志。"""
    if isinstance(value, dict):
        return {
            k: "***" if k.lower() in _SENSITIVE_LOG_FIELDS else _redact_sensitive(v)
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [_redact_sensitive(item) for item in value]
    return value

from backend.config import (
    CHUNKS_DIR, DATA_DIR,
    ENTITY_RELATIONS_FILE,
)

# Import AgenticRAG components
from backend.agent.sessions import SessionManager
from backend.agent.core import agent_loop
from backend.api.llm_factory import get_available_models, DEFAULT_MODEL
from backend.agent.tools import get_tool_registry
from backend.quick_questions import (
    STRUCTURED_TEMPLATES,
    PRTS_MCP_TEMPLATES,
    pick_template,
    pick_rag_question,
    make_artwork_question,
    make_stage_enemies_question,
    make_stage_item_question,
    pick_unique_questions,
)

# ============== Lifespan ==============

TRACE_RETENTION_DAYS = 30  # 本地 trace 保留时长，超期自动清理

_mcp_manager: Optional[Any] = None
_mcp_registered_count = 0


async def _start_prts_mcp():
    """Start prts-mcp and register allowlisted tools. Failure is non-fatal."""
    global _mcp_manager, _mcp_registered_count
    if not config.PRTS_MCP_ENABLED:
        logger.info("[MCP] PRTS_MCP_ENABLED=false, skipping MCP startup")
        return

    # 延迟导入：mcp 依赖 Python>=3.10，未安装时也要允许 PRTS_MCP_ENABLED=false 启动
    from backend.agent.mcp_client import McpClientManager, register_mcp_tools

    mcp_env = {
        "PRTS_OUTPUT_CHANNEL": "both",
        "LOCAL_IMAGE": "false",
        "PRTS_IMAGE_CACHE": "true",
        "IMAGES_ENABLED": "true",
    }
    # stdio 子进程默认只继承白名单环境变量；显式透传 GitHub 访问相关配置
    # （GITHUB_MIRRORS 用于 GitHub Release 数据同步走镜像，GITHUB_TOKEN 用于提高限流额度）
    for key in ("GITHUB_MIRRORS", "GITHUB_TOKEN"):
        value = os.environ.get(key)
        if value:
            mcp_env[key] = value

    manager = McpClientManager(
        command=config.PRTS_MCP_COMMAND,
        env=mcp_env,
        connect_timeout=config.PRTS_MCP_CONNECT_TIMEOUT,
        call_timeout=config.PRTS_MCP_CALL_TIMEOUT,
    )
    try:
        await manager.start()
        count = register_mcp_tools(get_tool_registry(), manager)
        _mcp_registered_count = count
        _mcp_manager = manager
        logger.info(f"[MCP] prts-mcp ready: {count} tools registered")
    except Exception as exc:
        manager.last_error = manager.last_error or str(exc)
        if manager.connected:
            try:
                await manager.close()
            except Exception as close_exc:
                logger.warning(f"[MCP] close after registration failure failed: {close_exc}")
        _mcp_registered_count = 0
        _mcp_manager = manager  # 保留对象，/status 可读到 last_error
        logger.warning(f"[MCP] prts-mcp unavailable, continuing without MCP tools: {exc}")


async def _stop_prts_mcp():
    global _mcp_manager
    if _mcp_manager is not None:
        await _mcp_manager.close()
        _mcp_manager = None


def _mcp_status() -> dict:
    if not config.PRTS_MCP_ENABLED:
        return {"enabled": False, "connected": False, "tool_count": 0}
    if _mcp_manager is not None and _mcp_manager.connected:
        return {"enabled": True, "connected": True, "tool_count": _mcp_registered_count}
    error = _mcp_manager.last_error if _mcp_manager is not None else "not started"
    return {"enabled": True, "connected": False, "tool_count": 0, "error": error}


async def _cleanup_old_traces():
    """删除超过保留期的本地 traces（trace 是运维观测数据，生命周期独立于会话）。"""
    try:
        db = await get_db()
        try:
            cursor = await db.execute(
                "DELETE FROM traces WHERE created_at < datetime('now', ?)",
                (f"-{TRACE_RETENTION_DAYS} days",)
            )
            await db.commit()
            deleted = cursor.rowcount
        finally:
            await db.close()
        if deleted:
            logger.info(f"[TRACE] Retention cleanup: deleted {deleted} traces older than {TRACE_RETENTION_DAYS} days")
    except Exception as e:
        logger.warning(f"[TRACE] Retention cleanup failed: {e}")


async def _trace_retention_loop():
    """启动时清理一次，之后每 24 小时清理一次。"""
    while True:
        await _cleanup_old_traces()
        await asyncio.sleep(24 * 3600)


@asynccontextmanager
async def lifespan(app: FastAPI):
    if not config.SILICONFLOW_API_KEY:
        raise RuntimeError("SILICONFLOW_API_KEY 环境变量未设置，拒绝启动。请在 .env 中配置。")
    await init_db()
    retention_task = asyncio.create_task(_trace_retention_loop())
    await _start_prts_mcp()
    yield
    retention_task.cancel()
    await _stop_prts_mcp()


# ============== FastAPI App ==============
app = FastAPI(
    title="Arknights RAG API",
    description="Backend API for Arknights RAG System",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS — restrict origins in production via ALLOWED_ORIGINS env var (comma-separated)
_allowed_origins = os.environ.get("ALLOWED_ORIGINS", "http://localhost:5300,http://localhost:8100").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in _allowed_origins if o.strip()],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Request logging middleware (raw ASGI to avoid BaseHTTPMiddleware + SSE streaming conflict)
class RequestLoggingMiddleware:
    """Raw ASGI request/response logging middleware.

    Uses pure ASGI (not BaseHTTPMiddleware) to avoid the
    "RuntimeError: Unexpected message received" error
    when streaming responses (SSE) are used.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        start = time.time()
        method = scope.get("method", "")
        path = scope.get("path", "")
        req_id = id(scope)

        body_chunks: list[bytes] = []

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                # Build body info from collected chunks
                body_info = ""
                if body_chunks and method in ("POST", "PUT", "PATCH"):
                    body = b"".join(body_chunks)
                    try:
                        body_json = _redact_sensitive(json.loads(body))
                        if isinstance(body_json, dict):
                            log_body = {k: (v if len(str(v)) < 200 else str(v)[:200] + "...") for k, v in body_json.items()}
                        else:
                            log_body = body_json
                        body_info = f" body={json.dumps(log_body, ensure_ascii=False)}"
                    except Exception:
                        body_info = f" body_length={len(body)}"

                logger.info(f"[REQ #{req_id}] {method} {path}{body_info}")

                status_code = message.get("status", 0)
                elapsed = (time.time() - start) * 1000
                logger.info(f"[RES #{req_id}] {method} {path} -> {status_code} ({elapsed:.0f}ms)")

            await send(message)

        async def receive_wrapper() -> Message:
            message = await receive()
            if message["type"] == "http.request":
                body_chunks.append(message.get("body", b""))
            return message

        await self.app(scope, receive_wrapper, send_wrapper)


app.add_middleware(RequestLoggingMiddleware)

# AgenticRAG Session Manager (singleton)
_session_manager = SessionManager(max_sessions=1000, ttl_seconds=3600)


async def _build_tool_trace_from_session(session_id: str, include_ids: bool = False) -> List[Dict]:
    """从内存会话中重建工具调用链（trace 详情/导出/调试端点共用）。

    Args:
        session_id: 会话 ID
        include_ids: 是否包含 tool_call_id / id 字段（详情与调试用，导出文件不需要）
    """
    session = await _session_manager.get_session(session_id)
    if not session:
        return []
    tool_trace = []
    for msg in session.messages:
        if msg.get("role") == "assistant" and msg.get("tool_calls"):
            for tc in msg["tool_calls"]:
                fn = tc.get("function", {})
                entry: Dict[str, Any] = {
                    "type": "tool_call",
                    "name": fn.get("name", ""),
                    "arguments": fn.get("arguments", ""),
                }
                if include_ids:
                    entry["id"] = tc.get("id", "")
                tool_trace.append(entry)
        elif msg.get("role") == "tool":
            entry = {
                "type": "tool_result",
                "content": str(msg.get("content", ""))[:500],
            }
            if include_ids:
                entry["tool_call_id"] = msg.get("tool_call_id", "")
            tool_trace.append(entry)
    return tool_trace


async def _get_last_assistant_answer(session_id: str, max_len: int = 4000) -> str:
    """取会话中最后一条 assistant 文本消息（trace 详情用）。会话过期时返回空串。"""
    session = await _session_manager.get_session(session_id)
    if not session:
        return ""
    for msg in reversed(session.messages):
        if msg.get("role") == "assistant" and msg.get("content"):
            return str(msg["content"])[:max_len]
    return ""


# ===== Entity Relations Cache =====
# Cache the 116KB entity_relations.json in memory to avoid repeated disk reads.
# Both /knowledge-graph and /stats need this data, and it doesn't change at runtime.
_entity_relations_cache: Optional[Dict] = None

# ===== Quick Questions Cache =====
# Cache quick questions to avoid repeated file reads and graph traversal.
_quick_questions_cache: Optional[List] = None
_quick_questions_cache_time: float = 0
_quick_questions_cache_ttl: float = 300  # 5 minutes


def _load_entity_relations() -> Dict:
    """Load entity relations from JSON file, cached in memory."""
    global _entity_relations_cache
    if _entity_relations_cache is None:
        if ENTITY_RELATIONS_FILE.exists():
            with open(ENTITY_RELATIONS_FILE, "r", encoding="utf-8") as f:
                _entity_relations_cache = json.load(f)
        else:
            _entity_relations_cache = {"entities": {}, "relations": []}
    return _entity_relations_cache


# ===== AgenticRAG Request Models =====

class AgentChatRequest(BaseModel):
    """Request for agent chat endpoint."""
    session_id: str
    message: str
    model: Optional[str] = None

    @field_validator('message')
    @classmethod
    def message_not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError('message cannot be empty')
        return v.strip()



class ChunkInfo(BaseModel):
    filename: str
    name: str
    char_count: int
    lines: int
    tokens: int


class EntityRelationData(BaseModel):
    entities: Dict
    relations: List[Dict]


class StatsResponse(BaseModel):
    operators: int
    stories: int
    knowledge: int
    relations: int


# ============== API Endpoints ==============

@app.get("/api")
async def root():
    return {"message": "Arknights RAG API", "version": "1.0.0"}


@app.get("/health")
async def health():
    """Health check endpoint for Docker."""
    return {"status": "healthy"}


@app.get("/status")
async def status():
    """Get service health status."""
    return {
        "status": "healthy",
        "api_key_configured": bool(config.SILICONFLOW_API_KEY),
        "mcp": _mcp_status(),
        "embedding_model": config.EMBEDDING_MODEL,
        "reranker_model": config.RERANKER_MODEL,
        "llm_model": config.DEEPSEEK_LLM_MODEL or "not configured"
    }


@app.get("/chunks/{collection}", response_model=List[ChunkInfo])
async def list_chunks(collection: str):
    """List all chunks in a collection"""
    valid_collections = ["operators", "stories", "knowledge"]
    if collection not in valid_collections:
        raise HTTPException(status_code=400, detail=f"Invalid collection. Must be one of: {valid_collections}")

    collection_dir = CHUNKS_DIR / collection
    if not collection_dir.exists():
        raise HTTPException(status_code=404, detail=f"Collection '{collection}' not found")

    chunks = []
    for f in sorted(collection_dir.glob("*.md")) + sorted(collection_dir.glob("*.txt")):
        content = f.read_text(encoding="utf-8")
        char_count = len(content)
        line_count = len(content.split("\n"))
        tokens = int(char_count / 1.5)

        chunks.append(ChunkInfo(
            filename=f.name,
            name=f.stem,
            char_count=char_count,
            lines=line_count,
            tokens=tokens
        ))

    return chunks


@app.get("/chunks/{collection}/{filename}")
async def get_chunk(collection: str, filename: str):
    """Get content of a specific chunk"""
    valid_collections = ["operators", "stories", "knowledge"]
    if collection not in valid_collections:
        raise HTTPException(status_code=400, detail="Invalid collection")

    # 防止路径穿越：解析后的路径必须仍位于该 collection 目录内
    try:
        filepath = (CHUNKS_DIR / collection / filename).resolve()
        filepath.relative_to((CHUNKS_DIR / collection).resolve())
    except (ValueError, OSError):
        raise HTTPException(status_code=400, detail="Invalid filename")
    if not filepath.is_file():
        raise HTTPException(status_code=404, detail="File not found")

    content = filepath.read_text(encoding="utf-8")
    return {"filename": filename, "content": content}


@app.get("/knowledge-graph", response_model=EntityRelationData)
async def get_graph():
    """Get entity relations for knowledge graph (cached)."""
    data = _load_entity_relations()
    return EntityRelationData(
        entities=data.get("entities", {}),
        relations=data.get("relations", [])
    )


@app.get("/stats", response_model=StatsResponse)
async def get_stats():
    """Get system statistics"""
    stats = {
        "operators": 0,
        "stories": 0,
        "knowledge": 0,
        "relations": 0
    }

    # Count chunks
    for coll in ["operators", "stories", "knowledge"]:
        collection_dir = CHUNKS_DIR / coll
        if collection_dir.exists():
            stats[coll] = len(list(collection_dir.glob("*.md"))) + len(list(collection_dir.glob("*.txt")))

    # Count relations (cached)
    data = _load_entity_relations()
    stats["relations"] = len(data.get("relations", []))

    return StatsResponse(**stats)


# ============== Auth & Conversation Endpoints ==============

class RegisterRequest(BaseModel):
    account: str
    username: str
    password: str

class LoginRequest(BaseModel):
    account: str
    password: str

class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str

class SyncConversationsRequest(BaseModel):
    conversations: list


async def get_current_user(authorization: str = Header(None)):
    """Extract current user from JWT token in Authorization header.

    同时校验 token 中的 pw_changed_at 与数据库一致，实现修改密码后旧 token 失效。
    """
    if not authorization or not authorization.startswith("Bearer "):
        return None
    token = authorization[7:]
    payload = decode_jwt(token)
    if not payload:
        return None

    try:
        db = await get_db()
        try:
            cursor = await db.execute(
                "SELECT password_changed_at FROM users WHERE id = ?",
                (payload.get("user_id"),)
            )
            row = await cursor.fetchone()
            if not row or row["password_changed_at"] != payload.get("pw_changed_at"):
                return None
        finally:
            await db.close()
    except Exception as e:
        logger.warning(f"[AUTH] Token verification against DB failed: {e}")
        return None

    return payload


@app.post("/auth/register")
async def register(req: RegisterRequest):
    """Register a new user."""
    err = validate_account(req.account)
    if err:
        raise HTTPException(status_code=400, detail=err)
    err = validate_username(req.username)
    if err:
        raise HTTPException(status_code=400, detail=err)
    err = validate_password(req.password)
    if err:
        raise HTTPException(status_code=400, detail=err)

    db = await get_db()
    try:
        cursor = await db.execute("SELECT id FROM users WHERE account = ?", (req.account,))
        if await cursor.fetchone():
            raise HTTPException(status_code=400, detail="该账号已被注册")

        pw_hash = hash_password(req.password)
        # 注册时显式写入 password_changed_at，确保签发 token 里的 pw_changed_at
        # 与数据库一致（get_current_user 会校验该字段实现改密后旧 token 失效）
        password_changed_at = datetime.now(timezone.utc).isoformat()
        cursor = await db.execute(
            "INSERT INTO users (account, username, password_hash, password_changed_at) VALUES (?, ?, ?, ?)",
            (req.account, req.username.strip(), pw_hash, password_changed_at)
        )
        await db.commit()
        user_id = cursor.lastrowid

        token = create_jwt(user_id, req.account, req.username.strip(), password_changed_at)
        return {"token": token, "user": {"id": user_id, "account": req.account, "username": req.username.strip()}}
    finally:
        await db.close()


@app.post("/auth/login")
async def login(req: LoginRequest):
    """Login with account + password."""
    db = await get_db()
    try:
        cursor = await db.execute("SELECT id, account, username, password_hash, password_changed_at FROM users WHERE account = ?", (req.account,))
        row = await cursor.fetchone()
        if not row or not verify_password(req.password, row["password_hash"]):
            raise HTTPException(status_code=401, detail="账号或密码错误")

        token = create_jwt(row["id"], row["account"], row["username"], row["password_changed_at"])
        return {"token": token, "user": {"id": row["id"], "account": row["account"], "username": row["username"]}}
    finally:
        await db.close()


@app.get("/auth/me")
async def get_me(user: dict = Depends(get_current_user)):
    """Get current user info."""
    if not user:
        raise HTTPException(status_code=401, detail="未登录")
    return {"user": {"id": user["user_id"], "account": user["account"], "username": user["username"]}}


@app.post("/auth/change-password")
async def change_password(req: ChangePasswordRequest, user: dict = Depends(get_current_user)):
    """Change password. Invalidates JWT after change."""
    if not user:
        raise HTTPException(status_code=401, detail="未登录")

    db = await get_db()
    try:
        cursor = await db.execute("SELECT password_hash FROM users WHERE id = ?", (user["user_id"],))
        row = await cursor.fetchone()
        if not row or not verify_password(req.old_password, row["password_hash"]):
            raise HTTPException(status_code=400, detail="旧密码错误")

        err = validate_password(req.new_password)
        if err:
            raise HTTPException(status_code=400, detail=err)

        new_hash = hash_password(req.new_password)
        now = datetime.now(timezone.utc).isoformat()
        await db.execute("UPDATE users SET password_hash = ?, password_changed_at = ? WHERE id = ?", (new_hash, now, user["user_id"]))
        await db.commit()

        token = create_jwt(user["user_id"], user["account"], user["username"], now)
        return {"token": token}
    finally:
        await db.close()


@app.get("/conversations")
async def list_conversations(user: dict = Depends(get_current_user)):
    """List all conversations for the current user."""
    if not user:
        raise HTTPException(status_code=401, detail="未登录")
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT session_id, name, created_at, updated_at FROM conversations WHERE user_id = ? ORDER BY updated_at DESC",
            (user["user_id"],)
        )
        rows = await cursor.fetchall()
        return {"conversations": [dict(r) for r in rows]}
    finally:
        await db.close()


@app.get("/conversations/{session_id}/messages")
async def get_conversation_messages(session_id: str, user: dict = Depends(get_current_user)):
    """Get all messages for a conversation."""
    if not user:
        raise HTTPException(status_code=401, detail="未登录")
    db = await get_db()
    try:
        cursor = await db.execute("SELECT user_id FROM conversations WHERE session_id = ?", (session_id,))
        row = await cursor.fetchone()
        if not row or row["user_id"] != user["user_id"]:
            raise HTTPException(status_code=404, detail="会话不存在")
        cursor = await db.execute(
            "SELECT role, content, metadata, created_at FROM messages WHERE session_id = ? ORDER BY created_at",
            (session_id,)
        )
        messages = [dict(r) for r in await cursor.fetchall()]
        for m in messages:
            try:
                m["metadata"] = json.loads(m["metadata"]) if m["metadata"] else {}
            except Exception:
                m["metadata"] = {}
        return {"messages": messages}
    finally:
        await db.close()


@app.post("/conversations/sync")
async def sync_conversations(req: SyncConversationsRequest, user: dict = Depends(get_current_user)):
    """Sync (upsert) conversations from frontend. Incremental: skip existing messages."""
    if not user:
        raise HTTPException(status_code=401, detail="未登录")
    db = await get_db()
    try:
        for conv in req.conversations:
            sid = conv.get("session_id")
            if not sid:
                continue
            cursor = await db.execute(
                "SELECT user_id FROM conversations WHERE session_id = ?", (sid,)
            )
            existing = await cursor.fetchone()
            if not existing:
                await db.execute(
                    "INSERT INTO conversations (session_id, user_id, name, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                    (sid, user["user_id"], conv.get("name", ""), conv.get("created_at", ""), conv.get("updated_at", ""))
                )
            elif existing["user_id"] != user["user_id"]:
                # 不允许通过同步接口覆盖/注入其他用户的会话
                logger.warning(f"[SYNC] Skipped conversation owned by another user: {sid}")
                continue
            else:
                await db.execute(
                    "UPDATE conversations SET name = ?, updated_at = ? WHERE session_id = ?",
                    (conv.get("name", ""), conv.get("updated_at", ""), sid)
                )

            for msg in conv.get("messages", []):
                metadata_str = json.dumps(msg.get("metadata", {}), ensure_ascii=False)
                cursor = await db.execute(
                    "SELECT id FROM messages WHERE session_id = ? AND role = ? AND content = ? AND created_at = ?",
                    (sid, msg.get("role", ""), msg.get("content", ""), msg.get("created_at", ""))
                )
                if not await cursor.fetchone():
                    await db.execute(
                        "INSERT INTO messages (session_id, role, content, metadata, created_at) VALUES (?, ?, ?, ?, ?)",
                        (sid, msg.get("role", ""), msg.get("content", ""), metadata_str, msg.get("created_at", ""))
                    )
        await db.commit()
        return {"status": "ok"}
    finally:
        await db.close()


@app.delete("/conversations/{session_id}")
async def delete_conversation(session_id: str, user: dict = Depends(get_current_user)):
    """Delete a conversation and its messages."""
    if not user:
        raise HTTPException(status_code=401, detail="未登录")
    db = await get_db()
    try:
        cursor = await db.execute("SELECT user_id FROM conversations WHERE session_id = ?", (session_id,))
        row = await cursor.fetchone()
        if not row or row["user_id"] != user["user_id"]:
            raise HTTPException(status_code=404, detail="会话不存在")
        await db.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
        await db.execute("DELETE FROM conversations WHERE session_id = ?", (session_id,))
        # 注意：不删除 traces —— trace 是全局运维观测数据，生命周期独立于用户会话
        await db.commit()
        return {"status": "ok"}
    finally:
        await db.close()


class RenameRequest(BaseModel):
    name: str

    @field_validator('name')
    @classmethod
    def name_not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError('会话名称不能为空')
        return v.strip()


@app.put("/conversations/{session_id}/rename")
async def rename_conversation(session_id: str, req: RenameRequest, user: dict = Depends(get_current_user)):
    """Rename a conversation."""
    if not user:
        raise HTTPException(status_code=401, detail="未登录")
    db = await get_db()
    try:
        cursor = await db.execute("SELECT user_id FROM conversations WHERE session_id = ?", (session_id,))
        row = await cursor.fetchone()
        if not row or row["user_id"] != user["user_id"]:
            raise HTTPException(status_code=404, detail="会话不存在")
        await db.execute("UPDATE conversations SET name = ? WHERE session_id = ?", (req.name, session_id))
        await db.commit()
        return {"status": "ok"}
    finally:
        await db.close()


# ============== AgenticRAG Endpoints ==============

@app.post("/agent/session")
async def create_agent_session():
    """Create a new agent session."""
    session_id = await _session_manager.create_session()
    return {"session_id": session_id}


@app.post("/agent/chat")
async def agent_chat(req: AgentChatRequest):
    """Agent chat endpoint with SSE streaming.
    
    If the session_id is invalid or expired, a new session is auto-created.
    """
    session = await _session_manager.get_session(req.session_id)
    actual_session_id = req.session_id

    if session is None:
        # Session expired or invalid — auto-create a new one
        actual_session_id = await _session_manager.create_session()
        logger.warning(f"Session '{req.session_id}' not found/expired, auto-created new session: {actual_session_id}")
    
    model_id = req.model or DEFAULT_MODEL
    logger.info(f"[AGENT CHAT] session={actual_session_id} model={model_id} message={req.message[:100]}")

    headers = {
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
    }
    if actual_session_id != req.session_id:
        headers["X-New-Session-Id"] = actual_session_id

    return StreamingResponse(
        agent_loop(
            session_id=actual_session_id,
            user_message=req.message,
            session_manager=_session_manager,
            model_id=model_id,
        ),
        media_type="text/event-stream",
        headers=headers,
    )


@app.get("/agent/session/{session_id}/messages")
async def get_session_messages(session_id: str, user: dict = Depends(get_current_user)):
    """Get session message history."""
    if not user:
        raise HTTPException(status_code=401, detail="未登录")
    session = await _session_manager.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found or expired")
    return {"messages": session.messages}


@app.delete("/agent/session/{session_id}")
async def delete_agent_session(session_id: str):
    """Delete a session (traces 保留，作为运维观测数据独立存在)。"""
    await _session_manager.delete_session(session_id)
    return {"status": "ok"}


@app.get("/agent/debug/trace")
async def get_agent_debug_trace(session_id: str, user: dict = Depends(get_current_user)):
    """Get Agent's complete tool call trace for debugging."""
    if not user:
        raise HTTPException(status_code=401, detail="未登录")
    session = await _session_manager.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found or expired")
    return {"traces": await _build_tool_trace_from_session(session_id, include_ids=True)}


@app.get("/agent/stats")
async def get_agent_stats():
    """Get agent session statistics."""
    return {
        "active_sessions": await _session_manager.get_active_count(),
        "max_sessions": _session_manager._max_sessions,
        "ttl_seconds": _session_manager._ttl,
    }


@app.get("/agent/models")
async def get_agent_models():
    """Get available LLM models."""
    return {
        "models": get_available_models(),
        "default": DEFAULT_MODEL,
    }


@app.get("/agent/traces")
async def get_agent_traces(page: int = 1, limit: int = 20,
                           status: str = None, model_id: str = None, q: str = None,
                           user: dict = Depends(get_current_user)):
    """Get paginated local agent traces with optional filters.

    - status: exact match on status (success / error / loop_detected / max_rounds)
    - model_id: exact match on model_id
    - q: keyword fuzzy match on user_message
    """
    if not user:
        raise HTTPException(status_code=401, detail="未登录")
    page = max(1, page)
    limit = max(1, min(limit, 100))
    try:
        db = await get_db()
        try:
            where_clauses = []
            filter_params = []
            if status:
                where_clauses.append("status = ?")
                filter_params.append(status)
            if model_id:
                where_clauses.append("model_id = ?")
                filter_params.append(model_id)
            if q:
                where_clauses.append("user_message LIKE ?")
                filter_params.append(f"%{q}%")
            where_sql = (" WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

            offset = (page - 1) * limit
            rows = await db.execute(
                f"""SELECT id, session_id, user_message, model_id, total_rounds,
                   total_time_ms, total_llm_calls, total_tool_calls, total_tokens,
                   answer_length, status, error, created_at
                   FROM traces{where_sql} ORDER BY created_at DESC LIMIT ? OFFSET ?""",
                (*filter_params, limit, offset)
            )
            traces = [dict(r) for r in await rows.fetchall()]
            # Get total count (with same filters)
            count_row = await db.execute(f"SELECT COUNT(*) as cnt FROM traces{where_sql}", filter_params)
            total = (await count_row.fetchone())["cnt"]
        finally:
            await db.close()
        return {
            "traces": traces,
            "total": total,
            "page": page,
            "limit": limit,
            "langfuse_enabled": config.LANGFUSE_ENABLED,
            "langfuse_host": config.LANGFUSE_HOST if config.LANGFUSE_ENABLED else "",
        }
    except Exception as e:
        return {
            "traces": [],
            "total": 0,
            "page": 1,
            "limit": limit,
            "error": str(e),
            "langfuse_enabled": config.LANGFUSE_ENABLED,
            "langfuse_host": config.LANGFUSE_HOST if config.LANGFUSE_ENABLED else "",
        }


async def _export_traces_payload(trace_ids: Optional[List[int]] = None) -> JSONResponse:
    """构建 traces 导出的 JSON 响应（选中导出与全部导出共用）。"""
    db = await get_db()
    try:
        if trace_ids is not None:
            if not trace_ids:
                traces = []
            else:
                placeholders = ",".join("?" * len(trace_ids))
                rows = await db.execute(
                    f"SELECT * FROM traces WHERE id IN ({placeholders}) ORDER BY created_at DESC",
                    trace_ids
                )
                traces = [dict(r) for r in await rows.fetchall()]
        else:
            rows = await db.execute("SELECT * FROM traces ORDER BY created_at DESC")
            traces = [dict(r) for r in await rows.fetchall()]
    finally:
        await db.close()

    for t in traces:
        try:
            t["tool_trace"] = await _build_tool_trace_from_session(t["session_id"])
        except Exception:
            t["tool_trace"] = []

    return JSONResponse(
        content={"exported_at": datetime.now().isoformat(), "total": len(traces), "traces": traces},
        headers={"Content-Disposition": "attachment; filename=arknights_traces.json"}
    )


@app.post("/agent/traces/export")
async def export_selected_traces(request: Request, user: dict = Depends(get_current_user)):
    """Export selected (or all) local traces as a JSON file download.
    Request body (optional): {"trace_ids": [1, 2, 3]}
    """
    if not user:
        raise HTTPException(status_code=401, detail="未登录")
    body = await request.json()
    trace_ids = body.get("trace_ids") if body else None
    try:
        return await _export_traces_payload(trace_ids)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/agent/traces")
async def delete_selected_traces(request: Request, user: dict = Depends(get_current_user)):
    """Delete selected local traces by IDs.
    Request body: {"trace_ids": [1, 2, 3]}
    """
    if not user:
        raise HTTPException(status_code=401, detail="未登录")
    body = await request.json()
    trace_ids = body.get("trace_ids", [])
    if not trace_ids:
        raise HTTPException(status_code=400, detail="trace_ids is required")
    db = await get_db()
    try:
        placeholders = ",".join("?" * len(trace_ids))
        cursor = await db.execute(
            f"DELETE FROM traces WHERE id IN ({placeholders})",
            trace_ids
        )
        await db.commit()
        return {"status": "ok", "deleted": cursor.rowcount}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        await db.close()


@app.get("/agent/traces/export")
async def export_all_traces(user: dict = Depends(get_current_user)):
    """Export all local traces as a JSON file download."""
    if not user:
        raise HTTPException(status_code=401, detail="未登录")
    try:
        return await _export_traces_payload()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/agent/traces/langfuse")
async def get_langfuse_traces(page: int = 1, limit: int = 20,
                              user: dict = Depends(get_current_user)):
    """Proxy: fetch paginated traces from LangFuse Public API."""
    if not user:
        raise HTTPException(status_code=401, detail="未登录")
    from backend.observability.tracing import fetch_langfuse_traces
    return await fetch_langfuse_traces(page=page, limit=limit)


@app.get("/agent/traces/summary")
async def get_agent_traces_summary(user: dict = Depends(get_current_user)):
    """Aggregated stats over local traces (for the observability dashboard)."""
    if not user:
        raise HTTPException(status_code=401, detail="未登录")
    try:
        db = await get_db()
        try:
            row = await db.execute(
                """SELECT COUNT(*) as total,
                   COALESCE(SUM(CASE WHEN status != 'success' THEN 1 ELSE 0 END), 0) as error_count,
                   COALESCE(SUM(CASE WHEN date(created_at) = date('now') THEN 1 ELSE 0 END), 0) as today_count,
                   COALESCE(AVG(total_time_ms), 0) as avg_time_ms,
                   COALESCE(AVG(total_tokens), 0) as avg_tokens,
                   COALESCE(AVG(total_rounds), 0) as avg_rounds,
                   COALESCE(SUM(total_tool_calls), 0) as total_tool_calls,
                   COALESCE(SUM(total_tokens), 0) as total_tokens
                   FROM traces"""
            )
            agg = dict(await row.fetchone())

            status_rows = await db.execute("SELECT status, COUNT(*) as cnt FROM traces GROUP BY status")
            by_status = {r["status"]: r["cnt"] for r in await status_rows.fetchall()}

            model_rows = await db.execute(
                "SELECT model_id, COUNT(*) as cnt FROM traces GROUP BY model_id ORDER BY cnt DESC"
            )
            by_model = [{"model_id": r["model_id"], "count": r["cnt"]} for r in await model_rows.fetchall()]
        finally:
            await db.close()

        total = agg["total"] or 0
        return {
            "total": total,
            "today": agg["today_count"],
            "error_count": agg["error_count"],
            "error_rate": (agg["error_count"] / total) if total > 0 else 0,
            "avg_time_ms": agg["avg_time_ms"],
            "avg_tokens": agg["avg_tokens"],
            "avg_rounds": agg["avg_rounds"],
            "total_tool_calls": agg["total_tool_calls"],
            "total_tokens": agg["total_tokens"],
            "by_status": by_status,
            "by_model": by_model,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/agent/traces/{trace_id}")
async def get_agent_trace_detail(trace_id: int, user: dict = Depends(get_current_user)):
    """Get detailed trace info including tool call chain."""
    if not user:
        raise HTTPException(status_code=401, detail="未登录")
    db = await get_db()
    try:
        row = await db.execute("SELECT * FROM traces WHERE id = ?", (trace_id,))
        trace = await row.fetchone()
        if not trace:
            raise HTTPException(status_code=404, detail="Trace not found")
        trace_dict = dict(trace)
    finally:
        await db.close()

    # Get tool call trace + final answer from session messages (if session still exists)
    try:
        trace_dict["tool_trace"] = await _build_tool_trace_from_session(
            trace_dict["session_id"], include_ids=True
        )
        trace_dict["answer"] = await _get_last_assistant_answer(trace_dict["session_id"])
    except Exception:
        trace_dict["tool_trace"] = []
        trace_dict["answer"] = ""
    return trace_dict


@app.get("/agent/traces/{trace_id}/export")
async def export_single_trace(trace_id: int, user: dict = Depends(get_current_user)):
    """Export a single trace as a JSON file download."""
    if not user:
        raise HTTPException(status_code=401, detail="未登录")
    db = await get_db()
    try:
        row = await db.execute("SELECT * FROM traces WHERE id = ?", (trace_id,))
        trace = await row.fetchone()
        if not trace:
            raise HTTPException(status_code=404, detail="Trace not found")
        trace_dict = dict(trace)
    finally:
        await db.close()

    # Enrich with tool trace
    try:
        trace_dict["tool_trace"] = await _build_tool_trace_from_session(trace_dict["session_id"])
    except Exception:
        trace_dict["tool_trace"] = []

    return JSONResponse(
        content=trace_dict,
        headers={"Content-Disposition": f"attachment; filename=trace_{trace_id}.json"}
    )


@app.get("/agent/traces/langfuse/{trace_id}")
async def get_langfuse_trace_detail(trace_id: str, user: dict = Depends(get_current_user)):
    """Proxy: fetch a single trace with full detail from LangFuse."""
    if not user:
        raise HTTPException(status_code=401, detail="未登录")
    from backend.observability.tracing import fetch_langfuse_trace_detail
    result = await fetch_langfuse_trace_detail(trace_id)
    if "error" in result and not any(k in result for k in ("traces", "id")):
        raise HTTPException(status_code=502, detail=result.get("error"))
    return result


# ============== Data Endpoints ==============

def extract_names_from_markdown_table(content: str) -> List[str]:
    """从Markdown表格中提取名字"""
    names = set()
    lines = content.split('\n')

    for line in lines:
        # 匹配表格行，排除分隔行和空行
        line = line.strip()
        if line.startswith('|') and line.endswith('|') and '---' not in line:
            # 移除首尾的|并分割单元格
            cells = line[1:-1].split('|')
            for cell in cells:
                cell = cell.strip()
                # 过滤空单元格和特殊标记
                if cell and cell != '<br />' and cell != '--' and not cell.startswith('...'):
                    # 移除可能的多余空格
                    name = cell.replace('\u3000', ' ').replace('\t', ' ').strip()
                    if name:
                        names.add(name)

    return sorted(list(names))


# ===== Quick Questions static data caches =====
# Pre-load these at first access to avoid repeated disk I/O on every refresh.
_qq_operator_names: Optional[List[str]] = None
_qq_story_names: Optional[List[str]] = None
_qq_enemy_names: Optional[List[str]] = None
_qq_stage_codes: Optional[List[str]] = None  # 关卡代码（如 1-7），来自 prts-mcp stage_table
_qq_alias_candidates: Optional[List[tuple]] = None  # [(standard_name, [aliases]), ...]
_qq_graph_operators: Optional[List[str]] = None  # operator nodes from graph
_qq_previous_labels: Optional[set] = None  # dedup: labels from previous batch


def _load_qq_stage_codes() -> List[str]:
    """Load stage codes from prts-mcp's synced stage_table.json (fallback list if absent)."""
    fallback = ["1-7", "0-1", "CE-5", "LS-5", "4-10", "5-10"]
    base = os.environ.get("PRTS_MCP_DATA_DIR")
    candidate_dirs = []
    if base:
        candidate_dirs.append(Path(base))
    else:
        home = Path.home()
        candidate_dirs.extend([
            home / ".local/share/prts-mcp/gamedata",
            home / "AppData/Local/prts-mcp/gamedata",
        ])
    for data_dir in candidate_dirs:
        if not data_dir.exists():
            continue
        releases = data_dir / ".releases"
        candidates = (
            list(releases.glob("*/zh_CN/gamedata/excel/stage_table.json"))
            if releases.exists()
            else list(data_dir.glob("*/zh_CN/gamedata/excel/stage_table.json"))
        )
        if not candidates:
            # 兜底：允许 PRTS_MCP_DATA_DIR 直接指向 stage_table.json 所在目录
            candidates = list(data_dir.glob("stage_table.json"))
        for stage_file in candidates:
            try:
                with open(stage_file, 'r', encoding='utf-8') as f:
                    stage_data = json.load(f)
            except Exception:
                continue
            stages = stage_data.get("stages", {})
            codes = []
            seen = set()
            for stage_id, info in stages.items():
                if not isinstance(info, dict):
                    continue
                # 排除突袭/四星等派生关卡，只留常规难度，避免重复代码
                if "#f#" in stage_id or info.get("difficulty") != "NORMAL":
                    continue
                code = (info.get("code") or "").strip()
                if code and code not in seen:
                    seen.add(code)
                    codes.append(code)
            if codes:
                return sorted(codes)
    return fallback


def _load_qq_data():
    """Lazy-load all static data needed for quick-question generation."""
    global _qq_operator_names, _qq_story_names, _qq_enemy_names, _qq_stage_codes, _qq_alias_candidates, _qq_graph_operators

    if _qq_operator_names is not None:
        return  # already loaded

    from backend.rag.alias_map import ALIAS_MAP
    from collections import defaultdict

    # Stage codes (PRTS-MCP questions): from prts-mcp synced stage_table.json
    _qq_stage_codes = _load_qq_stage_codes()

    # Operator names (skill questions): from all_operators.json
    operators_file = DATA_DIR / "all_operators.json"
    if operators_file.exists():
        with open(operators_file, 'r', encoding='utf-8') as f:
            operators_data = json.load(f)
        _qq_operator_names = [
            op['干员名'] for op in operators_data
            if '干员名' in op and op.get('星级', '6') not in ('1', '2')
        ]
    else:
        _qq_operator_names = []

    # Story names: from stories/*.md first heading
    stories_dir = DATA_DIR / "stories"
    _qq_story_names = []
    if stories_dir.exists():
        for f in sorted(stories_dir.glob("*.md")):
            try:
                first_line = f.read_text(encoding='utf-8').split('\n', 1)[0].strip()
                if first_line.startswith('# '):
                    _qq_story_names.append(first_line[2:].strip())
            except Exception:
                pass

    # Enemy names: from all_enemies.json
    enemies_file = DATA_DIR / "all_enemies.json"
    if enemies_file.exists():
        with open(enemies_file, 'r', encoding='utf-8') as f:
            enemies_data = json.load(f)
        _qq_enemy_names = [e['名称'] for e in enemies_data if '名称' in e]
    else:
        _qq_enemy_names = []

    # Alias candidates: operators with 2+ aliases
    name_to_aliases = defaultdict(set)
    for alias, standard in ALIAS_MAP.items():
        name_to_aliases[standard].add(alias)
    _qq_alias_candidates = [
        (name, sorted(aliases))
        for name, aliases in name_to_aliases.items()
        if len(aliases) >= 2
    ]

    # Graph operator nodes: operator-type nodes from entity_relations graph
    _qq_graph_operators = []
    try:
        er = _load_entity_relations()
        entities = er.get("entities", {})
        if isinstance(entities, dict):
            for entity_type, entity_list in entities.items():
                if isinstance(entity_list, list):
                    for e in entity_list:
                        name = e.get("name", "") if isinstance(e, dict) else str(e)
                        if name:
                            _qq_graph_operators.append(name)
    except Exception:
        pass




@app.get("/quick-questions")
async def get_quick_questions(refresh: bool = False):
    """生成9个快速问题：关系+技能+故事+敌人+别名+结构化+立绘+出怪顺序+材料掉落，批内不重复。"""
    global _quick_questions_cache, _quick_questions_cache_time, _qq_previous_labels

    now = time.time()
    if not refresh and _quick_questions_cache and now - _quick_questions_cache_time < _quick_questions_cache_ttl:
        return {"questions": _quick_questions_cache}

    # Lazy-load static data (cached after first call)
    _load_qq_data()

    # Build exclude set from previous batch (dedup)
    exclude_labels = _qq_previous_labels or set()

    questions = []

    # ===== 1. 关系问题：基于图中直接相连的干员对（O(deg) 替代 O(V+E) BFS） =====
    try:
        op_nodes = _qq_graph_operators or []
        if op_nodes:
            relation_label = None
            for _ in range(30):
                node_a = random.choice(op_nodes)
                # Use entity_relations to find directly connected pairs (fast)
                er = _load_entity_relations()
                relations = er.get("relations", [])
                connected = set()
                for r in relations:
                    s = r.get("source", r.get("head", ""))
                    t = r.get("target", r.get("tail", ""))
                    if s == node_a and t in op_nodes:
                        connected.add(t)
                    elif t == node_a and s in op_nodes:
                        connected.add(s)
                # Also try graph neighbors if graph is available
                try:
                    from backend.rag.graphrag.query import get_graph_builder
                    gb = get_graph_builder()
                    if gb and gb.graph and node_a in gb.graph:
                        for nb in set(list(gb.graph.successors(node_a)) + list(gb.graph.predecessors(node_a))):
                            if nb in op_nodes:
                                connected.add(nb)
                except Exception:
                    pass
                if connected:
                    node_b = random.choice(list(connected))
                    relation_label = f"{node_a}/{node_b}关系"
                    if relation_label not in exclude_labels:
                        questions.append({
                            "label": relation_label,
                            "question": f"{node_a}和{node_b}的关系",
                            "type": "relation", "category": "graph",
                        })
                        exclude_labels.add(relation_label)
                        break
            if not questions:  # no relation found after all attempts
                if len(op_nodes) >= 2:
                    a, b = random.sample(op_nodes, 2)
                    label = f"{a}/{b}关系"
                    question = f"{a}和{b}的关系"
                else:
                    label = "银灰/初雪关系"
                    question = "银灰和初雪的关系"
                questions.append({
                    "label": label,
                    "question": question,
                    "type": "relation", "category": "graph",
                })
                exclude_labels.add(label)
        else:
            label = "银灰/初雪关系"
            questions.append({
                "label": label,
                "question": "银灰和初雪的关系",
                "type": "relation", "category": "graph",
            })
            exclude_labels.add(label)
    except Exception as e:
        logger.error(f"Failed to generate relation question: {e}")
        label = "银灰/初雪关系"
        questions.append({
            "label": label,
            "question": "银灰和初雪的关系",
            "type": "relation", "category": "graph",
        })
        exclude_labels.add(label)

    # ===== RAG 能力：技能/故事/敌人/别名 各 1 条，保持类型不重复 =====
    for rag_kind in ("skill", "story", "enemy", "alias"):
        rag_question = pick_rag_question(
            _qq_operator_names or [],
            _qq_story_names or [],
            _qq_enemy_names or [],
            _qq_alias_candidates or [],
            exclude_labels,
            kind=rag_kind,
        )
        questions.append(rag_question)
        exclude_labels.add(rag_question["label"])

    # ===== 结构化查询能力 =====
    structured_question = pick_template(STRUCTURED_TEMPLATES, exclude_labels)
    questions.append(structured_question)
    exclude_labels.add(structured_question["label"])

    # ===== PRTS-MCP 能力：立绘/出怪顺序/材料掉落 各 1 条，类型不重复 =====
    questions.extend(pick_unique_questions(
        _qq_operator_names or [], make_artwork_question, exclude_labels, 1,
    ))
    questions.extend(pick_unique_questions(
        _qq_stage_codes or [], make_stage_enemies_question, exclude_labels, 1,
    ))
    questions.extend(pick_unique_questions(
        _qq_stage_codes or [], make_stage_item_question, exclude_labels, 1,
    ))
    # 数据缺失时用固定模板补齐到 9 个
    while len(questions) < 9:
        mcp_question = pick_template(PRTS_MCP_TEMPLATES, exclude_labels)
        questions.append(mcp_question)
        exclude_labels.add(mcp_question["label"])

    # Update caches
    _quick_questions_cache = questions
    _quick_questions_cache_time = time.time()
    _qq_previous_labels = {q["label"] for q in questions}

    return {"questions": questions}


@app.get("/operators")
async def get_operators():
    """获取所有干员名列表（从all_operators.json）"""
    operators_file = DATA_DIR / "all_operators.json"
    if not operators_file.exists():
        raise HTTPException(status_code=404, detail="Operators file not found")

    try:
        with open(operators_file, 'r', encoding='utf-8') as f:
            data = json.load(f)

        # 提取干员名字段
        operator_names = []
        for operator in data:
            if '干员名' in operator:
                operator_names.append(operator['干员名'])

        return {"operators": operator_names}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error reading operators data: {str(e)}")


@app.get("/characters")
async def get_characters():
    """获取角色名列表（从char_summary.md）"""
    char_file = DATA_DIR / "char_summary.md"
    if not char_file.exists():
        raise HTTPException(status_code=404, detail="Characters file not found")

    try:
        with open(char_file, 'r', encoding='utf-8') as f:
            content = f.read()

        names = extract_names_from_markdown_table(content)
        return {"characters": names}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error reading characters data: {str(e)}")


@app.get("/stories")
async def get_stories():
    """获取故事名列表（从story_summary.md）"""
    story_file = DATA_DIR / "story_summary.md"
    if not story_file.exists():
        raise HTTPException(status_code=404, detail="Stories file not found")

    try:
        with open(story_file, 'r', encoding='utf-8') as f:
            content = f.read()

        names = extract_names_from_markdown_table(content)
        return {"stories": names}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error reading stories data: {str(e)}")


# ============== 前端静态文件 ==============
_frontend_dist = Path(__file__).parent.parent / "frontend" / "dist"
if _frontend_dist.exists():
    app.mount("/assets", StaticFiles(directory=_frontend_dist / "assets"), name="static-assets")

    _frontend_dist_resolved = _frontend_dist.resolve()

    @app.get("/{full_path:path}", include_in_schema=False)
    async def serve_spa(full_path: str):
        """SPA catch-all: 非 API 路由统一返回 index.html，由前端路由处理。

        安全：resolve 后必须仍在 dist 目录内，防止 ../../ 路径穿越读取任意文件。
        """
        try:
            file_path = (_frontend_dist_resolved / full_path).resolve()
            file_path.relative_to(_frontend_dist_resolved)
        except (ValueError, OSError):
            file_path = None
        if file_path and file_path.is_file():
            return FileResponse(file_path)
        return FileResponse(_frontend_dist_resolved / "index.html")


# ============== Run Server ==============
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8100))
    uvicorn.run(app, host="0.0.0.0", port=port)
