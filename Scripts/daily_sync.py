"""
每日自动增量同步脚本
====================
用于 cron 每日定时执行，自动检测更新、切块、重建索引。

执行顺序:
  1. lore_sync (剧情 Wiki) → 增量同步角色/剧情/索引
  2. 增量 GraphRAG 抽取 → 新文件自动抽取实体关系
  3. sync_prts (PRTS Wiki) → 增量同步干员/敌人
  4. 如果有新增文件 → 重新切块 → 重建 BM25 → 增量更新 FAISS
  5. 如果索引有更新 → 重启 uvicorn

用法:
  python Scripts/daily_sync.py                          # 完整流程
  python Scripts/daily_sync.py --dry-run                # 只检测
  python Scripts/daily_sync.py --graphrag-only          # 仅补抽 GraphRAG（基于 mtime）
"""

import sys
import os
import json
import subprocess
import argparse
from pathlib import Path
from datetime import datetime, timezone

BASE_DIR = Path(__file__).parent.parent
SCRIPTS_DIR = Path(__file__).parent
LOG_FILE = SCRIPTS_DIR / "daily_sync_log.txt"


def log(msg: str):
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    line = f"[{stamp}] {msg}"
    print(line)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def count_files(dir_path: Path, pattern: str = "*.md") -> int:
    """统计目录下匹配文件数。"""
    if not dir_path.exists():
        return 0
    return len(list(dir_path.glob(pattern)))


