"""
PRTS Wiki 增量同步脚本
======================
检测 PRTS Wiki 上的新干员，增量爬取、清洗、切块。

流程:
  1. 从 PRTS 获取最新干员列表 → 对比本地 all_operators.json → 发现新增
  2. 只爬取新增干员（复用 scraper.py 的 parse_operator 清洗逻辑）
  3. 增量切块（只切新干员，不重建已有 chunk）
  4. 可选：增量更新 BM25 / FAISS 索引

用法:
  python Scripts/sync_prts.py              # 检测 + 爬取 + 切块
  python Scripts/sync_prts.py --dry-run    # 只检测，不爬取
  python Scripts/sync_prts.py --rebuild-index  # 完成后重建索引

环境要求:
  pip install requests beautifulsoup4

作者: ARKNIGHTS RAG Team
"""

import sys
import json
import time
import argparse
from pathlib import Path
from datetime import datetime, timezone

# 项目路径
BASE_DIR = Path(__file__).parent.parent
DATA_DIR = BASE_DIR / "data"
CHUNKS_DIR = BASE_DIR / "chunks" / "knowledge"
SCRIPTS_DIR = Path(__file__).parent

sys.path.insert(0, str(BASE_DIR))
sys.path.insert(0, str(SCRIPTS_DIR))

from scraper import parse_operator
from common import PRTS_HEADERS


# ===================== 日志 =====================

LOG_FILE = SCRIPTS_DIR / "sync_log.txt"


def log(msg: str):
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    line = f"[{stamp}] {msg}"
    try:
        print(line)
    except UnicodeEncodeError:
        # Windows GBK console fallback
        print(line.encode("gbk", errors="replace").decode("gbk", errors="replace"))
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")


# ===================== 干员同步 =====================

# ===================== 工具 =====================

def get_operator_list_from_api() -> list:
    """通过 PRTS API 获取干员列表（比解析 HTML CSV 更可靠）。"""
    import requests
    url = "https://prts.wiki/api.php"
    params = {
        "action": "query",
        "list": "categorymembers",
        "cmtitle": "Category:干员",
        "cmlimit": 500,
        "format": "json",
    }
    resp = requests.get(url, params=params, headers=PRTS_HEADERS, timeout=15)
    data = resp.json()
    members = data.get("query", {}).get("categorymembers", [])

    # 过滤：排除 Category/File 前缀，去掉模板/汇总页等非独立干员页
    names = []
    for m in members:
        title = m["title"].strip()
        if title.startswith(("Category:", "File:", "模板:", "帮助:", "PRTS:")):
            continue
        # 排除含代码字符的垃圾条目
        if any(c in title for c in ("/", "(", ")", ";", "{", "}")):
            continue
        # 排除非干员页
        if any(title.startswith(p) for p in ("干员", "敌人", "道具", "家具", "服装",
                                               "预备干员", "集成战略", "危机合约")):
            continue
        # 排除分支模板页（如"领主·Sharp"、"盟约·辅助干员"）
        if "·" in title:
            after_dot = title.split("·", 1)[1] if "·" in title else ""
            if any(kw in after_dot for kw in ("干员", "Sharp", "Stormeye", "Pith", "Touch")):
                continue
        # 排除含特殊上标字符的
        if any(c in title for c in ("²",)):
            continue
        # 排除看起来像代码片段的（连续小写+大写+小写如 langCode，但不误杀 PhonoR-0 这类）
        import re
        if re.search(r'[a-z]+[A-Z][a-z]', title):
            continue
        # 长度过滤（单字干员如 W、令、夕 等是合法的）
        if len(title) < 1 or len(title) > 15:
            continue
        names.append(title)

    # 二次过滤：排除 PRTS 重定向页面
    names = _filter_redirects(names)

    return sorted(names)


