"""Read-only Zendesk Support ticket tools."""

from __future__ import annotations

import os
import secrets
import stat
import subprocess
import sys
import tarfile
import time
import zipfile
import json
from datetime import datetime
from pathlib import Path
from typing import Mapping, Protocol

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
        _clean_attachment_cache(self._settings.attachment_cache_root)
        cached = _cache_attachment(self._settings.attachment_cache_root, attachment_id, content)
        if not cached.get("ok"):
            return cached
        return success({"ticket_id": ticket_id, "attachment_id": attachment_id, "cache_path": cached["data"]["cache_path"], "cache_hit": cached["data"]["cache_hit"], "content_type": response_data.get("content_type")})

    def inspect_attachment(self, ticket_id: int, attachment_id: int) -> dict[str, object]:
        downloaded = self.download_attachment(ticket_id, attachment_id)
        if not downloaded.get("ok"):
            return downloaded
        data = downloaded["data"]; path = data["cache_path"]; content_type = data.get("content_type")
        if isinstance(content_type, str) and content_type.startswith("text/"):
            inspected = _inspect_text(Path(path))
        elif content_type == "application/pdf":
            inspected = _inspect_pdf(Path(path))
        elif content_type in {"image/png", "image/jpeg", "image/gif"}:
            inspected = _inspect_image(Path(path), content_type)
        elif _is_archive(content_type):
            inspected = _inspect_archive(Path(path))
        else:
            return failure(ErrorCode.UNSUPPORTED, "attachment inspection supports text, PDF, image, and archive files only")
        if not inspected.get("ok"):
            return inspected
        return success({"ticket_id": ticket_id, "attachment_id": attachment_id, **inspected["data"]})

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

    def search_tickets(self, query: object, limit: int = 100, *, projection: Mapping[str, object] | None = None) -> dict[str, object]:
        if projection is not None and (set(projection) != {"include_custom_objects"} or not isinstance(projection.get("include_custom_objects"), list) or not projection["include_custom_objects"] or not all(isinstance(value, str) and value for value in projection["include_custom_objects"])):
            return failure(ErrorCode.VALIDATION_ERROR, "projection must contain include_custom_objects string array")
        if projection is not None and (self._settings is None or not self._settings.has_capability("custom_objects")):
            return failure(ErrorCode.UNSUPPORTED, "custom object projection is not enabled")
        ticket_query = self._resolve_ticket_query(query)
        if isinstance(ticket_query, dict): return ticket_query
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
        items = data["results"]
        if projection is not None:
            projected = self._project_custom_objects(items, projection["include_custom_objects"])
            if isinstance(projected, dict): return projected
            items = projected
        return {"ok": True, "items": items, "has_more": has_more, "next_cursor": None, "truncated": has_more}

    def count_tickets(self, query: object) -> dict[str, object]:
        ticket_query = self._resolve_ticket_query(query)
        if isinstance(ticket_query, dict): return ticket_query
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

    def export_tickets(self, query: object, *, cursor: str | None = None, limit: int = 100) -> dict[str, object]:
        ticket_query = self._resolve_ticket_query(query, include_type=False)
        if isinstance(ticket_query, dict): return ticket_query
        if ticket_query is None:
            return failure(ErrorCode.VALIDATION_ERROR, "query must be a non-empty string")
        if cursor is not None and (not isinstance(cursor, str) or not cursor): return failure(ErrorCode.VALIDATION_ERROR, "cursor must be a non-empty string")
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 1000: return failure(ErrorCode.VALIDATION_ERROR, "limit must be an integer from 1 to 1000")
        client = self._configured_client()
        if isinstance(client, dict): return client
        params = {"filter[type]": "ticket", "query": ticket_query, "page[size]": str(limit)}
        if cursor is not None: params["page[after]"] = cursor
        result = client.get("/api/v2/search/export.json", params=params)
        if not result.get("ok"): return result
        data = result.get("data"); items = data.get("results") if isinstance(data, dict) else None; meta = data.get("meta") if isinstance(data, dict) else None
        if not isinstance(items, list) or not isinstance(meta, dict): return failure(ErrorCode.UPSTREAM_ERROR, "Zendesk returned an invalid ticket export page")
        return success({"items": items, "has_more": bool(meta.get("has_more")), "next_cursor": meta.get("after_cursor") if isinstance(meta.get("after_cursor"), str) else None, "truncated": False})

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
        assignee_id: int | None = None,
        group_id: int | None = None,
        organization_id: int | None = None,
        custom_fields: list[dict[str, object]] | None = None,
    ) -> dict[str, object]:
        payload = _create_ticket_payload(
            requester_id=requester_id,
            subject=subject,
            description=description,
            tags=tags,
            priority=priority,
            ticket_type=ticket_type,
            assignee_id=assignee_id,
            group_id=group_id,
            organization_id=organization_id,
            custom_fields=custom_fields,
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
        custom_status_id: int | None = None,
        due_at: str | None = None,
        custom_fields: list[dict[str, object]] | None = None,
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
            custom_status_id=custom_status_id,
            due_at=due_at,
            custom_fields=custom_fields,
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

    def _project_custom_objects(self, items: list[object], keys: list[object]) -> list[dict[str, object]] | dict[str, object]:
        client = self._configured_client()
        if isinstance(client, dict): return client
        fields_result = client.get("/api/v2/ticket_fields.json")
        if not fields_result.get("ok"): return fields_result
        field_data = fields_result.get("data"); fields = field_data.get("ticket_fields") if isinstance(field_data, dict) else None
        lookup = {key: field["id"] for key in keys for field in fields if isinstance(field, dict) and field.get("relationship_target_type") == f"zen:custom_object:{key}" and _valid_ticket_id(field.get("id"))} if isinstance(fields, list) else {}
        if set(lookup) != set(keys): return failure(ErrorCode.VALIDATION_ERROR, "requested custom object key has no ticket lookup field")
        projected: list[dict[str, object]] = []
        for item in items:
            if not isinstance(item, dict): continue
            values = {field.get("id"): field.get("value") for field in item.get("custom_fields", []) if isinstance(field, dict)}
            objects: dict[str, list[object]] = {}
            for key, field_id in lookup.items():
                record_id = values.get(field_id)
                if record_id is None: objects[key] = []; continue
                record = client.get(f"/api/v2/custom_objects/{key}/records/{record_id}.json")
                if not record.get("ok"): return record
                payload = record.get("data"); value = payload.get("custom_object_record") if isinstance(payload, dict) else None
                if not isinstance(value, dict): return failure(ErrorCode.UPSTREAM_ERROR, "Zendesk returned an invalid custom object record")
                objects[key] = [value]
            projected.append({**item, "custom_objects": objects})
        return projected

    def _resolve_ticket_query(self, query: object, *, include_type: bool = True) -> str | dict[str, object] | None:
        if not isinstance(query, Mapping): return _ticket_query(query, include_type=include_type)
        resolved = dict(query)
        for field in ("assignee", "requester"):
            reference = resolved.get(field)
            if not isinstance(reference, Mapping) or reference.get("kind") not in {"name", "email", "phone"}:
                continue
            kind, value = reference.get("kind"), reference.get("value")
            if not isinstance(value, str) or not value.strip(): return None
            client = self._configured_client()
            if isinstance(client, dict): return client
            result = client.get("/api/v2/users/search.json", params={"query": value.strip()})
            if not result.get("ok"): return result
            data = result.get("data"); users = data.get("users") if isinstance(data, dict) else None
            matches = [user for user in users if isinstance(user, dict) and user.get(kind) == value and _valid_ticket_id(user.get("id"))] if isinstance(users, list) else []
            if len(matches) != 1: return failure(ErrorCode.VALIDATION_ERROR, f"{field} {kind} must match exactly one user", details={"candidate_ids": [user["id"] for user in matches]})
            resolved[field] = {"kind": "id", "value": matches[0]["id"]}
        organization = resolved.get("organization")
        if isinstance(organization, Mapping) and organization.get("kind") == "name":
            value = organization.get("value")
            if not isinstance(value, str) or not value.strip(): return None
            client = self._configured_client()
            if isinstance(client, dict): return client
            result = client.get("/api/v2/organizations/search.json", params={"name": value.strip()})
            if not result.get("ok"): return result
            data = result.get("data"); organizations = data.get("organizations") if isinstance(data, dict) else None
            matches = [item for item in organizations if isinstance(item, dict) and isinstance(item.get("name"), str) and item["name"].casefold() == value.casefold() and _valid_ticket_id(item.get("id"))] if isinstance(organizations, list) else []
            if len(matches) != 1: return failure(ErrorCode.VALIDATION_ERROR, "organization name must match exactly one organization", details={"candidate_ids": [item["id"] for item in matches]})
            resolved["organization"] = {"kind": "id", "value": matches[0]["id"]}
        return _ticket_query(resolved, include_type=include_type)

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


def _ticket_query(query: object, *, include_type: bool = True) -> str | None:
    if isinstance(query, str):
        if not (cleaned := query.strip()): return None
        return f"type:ticket {cleaned}" if include_type else cleaned
    if not isinstance(query, Mapping) or not set(query) <= {"status", "priority", "tags", "assignee", "requester", "organization", "brand", "group", "form"}: return None
    fragments: list[str] = []
    for key, allowed in (("status", {"new", "open", "pending", "hold", "solved", "closed"}), ("priority", {"low", "normal", "high", "urgent"})):
        value = query.get(key)
        if value is not None:
            if not isinstance(value, str) or value not in allowed: return None
            fragments.append(f"{key}:{value}")
    tags = query.get("tags")
    if tags is not None:
        if not isinstance(tags, Mapping) or not set(tags) <= {"include", "exclude"}: return None
        for prefix, name in (("", "include"), ("-", "exclude")):
            values = tags.get(name, [])
            if not isinstance(values, list) or not all(_valid_tag(value) for value in values): return None
            fragments.extend(f"{prefix}tags:{value}" for value in values)
    for field in ("assignee", "requester"):
        value = query.get(field)
        if value is not None:
            fragment = _ticket_user_reference(field, value)
            if fragment is None: return None
            fragments.append(fragment)
    organization = query.get("organization")
    if organization is not None:
        if not isinstance(organization, Mapping) or not isinstance(organization.get("kind"), str): return None
        if organization.get("kind") == "none" and set(organization) == {"kind"}: fragments.append("organization:none")
        elif organization.get("kind") == "id" and set(organization) == {"kind", "value"} and _valid_ticket_id(organization.get("value")): fragments.append(f"organization:{organization['value']}")
        else: return None
    for field in ("brand", "group", "form"):
        value = query.get(field)
        if value is not None:
            if not isinstance(value, Mapping) or value.get("kind") != "id" or set(value) != {"kind", "value"} or not _valid_ticket_id(value.get("value")): return None
            fragments.append(f"{field}:{value['value']}")
    if not fragments: return None
    return " ".join((["type:ticket"] if include_type else []) + fragments)


def _ticket_user_reference(field: str, value: object) -> str | None:
    if not isinstance(value, Mapping) or not isinstance(value.get("kind"), str): return None
    kind = value["kind"]
    if kind in {"me", "none"} and set(value) == {"kind"}: return f"{field}:{kind}"
    if kind == "id" and set(value) == {"kind", "value"} and _valid_ticket_id(value.get("value")): return f"{field}:{value['value']}"
    return None


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
    assignee_id: int | None,
    group_id: int | None,
    organization_id: int | None,
    custom_fields: list[dict[str, object]] | None,
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
    for name, value in (("assignee_id", assignee_id), ("group_id", group_id), ("organization_id", organization_id)):
        if value is not None:
            if not _valid_ticket_id(value): return failure(ErrorCode.VALIDATION_ERROR, f"{name} must be a positive integer")
            payload[name] = value
    if custom_fields is not None:
        if not _valid_custom_fields(custom_fields): return failure(ErrorCode.VALIDATION_ERROR, "custom_fields must contain positive IDs and scalar or string-list values")
        payload["custom_fields"] = custom_fields
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
    custom_status_id = values["custom_status_id"]
    if custom_status_id is not None:
        if not _valid_ticket_id(custom_status_id):
            return failure(ErrorCode.VALIDATION_ERROR, "custom_status_id must be a positive integer")
        payload["custom_status_id"] = custom_status_id
    due_at = values["due_at"]
    if due_at is not None:
        if not _valid_due_at(due_at):
            return failure(ErrorCode.VALIDATION_ERROR, "due_at must be an ISO-8601 timestamp with timezone")
        payload["due_at"] = due_at
    tags = values["tags"]
    if tags is not None:
        if not isinstance(tags, list) or any(not _valid_tag(tag) for tag in tags):
            return failure(ErrorCode.VALIDATION_ERROR, "tags must be non-empty strings without spaces")
        payload["tags"] = tags
    custom_fields = values["custom_fields"]
    if custom_fields is not None:
        if not _valid_custom_fields(custom_fields):
            return failure(ErrorCode.VALIDATION_ERROR, "custom_fields must contain positive IDs and scalar or string-list values")
        payload["custom_fields"] = custom_fields
    if not payload:
        return failure(ErrorCode.VALIDATION_ERROR, "at least one ticket field is required")
    return payload


def _valid_due_at(value: object) -> bool:
    if not isinstance(value, str): return False
    try: return datetime.fromisoformat(value.replace("Z", "+00:00")).tzinfo is not None
    except ValueError: return False


def _valid_custom_fields(value: object) -> bool:
    return isinstance(value, list) and all(isinstance(field, dict) and _valid_ticket_id(field.get("id")) and set(field) == {"id", "value"} and (field["value"] is None or isinstance(field["value"], (str, int, float, bool)) or isinstance(field["value"], list) and all(isinstance(item, str) for item in field["value"])) for field in value)


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


def _clean_attachment_cache(root: Path) -> None:
    user_root = root / str(os.getuid())
    try:
        info = user_root.lstat()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode): return
        cutoff = time.time() - 24 * 60 * 60
        for directory in user_root.iterdir():
            info = directory.lstat()
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode): continue
            attachment = directory / "attachment"
            if not attachment.exists(): continue
            info = attachment.lstat()
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode) or info.st_mtime >= cutoff: continue
            attachment.unlink(); directory.rmdir()
    except OSError:
        return


