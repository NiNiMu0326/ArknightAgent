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

    def test_keyword_inside_string_literal_is_allowed(self):
        # 字面量里的关键字是数据不是操作：按整条 SQL 文本扫描会把
        # WHERE name = 'Update' 这类合法查询误拒，因此只对词 token 判定。
        cleaned = sq._clean_sql("SELECT name FROM operators WHERE name = 'DROP TABLE'")
        assert "'DROP TABLE'" in cleaned
        assert f"LIMIT {sq.MAX_ROWS}" in cleaned

    def test_keyword_outside_string_literal_still_blocked(self):
        # 放行字面量不能退化成取消关键字过滤：字面量之外的同名关键字仍必须拒绝
        with pytest.raises(ValueError, match="不允许使用"):
            sq._clean_sql("SELECT name FROM operators WHERE name = 'DROP TABLE' DROP")


# ============================================================
# T04/T24: adversarial 用例集 —— 表名白名单对抗
# ============================================================

class TestAdversarialTableNames:
    """防的回归：旧实现只用正则扫 FROM 后的「裸词」表名，于是
    `FROM 'sqlite_master'`（单引号字面量在标识符位置会被 SQLite 当表名）、
    逗号连接与 JOIN 都能绕过白名单，直接读出白名单外的表（含 sqlite_master）。"""

    @pytest.mark.parametrize("sql", [
        # 单引号字面量形式的表名（SQLite 历史遗留特性：标识符位置的字面量即表名）
        "SELECT * FROM 'sqlite_master'",
        "SELECT name FROM 'sqlite_master'",
        # 逗号连接多表
        "SELECT * FROM operators, sqlite_master",
        "SELECT * FROM operators o, sqlite_master m",
        # JOIN
        "SELECT * FROM operators JOIN sqlite_master ON 1=1",
        "SELECT * FROM operators INNER JOIN sqlite_master USING (name)",
        "SELECT * FROM operators LEFT JOIN sqlite_master ON 1=1",
        # 大小写 / 引号变体（SQLite 标识符比较是 ASCII 大小写不敏感）
        "SELECT * FROM SQLITE_MASTER",
        'SELECT * FROM "sqlite_master"',
        "SELECT * FROM [sqlite_master]",
        "SELECT * FROM `sqlite_master`",
        # 子查询与 UNION 里的 FROM 同样要检查
        "SELECT * FROM (SELECT * FROM sqlite_master)",
        "SELECT name FROM operators UNION SELECT name FROM sqlite_master",
        # 近似名不能被当成白名单表
        "SELECT * FROM operators2",
        "SELECT * FROM sqlite_master2",
    ])
    def test_reads_outside_whitelist_are_rejected(self, sql):
        with pytest.raises(ValueError, match="不允许查询表"):
            sq._clean_sql(sql)

    @pytest.mark.parametrize("sql", [
        "SELECT * FROM operators",
        "SELECT * FROM enemies e WHERE e.rank = '领袖'",
        'SELECT * FROM "operators"',
        "SELECT * FROM (SELECT * FROM operators)",
        "SELECT o.name FROM operators o JOIN enemies e ON o.id = e.id",
    ])
    def test_whitelisted_forms_still_pass(self, sql):
        """正向对照：白名单表的各种写法必须放行，避免上面那条靠「一律拒绝」通过。"""
        assert sq._clean_sql(sql)


