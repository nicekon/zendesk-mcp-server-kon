"""Read-only Zendesk Community tools."""
from __future__ import annotations
from typing import Protocol
from ..contracts import ErrorCode, failure

class CommunityClient(Protocol):
    def get(self, path: str, *, params: dict[str, str] | None = None) -> dict[str, object]: ...

class CommunityTools:
    def __init__(self, client: CommunityClient | None) -> None: self._client = client
    def list_posts(self) -> dict[str, object]: return self._get("/api/v2/community/posts.json")
    def search_posts(self, query: str) -> dict[str, object]:
        if not isinstance(query, str) or not query.strip(): return failure(ErrorCode.VALIDATION_ERROR, "query must be a non-empty string")
        return self._get("/api/v2/community/posts/search.json", {"query": query.strip()})
    def get_post(self, post_id: int) -> dict[str, object]:
        if not isinstance(post_id, int) or isinstance(post_id, bool) or post_id < 1: return failure(ErrorCode.VALIDATION_ERROR, "post_id must be a positive integer")
        return self._get(f"/api/v2/community/posts/{post_id}.json")
    def _get(self, path: str, params: dict[str, str] | None = None) -> dict[str, object]:
        if self._client is None: return failure(ErrorCode.NOT_CONFIGURED, "Zendesk is not configured")
        return self._client.get(path, params=params)
