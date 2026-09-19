"""
FAISS vector index wrapper for building and querying FAISS indexes.
Each collection (operators, stories, knowledge) has its own index file + metadata pkl.
"""
import logging
import os
import pickle
import threading
import time
from contextlib import contextmanager
from pathlib import Path
import numpy as np
from typing import List, Dict, Optional, Tuple

from langchain_core.documents import Document
from backend import config

logger = logging.getLogger(__name__)

# index 与 meta 是「一对」文件：分别整体覆盖写时中途失败会留下「向量数 n+m、
# 元数据 n」的错位文件；并发读-改-写还会互相覆盖丢更新。
# 因此写入统一走「临时文件 + os.replace」并用进程内锁 + 文件锁串行化。
_INDEX_WRITE_LOCK = threading.RLock()
_FILE_LOCK_TIMEOUT = 30.0        # 等待跨进程写锁的最长时间（秒）
_FILE_LOCK_STALE_AFTER = 300.0   # 锁文件超过该时长视为持有者已崩溃的残留
_FILE_LOCK_POLL = 0.05


@contextmanager
def _collection_file_lock(collection_name: str, index_dir: Path):
    """跨进程写锁：用 O_EXCL 创建锁文件，串行化同一集合的追加/重建。

    拿不到锁文件（只读目录等）时降级为「仅进程内锁」并记 warning，
    不阻断正常写入。
    """
    lock_path = index_dir / f"{collection_name}.write.lock"
    acquired = False
    deadline = time.time() + _FILE_LOCK_TIMEOUT
    while True:
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode("ascii"))
            os.close(fd)
            acquired = True
            break
        except FileExistsError:
            if time.time() >= deadline:
                logger.warning(
                    "[FAISS] Timed out waiting for write lock %s; "
                    "falling back to in-process lock only", lock_path,
                )
                break
            try:
                age = time.time() - lock_path.stat().st_mtime
            except OSError:
                age = 0.0
            if age > _FILE_LOCK_STALE_AFTER:
                logger.warning("[FAISS] Removing stale write lock: %s", lock_path)
                try:
                    lock_path.unlink()
                except OSError:
                    pass
            time.sleep(_FILE_LOCK_POLL)
        except OSError as exc:
            logger.warning(
                "[FAISS] File lock unavailable for '%s' (%s: %s); "
                "serializing in-process only", collection_name, type(exc).__name__, exc,
            )
            break
    try:
        yield
    finally:
        if acquired:
            try:
                lock_path.unlink()
            except OSError:
                pass


