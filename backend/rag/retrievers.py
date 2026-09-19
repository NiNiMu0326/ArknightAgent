"""
MultiChannelRetriever: BM25 + FAISS Vector + RRF across 3 collections.
Wraps the existing hybrid_search logic as a LangChain BaseRetriever.
"""
import hashlib
import logging
import threading
import warnings
from pathlib import Path
from typing import Any, Dict, List, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

from langchain_core.documents import Document
from langchain_core.retrievers import BaseRetriever
from langchain_core.callbacks.manager import CallbackManagerForRetrieverRun
from pydantic import Field

from backend.rag.cache import LRUCache
from backend.lc.embeddings import SiliconFlowEmbeddings
from backend.data.bm25_index import BM25Indexer
from backend.storage.faiss_client import FAISSClientWrapper
from backend import config

RRF_K = config.RRF_K
logger = logging.getLogger(__name__)

# ===== Shared LRU cache with 5-hour TTL for recall results =====
_RECALL_CACHE_LOCK = threading.Lock()
_RECALL_CACHE_TTL = 18000  # 5 hours
_RECALL_CACHE_MAX_SIZE = 200
_RECALL_CACHE = LRUCache(max_size=_RECALL_CACHE_MAX_SIZE, ttl_seconds=_RECALL_CACHE_TTL)


def _doc_to_dict(doc: Document) -> Dict:
    """Serialize a Document to a plain dict for caching."""
    return {"page_content": doc.page_content, "metadata": dict(doc.metadata)}


def _dict_to_doc(d: Dict) -> Document:
    """Deserialize a dict back to a Document.

    metadata 必须复制：缓存条目是进程级共享状态，直接把同一个 dict 交给
    Document，调用方的任何改写都会污染后续所有请求（且在多线程下互相干扰）。
    """
    return Document(page_content=d["page_content"], metadata=dict(d["metadata"]))


def _content_identity(content: str) -> str:
    """内容身份键：整段内容的哈希，空内容返回空串。

    旧实现用 page_content[:200] 当身份键：长文本/同前缀分叉的文档会互相覆盖，
    空内容又全部坍缩到 "" 键，因此改为整段内容哈希（精确匹配）。
    """
    if not content:
        return ""
    return hashlib.md5(content.encode("utf-8")).hexdigest()


def _embedding_identity(embeddings) -> str:
    """嵌入模型的尽力而为身份标识，参与缓存键。"""
    if embeddings is None:
        return "none"
    parts = []
    for attr in ("model", "model_name", "model_id", "base_url", "api_base"):
        value = getattr(embeddings, attr, None)
        if value:
            parts.append(f"{attr}={value}")
    if not parts:
        parts.append(type(embeddings).__name__)
    return "|".join(str(p) for p in parts)


def _index_fingerprint(index_dir: str, collection_name: str) -> str:
    """索引文件指纹：文件被重建（mtime/size 变化）即得到不同指纹。"""
    base = Path(index_dir)
    parts = []
    for name in (f"{collection_name}.index", f"{collection_name}_meta.pkl"):
        try:
            st = (base / name).stat()
            parts.append(f"{name}:{st.st_mtime_ns}:{st.st_size}")
        except OSError:
            parts.append(f"{name}:missing")
    return ",".join(parts)


def _index_identity(
    embeddings, faiss_index_dir: str,
    collections: tuple = ("operators", "stories", "knowledge"),
) -> str:
    """召回结果的索引身份：嵌入模型标识 + 索引目录 + 各集合索引文件指纹。

    缓存键缺少这些维度时，切换嵌入模型或重建索引后会一直命中旧结果（脏读）。
    """
    index_dir = str(faiss_index_dir or config.FAISS_INDEX_DIR_STR)
    parts = [_embedding_identity(embeddings), index_dir]
    parts.extend(_index_fingerprint(index_dir, c) for c in collections)
    return "|".join(parts)


def _get_recall_cache_key(
    query: str, top_k_per_channel: int, final_top_k: int,
    vector_weight: float = 0.5, inner_top_k: int = 20,
    index_identity: str = "",
) -> str:
    key_str = (
        f"{query}:{top_k_per_channel}:{final_top_k}:{vector_weight}"
        f":{inner_top_k}:{index_identity}"
    )
    return hashlib.md5(key_str.encode("utf-8")).hexdigest()


def _get_cached_recall(cache_key: str) -> Optional[List[Dict]]:
    with _RECALL_CACHE_LOCK:
        cached = _RECALL_CACHE.get(cache_key)
        if cached is not None:
            logger.info(f"[RecallCache] HIT (cache_size={len(_RECALL_CACHE)})")
            return cached
        logger.info(f"[RecallCache] MISS (cache_size={len(_RECALL_CACHE)})")
    return None


