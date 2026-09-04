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
    def update_comment(self, post_id: int, comment_id: int, comment: dict[str, object], *, execution_mode: str = "preview", approval_request_id: str | None = None, approval_token: str | None = None) -> dict[str, object]:
        payload = self._comment_payload(comment)
        if not self._valid_id(post_id, "post_id") or not self._valid_id(comment_id, "comment_id") or payload is None: return failure(ErrorCode.VALIDATION_ERROR, "post_id, comment_id, and a valid comment update are required")
        return self._approved_request("zendesk_update_community_comment", {"post_id": post_id, "comment_id": comment_id, **payload}, "PUT", f"/api/v2/community/posts/{post_id}/comments/{comment_id}.json", payload, WriteRisk.PUBLIC, execution_mode, approval_request_id, approval_token)
    def delete_comment(self, post_id: int, comment_id: int, *, execution_mode: str = "preview", approval_request_id: str | None = None, approval_token: str | None = None) -> dict[str, object]:
        if not self._valid_id(post_id, "post_id") or not self._valid_id(comment_id, "comment_id"): return failure(ErrorCode.VALIDATION_ERROR, "post_id and comment_id must be positive integers")
        return self._approved_request("zendesk_delete_community_comment", {"post_id": post_id, "comment_id": comment_id}, "DELETE", f"/api/v2/community/posts/{post_id}/comments/{comment_id}.json", None, WriteRisk.DESTRUCTIVE, execution_mode, approval_request_id, approval_token)
    def update_topic(self, topic_id: int, topic: dict[str, object], *, execution_mode: str = "preview", approval_request_id: str | None = None, approval_token: str | None = None) -> dict[str, object]:
        payload = self._topic_payload(topic)
        if not self._valid_id(topic_id, "topic_id") or payload is None: return failure(ErrorCode.VALIDATION_ERROR, "topic_id and a valid topic update are required")
        return self._approved_request("zendesk_update_community_topic", {"topic_id": topic_id, **payload}, "PUT", f"/api/v2/community/topics/{topic_id}.json", payload, WriteRisk.PUBLIC, execution_mode, approval_request_id, approval_token)
    def delete_topic(self, topic_id: int, *, execution_mode: str = "preview", approval_request_id: str | None = None, approval_token: str | None = None) -> dict[str, object]:
        if not self._valid_id(topic_id, "topic_id"): return failure(ErrorCode.VALIDATION_ERROR, "topic_id must be a positive integer")
        return self._approved_request("zendesk_delete_community_topic", {"topic_id": topic_id}, "DELETE", f"/api/v2/community/topics/{topic_id}.json", None, WriteRisk.DESTRUCTIVE, execution_mode, approval_request_id, approval_token)
    def list_comments(self, post_id: int) -> dict[str, object]: return self._by_id("/api/v2/community/posts/{id}/comments.json", post_id, "post_id")
    def get_comment(self, comment_id: int) -> dict[str, object]: return self._by_id("/api/v2/community/comments/{id}.json", comment_id, "comment_id")
    def list_topics(self) -> dict[str, object]: return self._get("/api/v2/community/topics.json")
    def get_topic(self, topic_id: int) -> dict[str, object]: return self._by_id("/api/v2/community/topics/{id}.json", topic_id, "topic_id")
    def list_votes(self, post_id: int) -> dict[str, object]: return self._by_id("/api/v2/help_center/posts/{id}/votes.json", post_id, "post_id")
    def get_vote(self, vote_id: int) -> dict[str, object]: return self._by_id("/api/v2/help_center/votes/{id}.json", vote_id, "vote_id")
    def cast_vote(self, content_type: str, post_id: int, comment_id: int | None, direction: str, *, execution_mode: str = "preview", approval_request_id: str | None = None, approval_token: str | None = None) -> dict[str, object]:
        path = self._vote_path(content_type, post_id, comment_id, direction)
        if path is None: return failure(ErrorCode.VALIDATION_ERROR, "valid content_type, post_id, comment_id, and direction are required")
        payload: dict[str, object] = {"content_type": content_type, "post_id": post_id, "direction": direction}
        if comment_id is not None: payload["comment_id"] = comment_id
        return self._approved_request(f"zendesk_{direction}vote_community_content", payload, "POST", path, None, WriteRisk.PUBLIC, execution_mode, approval_request_id, approval_token)
    def delete_vote(self, vote_id: int, *, execution_mode: str = "preview", approval_request_id: str | None = None, approval_token: str | None = None) -> dict[str, object]:
        if not self._valid_id(vote_id, "vote_id"): return failure(ErrorCode.VALIDATION_ERROR, "vote_id must be a positive integer")
        return self._approved_request("zendesk_remove_community_vote", {"vote_id": vote_id}, "DELETE", f"/api/v2/help_center/votes/{vote_id}.json", None, WriteRisk.DESTRUCTIVE, execution_mode, approval_request_id, approval_token)
    def list_post_subscriptions(self, post_id: int) -> dict[str, object]: return self._by_id("/api/v2/community/posts/{id}/subscriptions.json", post_id, "post_id")
    def list_topic_subscriptions(self, topic_id: int) -> dict[str, object]: return self._by_id("/api/v2/community/topics/{id}/subscriptions.json", topic_id, "topic_id")
    def get_subscription(self, content_type: str, content_id: int, subscription_id: int) -> dict[str, object]:
        path = self._subscription_path(content_type, content_id, subscription_id)
        return self._get(path) if path else failure(ErrorCode.VALIDATION_ERROR, "valid content_type, content_id, and subscription_id are required")
    def create_subscription(self, content_type: str, content_id: int, *, include_comments: bool = False, execution_mode: str = "preview", approval_request_id: str | None = None, approval_token: str | None = None) -> dict[str, object]:
        path = self._subscription_path(content_type, content_id)
        if path is None or not isinstance(include_comments, bool) or (content_type == "post" and include_comments): return failure(ErrorCode.VALIDATION_ERROR, "valid subscription fields are required")
        body = {"subscription": {"include_comments": include_comments}} if content_type == "topic" else None
        payload = {"content_type": content_type, "content_id": content_id, "include_comments": include_comments}
        return self._approved_request("zendesk_create_content_subscription", payload, "POST", path, body, WriteRisk.PUBLIC, execution_mode, approval_request_id, approval_token)
    def update_subscription(self, topic_id: int, subscription_id: int, *, include_comments: bool, execution_mode: str = "preview", approval_request_id: str | None = None, approval_token: str | None = None) -> dict[str, object]:
        path = self._subscription_path("topic", topic_id, subscription_id)
        if path is None or not isinstance(include_comments, bool): return failure(ErrorCode.VALIDATION_ERROR, "valid topic_id, subscription_id, and include_comments are required")
        body = {"subscription": {"include_comments": include_comments}}
        return self._approved_request("zendesk_update_content_subscription", {"topic_id": topic_id, "subscription_id": subscription_id, "include_comments": include_comments}, "PUT", path, body, WriteRisk.PUBLIC, execution_mode, approval_request_id, approval_token)
    def delete_subscription(self, content_type: str, content_id: int, subscription_id: int, *, execution_mode: str = "preview", approval_request_id: str | None = None, approval_token: str | None = None) -> dict[str, object]:
        path = self._subscription_path(content_type, content_id, subscription_id)
        if path is None: return failure(ErrorCode.VALIDATION_ERROR, "valid content_type, content_id, and subscription_id are required")
        return self._approved_request("zendesk_delete_content_subscription", {"content_type": content_type, "content_id": content_id, "subscription_id": subscription_id}, "DELETE", path, None, WriteRisk.DESTRUCTIVE, execution_mode, approval_request_id, approval_token)
    def list_user_subscriptions(self, user_id: int | str, direction: str = "followers") -> dict[str, object]:
        path = self._user_subscription_path(user_id)
        if path is None or direction not in {"followers", "followings"}: return failure(ErrorCode.VALIDATION_ERROR, "valid user_id and direction are required")
        return self._get(path, {"type": direction})
    def upsert_user_subscription(self, user_id: int | str, followed_id: int, *, include_comments: bool = False, execution_mode: str = "preview", approval_request_id: str | None = None, approval_token: str | None = None) -> dict[str, object]:
        path = self._user_subscription_path(user_id)
        if path is None or not self._valid_id(followed_id, "followed_id") or not isinstance(include_comments, bool): return failure(ErrorCode.VALIDATION_ERROR, "valid user subscription fields are required")
        payload = {"user_id": user_id, "followed_id": followed_id, "include_comments": include_comments}
        body = {"user_subscription": {"followed_id": followed_id, "include_comments": include_comments}}
        risk: WriteRisk | tuple[WriteRisk, ...] = WriteRisk.PUBLIC if user_id == "me" else (WriteRisk.PUBLIC, WriteRisk.IMPERSONATION)
        return self._approved_request("zendesk_upsert_user_subscription", payload, "POST", path, body, risk, execution_mode, approval_request_id, approval_token)
    def delete_user_subscription(self, user_id: int | str, subscription_id: int, *, execution_mode: str = "preview", approval_request_id: str | None = None, approval_token: str | None = None) -> dict[str, object]:
        path = self._user_subscription_path(user_id, subscription_id)
        if path is None: return failure(ErrorCode.VALIDATION_ERROR, "valid user_id and subscription_id are required")
        risk: WriteRisk | tuple[WriteRisk, ...] = WriteRisk.DESTRUCTIVE if user_id == "me" else (WriteRisk.DESTRUCTIVE, WriteRisk.IMPERSONATION)
        return self._approved_request("zendesk_delete_user_subscription", {"user_id": user_id, "subscription_id": subscription_id}, "DELETE", path, None, risk, execution_mode, approval_request_id, approval_token)
    def search_content_tags(self, prefix: str) -> dict[str, object]:
        if not isinstance(prefix, str): return failure(ErrorCode.VALIDATION_ERROR, "prefix must be a string")
        return self._get("/api/v2/guide/content_tags.json", {"filter[name_prefix]": prefix})
    def count_content_tags(self) -> dict[str, object]: return self._get("/api/v2/guide/content_tags/count.json")
    def get_content_tag(self, tag_id: str) -> dict[str, object]:
        if not self._valid_tag_id(tag_id): return failure(ErrorCode.VALIDATION_ERROR, "tag_id must be a non-empty path-safe string")
        return self._get(f"/api/v2/guide/content_tags/{tag_id}.json")
    def create_content_tag(self, name: str, *, execution_mode: str = "preview", approval_request_id: str | None = None, approval_token: str | None = None) -> dict[str, object]:
        payload = self._content_tag_payload(name)
        if payload is None: return failure(ErrorCode.VALIDATION_ERROR, "name must be a non-empty string")
        return self._approved_request("zendesk_create_content_tag", payload, "POST", "/api/v2/guide/content_tags.json", payload, WriteRisk.PUBLIC, execution_mode, approval_request_id, approval_token)
    def update_content_tag(self, tag_id: str, name: str, *, execution_mode: str = "preview", approval_request_id: str | None = None, approval_token: str | None = None) -> dict[str, object]:
        payload = self._content_tag_payload(name)
        if not self._valid_tag_id(tag_id) or payload is None: return failure(ErrorCode.VALIDATION_ERROR, "tag_id and name must be valid")
        return self._approved_request("zendesk_update_content_tag", {"tag_id": tag_id, **payload}, "PUT", f"/api/v2/guide/content_tags/{tag_id}.json", payload, WriteRisk.PUBLIC, execution_mode, approval_request_id, approval_token)
    def delete_content_tag(self, tag_id: str, *, execution_mode: str = "preview", approval_request_id: str | None = None, approval_token: str | None = None) -> dict[str, object]:
        if not self._valid_tag_id(tag_id): return failure(ErrorCode.VALIDATION_ERROR, "tag_id must be a non-empty path-safe string")
        return self._approved_request("zendesk_delete_content_tag", {"tag_id": tag_id}, "DELETE", f"/api/v2/guide/content_tags/{tag_id}.json", None, WriteRisk.DESTRUCTIVE, execution_mode, approval_request_id, approval_token)
    def _by_id(self, template: str, value: int, name: str) -> dict[str, object]:
        if not self._valid_id(value, name): return failure(ErrorCode.VALIDATION_ERROR, f"{name} must be a positive integer")
        return self._get(template.format(id=value))
    @staticmethod
    def _valid_id(value: object, name: str) -> bool: return isinstance(value, int) and not isinstance(value, bool) and value > 0
    @staticmethod
    def _valid_tag_id(value: object) -> bool: return isinstance(value, str) and bool(value.strip()) and "/" not in value
    def _vote_path(self, content_type: str, post_id: int, comment_id: int | None, direction: str) -> str | None:
        if direction not in {"up", "down"} or not self._valid_id(post_id, "post_id"): return None
        if content_type == "post" and comment_id is None: return f"/api/v2/help_center/posts/{post_id}/{direction}.json"
        if content_type == "post_comment" and self._valid_id(comment_id, "comment_id"): return f"/api/v2/community/posts/{post_id}/comments/{comment_id}/{direction}.json"
        return None
    def _subscription_path(self, content_type: str, content_id: int, subscription_id: int | None = None) -> str | None:
        if content_type not in {"post", "topic"} or not self._valid_id(content_id, "content_id") or (subscription_id is not None and not self._valid_id(subscription_id, "subscription_id")): return None
        path = f"/api/v2/community/{content_type}s/{content_id}/subscriptions"
        return f"{path}/{subscription_id}.json" if subscription_id is not None else f"{path}.json"
    def _user_subscription_path(self, user_id: int | str, subscription_id: int | None = None) -> str | None:
        if user_id != "me" and not self._valid_id(user_id, "user_id"): return None
        if subscription_id is not None and not self._valid_id(subscription_id, "subscription_id"): return None
        path = f"/api/v2/help_center/users/{user_id}/user_subscriptions"
        return f"{path}/{subscription_id}.json" if subscription_id is not None else f"{path}.json"
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
    def _comment_payload(self, comment: dict[str, object]) -> dict[str, object] | None:
        if not isinstance(comment, dict) or not comment or set(comment) - {"body", "official"}: return None
        normalized = dict(comment)
        if "body" in normalized and (not isinstance(normalized["body"], str) or not normalized["body"].strip()): return None
        if "body" in normalized: normalized["body"] = normalized["body"].strip()
        if "official" in normalized and not isinstance(normalized["official"], bool): return None
        return {"comment": normalized}
    def _topic_payload(self, topic: dict[str, object]) -> dict[str, object] | None:
        if not isinstance(topic, dict) or not topic or set(topic) - {"name", "description", "manageable_by", "position", "user_segment_id"}: return None
        normalized = dict(topic)
        if "name" in normalized and (not isinstance(normalized["name"], str) or not normalized["name"].strip()): return None
        if "name" in normalized: normalized["name"] = normalized["name"].strip()
        if "description" in normalized and not isinstance(normalized["description"], str): return None
        if "manageable_by" in normalized and normalized["manageable_by"] not in {"staff", "managers"}: return None
        if "position" in normalized and (not isinstance(normalized["position"], int) or isinstance(normalized["position"], bool) or normalized["position"] < 0): return None
        if "user_segment_id" in normalized and not self._valid_id(normalized["user_segment_id"], "user_segment_id"): return None
        return {"topic": normalized}
    @staticmethod
    def _content_tag_payload(name: object) -> dict[str, object] | None:
        if not isinstance(name, str) or not name.strip(): return None
        return {"content_tag": {"name": name.strip()}}
    def _approved_request(self, tool: str, approval_payload: dict[str, object], method: str, path: str, json_body: dict[str, object] | None, risk: WriteRisk | tuple[WriteRisk, ...], execution_mode: str, approval_request_id: str | None, approval_token: str | None) -> dict[str, object]:
        risks = risk if isinstance(risk, tuple) else (risk,)
        if execution_mode == "preview":
            if self._approvals is None: return failure(ErrorCode.NOT_CONFIGURED, "Zendesk approval store is not configured")
            return success({"approval_request_id": self._approvals.create(tool, approval_payload), "execution_mode": "preview", **{item.value: True for item in risks}, "outbound_write": False})
        if execution_mode != "apply": return failure(ErrorCode.VALIDATION_ERROR, "execution_mode must be preview or apply")
        if self._settings is None: return failure(ErrorCode.WRITE_DISABLED, "Zendesk writes are disabled")
        for item in risks:
            if (blocked := check_write_permission(self._settings, item)) is not None: return blocked
        if self._approvals is None or not isinstance(approval_request_id, str) or not isinstance(approval_token, str) or not self._approvals.consume(approval_request_id, tool, approval_payload, approval_token): return failure(ErrorCode.APPROVAL_REQUIRED, "a matching local approval is required")
        if self._client is None: return failure(ErrorCode.NOT_CONFIGURED, "Zendesk write client is not configured")
        return self._client.request(method, path, json_body=json_body)
    def _get(self, path: str, params: dict[str, str] | None = None) -> dict[str, object]:
        if self._client is None: return failure(ErrorCode.NOT_CONFIGURED, "Zendesk is not configured")
        return self._client.get(path, params=params)