def run_script(script_name: str, args: list = None) -> bool:
    """运行一个 Python 脚本，返回是否成功。"""
    script_path = SCRIPTS_DIR / script_name
    cmd = [sys.executable, str(script_path)] + (args or [])
    log(f"  执行: {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=str(BASE_DIR))
    # 输出关键日志行
    for line in result.stdout.strip().split("\n"):
        if any(kw in line for kw in ("新增", "同步", "✓", "✗", "完成", "失败", "跳过", "无变化")):
            log(f"    {line.strip()}")
    if result.returncode != 0:
        log(f"    ✗ 返回码 {result.returncode}: {result.stderr[:300]}")
        return False
    return True


def rebuild_bm25() -> bool:
    """重建所有 BM25 索引（快速，纯本地）。"""
    log("  重建 BM25 索引...")
    bm25_script = BASE_DIR / "backend" / "data" / "bm25_index.py"
    result = subprocess.run(
        [sys.executable, str(bm25_script)],
        capture_output=True, text=True, cwd=str(BASE_DIR)
    )
    if result.returncode != 0:
        log(f"    ✗ {result.stderr[:300]}")
        return False
    for line in result.stdout.strip().split("\n"):
        log(f"    {line.strip()}")
    return True


def rebuild_faiss() -> bool:
    """重建所有 FAISS 索引（需要 API 调用，较慢）。"""
    log("  重建 FAISS 索引（需要 API）...")
    faiss_script = BASE_DIR / "backend" / "build_faiss_index.py"
    result = subprocess.run(
        [sys.executable, str(faiss_script), "--force"],
        capture_output=True, text=True, cwd=str(BASE_DIR),
    )
    if result.returncode != 0:
        log(f"    ✗ {result.stderr[:300]}")
        return False
    for line in result.stdout.strip().split("\n"):
        if any(kw in line for kw in ("Built", "Embedding", "Done", "chunks")):
            log(f"    {line.strip()}")
    return True


def rechunk_all(skip_knowledge: bool = False) -> bool:
    """重新运行 chunker（文本处理，快速）。"""
    log("  重新切块...")
    chunker_script = BASE_DIR / "backend" / "data" / "chunker.py"
    if not chunker_script.exists():
        log("    ✗ chunker.py 不存在")
        return False
    result = subprocess.run(
        [sys.executable, str(chunker_script)],
        capture_output=True, text=True, cwd=str(BASE_DIR)
    )
    if result.returncode != 0:
        log(f"    ✗ {result.stderr[:300]}")
        return False
    for line in result.stdout.strip().split("\n"):
        if "complete" in line.lower() or ":" in line:
            log(f"    {line.strip()}")
    return True


def restart_uvicorn():
    """重启 uvicorn 服务（由 systemd 管理，避免 pkill+nohup 与 systemd 冲突产生双进程）。"""
    log("  重启 uvicorn (systemctl restart arknights-rag)...")
    result = subprocess.run(
        ["systemctl", "restart", "arknights-rag"],
        capture_output=True, text=True
    )
    if result.returncode == 0:
        log("    ✓ uvicorn 已重启")
    else:
        log(f"    ✗ systemctl 重启失败: {result.stderr[:300]}")


# ===================== 增量 GraphRAG 更新 =====================

def _snapshot_data_files() -> dict:
    """快照 data/ 目录下所有 .md 文件的路径集合。

    Returns:
        {"operators": {name, ...}, "stories": {name, ...}}
    """
    snapshot = {"operators": set(), "stories": set()}
    for key, subdir in [("operators", "operators"), ("stories", "stories")]:
        dir_path = BASE_DIR / "data" / subdir
        if dir_path.exists():
            snapshot[key] = {p.name for p in dir_path.glob("*.md")}
    return snapshot


def _find_new_files(before: dict, after: dict) -> dict:
    """对比快照，找出新增文件。

    Returns:
        {"operators": [full_path, ...], "stories": [full_path, ...]}
    """
    new_files = {"operators": [], "stories": []}
    for key, subdir in [("operators", "operators"), ("stories", "stories")]:
        new_names = after.get(key, set()) - before.get(key, set())
        dir_path = BASE_DIR / "data" / subdir
        new_files[key] = [str(dir_path / name) for name in sorted(new_names)]
    return new_files


def _find_files_newer_than(timestamp: float) -> dict:
    """找出 mtime 晚于给定时间戳的 data/ 文件。

    Args:
        timestamp: Unix timestamp (float)

    Returns:
        {"operators": [full_path, ...], "stories": [full_path, ...]}
    """
    result = {"operators": [], "stories": []}
    for key, subdir in [("operators", "operators"), ("stories", "stories")]:
        dir_path = BASE_DIR / "data" / subdir
        if not dir_path.exists():
            continue
        for p in sorted(dir_path.glob("*.md")):
            if p.stat().st_mtime > timestamp:
                result[key].append(str(p))
    return result


def incremental_graphrag(new_files: dict = None, dry_run: bool = False) -> bool:
    """增量更新 GraphRAG 的 entity_relations.json。

    支持两种模式：
    1. 传入 new_files dict（daily_sync 快照对比模式）
    2. 不传 new_files，自动用 entity_relations.json 的 mtime 检测（补抽模式）

    Args:
        new_files: {"operators": [path, ...], "stories": [path, ...]} 或 None
        dry_run: 只检测不实际抽取

    Returns:
        bool: 是否有更新
    """
    entity_file = BASE_DIR / "chunks" / "graphrag" / "entity_relations.json"

    # --- 确定要抽取的文件 ---
    if new_files is None:
        # 补抽模式：用 mtime 检测
        if not entity_file.exists():
            log("    entity_relations.json 不存在，做不了增量，建议全量构建")
            return False
        last_mtime = entity_file.stat().st_mtime
        new_files = _find_files_newer_than(last_mtime)
        log(f"  补抽模式：entity_relations.json mtime={datetime.fromtimestamp(last_mtime).strftime('%Y-%m-%d %H:%M')}")

    total_new = len(new_files.get("operators", [])) + len(new_files.get("stories", []))
    if total_new == 0:
        log("  GraphRAG: 无新文件，跳过")
        return False

    log(f"  GraphRAG: 发现 +{len(new_files.get('operators', []))} operators, "
        f"+{len(new_files.get('stories', []))} stories 需抽取")

    if dry_run:
        for key in ["operators", "stories"]:
            for fp in new_files.get(key, [])[:5]:
                log(f"      [{key}] {Path(fp).name}")
        if total_new > 10:
            log(f"      ... 还有 {total_new - 10} 个文件")
        return False

    # --- 加载现有数据（兼容两种 entities 格式）---
    existing_relations = []
    existing_entities_dict = {}   # {type: {name, ...}}
    existing_entity_names = set() # 所有实体名
    existing_entity_count = 0

    if entity_file.exists():
        try:
            with open(entity_file, "r", encoding="utf-8") as f:
                existing_data = json.load(f)
            existing_relations = existing_data.get("relations", [])

            entities_raw = existing_data.get("entities", {})
            if isinstance(entities_raw, dict):
                # dict 格式: {"干员": [...], "组织": [...], ...}
                for etype, names in entities_raw.items():
                    if isinstance(names, list):
                        existing_entities_dict.setdefault(etype, set())
                        for name in names:
                            if isinstance(name, str) and name.strip():
                                existing_entities_dict[etype].add(name.strip())
                                existing_entity_names.add(name.strip())
                existing_entity_count = sum(len(v) for v in existing_entities_dict.values())
            elif isinstance(entities_raw, list):
                # list 格式: [{"entity": "name", "type": "type"}, ...]
                for e in entities_raw:
                    if isinstance(e, dict):
                        name = e.get("entity", "").strip()
                        etype = e.get("type", "干员").strip()
                        if name:
                            existing_entities_dict.setdefault(etype, set()).add(name)
                            existing_entity_names.add(name)
                existing_entity_count = len(entities_raw)
            log(f"  现有: {existing_entity_count} entities ({len(existing_entities_dict)} 类型), {len(existing_relations)} relations")
        except Exception as e:
            log(f"    ⚠ 读取现有 entity_relations.json 失败: {e}")

    # --- 收集已知关系类型 ---
    known_types = list(set(r.get("relation", "") for r in existing_relations if r.get("relation")))

    # --- 收集已知干员（从 dict 格式的干员类型中） ---
    known_operators = list(existing_entities_dict.get("干员", set()))

    # --- 增量抽取 ---
    try:
        from backend.rag.graphrag.extractor import EntityExtractor

        extractor = EntityExtractor()
        all_new_entities = []
        all_new_relations = []

        for key in ["operators", "stories"]:
            files = new_files.get(key, [])
            if not files:
                continue

            use_key_sections = (key == "stories")
            log(f"  正在抽取 {key} ({len(files)} 文件)...")

            for i in range(0, len(files), extractor.BATCH_SIZE):
                batch = files[i:i + extractor.BATCH_SIZE]
                batch_results, batch_types, new_ops = extractor.extract_batch(
                    batch,
                    known_types,
                    known_operators,
                    extract_key_sections=use_key_sections,
                )

                for result in batch_results:
                    all_new_entities.extend(result.get("entities", []))
                    all_new_relations.extend(result.get("relations", []))

                # 累积已知信息，供后续批次复用
                for t in batch_types:
                    if t not in known_types:
                        known_types.append(t)
                for op in new_ops:
                    if op not in known_operators:
                        known_operators.append(op)

                processed = min(i + extractor.BATCH_SIZE, len(files))
                log(f"      {processed}/{len(files)} | types: {len(known_types)} | operators: {len(known_operators)}")

        # --- 合并 + 去重 ---
        if not all_new_entities and not all_new_relations:
            log("  GraphRAG: 未抽到新实体/关系")
            return False

        # 合并实体到 dict 格式
        merged_entities = {k: set(v) for k, v in existing_entities_dict.items()}
        new_entity_count = 0
        for e in all_new_entities:
            name = e.get("entity", "").strip()
            etype = e.get("type", "干员").strip()
            if not name or any(c in name for c in "[]{}()"):
                continue
            if name not in existing_entity_names:
                merged_entities.setdefault(etype, set()).add(name)
                existing_entity_names.add(name)
                new_entity_count += 1
        merged_entities = {k: sorted(v) for k, v in merged_entities.items()}

        # 合并关系（去重 by source+target+relation）
        seen_relations = set()
        merged_relations = []
        for r in existing_relations + all_new_relations:
            src = r.get("source", "").strip()
            tgt = r.get("target", "").strip()
            rel = r.get("relation", "").strip()
            if not src or not tgt or not rel:
                continue
            if any(c in src + tgt for c in "[]{}()"):
                continue
            key = (src, tgt, rel)
            if key not in seen_relations:
                seen_relations.add(key)
                merged_relations.append(r)

        new_relation_count = len(merged_relations) - len(existing_relations)
        total_entities = sum(len(v) for v in merged_entities.values())
        log(f"  合并结果: +{new_entity_count} entities, +{new_relation_count} relations")
        log(f"  总计: {total_entities} entities ({len(merged_entities)} 类型), {len(merged_relations)} relations")

        # --- 保存 ---
        from datetime import timezone as tz
        output = {
            "entities": merged_entities,
            "relations": merged_relations,
            "last_extraction": datetime.now(tz.utc).isoformat(),
        }
        entity_file.parent.mkdir(parents=True, exist_ok=True)
        with open(entity_file, "w", encoding="utf-8") as f:
            json.dump(output, f, ensure_ascii=False, indent=2)
        log(f"    ✓ 已保存到 {entity_file}")

        return True

    except Exception as e:
        log(f"    ✗ GraphRAG 增量更新失败: {e}")
        import traceback
        log(f"    {traceback.format_exc()}")
        return False


# ===================== 主入口 ====================

def main():
    parser = argparse.ArgumentParser(description="每日自动增量同步")
    parser.add_argument("--dry-run", action="store_true", help="只检测，不实际执行")
    parser.add_argument("--graphrag-only", action="store_true",
                        help="仅做 GraphRAG 增量补抽（基于 mtime），跳过数据同步")
    args = parser.parse_args()

    log("=" * 60)
    log("每日自动同步开始" + (" (dry-run)" if args.dry_run else ""))

    # --- 独立的 GraphRAG 补抽模式 ---
    if args.graphrag_only:
        log("\n[GraphRAG 补抽模式]")
        log("  基于 entity_relations.json 的 mtime 检测需要更新的文件...")
        changed = incremental_graphrag(new_files=None, dry_run=args.dry_run)
        if changed:
            log("\nGraphRAG 已更新，建议重启服务加载新的 entity_relations.json")
        else:
            log("\nGraphRAG 无需更新")
        return

    any_changes = False

    # ===== 1. 剧情 Wiki 同步 =====
    log("\n[1/4] 剧情 Wiki 增量同步")

    # 快照 data/ 目录（同步前），用于检测新增文件
    before_snapshot = _snapshot_data_files() if not args.dry_run else None

    lore_args = ["--dry-run"] if args.dry_run else []
    lore_ok = run_script("lore_sync.py", lore_args)

    # ===== 2. 增量 GraphRAG 抽取 =====
    log("\n[2/4] 增量 GraphRAG 抽取")
    if args.dry_run:
        log("  (dry-run 模式，跳过)")
    elif lore_ok:
        after_snapshot = _snapshot_data_files()
        new_files = _find_new_files(before_snapshot, after_snapshot)
        if incremental_graphrag(new_files=new_files, dry_run=False):
            any_changes = True
    else:
        log("  lore_sync 未成功执行，跳过 GraphRAG 抽取")

    # ===== 3. PRTS Wiki 同步 =====
    log("\n[3/4] PRTS Wiki 增量同步")
    prts_args = ["--update-index"] if not args.dry_run else ["--dry-run"]

    # 快照数据文件（同步后对比），检测 sync_prts 是否新增干员/敌人。
    # knowledge 集合的更新不经过步骤 2 的 GraphRAG 检测，需单独判断是否重启。
    def _snapshot_prts_data() -> dict:
        snap = {}
        for p in (BASE_DIR / "data").glob("all_*.json"):
            snap[p.name] = (p.stat().st_size, p.stat().st_mtime)
        return snap

    prts_before = _snapshot_prts_data() if not args.dry_run else None
    run_script("sync_prts.py", prts_args)
    if prts_before is not None and _snapshot_prts_data() != prts_before:
        log("  PRTS 数据有更新，稍后重启服务")
        any_changes = True

    if args.dry_run:
        log("\n(dry-run 结束)")
        return

    # ===== 4. 检查是否需要重新切块和重建索引 =====
    log("\n[4/4] 索引维护")

    # 重新切块（确保 chunks 目录与 data 目录一致）
    # 这是最快的保证一致性的方式
    rechunk_ok = rechunk_all(skip_knowledge=True)
    if rechunk_ok:
        rebuild_bm25()

    # FAISS 增量更新：只对有 diff 的 collection 重建
    # 简单策略：如果 lore_sync 有更新，重建 operators + stories 的 FAISS
    # 具体判断略复杂，简化处理：总是做增量 FAISS
    # （sync_prts 的 --update-index 已经处理了 knowledge）
    # 这里只处理 operators + stories
    log("  检查 operators + stories FAISS 增量...")
    try:
        sys.path.insert(0, str(BASE_DIR))
        from backend.api.siliconflow import SiliconFlowClient
        from backend.storage.faiss_client import FAISSClientWrapper
        from langchain_core.documents import Document

        client = FAISSClientWrapper()
        emb_client = SiliconFlowClient()

        for coll in ["operators", "stories"]:
            chunks_dir = BASE_DIR / "chunks" / coll
            if not chunks_dir.exists():
                continue

            # 加载现有索引
            result = client.load_index(coll)
            if result is None:
                log(f"    {coll}: 索引不存在，全量构建")
                # 全量构建
                chunk_files = sorted(list(chunks_dir.glob("*.md")) + list(chunks_dir.glob("*.txt")))
                docs = []
                for fp in chunk_files:
                    content = fp.read_text(encoding="utf-8")
                    docs.append(Document(
                        page_content=content,
                        metadata={"chunk_id": fp.stem, "section": fp.stem,
                                  "source_file": fp.name, "source_collection": coll}
                    ))
                if docs:
                    embeddings = emb_client.embed([d.page_content for d in docs])
                    client.build_index(coll, docs, embeddings=embeddings)
                    log(f"      ✓ 已构建 {len(docs)} chunks")
                continue

            index, meta = result
            existing_ids = {m["id"] for m in meta.values()}
            chunk_files = set(p.stem for p in chunks_dir.glob("*.md"))
            chunk_files |= set(p.stem for p in chunks_dir.glob("*.txt"))
            new_ids = chunk_files - existing_ids

            if not new_ids:
                log(f"    {coll}: 无新增 (已有 {index.ntotal})")
                continue

            log(f"    {coll}: +{len(new_ids)} 新 chunks")
            new_docs = []
            for cid in sorted(new_ids):
                for ext in [".md", ".txt"]:
                    fp = chunks_dir / f"{cid}{ext}"
                    if fp.exists():
                        content = fp.read_text(encoding="utf-8")
                        new_docs.append(Document(
                            page_content=content,
                            metadata={"chunk_id": cid, "section": cid,
                                      "source_file": fp.name, "source_collection": coll}
                        ))
                        break

            if new_docs:
                texts = [d.page_content for d in new_docs]
                embeddings = emb_client.embed(texts)
                total = client.add_documents(coll, new_docs, embeddings=embeddings)
                log(f"      ✓ 总计 {total}")
                any_changes = True

    except Exception as e:
        log(f"    ✗ FAISS 增量更新失败: {e}")

    # ===== 5. 重启服务 =====
    if any_changes:
        log("\n索引有更新，重启服务...")
        restart_uvicorn()
    else:
        log("\n无索引变更，跳过重启")

    log("\n每日自动同步完成")


if __name__ == "__main__":
    main()
