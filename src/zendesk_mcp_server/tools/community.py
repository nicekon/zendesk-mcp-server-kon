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
    def list_comments(self, post_id: int) -> dict[str, object]: return self._by_id("/api/v2/community/posts/{id}/comments.json", post_id, "post_id")
    def get_comment(self, comment_id: int) -> dict[str, object]: return self._by_id("/api/v2/community/comments/{id}.json", comment_id, "comment_id")
    def list_topics(self) -> dict[str, object]: return self._get("/api/v2/community/topics.json")
    def get_topic(self, topic_id: int) -> dict[str, object]: return self._by_id("/api/v2/community/topics/{id}.json", topic_id, "topic_id")
    def list_votes(self, post_id: int) -> dict[str, object]: return self._by_id("/api/v2/community/posts/{id}/votes.json", post_id, "post_id")
    def _by_id(self, template: str, value: int, name: str) -> dict[str, object]:
        if not isinstance(value, int) or isinstance(value, bool) or value < 1: return failure(ErrorCode.VALIDATION_ERROR, f"{name} must be a positive integer")
        return self._get(template.format(id=value))
    def _get(self, path: str, params: dict[str, str] | None = None) -> dict[str, object]:
        if self._client is None: return failure(ErrorCode.NOT_CONFIGURED, "Zendesk is not configured")
        return self._client.get(path, params=params)