def _filter_redirects(names: list) -> list:
    """批量查询 PRTS API，排除重定向页面。"""
    import requests
    filtered = []
    batch_size = 50
    for i in range(0, len(names), batch_size):
        batch = names[i:i + batch_size]
        titles = "|".join(batch)
        url = "https://prts.wiki/api.php"
        params = {
            "action": "query",
            "titles": titles,
            "redirects": "",
            "format": "json",
        }
        try:
            resp = requests.get(url, params=params, headers=PRTS_HEADERS, timeout=15)
            data = resp.json()
            # 获取重定向映射: redirect_from → redirect_to
            redirects = {}
            for r in data.get("query", {}).get("redirects", []):
                redirects[r["from"]] = r["to"]
            pages = data.get("query", {}).get("pages", {})
            for pid, page in pages.items():
                title = page.get("title", "")
                if int(pid) > 0 and title and title not in redirects:
                    filtered.append(page["title"])
        except Exception:
            filtered.extend(batch)
    return filtered


def sync_operators(dry_run: bool = False) -> list:
    """检测新增干员并增量爬取。

    Returns:
        list[dict]: 新爬取的干员数据列表
    """
    log("=" * 60)
    log("干员增量同步")

    # 1. PRTS 列表
    log("获取 PRTS 干员列表...")
    try:
        prts_names = get_operator_list_from_api()
        prts_set = set(prts_names)
        log(f"  PRTS 当前: {len(prts_names)} 名干员")
    except Exception as e:
        log(f"  ✗ 获取 PRTS 列表失败: {e}")
        return []

    # 2. 本地数据
    ops_file = DATA_DIR / "all_operators.json"
    if not ops_file.exists():
        log(f"  ✗ 本地 all_operators.json 不存在")
        return []

    with open(ops_file, "r", encoding="utf-8") as f:
        local_ops = json.load(f)
    local_names = {op["干员名"] for op in local_ops}
    log(f"  本地: {len(local_ops)} 名干员")

    # 3. Diff
    new_names = sorted(n for n in prts_set if n not in local_names)
    removed_names = sorted(n for n in local_names if n not in prts_set)

    if removed_names:
        log(f"  ⚠ PRTS 上已移除: {len(removed_names)} 名 (可能改名/合并)")
        for n in removed_names[:10]:
            log(f"      - {n}")

    if not new_names:
        log("  无新增干员 ✓")
        return []

    log(f"  新增: {len(new_names)} 名")
    for n in new_names[:30]:
        log(f"      + {n}")
    if len(new_names) > 30:
        log(f"      ... 还有 {len(new_names) - 30} 名")

    if dry_run:
        log("  (dry-run 模式，跳过爬取)")
        return []

    # 4. 爬取新干员
    log(f"\n开始爬取 {len(new_names)} 名新干员...")
    new_ops = []
    fail_list = []

    for i, name in enumerate(new_names):
        progress = f"[{i+1}/{len(new_names)}]"
        try:
            result = parse_operator(name)
            if result:
                new_ops.append(result)
                log(f"  {progress} ✓ {name}")
            else:
                fail_list.append(name)
                log(f"  {progress} ✗ {name} (parse_operator 返回空)")
        except Exception as e:
            fail_list.append(name)
            log(f"  {progress} ✗ {name}: {e}")

        time.sleep(0.5)  # 限速，避免 PRTS 封 IP

    # 5. 更新本地 JSON
    if new_ops:
        local_ops.extend(new_ops)
        with open(ops_file, "w", encoding="utf-8") as f:
            json.dump(local_ops, f, ensure_ascii=False, indent=2)
        log(f"\n  已追加 {len(new_ops)} 名干员到 all_operators.json")

    if fail_list:
        log(f"  失败 {len(fail_list)} 名: {', '.join(fail_list)}")

    return new_ops


# ===================== 增量切块 =====================

