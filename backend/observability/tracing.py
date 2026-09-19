"""
LangFuse tracing for Agent observability.
Traces LLM calls, tool executions, and overall agent sessions.
"""

import asyncio
import base64
import json
import logging
import time
import uuid
from typing import Dict

logger = logging.getLogger(__name__)

# asyncio.to_thread 在 Python 3.9 才加入；服务器运行的是 3.8.10，此处提供兼容回退。
if not hasattr(asyncio, "to_thread"):
    async def _to_thread(func, *args, **kwargs):
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, lambda: func(*args, **kwargs))

    asyncio.to_thread = _to_thread

_langfuse_client = None


def _unique_trace_suffix() -> str:
    """trace id 的唯一后缀：毫秒时间戳 + uuid 片段（同毫秒内也不重复）。"""
    return f"{int(time.time() * 1000):x}{uuid.uuid4().hex[:8]}"


def _flush_client(client) -> None:
    """同步 flush LangFuse 客户端（阻塞网络 I/O）；失败必须留下日志。"""
    try:
        client.flush()
    except Exception as e:
        logger.warning(f"Failed to flush LangFuse client: {e}", exc_info=True)


def get_langfuse_client():
    """Get or create the LangFuse client singleton."""
    global _langfuse_client
    if _langfuse_client is not None:
        return _langfuse_client

    from backend import config
    if not config.LANGFUSE_ENABLED:
        logger.info("LangFuse not configured, tracing disabled")
        return None

    try:
        from langfuse import Langfuse
        _langfuse_client = Langfuse(
            public_key=config.LANGFUSE_PUBLIC_KEY,
            secret_key=config.LANGFUSE_SECRET_KEY,
            host=config.LANGFUSE_HOST,
        )
        logger.info(f"LangFuse client initialized (host={config.LANGFUSE_HOST})")
        return _langfuse_client
    except ImportError:
        logger.warning("langfuse package not installed, tracing disabled")
        return None
    except Exception as e:
        logger.error(f"Failed to initialize LangFuse: {e}")
        return None