def _set_cached_recall(cache_key: str, results: List[Dict]) -> None:
    with _RECALL_CACHE_LOCK:
        _RECALL_CACHE.set(cache_key, results)
        logger.info(f"[RecallCache] STORED {len(results)} docs (cache_size={len(_RECALL_CACHE)})")


def clear_recall_cache() -> None:
    """Clear multi-channel recall cache. Call when indexes are rebuilt."""
    with _RECALL_CACHE_LOCK:
        _RECALL_CACHE.clear()
    clear_vector_store_cache()


# ===== Process-level FAISS vector store cache =====
# to_langchain_faiss() reads the index + unpickles metadata from disk on every
# call. MultiChannelRetriever instances are per-request, so their instance-level
# _vector_stores cache never survives a single tool call. Cache stores process-wide.
_VECTOR_STORES: Dict[str, Any] = {}
_VECTOR_STORES_LOCK = threading.Lock()


def get_cached_vector_store(collection_name: str, embeddings, faiss_index_dir: str = ""):
    """Load a LangChain FAISS vector store once per process, then reuse it.

    Returns None if the index files don't exist (caller falls back to BM25-only).

    缓存键包含嵌入模型标识、索引目录与索引文件指纹：换了嵌入模型或重建了索引
    都必须重新加载，否则会一直命中旧索引（脏读）。
    """
    index_dir = faiss_index_dir or config.FAISS_INDEX_DIR_STR
    cache_key = (
        f"{collection_name}|{_embedding_identity(embeddings)}|{index_dir}"
        f"|{_index_fingerprint(index_dir, collection_name)}"
    )
    vs = _VECTOR_STORES.get(cache_key)
    if vs is not None:
        return vs
    with _VECTOR_STORES_LOCK:
        vs = _VECTOR_STORES.get(cache_key)
        if vs is not None:
            return vs
        client = FAISSClientWrapper(index_dir=index_dir)
        vs = client.to_langchain_faiss(collection_name, embeddings)
        if vs is None:
            return None
        # 同一集合只保留最新索引对应的 store，避免重建后旧 store 常驻内存
        for stale_key in [k for k in _VECTOR_STORES if k.split("|", 1)[0] == collection_name]:
            _VECTOR_STORES.pop(stale_key, None)
        _VECTOR_STORES[cache_key] = vs
        logger.info(f"[FAISS] Loaded and cached vector store: {collection_name}")
        return vs



def clear_vector_store_cache() -> None:
    """Clear cached FAISS vector stores. Call when indexes are rebuilt."""
    with _VECTOR_STORES_LOCK:
        _VECTOR_STORES.clear()


def _rrf_fusion(rankings: List[Dict[str, int]], k: int = 60) -> Dict[str, float]:
    """Reciprocal Rank Fusion."""
    scores: Dict[str, float] = {}
    for ranking in rankings:
        for doc_id, rank in ranking.items():
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank)
    return scores


