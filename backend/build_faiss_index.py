"""
Build FAISS vector indexes for all collections.
Run this script to rebuild the vector database after it becomes corrupted or empty.
"""
import logging
import sys
import os
from pathlib import Path

# Setup path
SCRIPT_DIR = Path(__file__).parent.resolve()
PROJECT_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(PROJECT_ROOT))
os.chdir(PROJECT_ROOT)

from backend import config
from backend.api.siliconflow import SiliconFlowClient
from backend.storage.faiss_client import FAISSClientWrapper
from langchain_core.documents import Document

logger = logging.getLogger(__name__)


def build_all_indexes(force: bool = False):
    """Build FAISS indexes for all 3 collections by embedding chunk texts."""
    client = FAISSClientWrapper()
    embedding_client = SiliconFlowClient()

    collections = ['operators', 'stories', 'knowledge']

    for coll_name in collections:
        chunks_dir = Path(config.CHUNKS_DIR) / coll_name
        if not chunks_dir.exists():
            print(f"Skipping '{coll_name}' - directory not found: {chunks_dir}")
            continue

        # Check if already built
        try:
            existing = client.get_chunk_count(coll_name)
            if not force and existing > 0:
                print(f"Collection '{coll_name}' already has {existing} chunks, skipping. Use force=True to rebuild.")
                continue
        except (OSError, EOFError, ValueError, RuntimeError, ImportError) as e:
            # 索引损坏 / IO / 权限失败不能被当成“索引不存在”静默覆盖，至少留下日志
            logger.warning(
                "读取 '%s' 现有索引失败（%s: %s），继续按需重建",
                coll_name, type(e).__name__, e,
            )

        # Load chunks
        chunk_files = list(chunks_dir.glob('*.md')) + list(chunks_dir.glob('*.txt'))
        chunk_files.sort()

        documents = []
        for path in chunk_files:
            with open(path, 'r', encoding='utf-8') as f:
                content = f.read()
            chunk_id = path.stem
            documents.append(Document(
                page_content=content,
                metadata={
                    'chunk_id': chunk_id,
                    'section': chunk_id,
                    'source_file': path.name,
                    'source_collection': coll_name,
                }
            ))

        if not documents:
            print(f"Skipping '{coll_name}' - no chunk files found in {chunks_dir}")
            continue

        print(f"Embedding {len(documents)} chunks for collection '{coll_name}'...")

        # Build FAISS index using pre-computed embeddings (batch size 20)
        batch_size = 20
        all_embeddings = []
        for i in range(0, len(documents), batch_size):
            batch_docs = documents[i:i + batch_size]
            texts = [d.page_content for d in batch_docs]
            batch_emb = embedding_client.embed(texts)
            all_embeddings.extend(batch_emb)
            print(f"  Embedded {min(i + batch_size, len(documents))}/{len(documents)}")

        # 某批 embed 少返回时，按 documents 顺序生成 metadata 会与向量错位，必须先拦住
        if len(all_embeddings) != len(documents):
            raise ValueError(
                f"Collection '{coll_name}': got {len(all_embeddings)} embeddings for "
                f"{len(documents)} chunks; aborting to avoid document/vector misalignment"
            )

        # Save index
        client.build_index(
            collection_name=coll_name,
            documents=documents,
            embeddings=all_embeddings,
        )

        print(f"Built index for '{coll_name}': {len(documents)} chunks")


if __name__ == "__main__":
    force = '--force' in sys.argv or '-f' in sys.argv

    if not force:
        print("WARNING: This will rebuild FAISS vector indexes.")
        print("It will make API calls for ~2800 chunks (operators + stories + knowledge).")
        confirm = input("Continue? (y/n): ")
        if confirm.lower() != 'y':
            print("Cancelled.")
            sys.exit(0)

    build_all_indexes(force=True)
    print("\nDone! FAISS indexes rebuilt.")
