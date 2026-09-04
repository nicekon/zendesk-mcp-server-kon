"""Read-only Zendesk Support ticket tools."""

from __future__ import annotations

import os
import secrets
import stat
from pathlib import Path
from typing import Protocol

from ..approvals import ApprovalStore
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


class TicketAttachmentClient(TicketClient, Protocol):
    def download_attachment(self, content_url: str, *, max_bytes: int) -> dict[str, object]: ...


class TicketTools:
    def __init__(
        self,
        client: TicketClient | None,
        settings: Settings | None = None,
        approvals: ApprovalStore | None = None,
    ) -> None:
        self._client = client
        self._settings = settings
        self._approvals = approvals

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

    def list_attachments(self, ticket_id: int) -> dict[str, object]:
        conversation = self.get_conversation(ticket_id)
        if not conversation.get("ok"):
            return conversation
        data = conversation.get("data", {})
        comments = data.get("comments") if isinstance(data, dict) else None
        if not isinstance(comments, list):
            return failure(ErrorCode.UPSTREAM_ERROR, "Zendesk returned invalid comments")
        attachments: list[dict[str, object]] = []
        for comment in comments:
            if not isinstance(comment, dict) or not isinstance(comment.get("attachments", []), list):
                continue
            for attachment in comment["attachments"]:
                if isinstance(attachment, dict):
                    attachments.append({**attachment, "ticket_id": ticket_id, "comment_id": comment.get("id"), "untrusted_user_content": True})
        return success({"attachments": attachments})

    def download_attachment(self, ticket_id: int, attachment_id: int) -> dict[str, object]:
        if not _valid_ticket_id(ticket_id) or not _valid_ticket_id(attachment_id):
            return failure(ErrorCode.VALIDATION_ERROR, "ticket_id and attachment_id must be positive integers")
        listed = self.list_attachments(ticket_id)
        if not listed.get("ok"):
            return listed
        data = listed.get("data")
        attachments = data.get("attachments") if isinstance(data, dict) else None
        attachment = next((value for value in attachments if isinstance(value, dict) and value.get("id") == attachment_id), None) if isinstance(attachments, list) else None
        if attachment is None:
            return failure(ErrorCode.NOT_FOUND, "attachment does not belong to this ticket")
        if not self.attachment_is_safe_to_download(attachment):
            return failure(ErrorCode.PERMISSION_DENIED, "attachment is deleted, unscanned, unsafe, or exceeds the size limit")
        content_url = attachment.get("content_url")
        client = self._configured_client()
        if not isinstance(content_url, str) or not hasattr(client, "download_attachment"):
            return failure(ErrorCode.NOT_CONFIGURED, "Zendesk attachment download client is not configured")
        downloaded = client.download_attachment(content_url, max_bytes=20 * 1024 * 1024)
        if not downloaded.get("ok"):
            return downloaded
        response_data = downloaded.get("data")
        content = response_data.get("content") if isinstance(response_data, dict) else None
        if not isinstance(content, bytes) or self._settings is None or self._settings.attachment_cache_root is None:
            return failure(ErrorCode.UPSTREAM_ERROR, "Zendesk returned an invalid attachment download")
        cached = _cache_attachment(self._settings.attachment_cache_root, attachment_id, content)
        if not cached.get("ok"):
            return cached
        return success({"ticket_id": ticket_id, "attachment_id": attachment_id, "cache_path": cached["data"]["cache_path"], "cache_hit": cached["data"]["cache_hit"], "content_type": response_data.get("content_type")})

    def ticket_to_issue_context(self, ticket_id: int) -> dict[str, object]:
        ticket = self.get_ticket(ticket_id)
        conversation = self.get_conversation(ticket_id)
        if not ticket.get("ok"):
            return ticket
        if not conversation.get("ok"):
            return conversation
        ticket_data = ticket.get("data", {})
        item = ticket_data.get("ticket") if isinstance(ticket_data, dict) else None
        comments_data = conversation.get("data", {})
        comments = comments_data.get("comments", []) if isinstance(comments_data, dict) else []
        if not isinstance(item, dict) or not isinstance(comments, list):
            return failure(ErrorCode.UPSTREAM_ERROR, "Zendesk returned invalid ticket context")
        subject = item.get("subject") if isinstance(item.get("subject"), str) else "Untitled"
        description = item.get("description") if isinstance(item.get("description"), str) else ""
        lines = [f"# Ticket {ticket_id}: {subject}", "", description]
        for comment in comments:
            if isinstance(comment, dict) and isinstance(comment.get("body"), str): lines.extend(["", f"- {comment['body']}"])
        return success({"markdown": "\n".join(lines).strip()})

    @staticmethod
    def attachment_is_safe_to_download(attachment: dict[str, object]) -> bool:
        return (
            attachment.get("deleted") is not True
            and attachment.get("malware_scan_result") == "malware_not_found"
            and isinstance(attachment.get("size"), int)
            and 0 <= attachment["size"] <= 20 * 1024 * 1024
        )

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

    def export_tickets(self, query: str) -> dict[str, object]:
        if not isinstance(query, str) or not query.strip():
            return failure(ErrorCode.VALIDATION_ERROR, "query must be a non-empty string")
        client = self._configured_client()
        if isinstance(client, dict): return client
        return client.get("/api/v2/search/export.json", params={"filter[type]": "ticket", "query": query.strip()})

    def apply_macro(
        self,
        ticket_id: int,
        macro_id: int,
        *,
        execution_mode: str = "preview",
        approval_request_id: str | None = None,
        approval_token: str | None = None,
    ) -> dict[str, object]:
        if not _valid_ticket_id(ticket_id) or not _valid_ticket_id(macro_id):
            return failure(ErrorCode.VALIDATION_ERROR, "ticket_id and macro_id must be positive integers")
        preview = self._macro_changes(ticket_id, macro_id)
        if not preview.get("ok"):
            return preview
        data = preview["data"]
        ticket = data["ticket"]
        payload = {"ticket_id": ticket_id, "macro_id": macro_id, "ticket": ticket}
        public = isinstance(ticket.get("comment"), dict) and ticket["comment"].get("public") is True
        if execution_mode == "preview":
            if self._approvals is None:
                return failure(ErrorCode.NOT_CONFIGURED, "Zendesk approval store is not configured")
            return success({"approval_request_id": self._approvals.create("zendesk_apply_ticket_macro", payload), "execution_mode": "preview", "standard": True, "public": public, "ticket": ticket, "outbound_write": False})
        if execution_mode != "apply":
            return failure(ErrorCode.VALIDATION_ERROR, "execution_mode must be preview or apply")
        for risk in (WriteRisk.STANDARD, WriteRisk.PUBLIC) if public else (WriteRisk.STANDARD,):
            if (blocked := self._write_permitted(risk)) is not None:
                return blocked
        if self._approvals is None or not isinstance(approval_request_id, str) or not isinstance(approval_token, str) or not self._approvals.consume(approval_request_id, "zendesk_apply_ticket_macro", payload, approval_token):
            return failure(ErrorCode.APPROVAL_REQUIRED, "a matching local approval is required")
        client = self._configured_mutation_client()
        if isinstance(client, dict):
            return client
        return _with_automation_notice(client.request("PUT", f"/api/v2/tickets/{ticket_id}.json", json_body={"ticket": ticket}))

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

    def post_internal_note(self, ticket_id: int, body: str) -> dict[str, object]:
        payload = _comment_payload(ticket_id, body, public=False)
        if isinstance(payload, dict) and "error" in payload:
            return payload
        permitted = self._write_permitted(WriteRisk.STANDARD)
        if permitted is not None:
            return permitted
        client = self._configured_mutation_client()
        if isinstance(client, dict):
            return client
        return _with_automation_notice(client.request("PUT", f"/api/v2/tickets/{ticket_id}.json", json_body=payload))

    def post_public_reply(
        self,
        ticket_id: int,
        body: str,
        *,
        execution_mode: str = "preview",
        approval_request_id: str | None = None,
        approval_token: str | None = None,
    ) -> dict[str, object]:
        payload = _comment_payload(ticket_id, body, public=True)
        if isinstance(payload, dict) and "error" in payload:
            return payload
        if execution_mode == "preview":
            if self._approvals is None:
                return failure(ErrorCode.NOT_CONFIGURED, "Zendesk approval store is not configured")
            request_id = self._approvals.create("zendesk_post_public_reply", payload)
            return success(
                {
                    "approval_request_id": request_id,
                    "execution_mode": "preview",
                    "public": True,
                    "outbound_write": False,
                }
            )
        if execution_mode != "apply":
            return failure(ErrorCode.VALIDATION_ERROR, "execution_mode must be preview or apply")
        permitted = self._write_permitted(WriteRisk.PUBLIC)
        if permitted is not None:
            return permitted
        if (
            self._approvals is None
            or not isinstance(approval_request_id, str)
            or not isinstance(approval_token, str)
            or not self._approvals.consume(
                approval_request_id,
                "zendesk_post_public_reply",
                payload,
                approval_token,
            )
        ):
            return failure(ErrorCode.APPROVAL_REQUIRED, "a matching local approval is required")
        client = self._configured_mutation_client()
        if isinstance(client, dict):
            return client
        return _with_automation_notice(client.request("PUT", f"/api/v2/tickets/{ticket_id}.json", json_body=payload))

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

    def _macro_changes(self, ticket_id: int, macro_id: int) -> dict[str, object]:
        client = self._configured_client()
        if isinstance(client, dict):
            return client
        result = client.get(f"/api/v2/tickets/{ticket_id}/macros/{macro_id}/apply.json")
        if not result.get("ok"):
            return result
        data = result.get("data")
        changes = data.get("result") if isinstance(data, dict) else None
        ticket = changes.get("ticket") if isinstance(changes, dict) else None
        if not isinstance(ticket, dict):
            return failure(ErrorCode.UPSTREAM_ERROR, "Zendesk returned an invalid macro preview")
        return success({"ticket": ticket})


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


