"""
Context compression and rolling-summary engine for Agent sessions.

This module is responsible for:

* Splitting ``Session.messages`` into conversation turns.  A turn starts at a
  user message and includes all following assistant/tool/system messages until
  the next user message.
* Deciding whether the LLM context should be compressed.
* Building the compressed LLM context:
  ``system + first turn + rolling summary + last N turns``.
* Incrementally updating ``session.summary`` with a small non-streaming LLM
  chat call.
* Persisting one context snapshot row per agent request for trace analysis.

``Session.messages`` is never mutated or truncated by this module.
"""

import json
import logging
import re
import time
from typing import Any, Dict, List, Optional

from backend.agent.sessions import clean_messages_for_llm
from backend.db import get_db

logger = logging.getLogger(__name__)

# Compression trigger constants.
# Compression is triggered when EITHER condition holds:
#   * more than CONTEXT_COMPRESSION_MIN_TURNS user turns (i.e. from turn 9)
#   * estimated full-context tokens exceed CONTEXT_COMPRESSION_TOKEN_BUDGET
CONTEXT_COMPRESSION_MIN_TURNS = 8
CONTEXT_COMPRESSION_TOKEN_BUDGET = 20000
CONTEXT_KEEP_FIRST_TURNS = 1
CONTEXT_KEEP_RECENT_TURNS = 3

# Conservative token estimator:
#   * CJK characters (including full-width forms and CJK punctuation) count as 1 token.
#   * all other characters count as approximately 4 chars/token (ceil division).
_CJK_RE = re.compile(
    r"[\u3000-\u303f\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff"
    r"\uf900-\ufaff\uff00-\uffef]"
)


def estimate_tokens(text: str) -> int:
    """Estimate token count for a piece of text.

    Conservative estimator used for the compression budget:
    CJK chars ≈ 1 token, other chars ≈ 4 chars/token.
    """
    if not text:
        return 0
    cjk_count = len(_CJK_RE.findall(text))
    other_count = len(text) - cjk_count
    return cjk_count + (other_count + 3) // 4


def _estimate_value_tokens(value: Any) -> int:
    """Recursively estimate tokens for a JSON-like message value."""
    if isinstance(value, str):
        return estimate_tokens(value)
    if isinstance(value, dict):
        return sum(_estimate_value_tokens(v) for v in value.values())
    if isinstance(value, list):
        return sum(_estimate_value_tokens(v) for v in value)
    return estimate_tokens(str(value))


def estimate_messages_tokens(messages: List[Dict[str, Any]]) -> int:
    """Estimate total tokens for a list of chat messages.

    All user-visible/LLM-visible fields are counted; internal underscore
    fields are ignored just like the orphan cleanup path does.
    """
    total = 0
    for msg in messages or []:
        for key, value in msg.items():
            if key.startswith("_"):
                continue
            total += _estimate_value_tokens(value)
    return total


def split_turns(messages: List[Dict[str, Any]]) -> List[List[Dict[str, Any]]]:
    """Split a full message list into conversation turns.

    Each turn is one user message plus all following assistant/tool/system
    messages until the next user message.  Leading non-user messages (which
    should not normally exist) are merged into the first turn so they are not
    silently dropped.
    """
    turns: List[List[Dict[str, Any]]] = []
    preamble: List[Dict[str, Any]] = []

    for msg in messages or []:
        if msg.get("role") == "user":
            if preamble:
                turns.append(preamble + [msg])
                preamble = []
            else:
                turns.append([msg])
        elif turns:
            turns[-1].append(msg)
        else:
            preamble.append(msg)

    if preamble:
        if turns:
            turns[0] = preamble + turns[0]
        else:
            turns.append(preamble)

    return turns


