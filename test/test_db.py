"""
Tests for backend.db: SQLite database initialization.
Usage: cd test && python -m pytest test_db.py -v
"""
import sys
import os
import pytest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

# We need to patch DB_PATH before importing db
import backend.db as db
from backend.config import BASE_DIR


# ============================================================
# Database connection tests
# ============================================================

class TestGetDb:
    """Test database connection creation."""

    def test_get_db_returns_connection(self):
        async def _test():
            conn = await db.get_db()
            try:
                assert conn is not None
                # Should be able to execute a query
                cursor = await conn.execute("SELECT 1")
                row = await cursor.fetchone()
                assert row[0] == 1
            finally:
                await conn.close()

        import asyncio
        asyncio.run(_test())

    def test_get_db_row_factory(self):
        """Connection should return rows as aiosqlite.Row objects."""
        async def _test():
            conn = await db.get_db()
            try:
                assert conn.row_factory is db.aiosqlite.Row
            finally:
                await conn.close()

        import asyncio
        asyncio.run(_test())

    def test_get_db_enables_foreign_keys(self):
        """foreign_keys pragma should be ON."""
        async def _test():
            conn = await db.get_db()
            try:
                cursor = await conn.execute("PRAGMA foreign_keys")
                row = await cursor.fetchone()
                assert row[0] == 1
            finally:
                await conn.close()

        import asyncio
        asyncio.run(_test())

    def test_get_db_uses_wal_mode(self):
        """journal_mode should be WAL."""
        async def _test():
            conn = await db.get_db()
            try:
                cursor = await conn.execute("PRAGMA journal_mode")
                row = await cursor.fetchone()
                assert row[0].upper() == "WAL"
            finally:
                await conn.close()

        import asyncio
        asyncio.run(_test())


# ============================================================
# Database initialization tests
# ============================================================

class TestInitDb:
    """Test database table creation."""

    @pytest.fixture(autouse=True)
    def setup_and_teardown(self):
        """Ensure tables exist before each test and clean up."""
        async def _init():
            await db.init_db()
        import asyncio
        asyncio.run(_init())
        yield

    async def _table_exists(self, table_name: str) -> bool:
        """Check if a table exists in the database."""
        conn = await db.get_db()
        try:
            cursor = await conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                (table_name,)
            )
            row = await cursor.fetchone()
            return row is not None
        finally:
            await conn.close()

    async def _index_exists(self, index_name: str) -> bool:
        """Check if an index exists."""
        conn = await db.get_db()
        try:
            cursor = await conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND name=?",
                (index_name,)
            )
            row = await cursor.fetchone()
            return row is not None
        finally:
            await conn.close()

    def test_users_table_created(self):
        async def _test():
            assert await self._table_exists("users")
        import asyncio
        asyncio.run(_test())

    def test_conversations_table_created(self):
        async def _test():
            assert await self._table_exists("conversations")
        import asyncio
        asyncio.run(_test())

    def test_messages_table_created(self):
        async def _test():
            assert await self._table_exists("messages")
        import asyncio
        asyncio.run(_test())

    def test_users_table_columns(self):
        """Verify users table has expected columns."""
        async def _test():
            conn = await db.get_db()
            try:
                cursor = await conn.execute("PRAGMA table_info(users)")
                columns = {col[1]: col for col in await cursor.fetchall()}
                assert "id" in columns
                assert columns["id"][2] == "INTEGER"
                assert "account" in columns
                assert columns["account"][3] == 1  # not null
                assert "username" in columns
                assert "password_hash" in columns
                assert "created_at" in columns
                assert "password_changed_at" in columns
            finally:
                await conn.close()

        import asyncio
        asyncio.run(_test())

    def test_conversations_table_columns(self):
        async def _test():
            conn = await db.get_db()
            try:
                cursor = await conn.execute("PRAGMA table_info(conversations)")
                columns = {col[1]: col for col in await cursor.fetchall()}
                assert "session_id" in columns
                assert "user_id" in columns
                assert columns["user_id"][3] == 1  # not null
                assert "name" in columns
                assert "created_at" in columns
                assert "updated_at" in columns
            finally:
                await conn.close()

        import asyncio
        asyncio.run(_test())

    def test_messages_table_columns(self):
        async def _test():
            conn = await db.get_db()
            try:
                cursor = await conn.execute("PRAGMA table_info(messages)")
                columns = {col[1]: col for col in await cursor.fetchall()}
                assert "id" in columns
                assert "session_id" in columns
                assert "role" in columns
                assert "content" in columns
                assert "metadata" in columns
                assert "created_at" in columns
            finally:
                await conn.close()

        import asyncio
        asyncio.run(_test())

    def test_conversations_user_id_index(self):
        async def _test():
            assert await self._index_exists("idx_conversations_user_id")
        import asyncio
        asyncio.run(_test())

    def test_conversations_updated_at_index(self):
        async def _test():
            assert await self._index_exists("idx_conversations_updated_at")
        import asyncio
        asyncio.run(_test())

    def test_messages_session_id_index(self):
        async def _test():
            assert await self._index_exists("idx_messages_session_id")
        import asyncio
        asyncio.run(_test())

    def test_init_db_is_idempotent(self):
        """Running init_db multiple times should not raise errors."""
        async def _test():
            # init_db already ran from fixture, run again
            await db.init_db()
            # Tables should still exist
            assert await self._table_exists("users")
            assert await self._table_exists("conversations")
            assert await self._table_exists("messages")

        import asyncio
        asyncio.run(_test())


# ============================================================
# DB_PATH validation
# ============================================================

class TestDbPath:
    """Test the database path configuration."""

    def test_db_path_is_absolute(self):
        assert isinstance(db.DB_PATH, Path)
        assert db.DB_PATH.is_absolute()

    def test_db_path_ends_in_db(self):
        assert db.DB_PATH.name == "arknights_rag.db"

    def test_db_path_parent_exists(self):
        assert db.DB_PATH.parent.exists()

    def test_db_path_parent_is_data_dir(self):
        """生产配置下 DB_PATH 必须落在 <repo>/data/。

        注意：conftest.py 会在测试期把 backend.db.DB_PATH 覆盖为临时库（避免测试污染本地
        开发数据），所以这里不能直接断言当前生效值，改为反推源码中声明的默认值。
        """
        import re

        source = Path(db.__file__).read_text(encoding="utf-8")
        default_expr = re.search(r"^DB_PATH\s*=\s*(.+)$", source, re.MULTILINE).group(1)
        declared = eval(default_expr, {"Path": Path, "__file__": db.__file__})  # noqa: S307
        assert declared.parent == BASE_DIR / "data"
        assert declared.name == "arknights_rag.db"
