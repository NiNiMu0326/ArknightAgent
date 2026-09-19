"""
Stage spawn-order tool.

Reads the prts-mcp synced `stage_table.json` + per-level JSON files and turns
the raw `waves`/`fragments`/`actions` data into a readable spawn sequence.

This complements prts-mcp's `get_stage_enemies`, which only returns the enemy
list with total counts and battle stats — it has no wave/order information.
"""

import asyncio
import functools
import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# asyncio.to_thread 在 Python 3.9 才加入；服务器运行的是 3.8.10，此处提供兼容回退。
if not hasattr(asyncio, "to_thread"):
    async def _to_thread(func, *args, **kwargs):
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, functools.partial(func, *args, **kwargs))

    asyncio.to_thread = _to_thread

# 工具结果大小保护：防止超长关卡的完整刷怪表撑爆 LLM 上下文
MAX_STAGE_WAVES = 60
MAX_STAGE_SPAWNS = 400

# Lazy caches. 只有"加载成功"才缓存；数据目录缺失/损坏时下次调用会重试，
# 这样 prts-mcp 在进程运行期间补完同步数据后无需重启后端即可生效。
_stage_table: Dict[str, Any] = {}
_stage_table_loaded: bool = False
_enemy_names: Dict[str, str] = {}
_enemy_names_loaded: bool = False
_levels_root: Optional[Path] = None


def _share_root() -> Path:
    base = os.environ.get("PRTS_MCP_DATA_DIR")
    if base:
        return Path(base)
    return Path.home() / ".local/share/prts-mcp"


def _latest_zh_dir(kind: str) -> Optional[Path]:
    """Return .../gamedata[-levels]/.releases/<hash>/zh_CN for a data kind."""
    releases = _share_root() / kind / ".releases"
    if not releases.exists():
        return None
    latest: Optional[Path] = None
    latest_mtime = -1.0
    for candidate in releases.glob("*/zh_CN"):
        # 目录可能在 glob 与 stat 之间被 prts-mcp 的同步/清理流程删除，
        # 单个失败项跳过即可，不要影响整个工具调用。
        try:
            mtime = candidate.stat().st_mtime
        except OSError as exc:
            logger.warning(f"[stage-waves] skip unreadable release dir {candidate}: {exc}")
            continue
        if mtime > latest_mtime:
            latest_mtime = mtime
            latest = candidate
    return latest


