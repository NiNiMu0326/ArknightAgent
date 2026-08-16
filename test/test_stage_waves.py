"""
Tests for backend.agent.stage_waves: spawn-order extraction from prts-mcp data.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import asyncio

from backend.agent import stage_waves as sw


def _reset_caches():
    sw._stage_table = {}
    sw._stage_table_loaded = False
    sw._enemy_names = {}
    sw._enemy_names_loaded = False
    sw._levels_root = None


def _write_fake_prts_data(tmp_path):
    gamedata = tmp_path / "gamedata/.releases/r1/zh_CN/gamedata"
    gamedata.mkdir(parents=True)
    (gamedata / "excel").mkdir()
    (gamedata / "excel/stage_table.json").write_text(json.dumps({
        "stages": {
            "main_01-07": {
                "stageId": "main_01-07",
                "code": "1-7",
                "name": "暴君",
                "levelId": "Obt/Main/level_main_01-07",
                "difficulty": "NORMAL",
            }
        }
    }, ensure_ascii=False), encoding="utf-8")
    (gamedata / "excel/enemy_handbook_table.json").write_text(json.dumps({
        "enemyData": {
            "enemy_1007_slime_2": {"name": "源石虫·α"},
            "enemy_1027_mob": {"name": "暴徒"},
        }
    }, ensure_ascii=False), encoding="utf-8")

    levels = tmp_path / "gamedata-levels/.releases/r2/zh_CN/gamedata/levels/obt/main"
    levels.mkdir(parents=True)
    (levels / "level_main_01-07.json").write_text(json.dumps({
        "waves": [
            {
                "preDelay": 0.0,
                "fragments": [
                    {
                        "preDelay": 3.0,
                        "actions": [
                            {
                                "actionType": "SPAWN",
                                "key": "enemy_1007_slime_2",
                                "count": 1,
                                "preDelay": 3.0,
                                "interval": 1.0,
                                "routeIndex": 1,
                            }
                        ],
                    },
                    {
                        "preDelay": 5.0,
                        "actions": [
                            {
                                "actionType": "SPAWN",
                                "key": "enemy_1027_mob",
                                "count": 2,
                                "preDelay": 3.0,
                                "interval": 7.0,
                                "routeIndex": 7,
                            }
                        ],
                    },
                ],
            }
        ]
    }, ensure_ascii=False), encoding="utf-8")


def test_extracts_spawn_order_from_prts_data(tmp_path, monkeypatch):
    _write_fake_prts_data(tmp_path)
    _reset_caches()
    monkeypatch.setenv("PRTS_MCP_DATA_DIR", str(tmp_path))

    result = asyncio.run(sw.execute_stage_waves({"stage_code": "1-7"}))

    assert result["stage_name"] == "暴君"
    assert result["stage_id"] == "main_01-07"
    assert result["total_waves"] == 2
    assert result["waves"][0]["spawns"][0]["enemy_name"] == "源石虫·α"
    assert result["waves"][1]["spawns"][0]["enemy_name"] == "暴徒"
    assert result["waves"][1]["spawns"][0]["count"] == 2
    assert result["waves"][1]["wave"] == 2


def test_stage_id_input_also_resolves(tmp_path, monkeypatch):
    _write_fake_prts_data(tmp_path)
    _reset_caches()
    monkeypatch.setenv("PRTS_MCP_DATA_DIR", str(tmp_path))

    result = asyncio.run(sw.execute_stage_waves({"stage_code": "main_01-07"}))
    assert result["stage_code"] == "1-7"


def test_fragments_without_spawn_do_not_advance_wave_number(tmp_path, monkeypatch):
    _write_fake_prts_data(tmp_path)
    _reset_caches()
    monkeypatch.setenv("PRTS_MCP_DATA_DIR", str(tmp_path))

    level_file = (
        tmp_path
        / "gamedata-levels/.releases/r2/zh_CN/gamedata/levels/obt/main/level_main_01-07.json"
    )
    level_file.write_text(json.dumps({
        "waves": [
            {
                "fragments": [
                    # 无刷怪 fragment：不应占一个波次编号
                    {
                        "preDelay": 1.0,
                        "actions": [{"actionType": "STORY", "key": "story_1"}],
                    },
                    {
                        "preDelay": 3.0,
                        "actions": [{
                            "actionType": "SPAWN",
                            "key": "enemy_1007_slime_2",
                            "count": 1,
                            "preDelay": 0.0,
                            "interval": 1.0,
                            "routeIndex": 0,
                        }],
                    },
                ],
            }
        ]
    }, ensure_ascii=False), encoding="utf-8")

    result = asyncio.run(sw.execute_stage_waves({"stage_code": "1-7"}))
    assert result["total_waves"] == 1
    assert result["waves"][0]["wave"] == 1


def test_count_zero_is_preserved(tmp_path, monkeypatch):
    _write_fake_prts_data(tmp_path)
    _reset_caches()
    monkeypatch.setenv("PRTS_MCP_DATA_DIR", str(tmp_path))

    spawns = sw._spawn_sequence({
        "waves": [
            {
                "fragments": [
                    {
                        "preDelay": 0.0,
                        "actions": [{
                            "actionType": "SPAWN",
                            "key": "enemy_1007_slime_2",
                            "count": 0,
                            "preDelay": 0.0,
                            "interval": 0.0,
                            "routeIndex": 0,
                        }],
                    },
                ],
            }
        ]
    })
    assert len(spawns) == 1
    assert spawns[0]["wave"] == 1
    assert spawns[0]["count"] == 0


def test_missing_data_is_not_cached_permanently(tmp_path, monkeypatch):
    _reset_caches()
    monkeypatch.setenv("PRTS_MCP_DATA_DIR", str(tmp_path))

    # 首次加载：数据不存在 → 返回空表，但不允许缓存"缺失"这个负结果
    assert sw._load_stage_table() == {}
    assert sw._stage_table_loaded is False

    # 同步完成后，同一进程无需重启即可重新加载成功
    _write_fake_prts_data(tmp_path)
    stages = sw._load_stage_table()
    assert "main_01-07" in stages


def test_large_result_is_truncated_with_note(tmp_path, monkeypatch):
    _write_fake_prts_data(tmp_path)
    _reset_caches()
    monkeypatch.setenv("PRTS_MCP_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(sw, "MAX_STAGE_SPAWNS", 3)

    level_file = (
        tmp_path
        / "gamedata-levels/.releases/r2/zh_CN/gamedata/levels/obt/main/level_main_01-07.json"
    )
    fragments = []
    for i in range(5):
        fragments.append({
            "preDelay": 0.0,
            "actions": [{
                "actionType": "SPAWN",
                "key": "enemy_1007_slime_2",
                "count": 1,
                "preDelay": 0.0,
                "interval": 1.0,
                "routeIndex": 0,
            }],
        })
    level_file.write_text(json.dumps({"waves": [{"fragments": fragments}]}), encoding="utf-8")

    result = asyncio.run(sw.execute_stage_waves({"stage_code": "1-7"}))
    assert result["total_waves"] == 5
    assert len(result["waves"]) == 3
    assert result["truncated"] is True
    assert "已截断" in result["note"]


def test_missing_stage_returns_error(tmp_path, monkeypatch):
    _reset_caches()
    monkeypatch.setenv("PRTS_MCP_DATA_DIR", str(tmp_path))
    result = asyncio.run(sw.execute_stage_waves({"stage_code": "9-9"}))
    assert "未找到关卡" in result["error"]
