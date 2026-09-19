"""
Sync operators and enemies data into SQLite for structured queries.
Run: python backend/data/sync_structured_db.py
"""

import json
import sqlite3
import logging
from pathlib import Path

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent.parent.parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "arknights_structured.db"


def _safe_int(value, default=0, field=""):
    """容错转 int：空串 / None / 非数字字符串一律回退 default 并记录坏数据。

    原始 JSON 里星级、敌人属性等字段可能是 ""、"null"、"3次" 之类的脏值，
    直接 int() 会抛 ValueError/TypeError 让整个 sync_operators/sync_enemies
    失败（一条坏数据毁掉整次同步）。字符串先 strip，空串视作缺失。
    """
    if isinstance(value, str):
        value = value.strip()
    if value is None or value == "":
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        logger.warning(f"坏数据: 字段 {field or '?'} 无法转 int: {value!r}，回退 {default}")
        return default


def _safe_float(value, default=0.0, field=""):
    """容错转 float，语义同 _safe_int。"""
    if isinstance(value, str):
        value = value.strip()
    if value is None or value == "":
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        logger.warning(f"坏数据: 字段 {field or '?'} 无法转 float: {value!r}，回退 {default}")
        return default


def parse_operator_stats(stats_dict):
    """Parse 生命上限_攻击_防御_法术抗性 field.
    Returns (hp, atk, def, mres) from 精英2_满级, or (0,0,0,0) if not available."""
    if not stats_dict or not isinstance(stats_dict, dict):
        return 0, 0, 0, 0
    elite2 = stats_dict.get("精英2_满级", "")
    if not elite2:
        return 0, 0, 0, 0
    parts = str(elite2).strip().split()
    return (
        _safe_int(parts[0], field="精英2_满级.hp") if len(parts) > 0 else 0,
        _safe_int(parts[1], field="精英2_满级.atk") if len(parts) > 1 else 0,
        _safe_int(parts[2], field="精英2_满级.def") if len(parts) > 2 else 0,
        _safe_int(parts[3], field="精英2_满级.mres") if len(parts) > 3 else 0,
    )


def parse_block_count(block_str):
    """Parse 阻挡数 field."""
    if not block_str:
        return 0
    return _safe_int(block_str, field="阻挡数")


def sync_operators(conn):
    """Import operators data."""
    operators_file = DATA_DIR / "all_operators.json"
    if not operators_file.exists():
        logger.error(f"Operators file not found: {operators_file}")
        return

    with open(operators_file, 'r', encoding='utf-8') as f:
        operators = json.load(f)

    conn.execute("DELETE FROM operators")

    count = 0
    for op in operators:
        name = op.get("干员名", "")
        if not name:
            continue

        stats = op.get("生命上限_攻击_防御_法术抗性", {})
        hp, atk, def_, mres = parse_operator_stats(stats)

        cv = op.get("配音", {}) or {}

        conn.execute("""
            INSERT INTO operators (
                name, name_en, rarity, class, branch, trait,
                faction, obtain_method, artist,
                cv_cn, cv_jp, cv_en,
                hp_elite2, atk_elite2, def_elite2, mres_elite2,
                redeploy_time, dp_cost, block_count, attack_speed,
                release_date
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            name,
            op.get("干员外文名", ""),
            _safe_int(op.get("星级", 0), field="星级"),
            op.get("职业", ""),
            op.get("分支", ""),
            op.get("特性", ""),
            op.get("所属势力") or op.get("隐藏势力") or "",
            op.get("获得方式", ""),
            op.get("画师", ""),
            cv.get("中文", ""),
            cv.get("日文", ""),
            cv.get("英文", ""),
            hp, atk, def_, mres,
            op.get("再部署", ""),
            op.get("部署费用", ""),
            parse_block_count(op.get("阻挡数", "0")),
            op.get("攻击速度", ""),
            op.get("上线时间", ""),
        ))
        count += 1

    conn.commit()
    logger.info(f"Synced {count} operators")


def sync_enemies(conn):
    """Import enemies data."""
    enemies_file = DATA_DIR / "all_enemies.json"
    if not enemies_file.exists():
        logger.error(f"Enemies file not found: {enemies_file}")
        return

    with open(enemies_file, 'r', encoding='utf-8') as f:
        enemies = json.load(f)

    conn.execute("DELETE FROM enemies")

    count = 0
    for enemy in enemies:
        name = enemy.get("名称", "")
        if not name:
            continue

        # Get level 0 stats
        level_data = enemy.get("级别数据", [])
        stats = {}
        if isinstance(level_data, list) and level_data:
            first_level = level_data[0]
            # 「属性」可能存在但值为 null：.get 的默认值只在键缺失时生效，
            # 之后 stats.get 会 AttributeError 打断整次同步，这里显式判类型
            if isinstance(first_level, dict):
                stats = first_level.get("属性") or {}
                if not isinstance(stats, dict):
                    logger.warning(f"坏数据: 敌人 {name} 的级别属性不是对象，按空属性处理")
                    stats = {}

        conn.execute("""
            INSERT INTO enemies (
                name, enemy_index, category, rank, attack_type, damage_type,
                movement, description, ability, stages,
                hp, atk, def, mres, move_speed, attack_interval
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            name,
            enemy.get("敌人索引", ""),
            enemy.get("种类", ""),
            enemy.get("地位级别", ""),
            enemy.get("攻击类型", ""),
            enemy.get("伤害类型", ""),
            enemy.get("行动方式", ""),
            enemy.get("描述", ""),
            enemy.get("能力", ""),
            enemy.get("出场关卡", ""),
            _safe_int(stats.get("最大生命值", 0), field=f"{name}.最大生命值"),
            _safe_int(stats.get("攻击力", 0), field=f"{name}.攻击力"),
            _safe_int(stats.get("防御力", 0), field=f"{name}.防御力"),
            _safe_int(stats.get("法术抗性", 0), field=f"{name}.法术抗性"),
            _safe_float(stats.get("移动速度", 0), field=f"{name}.移动速度"),
            _safe_float(stats.get("攻击间隔", 0), field=f"{name}.攻击间隔"),
        ))
        count += 1

    conn.commit()
    logger.info(f"Synced {count} enemies")