def _load_stage_table() -> Dict[str, Any]:
    global _stage_table, _stage_table_loaded
    if _stage_table_loaded:
        return _stage_table
    zh = _latest_zh_dir("gamedata")
    if zh:
        table_file = zh / "gamedata/excel/stage_table.json"
        try:
            with open(table_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            stages = data.get("stages")
            # 解析成功但结构异常（缺 stages / 不是 dict / 空表）时不能置位标志，
            # 否则空表会被永久缓存，直到进程重启才可能恢复。
            if not isinstance(stages, dict) or not stages:
                logger.warning(
                    f"[stage-waves] stage_table.json 结构异常（stages 缺失或为空）: {table_file}"
                )
                return _stage_table
            _stage_table = stages
            _stage_table_loaded = True
            logger.info(f"[stage-waves] loaded {len(_stage_table)} stages")
        except Exception as exc:
            logger.warning(f"[stage-waves] load stage_table failed: {exc}")
            _stage_table_loaded = False
    return _stage_table


def _load_enemy_names() -> Dict[str, str]:
    global _enemy_names, _enemy_names_loaded
    if _enemy_names_loaded:
        return _enemy_names
    zh = _latest_zh_dir("gamedata")
    if zh:
        handbook = zh / "gamedata/excel/enemy_handbook_table.json"
        try:
            with open(handbook, "r", encoding="utf-8") as f:
                data = json.load(f)
            enemy_data = data.get("enemyData")
            # 同上：缺 enemyData / 不是 dict / 空表时不缓存，下次调用重试
            if not isinstance(enemy_data, dict) or not enemy_data:
                logger.warning(
                    f"[stage-waves] enemy_handbook_table.json 结构异常（enemyData 缺失或为空）: {handbook}"
                )
                return _enemy_names
            names: Dict[str, str] = {}
            for enemy_id, info in enemy_data.items():
                if isinstance(info, dict) and info.get("name"):
                    names[enemy_id] = str(info["name"])
            _enemy_names = names
            _enemy_names_loaded = True
            logger.info(f"[stage-waves] loaded {len(_enemy_names)} enemy names")
        except Exception as exc:
            logger.warning(f"[stage-waves] load enemy handbook failed: {exc}")
            _enemy_names_loaded = False
    return _enemy_names


def _get_levels_root() -> Optional[Path]:
    global _levels_root
    if _levels_root is not None and _levels_root.exists():
        return _levels_root
    _levels_root = None
    zh = _latest_zh_dir("gamedata-levels")
    if zh:
        candidate = zh / "gamedata/levels"
        if candidate.exists():
            _levels_root = candidate
            return _levels_root
    return None


def _resolve_stage(stage_key: str) -> Optional[Dict[str, Any]]:
    """Resolve `1-7` or `main_01-07` to a stage_table entry."""
    stages = _load_stage_table()
    if not stages:
        return None
    key = (stage_key or "").strip()
    if not key:
        return None

    # stageId exact match (prefer normal difficulty over #f#)
    normal = stages.get(key)
    if isinstance(normal, dict):
        return normal

    # code match (e.g. 1-7 -> main_01-07)
    for stage_id, info in stages.items():
        if not isinstance(info, dict):
            continue
        if (info.get("code") or "") == key and "#f#" not in stage_id:
            return info
    for stage_id, info in stages.items():
        if not isinstance(info, dict):
            continue
        if (info.get("code") or "") == key:
            return info
    return None


def _enemy_name(enemy_id: str) -> str:
    if not enemy_id:
        return "未知敌人"
    return _load_enemy_names().get(enemy_id, enemy_id)


def _load_level_json(stage_info: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    level_id = (stage_info.get("levelId") or "").strip()
    if not level_id:
        return None
    root = _get_levels_root()
    if not root:
        return None
    rel = level_id.lower()
    if not rel.endswith(".json"):
        rel += ".json"
    candidate = root / rel
    if candidate.exists():
        try:
            with open(candidate, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as exc:
            logger.warning(f"[stage-waves] load level json failed: {exc}")
            return None
    # 兜底：按 levelId 最后一段文件名搜索
    file_name = Path(level_id).name.lower()
    if not file_name.endswith(".json"):
        file_name += ".json"
    matches = list(root.rglob(file_name))
    if matches:
        try:
            with open(matches[0], "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as exc:
            logger.warning(f"[stage-waves] load level json fallback failed: {exc}")
    return None


def _spawn_sequence(level_data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Flatten waves/fragments/actions into an ordered spawn sequence.

    关卡 JSON 通常只有一个 `waves` 元素，真正的“波次”体现在其 fragments
    列表里，因此这里把每个 fragment 作为一个用户可见的波次。只给真正
    包含刷怪动作的 fragment 编号，避免 STORY 等无怪 fragment 造成跳号。
    """
    spawns: List[Dict[str, Any]] = []
    waves = level_data.get("waves", [])
    if not isinstance(waves, list):
        return spawns

    wave_counter = 0
    for wave in waves:
        if not isinstance(wave, dict):
            continue
        fragments = wave.get("fragments", [])
        if not isinstance(fragments, list):
            continue
        for fragment in fragments:
            if not isinstance(fragment, dict):
                continue
            actions = fragment.get("actions", [])
            if not isinstance(actions, list):
                continue

            fragment_spawns: List[Dict[str, Any]] = []
            for action in actions:
                if not isinstance(action, dict):
                    continue
                action_type = action.get("actionType", "")
                enemy_key = action.get("key", "")
                # 只统计真正刷出的敌人；STORY/PREVIEW_CURSOR 等非刷怪动作跳过
                if action_type != "SPAWN" or not enemy_key.startswith("enemy_"):
                    continue
                raw_count = action.get("count")
                fragment_spawns.append({
                    "wave_pre_delay": float(fragment.get("preDelay", 0) or 0),
                    "enemy_id": enemy_key,
                    "enemy_name": _enemy_name(enemy_key),
                    # count 缺失时默认 1；count=0 是原始数据，原样保留
                    "count": int(raw_count) if raw_count is not None else 1,
                    "pre_delay": float(action.get("preDelay", 0) or 0),
                    "interval": float(action.get("interval", 0) or 0),
                    "route_index": action.get("routeIndex"),
                })

            if not fragment_spawns:
                continue
            wave_counter += 1
            for entry in fragment_spawns:
                spawns.append({"wave": wave_counter, **entry})
    return spawns


async def execute_stage_waves(arguments: Dict[str, Any], session_id: str = "") -> Dict[str, Any]:
    """Execute arknights_stage_waves tool."""
    stage_key = str(arguments.get("stage_code") or arguments.get("stage_id") or "").strip()
    if not stage_key:
        return {"error": "stage_code 参数必填，例如 '1-7'、'CE-5' 或 'main_01-07'"}

    # stage_table.json / 关卡 JSON / 敌人手册都可能有数 MB，未命中缓存时还含 rglob
    # 全树扫描；这些同步磁盘 I/O 放到线程里执行，避免阻塞事件循环。
    stage_info = await asyncio.to_thread(_resolve_stage, stage_key)
    if not stage_info:
        return {
            "error": f"未找到关卡 '{stage_key}'。请检查关卡编号（如 1-7、CE-5），"
                     "或先查关卡列表确认 ID",
        }

    level_data = await asyncio.to_thread(_load_level_json, stage_info)
    if not level_data:
        return {
            "error": f"关卡 '{stage_key}' 的出怪数据文件不可用（未同步 levels 数据），"
                     "可改用工具列表中的关卡敌人工具查看敌人列表与数量",
        }

    # _spawn_sequence 内部会懒加载敌人名表（同样是一次大 JSON 读取），一并移出事件循环
    spawns = await asyncio.to_thread(_spawn_sequence, level_data)
    waves = []
    for spawn in spawns:
        wave_num = spawn["wave"]
        if not waves or waves[-1]["wave"] != wave_num:
            waves.append({"wave": wave_num, "wave_pre_delay": spawn["wave_pre_delay"], "spawns": []})
        waves[-1]["spawns"].append({
            "order": len(waves[-1]["spawns"]) + 1,
            "enemy_id": spawn["enemy_id"],
            "enemy_name": spawn["enemy_name"],
            "count": spawn["count"],
            "pre_delay": spawn["pre_delay"],
            "interval": spawn["interval"],
            "route_index": spawn["route_index"],
        })

    total_waves = len(waves)
    truncated = False
    kept_waves = []
    remaining = MAX_STAGE_SPAWNS
    for wave in waves:
        if len(kept_waves) >= MAX_STAGE_WAVES or remaining <= 0:
            truncated = True
            break
        wave_spawns = wave["spawns"]
        if len(wave_spawns) > remaining:
            # 单波就超出剩余预算：按预算裁剪当前波，避免 kept_spawns 远超上限
            kept_waves.append({**wave, "spawns": wave_spawns[:remaining]})
            truncated = True
            break
        kept_waves.append(wave)
        remaining -= len(wave_spawns)

    note = "波次按关卡数据中的 fragments 划分，按出现顺序排列；pre_delay/interval 为关卡原始数据（秒），仅供参考"
    if truncated:
        note += (
            f"；本关共 {total_waves} 波，结果过大已截断，仅展示前 {len(kept_waves)} 波，"
            "如需后续波次请换用工具列表中的其他关卡工具或查询更精确的范围"
        )

    return {
        "stage_code": stage_info.get("code", stage_key),
        "stage_id": stage_info.get("stageId", stage_key),
        "stage_name": stage_info.get("name", ""),
        "total_waves": total_waves,
        "waves": kept_waves,
        "truncated": truncated,
        "note": note,
    }
