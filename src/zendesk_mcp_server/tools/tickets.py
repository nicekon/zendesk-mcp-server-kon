"""Read-only Zendesk Support ticket tools."""

from __future__ import annotations

from typing import Protocol

from ..config import Settings
from ..contracts import ErrorCode, failure, success
from ..write_policy import WriteRisk, check_write_permission


class TicketClient(Protocol):
    def get(self, path: str, *, params: dict[str, str] | None = None) -> dict[str, object]: ...


class TicketMutationClient(TicketClient, Protocol):
    def request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, object] | None = None,
    ) -> dict[str, object]: ...


class TicketTools:
    def __init__(self, client: TicketClient | None, settings: Settings | None = None) -> None:
        self._client = client
        self._settings = settings

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

    def create_ticket(
        self,
        *,
        requester_id: int,
        subject: str,
        description: str,
        tags: list[str] | None = None,
        priority: str | None = None,
        ticket_type: str | None = None,
    ) -> dict[str, object]:
        payload = _create_ticket_payload(
            requester_id=requester_id,
            subject=subject,
            description=description,
            tags=tags,
            priority=priority,
            ticket_type=ticket_type,
        )
        if isinstance(payload, dict) and "error" in payload:
            return payload
        permitted = self._write_permitted(WriteRisk.STANDARD)
        if permitted is not None:
            return permitted
        client = self._configured_mutation_client()
        if isinstance(client, dict):
            return client
        return _with_automation_notice(client.request("POST", "/api/v2/tickets.json", json_body={"ticket": payload}))

    def update_ticket(
        self,
        ticket_id: int,
        *,
        subject: str | None = None,
        status: str | None = None,
        priority: str | None = None,
        ticket_type: str | None = None,
        requester_id: int | None = None,
        assignee_id: int | None = None,
        group_id: int | None = None,
        organization_id: int | None = None,
        tags: list[str] | None = None,
    ) -> dict[str, object]:
        if not _valid_ticket_id(ticket_id):
            return failure(ErrorCode.VALIDATION_ERROR, "ticket_id must be a positive integer")
        payload = _update_ticket_payload(
            subject=subject,
            status=status,
            priority=priority,
            ticket_type=ticket_type,
            requester_id=requester_id,
            assignee_id=assignee_id,
            group_id=group_id,
            organization_id=organization_id,
            tags=tags,
        )
        if isinstance(payload, dict) and "error" in payload:
            return payload
        permitted = self._write_permitted(WriteRisk.STANDARD)
        if permitted is not None:
            return permitted
        client = self._configured_mutation_client()
        if isinstance(client, dict):
            return client
        return _with_automation_notice(client.request("PUT", f"/api/v2/tickets/{ticket_id}.json", json_body={"ticket": payload}))

    def set_ticket_status(self, ticket_id: int, status: str) -> dict[str, object]:
        return self.update_ticket(ticket_id, status=status)

    def assign_ticket(
        self,
        ticket_id: int,
        *,
        assignee_id: int | None = None,
        group_id: int | None = None,
    ) -> dict[str, object]:
        if assignee_id is None and group_id is None:
            return failure(ErrorCode.VALIDATION_ERROR, "assignee_id or group_id is required")
        return self.update_ticket(ticket_id, assignee_id=assignee_id, group_id=group_id)

    def add_ticket_tag(self, ticket_id: int, tag: str) -> dict[str, object]:
        tags = self._current_tags(ticket_id, tag)
        if isinstance(tags, dict):
            return tags
        if tag in tags:
            return success({"ticket_id": ticket_id, "tags": tags, "idempotent": True})
        return self.update_ticket(ticket_id, tags=[*tags, tag])

    def remove_ticket_tag(self, ticket_id: int, tag: str) -> dict[str, object]:
        tags = self._current_tags(ticket_id, tag)
        if isinstance(tags, dict):
            return tags
        if tag not in tags:
            return success({"ticket_id": ticket_id, "tags": tags, "idempotent": True})
        return self.update_ticket(ticket_id, tags=[value for value in tags if value != tag])

    def _configured_client(self) -> TicketClient | dict[str, object]:
        if self._client is None:
            return failure(ErrorCode.NOT_CONFIGURED, "Zendesk is not configured")
        return self._client

    def _configured_mutation_client(self) -> TicketMutationClient | dict[str, object]:
        client = self._configured_client()
        if isinstance(client, dict):
            return client
        if not hasattr(client, "request"):
            return failure(ErrorCode.NOT_CONFIGURED, "Zendesk write client is not configured")
        return client

    def _write_permitted(self, risk: WriteRisk) -> dict[str, object] | None:
        if self._settings is None:
            return failure(ErrorCode.WRITE_DISABLED, "Zendesk writes require configured write policy")
        return check_write_permission(self._settings, risk)

    def _current_tags(self, ticket_id: int, tag: str) -> list[str] | dict[str, object]:
        if not _valid_ticket_id(ticket_id):
            return failure(ErrorCode.VALIDATION_ERROR, "ticket_id must be a positive integer")
        if not _valid_tag(tag):
            return failure(ErrorCode.VALIDATION_ERROR, "tag must be a non-empty string without spaces")
        permitted = self._write_permitted(WriteRisk.STANDARD)
        if permitted is not None:
            return permitted
        result = self.get_ticket(ticket_id)
        if not result.get("ok"):
            return result
        data = result.get("data", {})
        ticket = data.get("ticket") if isinstance(data, dict) else None
        tags = ticket.get("tags") if isinstance(ticket, dict) else None
        if not isinstance(tags, list) or any(not isinstance(value, str) for value in tags):
            return failure(ErrorCode.UPSTREAM_ERROR, "Zendesk returned invalid ticket tags")
        return tags


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


