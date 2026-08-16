"""
MultiChannelRetriever: BM25 + FAISS Vector + RRF across 3 collections.
Wraps the existing hybrid_search logic as a LangChain BaseRetriever.
"""
import hashlib
import logging
import threading
import warnings
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
    """Deserialize a dict back to a Document."""
    return Document(page_content=d["page_content"], metadata=d["metadata"])


def _get_recall_cache_key(
    query: str, top_k_per_channel: int, final_top_k: int,
    vector_weight: float = 0.5, inner_top_k: int = 20,
) -> str:
    key_str = f"{query}:{top_k_per_channel}:{final_top_k}:{vector_weight}:{inner_top_k}"
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
    """
    if collection_name in _VECTOR_STORES:
        return _VECTOR_STORES[collection_name]
    with _VECTOR_STORES_LOCK:
        if collection_name in _VECTOR_STORES:
            return _VECTOR_STORES[collection_name]
        client = FAISSClientWrapper(index_dir=faiss_index_dir or config.FAISS_INDEX_DIR_STR)
        vs = client.to_langchain_faiss(collection_name, embeddings)
        if vs is None:
            return None
        _VECTOR_STORES[collection_name] = vs
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

    # 3. Build rankings
    vector_ranking = {
        doc.metadata.get("chunk_id", f"{collection_name}_{i}"): i + 1
        for i, doc in enumerate(vector_docs)
        if doc.page_content
    }
    bm25_ranking = {
        r["chunk_id"]: i + 1
        for i, r in enumerate(bm25_docs)
        if r.get("content")
    }

    # 4. Weighted RRF fusion
    all_ids = set(vector_ranking) | set(bm25_ranking)
    combined: Dict[str, float] = {}
    for doc_id in all_ids:
        vec_rrf = 1.0 / (RRF_K + vector_ranking.get(doc_id, RRF_K + 100))
        bm25_rrf = 1.0 / (RRF_K + bm25_ranking.get(doc_id, RRF_K + 100))
        combined[doc_id] = vector_weight * vec_rrf + (1 - vector_weight) * bm25_rrf

    sorted_ids = sorted(combined, key=lambda x: combined[x], reverse=True)[:top_k]

    # 5. Build content-to-chunk_id lookup from BM25 for fixing FAISS docs without chunk_id
    bm25_content_to_id: Dict[str, str] = {}
    for r in bm25_docs:
        bm25_content_to_id[r.get("content", "")[:200]] = r["chunk_id"]

    # 6. Build result Documents
    vector_map = {}
    for i, doc in enumerate(vector_docs):
        cid = doc.metadata.get("chunk_id", "")
        if not cid:
            # Try to find chunk_id from BM25 by content match
            matched = bm25_content_to_id.get(doc.page_content[:200], "")
            if matched:
                doc.metadata["chunk_id"] = matched
                cid = matched
        vector_map[cid or f"{collection_name}_{i}"] = doc
    bm25_map = {r["chunk_id"]: r for r in bm25_docs}

    results = []
    for doc_id in sorted_ids:
        if doc_id in vector_map:
            doc = vector_map[doc_id]
            metadata = dict(doc.metadata)
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
        )
        cached = _get_cached_recall(cache_key)
        if cached is not None:
            return [_dict_to_doc(d) for d in cached]

        collections = ["operators", "stories", "knowledge"]

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
                results = future.result()
                for rank, doc in enumerate(results, 1):
                    chunk_id = doc.metadata.get("chunk_id", doc.page_content[:30])
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

        # Deduplicate by page_content, preferring docs with chunk_id
        seen_content: Dict[str, Document] = {}
        for doc in final:
            content_key = doc.page_content[:200]  # use first 200 chars as key
            existing = seen_content.get(content_key)
            if existing is None:
                seen_content[content_key] = doc
            elif not existing.metadata.get("chunk_id") and doc.metadata.get("chunk_id"):
                # Replace doc without chunk_id with one that has chunk_id
                seen_content[content_key] = doc
        final = list(seen_content.values())

        # Cache results before returning
        _set_cached_recall(cache_key, [_doc_to_dict(d) for d in final])
        return final