def should_compress(session) -> bool:
    """Return True when the session should use the compressed context.

    Trigger is OR:
      * turn count > CONTEXT_COMPRESSION_MIN_TURNS (8), or
      * estimated full transcript tokens > CONTEXT_COMPRESSION_TOKEN_BUDGET (20000).
    """
    if session is None:
        return False
    turn_count = len(split_turns(session.messages))
    estimated = estimate_messages_tokens(session.messages)
    if turn_count > CONTEXT_COMPRESSION_MIN_TURNS:
        logger.info(
            "[CONTEXT] compress by turns: turns=%d > min=%d",
            turn_count, CONTEXT_COMPRESSION_MIN_TURNS,
        )
        return True
    if estimated > CONTEXT_COMPRESSION_TOKEN_BUDGET:
        logger.info(
            "[CONTEXT] compress by tokens: estimated=%d > budget=%d",
            estimated, CONTEXT_COMPRESSION_TOKEN_BUDGET,
        )
        return True
    return False


def _turn_windows(turn_count: int):
    """Return (first_turn_slice, middle_turn_slice, recent_turn_slice) indexes.

    Slices are Python slice objects over the turns list (0-based).
    Middle is empty when first and recent windows overlap or touch.
    """
    first_end = CONTEXT_KEEP_FIRST_TURNS
    recent_start = max(first_end, turn_count - CONTEXT_KEEP_RECENT_TURNS)
    middle_end = turn_count - CONTEXT_KEEP_RECENT_TURNS
    return (
        slice(0, first_end),
        slice(first_end, middle_end),
        slice(recent_start, turn_count),
    )


def can_build_compressed(session) -> bool:
    """Return True if a safe compressed context can be built.

    If there are middle turns, a rolling summary must already exist; otherwise
    the caller should fall back to the uncompressed context for this request.
    """
    turn_count = len(split_turns(session.messages))
    _, middle_slice, _ = _turn_windows(turn_count)
    if middle_slice.start >= middle_slice.stop:
        return True
    return bool(session.summary)


def build_compressed_messages(session, system_prompt: Optional[str] = None) -> List[Dict[str, Any]]:
    """Build the compressed LLM context for a session.

    Shape: system prompt, first turn in full, one system rolling-summary
    message, then the last CONTEXT_KEEP_RECENT_TURNS turns in full.

    If there is no middle window, the summary message is omitted.
    """
    if system_prompt is None:
        # Local import avoids a circular import at module load time.
        from backend.agent.prompts import SYSTEM_PROMPT
        system_prompt = SYSTEM_PROMPT

    turns = split_turns(session.messages)
    turn_count = len(turns)
    first_slice, middle_slice, recent_slice = _turn_windows(turn_count)

    messages: List[Dict[str, Any]] = [{"role": "system", "content": system_prompt}]

    # First turn(s) in full, cleaned of orphaned tool pairs.
    for turn in turns[first_slice]:
        messages.extend(clean_messages_for_llm(turn))

    # Rolling summary of middle turns.
    if middle_slice.start < middle_slice.stop:
        if not session.summary:
            logger.warning(
                "[CONTEXT] compressed build requested without summary for middle turns; "
                "caller should have fallen back to uncompressed context"
            )
        messages.append({
            "role": "system",
            "content": "以下是此前对话的滚动摘要（仅作为背景信息，不要将其视为当前用户输入）：\n"
                       + (session.summary or ""),
        })

    # Last few turns in full.
    for turn in turns[recent_slice]:
        messages.extend(clean_messages_for_llm(turn))

    return messages


# ===== Incremental rolling summary =====

_SUMMARY_SYSTEM_PROMPT = (
    "你是一个简洁的对话摘要助手。你只负责总结对话，不回答问题，不调用工具，"
    "不输出任何与摘要无关的内容。"
)

