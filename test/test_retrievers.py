"""
Tests for backend.rag.retrievers: RRF fusion + recall cache.
Usage: cd test && python -m pytest test_retrievers.py -v
"""
import sys
import time
import pytest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from backend.rag.cache import LRUCache
from backend.rag.retrievers import (
    _rrf_fusion,
    _get_recall_cache_key,
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
