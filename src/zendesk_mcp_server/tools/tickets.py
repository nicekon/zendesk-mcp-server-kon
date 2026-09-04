"""Read-only Zendesk Support ticket tools."""

from __future__ import annotations

from typing import Protocol

from ..contracts import ErrorCode, failure, success


class TicketClient(Protocol):
    def get(self, path: str, *, params: dict[str, str] | None = None) -> dict[str, object]: ...


class TicketTools:
    def __init__(self, client: TicketClient | None) -> None:
        self._client = client

    def get_ticket(self, ticket_id: int) -> dict[str, object]:
        if not _valid_ticket_id(ticket_id):
            return failure(ErrorCode.VALIDATION_ERROR, "ticket_id must be a positive integer")
        client = self._configured_client()
        if isinstance(client, dict):
            return client
        return client.get(f"/api/v2/tickets/{ticket_id}.json")

    def list_tickets(self, limit: int = 100) -> dict[str, object]:
        page_size = _page_size(limit)
        if page_size is None:
            return failure(ErrorCode.VALIDATION_ERROR, "limit must be an integer")
        client = self._configured_client()
        if isinstance(client, dict):
            return client
        result = client.get("/api/v2/tickets.json", params={"page[size]": str(page_size)})
        if not result.get("ok"):
            return result
        data = result.get("data", {})
        if not isinstance(data, dict):
            return failure(ErrorCode.UPSTREAM_ERROR, "Zendesk returned an invalid ticket list")
        meta = data.get("meta", {})
        return {
            "ok": True,
            "items": data.get("tickets", []),
            "has_more": bool(meta.get("has_more", False)) if isinstance(meta, dict) else False,
            "next_cursor": meta.get("after_cursor") if isinstance(meta, dict) else None,
            "truncated": False,
        }

    def get_conversation(self, ticket_id: int) -> dict[str, object]:
        if not _valid_ticket_id(ticket_id):
            return failure(ErrorCode.VALIDATION_ERROR, "ticket_id must be a positive integer")
        client = self._configured_client()
        if isinstance(client, dict):
            return client
        result = client.get(f"/api/v2/tickets/{ticket_id}/comments.json")
        if not result.get("ok"):
            return result
        data = result.get("data", {})
        if not isinstance(data, dict):
            return failure(ErrorCode.UPSTREAM_ERROR, "Zendesk returned an invalid conversation")
        comments = data.get("comments", [])
        if not isinstance(comments, list):
            return failure(ErrorCode.UPSTREAM_ERROR, "Zendesk returned invalid comments")
        marked = [{**comment, "untrusted_user_content": True} for comment in comments if isinstance(comment, dict)]
        return success({"comments": marked})

    def search_tickets(self, query: str, limit: int = 100) -> dict[str, object]:
        ticket_query = _ticket_query(query)
        if ticket_query is None:
            return failure(ErrorCode.VALIDATION_ERROR, "query must be a non-empty string")
        page_size = _page_size(limit)
        if page_size is None:
            return failure(ErrorCode.VALIDATION_ERROR, "limit must be an integer")
        client = self._configured_client()
        if isinstance(client, dict):
            return client
        result = client.get(
            "/api/v2/search.json",
            params={"query": ticket_query, "page[size]": str(page_size)},
        )
        if not result.get("ok"):
            return result
        data = result.get("data", {})
        if not isinstance(data, dict) or not isinstance(data.get("results", []), list):
            return failure(ErrorCode.UPSTREAM_ERROR, "Zendesk returned an invalid ticket search")
        has_more = bool(data.get("next_page"))
        return {"ok": True, "items": data["results"], "has_more": has_more, "next_cursor": None, "truncated": has_more}

    def count_tickets(self, query: str) -> dict[str, object]:
        ticket_query = _ticket_query(query)
        if ticket_query is None:
            return failure(ErrorCode.VALIDATION_ERROR, "query must be a non-empty string")
        client = self._configured_client()
        if isinstance(client, dict):
            return client
        result = client.get("/api/v2/search/count.json", params={"query": ticket_query})
        if not result.get("ok"):
            return result
        data = result.get("data", {})
        count = data.get("count") if isinstance(data, dict) else None
        if not isinstance(count, dict) or not isinstance(count.get("value"), int):
            return failure(ErrorCode.UPSTREAM_ERROR, "Zendesk returned an invalid ticket count")
        return success({"count": count["value"], "refreshed_at": count.get("refreshed_at")})

    def _configured_client(self) -> TicketClient | dict[str, object]:
        if self._client is None:
            return failure(ErrorCode.NOT_CONFIGURED, "Zendesk is not configured")
        return self._client


def _valid_ticket_id(ticket_id: int) -> bool:
    return isinstance(ticket_id, int) and not isinstance(ticket_id, bool) and ticket_id > 0


def _ticket_query(query: str) -> str | None:
    if not isinstance(query, str) or not (cleaned := query.strip()):
        return None
    return f"type:ticket {cleaned}"


def _page_size(limit: int) -> int | None:
    if not isinstance(limit, int) or isinstance(limit, bool):
        return None
    return max(1, min(limit, 100))
