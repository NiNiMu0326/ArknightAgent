"""Tests for the shared LRU cache and the recall cache LRU behavior."""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import backend.rag.retrievers as retrievers
from backend.rag.cache import LRUCache
from backend.rag.retrievers import (
    _get_cached_recall,
    _set_cached_recall,
    clear_recall_cache,
)


class TestLRUCache:
    def test_basic_get_set(self):
        cache = LRUCache(max_size=5)
        cache.set("a", "value_a")
        assert cache.get("a") == "value_a"

    def test_missing_returns_none(self):
        cache = LRUCache(max_size=5)
        assert cache.get("missing") is None

    def test_contains(self):
        cache = LRUCache(max_size=5)
        cache.set("a", "v")
        assert "a" in cache
        assert "b" not in cache

    def test_len_and_clear(self):
        cache = LRUCache(max_size=5)
        assert len(cache) == 0
        cache.set("a", "1")
        cache.set("b", "2")
        assert len(cache) == 2
        cache.clear()
        assert len(cache) == 0
        assert cache.get("a") is None

    def test_set_existing_updates_value_and_recency(self):
        cache = LRUCache(max_size=2)
        cache.set("a", "old")
        cache.set("b", "b")
        # Updating a refreshes its recency; b becomes LRU.
        cache.set("a", "new")
        cache.set("c", "c")
        assert "b" not in cache
        assert cache.get("a") == "new"
        assert cache.get("c") == "c"


class TestLRUCacheEviction:
    def test_evicts_lru_not_fifo(self):
        cache = LRUCache(max_size=2)
        cache.set("a", "A")
        cache.set("b", "B")
        # Refresh a, so b is now the least-recently-used entry.
        assert cache.get("a") == "A"
        cache.set("c", "C")
        assert "b" not in cache
        assert cache.get("a") == "A"
        assert cache.get("c") == "C"


class TestLRUCacheTTL:
    def test_ttl_expiry(self):
        cache = LRUCache(max_size=5, ttl_seconds=0.01)
        cache.set("a", "v")
        time.sleep(0.02)
        assert cache.get("a") is None
        assert "a" not in cache

    def test_ttl_not_expired_within_window(self):
        cache = LRUCache(max_size=5, ttl_seconds=5)
        cache.set("a", "v")
        assert cache.get("a") == "v"
        assert "a" in cache

    def test_no_ttl_never_expires(self):
        cache = LRUCache(max_size=5)
        cache.set("a", "v")
        time.sleep(0.02)
        assert cache.get("a") == "v"
        assert "a" in cache


class TestSharedImplementation:
    def test_parent_document_uses_same_lru_class(self):
        from backend.rag import cache
        from backend.rag import parent_document

        assert parent_document.LRUCache is cache.LRUCache


class TestRecallCacheLRU:
    def test_recall_cache_evicts_lru(self, monkeypatch):
        monkeypatch.setattr(retrievers, "_RECALL_CACHE", LRUCache(max_size=2, ttl_seconds=18000))
        docs_a = [{"content": "a"}]
        docs_b = [{"content": "b"}]
        docs_c = [{"content": "c"}]

        _set_cached_recall("a", docs_a)
        _set_cached_recall("b", docs_b)
        # Refresh a; b becomes the least-recently-used entry.
        assert _get_cached_recall("a") == docs_a

        _set_cached_recall("c", docs_c)
        assert _get_cached_recall("b") is None
        assert _get_cached_recall("a") == docs_a
        assert _get_cached_recall("c") == docs_c

    def test_clear_recall_cache_works(self, monkeypatch):
        monkeypatch.setattr(retrievers, "_RECALL_CACHE", LRUCache(max_size=2))
        _set_cached_recall("a", [{"content": "a"}])
        _set_cached_recall("b", [{"content": "b"}])
        assert len(retrievers._RECALL_CACHE) == 2

        clear_recall_cache()

        assert len(retrievers._RECALL_CACHE) == 0
        assert _get_cached_recall("a") is None
        assert _get_cached_recall("b") is None