class TestAdversarialCommentsAndLimits:
    """注释与 LIMIT 的清洗。

    防的回归：LIMIT 是直接拼在 SQL 末尾的 —— 语句以行注释/块注释结尾时，
    追加的 LIMIT 会被注释吞掉，查询失去行数上限；`LIMIT -1`（SQLite 的「无上限」）
    与 `LIMIT 1e9` 也会绕过上限。这些都不该是「拒绝」，而是被清洗成有效上限。
    """

    @pytest.mark.parametrize("sql", [
        "SELECT name FROM operators -- 尾随行注释",
        "SELECT name FROM operators /* 尾随块注释 */",
        "SELECT name FROM operators -- 注释里带分号 ; DROP TABLE operators",
    ])
    def test_trailing_comment_cannot_swallow_enforced_limit(self, sql):
        cleaned = sq._clean_sql(sql)
        # LIMIT 必须真的落在语句末尾（被注释吞掉就等价于没有上限）
        assert cleaned.rstrip().endswith(f"LIMIT {sq.MAX_ROWS}")
        assert "--" not in cleaned and "/*" not in cleaned

    @pytest.mark.parametrize("sql", [
        "SELECT name FROM operators LIMIT -1",     # SQLite: 负值 = 无上限
        "SELECT name FROM operators LIMIT 1e9",
        "SELECT name FROM operators LIMIT 0",
        "SELECT name FROM operators LIMIT 100000",
        "SELECT name FROM operators LIMIT (SELECT 1)",
        "SELECT name FROM operators LIMIT 1.5",    # 非整数
        "SELECT name FROM operators LIMIT ٥٠",     # str.isdigit() 认非 ASCII 数字，必须用 isascii() 兜住
        "SELECT name FROM operators LIMIT +50",    # 带符号表达式，不是纯整数字面量
    ])
    def test_unsafe_limit_is_capped(self, sql):
        cleaned = sq._clean_sql(sql)
        assert f"LIMIT {sq.MAX_ROWS}" in cleaned
        assert "-1" not in cleaned.split("LIMIT")[-1]
        assert "1e9" not in cleaned

    def test_safe_limit_and_offset_form_preserved(self):
        """`LIMIT a, b` 的 b 才是行数：合法值不该被改写。"""
        assert sq._clean_sql("SELECT name FROM operators LIMIT 10, 5").endswith("LIMIT 10, 5")

    @pytest.mark.parametrize("sql", [
        "SELECT 1; SELECT 2",
        "SELECT 1; /* 块注释 */ SELECT 2",
        "SELECT name FROM operators;-- 注释后接第二条\nSELECT 2",
    ])
    def test_multiple_statements_rejected(self, sql):
        with pytest.raises(ValueError, match="多条语句"):
            sq._clean_sql(sql)

    def test_single_trailing_semicolon_is_stripped_not_rejected(self):
        cleaned = sq._clean_sql("SELECT name FROM operators;")
        assert ";" not in cleaned
        assert cleaned.rstrip().endswith(f"LIMIT {sq.MAX_ROWS}")


class TestAdversarialLiteralsAllowed:
    """防的回归：为了拦表名/关键字而按整条 SQL 文本扫描，会把字面量里的
    'Update' / '%drop%' / 'FROM x' 误判成操作或表名，合法查询被 422 拒绝。"""

    @pytest.mark.parametrize("sql,needle", [
        ("SELECT name FROM operators WHERE name = 'Update'", "'Update'"),
        ("SELECT name FROM operators WHERE name = 'Delete'", "'Delete'"),
        ("SELECT name FROM operators WHERE name LIKE '%drop%'", "'%drop%'"),
        ("SELECT name FROM operators WHERE name = 'FROM x'", "'FROM x'"),
        ("SELECT name FROM operators WHERE name = 'JOIN sqlite_master'", "'JOIN sqlite_master'"),
        ("SELECT name FROM operators WHERE description LIKE '%DROP TABLE%'", "'%DROP TABLE%'"),
    ])
    def test_literals_are_data_not_operations(self, sql, needle):
        cleaned = sq._clean_sql(sql)
        assert needle in cleaned                       # 字面量原样保留
        assert cleaned.rstrip().endswith(f"LIMIT {sq.MAX_ROWS}")

    def test_subquery_from_is_checked_not_bypassed(self):
        """子查询里的表名仍然受白名单约束（放行合法子查询不等于放行全部子查询）。"""
        assert sq._clean_sql("SELECT * FROM (SELECT * FROM operators)")
        with pytest.raises(ValueError, match="不允许查询表"):
            sq._clean_sql("SELECT * FROM (SELECT * FROM sqlite_master)")

    def test_literal_in_table_slot_is_still_a_table_name(self):
        """表名槽位上的字面量是表名而非数据：`FROM 'FROM x'` 必须拒绝。"""
        with pytest.raises(ValueError, match="不允许查询表"):
            sq._clean_sql("SELECT * FROM 'FROM x'")


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
