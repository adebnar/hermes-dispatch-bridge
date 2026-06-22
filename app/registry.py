"""Registry of device push endpoints (UnifiedPush/ntfy URLs).

Endpoints are full publish URLs, so the bridge POSTs progress directly to each
registered device. Optionally backed by a :class:`StateStore` so registrations
survive a bridge restart (otherwise kept in memory only).
"""

from __future__ import annotations

from .store import StateStore

_KEY = "push_endpoints"


class EndpointRegistry:
    def __init__(self, store: StateStore | None = None) -> None:
        self._store = store
        self._endpoints: set[str] = set(store.get(_KEY, [])) if store is not None else set()

    def _persist(self) -> None:
        if self._store is not None:
            self._store.set(_KEY, sorted(self._endpoints))

    def add(self, endpoint: str) -> None:
        endpoint = (endpoint or "").strip()
        if endpoint and endpoint not in self._endpoints:
            self._endpoints.add(endpoint)
            self._persist()

    def remove(self, endpoint: str) -> None:
        endpoint = (endpoint or "").strip()
        if endpoint in self._endpoints:
            self._endpoints.discard(endpoint)
            self._persist()

    def all(self) -> list[str]:
        return sorted(self._endpoints)

    def __len__(self) -> int:
        return len(self._endpoints)
