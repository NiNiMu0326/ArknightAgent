"""Shared process-wide LRU cache helpers.

This module intentionally does not import any other backend.rag modules so it
can be safely reused by both ``parent_document.py`` and ``retrievers.py``.
"""

import time
from collections import OrderedDict
from typing import Any, Generic, Optional, TypeVar

T = TypeVar("T")


class LRUCache(Generic[T]):
    """A simple least-recently-used cache with optional TTL expiry.

    The cache is intentionally thread-safety-neutral: callers that share a
    cache across threads must guard access with their own lock. This keeps the
    implementation small and predictable, and matches the existing call sites in
    this project.

    Internally this uses :class:`collections.OrderedDict`:

    * ``get`` moves a hit to the most-recently-used end.
    * ``set`` moves an existing key to the most-recently-used end, or evicts the
      least-recently-used key before inserting when the cache is full.
    * When ``ttl_seconds`` is not ``None``, entries store ``{"value": value,
      "timestamp": time.time()}``; expired entries are lazily removed on access.
    * When ``ttl_seconds`` is ``None``, entries are stored as plain values and
      never expire.
    """

    def __init__(self, max_size: int = 100, ttl_seconds: Optional[float] = None) -> None:
        if max_size < 1:
            raise ValueError("max_size must be at least 1")
        self._cache: OrderedDict[str, Any] = OrderedDict()
        self._max_size = max_size
        self._ttl_seconds = ttl_seconds

    def _is_expired(self, key: str) -> bool:
        """Return whether the entry for ``key`` has passed its TTL."""
        if self._ttl_seconds is None:
            return False
        entry = self._cache[key]
        return time.time() - entry["timestamp"] > self._ttl_seconds

    def get(self, key: str) -> Optional[T]:
        """Return the cached value, or ``None`` on miss/expiry.

        A successful hit moves the key to the most-recently-used position.
        Expired entries are removed lazily and reported as a miss.
        """
        if key not in self._cache:
            return None

        if self._is_expired(key):
            del self._cache[key]
            return None

        self._cache.move_to_end(key)
        entry = self._cache[key]
        if self._ttl_seconds is not None:
            return entry["value"]
        return entry

    def set(self, key: str, value: T) -> None:
        """Insert or update ``key`` with ``value``.

        Updating an existing key refreshes its recency. Inserting a new key
        evicts the least-recently-used key first if the cache is at capacity.
        """
        if key in self._cache:
            self._cache.move_to_end(key)
        elif len(self._cache) >= self._max_size:
            self._cache.popitem(last=False)

        if self._ttl_seconds is not None:
            self._cache[key] = {"value": value, "timestamp": time.time()}
        else:
            self._cache[key] = value

    def __contains__(self, key: str) -> bool:
        """Return whether ``key`` is present and not expired.

        Expired entries are removed lazily during this check.
        """
        if key not in self._cache:
            return False
        if self._is_expired(key):
            del self._cache[key]
            return False
        return True

    def __len__(self) -> int:
        return len(self._cache)

    def clear(self) -> None:
        """Remove all cached entries."""
        self._cache.clear()

    def pop(self, key: str, default: Optional[T] = None) -> Optional[T]:
        """Remove and return the value for ``key``, or ``default`` if missing/expired."""
        if key not in self._cache:
            return default
        if self._is_expired(key):
            del self._cache[key]
            return default
        entry = self._cache.pop(key)
        if self._ttl_seconds is not None:
            return entry["value"]
        return entry
