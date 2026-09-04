"""Read-only Zendesk Community tools."""
from __future__ import annotations
from typing import Protocol
from ..approvals import ApprovalStore
from ..config import Settings
from ..contracts import ErrorCode, failure
from ..contracts import success
from ..write_policy import WriteRisk, check_write_permission

class CommunityClient(Protocol):
    def get(self, path: str, *, params: dict[str, str] | None = None) -> dict[str, object]: ...
    def request(self, method: str, path: str, *, json_body: dict[str, object] | None = None) -> dict[str, object]: ...

class CommunityTools:
    def __init__(self, client: CommunityClient | None, settings: Settings | None = None, approvals: ApprovalStore | None = None) -> None: self._client, self._settings, self._approvals = client, settings, approvals
    def list_posts(self) -> dict[str, object]: return self._get("/api/v2/community/posts.json")
    def search_posts(self, query: str) -> dict[str, object]:
        if not isinstance(query, str) or not query.strip(): return failure(ErrorCode.VALIDATION_ERROR, "query must be a non-empty string")
        return self._get("/api/v2/community/posts/search.json", {"query": query.strip()})
    def get_post(self, post_id: int) -> dict[str, object]:
        if not isinstance(post_id, int) or isinstance(post_id, bool) or post_id < 1: return failure(ErrorCode.VALIDATION_ERROR, "post_id must be a positive integer")
        return self._get(f"/api/v2/community/posts/{post_id}.json")
    def create_post(self, topic_id: int, title: str, details: str, *, execution_mode: str = "preview", approval_request_id: str | None = None, approval_token: str | None = None) -> dict[str, object]:
        if not isinstance(topic_id, int) or topic_id < 1 or not isinstance(title, str) or not title.strip() or not isinstance(details, str) or not details.strip(): return failure(ErrorCode.VALIDATION_ERROR, "topic_id, title, and details are required")
        payload = {"post": {"topic_id": topic_id, "title": title.strip(), "details": details.strip()}}
        if execution_mode == "preview":
            if self._approvals is None: return failure(ErrorCode.NOT_CONFIGURED, "Zendesk approval store is not configured")
            return success({"approval_request_id": self._approvals.create("zendesk_create_community_post", payload), "execution_mode": "preview", "public": True, "outbound_write": False})
        if execution_mode != "apply": return failure(ErrorCode.VALIDATION_ERROR, "execution_mode must be preview or apply")
        if self._settings is None or (blocked := check_write_permission(self._settings, WriteRisk.PUBLIC)) is not None: return blocked or failure(ErrorCode.WRITE_DISABLED, "Zendesk public writes are disabled")
        if self._approvals is None or not isinstance(approval_request_id, str) or not isinstance(approval_token, str) or not self._approvals.consume(approval_request_id, "zendesk_create_community_post", payload, approval_token): return failure(ErrorCode.APPROVAL_REQUIRED, "a matching local approval is required")
        if self._client is None or not hasattr(self._client, "request"): return failure(ErrorCode.NOT_CONFIGURED, "Zendesk write client is not configured")
        return self._client.request("POST", "/api/v2/community/posts.json", json_body=payload)
    def create_comment(self, post_id: int, body: str, *, execution_mode: str = "preview", approval_request_id: str | None = None, approval_token: str | None = None) -> dict[str, object]:
        if not isinstance(post_id, int) or post_id < 1 or not isinstance(body, str) or not body.strip(): return failure(ErrorCode.VALIDATION_ERROR, "post_id and body are required")
        payload = {"comment": {"body": body.strip()}}
        if execution_mode == "preview":
            if self._approvals is None: return failure(ErrorCode.NOT_CONFIGURED, "Zendesk approval store is not configured")
            return success({"approval_request_id": self._approvals.create("zendesk_create_community_comment", {"post_id": post_id, **payload}), "execution_mode": "preview", "public": True, "outbound_write": False})
        approval_payload = {"post_id": post_id, **payload}
        if execution_mode != "apply": return failure(ErrorCode.VALIDATION_ERROR, "execution_mode must be preview or apply")
        if self._settings is None or (blocked := check_write_permission(self._settings, WriteRisk.PUBLIC)) is not None: return blocked or failure(ErrorCode.WRITE_DISABLED, "Zendesk public writes are disabled")
        if self._approvals is None or not isinstance(approval_request_id, str) or not isinstance(approval_token, str) or not self._approvals.consume(approval_request_id, "zendesk_create_community_comment", approval_payload, approval_token): return failure(ErrorCode.APPROVAL_REQUIRED, "a matching local approval is required")
        if self._client is None or not hasattr(self._client, "request"): return failure(ErrorCode.NOT_CONFIGURED, "Zendesk write client is not configured")
        return self._client.request("POST", f"/api/v2/community/posts/{post_id}/comments.json", json_body=payload)
    def create_topic(self, name: str, description: str, *, execution_mode: str = "preview", approval_request_id: str | None = None, approval_token: str | None = None) -> dict[str, object]:
        if not isinstance(name, str) or not name.strip() or not isinstance(description, str): return failure(ErrorCode.VALIDATION_ERROR, "name and description are required")
        payload = {"topic": {"name": name.strip(), "description": description.strip()}}
        if execution_mode == "preview":
            if self._approvals is None: return failure(ErrorCode.NOT_CONFIGURED, "Zendesk approval store is not configured")
            return success({"approval_request_id": self._approvals.create("zendesk_create_community_topic", payload), "execution_mode": "preview", "public": True, "outbound_write": False})
        if execution_mode != "apply": return failure(ErrorCode.VALIDATION_ERROR, "execution_mode must be preview or apply")
        if self._settings is None or (blocked := check_write_permission(self._settings, WriteRisk.PUBLIC)) is not None: return blocked or failure(ErrorCode.WRITE_DISABLED, "Zendesk public writes are disabled")
        if self._approvals is None or not isinstance(approval_request_id, str) or not isinstance(approval_token, str) or not self._approvals.consume(approval_request_id, "zendesk_create_community_topic", payload, approval_token): return failure(ErrorCode.APPROVAL_REQUIRED, "a matching local approval is required")
        if self._client is None or not hasattr(self._client, "request"): return failure(ErrorCode.NOT_CONFIGURED, "Zendesk write client is not configured")
        return self._client.request("POST", "/api/v2/community/topics.json", json_body=payload)
    def update_post(self, post_id: int, post: dict[str, object], *, execution_mode: str = "preview", approval_request_id: str | None = None, approval_token: str | None = None) -> dict[str, object]:
        payload = self._post_payload(post)
        if not self._valid_id(post_id, "post_id") or payload is None: return failure(ErrorCode.VALIDATION_ERROR, "post_id and a valid post update are required")
        return self._approved_request("zendesk_update_community_post", {"post_id": post_id, **payload}, "PUT", f"/api/v2/community/posts/{post_id}.json", payload, WriteRisk.PUBLIC, execution_mode, approval_request_id, approval_token)
    def delete_post(self, post_id: int, *, execution_mode: str = "preview", approval_request_id: str | None = None, approval_token: str | None = None) -> dict[str, object]:
        if not self._valid_id(post_id, "post_id"): return failure(ErrorCode.VALIDATION_ERROR, "post_id must be a positive integer")
        return self._approved_request("zendesk_delete_community_post", {"post_id": post_id}, "DELETE", f"/api/v2/community/posts/{post_id}.json", None, WriteRisk.DESTRUCTIVE, execution_mode, approval_request_id, approval_token)
    def list_comments(self, post_id: int) -> dict[str, object]: return self._by_id("/api/v2/community/posts/{id}/comments.json", post_id, "post_id")
    def get_comment(self, comment_id: int) -> dict[str, object]: return self._by_id("/api/v2/community/comments/{id}.json", comment_id, "comment_id")
    def list_topics(self) -> dict[str, object]: return self._get("/api/v2/community/topics.json")
    def get_topic(self, topic_id: int) -> dict[str, object]: return self._by_id("/api/v2/community/topics/{id}.json", topic_id, "topic_id")
    def list_votes(self, post_id: int) -> dict[str, object]: return self._by_id("/api/v2/community/posts/{id}/votes.json", post_id, "post_id")
    def list_post_subscriptions(self, post_id: int) -> dict[str, object]: return self._by_id("/api/v2/community/posts/{id}/subscriptions.json", post_id, "post_id")
    def list_topic_subscriptions(self, topic_id: int) -> dict[str, object]: return self._by_id("/api/v2/community/topics/{id}/subscriptions.json", topic_id, "topic_id")
    def search_content_tags(self, prefix: str) -> dict[str, object]:
        if not isinstance(prefix, str): return failure(ErrorCode.VALIDATION_ERROR, "prefix must be a string")
        return self._get("/api/v2/guide/content_tags.json", {"prefix": prefix})
    def count_content_tags(self) -> dict[str, object]: return self._get("/api/v2/guide/content_tags/count.json")
    def get_content_tag(self, tag_id: str) -> dict[str, object]:
        if not isinstance(tag_id, str) or not tag_id.strip(): return failure(ErrorCode.VALIDATION_ERROR, "tag_id must be a non-empty string")
        return self._get(f"/api/v2/guide/content_tags/{tag_id}.json")
    def _by_id(self, template: str, value: int, name: str) -> dict[str, object]:
        if not self._valid_id(value, name): return failure(ErrorCode.VALIDATION_ERROR, f"{name} must be a positive integer")
        return self._get(template.format(id=value))
    @staticmethod
    def _valid_id(value: object, name: str) -> bool: return isinstance(value, int) and not isinstance(value, bool) and value > 0
    def _post_payload(self, post: dict[str, object]) -> dict[str, object] | None:
        if not isinstance(post, dict) or not post or set(post) - {"title", "details", "topic_id", "status", "closed", "featured", "pinned", "content_tag_ids"}: return None
        normalized = dict(post)
        for name in {"title", "details"} & normalized.keys():
            if not isinstance(normalized[name], str) or (name == "title" and not normalized[name].strip()): return None
            normalized[name] = normalized[name].strip()
        if "topic_id" in normalized and not self._valid_id(normalized["topic_id"], "topic_id"): return None
        if "status" in normalized and normalized["status"] not in {"planned", "not_planned", "answered", "completed"}: return None
        if any(name in normalized and not isinstance(normalized[name], bool) for name in {"closed", "featured", "pinned"}): return None
        if "content_tag_ids" in normalized and (not isinstance(normalized["content_tag_ids"], list) or not all(self._valid_id(value, "content_tag_id") for value in normalized["content_tag_ids"])): return None
        return {"post": normalized}
    def _approved_request(self, tool: str, approval_payload: dict[str, object], method: str, path: str, json_body: dict[str, object] | None, risk: WriteRisk, execution_mode: str, approval_request_id: str | None, approval_token: str | None) -> dict[str, object]:
        if execution_mode == "preview":
            if self._approvals is None: return failure(ErrorCode.NOT_CONFIGURED, "Zendesk approval store is not configured")
            return success({"approval_request_id": self._approvals.create(tool, approval_payload), "execution_mode": "preview", risk.value: True, "outbound_write": False})
        if execution_mode != "apply": return failure(ErrorCode.VALIDATION_ERROR, "execution_mode must be preview or apply")
        if self._settings is None or (blocked := check_write_permission(self._settings, risk)) is not None: return blocked or failure(ErrorCode.WRITE_DISABLED, f"Zendesk {risk.value} writes are disabled")
        if self._approvals is None or not isinstance(approval_request_id, str) or not isinstance(approval_token, str) or not self._approvals.consume(approval_request_id, tool, approval_payload, approval_token): return failure(ErrorCode.APPROVAL_REQUIRED, "a matching local approval is required")
        if self._client is None: return failure(ErrorCode.NOT_CONFIGURED, "Zendesk write client is not configured")
        return self._client.request(method, path, json_body=json_body)
    def _get(self, path: str, params: dict[str, str] | None = None) -> dict[str, object]:
        if self._client is None: return failure(ErrorCode.NOT_CONFIGURED, "Zendesk is not configured")
        return self._client.get(path, params=params)
