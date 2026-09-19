"""
Tests for backend.rag.retrievers: RRF fusion + recall cache.
Usage: cd test && python -m pytest test_retrievers.py -v
"""
import sys
import time
from typing import Any, Dict
import pytest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from backend.rag.cache import LRUCache
from backend.rag.retrievers import (
    MultiChannelRetriever,
    _rrf_fusion,
    _doc_to_dict,
    _dict_to_doc,
    _get_recall_cache_key,
    _index_identity,
    _get_cached_recall,
    _set_cached_recall,
    clear_recall_cache,
)


class TestRRFFusion:
    def test_basic_fusion(self):
        r1 = {"doc_a": 1, "doc_b": 2}
        r2 = {"doc_b": 1, "doc_a": 2}
        scores = _rrf_fusion([r1, r2], k=60)
        assert "doc_a" in scores
        assert "doc_b" in scores
        assert scores["doc_a"] > 0
        assert scores["doc_b"] > 0

    def test_single_ranking(self):
        r = {"a": 1, "b": 2, "c": 3}
        scores = _rrf_fusion([r], k=60)
        assert scores["a"] > scores["b"] > scores["c"]

    def test_empty_rankings(self):
        scores = _rrf_fusion([], k=60)
        assert scores == {}

    def test_no_overlap(self):
        r1 = {"a": 1}
        r2 = {"b": 1}
        scores = _rrf_fusion([r1, r2], k=60)
        assert "a" in scores
        assert "b" in scores

    def test_identical_rankings(self):
        r = {"a": 1, "b": 2}
        scores = _rrf_fusion([r, r, r], k=60)
        # Same doc in all 3 rankings gets 3x the contribution
        assert scores["a"] > scores["b"]

    def test_custom_k(self):
        r = {"a": 1, "b": 2}
        scores_k10 = _rrf_fusion([r], k=10)
        scores_k60 = _rrf_fusion([r], k=60)
        # Different k values produce different scores
        assert scores_k10["a"] != scores_k60["a"]

    def test_empty_ranking_in_list(self):
        r1 = {"a": 1, "b": 2}
        r2 = {}
        scores = _rrf_fusion([r1, r2], k=60)
        assert scores["a"] > 0
        assert scores["b"] > 0


class TestRecallCache:
    def setup_method(self):
        clear_recall_cache()

    def teardown_method(self):
        clear_recall_cache()

    def test_cache_key_different_queries(self):
        k1 = _get_recall_cache_key("query a", 8, 24)
        k2 = _get_recall_cache_key("query b", 8, 24)
        assert k1 != k2

    def test_cache_key_different_params(self):
        k1 = _get_recall_cache_key("query", 8, 24, 0.5)
        k2 = _get_recall_cache_key("query", 8, 24, 0.3)
        assert k1 != k2

    def test_cache_key_deterministic(self):
        k1 = _get_recall_cache_key("hello", 5, 10, 0.5)
        k2 = _get_recall_cache_key("hello", 5, 10, 0.5)
        assert k1 == k2

    def test_cache_key_includes_inner_top_k(self):
        k1 = _get_recall_cache_key("hello", 5, 10, 0.5, 20)
        k2 = _get_recall_cache_key("hello", 5, 10, 0.5, 40)
        assert k1 != k2

    def test_cache_miss(self):
        result = _get_cached_recall("nonexistent_key")
        assert result is None

    def test_cache_store_and_retrieve(self):
        key = "test_key"
        docs = [{"content": "hello", "score": 0.9}]
        _set_cached_recall(key, docs)
        result = _get_cached_recall(key)
        assert result == docs

    def test_cache_expiry(self, monkeypatch):
        import backend.rag.retrievers as retrievers
        monkeypatch.setattr(retrievers, "_RECALL_CACHE", LRUCache(max_size=10, ttl_seconds=0.01))
        key = "expire_key"
        _set_cached_recall(key, [{"content": "x"}])
        time.sleep(0.02)
        assert _get_cached_recall(key) is None


# ============================================================
# T25: 缓存键必须包含索引身份（换嵌入模型 / 重建索引后不能命中旧结果）
# ============================================================

