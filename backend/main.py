"""
Arknights Agent Backend - FastAPI Server
Provides REST API for the frontend
"""
import asyncio
import functools
import sys
import os
import json
import random
import time
import threading
import logging
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Dict, Any

# asyncio.to_thread 在 Python 3.9 才加入；服务器运行的是 3.8.10，此处提供兼容回退。
if not hasattr(asyncio, "to_thread"):
    async def _to_thread(func, *args, **kwargs):
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, functools.partial(func, *args, **kwargs))

    asyncio.to_thread = _to_thread

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Request, Depends, Header, Query
# 注意：fastapi.Path 与 pathlib.Path 同名，这里起别名以免覆盖下面用到的 pathlib.Path
from fastapi import Path as PathParam
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator
from starlette.types import ASGIApp, Scope, Receive, Send, Message
import uvicorn

from backend import config  # 必须在 auth 之前导入，以加载 .env

from backend.db import get_db, init_db
from backend.auth import (
    validate_account, validate_username, validate_password,
    hash_password, verify_password, create_jwt, decode_jwt,
    extract_jwt_claims_unverified,
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
AGENT_SESSION_RETENTION_DAYS = 30  # 持久化 agent 会话保留时长（过期后允许恢复的窗口）
MAX_CHAT_MESSAGE_LENGTH = 8000  # 单条用户消息长度上限（防止超长输入放大 LLM 成本）

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
    # 以及数据目录，保证 prts-mcp 子进程与 stage_waves 读取的是同一份同步数据
    for key in ("GITHUB_MIRRORS", "GITHUB_TOKEN", "PRTS_MCP_DATA_DIR"):
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


async def _cleanup_old_agent_sessions():
    """删除超过保留期的持久化 agent 会话，防止 agent_session_store 无限膨胀。

    内存 TTL 只有 1 小时；持久化行保留 30 天，供用户关闭/重开页面或服务重启后
    恢复完整对话历史。超过 30 天未活跃的会话视为废弃，清掉即可。
    """
    try:
        db = await get_db()
        try:
            cutoff = time.time() - AGENT_SESSION_RETENTION_DAYS * 24 * 3600
            # 先取回待删除的 session_id，便于同步清理会话归属记录
            cursor = await db.execute(
                "SELECT session_id FROM agent_session_store WHERE last_active < ?",
                (cutoff,),
            )
            stale_ids = [r["session_id"] for r in await cursor.fetchall()]
            cursor = await db.execute(
                "DELETE FROM agent_session_store WHERE last_active < ?",
                (cutoff,),
            )
            await db.commit()
            deleted = cursor.rowcount
        finally:
            await db.close()
        for sid in stale_ids:
            _forget_session_owner(sid)
        if deleted:
            logger.info(
                f"[SESSION] Retention cleanup: deleted {deleted} persisted sessions "
                f"older than {AGENT_SESSION_RETENTION_DAYS} days"
            )
    except Exception as e:
        logger.warning(f"[SESSION] Persisted session retention cleanup failed: {e}")


async def _trace_retention_loop():
    """启动时清理一次，之后每 24 小时清理一次。"""
    while True:
        await _cleanup_old_traces()
        await _cleanup_old_agent_sessions()
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
    title="Arknights Agent API",
    description="Backend API for Arknights Agent",
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

# ===== Agent 会话归属（D01/T01）=====
# 归属是**持久化事实**：
#   - agent_session_store.user_id → agent 会话属主
#   - traces.user_id            → trace 属主（trace 生命周期独立于会话）
# 进程内只保留一层有容量上限的 LRU **读缓存**。服务重启（push 到 master 即自动
# 部署重启）后缓存为空，判定自动回落到数据库；「内存里没记录」不再等于「无主可认领」。
_OWNER_CACHE_MAX = 4096
_session_owner_cache: "OrderedDict[str, int]" = OrderedDict()
_session_owner_lock = threading.Lock()


def _cache_session_owner(session_id: str, user_id: int) -> None:
    """写入归属读缓存（LRU：超过容量即淘汰最久未用的条目，不会无界增长）。"""
    with _session_owner_lock:
        _session_owner_cache[session_id] = user_id
        _session_owner_cache.move_to_end(session_id)
        while len(_session_owner_cache) > _OWNER_CACHE_MAX:
            _session_owner_cache.popitem(last=False)


def _cached_session_owner(session_id: str) -> Optional[int]:
    with _session_owner_lock:
        owner = _session_owner_cache.get(session_id)
        if owner is not None:
            _session_owner_cache.move_to_end(session_id)
    return owner


def _forget_session_owner(session_id: str) -> None:
    """会话过期/删除后丢弃缓存条目（库里的 trace 归属不受影响）。"""
    with _session_owner_lock:
        _session_owner_cache.pop(session_id, None)


async def _resolve_session_owner(session_id: str) -> Optional[int]:
    """返回会话属主；None = 归属未知（无记录、无主或读库失败，一律按拒绝处理）。"""
    owner = _cached_session_owner(session_id)
    if owner is not None:
        return owner
    owner = await _session_manager.get_persisted_owner(session_id)
    if owner is not None:
        _cache_session_owner(session_id, owner)
    return owner


async def _bind_session_owner(session_id: str, user_id: int) -> bool:
    """绑定会话归属（持久化 + 缓存）；已被他人占用或写库失败时返回 False。

    写库失败一律按拒绝处理（fail-closed）：既不放行无法证明归属的访问，
    也避免把「只在内存里成立」的归属写进缓存、反过来把真正的属主挡在门外。
    """
    try:
        bound = await _session_manager.bind_persisted_owner(session_id, user_id)
    except Exception as exc:
        logger.warning(f"[AUTH] Failed to persist owner for {session_id}: {exc}")
        return False
    if bound:
        _cache_session_owner(session_id, user_id)
    return bound


async def _bind_new_session(session_id: str, user_id: int) -> bool:
    """新建会话（或空会话首次使用）时登记归属。"""
    return await _bind_session_owner(session_id, user_id)


async def _check_session_access(session_id: str, user: dict) -> Optional[str]:
    """校验当前用户是否有权访问该 agent 会话；无权时返回中文原因，放行返回 None。

    归属只认持久化数据：
    - 已有归属 → 仅属主本人可用；
    - 归属未知（NULL/查不到）→ 只有「确实存在且没有任何消息」的会话允许首次绑定；
      已有历史消息而无归属的会话一律拒绝，不做「先到先得」式认领；
    - 会话根本不存在 → 放行（无数据可泄漏，由调用方决定 404 / 幂等删除）。
    """
    owner = await _resolve_session_owner(session_id)
    if owner is not None:
        return None if owner == user["user_id"] else "无权访问该会话"

    session = await _session_manager.get_session(session_id)
    if session is None:
        session = await _session_manager.restore_session(session_id)
    if session is None:
        return None
    if session.messages:
        logger.warning(f"[AUTH] Rejected unowned non-empty session: {session_id}")
        return "会话不存在或无权访问"

    if not await _bind_session_owner(session_id, user["user_id"]):
        return "无权访问该会话"
    session.owner_id = user["user_id"]
    return None


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
    """Load entity relations from JSON file, cached in memory.

    同步磁盘读取（116KB JSON）；async 路由里请用 ``await asyncio.to_thread(_load_entity_relations)``
    调用，避免阻塞事件循环。
    """
    global _entity_relations_cache
    if _entity_relations_cache is None:
        if ENTITY_RELATIONS_FILE.exists():
            with open(ENTITY_RELATIONS_FILE, "r", encoding="utf-8") as f:
                _entity_relations_cache = json.load(f)
        else:
            _entity_relations_cache = {"entities": {}, "relations": []}
    return _entity_relations_cache


def _read_text_file(path: Path) -> str:
    """同步读取文本文件（供 asyncio.to_thread 调用，避免阻塞事件循环）。"""
    return path.read_text(encoding="utf-8")


def _read_json_file(path: Path):
    """同步读取 JSON 文件（供 asyncio.to_thread 调用，避免阻塞事件循环）。"""
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _collect_chunk_infos(collection_dir: Path) -> List["ChunkInfo"]:
    """遍历集合目录并读取每个切块（同步 I/O，供 asyncio.to_thread 调用）。"""
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


def _count_chunk_files(collection_dir: Path) -> int:
    """统计集合目录下的切块文件数（同步目录遍历，供 asyncio.to_thread 调用）。"""
    return len(list(collection_dir.glob("*.md"))) + len(list(collection_dir.glob("*.txt")))


# ===== AgenticRAG Request Models =====

# 会话 ID 是服务端生成的 UUID（36 字符）；给一个宽松上限，避免超长 ID 变成
# 归属缓存/日志/数据库查询的无界输入。
MAX_SESSION_ID_LENGTH = 128


class AgentChatRequest(BaseModel):
    """Request for agent chat endpoint."""
    session_id: str = Field(..., min_length=1, max_length=MAX_SESSION_ID_LENGTH)
    message: str
    model: Optional[str] = None

    @field_validator('message')
    @classmethod
    def message_not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError('message cannot be empty')
        v = v.strip()
        if len(v) > MAX_CHAT_MESSAGE_LENGTH:
            raise ValueError(f'message 长度不能超过 {MAX_CHAT_MESSAGE_LENGTH} 个字符')
        return v



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
    return {"message": "Arknights Agent API", "version": "1.0.0"}


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

    # 目录遍历 + 逐文件 read_text 都是阻塞 I/O，放到线程里执行
    return await asyncio.to_thread(_collect_chunk_infos, collection_dir)


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

    content = await asyncio.to_thread(_read_text_file, filepath)
    return {"filename": filename, "content": content}


@app.get("/knowledge-graph", response_model=EntityRelationData)
async def get_graph():
    """Get entity relations for knowledge graph (cached)."""
    # 首次调用要读 116KB JSON，缓存未命中时同样放到线程里
    data = await asyncio.to_thread(_load_entity_relations)
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

    # Count chunks（目录遍历是阻塞 I/O，放到线程里执行）
    for coll in ["operators", "stories", "knowledge"]:
        collection_dir = CHUNKS_DIR / coll
        if collection_dir.exists():
            stats[coll] = await asyncio.to_thread(_count_chunk_files, collection_dir)

    # Count relations (cached；缓存未命中时要读 116KB JSON)
    data = await asyncio.to_thread(_load_entity_relations)
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

# /conversations/sync 的请求体上限：客户端每次同步会带上本地全部会话，
# 无上限的 payload 会长时间占用单个 DB 连接并产生海量查询（DoS）
MAX_SYNC_BODY_BYTES = 64 * 1024 * 1024          # 整个请求体上限（Content-Length 预检）
MAX_SYNC_CONVERSATIONS = 500                    # 单次同步的会话数上限
MAX_SYNC_MESSAGES_PER_CONVERSATION = 2000       # 单会话消息数上限
MAX_SYNC_CONTENT_CHARS = 100_000                # 单条消息正文长度上限
MAX_SYNC_METADATA_CHARS = 200_000               # 单条消息 metadata 序列化后长度上限


class SyncMessage(BaseModel):
    """同步入参中的单条消息（字段长度受限，避免超大 payload 拖垮 DB 连接）。"""
    role: str = Field(default="", max_length=32)
    content: str = Field(default="", max_length=MAX_SYNC_CONTENT_CHARS)
    metadata: Optional[Dict[str, Any]] = Field(default_factory=dict)
    created_at: str = Field(default="", max_length=64)

    @field_validator("role", "content", "created_at", mode="before")
    @classmethod
    def _coerce_text(cls, v):
        """历史数据里可能出现 null/数字，统一收敛成字符串而不是直接 422。"""
        if v is None:
            return ""
        return v if isinstance(v, str) else str(v)

    @field_validator("metadata", mode="before")
    @classmethod
    def _limit_metadata(cls, v):
        if not isinstance(v, dict):
            return {}  # 非对象 metadata 按空对象处理（与原 json.dumps 的容错一致）
        try:
            size = len(json.dumps(v, ensure_ascii=False))
        except (TypeError, ValueError, RecursionError):
            return {}
        if size > MAX_SYNC_METADATA_CHARS:
            raise ValueError(f"单条消息 metadata 过大（{size} 字符，上限 {MAX_SYNC_METADATA_CHARS}）")
        return v


class SyncConversation(BaseModel):
    """同步入参中的单个会话。"""
    session_id: str = Field(default="", max_length=128)
    name: str = Field(default="", max_length=200)
    created_at: str = Field(default="", max_length=64)
    updated_at: str = Field(default="", max_length=64)
    messages: List[SyncMessage] = Field(
        default_factory=list, max_length=MAX_SYNC_MESSAGES_PER_CONVERSATION
    )

    @field_validator("session_id", "name", "created_at", "updated_at", mode="before")
    @classmethod
    def _coerce_text(cls, v):
        if v is None:
            return ""
        return v if isinstance(v, str) else str(v)


class SyncConversationsRequest(BaseModel):
    conversations: List[SyncConversation] = Field(
        default_factory=list, max_length=MAX_SYNC_CONVERSATIONS
    )


async def get_current_user(authorization: str = Header(None)):
    """Extract current user from JWT token in Authorization header.

    先查库取当前 password_changed_at，再交给 decode_jwt 强制比对
    （比对逻辑收敛在 auth.decode_jwt，调用方无法跳过），实现改密后旧 token 失效。
    """
    if not authorization or not authorization.startswith("Bearer "):
        return None
    token = authorization[7:]

    # 先解出 user_id 仅用于查库（不信任其中的 pw_changed_at），
    # 真正的校验由下面带 DB 当前值的 decode_jwt 完成——比对无法被调用方跳过。
    unverified = extract_jwt_claims_unverified(token)
    user_id = unverified.get("user_id") if isinstance(unverified, dict) else None
    if not user_id:
        return None

    try:
        db = await get_db()
        try:
            cursor = await db.execute(
                "SELECT password_changed_at FROM users WHERE id = ?",
                (user_id,)
            )
            row = await cursor.fetchone()
        finally:
            await db.close()
    except Exception as e:
        logger.warning(f"[AUTH] Token verification against DB failed: {e}")
        return None

    if not row:
        return None

    return decode_jwt(token, row["password_changed_at"])


async def require_user(user: dict = Depends(get_current_user)) -> dict:
    """需要登录的依赖：未登录/凭据失效直接 401，而不是把 None 交给路由体。"""
    if not user:
        raise HTTPException(status_code=401, detail="未登录或登录已过期")
    return user


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
async def sync_conversations(req: SyncConversationsRequest, request: Request, user: dict = Depends(get_current_user)):
    """Sync (upsert) conversations from frontend. Incremental: skip existing messages."""
    if not user:
        raise HTTPException(status_code=401, detail="未登录")
    # Content-Length 预检：请求体过大时直接拒绝，避免先解析完整 JSON 再做字段校验
    content_length = request.headers.get("content-length", "")
    if content_length.isdigit() and int(content_length) > MAX_SYNC_BODY_BYTES:
        raise HTTPException(status_code=413, detail="同步数据过大，请精简本地会话后重试")
    db = await get_db()
    try:
        for conv in req.conversations:
            sid = conv.session_id
            if not sid:
                continue
            cursor = await db.execute(
                "SELECT user_id FROM conversations WHERE session_id = ?", (sid,)
            )
            existing = await cursor.fetchone()
            if not existing:
                await db.execute(
                    "INSERT INTO conversations (session_id, user_id, name, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                    (sid, user["user_id"], conv.name, conv.created_at, conv.updated_at)
                )
            elif existing["user_id"] != user["user_id"]:
                # 不允许通过同步接口覆盖/注入其他用户的会话
                logger.warning(f"[SYNC] Skipped conversation owned by another user: {sid}")
                continue
            else:
                await db.execute(
                    "UPDATE conversations SET name = ?, updated_at = ? WHERE session_id = ?",
                    (conv.name, conv.updated_at, sid)
                )

            # 一次 SELECT 取回该会话已有消息的判重键，内存去重后 executemany 批量插入：
            # 把原来的「每条消息一次 SELECT + 一次 INSERT」（N+1 次往返）压成 1 次查询 + 1 次批量写
            cursor = await db.execute(
                "SELECT role, content, created_at FROM messages WHERE session_id = ?",
                (sid,)
            )
            seen = {(row["role"], row["content"], row["created_at"]) for row in await cursor.fetchall()}
            pending = []
            for msg in conv.messages:
                key = (msg.role, msg.content, msg.created_at)
                if key in seen:
                    continue
                seen.add(key)
                pending.append((
                    sid, msg.role, msg.content,
                    json.dumps(msg.metadata, ensure_ascii=False),
                    msg.created_at,
                ))
            if pending:
                await db.executemany(
                    "INSERT INTO messages (session_id, role, content, metadata, created_at) VALUES (?, ?, ?, ?, ?)",
                    pending
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
async def create_agent_session(user: dict = Depends(require_user)):
    """Create a new agent session（需登录，会话归属创建者，随会话持久化）。"""
    session_id = await _session_manager.create_session()
    session = await _session_manager.get_session(session_id)
    if session is not None:
        session.owner_id = user["user_id"]
    await _bind_new_session(session_id, user["user_id"])
    return {"session_id": session_id}


@app.post("/agent/chat")
async def agent_chat(req: AgentChatRequest, user: dict = Depends(require_user)):
    """Agent chat endpoint with SSE streaming.

    需登录；会话必须属于当前用户。归属以持久化数据为准：内存未命中（含服务重启）
    时先查会话表的属主；已有历史消息但归属未知的会话拒绝访问（不可认领）。
    If the session_id is invalid or expired, a new session is auto-created.
    """
    owner = await _resolve_session_owner(req.session_id)
    if owner is not None and owner != user["user_id"]:
        raise HTTPException(status_code=403, detail="无权访问该会话")

    session = await _session_manager.get_session(req.session_id)
    actual_session_id = req.session_id

    if session is None:
        # 内存未命中时先尝试 SQLite 恢复；归属未知的已有会话按“不可认领”处理。
        session = await _session_manager.restore_session(req.session_id)
        if session is None:
            # Session expired or invalid — auto-create a new one
            actual_session_id = await _session_manager.create_session()
            session = await _session_manager.get_session(actual_session_id)
            logger.warning(f"Session '{req.session_id}' not found/expired, auto-created new session: {actual_session_id}")
        elif owner is not None:
            logger.info(f"Session '{req.session_id}' restored from SQLite, reusing same session")
        elif session.messages:
            # 有历史消息但没有归属记录：无法证明归属，拒绝访问（避免 IDOR）
            logger.warning(f"[AUTH] Rejected unowned non-empty session: {req.session_id}")
            raise HTTPException(status_code=404, detail="Session not found or expired")
        else:
            logger.info(f"Session '{req.session_id}' restored from SQLite, reusing same session")

    # 归属判定/绑定必须在新消息写入之前完成（此刻 session 已是恢复后的对象）
    if owner is None:
        if session is not None and session.messages:
            # 内存对象有历史消息但归属未知（例如归属写入失败）→ 拒绝，不认领
            logger.warning(f"[AUTH] Rejected unowned non-empty session: {actual_session_id}")
            raise HTTPException(status_code=404, detail="Session not found or expired")
        if not await _bind_session_owner(actual_session_id, user["user_id"]):
            raise HTTPException(status_code=404, detail="Session not found or expired")
        if session is not None:
            session.owner_id = user["user_id"]

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
async def get_session_messages(session_id: str = PathParam(..., max_length=MAX_SESSION_ID_LENGTH),
                               user: dict = Depends(require_user)):
    """Get session message history（仅限会话归属者）。"""
    denial = await _check_session_access(session_id, user)
    if denial:
        raise HTTPException(status_code=404, detail=denial)
    session = await _session_manager.get_session(session_id)
    if session is None:
        session = await _session_manager.restore_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found or expired")
    return {"messages": session.messages}


@app.delete("/agent/session/{session_id}")
async def delete_agent_session(session_id: str = PathParam(..., max_length=MAX_SESSION_ID_LENGTH),
                               user: dict = Depends(require_user)):
    """Delete a session（需登录且限会话归属者；traces 保留，作为运维观测数据独立存在）。"""
    owner = await _resolve_session_owner(session_id)
    if owner is None:
        # 归属未知（库中无记录/NULL）的会话：只有确实存在且无历史消息的才允许删
        denial = await _check_session_access(session_id, user)
        if denial:
            raise HTTPException(status_code=404, detail=denial)
    elif owner != user["user_id"]:
        raise HTTPException(status_code=403, detail="无权删除该会话")

    await _session_manager.delete_session(session_id)
    _forget_session_owner(session_id)
    return {"status": "ok"}


@app.get("/agent/debug/trace")
async def get_agent_debug_trace(session_id: str = Query(..., max_length=MAX_SESSION_ID_LENGTH),
                                user: dict = Depends(require_user)):
    """Get Agent's complete tool call trace for debugging（仅限会话归属者）。"""
    denial = await _check_session_access(session_id, user)
    if denial:
        raise HTTPException(status_code=404, detail=denial)
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


def _trace_owned_by_user(trace: Dict, user: dict) -> bool:
    """判断某条 trace 行是否允许当前用户查看。

    归属来自 traces.user_id（持久化列）：NULL 表示归属未知（迁移前的历史数据、
    或写入时无法确定属主），一律**拒绝**——历史 trace 默认对所有人不可见，
    宁可少显示也不放行。
    """
    owner = trace.get("user_id")
    if owner is None:
        return False
    try:
        return int(owner) == int(user["user_id"])
    except (TypeError, ValueError):
        return False


def _langfuse_session_of(item: Dict) -> Optional[str]:
    """从 LangFuse trace 中还原对应的 agent session_id。

    tracing.py 用 ``id=f"agent-{session_id}"`` 建 trace，session_id 存在
    metadata 里（LangFuse 的 sessionId 字段本项目未设置），三处都兜一下。
    """
    if not isinstance(item, dict):
        return None
    sid = item.get("sessionId")
    if not sid:
        meta = item.get("metadata")
        if isinstance(meta, dict):
            sid = meta.get("session_id")
    if not sid:
        tid = item.get("id")
        if isinstance(tid, str) and tid.startswith("agent-"):
            sid = tid[len("agent-"):]
    return str(sid) if sid else None


async def _langfuse_trace_allowed(item: Dict, user: dict) -> bool:
    """LangFuse trace 的归属判定：按 sessionId 对应的**持久化**会话属主放行。"""
    session_id = _langfuse_session_of(item)
    if session_id:
        owner = await _resolve_session_owner(session_id)
        if owner is not None:
            return owner == user["user_id"]
        # 会话归属未知：若 LangFuse 侧带了可信的 userId 维度则据此判定
        langfuse_user = item.get("userId")
        if langfuse_user:
            return str(langfuse_user) == str(user["user_id"])
    return False


@app.get("/agent/traces")
async def get_agent_traces(page: int = 1, limit: int = 20,
                           status: str = None, model_id: str = None, q: str = None,
                           user: dict = Depends(get_current_user)):
    """Get paginated local agent traces with optional filters.

    - status: exact match on status (success / error / loop_detected / max_rounds)
    - model_id: exact match on model_id
    - q: keyword fuzzy match on user_message

    归属过滤：只返回 traces.user_id == 当前用户的行；user_id 为 NULL 的历史
    数据归属未知，一律不返回（fail-closed）。
    """
    if not user:
        raise HTTPException(status_code=401, detail="未登录")
    page = max(1, page)
    limit = max(1, min(limit, 100))
    try:
        db = await get_db()
        try:
            where_clauses = ["user_id = ?"]
            filter_params = [user["user_id"]]
            if status:
                where_clauses.append("status = ?")
                filter_params.append(status)
            if model_id:
                where_clauses.append("model_id = ?")
                filter_params.append(model_id)
            if q:
                where_clauses.append("user_message LIKE ?")
                filter_params.append(f"%{q}%")
            where_sql = " WHERE " + " AND ".join(where_clauses)

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


async def _export_traces_payload(trace_ids: Optional[List[int]] = None,
                                 user: Optional[dict] = None) -> JSONResponse:
    """构建 traces 导出的 JSON 响应（选中导出与全部导出共用，仅含本人 trace）。"""
    db = await get_db()
    try:
        if trace_ids is not None:
            if not trace_ids:
                traces = []
            else:
                placeholders = ",".join("?" * len(trace_ids))
                rows = await db.execute(
                    f"SELECT * FROM traces WHERE id IN ({placeholders}) AND user_id = ? "
                    "ORDER BY created_at DESC",
                    (*trace_ids, user["user_id"])
                )
                traces = [dict(r) for r in await rows.fetchall()]
        else:
            rows = await db.execute(
                "SELECT * FROM traces WHERE user_id = ? ORDER BY created_at DESC",
                (user["user_id"],)
            )
            traces = [dict(r) for r in await rows.fetchall()]
    finally:
        await db.close()

    # 双保险：SQL 之外再过一遍归属判定（user_id 为 NULL 的历史数据不导出）
    traces = [t for t in traces if _trace_owned_by_user(t, user)]

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
        return await _export_traces_payload(trace_ids, user)
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
        # 只允许删除自己的 trace（traces.user_id 为 NULL 的历史数据视为无主，不可删）
        placeholders = ",".join("?" * len(trace_ids))
        rows = await db.execute(
            f"SELECT id, session_id, user_id FROM traces WHERE id IN ({placeholders})",
            trace_ids
        )
        deletable = [
            r["id"] for r in await rows.fetchall()
            if _trace_owned_by_user(dict(r), user)
        ]
        if not deletable:
            return {"status": "ok", "deleted": 0}
        placeholders = ",".join("?" * len(deletable))
        cursor = await db.execute(
            f"DELETE FROM traces WHERE id IN ({placeholders})",
            deletable
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
        return await _export_traces_payload(None, user)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/agent/traces/langfuse")
async def get_langfuse_traces(page: int = 1, limit: int = 20,
                              user: dict = Depends(get_current_user)):
    """Proxy: fetch paginated traces from LangFuse Public API（仅返回本人会话的 trace）。

    归属按 trace 关联的 agent session 的**持久化**属主判定；取不到 session 且
    没有可信 userId 的 trace 一律不返回（fail-closed）。返回的 total 因此是
    「本页可见条数」，不是 LangFuse 的全局总数（避免泄漏他人 trace 数量）。
    """
    if not user:
        raise HTTPException(status_code=401, detail="未登录")
    from backend.observability.tracing import fetch_langfuse_traces
    result = await fetch_langfuse_traces(page=page, limit=limit)
    if isinstance(result, dict) and isinstance(result.get("traces"), list):
        visible = [
            t for t in result["traces"]
            if await _langfuse_trace_allowed(t, user)
        ]
        result["traces"] = visible
        result["total"] = len(visible)
        result["filtered_by_owner"] = True
    return result


@app.get("/agent/traces/summary")
async def get_agent_traces_summary(user: dict = Depends(get_current_user)):
    """Aggregated stats over local traces (for the observability dashboard).

    只统计 traces.user_id == 当前用户的行（NULL 归属的历史数据不计入）。
    """
    if not user:
        raise HTTPException(status_code=401, detail="未登录")
    try:
        where_sql = " WHERE user_id = ?"
        own_params = [user["user_id"]]
        db = await get_db()
        try:
            row = await db.execute(
                f"""SELECT COUNT(*) as total,
                   COALESCE(SUM(CASE WHEN status != 'success' THEN 1 ELSE 0 END), 0) as error_count,
                   COALESCE(SUM(CASE WHEN date(created_at) = date('now') THEN 1 ELSE 0 END), 0) as today_count,
                   COALESCE(AVG(total_time_ms), 0) as avg_time_ms,
                   COALESCE(AVG(total_tokens), 0) as avg_tokens,
                   COALESCE(AVG(total_rounds), 0) as avg_rounds,
                   COALESCE(SUM(total_tool_calls), 0) as total_tool_calls,
                   COALESCE(SUM(total_tokens), 0) as total_tokens
                   FROM traces{where_sql}""",
                own_params
            )
            agg = dict(await row.fetchone())

            status_rows = await db.execute(
                f"SELECT status, COUNT(*) as cnt FROM traces{where_sql} GROUP BY status",
                own_params
            )
            by_status = {r["status"]: r["cnt"] for r in await status_rows.fetchall()}

            model_rows = await db.execute(
                f"SELECT model_id, COUNT(*) as cnt FROM traces{where_sql} "
                "GROUP BY model_id ORDER BY cnt DESC",
                own_params
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
    """Get detailed trace info including tool call chain（仅限 trace 属主）。"""
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

    if not _trace_owned_by_user(trace_dict, user):
        raise HTTPException(status_code=404, detail="Trace not found")

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
    """Export a single trace as a JSON file download（仅限 trace 属主）。"""
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

    if not _trace_owned_by_user(trace_dict, user):
        raise HTTPException(status_code=404, detail="Trace not found")

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
    """Proxy: fetch a single trace with full detail from LangFuse（仅限本人会话）。"""
    if not user:
        raise HTTPException(status_code=401, detail="未登录")
    from backend.observability.tracing import fetch_langfuse_trace_detail
    result = await fetch_langfuse_trace_detail(trace_id)
    if "error" in result and not any(k in result for k in ("traces", "id")):
        raise HTTPException(status_code=502, detail=result.get("error"))
    # 归属校验：能拿到 session 就按持久化属主判定，否则按 trace id 前缀还原后判定；
    # 两者都取不到时拒绝（fail-closed），不返回详情。
    if not await _langfuse_trace_allowed(result, user):
        if not await _langfuse_trace_allowed({**result, "id": trace_id}, user):
            logger.warning(f"[AUTH] Rejected langfuse trace detail for user={user['user_id']}: {trace_id}")
            raise HTTPException(status_code=404, detail="Trace not found")
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


def _stage_table_candidates() -> List[Path]:
    """Collect prts-mcp stage_table.json candidates across supported layouts.

    PRTS_MCP_DATA_DIR 可能指向 prts-mcp 根目录（数据在其下的 gamedata/），
    也可能直接指向 gamedata 目录；都兼容。多个 release 存在时按 mtime
    取最新的（与 stage_waves 的 _latest_zh_dir 语义一致）。
    """
    base = os.environ.get("PRTS_MCP_DATA_DIR")
    data_roots: List[Path] = []
    if base:
        p = Path(base)
        data_roots.extend([p / "gamedata", p])
    else:
        home = Path.home()
        data_roots.extend([
            home / ".local/share/prts-mcp/gamedata",
            home / "AppData/Local/prts-mcp/gamedata",
        ])

    candidates: List[Path] = []
    for data_dir in data_roots:
        if not data_dir.exists():
            continue
        releases = data_dir / ".releases"
        if releases.exists():
            candidates.extend(releases.glob("*/zh_CN/gamedata/excel/stage_table.json"))
        else:
            candidates.extend(data_dir.glob("*/zh_CN/gamedata/excel/stage_table.json"))
            candidates.extend(data_dir.glob("excel/stage_table.json"))
        candidates.extend(data_dir.glob("stage_table.json"))

    unique: Dict[str, Path] = {}
    for f in candidates:
        try:
            unique[str(f.resolve())] = f
        except OSError:
            unique[str(f)] = f
    try:
        return sorted(unique.values(), key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        return list(unique.values())


def _load_qq_stage_codes() -> List[str]:
    """Load stage codes from prts-mcp's synced stage_table.json (fallback list if absent)."""
    fallback = ["1-7", "0-1", "CE-5", "LS-5", "4-10", "5-10"]
    for stage_file in _stage_table_candidates():
        try:
            with open(stage_file, 'r', encoding='utf-8') as f:
                stage_data = json.load(f)
        except Exception:
            continue
        stages = stage_data.get("stages")
        if not isinstance(stages, dict):
            continue
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
    # 首次调用要读 stage_table.json（可能数 MB）、干员/敌人 JSON 与全部故事 md，
    # 全是阻塞 I/O，放到线程里执行
    await asyncio.to_thread(_load_qq_data)

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
                    # 图构建器是懒加载单例：首次调用要读 entity_relations.json 并建图
                    # （同步 I/O），放到线程里执行
                    gb = await asyncio.to_thread(get_graph_builder)
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
        data = await asyncio.to_thread(_read_json_file, operators_file)

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
        content = await asyncio.to_thread(_read_text_file, char_file)

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
        content = await asyncio.to_thread(_read_text_file, story_file)

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

