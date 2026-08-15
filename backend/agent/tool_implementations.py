"""
Tool implementations for AgenticRAG.
Each function takes a dict of arguments and returns a result.
"""

import asyncio
import logging
import threading
from typing import Dict, Any, List

logger = logging.getLogger(__name__)

# search_mode -> RRF vector_weight
# Higher vector_weight = more semantic; lower = more BM25 keyword
_MODE_WEIGHTS = {
    "precise": 0.25,    # Heavy BM25 for exact keyword matching (stats, skill names)
    "semantic": 0.75,   # Heavy vector for meaning-based queries (lore, relationships)
    "balanced": 0.5,    # Default even split
}

_MAX_RAG_TOP_K = 20  # 防止 LLM 传入超大 top_k 造成 API 成本与响应体积失控


def _run_rag_search_sync(query: str, top_k: int, vector_weight: float, enable_parent_expansion: bool) -> List[Dict]:
    """Blocking implementation of arknights_rag_search (runs in a worker thread)."""
    from backend.rag.retrievers import MultiChannelRetriever
    from backend.lc.embeddings import SiliconFlowEmbeddings
    from backend.lc.reranker import SiliconFlowReranker
    from backend.rag.parent_document import ParentDocumentRetriever
    from backend import config

    # Load BM25 indexes (lazy, cached)
    bm25_indexes = _get_bm25_indexes()

    # Multi-channel retrieval
    embeddings = SiliconFlowEmbeddings(api_key=config.SILICONFLOW_API_KEY)
    retriever = MultiChannelRetriever(
        embeddings=embeddings,
        faiss_index_dir=config.FAISS_INDEX_DIR_STR,
        bm25_indexes=bm25_indexes,
        top_k_per_channel=8,
        final_top_k=top_k * 3,  # Get more for reranking
        vector_weight=vector_weight,
    )

    docs = retriever.invoke(query)

    # Rerank
    reranker = SiliconFlowReranker(api_key=config.SILICONFLOW_API_KEY, top_n=top_k)
    reranked_docs = reranker.compress_documents(docs, query)

    # Parent document expansion
    parent_retriever = ParentDocumentRetriever()
    results = []
    for doc in reranked_docs:
        chunk_id = doc.metadata.get("chunk_id", "")
        source = doc.metadata.get("source_collection", "")
        content = doc.page_content

        # Expand operators/stories chunks to full parent doc
        if enable_parent_expansion and (chunk_id.startswith("operators_") or chunk_id.startswith("stories_")):
            chunk_data = {
                "chunk_id": chunk_id,
                "content": content,
                "metadata": dict(doc.metadata),
            }
            src = "operators" if chunk_id.startswith("operators_") else "stories"
            expanded = parent_retriever.get_parent_content(chunk_data, src)
            if expanded and len(expanded) > len(content):
                content = expanded

        results.append({
            "content": content[:2000],  # Truncate long content
            "source": source,
            "score": round(doc.metadata.get("relevance_score", 0.0), 4),
            "chunk_id": chunk_id,
        })

    # Deduplicate results after parent document expansion:
    # Multiple chunks from the same parent doc produce identical expanded content.
    # Keep the first occurrence (highest reranker score) for each unique content.
    seen_content = set()
    deduped = []
    for r in results:
        content_key = r["content"][:300]
        if content_key not in seen_content:
            seen_content.add(content_key)
            deduped.append(r)
    return deduped


async def execute_rag_search(arguments: Dict[str, Any], session_id: str = "") -> List[Dict]:
    """Execute arknights_rag_search tool.

    Internal pipeline: MultiChannelRetriever → SiliconFlowReranker → ParentDocumentRetriever

    Returns list of {content, source, score} dicts. The blocking retrieval/rerank
    work runs in a worker thread so parallel tool calls don't serialize on the event loop.
    """
    query = arguments.get("query", "")
    try:
        top_k = max(1, min(int(arguments.get("top_k", 8)), _MAX_RAG_TOP_K))
    except (TypeError, ValueError):
        top_k = 8
    enable_parent_expansion = arguments.get("enable_parent_expansion", True)
    search_mode = arguments.get("search_mode", "balanced")
    vector_weight = _MODE_WEIGHTS.get(search_mode, 0.5)

    if not query:
        return [{"error": "query parameter is required"}]

    try:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None,
            _run_rag_search_sync,
            query,
            top_k,
            vector_weight,
            enable_parent_expansion,
        )
    except Exception as e:
        logger.error(f"RAG search failed: {e}", exc_info=True)
        return [{"error": f"检索失败: {str(e)}"}]