def incremental_chunk(new_operators: list) -> int:
    """只对新干员做 JSON → 文本切块，追加到 chunks/knowledge/ 目录。

    复用 chunker.py 的 chunk_json_record() 逻辑。
    """
    if not new_operators:
        return 0

    log("\n增量切块...")
    from backend.data.chunker import chunk_json_record

    CHUNKS_DIR.mkdir(parents=True, exist_ok=True)

    # 找到当前 knowledge 目录下的最大 operators_json 序号
    existing = sorted(CHUNKS_DIR.glob("operators_json_*.txt"))
    start_idx = 0
    if existing:
        # 从文件名提取最大序号: operators_json_0123.txt → 123
        nums = []
        for p in existing:
            try:
                nums.append(int(p.stem.replace("operators_json_", "")))
            except ValueError:
                pass
        start_idx = max(nums) if nums else 0

    count = 0
    for i, op in enumerate(new_operators, 1):
        idx = start_idx + i
        chunks = chunk_json_record(op, "operators_json", idx)
        for chunk in chunks:
            out_path = CHUNKS_DIR / f"{chunk['chunk_id']}.txt"
            with open(out_path, "w", encoding="utf-8") as f:
                f.write(chunk["content"])
            count += 1

    log(f"  生成 {count} 个新 chunk 文件")
    return count


# ===================== 索引更新 =====================

def update_indexes():
    """增量更新 BM25 和 FAISS 索引，各自独立检测新 chunk。"""
    log("\n增量更新索引...")

    # --- BM25 增量 ---
    bm25_new_ids = _find_new_chunk_ids("bm25")
    if bm25_new_ids:
        _update_bm25(bm25_new_ids)
    else:
        log("  BM25: 无新增")

    # --- FAISS 增量 ---
    faiss_new_ids = _find_new_chunk_ids("faiss")
    if faiss_new_ids:
        _update_faiss(faiss_new_ids)
    else:
        log("  FAISS: 无新增")


def _update_bm25(new_ids: set):
    """增量追加文档到 BM25 索引。"""
    log("  BM25 增量更新...")
    try:
        from backend.data.bm25_index import BM25Indexer
        bm25_path = str(CHUNKS_DIR.parent / "knowledge_bm25.pkl")
        if Path(bm25_path).exists():
            indexer = BM25Indexer.load(bm25_path)
            new_texts = []
            new_corpus_ids = []
            for cid in sorted(new_ids):
                chunk_file = CHUNKS_DIR / f"{cid}.txt"
                if chunk_file.exists():
                    new_texts.append(chunk_file.read_text(encoding="utf-8"))
                    new_corpus_ids.append(cid)
            if new_texts:
                indexer.add_documents(new_texts, new_corpus_ids)
                indexer.save(bm25_path)
                log(f"    ✓ +{len(new_texts)} 文档, 总计 {indexer.corpus_size}")
            else:
                log("    无有效文件")
        else:
            log("    索引文件不存在，建议用 --rebuild-index 全量构建")
    except Exception as e:
        log(f"    ✗ {e}")

def _update_faiss(new_ids: set):
    """增量追加文档到 FAISS 索引。"""
    log("  FAISS 增量更新 (需要嵌入 API)...")
    try:
        from backend.api.siliconflow import SiliconFlowClient
        from backend.storage.faiss_client import FAISSClientWrapper
        from langchain_core.documents import Document

        client = FAISSClientWrapper()
        emb_client = SiliconFlowClient()

        # 收集已存在的 chunk_id
        existing_ids = set()
        result = client.load_index("knowledge")
        if result:
            _, meta = result
            existing_ids = {m["id"] for m in meta.values()}

        new_docs = []
        for cid in sorted(new_ids):
            if cid not in existing_ids:
                chunk_file = CHUNKS_DIR / f"{cid}.txt"
                if chunk_file.exists():
                    content = chunk_file.read_text(encoding="utf-8")
                    new_docs.append(Document(
                        page_content=content,
                        metadata={
                            "chunk_id": cid,
                            "section": cid,
                            "source_file": chunk_file.name,
                            "source_collection": "knowledge",
                        }
                    ))

        if new_docs:
            texts = [d.page_content for d in new_docs]
            embeddings = emb_client.embed(texts)
            new_count = client.add_documents("knowledge", new_docs, embeddings=embeddings)
            log(f"    ✓ +{len(new_docs)} 文档, 总计 {new_count}")
        else:
            total = client.get_chunk_count("knowledge")
            log(f"    无新增（已有 {total} 文档）")
    except Exception as e:
        log(f"    ✗ {e}")