def _comment_payload(ticket_id: int, body: str, *, public: bool) -> dict[str, object]:
    if not _valid_ticket_id(ticket_id):
        return failure(ErrorCode.VALIDATION_ERROR, "ticket_id must be a positive integer")
    if not _valid_text(body):
        return failure(ErrorCode.VALIDATION_ERROR, "body must be a non-empty string")
    return {"ticket": {"comment": {"body": body.strip(), "public": public}}}


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


def _cache_attachment(root: Path, attachment_id: int, content: bytes) -> dict[str, object]:
    try:
        for directory in (root, root / str(os.getuid()), root / str(os.getuid()) / str(attachment_id)):
            directory.mkdir(mode=0o700, exist_ok=True)
            info = directory.lstat()
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
                return failure(ErrorCode.VALIDATION_ERROR, "attachment cache path is unsafe")
            os.chmod(directory, 0o700)
        destination = root / str(os.getuid()) / str(attachment_id) / "attachment"
        if destination.exists():
            info = destination.lstat()
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
                return failure(ErrorCode.VALIDATION_ERROR, "attachment cache file is unsafe")
            return success({"cache_path": str(destination), "cache_hit": True})
        temporary = destination.with_name(f".{secrets.token_hex(16)}.tmp")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            try:
                os.link(temporary, destination)
            except FileExistsError:
                return success({"cache_path": str(destination), "cache_hit": True})
            return success({"cache_path": str(destination), "cache_hit": False})
        finally:
            temporary.unlink(missing_ok=True)
    except OSError:
        return failure(ErrorCode.UPSTREAM_ERROR, "attachment cache could not be written")
