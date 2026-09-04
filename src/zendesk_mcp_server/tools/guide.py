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