def _hybrid_search_collection(
    query: str,
    collection_name: str,
    vector_store,
    bm25_indexer: BM25Indexer,
    top_k: int = 8,
    inner_top_k: int = 20,
    vector_weight: float = 0.5,
) -> List[Document]:
    """Hybrid search (FAISS vector + BM25 + RRF) on a single collection."""
    # 1. FAISS vector search
    try:
        vector_docs = vector_store.similarity_search(query, k=inner_top_k)
    except Exception as e:
        warnings.warn(f"FAISS vector search failed on '{collection_name}': {e}")
        vector_docs = []

    # 2. BM25 search
    bm25_indices = bm25_indexer.retrieve(query, top_k=inner_top_k)
    bm25_docs: List[Dict] = []
    if bm25_indexer.corpus and hasattr(bm25_indexer, "corpus_ids") and bm25_indexer.corpus_ids:
        for idx in bm25_indices:
            if idx is not None and idx < len(bm25_indexer.corpus):
                bm25_docs.append({
                    "chunk_id": bm25_indexer.corpus_ids[idx],
                    "content": bm25_indexer.corpus[idx],
                })

    # 3. Resolve one stable key per vector doc.
    # Build the content-to-chunk_id lookup from BM25 first and backfill missing
    # chunk_ids BEFORE building the rankings: otherwise the ranking holds a
    # synthetic key while the result-building step below uses the backfilled
    # real chunk_id, so the vector hit is silently dropped in the fusion.
    # 内容匹配键用整段内容哈希（旧实现用 page_content[:200]，同前缀文档会串号）。
    bm25_content_to_id: Dict[str, str] = {}
    for r in bm25_docs:
        content_key = _content_identity(r.get("content", ""))
        if content_key:
            bm25_content_to_id[content_key] = r["chunk_id"]

    vector_keys: Dict[int, str] = {}
    for i, doc in enumerate(vector_docs):
        cid = doc.metadata.get("chunk_id", "")
        if not cid:
            # Try to find chunk_id from BM25 by exact content match
            matched = bm25_content_to_id.get(_content_identity(doc.page_content), "")
            if matched:
                cid = matched
        vector_keys[i] = cid or f"{collection_name}_{i}"

    # 4. Build rankings (keys are shared with vector_map / bm25_map below)
    vector_ranking = {
        vector_keys[i]: i + 1
        for i, doc in enumerate(vector_docs)
        if doc.page_content
    }
    bm25_ranking = {
        r["chunk_id"]: i + 1
        for i, r in enumerate(bm25_docs)
        if r.get("content")
    }

    # 5. Weighted RRF fusion
    all_ids = set(vector_ranking) | set(bm25_ranking)
    combined: Dict[str, float] = {}
    for doc_id in all_ids:
        vec_rrf = 1.0 / (RRF_K + vector_ranking.get(doc_id, RRF_K + 100))
        bm25_rrf = 1.0 / (RRF_K + bm25_ranking.get(doc_id, RRF_K + 100))
        combined[doc_id] = vector_weight * vec_rrf + (1 - vector_weight) * bm25_rrf

    sorted_ids = sorted(combined, key=lambda x: combined[x], reverse=True)[:top_k]

    # 6. Build result Documents (same keys as the rankings above)
    vector_map = {vector_keys[i]: i for i in range(len(vector_docs))}
    bm25_map = {r["chunk_id"]: r for r in bm25_docs}

    results = []
    for doc_id in sorted_ids:
        if doc_id in vector_map:
            idx = vector_map[doc_id]
            doc = vector_docs[idx]
            metadata = dict(doc.metadata)
            if not metadata.get("chunk_id") and doc_id != f"{collection_name}_{idx}":
                # BM25 内容匹配回填出的 chunk_id 只写进本次结果的副本，
                # 不原地改共享 vector store 的 docstore（跨请求污染共享状态）
                metadata["chunk_id"] = doc_id
        elif doc_id in bm25_map:
            metadata = {"chunk_id": doc_id, "source": collection_name}
            doc = Document(page_content=bm25_map[doc_id]["content"], metadata=metadata)
        else:
            continue
        metadata["fused_score"] = combined[doc_id]
        metadata["source_collection"] = collection_name
        results.append(Document(page_content=doc.page_content, metadata=metadata))

    return results