_SUMMARY_PROMPT_TEMPLATE = """请对以下明日方舟问答对话片段进行中文滚动摘要更新。

要求：
1. 输出一份简洁的纯中文摘要，不超过 300 字。
2. 保留用户的核心意图、重要问题、提到的关键事实/实体/数字，以及助手给出的关键结论。
3. 不得编造对话中不存在的内容，不得添加外部知识。
4. 如果提供了“已有摘要”，请将新增片段合并进已有摘要，而不是单独只写新增片段。
5. 直接输出摘要正文，不要加标题、前后缀或解释。

已有摘要：
{existing_summary}

新增对话片段（JSON）：
{new_turns_json}
"""


async def update_rolling_summary(session, client, model_id: str) -> bool:
    """Incrementally update ``session.summary`` using only new middle turns.

    This is called once per incoming user turn, before building messages, only
    when compression is triggered.  It uses a small, tool-free, non-streaming
    LLM chat completion through the existing DeepSeekClient.

    Returns True when the summary was updated.  On any failure it logs a
    warning, leaves the previous summary untouched, and returns False so the
    caller can use the documented safe fallback.
    """
    if not should_compress(session):
        return False

    turns = split_turns(session.messages)
    turn_count = len(turns)
    _, middle_slice, _ = _turn_windows(turn_count)

    if middle_slice.start >= middle_slice.stop:
        return False

    # middle_slice.stop is the exclusive end index, i.e. the 1-based number of
    # the last middle turn (N - CONTEXT_KEEP_RECENT_TURNS).
    target_up_to_turn = middle_slice.stop
    start_turn = max(middle_slice.start + 1, session.summary_up_to_turn + 1)
    if start_turn > target_up_to_turn:
        return False

    new_turns = turns[start_turn - 1:target_up_to_turn]
    if not new_turns:
        return False

    existing_summary = session.summary or "（无）"
    new_turns_json = json.dumps(new_turns, ensure_ascii=False, default=str)
    prompt = _SUMMARY_PROMPT_TEMPLATE.format(
        existing_summary=existing_summary,
        new_turns_json=new_turns_json,
    )

    try:
        result = await client.chat_completion(
            messages=[
                {"role": "system", "content": _SUMMARY_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            model=model_id,
            temperature=0.2,
        )
    except Exception as exc:
        logger.warning(
            "[CONTEXT] rolling summary update failed for session=%s: %s",
            session.session_id, exc,
        )
        return False

    if not result or not str(result).strip():
        logger.warning("[CONTEXT] rolling summary update returned empty content")
        return False

    session.summary = str(result).strip()
    session.summary_up_to_turn = target_up_to_turn
    logger.info(
        "[CONTEXT] rolling summary updated: session=%s summary_up_to_turn=%d summary_len=%d",
        session.session_id, session.summary_up_to_turn, len(session.summary),
    )
    return True


# ===== Context snapshot log =====

_CONTEXT_LOG_RETENTION = 100


async def log_context_snapshot(
    session_id: str,
    turn_no: int,
    messages: List[Dict[str, Any]],
    estimated_tokens: int,
    compressed: bool,
) -> None:
    """Insert one context snapshot row and cap retention at latest 100 rows/session.

    Failures are logged but never allowed to break the agent request.
    """
    try:
        context_json = json.dumps(messages, ensure_ascii=False, default=str)
    except Exception as exc:
        logger.warning("[CONTEXT] failed to serialize context snapshot: %s", exc)
        return

    try:
        db = await get_db()
        try:
            await db.execute(
                "INSERT INTO agent_context_logs "
                "(session_id, turn_no, created_at, estimated_tokens, compressed, context_messages) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (session_id, turn_no, time.time(),
                 estimated_tokens, 1 if compressed else 0, context_json),
            )
            await db.execute(
                "DELETE FROM agent_context_logs WHERE session_id=? AND id NOT IN ("
                "SELECT id FROM agent_context_logs WHERE session_id=? "
                "ORDER BY id DESC LIMIT ?"
                ")",
                (session_id, session_id, _CONTEXT_LOG_RETENTION),
            )
            await db.commit()
        finally:
            await db.close()
    except Exception as exc:
        logger.warning("[CONTEXT] failed to insert context snapshot: %s", exc)