def _safe_cache_open(path: Path):
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        stream = os.fdopen(descriptor, "rb")
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            stream.close(); return None
        return stream
    except OSError:
        return None


def _inspect_text(path: Path) -> dict[str, object]:
    stream = _safe_cache_open(path)
    if stream is None: return failure(ErrorCode.VALIDATION_ERROR, "attachment cache file is unsafe")
    with stream:
        content = stream.read(500 * 1024 + 1)
    return success({"kind": "text", "text": content[: 500 * 1024].decode("utf-8", errors="replace"), "truncated": len(content) > 500 * 1024})


_PDF_INSPECTOR = """
import json, resource, sys
soft, hard = resource.getrlimit(resource.RLIMIT_AS)
limits = [256 * 1024 * 1024]
if soft > 0: limits.append(soft)
if hard > 0: limits.append(hard)
limit = min(limits)
resource.setrlimit(resource.RLIMIT_AS, (limit, hard))
from pypdf import PdfReader
reader = PdfReader(sys.argv[1])
if len(reader.pages) > 100: raise ValueError('PDF exceeds 100 pages')
text = ''
truncated = False
for page in reader.pages:
    text += page.extract_text() or ''
    if len(text.encode('utf-8')) > 500 * 1024:
        text = text.encode('utf-8')[:500 * 1024].decode('utf-8', 'ignore'); truncated = True; break
print(json.dumps({'page_count': len(reader.pages), 'text': text, 'truncated': truncated}))
"""


