"""Read-only Zendesk account metadata tools."""

from __future__ import annotations

from typing import Protocol

from ..contracts import ErrorCode, failure


class MetadataClient(Protocol):
    def get(self, path: str, *, params: dict[str, str] | None = None) -> dict[str, object]: ...


class MetadataTools:
    def __init__(self, client: MetadataClient | None) -> None:
        self._client = client

    def search_users(self, query: str) -> dict[str, object]:
        if not isinstance(query, str) or not query.strip():
            return failure(ErrorCode.VALIDATION_ERROR, "query must be a non-empty string")
        return self._get("/api/v2/users/search.json", {"query": query.strip()})

    def list_groups(self) -> dict[str, object]: return self._get("/api/v2/groups.json")
    def list_group_users(self, group_id: int) -> dict[str, object]: return self._by_id("/api/v2/groups/{id}/users.json", group_id, "group_id")
    def get_organization(self, organization_id: int) -> dict[str, object]: return self._by_id("/api/v2/organizations/{id}.json", organization_id, "organization_id")
    def list_brands(self) -> dict[str, object]: return self._get("/api/v2/brands.json")
    def list_ticket_fields(self) -> dict[str, object]: return self._get("/api/v2/ticket_fields.json")
    def list_ticket_forms(self) -> dict[str, object]: return self._get("/api/v2/ticket_forms.json")
    def list_custom_statuses(self) -> dict[str, object]: return self._get("/api/v2/custom_statuses.json")

    def _by_id(self, template: str, value: int, name: str) -> dict[str, object]:
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            return failure(ErrorCode.VALIDATION_ERROR, f"{name} must be a positive integer")
        return self._get(template.format(id=value))

    def _get(self, path: str, params: dict[str, str] | None = None) -> dict[str, object]:
        if self._client is None:
            return failure(ErrorCode.NOT_CONFIGURED, "Zendesk is not configured")
        return self._client.get(path, params=params)
