"""
统一的重建索引入口脚本。
步骤1: 使用 chunker 切分原始数据 -> chunks/
步骤2: 使用 build_faiss_index 将 chunks 嵌入到 FAISS

用法:
    python rebuild_index.py          # 完整重建（切块 + 建索引）
    python rebuild_index.py --index  # 仅重建索引（跳过切块）
    python rebuild_index.py --chunk  # 仅切分数据（跳过建索引）
"""
import sys
import os
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent.resolve()
sys.path.insert(0, str(SCRIPT_DIR))
os.chdir(SCRIPT_DIR)

import config


def run_chunking():
    """步骤1: 切分原始数据到 chunks/ 目录"""
    from data.chunker import chunk_all_data
    print("=" * 50)
    print("Step 1: Chunking raw data...")
    print("=" * 50)
    chunk_all_data()
    print("\nChunking done.\n")


def run_indexing():
    """步骤2: 将 chunks/ 嵌入到 FAISS"""
    from build_faiss_index import build_all_indexes
    print("=" * 50)
    print("Step 2: Building FAISS vector indexes...")
    print("=" * 50)
    build_all_indexes(force=True)
    print("\nIndexing done.\n")


if __name__ == "__main__":
    args = sys.argv[1:]
    only_index = "--index" in args
    only_chunk = "--chunk" in args

    print("=== Rebuilding Arknights Agent Indexes ===")
    print()

    if only_chunk:
        run_chunking()
    elif only_index:
        run_indexing()
    else:
        # 默认: 两步都执行
        run_chunking()
        run_indexing()

    print("=" * 50)
    print("All done!")
    print("=" * 50)
