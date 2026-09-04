"""Read-only Zendesk Guide and CSAT tools."""

from __future__ import annotations

from typing import Protocol

from ..contracts import ErrorCode, failure


class GuideClient(Protocol):
    def get(self, path: str, *, params: dict[str, str] | None = None) -> dict[str, object]: ...


class GuideTools:
    def __init__(self, client: GuideClient | None) -> None: self._client = client

    def list_categories(self) -> dict[str, object]: return self._get("/api/v2/help_center/categories.json")
    def list_sections(self) -> dict[str, object]: return self._get("/api/v2/help_center/sections.json")
    def get_satisfaction_ratings(self) -> dict[str, object]: return self._get("/api/v2/satisfaction_ratings.json")
    def list_permission_groups(self) -> dict[str, object]: return self._get("/api/v2/guide/permission_groups.json")
    def list_user_segments(self, *, built_in: bool | None = None, applicable: bool = False) -> dict[str, object]:
        if not isinstance(applicable, bool) or (built_in is not None and not isinstance(built_in, bool)): return failure(ErrorCode.VALIDATION_ERROR, "built_in and applicable must be booleans")
        return self._get("/api/v2/help_center/user_segments/applicable.json" if applicable else "/api/v2/help_center/user_segments.json", {"built_in": str(built_in).lower()} if built_in is not None else None)

    def search_articles(self, query: str) -> dict[str, object]:
        if not isinstance(query, str) or not query.strip():
            return failure(ErrorCode.VALIDATION_ERROR, "query must be a non-empty string")
        return self._get("/api/v2/help_center/articles/search.json", {"query": query.strip()})

    def get_article(self, article_id: int) -> dict[str, object]:
        if not isinstance(article_id, int) or isinstance(article_id, bool) or article_id < 1:
            return failure(ErrorCode.VALIDATION_ERROR, "article_id must be a positive integer")
        return self._get(f"/api/v2/help_center/articles/{article_id}.json")

    def _get(self, path: str, params: dict[str, str] | None = None) -> dict[str, object]:
        if self._client is None: return failure(ErrorCode.NOT_CONFIGURED, "Zendesk is not configured")
        return self._client.get(path, params=params)