class FAISSClientWrapper:
    """Builds and loads FAISS indexes with associated document metadata."""

    def __init__(self, index_dir: str = None):
        self.index_dir = Path(index_dir) if index_dir else config.FAISS_INDEX_DIR
        self.index_dir.mkdir(parents=True, exist_ok=True)

    def _index_path(self, collection_name: str) -> Path:
        return self.index_dir / f"{collection_name}.index"

    def _meta_path(self, collection_name: str) -> Path:
        return self.index_dir / f"{collection_name}_meta.pkl"

    @staticmethod
    def _embed_documents(
        documents: List[Document],
        embedding_fn,
        batch_size: int = 20,
    ) -> List[List[float]]:
        """Batch-embed documents (batch size 20 to avoid API 413)."""
        embeddings = []
        for i in range(0, len(documents), batch_size):
            batch_docs = documents[i:i + batch_size]
            texts = [d.page_content for d in batch_docs]
            embeddings.extend(embedding_fn.embed_documents(texts))
        return embeddings

    @staticmethod
    def _doc_to_meta_entry(doc: Document, internal_id: int) -> Dict:
        """Build a metadata entry for one document."""
        return {
            "id": doc.metadata.get("chunk_id", f"doc_{internal_id}"),
            "page_content": doc.page_content,
            "metadata": dict(doc.metadata),
        }

    def _tmp_path(self, path: Path) -> Path:
        """同目录唯一临时文件名（同目录才能用 os.replace 原子替换）。"""
        return path.with_name(f"{path.name}.tmp-{os.getpid()}-{threading.get_ident()}")

    def _write_index_and_meta(self, collection_name: str, index, meta: Dict) -> None:
        """原子写入 index + meta：先写临时文件，再 os.replace 替换正式文件。

        直接覆盖写时，第二次写失败会留下「向量数 n+m、元数据 n」的错位索引；
        临时文件 + 原子替换保证正式文件要么是旧的完整版本，要么是新的完整版本。
        调用方必须已持有写锁。
        """
        import faiss

        idx_path = self._index_path(collection_name)
        meta_path = self._meta_path(collection_name)
        tmp_idx = self._tmp_path(idx_path)
        tmp_meta = self._tmp_path(meta_path)
        try:
            faiss.write_index(index, str(tmp_idx))
            with open(tmp_meta, "wb") as f:
                pickle.dump(meta, f)
            # 两个临时文件都写成功后再替换，缩短 index/meta 不一致的窗口
            os.replace(str(tmp_idx), str(idx_path))
            os.replace(str(tmp_meta), str(meta_path))
        finally:
            for tmp in (tmp_idx, tmp_meta):
                try:
                    if tmp.exists():
                        tmp.unlink()
                except OSError:
                    pass

    def _build_index_objects(
        self,
        collection_name: str,
        documents: List[Document],
        embeddings: List[List[float]],
    ) -> Tuple:
        """校验并构建 (faiss_index, meta) 对象，不落盘。"""
        # 一致性校验：向量数必须与文档数一致，否则 metadata 会与向量整体错位
        if not documents:
            raise ValueError(
                f"Cannot build index '{collection_name}': documents is empty"
            )
        if embeddings is None or len(embeddings) == 0:
            raise ValueError(
                f"Cannot build index '{collection_name}': no embeddings for "
                f"{len(documents)} documents"
            )
        if len(embeddings) != len(documents):
            raise ValueError(
                f"Embedding/document count mismatch for '{collection_name}': "
                f"{len(embeddings)} embeddings vs {len(documents)} documents; "
                f"refusing to build a misaligned index"
            )

        import faiss
        dims = {len(e) for e in embeddings}
        if len(dims) != 1 or 0 in dims:
            raise ValueError(
                f"Invalid embedding dimensions for '{collection_name}': {sorted(dims)}"
            )
        dim = len(embeddings[0])
        vectors = np.array(embeddings, dtype=np.float32)

        # Normalize for cosine similarity (use IndexFlatIP on normalized vectors)
        faiss.normalize_L2(vectors)
        index = faiss.IndexFlatIP(dim)
        index.add(vectors)

        # metadata: id -> {page_content, metadata}
        meta = {
            i: self._doc_to_meta_entry(doc, i)
            for i, doc in enumerate(documents)
        }
        return index, meta

    def build_index(
        self,
        collection_name: str,
        documents: List[Document],
        embeddings: List[List[float]] = None,
        embedding_fn=None,
    ) -> None:
        """Build and save a FAISS index for the given collection.

        Args:
            collection_name: Name of the collection (operators, stories, knowledge)
            documents: List of LangChain Document objects
            embeddings: Pre-computed embeddings (optional)
            embedding_fn: Embedding function to use if embeddings not provided
        """
        if embeddings is None:
            if embedding_fn is None:
                raise ValueError("Either embeddings or embedding_fn must be provided")
            embeddings = self._embed_documents(documents, embedding_fn)

        index, meta = self._build_index_objects(collection_name, documents, embeddings)

        with _INDEX_WRITE_LOCK, _collection_file_lock(collection_name, self.index_dir):
            self._write_index_and_meta(collection_name, index, meta)


    def load_index(self, collection_name: str) -> Optional[Tuple]:
        """Load a FAISS index and metadata.

        Returns:
            Tuple of (faiss_index, metadata_dict) or None if not found.
        """
        import faiss

        idx_path = self._index_path(collection_name)
        meta_path = self._meta_path(collection_name)

        if not idx_path.exists() or not meta_path.exists():
            return None

        index = faiss.read_index(str(idx_path))
        with open(meta_path, "rb") as f:
            meta = pickle.load(f)

        return index, meta

    def add_documents(
        self,
        collection_name: str,
        documents: List[Document],
        embeddings: List[List[float]] = None,
        embedding_fn=None,
    ) -> int:
        """增量向已有 FAISS 索引追加文档。

        加载已有索引 → 嵌入新文档 → add 到 FAISS → 更新 metadata → 原子保存。
        返回追加后的总向量数。

        load→add→write 全程持锁（进程内 + 跨进程文件锁）：否则并发追加会
        读到同一份旧索引并互相覆盖，丢更新。

        Args:
            collection_name: 集合名称
            documents: 新文档列表
            embeddings: 预计算的嵌入向量（可选）
            embedding_fn: 嵌入函数（embeddings 为空时必填）
        """
        import faiss

        # 生成新嵌入（先算嵌入，避免长时间持有写锁）
        if embeddings is None:
            if embedding_fn is None:
                raise ValueError("Either embeddings or embedding_fn must be provided")
            embeddings = self._embed_documents(documents, embedding_fn)

        # 同样的错位校验：追加批次少返回向量会让向量与 metadata 整体错位
        if len(embeddings) != len(documents):
            raise ValueError(
                f"Embedding/document count mismatch for '{collection_name}': "
                f"{len(embeddings)} embeddings vs {len(documents)} documents; "
                f"refusing to append a misaligned batch"
            )

        with _INDEX_WRITE_LOCK, _collection_file_lock(collection_name, self.index_dir):
            # 加载已有索引
            result = self.load_index(collection_name)
            if result is None:
                # 索引不存在，创建新的
                index, meta = self._build_index_objects(collection_name, documents, embeddings)
                self._write_index_and_meta(collection_name, index, meta)
                return index.ntotal

            index, meta = result
            old_count = index.ntotal

            # 归一化并追加向量
            vectors = np.array(embeddings, dtype=np.float32)
            faiss.normalize_L2(vectors)
            index.add(vectors)

            # 追加 metadata
            for i, doc in enumerate(documents):
                new_id = old_count + i
                meta[new_id] = self._doc_to_meta_entry(doc, new_id)

            # 保存
            self._write_index_and_meta(collection_name, index, meta)

            return index.ntotal

    def get_chunk_count(self, collection_name: str) -> int:
        """Get number of vectors in the index."""
        import faiss

        idx_path = self._index_path(collection_name)
        if not idx_path.exists():
            return 0

        try:
            index = faiss.read_index(str(idx_path))
            return index.ntotal
        except Exception as exc:
            # 索引损坏 / 权限 / IO 失败不等于「集合为空」：至少留下 warning，
            # 避免上层把损坏索引静默当成空集合（进而覆盖重建或返回空结果）。
            logger.warning(
                "[FAISS] Failed to read index for '%s' (%s: %s); reporting 0 chunks",
                collection_name, type(exc).__name__, exc,
            )
            return 0

    def to_langchain_faiss(
        self, collection_name: str, embedding_fn
    ):
        """Convert a saved FAISS index to a LangChain FAISS vector store.

        Args:
            collection_name: Collection to load
            embedding_fn: LangChain Embeddings instance (required by LangChain FAISS)

        Returns:
            langchain_community.vectorstores.FAISS instance or None
        """
        from langchain_community.vectorstores import FAISS
        from langchain_community.docstore.in_memory import InMemoryDocstore

        result = self.load_index(collection_name)
        if result is None:
            return None

        index, meta = result

        # 校验索引与元数据条数一致，否则向量位置与文档 id 会静默错位
        index_total = getattr(index, "ntotal", None)
        if index_total != len(meta):
            raise ValueError(
                f"FAISS index/meta mismatch for '{collection_name}': "
                f"index.ntotal={index_total} vs {len(meta)} metadata entries; "
                f"refusing to load a misaligned index"
            )

        # Reconstruct LangChain Documents from metadata
        documents = []
        docstore_ids = []
        for idx in sorted(meta.keys()):
            m = meta[idx]
            doc = Document(
                page_content=m["page_content"],
                metadata=m["metadata"],
            )
            # Ensure chunk_id is always in metadata
            if "chunk_id" not in doc.metadata:
                doc.metadata["chunk_id"] = m["id"]
            documents.append(doc)
            docstore_ids.append(str(idx))

        if not documents:
            return None

        # Build LangChain FAISS from existing index + docstore (no re-embedding).
        # docstore id 取 meta 的真实键（而非枚举下标），meta 键不连续时也不会错位。
        docstore = InMemoryDocstore(
            {doc_id: doc for doc_id, doc in zip(docstore_ids, documents)}
        )
        index_to_docstore_id = {i: doc_id for i, doc_id in enumerate(docstore_ids)}
        return FAISS(
            embedding_function=embedding_fn,
            index=index,
            docstore=docstore,
            index_to_docstore_id=index_to_docstore_id,
        )