def init_db(conn):
    """Create tables if not exist."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS operators (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            name_en TEXT DEFAULT '',
            rarity INTEGER DEFAULT 0,
            class TEXT DEFAULT '',
            branch TEXT DEFAULT '',
            trait TEXT DEFAULT '',
            faction TEXT DEFAULT '',
            obtain_method TEXT DEFAULT '',
            artist TEXT DEFAULT '',
            cv_cn TEXT DEFAULT '',
            cv_jp TEXT DEFAULT '',
            cv_en TEXT DEFAULT '',
            hp_elite2 INTEGER DEFAULT 0,
            atk_elite2 INTEGER DEFAULT 0,
            def_elite2 INTEGER DEFAULT 0,
            mres_elite2 INTEGER DEFAULT 0,
            redeploy_time TEXT DEFAULT '',
            dp_cost TEXT DEFAULT '',
            block_count INTEGER DEFAULT 0,
            attack_speed TEXT DEFAULT '',
            release_date TEXT DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS enemies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            enemy_index TEXT DEFAULT '',
            category TEXT DEFAULT '',
            rank TEXT DEFAULT '',
            attack_type TEXT DEFAULT '',
            damage_type TEXT DEFAULT '',
            movement TEXT DEFAULT '',
            description TEXT DEFAULT '',
            ability TEXT DEFAULT '',
            stages TEXT DEFAULT '',
            hp INTEGER DEFAULT 0,
            atk INTEGER DEFAULT 0,
            def INTEGER DEFAULT 0,
            mres INTEGER DEFAULT 0,
            move_speed REAL DEFAULT 0,
            attack_interval REAL DEFAULT 0
        );

        CREATE INDEX IF NOT EXISTS idx_operators_name ON operators(name);
        CREATE INDEX IF NOT EXISTS idx_operators_class ON operators(class);
        CREATE INDEX IF NOT EXISTS idx_operators_rarity ON operators(rarity);
        CREATE INDEX IF NOT EXISTS idx_operators_atk ON operators(atk_elite2);
        CREATE INDEX IF NOT EXISTS idx_enemies_name ON enemies(name);
        CREATE INDEX IF NOT EXISTS idx_enemies_rank ON enemies(rank);
    """)
    conn.commit()


def main():
    conn = sqlite3.connect(str(DB_PATH))
    try:
        init_db(conn)
        sync_operators(conn)
        sync_enemies(conn)
        logger.info(f"Database synced to {DB_PATH}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