def _inspect_pdf(path: Path) -> dict[str, object]:
    stream = _safe_cache_open(path)
    if stream is None: return failure(ErrorCode.VALIDATION_ERROR, "attachment cache file is unsafe")
    stream.close()
    try:
        result = subprocess.run([sys.executable, "-c", _PDF_INSPECTOR, str(path)], capture_output=True, text=True, timeout=10, check=False)
        data = json.loads(result.stdout) if result.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
        data = None
    if not isinstance(data, dict) or not isinstance(data.get("page_count"), int) or not isinstance(data.get("text"), str) or not isinstance(data.get("truncated"), bool): return failure(ErrorCode.UNSUPPORTED, "PDF inspection requires a bounded parser subprocess")
    return success({"kind": "pdf", **data})


_IMAGE_INSPECTOR = """
import base64, json, os, resource, sys
soft, hard = resource.getrlimit(resource.RLIMIT_AS)
limits = [256 * 1024 * 1024]
if soft > 0: limits.append(soft)
if hard > 0: limits.append(hard)
resource.setrlimit(resource.RLIMIT_AS, (min(limits), hard))
with os.fdopen(int(sys.argv[1]), 'rb', closefd=False) as stream:
    content = stream.read(20 * 1024 * 1024 + 1)
if len(content) > 20 * 1024 * 1024: raise ValueError('image exceeds size limit')
content_type = sys.argv[2]
if content_type == 'image/png' and content[:8] == b'\\x89PNG\\r\\n\\x1a\\n' and len(content) >= 24:
    width, height = int.from_bytes(content[16:20], 'big'), int.from_bytes(content[20:24], 'big')
elif content_type == 'image/gif' and content[:6] in {b'GIF87a', b'GIF89a'} and len(content) >= 10:
    width, height = int.from_bytes(content[6:8], 'little'), int.from_bytes(content[8:10], 'little')
elif content_type == 'image/jpeg' and content[:2] == b'\\xff\\xd8':
    index = 2; width = height = 0
    while index + 4 <= len(content):
        if content[index] != 0xff: index += 1; continue
        while index < len(content) and content[index] == 0xff: index += 1
        if index >= len(content): break
        marker = content[index]; index += 1
        if marker in {*range(0xd0, 0xd8), 0xd8, 0xd9}: continue
        if index + 2 > len(content): break
        length = int.from_bytes(content[index:index + 2], 'big')
        if length < 7 or index + length > len(content): break
        if marker in {0xc0, 0xc1, 0xc2, 0xc3, 0xc5, 0xc6, 0xc7, 0xc9, 0xca, 0xcb, 0xcd, 0xce, 0xcf}:
            height, width = int.from_bytes(content[index + 3:index + 5], 'big'), int.from_bytes(content[index + 5:index + 7], 'big'); break
        index += length
else:
    width = height = 0
if not 0 < width <= 16384 or not 0 < height <= 16384 or width * height > 40_000_000: raise ValueError('invalid image dimensions')
print(json.dumps({'width': width, 'height': height, 'image_data': base64.b64encode(content).decode('ascii')}))
"""