class MultiChannelRetriever(BaseRetriever):
    """Retrieves from operators/stories/knowledge collections via FAISS+BM25+RRF."""

    embeddings: SiliconFlowEmbeddings
    faiss_index_dir: str = ""
    bm25_indexes: Dict[str, Any] = Field(default_factory=dict)
    top_k_per_channel: int = 8
    final_top_k: int = 24
    vector_weight: float = 0.5
    inner_top_k: int = 20

    class Config:
        arbitrary_types_allowed = True

    def _bm25_only_search(
        self,
        query: str,
        collection_name: str,
        bm25_indexer: BM25Indexer,
        top_k: int,
    ) -> List[Document]:
        """BM25-only search when FAISS is unavailable."""
        bm25_indices = bm25_indexer.retrieve(query, top_k=top_k)
        results = []
        for rank, idx in enumerate(bm25_indices):
            if idx is None or idx >= len(bm25_indexer.corpus):
                continue
            content = bm25_indexer.corpus[idx]
            if bm25_indexer.corpus_ids and idx < len(bm25_indexer.corpus_ids):
                chunk_id = bm25_indexer.corpus_ids[idx]
            else:
                chunk_id = f"{collection_name}_{idx}"
            metadata = {
                "chunk_id": chunk_id,
                "source": collection_name,
                "source_collection": collection_name,
                "bm25_score": 1.0 / (rank + 1),
            }
            results.append(Document(page_content=content, metadata=metadata))
        return results

    def _get_relevant_documents(
        self, query: str, *, run_manager: CallbackManagerForRetrieverRun
    ) -> List[Document]:
        # Check recall cache first
        cache_key = _get_recall_cache_key(
            query, self.top_k_per_channel, self.final_top_k,
            self.vector_weight, self.inner_top_k,
            index_identity=_index_identity(self.embeddings, self.faiss_index_dir),
        )
        cached = _get_cached_recall(cache_key)
        if cached is not None:
            return [_dict_to_doc(d) for d in cached]

        collections = ["operators", "stories", "knowledge"]
        # 记录是否发生了 FAISS 降级（BM25-only）：降级结果不进缓存
        degraded = threading.Event()

        def search_one(coll_name: str):
            if coll_name not in self.bm25_indexes:
                return []
            bm25_indexer = self.bm25_indexes[coll_name]
            try:
                vs = get_cached_vector_store(coll_name, self.embeddings, self.faiss_index_dir)
                if vs is None:
                    raise FileNotFoundError(f"FAISS index for '{coll_name}' not found")
                return _hybrid_search_collection(
                    query=query,
                    collection_name=coll_name,
                    vector_store=vs,
                    bm25_indexer=bm25_indexer,
                    top_k=self.top_k_per_channel,
                    inner_top_k=self.inner_top_k,
                    vector_weight=self.vector_weight,
                )
            except Exception as e:
                warnings.warn(f"FAISS unavailable for '{coll_name}', using BM25-only: {e}")
                degraded.set()
                return self._bm25_only_search(
                    query=query,
                    collection_name=coll_name,
                    bm25_indexer=bm25_indexer,
                    top_k=self.top_k_per_channel,
                )

        all_rankings: List[Dict[str, int]] = []
        all_docs: Dict[str, Document] = {}

        with ThreadPoolExecutor(max_workers=3) as executor:
            futures = {executor.submit(search_one, c): c for c in collections}
            for future in as_completed(futures):
                coll_name = futures[future]
                results = future.result()
                for rank, doc in enumerate(results, 1):
                    # 身份键：chunk_id 优先，缺失时用整段内容哈希（旧实现用
                    # page_content[:30]，同前缀文档会被合并成同一条）
                    chunk_id = doc.metadata.get("chunk_id") or ""
                    if not chunk_id:
                        chunk_id = _content_identity(doc.page_content) or f"{coll_name}_anon_{rank}"
                    all_docs[chunk_id] = doc
                    all_rankings.append({chunk_id: rank})

        # Cross-collection RRF
        fused = _rrf_fusion(all_rankings, k=RRF_K)
        sorted_ids = sorted(fused, key=lambda x: fused[x], reverse=True)[: self.final_top_k]

        final = []
        for doc_id in sorted_ids:
            if doc_id in all_docs:
                doc = all_docs[doc_id]
                doc.metadata["cross_collection_score"] = fused[doc_id]
                final.append(doc)

        # Deduplicate: chunk_id 优先，缺失时退化为整段内容哈希。
        # 旧实现用 page_content[:200] 作键，共享前 200 字符的不同文档会被误判为重复。
        # 先按内容选出「带 chunk_id 优先」的代表，再按身份键去重并保持原顺序。
        preferred_by_content: Dict[str, Document] = {}
        for doc in final:
            content_key = _content_identity(doc.page_content)
            if not content_key:
                continue
            current = preferred_by_content.get(content_key)
            if current is None or (
                not (current.metadata.get("chunk_id") or "")
                and (doc.metadata.get("chunk_id") or "")
            ):
                preferred_by_content[content_key] = doc

        seen: Dict[str, Document] = {}
        deduped: List[Document] = []
        for doc in final:
            content_key = _content_identity(doc.page_content)
            if content_key and preferred_by_content.get(content_key) is not doc:
                # 同一内容已有带 chunk_id 的代表，丢弃当前这条
                continue
            chunk_id = doc.metadata.get("chunk_id") or ""
            if chunk_id:
                key = f"id:{chunk_id}"
            elif content_key:
                key = f"content:{content_key}"
            else:
                # 空内容且无 chunk_id：不参与去重，避免全部坍缩到同一个键
                key = f"obj:{id(doc)}"
            if key in seen:
                continue
            seen[key] = doc
            deduped.append(doc)
        final = deduped

        # 空结果与「FAISS 降级为 BM25-only」的结果不写缓存：否则一次暂时性故障
        # 会在整个 TTL（5 小时）内持续返回错误/空答案。
        if final and not degraded.is_set():
            _set_cached_recall(cache_key, [_doc_to_dict(d) for d in final])
        else:
            logger.info(
                "[RecallCache] SKIP store (empty=%s, degraded=%s)",
                not final, degraded.is_set(),
            )
        return final