def _create_ticket_payload(
    *,
    requester_id: int,
    subject: str,
    description: str,
    tags: list[str] | None,
    priority: str | None,
    ticket_type: str | None,
) -> dict[str, object]:
    if not _valid_ticket_id(requester_id):
        return failure(ErrorCode.VALIDATION_ERROR, "requester_id must be a positive integer")
    if not _valid_text(subject) or not _valid_text(description):
        return failure(ErrorCode.VALIDATION_ERROR, "subject and description must be non-empty strings")
    payload: dict[str, object] = {
        "requester_id": requester_id,
        "subject": subject.strip(),
        "comment": {"body": description.strip()},
    }
    if tags is not None:
        if not isinstance(tags, list) or any(not _valid_tag(tag) for tag in tags):
            return failure(ErrorCode.VALIDATION_ERROR, "tags must be non-empty strings without spaces")
        payload["tags"] = tags
    if priority is not None:
        if priority not in {"low", "normal", "high", "urgent"}:
            return failure(ErrorCode.VALIDATION_ERROR, "priority is invalid")
        payload["priority"] = priority
    if ticket_type is not None:
        if ticket_type not in {"question", "incident", "problem", "task"}:
            return failure(ErrorCode.VALIDATION_ERROR, "ticket_type is invalid")
        payload["type"] = ticket_type
    return payload


def _valid_text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _valid_tag(value: object) -> bool:
    return _valid_text(value) and " " not in value


def _with_automation_notice(result: dict[str, object]) -> dict[str, object]:
    data = result.get("data")
    if result.get("ok") and isinstance(data, dict):
        data["account_automation_side_effects_possible"] = True
    return result


def _update_ticket_payload(**values: object) -> dict[str, object]:
    payload: dict[str, object] = {}
    subject = values["subject"]
    if subject is not None:
        if not _valid_text(subject):
            return failure(ErrorCode.VALIDATION_ERROR, "subject must be a non-empty string")
        payload["subject"] = subject.strip()
    status = values["status"]
    if status is not None:
        if status not in {"new", "open", "pending", "hold", "solved", "closed"}:
            return failure(ErrorCode.VALIDATION_ERROR, "status is invalid")
        payload["status"] = status
    priority = values["priority"]
    if priority is not None:
        if priority not in {"low", "normal", "high", "urgent"}:
            return failure(ErrorCode.VALIDATION_ERROR, "priority is invalid")
        payload["priority"] = priority
    ticket_type = values["ticket_type"]
    if ticket_type is not None:
        if ticket_type not in {"question", "incident", "problem", "task"}:
            return failure(ErrorCode.VALIDATION_ERROR, "ticket_type is invalid")
        payload["type"] = ticket_type
    for name in ("requester_id", "assignee_id", "group_id", "organization_id"):
        value = values[name]
        if value is not None:
            if not _valid_ticket_id(value):
                return failure(ErrorCode.VALIDATION_ERROR, f"{name} must be a positive integer")
            payload[name] = value
    tags = values["tags"]
    if tags is not None:
        if not isinstance(tags, list) or any(not _valid_tag(tag) for tag in tags):
            return failure(ErrorCode.VALIDATION_ERROR, "tags must be non-empty strings without spaces")
        payload["tags"] = tags
    if not payload:
        return failure(ErrorCode.VALIDATION_ERROR, "at least one ticket field is required")
    return payload
