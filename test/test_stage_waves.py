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
    sw._stage_table = None
    sw._enemy_names = None
    sw._levels_root = None
    sw._levels_root_missing = False


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
    assert result["total_waves"] == 1
    assert result["waves"][0]["spawns"][0]["enemy_name"] == "源石虫·α"
    assert result["waves"][0]["spawns"][1]["enemy_name"] == "暴徒"
    assert result["waves"][0]["spawns"][1]["count"] == 2


def test_stage_id_input_also_resolves(tmp_path, monkeypatch):
    _write_fake_prts_data(tmp_path)
    _reset_caches()
    monkeypatch.setenv("PRTS_MCP_DATA_DIR", str(tmp_path))

    result = asyncio.run(sw.execute_stage_waves({"stage_code": "main_01-07"}))
    assert result["stage_code"] == "1-7"


def test_missing_stage_returns_error(tmp_path, monkeypatch):
    _reset_caches()
    monkeypatch.setenv("PRTS_MCP_DATA_DIR", str(tmp_path))
    result = asyncio.run(sw.execute_stage_waves({"stage_code": "9-9"}))
    assert "未找到关卡" in result["error"]
