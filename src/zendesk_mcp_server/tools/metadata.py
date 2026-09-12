"""Read-only Zendesk account metadata tools."""

from __future__ import annotations

from typing import Protocol

from ..contracts import ErrorCode, failure
from ..pagination import collect_cursor, collect_offset


class MetadataClient(Protocol):
    def get(self, path: str, *, params: dict[str, str] | None = None) -> dict[str, object]: ...


class MetadataTools:
    def __init__(self, client: MetadataClient | None) -> None:
        self._client = client

    def search_users(self, query: str, limit: int = 100, *, cursor: str | None = None) -> dict[str, object]:
        if not isinstance(query, str) or not query.strip():
            return failure(ErrorCode.VALIDATION_ERROR, "query must be a non-empty string")
        return collect_offset(self._get, "/api/v2/users/search.json", "users", limit, cursor, filters={"query": query.strip()})

    def list_groups(self, limit: int = 100, *, cursor: str | None = None) -> dict[str, object]:
        return self._list_cursor("/api/v2/groups.json", "groups", limit, cursor)

    def _list_cursor(self, path: str, key: str, limit: int, cursor: str | None) -> dict[str, object]:
        return collect_cursor(self._get, path, key, limit, cursor)
    def list_group_users(self, group_id: int, limit: int = 100, *, cursor: str | None = None) -> dict[str, object]:
        if not isinstance(group_id, int) or isinstance(group_id, bool) or group_id < 1:
            return failure(ErrorCode.VALIDATION_ERROR, "group_id must be a positive integer")
        return self._list_cursor(f"/api/v2/groups/{group_id}/users.json", "users", limit, cursor)
    def get_organization(self, organization_id: int) -> dict[str, object]: return self._by_id("/api/v2/organizations/{id}.json", organization_id, "organization_id")
    def list_brands(self, limit: int = 100, *, cursor: str | None = None) -> dict[str, object]:
        return self._list_cursor("/api/v2/brands.json", "brands", limit, cursor)
    def list_ticket_fields(self, limit: int = 100, *, cursor: str | None = None) -> dict[str, object]:
        return self._list_cursor("/api/v2/ticket_fields.json", "ticket_fields", limit, cursor)
    def list_ticket_forms(self, limit: int = 100, *, cursor: str | None = None, active: bool | None = None) -> dict[str, object]:
        if active is not None and type(active) is not bool:
            return failure(ErrorCode.VALIDATION_ERROR, "active must be a boolean")
        filters = {} if active is None else {"active": str(active).lower()}
        return collect_cursor(self._get, "/api/v2/ticket_forms.json", "ticket_forms", limit, cursor, filters=filters)
    def list_custom_statuses(self, limit: int = 100, *, cursor: str | None = None) -> dict[str, object]:
        if type(limit) is not int or not 1 <= limit <= 1000 or (cursor is not None and (not isinstance(cursor, str) or not cursor.isascii() or not cursor.isdecimal() or len(cursor) > 4)):
            return failure(ErrorCode.VALIDATION_ERROR, "limit must be 1 to 1000 and cursor must be a non-negative list offset")
        result = self._get("/api/v2/custom_statuses.json")
        if not result.get("ok"): return result
        data = result.get("data")
        statuses = data.get("custom_statuses") if isinstance(data, dict) else None
        if not isinstance(statuses, list) or any(not isinstance(item, dict) for item in statuses):
            return failure(ErrorCode.UPSTREAM_ERROR, "Zendesk returned an invalid custom status list")
        offset = int(cursor) if cursor is not None else 0
        items = statuses[offset:offset + limit]
        end = offset + len(items)
        more = end < len(statuses)
        return {"ok": True, "items": items, "has_more": more, "next_cursor": str(end) if more else None, "truncated": more}
    def list_views(self, limit: int = 100, *, cursor: str | None = None) -> dict[str, object]:
        return self._list_cursor("/api/v2/views.json", "views", limit, cursor)
    def get_view(self, view_id: int) -> dict[str, object]: return self._by_id("/api/v2/views/{id}.json", view_id, "view_id")
    def list_view_tickets(self, view_id: int, limit: int = 100, *, cursor: str | None = None) -> dict[str, object]:
        if not isinstance(view_id, int) or isinstance(view_id, bool) or view_id < 1:
            return failure(ErrorCode.VALIDATION_ERROR, "view_id must be a positive integer")
        return self._list_cursor(f"/api/v2/views/{view_id}/tickets.json", "tickets", limit, cursor)
    def list_macros(self, limit: int = 100, *, cursor: str | None = None) -> dict[str, object]:
        return self._list_cursor("/api/v2/macros.json", "macros", limit, cursor)
    def list_triggers(self, limit: int = 100, *, cursor: str | None = None, active: bool | None = None, category_id: str | None = None, sort: str | None = None, sort_order: str | None = None, include_usage: bool = False) -> dict[str, object]:
        if type(include_usage) is not bool:
            return failure(ErrorCode.VALIDATION_ERROR, "include_usage must be a boolean")
        if active is not None and type(active) is not bool:
            return failure(ErrorCode.VALIDATION_ERROR, "active must be a boolean")
        if category_id is not None and (not isinstance(category_id, str) or not category_id.strip()):
            return failure(ErrorCode.VALIDATION_ERROR, "category_id must be a non-empty string")
        if sort is not None and sort not in ("alphabetical", "created_at", "updated_at", "position"):
            return failure(ErrorCode.VALIDATION_ERROR, "Unsupported cursor sort")
        if sort_order is not None and sort_order not in ("asc", "desc"):
            return failure(ErrorCode.VALIDATION_ERROR, "sort_order must be asc or desc")
        filters = {key: value for key, value in (("category_id", category_id), ("sort", sort), ("sort_order", sort_order)) if value is not None}
        if active is not None:
            filters["active"] = str(active).lower()
        if not include_usage:
            return collect_cursor(self._get, "/api/v2/triggers.json", "triggers", limit, cursor, filters=filters)
        filters["include"] = "usage_1h,usage_24h,usage_7d,usage_30d"
        sideloads = []
        def get_page(path, *, params=None):
            response = self._get(path, params=params)
            data = response.get("data")
            if response.get("ok") and isinstance(data, dict):
                extra = {key: value for key, value in data.items() if key not in ("triggers", "meta", "links", "count", "next_page", "previous_page")}
                if extra: sideloads.append(extra)
            return response
        result = collect_cursor(get_page, "/api/v2/triggers.json", "triggers", limit, cursor, filters=filters)
        return {**result, "sideloads": sideloads} if result.get("ok") and sideloads else result

    def _by_id(self, template: str, value: int, name: str) -> dict[str, object]:
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            return failure(ErrorCode.VALIDATION_ERROR, f"{name} must be a positive integer")
        return self._get(template.format(id=value))

    def _get(self, path: str, params: dict[str, str] | None = None) -> dict[str, object]:
        if self._client is None:
            return failure(ErrorCode.NOT_CONFIGURED, "Zendesk is not configured")
        return self._client.get(path, params=params)