def _find_new_chunk_ids(source: str = "bm25") -> set:
    """对比 chunks/knowledge/ 目录和指定索引，找出新增的 chunk_id。

    Args:
        source: "bm25" 或 "faiss"
    """
    chunk_files = set(p.stem for p in CHUNKS_DIR.glob("*.txt"))
    if not chunk_files:
        return set()

    if source == "bm25":
        bm25_path = CHUNKS_DIR.parent / "knowledge_bm25.pkl"
        if not Path(bm25_path).exists():
            return chunk_files
        try:
            from backend.data.bm25_index import BM25Indexer
            indexer = BM25Indexer.load(str(bm25_path))
            return chunk_files - set(indexer.corpus_ids)
        except Exception:
            return set()

    elif source == "faiss":
        try:
            from backend.storage.faiss_client import FAISSClientWrapper
            client = FAISSClientWrapper()
            result = client.load_index("knowledge")
            if result is None:
                return chunk_files
            _, meta = result
            existing = {m["id"] for m in meta.values()}
            return chunk_files - existing
        except Exception:
            return set()

    return set()


# ===================== 索引重建 =====================

def rebuild_all_indexes():
    """全量重建 BM25 和 FAISS 索引。"""
    log("\n重建索引...")

    # BM25
    log("  重建 BM25...")
    import subprocess
    bm25_script = BASE_DIR / "backend" / "data" / "bm25_index.py"
    result = subprocess.run(
        [sys.executable, str(bm25_script)],
        capture_output=True, text=True, cwd=str(BASE_DIR)
    )
    if result.returncode == 0:
        log("  ✓ BM25 完成")
    else:
        log(f"  ✗ BM25 失败: {result.stderr[:200]}")

    # FAISS
    log("  重建 FAISS (需要 API 调用，较慢)...")
    faiss_script = BASE_DIR / "backend" / "build_faiss_index.py"
    result = subprocess.run(
        [sys.executable, str(faiss_script), "--force"],
        capture_output=True, text=True, cwd=str(BASE_DIR),
        input="y\n",  # 自动确认
    )
    if result.returncode == 0:
        log("  ✓ FAISS 完成")
    else:
        log(f"  ✗ FAISS 失败: {result.stderr[:200]}")


# ===================== 敌人同步 =====================

