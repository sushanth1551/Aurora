"""Small in-memory TTL caches for search and map results.

Avoids repeating identical provider calls (each costs credits) within a
process. Entries are copied on the way in and out, failures are never cached,
and a TTL of 0 disables caching. Keys hold queries/URLs only in memory; nothing
is logged. This is a local optimisation, not a provider feature.
"""

import time
from collections import OrderedDict
from collections.abc import Callable
from functools import lru_cache
from typing import Any

from app.core import metrics
from app.schemas.research import SearchResult


class TTLCache[V]:
    """Bounded least-recently-used cache whose entries expire after ``ttl`` seconds."""

    def __init__(
        self,
        ttl: float,
        max_entries: int = 128,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.ttl = ttl
        self.max_entries = max(1, max_entries)
        self._clock = clock
        self._items: OrderedDict[Any, tuple[float, V]] = OrderedDict()
        self.hits = 0
        self.misses = 0

    @property
    def enabled(self) -> bool:
        return self.ttl > 0

    def get(self, key: Any) -> V | None:
        entry = self._items.get(key)
        if entry is None or entry[0] <= self._clock():
            self._items.pop(key, None)
            self.misses += 1
            return None
        self._items.move_to_end(key)
        self.hits += 1
        return entry[1]

    def set(self, key: Any, value: V) -> None:
        if not self.enabled:
            return
        self._items[key] = (self._clock() + self.ttl, value)
        self._items.move_to_end(key)
        while len(self._items) > self.max_entries:
            self._items.popitem(last=False)

    def clear(self) -> None:
        self._items.clear()

    def __len__(self) -> int:
        return len(self._items)


@lru_cache(maxsize=8)
def shared_cache(name: str, ttl: float, max_entries: int) -> TTLCache[Any]:
    """Process-wide cache for ``name`` (rebuilt only if its settings change)."""
    return TTLCache(ttl, max_entries)


class CachedSearch:
    """Search provider wrapper that serves repeated identical queries from cache."""

    def __init__(self, inner: Any, cache: TTLCache[list[SearchResult]]) -> None:
        self._inner, self._cache = inner, cache

    async def search(
        self,
        query: str,
        *,
        num_results: int = 10,
        country: str | None = None,
        language: str | None = None,
    ) -> list[SearchResult]:
        key = (query, num_results, country, language)
        cached = self._cache.get(key) if self._cache.enabled else None
        if cached is not None:
            metrics.count("cache_hit.search")
            return [r.model_copy(deep=True) for r in cached]
        found = await self._inner.search(
            query, num_results=num_results, country=country, language=language
        )
        self._cache.set(key, [r.model_copy(deep=True) for r in found])
        return found


class CachedMapper:
    """Map provider wrapper that serves repeated identical maps from cache."""

    def __init__(self, inner: Any, cache: TTLCache[list[Any]]) -> None:
        self._inner, self._cache = inner, cache

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    async def map_site(
        self,
        url: str,
        *,
        search: str | None = None,
        limit: int = 200,
        include_subdomains: bool = False,
    ) -> list[Any]:
        key = (url, search, limit, include_subdomains)
        cached = self._cache.get(key) if self._cache.enabled else None
        if cached is not None:
            metrics.count("cache_hit.map")
            return [link.model_copy(deep=True) for link in cached]
        links = await self._inner.map_site(
            url, search=search, limit=limit, include_subdomains=include_subdomains
        )
        self._cache.set(key, [link.model_copy(deep=True) for link in links])
        return links