class TestRecallCacheIdentity:
    """召回缓存键的索引身份维度。

    防的回归：缓存键只由 query + 检索参数组成时，切换嵌入模型或重建 FAISS 索引
    后仍会命中 5 小时 TTL 的旧结果 —— 用户看到的是与新索引无关的脏数据。
    """

    def setup_method(self):
        clear_recall_cache()

    def teardown_method(self):
        clear_recall_cache()

    def test_cache_key_differs_by_index_identity(self):
        """不同索引身份 → 不同缓存键；相同身份 → 相同键（可命中）。"""
        k1 = _get_recall_cache_key("银灰技能", 8, 24, 0.5, 20, index_identity="fp-A")
        k2 = _get_recall_cache_key("银灰技能", 8, 24, 0.5, 20, index_identity="fp-B")
        assert k1 != k2, "索引身份没参与缓存键：重建索引后仍会命中旧结果"

        same = _get_recall_cache_key("银灰技能", 8, 24, 0.5, 20, index_identity="fp-A")
        assert k1 == same

    def test_cache_key_differs_from_empty_identity(self):
        """空身份（未传/未实现时）与真实身份也必须区分，避免静默回退到「无身份」键。"""
        k_empty = _get_recall_cache_key("q", 8, 24, 0.5, 20)
        k_real = _get_recall_cache_key("q", 8, 24, 0.5, 20, index_identity="model=bge-m3")
        assert k_empty != k_real

    def test_index_identity_changes_when_index_file_rebuilt(self, tmp_path):
        """索引文件被重建（size/mtime 变化）→ 指纹变化 → 缓存身份变化。"""
        (tmp_path / "operators.index").write_bytes(b"x" * 16)
        (tmp_path / "operators_meta.pkl").write_bytes(b"m" * 8)
        before = _index_identity(None, str(tmp_path))

        # 模拟重建：文件内容/长度变化
        (tmp_path / "operators.index").write_bytes(b"y" * 64)
        (tmp_path / "operators_meta.pkl").write_bytes(b"n" * 32)
        after = _index_identity(None, str(tmp_path))

        assert before != after, "索引指纹没变：重建索引后召回缓存不会失效"
        assert "operators.index" in after

    def test_index_identity_changes_with_embedding_model(self):
        """换嵌入模型 → 身份变化（向量空间不同，旧结果不可复用）。"""

        class _Emb:
            def __init__(self, model):
                self.model = model

        a = _index_identity(_Emb("bge-m3"), "")
        b = _index_identity(_Emb("text-embedding-3"), "")
        assert a != b

    def test_index_identity_missing_files_is_stable(self, tmp_path):
        """索引文件不存在时身份仍可用且稳定（不抛异常，两级指纹一致）。"""
        first = _index_identity(None, str(tmp_path))
        second = _index_identity(None, str(tmp_path))
        assert first == second
        assert "missing" in first


class _StubRetriever(MultiChannelRetriever):
    """测试替身：把 embeddings 放宽为任意对象，避免构造真实嵌入客户端（不联网）。"""

    embeddings: Any = None
    bm25_indexes: Dict[str, Any] = {}


def _make_doc(content, chunk_id):
    from langchain_core.documents import Document
    return Document(page_content=content, metadata={"chunk_id": chunk_id})


class TestCachedRecallIsolation:
    """缓存条目是进程级共享状态：取出的 Document 必须是独立副本。

    防的回归：_dict_to_doc 直接复用缓存里的 metadata dict 时，调用方一次
    `doc.metadata["x"] = ...`（例如补 chunk_id / 写入 fused_score）就会污染缓存，
    后续所有请求都会拿到被改写过的元数据，且多线程下互相干扰。
    """

    def setup_method(self):
        clear_recall_cache()

    def teardown_method(self):
        clear_recall_cache()

    def test_dict_to_doc_copies_metadata(self):
        """_dict_to_doc 必须复制 metadata，返回的 Document 与缓存条目解耦。"""
        entry = {"page_content": "正文", "metadata": {"chunk_id": "c1"}}
        first = _dict_to_doc(entry)
        assert first.metadata is not entry["metadata"]     # 不是同一个 dict 对象
        first.metadata["chunk_id"] = "MUTATED"

        second = _dict_to_doc(entry)
        assert second.metadata["chunk_id"] == "c1"
        assert entry["metadata"]["chunk_id"] == "c1"       # 缓存条目本身未被污染

    def test_cache_hit_returns_independent_documents(self):
        """端到端：两次从缓存取出的 Document 互不影响。"""
        retriever = _StubRetriever(embeddings=None, bm25_indexes={}, faiss_index_dir="")
        query = "缓存命中查询"
        cache_key = _get_recall_cache_key(
            query, retriever.top_k_per_channel, retriever.final_top_k,
            retriever.vector_weight, retriever.inner_top_k,
            index_identity=_index_identity(retriever.embeddings, retriever.faiss_index_dir),
        )
        _set_cached_recall(cache_key, [
            _doc_to_dict(_make_doc("缓存正文", "cached_1")),
        ])

        first = retriever._get_relevant_documents(query, run_manager=None)
        assert [d.page_content for d in first] == ["缓存正文"]   # 确认走的是缓存路径

        first[0].metadata["chunk_id"] = "MUTATED"
        first[0].metadata["cross_collection_score"] = 999.0

        second = retriever._get_relevant_documents(query, run_manager=None)
        assert second[0].metadata["chunk_id"] == "cached_1"
        assert "cross_collection_score" not in second[0].metadata
        # 缓存内部条目也未被改写
        assert _get_cached_recall(cache_key)[0]["metadata"]["chunk_id"] == "cached_1"