def sync_enemies(dry_run: bool = False) -> list:
    """检测新增敌人并增量爬取（复用 enemy_scraper.py 的解析逻辑）。"""
    log("\n" + "=" * 60)
    log("敌人增量同步")

    # 外部爬虫模块
    sys.path.insert(0, str(SCRIPTS_DIR))
    try:
        from enemy_scraper import get_enemy_list_from_api, parse_enemy
    except ImportError:
        log("  ✗ 无法导入 enemy_scraper.py")
        return []

    # 1. PRTS 列表
    log("获取 PRTS 敌人列表...")
    try:
        prts_names = get_enemy_list_from_api()
        prts_set = set(prts_names)
        log(f"  PRTS 当前: {len(prts_names)} 个敌人")
    except Exception as e:
        log(f"  ✗ 获取失败: {e}")
        return []

    # 2. 本地数据
    enemies_file = DATA_DIR / "all_enemies.json"
    if not enemies_file.exists():
        log("  ✗ 本地 all_enemies.json 不存在")
        return []

    with open(enemies_file, "r", encoding="utf-8") as f:
        local_enemies = json.load(f)
    local_names = {e.get("名称", "") for e in local_enemies}
    log(f"  本地: {len(local_enemies)} 个敌人")

    # 3. Diff
    new_names = sorted(n for n in prts_set if n not in local_names)
    removed_names = sorted(n for n in local_names if n not in prts_set)

    if removed_names:
        log(f"  ⚠ PRTS 上已移除: {len(removed_names)} 个")
        for n in removed_names[:10]:
            log(f"      - {n}")

    if not new_names:
        log("  无新增敌人 ✓")
        return []

    log(f"  新增: {len(new_names)} 个")
    for n in new_names[:20]:
        log(f"      + {n}")
    if len(new_names) > 20:
        log(f"      ... 还有 {len(new_names) - 20} 个")

    if dry_run:
        log("  (dry-run 模式，跳过爬取)")
        return []

    # 4. 爬取
    log(f"\n开始爬取 {len(new_names)} 个新敌人...")
    new_enemies = []
    fail_list = []

    for i, name in enumerate(new_names):
        progress = f"[{i+1}/{len(new_names)}]"
        try:
            result = parse_enemy(name)
            if result and result.get("级别数据"):
                new_enemies.append(result)
                log(f"  {progress} ✓ {name}")
            else:
                fail_list.append(name)
                log(f"  {progress} ✗ {name} (无级别数据)")
        except Exception as e:
            fail_list.append(name)
            log(f"  {progress} ✗ {name}: {e}")
        time.sleep(0.3)

    # 5. 保存 + 切块
    if new_enemies:
        local_enemies.extend(new_enemies)
        with open(enemies_file, "w", encoding="utf-8") as f:
            json.dump(local_enemies, f, ensure_ascii=False, indent=2)
        log(f"\n  已追加 {len(new_enemies)} 个敌人")

        # 增量切块
        from backend.data.chunker import chunk_json_record
        CHUNKS_DIR.mkdir(parents=True, exist_ok=True)
        existing_chunks = sorted(CHUNKS_DIR.glob("enemies_json_*.txt"))
        max_idx = 0
        for p in existing_chunks:
            try:
                max_idx = max(max_idx, int(p.stem.replace("enemies_json_", "")))
            except ValueError:
                pass

        chunk_count = 0
        for i, enemy in enumerate(new_enemies, 1):
            chunks = chunk_json_record(enemy, "enemies_json", max_idx + i)
            for chunk in chunks:
                out_path = CHUNKS_DIR / f"{chunk['chunk_id']}.txt"
                with open(out_path, "w", encoding="utf-8") as f:
                    f.write(chunk["content"])
                chunk_count += 1
        log(f"  生成 {chunk_count} 个新 chunk 文件")

    if fail_list:
        log(f"  失败 {len(fail_list)} 个: {', '.join(fail_list[:10])}")

    return new_enemies


# ===================== 主入口 =====================

def main():
    parser = argparse.ArgumentParser(description="PRTS Wiki 增量同步")
    parser.add_argument("--dry-run", action="store_true",
                        help="只检测变化，不实际爬取")
    parser.add_argument("--rebuild-index", action="store_true",
                        help="爬取完成后全量重建 BM25 + FAISS 索引（慢）")
    parser.add_argument("--update-index", action="store_true",
                        help="爬取完成后增量更新 BM25 + FAISS 索引（快）")
    parser.add_argument("--skip-enemies", action="store_true",
                        help="跳过敌人检测")
    parser.add_argument("--index-only", action="store_true",
                        help="仅更新索引（不爬取新数据）")
    args = parser.parse_args()

    # 仅更新索引模式
    if args.index_only:
        update_indexes()
        return

    log("PRTS 增量同步开始")

    # 1. 干员
    new_ops = sync_operators(dry_run=args.dry_run)

    # 2. 增量切块
    if new_ops and not args.dry_run:
        incremental_chunk(new_ops)

    # 3. 敌人（基础检测）
    if not args.skip_enemies:
        sync_enemies(dry_run=args.dry_run)

    # 4. 索引更新
    if args.rebuild_index and new_ops and not args.dry_run:
        rebuild_all_indexes()
    elif args.update_index and not args.dry_run:
        update_indexes()

    log("\n同步完成")


if __name__ == "__main__":
    main()