class AgentTrace:
    """Manages a single agent run (= one user message) inside a session.

    同一 session 的多轮对话各自拥有一条 trace（id 带唯一后缀）；按会话查看时
    用 trace 的 sessionId / metadata.session_id 归组。
    """

    def __init__(self, session_id: str, user_message: str, model_id: str):
        self.session_id = session_id
        self.user_message = user_message
        self.model_id = model_id
        self.trace = None
        self.start_time = time.time()
        self.total_llm_calls = 0
        self.total_tool_calls = 0
        self.total_tokens = 0

        client = get_langfuse_client()
        if client:
            # trace id 必须「每条用户消息」唯一：session 会跨多轮复用，id 写死成
            # agent-{session_id} 时同会话多轮会命中 LangFuse 的同 id 合并，后一轮的
            # output/metadata 直接覆盖前一轮，可观测数据只剩最后一条。
            # 保留 `agent-{session_id}` 前缀，同时把 session_id 写进 metadata 与
            # LangFuse 的 sessionId 字段，归属过滤（main.py 的 _langfuse_session_of）
            # 仍可按会话解析。
            trace_id = f"agent-{session_id}-{_unique_trace_suffix()}"
            trace_kwargs = {
                "id": trace_id,
                "name": "agent-conversation",
                "metadata": {
                    "session_id": session_id,
                    "model": model_id,
                },
                "input": user_message[:500],
            }
            try:
                # session_id 是 LangFuse 一等字段：写入后 trace 列表可直接按会话过滤
                self.trace = client.trace(session_id=session_id, **trace_kwargs)
            except TypeError:
                # 兼容不接受 session_id 参数的旧版 SDK
                try:
                    self.trace = client.trace(**trace_kwargs)
                except Exception as e:
                    logger.warning(f"Failed to create LangFuse trace: {e}")
                    self.trace = None
            except Exception as e:
                logger.warning(f"Failed to create LangFuse trace: {e}")
                self.trace = None

    def add_llm_generation(self, round_num: int, messages_count: int,
                           input_tokens: int = 0, output_tokens: int = 0,
                           latency_ms: float = 0, model: str = "",
                           tool_calls_count: int = 0, error: str = ""):
        """Record an LLM call as a generation span."""
        self.total_llm_calls += 1
        self.total_tokens += input_tokens + output_tokens

        if not self.trace:
            return

        try:
            metadata = {
                "round": round_num,
                "messages_count": messages_count,
                "tool_calls_count": tool_calls_count,
                "latency_ms": round(latency_ms),
            }
            if error:
                metadata["error"] = error

            # 用本轮真实耗时反推 start_time：写死 self.start_time（会话开始时间）
            # 会让每个 generation 的时长等于"会话开始 → 本轮结束"，轮数越多越失真。
            end_time = time.time()
            if latency_ms and latency_ms > 0:
                start_time = max(end_time - latency_ms / 1000.0, self.start_time)
            else:
                start_time = self.start_time

            self.trace.generation(
                name=f"llm-round-{round_num}",
                model=model or self.model_id,
                start_time=start_time,
                end_time=end_time,
                usage={
                    "input": input_tokens,
                    "output": output_tokens,
                    "total": input_tokens + output_tokens,
                },
                metadata=metadata,
            )
        except Exception as e:
            logger.warning(f"Failed to record LangFuse generation: {e}")

    def add_tool_span(self, tool_name: str, round_num: int,
                      args: Dict = None, result_summary: str = "",
                      latency_ms: float = 0, error: str = ""):
        """Record a tool execution as a span."""
        self.total_tool_calls += 1

        if not self.trace:
            return

        try:
            metadata = {
                "round": round_num,
                "tool_name": tool_name,
                "latency_ms": round(latency_ms),
            }
            if args:
                # Truncate args for storage
                args_str = json.dumps(args, ensure_ascii=False)[:500]
                metadata["arguments"] = args_str
            if result_summary:
                metadata["result_summary"] = result_summary[:200]
            if error:
                metadata["error"] = error

            self.trace.span(
                name=f"tool-{tool_name}",
                metadata=metadata,
            )
        except Exception as e:
            logger.warning(f"Failed to record LangFuse span: {e}")

    def _apply_final_update(self, total_rounds: int = 0, total_time_ms: float = 0,
                            answer_length: int = 0, error: str = "") -> bool:
        """把最终统计写进 trace；返回是否存在 trace（决定后续是否需要 flush）。"""
        if not self.trace:
            return False

        try:
            self.trace.update(
                output=f"rounds={total_rounds}, llm_calls={self.total_llm_calls}, tool_calls={self.total_tool_calls}, tokens={self.total_tokens}, answer_len={answer_length}"[:500],
                metadata={
                    "total_rounds": total_rounds,
                    "total_time_ms": round(total_time_ms),
                    "total_llm_calls": self.total_llm_calls,
                    "total_tool_calls": self.total_tool_calls,
                    "total_tokens": self.total_tokens,
                    "answer_length": answer_length,
                    "status": "error" if error else "success",
                    "error": error[:200] if error else "",
                },
            )
        except Exception as e:
            logger.warning(f"Failed to update LangFuse trace: {e}", exc_info=True)
        return True

    def _schedule_flush(self) -> None:
        """触发 flush，但不阻塞事件循环。

        ``client.flush()`` 会阻塞等待队列数据发送完（网络 I/O）。``end()`` 经常被
        async 生成器直接调用，同步 flush 会卡住整个事件循环，所以检测到运行中的
        事件循环时把 flush 丢到线程池；没有事件循环的同步调用方才直接 flush。
        """
        client = get_langfuse_client()
        if not client:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            _flush_client(client)
            return
        try:
            loop.run_in_executor(None, _flush_client, client)
        except Exception as e:
            logger.warning(f"Failed to schedule LangFuse flush: {e}", exc_info=True)

    def end(self, total_rounds: int = 0, total_time_ms: float = 0,
            answer_length: int = 0, error: str = "") -> None:
        """End the trace with final metadata（同步调用方用；async 调用方见 aend）。"""
        if not self._apply_final_update(total_rounds, total_time_ms, answer_length, error):
            return
        # Flush to ensure data is sent（事件循环内不阻塞，见 _schedule_flush）
        self._schedule_flush()

    async def aend(self, total_rounds: int = 0, total_time_ms: float = 0,
                   answer_length: int = 0, error: str = "") -> None:
        """``end()`` 的 await 版本：flush 在线程池里执行完才返回。"""
        if not self._apply_final_update(total_rounds, total_time_ms, answer_length, error):
            return
        client = get_langfuse_client()
        if client:
            await asyncio.to_thread(_flush_client, client)