def _inspect_image(path: Path, content_type: str) -> dict[str, object]:
    stream = _safe_cache_open(path)
    if stream is None: return failure(ErrorCode.VALIDATION_ERROR, "attachment cache file is unsafe")
    with stream:
        try:
            result = subprocess.run([sys.executable, "-c", _IMAGE_INSPECTOR, str(stream.fileno()), content_type], capture_output=True, text=True, timeout=10, check=False, pass_fds=(stream.fileno(),))
            data = json.loads(result.stdout) if result.returncode == 0 else None
        except (OSError, subprocess.TimeoutExpired, ValueError, json.JSONDecodeError):
            data = None
    if not isinstance(data, dict) or not all(isinstance(data.get(key), int) for key in ("width", "height")) or not isinstance(data.get("image_data"), str): return failure(ErrorCode.UNSUPPORTED, "Image inspection requires a bounded parser subprocess")
    return success({"kind": "image", "mime_type": content_type, **data})


def _is_archive(content_type: object) -> bool:
    return isinstance(content_type, str) and content_type in {"application/zip", "application/x-zip-compressed", "application/x-tar", "application/gzip", "application/x-gzip"}


def _inspect_archive(path: Path) -> dict[str, object]:
    stream = _safe_cache_open(path)
    if stream is None: return failure(ErrorCode.VALIDATION_ERROR, "attachment cache file is unsafe")
    with stream:
        try:
            if zipfile.is_zipfile(stream):
                stream.seek(0)
                with zipfile.ZipFile(stream) as archive:
                    entries = [{"name": item.filename, "size": item.file_size, "directory": item.is_dir()} for item in archive.infolist()[:501]]
            else:
                stream.seek(0)
                with tarfile.open(fileobj=stream, mode="r|*") as archive:
                    entries = [{"name": item.name, "size": item.size, "directory": item.isdir()} for _, item in zip(range(501), archive)]
        except (tarfile.TarError, zipfile.BadZipFile, OSError):
            return failure(ErrorCode.VALIDATION_ERROR, "attachment archive is invalid")
    return success({"kind": "archive", "entries": entries[:500], "truncated": len(entries) > 500})