async def execute_graphrag_search(arguments: Dict[str, Any], session_id: str = "") -> Dict:
    """Execute arknights_graphrag_search tool.
    
    Supports two modes:
    1. Single entity (entity): returns neighbors
    2. Two entities (entity1 + entity2): returns shortest path with edge info
    """
    entity = arguments.get("entity", "")
    entity1 = arguments.get("entity1", "")
    entity2 = arguments.get("entity2", "")

    try:
        from backend.rag.graphrag.query import get_graph_builder

        builder = get_graph_builder()
        if builder is None or builder.graph is None:
            return {"error": "知识图谱未加载，关系查询不可用"}

        if entity1 and entity2:
            # Two-entity path mode (max 3 hops to avoid meaningless long paths)
            result = builder.find_path(entity1, entity2, max_hops=3)
            if not result.get("path"):
                return {
                    "found": False,
                    "message": f"未找到 '{entity1}' 和 '{entity2}' 之间的直接关系路径（3跳以内）",
                    "entity1": entity1,
                    "entity2": entity2,
                }
            return {
                "found": True,
                "mode": "path",
                "entity1": entity1,
                "entity2": entity2,
                "path": result["path"],
                "edges": result["edges"],
            }
        elif entity:
            # Single-entity neighbor mode
            neighbors = builder.get_neighbors(entity)
            relations = builder.get_all_relations(entity)
            if not neighbors:
                return {
                    "found": False,
                    "message": f"未找到实体 '{entity}' 的关系信息",
                    "entity": entity,
                }
            return {
                "found": True,
                "mode": "neighbors",
                "entity": entity,
                "neighbors": neighbors,
                "relations": relations,
            }
        else:
            return {"error": "请提供 entity 或 entity1+entity2 参数"}

    except Exception as e:
        logger.error(f"GraphRAG search failed: {e}", exc_info=True)
        return {"error": f"关系查询失败: {str(e)}"}


async def execute_web_search(arguments: Dict[str, Any], session_id: str = "") -> List[Dict]:
    """Execute web_search tool using Tavily + DuckDuckGo.

    Deduplicates results against previously returned URLs within the same session,
    so the LLM doesn't see the same links across multiple rounds.
    """
    query = arguments.get("query", "")
    if not query:
        return [{"error": "query parameter is required"}]

    # Periodically cleanup old dedup entries to prevent memory leak
    _cleanup_web_search_seen()

    try:
        from backend.api.web_search import search as web_search

        # requests 是阻塞式 IO，放到工作线程避免卡住事件循环和并行执行的其他工具
        loop = asyncio.get_running_loop()
        results = await loop.run_in_executor(None, web_search, query, 5)

        if not results:
            return [{"message": "未找到相关网络搜索结果", "query": query}]

        # Get or create the seen-URL set for this session
        if session_id:
            seen = _web_search_seen.setdefault(session_id, set())
        else:
            seen = set()

        deduped = []
        for r in results:
            url = r.get("url", "")
            content = (r.get("snippet") or r.get("content", ""))[:300]
            # Deduplicate by URL, fallback to content prefix
            key = url if url else content[:200]
            if key and key in seen:
                continue
            if key:
                seen.add(key)
            deduped.append({
                "title": r.get("title", ""),
                "url": url,
                "content": content[:500],
                "source_id": "web",  # Add this for source citation
            })

        return deduped if deduped else [{"message": "未找到相关网络搜索结果（其余结果已在之前返回）", "query": query}]

    except Exception as e:
        logger.error(f"Web search failed: {e}", exc_info=True)
        return [{"error": f"网络搜索失败: {str(e)}"}]




# ===== Lazy-loaded singletons =====

_bm25_indexes = None
_bm25_lock = threading.Lock()

# Web search dedup: session_id -> set of seen URLs/content keys
_web_search_seen: Dict[str, set] = {}
_web_search_seen_max_size = 1000  # Maximum number of sessions to track


def clear_web_search_seen(session_id: str) -> None:
    """Clear web search dedup state for a session. Call when session expires."""
    _web_search_seen.pop(session_id, None)


def _cleanup_web_search_seen() -> None:
    """Remove oldest entries if web search dedup state exceeds max size."""
    if len(_web_search_seen) > _web_search_seen_max_size:
        # Remove oldest entries (first 20% of entries)
        keys_to_remove = list(_web_search_seen.keys())[:len(_web_search_seen) // 5]
        for key in keys_to_remove:
            _web_search_seen.pop(key, None)
        logger.info(f"[WebSearch] Cleaned up {len(keys_to_remove)} old dedup entries")


def _get_bm25_indexes():
    """Lazy-load BM25 indexes."""
    global _bm25_indexes

    if _bm25_indexes is not None:
        return _bm25_indexes

    from backend.data.bm25_index import BM25Indexer
    from backend import config

    with _bm25_lock:
        if _bm25_indexes is not None:
            return _bm25_indexes

        indexes = {}
        for name in ["operators", "stories", "knowledge"]:
            try:
                path = config.get_bm25_index_path(name)
                indexes[name] = BM25Indexer.load(path)
                logger.info(f"Loaded BM25 index: {name}")
            except FileNotFoundError:
                logger.warning(f"BM25 index file not found for '{name}', collection will be unavailable. Run: python backend/data/bm25_index.py")
            except Exception as e:
                logger.error(f"Failed to load BM25 index for '{name}': {e}")

        _bm25_indexes = indexes
        return _bm25_indexes
