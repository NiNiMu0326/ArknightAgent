"""
Structured query tool for Arknights data.
Allows LLM to query operator/enemy data via SQL on pre-built SQLite tables.
"""

import sqlite3
import logging
import asyncio
import contextlib
import functools
import re
from collections import namedtuple
from pathlib import Path
from typing import Dict, Any

logger = logging.getLogger(__name__)

# asyncio.to_thread 在 Python 3.9 才加入；服务器运行的是 3.8.10，此处提供兼容回退。
if not hasattr(asyncio, "to_thread"):
    async def _to_thread(func, *args, **kwargs):
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, functools.partial(func, *args, **kwargs))

    asyncio.to_thread = _to_thread

# Path to the SQLite database
DB_PATH = Path(__file__).parent.parent.parent / "data" / "arknights_structured.db"

# Schema description given to the LLM
SCHEMA_DESCRIPTION = """
## operators 表（干员数据）
| 列名 | 类型 | 说明 |
|------|------|------|
| id | INTEGER | 主键 |
| name | TEXT | 干员名 |
| name_en | TEXT | 干员外文名 |
| rarity | INTEGER | 星级 (1-6) |
| class | TEXT | 职业（近卫/狙击/重装/术师/医疗/辅助/特种/先锋） |
| branch | TEXT | 分支（如 无畏者/领主/剑豪 等） |
| trait | TEXT | 特性描述 |
| faction | TEXT | 所属势力 |
| obtain_method | TEXT | 获得方式 |
| artist | TEXT | 画师 |
| cv_cn | TEXT | 中文配音 |
| cv_jp | TEXT | 日文配音 |
| cv_en | TEXT | 英文配音 |
| hp_elite2 | INTEGER | 精英2满级生命上限 |
| atk_elite2 | INTEGER | 精英2满级攻击力 |
| def_elite2 | INTEGER | 精英2满级防御力 |
| mres_elite2 | INTEGER | 精英2满级法术抗性 |
| redeploy_time | TEXT | 再部署时间 |
| dp_cost | TEXT | 部署费用 |
| block_count | INTEGER | 阻挡数 |
| attack_speed | TEXT | 攻击速度 |
| release_date | TEXT | 上线时间 |

## enemies 表（敌人数据）
| 列名 | 类型 | 说明 |
|------|------|------|
| id | INTEGER | 主键 |
| name | TEXT | 名称 |
| enemy_index | TEXT | 敌人索引 |
| category | TEXT | 种类 |
| rank | TEXT | 地位级别（普通/精英/领袖） |
| attack_type | TEXT | 攻击类型（近战/远程） |
| damage_type | TEXT | 伤害类型（物理/法术） |
| movement | TEXT | 行动方式（地面/飞行） |
| description | TEXT | 描述 |
| ability | TEXT | 能力描述 |
| stages | TEXT | 出场关卡 |
| hp | INTEGER | 最大生命值 |
| atk | INTEGER | 攻击力 |
| def | INTEGER | 防御力 |
| mres | INTEGER | 法术抗性 |
| move_speed | REAL | 移动速度 |
| attack_interval | REAL | 攻击间隔 |

查询提示：
- 字符串匹配使用 LIKE '%keyword%' 或 = 'exact'
- 数值比较使用 >、<、>=、<=、=
- 排序使用 ORDER BY column ASC/DESC
- 限制结果数使用 LIMIT N
- 计数使用 COUNT(*)
- 分组使用 GROUP BY
- 示例：SELECT name, rarity, atk_elite2 FROM operators WHERE class='近卫' AND atk_elite2 > 700 ORDER BY atk_elite2 DESC
- 示例：SELECT name, hp, atk, def FROM enemies WHERE rank='领袖' ORDER BY hp DESC LIMIT 10
"""

MAX_ROWS = 50  # Maximum rows to return

# Whitelist: only these tables are allowed in queries
ALLOWED_TABLES = {"operators", "enemies"}