# ── LangFuse Public API client ──────────────────────────────────────────────

def _langfuse_auth_header() -> str:
    """Build the Basic Auth header for LangFuse Public API."""
    from backend import config
    credentials = f"{config.LANGFUSE_PUBLIC_KEY}:{config.LANGFUSE_SECRET_KEY}"
    encoded = base64.b64encode(credentials.encode()).decode()
    return f"Basic {encoded}"


def _langfuse_api_url(path: str) -> str:
    """Build a full LangFuse Public API URL."""
    from backend import config
    host = config.LANGFUSE_HOST.rstrip("/")
    return f"{host}/api/public{path}"


async def fetch_langfuse_traces(page: int = 1, limit: int = 20,
                                 name: str = None, user_id: str = None) -> dict:
    """Fetch a paginated list of traces from LangFuse.

    Uses the LangFuse Public API (GET /api/public/traces).
    Returns {"traces": [...], "total": int, "page": int, "limit": int} or an error dict.
    """
    from backend import config
    if not config.LANGFUSE_ENABLED:
        return {"error": "LangFuse 未配置", "traces": [], "total": 0}

    url = _langfuse_api_url("/traces")
    params = {"page": page, "limit": limit, "orderBy": "timestamp.desc"}
    if name:
        params["name"] = name
    if user_id:
        params["user_id"] = user_id

    headers = {"Authorization": _langfuse_auth_header()}

    try:
        import requests as _requests
        resp = await asyncio.to_thread(
            _requests.get, url, params=params, headers=headers, timeout=15
        )
        if resp.status_code == 401:
            return {"error": "LangFuse 认证失败，请检查 API Key", "traces": [], "total": 0}
        resp.raise_for_status()
        data = resp.json()
        traces = data.get("data", [])
        meta = data.get("meta", {})
        return {
            "traces": [
                {
                    "id": t.get("id"),
                    "name": t.get("name"),
                    "userId": t.get("userId"),
                    "sessionId": t.get("sessionId"),
                    "timestamp": t.get("timestamp"),
                    "input": str(t.get("input", ""))[:200] if t.get("input") else "",
                    "output": str(t.get("output", ""))[:200] if t.get("output") else "",
                    "metadata": t.get("metadata", {}),
                    "latency": t.get("latency"),
                    "totalCost": t.get("totalCost"),
                    "environment": t.get("environment"),
                }
                for t in traces
            ],
            "total": meta.get("totalItems", len(traces)),
            "page": meta.get("page", page),
            "limit": limit,
        }
    except Exception as e:
        logger.warning(f"[LANGFUSE] Failed to fetch traces: {e}")
        return {"error": f"获取 LangFuse trace 列表失败: {e}", "traces": [], "total": 0}


