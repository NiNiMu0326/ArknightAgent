"""
backend/db.py — SQLite database initialization and helpers.
"""
import logging

import aiosqlite
from pathlib import Path

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent.parent / "data" / "arknights_rag.db"


async def get_db() -> aiosqlite.Connection:
    """Get a database connection."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    db = await aiosqlite.connect(str(DB_PATH))
    db.row_factory = aiosqlite.Row
    await db.execute("PRAGMA journal_mode=WAL")
    await db.execute("PRAGMA foreign_keys=ON")
    return db


async def init_db():
    """Initialize database tables."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(str(DB_PATH)) as db:
        await db.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                account TEXT UNIQUE NOT NULL,
                username TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                created_at TEXT DEFAULT (datetime('now')),
                password_changed_at TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS conversations (
                session_id TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                name TEXT NOT NULL DEFAULT '',
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now')),
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                metadata TEXT DEFAULT '{}',
                created_at TEXT DEFAULT (datetime('now')),
                FOREIGN KEY (session_id) REFERENCES conversations(session_id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_conversations_user_id ON conversations(user_id);
            CREATE INDEX IF NOT EXISTS idx_conversations_updated_at ON conversations(updated_at);
            CREATE INDEX IF NOT EXISTS idx_messages_session_id ON messages(session_id);

            CREATE TABLE IF NOT EXISTS traces (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                user_message TEXT NOT NULL DEFAULT '',
                model_id TEXT NOT NULL DEFAULT '',
                total_rounds INTEGER NOT NULL DEFAULT 0,
                total_time_ms REAL NOT NULL DEFAULT 0,
                total_llm_calls INTEGER NOT NULL DEFAULT 0,
                total_tool_calls INTEGER NOT NULL DEFAULT 0,
                total_tokens INTEGER NOT NULL DEFAULT 0,
                answer_length INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'success',
                error TEXT NOT NULL DEFAULT '',
                created_at TEXT DEFAULT (datetime('now'))
            );

            CREATE INDEX IF NOT EXISTS idx_traces_session_id ON traces(session_id);
            CREATE INDEX IF NOT EXISTS idx_traces_created_at ON traces(created_at);

            CREATE TABLE IF NOT EXISTS agent_session_store (
                session_id TEXT PRIMARY KEY,
                messages TEXT NOT NULL,
                summary TEXT DEFAULT '',
                summary_up_to_turn INTEGER DEFAULT 0,
                last_active REAL,
                created_at REAL
            );

            CREATE TABLE IF NOT EXISTS agent_context_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT,
                turn_no INTEGER,
                created_at REAL,
                estimated_tokens INTEGER,
                compressed INTEGER,
                context_messages TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_agent_context_logs_session_id ON agent_context_logs(session_id);
            CREATE INDEX IF NOT EXISTS idx_agent_context_logs_created_at ON agent_context_logs(created_at);
        """)
        await _migrate_user_ownership(db)
        await db.commit()


async def _add_column_if_missing(db: aiosqlite.Connection, table: str, column: str, ddl: str) -> None:
    """幂等地给表加列（列已存在时忽略）。"""
    try:
        await db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
        logger.info(f"[DB] Migrated: added {table}.{column}")
    except Exception:
        # 绝大多数情况是 "duplicate column name"（列已存在）——幂等迁移的正常路径
        pass


async def _migrate_user_ownership(db: aiosqlite.Connection) -> None:
    """幂等迁移：把「会话/trace → 用户」归属落库（T01）。

    归属必须是持久化数据，否则服务重启（每次 push 部署都会重启）后进程内的
    归属表为空，任何登录用户都能靠先发请求把别人的会话/trace 认领成自己的。

    - ``agent_session_store.user_id``：agent 会话属主（原表只有会话内容）
    - ``traces.user_id``：trace 属主（原表没有 user 维度，只能靠内存推断）

    加列不破坏既有表结构与数据；历史行的 user_id 为 NULL，语义是「归属未知」，
    读取侧对未知归属一律按拒绝处理（fail-closed）。
    """
    await _add_column_if_missing(db, "agent_session_store", "user_id", "INTEGER")
    await _add_column_if_missing(db, "traces", "user_id", "INTEGER")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_traces_user_id ON traces(user_id)")
    # 回填：trace 的归属可从未过期的会话行推出（只填 NULL，不覆盖已有归属）
    try:
        await db.execute(
            "UPDATE traces SET user_id = ("
            "  SELECT s.user_id FROM agent_session_store s WHERE s.session_id = traces.session_id"
            ") WHERE traces.user_id IS NULL AND EXISTS ("
            "  SELECT 1 FROM agent_session_store s "
            "  WHERE s.session_id = traces.session_id AND s.user_id IS NOT NULL"
            ")"
        )
    except Exception as exc:
        logger.warning(f"[DB] traces.user_id backfill skipped: {exc}")
