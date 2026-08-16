"""
Tests for backend.main quick-question stage-code loader.
"""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
os.environ.setdefault("JWT_SECRET", "test-secret-key-for-helper-tests")

from backend import main as main_mod


def _write_stage_table(release_dir: Path, codes):
    table = release_dir / "zh_CN/gamedata/excel/stage_table.json"
    table.parent.mkdir(parents=True, exist_ok=True)
    stages = {}
    for idx, code in enumerate(codes):
        stages[f"stage_{idx}"] = {
            "stageId": f"stage_{idx}",
            "code": code,
            "name": code,
            "difficulty": "NORMAL",
        }
    table.write_text(json.dumps({"stages": stages}, ensure_ascii=False), encoding="utf-8")
    return table


def test_loads_from_prts_root_and_picks_latest_release(tmp_path, monkeypatch):
    root = tmp_path / "prts-mcp"
    old = _write_stage_table(root / "gamedata/.releases/old", ["1-7", "0-1"])
    new = _write_stage_table(root / "gamedata/.releases/new", ["1-7", "CE-5"])
    old.touch()
    new.touch()
    # 强制保证 new 的 mtime 更新（同一文件系统上 glob 顺序不保证语义）
    os.utime(old, (1_000_000_000, 1_000_000_000))
    os.utime(new, (2_000_000_000, 2_000_000_000))

    monkeypatch.setenv("PRTS_MCP_DATA_DIR", str(root))
    codes = main_mod._load_qq_stage_codes()
    assert "CE-5" in codes
    assert codes == sorted(codes)


def test_supports_env_pointing_directly_to_gamedata(tmp_path, monkeypatch):
    gamedata = tmp_path / "gamedata"
    _write_stage_table(gamedata / ".releases/r1", ["1-7", "LS-5"])

    monkeypatch.setenv("PRTS_MCP_DATA_DIR", str(gamedata))
    codes = main_mod._load_qq_stage_codes()
    assert "LS-5" in codes


def test_malformed_stages_falls_back(tmp_path, monkeypatch):
    root = tmp_path / "prts-mcp"
    table = root / "gamedata/.releases/r1/zh_CN/gamedata/excel/stage_table.json"
    table.parent.mkdir(parents=True)
    table.write_text(json.dumps({"stages": ["not", "a", "dict"]}), encoding="utf-8")

    monkeypatch.setenv("PRTS_MCP_DATA_DIR", str(root))
    assert main_mod._load_qq_stage_codes() == ["1-7", "0-1", "CE-5", "LS-5", "4-10", "5-10"]