async def fetch_langfuse_trace_detail(trace_id: str) -> dict:
    """Fetch a single trace with full detail from LangFuse.

    Uses GET /api/public/traces/{id}.
    """
    from backend import config
    if not config.LANGFUSE_ENABLED:
        return {"error": "LangFuse 未配置"}

    url = _langfuse_api_url(f"/traces/{trace_id}")
    headers = {"Authorization": _langfuse_auth_header()}

    try:
        import requests as _requests
        resp = await asyncio.to_thread(
            _requests.get, url, headers=headers, timeout=15
        )
        if resp.status_code == 404:
            return {"error": "Trace 不存在"}
        resp.raise_for_status()
        data = resp.json()

        # Parse observations (spans/generations)
        observations = data.get("observations", [])
        spans = []
        generations = []
        for obs in observations:
            if obs.get("type") == "SPAN":
                spans.append({
                    "id": obs.get("id"),
                    "name": obs.get("name"),
                    "startTime": obs.get("startTime"),
                    "endTime": obs.get("endTime"),
                    "latency": obs.get("latency"),
                    "input": str(obs.get("input", ""))[:300] if obs.get("input") else "",
                    "output": str(obs.get("output", ""))[:300] if obs.get("output") else "",
                    "metadata": obs.get("metadata", {}),
                    "level": obs.get("level"),
                })
            elif obs.get("type") == "GENERATION":
                generations.append({
                    "id": obs.get("id"),
                    "name": obs.get("name"),
                    "model": obs.get("model"),
                    "startTime": obs.get("startTime"),
                    "endTime": obs.get("endTime"),
                    "latency": obs.get("latency"),
                    "input": str(obs.get("input", ""))[:300] if obs.get("input") else "",
                    "output": str(obs.get("output", ""))[:300] if obs.get("output") else "",
                    "usage": obs.get("usage", {}),
                    "metadata": obs.get("metadata", {}),
                })

        return {
            "id": data.get("id"),
            "name": data.get("name"),
            "userId": data.get("userId"),
            "sessionId": data.get("sessionId"),
            "timestamp": data.get("timestamp"),
            "input": str(data.get("input", ""))[:500] if data.get("input") else "",
            "output": str(data.get("output", ""))[:500] if data.get("output") else "",
            "metadata": data.get("metadata", {}),
            "latency": data.get("latency"),
            "totalCost": data.get("totalCost"),
            "environment": data.get("environment"),
            "spans": spans,
            "generations": generations,
        }
    except Exception as e:
        logger.warning(f"[LANGFUSE] Failed to fetch trace detail: {e}")
        return {"error": f"获取 LangFuse trace 详情失败: {e}"}


async def _resolve_owner_from_session(session_id: str) -> int:
    """从会话表读出该 trace 所属会话的属主；未知返回 0（= 归属未知）。"""
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
        logger.warning(f"[TRACE] Failed to resolve owner for session={session_id}: {exc}")
        return 0

    if row is None or row["user_id"] is None:
        return 0
    try:
        return int(row["user_id"])
    except (TypeError, ValueError):
        return 0


async def save_trace_to_db(
    session_id: str,
    user_message: str,
    model_id: str,
    total_rounds: int,
    total_time_ms: float,
    total_llm_calls: int,
    total_tool_calls: int,
    total_tokens: int,
    answer_length: int,
    status: str = "success",
    error: str = "",
    user_id: int = None,
):
    """Save a completed agent trace to the local SQLite database.

    ``user_id`` 为 None 时按会话表推断属主；推断不出就写 NULL，语义是
    「归属未知」，读取侧对未知归属按拒绝处理（fail-closed，不会误放行）。
    """
    try:
        from backend.db import get_db
        if user_id is None:
            user_id = await _resolve_owner_from_session(session_id)
        db = await get_db()
        try:
            await db.execute(
                """INSERT INTO traces (session_id, user_message, model_id, total_rounds,
                   total_time_ms, total_llm_calls, total_tool_calls, total_tokens,
                   answer_length, status, error, user_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (session_id, user_message[:500], model_id, total_rounds, total_time_ms,
                 total_llm_calls, total_tool_calls, total_tokens, answer_length, status, error,
                 user_id or None)
            )
            await db.commit()
        except Exception:
            # 写失败必须先回滚：否则未提交的事务会一直挂在这个连接上，
            # 连接归还/复用时把后续写入一起拖下水。
            try:
                await db.rollback()
            except Exception as rollback_exc:
                logger.warning(f"[TRACE] Rollback failed: {rollback_exc}", exc_info=True)
            raise
        finally:
            await db.close()
        logger.info(f"[TRACE] Saved trace for session={session_id} user={user_id} status={status}")
    except Exception as e:
        logger.warning(f"[TRACE] Failed to save trace to DB: {e}", exc_info=True)