# Dangerous SQL patterns (checked case-insensitively after stripping comments)
_DANGEROUS_RE = re.compile(
    r'\b(DROP|DELETE|UPDATE|INSERT|ALTER|CREATE|EXEC|EXECUTE|ATTACH|DETACH|PRAGMA|'
    r'GRANT|REVOKE|TRUNCATE|REINDEX|VACUUM)\b',
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# 轻量 SQL 分词：把「字符串字面量 / 带引号标识符 / 注释 / 词 / 标点」区分开，
# 让分号、注释、表名与 LIMIT 的判定不会被字面量或注释里的内容干扰。
# ---------------------------------------------------------------------------

_Token = namedtuple("_Token", "kind text start end")

# FROM 子句表名列表的结束标志（这些关键字出现即说明表名已列举完）
_FROM_CLAUSE_END = frozenset({
    "WHERE", "GROUP", "ORDER", "HAVING", "LIMIT", "OFFSET", "WINDOW",
    "UNION", "INTERSECT", "EXCEPT", "ON", "USING", "JOIN", "INNER",
    "LEFT", "RIGHT", "FULL", "CROSS", "NATURAL", "OUTER", "SET",
    "VALUES", "RETURNING",
})

# LIMIT 值表达式的结束标志
_LIMIT_VALUE_END = frozenset({
    "OFFSET", "UNION", "INTERSECT", "EXCEPT", "RETURNING", "WINDOW",
    "GROUP", "ORDER", "HAVING", "WHERE", "LIMIT",
})


def _tokenize(sql: str) -> list:
    """把 SQL 切成 token 序列（注释丢弃，字面量整段保留）。"""
    tokens = []
    i, n = 0, len(sql)
    while i < n:
        ch = sql[i]
        if ch.isspace():
            i += 1
        elif sql.startswith("--", i):  # 行注释
            newline = sql.find("\n", i)
            i = n if newline == -1 else newline + 1
        elif sql.startswith("/*", i):  # 块注释
            close = sql.find("*/", i + 2)
            i = n if close == -1 else close + 2
        elif ch in ("'", '"', '`'):  # 字符串字面量 / 双引号、反引号标识符
            j = i + 1
            while j < n:
                if sql[j] == ch:
                    if j + 1 < n and sql[j + 1] == ch:  # '' 表示转义引号
                        j += 2
                        continue
                    j += 1
                    break
                j += 1
            tokens.append(_Token("string" if ch == "'" else "quoted", sql[i:j], i, j))
            i = j
        elif ch == "[":  # 方括号标识符
            close = sql.find("]", i + 1)
            j = n if close == -1 else close + 1
            tokens.append(_Token("quoted", sql[i:j], i, j))
            i = j
        elif ch.isalnum() or ch in "_$":  # 词/数字（1e9、1.5 等整体保留）
            j = i
            while j < n and (sql[j].isalnum() or sql[j] in "_$."):
                j += 1
            tokens.append(_Token("word", sql[i:j], i, j))
            i = j
        else:
            tokens.append(_Token("punct", ch, i, i + 1))
            i += 1
    return tokens


def _strip_trailing_noise(sql: str) -> str:
    """剥掉尾部的分号与注释（字面量里的 `;` / `--` 不受影响）。

    `SELECT ...;` 追加 LIMIT 后会变成多条语句；以行注释结尾时追加的 LIMIT
    会被注释吞掉，结果集失去上限。两者都在这里先消除。
    """
    end = 0
    for tok in reversed(_tokenize(sql)):
        if tok.kind == "punct" and tok.text == ";":
            continue  # 尾部的分号直接丢弃
        end = tok.end
        break
    return sql[:end].strip()


def _iter_from_clause_tables(tokens: list, start: int):
    """从 FROM/JOIN 之后的 token 起，产出该子句里的表名 token。

    覆盖逗号连接的多表（含 `(子查询) x, 表` 这种写法）；
    子查询内部的表名由外层对 FROM/JOIN 的整体扫描单独处理。

    表名位置的 `string`（单引号字面量）也必须产出：SQLite 在标识符位置会把这个
    字面量当表名用（历史遗留特性），`SELECT * FROM 'sqlite_master'` 实测能读出
    白名单外的表。漏掉它等于把该位置的字面量当成「不是表名」直接跳过。
    位置判定仍然靠 expect_ref：只在 FROM/JOIN 之后的表名槽位（以及逗号后的槽位）
    才产出，因此 `WHERE name = 'FROM x'`、`LIKE '%drop%'` 这类字面量不受影响。
    """
    depth = 0
    expect_ref = True
    for tok in tokens[start:]:
        if tok.kind == "punct" and tok.text == "(":
            depth += 1
            expect_ref = False
        elif tok.kind == "punct" and tok.text == ")":
            if depth == 0:
                break
            depth -= 1
            expect_ref = False
        elif depth == 0:
            if tok.kind == "word" and tok.text.upper() in _FROM_CLAUSE_END:
                break
            if tok.kind == "punct" and tok.text == ",":
                expect_ref = True  # 逗号后是同一 FROM 子句里的下一张表
            elif expect_ref and tok.kind in ("word", "quoted", "string"):
                yield tok
                expect_ref = False
            else:
                expect_ref = False  # 表别名等，跳过


def _ascii_lower(text: str) -> str:
    """只折叠 ASCII 大写字母——SQLite 的标识符比较就是 ASCII 大小写不敏感。"""
    return "".join(chr(ord(c) + 32) if "A" <= c <= "Z" else c for c in text)


def _normalize_table_ref(tok) -> str:
    """把一个表名 token 还原成 SQLite 实际使用的标识符文本。

    - 词（`word`）：原样。
    - 双引号 / 反引号 / 方括号（`quoted`）：剥掉外壳，双写转义还原。
    - 单引号字面量（`string`）：SQLite 在标识符位置直接拿字面量内容当标识符，
      不会再解析内容里的引号或方括号，也不 trim 空格（实测 `FROM '[operators]'`
      找的是名为 `[operators]` 的表）。所以这里只还原 `''` 转义、其余原样，
      保证「校验通过的」一定就是「SQLite 解析成白名单表的」。
    """
    text = tok.text
    if tok.kind == "string":
        if len(text) >= 2 and text.endswith("'"):
            return text[1:-1].replace("''", "'")
        return text[1:] if text.startswith("'") else text  # 未闭合字面量：fail-closed
    if text.startswith("["):
        return text[1:-1] if text.endswith("]") else text[1:]
    if len(text) >= 2 and text[0] == text[-1] and text[0] in ('"', '`'):
        return text[1:-1].replace(text[0] * 2, text[0])
    return text


def _limit_value_tokens(tokens: list, start: int) -> list:
    """取 LIMIT 关键字之后的值表达式 token（遇到 OFFSET 等关键字即结束）。"""
    value_tokens = []
    depth = 0
    for tok in tokens[start:]:
        if tok.kind == "punct" and tok.text == "(":
            depth += 1
        elif tok.kind == "punct" and tok.text == ")":
            if depth == 0:
                break
            depth -= 1
        elif depth == 0 and tok.kind == "word" and tok.text.upper() in _LIMIT_VALUE_END:
            break
        value_tokens.append(tok)
    return value_tokens


def _is_safe_limit(value_tokens: list) -> bool:
    """LIMIT 是否为 1..MAX_ROWS 的纯整数（SQLite 的 `LIMIT a, b` 以 b 为行数）。"""

    def _is_uint(tok):
        return tok.kind == "word" and tok.text.isascii() and tok.text.isdigit()

    if len(value_tokens) == 1 and _is_uint(value_tokens[0]):
        return 1 <= int(value_tokens[0].text) <= MAX_ROWS
    if (
        len(value_tokens) == 3
        and value_tokens[1].text == ","
        and _is_uint(value_tokens[0])
        and _is_uint(value_tokens[2])
    ):
        return 1 <= int(value_tokens[2].text) <= MAX_ROWS
    return False


def _clean_sql(sql: str) -> str:
    """Validate and sanitize an LLM-generated SQL query.

    - Only SELECT statements are allowed.
    - Dangerous keywords (DROP, DELETE, etc.) are blocked via precompiled regex.
    - Table names are checked against an allowlist（在 FROM/JOIN 的表名槽位上判定，
      含单引号字面量、带引号标识符、逗号连接与 JOIN）。
    - 多条语句、尾随分号与尾随注释会被拒绝或剥离。
    - A LIMIT clause is enforced: 缺失/非法/非正数/超限一律收敛为 MAX_ROWS。
    """
    cleaned = sql.strip()
    # Remove markdown code blocks if present
    cleaned = re.sub(r'^```(?:sql)?\s*\n?', '', cleaned)
    cleaned = re.sub(r'\n?```\s*$', '', cleaned)
    cleaned = cleaned.strip()

    # 先剥掉尾部的分号与注释，再做后续判定
    cleaned = _strip_trailing_noise(cleaned)
    tokens = _tokenize(cleaned)

    # Only allow SELECT（忽略前导注释，取第一个有效 token 判定）
    if not tokens or tokens[0].kind != "word" or tokens[0].text.upper() != "SELECT":
        raise ValueError("只允许 SELECT 查询")

    # Block dangerous keywords via precompiled regex.
    # 只在「词」token 上判定：字符串字面量/引号标识符里的同名内容（WHERE name = 'Update'、
    # LIKE '%drop%'）是数据而不是操作，按整条 SQL 文本扫描会把合法查询误拒。
    for tok in tokens:
        if tok.kind == "word" and _DANGEROUS_RE.fullmatch(tok.text):
            raise ValueError(f"不允许使用 {tok.text} 操作")

    # 拒绝多条语句：内部分号（字面量/注释里的分号已被分词排除）
    if any(tok.kind == "punct" and tok.text == ";" for tok in tokens):
        raise ValueError("不允许多条语句，一次只能执行一条 SELECT 查询")

    # Validate table names against allowlist.
    # 兼容双引号/方括号/反引号标识符与单引号字面量（SQLite 会把后者当表名用），
    # 并覆盖逗号连接的多表与 JOIN。
    table_refs = []
    for idx, tok in enumerate(tokens):
        if tok.kind == "word" and tok.text.upper() in ("FROM", "JOIN"):
            table_refs.extend(_iter_from_clause_tables(tokens, idx + 1))
    for ref in table_refs:
        normalized = _normalize_table_ref(ref)
        if _ascii_lower(normalized) not in ALLOWED_TABLES:
            raise ValueError(
                f"不允许查询表 '{normalized}'，只允许 {', '.join(sorted(ALLOWED_TABLES))} 表"
            )

    # Enforce LIMIT：只认顶层 LIMIT，缺失/非法/非正数/超限一律收敛到 MAX_ROWS
    limit_idx = None
    depth = 0
    for idx, tok in enumerate(tokens):
        if tok.kind == "punct" and tok.text == "(":
            depth += 1
        elif tok.kind == "punct" and tok.text == ")":
            depth = max(0, depth - 1)
        elif depth == 0 and tok.kind == "word" and tok.text.upper() == "LIMIT":
            limit_idx = idx

    if limit_idx is None:
        return f"{cleaned} LIMIT {MAX_ROWS}"

    value_tokens = _limit_value_tokens(tokens, limit_idx + 1)
    if _is_safe_limit(value_tokens):
        return cleaned

    # 用 MAX_ROWS 覆盖原来的 LIMIT 值表达式（LIMIT -1 / 1e9 / (子查询) 等）
    limit_end = tokens[limit_idx].end
    if not value_tokens:
        return f"{cleaned[:limit_end]} {MAX_ROWS}{cleaned[limit_end:]}"
    return f"{cleaned[:value_tokens[0].start]}{MAX_ROWS}{cleaned[value_tokens[-1].end:]}"


def _run_select_query(cleaned_sql: str) -> Dict:
    """同步执行只读 SELECT 并取回全部结果（由 asyncio.to_thread 调用）。

    sqlite3 是同步驱动，大表扫描/笛卡尔连接会长时间占用调用线程；放进线程池
    执行才不会卡住事件循环。
    """
    # 只读连接：即使白名单被绕过，也无法执行写操作。
    # contextlib.closing：`with sqlite3.connect(...)` 只负责事务提交/回滚、不关闭连接，
    # 异常路径下句柄要等 GC 才释放；这里保证连接在离开作用域时被关闭。
    db_uri = f"{DB_PATH.resolve().as_uri()}?mode=ro"
    with contextlib.closing(sqlite3.connect(db_uri, uri=True)) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.execute(cleaned_sql)
        rows = [dict(row) for row in cursor.fetchall()]
        columns = [desc[0] for desc in cursor.description] if cursor.description else []

        return {
            "columns": columns,
            "rows": rows,
            "row_count": len(rows),
            "sql": cleaned_sql,
        }


async def execute_structured_query(arguments: Dict[str, Any], session_id: str = "") -> Dict:
    """Execute arknights_structured_query tool.

    The SQL query is generated by the LLM based on the schema description.
    Returns {columns: [...], rows: [...], row_count: N, sql: "..."}
    """
    sql = arguments.get("sql", "")
    if not sql:
        return {"error": "sql parameter is required", "schema": SCHEMA_DESCRIPTION}

    try:
        cleaned_sql = _clean_sql(sql)
    except ValueError as e:
        return {"error": str(e), "schema": SCHEMA_DESCRIPTION}

    if not DB_PATH.exists():
        return {"error": "结构化数据库未初始化，请先运行数据同步脚本"}

    try:
        # 同步 sqlite3 查询放到线程池：事件循环线程不能被大表/笛卡尔连接卡住
        return await asyncio.to_thread(_run_select_query, cleaned_sql)
    except sqlite3.OperationalError as e:
        return {"error": f"SQL 执行错误: {str(e)}. 请检查表名是否正确。", "schema": SCHEMA_DESCRIPTION}
    except Exception as e:
        logger.error(f"Structured query failed: {e}", exc_info=True)
        return {"error": f"查询失败: {str(e)}"}
