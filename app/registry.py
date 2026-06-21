"""In-memory registry of device push endpoints (UnifiedPush/ntfy URLs).

Endpoints are full publish URLs, so the bridge POSTs progress directly to each
registered device. Kept in memory for simplicity — devices re-register on next
launch/pair after a bridge restart. (A persistent store is a future enhancement.)
"""

from __future__ import annotations


class EndpointRegistry:
    def __init__(self) -> None:
        self._endpoints: set[str] = set()

    def add(self, endpoint: str) -> None:
        endpoint = (endpoint or "").strip()
        if endpoint:
            self._endpoints.add(endpoint)

    def remove(self, endpoint: str) -> None:
        self._endpoints.discard((endpoint or "").strip())

    def all(self) -> list[str]:
        return sorted(self._endpoints)

    def __len__(self) -> int:
        return len(self._endpoints)
