"""
Tests for backend.agent.structured_query: SQL sanitization (_clean_sql)
and execute_structured_query against a real temporary SQLite database.
Usage: cd test && python -m pytest test_structured_query.py -v
"""
import asyncio
import sqlite3
import sys
import pytest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from backend.agent import structured_query as sq


def run(coro):
    return asyncio.run(coro)


# ============================================================
# _clean_sql — validation & sanitization
# ============================================================

class TestCleanSql:
    def test_valid_select_passes(self):
        sql = "SELECT name FROM operators WHERE rarity = 6"
        cleaned = sq._clean_sql(sql)
        assert cleaned.startswith("SELECT")

    def test_auto_appends_limit(self):
        cleaned = sq._clean_sql("SELECT name FROM operators")
        assert f"LIMIT {sq.MAX_ROWS}" in cleaned

    def test_existing_limit_preserved(self):
        cleaned = sq._clean_sql("SELECT name FROM operators LIMIT 5")
        assert cleaned.count("LIMIT") == 1
        assert "LIMIT 5" in cleaned

    def test_existing_oversized_limit_is_capped(self):
        cleaned = sq._clean_sql("SELECT name FROM operators LIMIT 100000")
        assert f"LIMIT {sq.MAX_ROWS}" in cleaned

    def test_quoted_unknown_table_rejected(self):
        with pytest.raises(ValueError, match="不允许查询表"):
            sq._clean_sql('SELECT * FROM "users"')

    def test_quoted_allowed_table_passes(self):
        cleaned = sq._clean_sql('SELECT * FROM "operators"')
        assert "operators" in cleaned

    def test_strips_markdown_code_block(self):
        cleaned = sq._clean_sql("```sql\nSELECT name FROM operators LIMIT 1\n```")
        assert "```" not in cleaned
        assert "SELECT" in cleaned

    def test_strips_plain_code_block(self):
        cleaned = sq._clean_sql("```\nSELECT name FROM enemies LIMIT 1\n```")
        assert "```" not in cleaned

    def test_rejects_non_select(self):
        with pytest.raises(ValueError, match="只允许 SELECT"):
            sq._clean_sql("DELETE FROM operators")

    @pytest.mark.parametrize("keyword", [
        "DROP", "DELETE", "UPDATE", "INSERT", "ALTER", "CREATE",
        "ATTACH", "PRAGMA", "TRUNCATE", "VACUUM",
    ])
    def test_rejects_dangerous_keywords(self, keyword):
        with pytest.raises(ValueError, match="不允许使用"):
            sq._clean_sql(f"SELECT name FROM operators; {keyword} TABLE operators")

    def test_dangerous_keyword_case_insensitive(self):
        with pytest.raises(ValueError, match="不允许使用"):
            sq._clean_sql("select name from operators where name = 'x' drop")

    def test_rejects_unknown_table(self):
        with pytest.raises(ValueError, match="不允许查询表"):
            sq._clean_sql("SELECT * FROM users")

    def test_rejects_unknown_join_table(self):
        with pytest.raises(ValueError, match="不允许查询表"):
            sq._clean_sql("SELECT * FROM operators JOIN secrets ON 1=1")

    def test_allows_both_whitelisted_tables(self):
        cleaned = sq._clean_sql(
            "SELECT o.name, e.name FROM operators o JOIN enemies e ON o.id = e.id LIMIT 3"
        )
        assert "operators" in cleaned and "enemies" in cleaned

    def test_keyword_inside_string_literal_is_blocked(self):
        # 'DROP' as data still triggers the keyword filter — acceptable strictness
        with pytest.raises(ValueError):
            sq._clean_sql("SELECT name FROM operators WHERE name = 'DROP TABLE'")


# ============================================================
# execute_structured_query
# ============================================================

@pytest.fixture
def temp_db(tmp_path):
    db_file = tmp_path / "test_structured.db"
    conn = sqlite3.connect(str(db_file))
    conn.execute(
        "CREATE TABLE operators (id INTEGER PRIMARY KEY, name TEXT, rarity INTEGER, class TEXT)"
    )
    conn.executemany(
        "INSERT INTO operators (name, rarity, class) VALUES (?, ?, ?)",
        [("银灰", 6, "近卫"), ("能天使", 6, "狙击"), ("阿米娅", 5, "术师")],
    )
    conn.execute(
        "CREATE TABLE enemies (id INTEGER PRIMARY KEY, name TEXT, rank TEXT, hp INTEGER)"
    )
    conn.executemany(
        "INSERT INTO enemies (name, rank, hp) VALUES (?, ?, ?)",
        [("弑君者", "领袖", 28000), ("源石虫", "普通", 500)],
    )
    conn.commit()
    conn.close()
    return db_file


class TestExecuteStructuredQuery:
    def test_empty_sql_returns_error_with_schema(self):
        result = run(sq.execute_structured_query({}))
        assert result["error"] == "sql parameter is required"
        assert "schema" in result

    def test_invalid_sql_returns_error_with_schema(self):
        result = run(sq.execute_structured_query({"sql": "DROP TABLE operators"}))
        assert "error" in result
        assert "schema" in result

    def test_db_not_initialized(self, tmp_path):
        missing = tmp_path / "nonexistent.db"
        with patch.object(sq, "DB_PATH", missing):
            result = run(sq.execute_structured_query({"sql": "SELECT * FROM operators"}))
            assert "结构化数据库未初始化" in result["error"]

    def test_successful_query(self, temp_db):
        with patch.object(sq, "DB_PATH", temp_db):
            result = run(sq.execute_structured_query(
                {"sql": "SELECT name, rarity FROM operators WHERE rarity = 6 ORDER BY name"}
            ))
            assert result["row_count"] == 2
            assert result["columns"] == ["name", "rarity"]
            names = [r["name"] for r in result["rows"]]
            assert "银灰" in names and "能天使" in names
            assert result["sql"].endswith(f"LIMIT {sq.MAX_ROWS}")

    def test_query_with_aggregation(self, temp_db):
        with patch.object(sq, "DB_PATH", temp_db):
            result = run(sq.execute_structured_query(
                {"sql": "SELECT class, COUNT(*) AS cnt FROM operators GROUP BY class"}
            ))
            assert result["row_count"] == 3

    def test_enemies_table_query(self, temp_db):
        with patch.object(sq, "DB_PATH", temp_db):
            result = run(sq.execute_structured_query(
                {"sql": "SELECT name, hp FROM enemies WHERE rank = '领袖'"}
            ))
            assert result["row_count"] == 1
            assert result["rows"][0]["name"] == "弑君者"

    def test_sql_execution_error(self, temp_db):
        with patch.object(sq, "DB_PATH", temp_db):
            result = run(sq.execute_structured_query(
                {"sql": "SELECT no_such_column FROM operators"}
            ))
            assert "error" in result
            assert "SQL 执行错误" in result["error"]
            assert "schema" in result

    def test_respects_explicit_limit(self, temp_db):
        with patch.object(sq, "DB_PATH", temp_db):
            result = run(sq.execute_structured_query(
                {"sql": "SELECT name FROM operators LIMIT 1"}
            ))
            assert result["row_count"] == 1
