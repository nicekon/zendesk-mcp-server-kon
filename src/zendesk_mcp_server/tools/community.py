"""Read-only Zendesk Community tools."""
from __future__ import annotations
import base64
import hashlib
import json
import os
import re
import stat
from contextlib import ExitStack
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import BinaryIO, Iterator, Protocol
from tempfile import TemporaryFile
from urllib.parse import urlsplit
from ..approvals import ApprovalStore
from ..config import Settings
from ..client import valid_user_image_path
from ..html_text import HTMLText
from ..pagination import collect_array, collect_cursor, collect_offset
from ..contracts import ErrorCode, failure
from ..contracts import success
from ..write_policy import WriteRisk, check_write_permission


_HTML_TAGS = {"p", "div", "span", "br", "b", "i", "u", "strong", "em", "sub", "sup", "a", "hr", "img", "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "li", "dl", "dt", "dd", "table", "thead", "tbody", "tfoot", "tr", "th", "td", "colgroup", "col", "blockquote", "pre", "abbr", "acronym", "cite", "code", "tt", "samp", "kbd", "var", "dfn", "address", "x-zendesk-user"}
_VOID_HTML_TAGS = {"br", "hr", "img", "col"}
_HTML_ATTRIBUTES = {"a": {"href", "title", "rel"}, "img": {"src", "alt", "title", "width", "height"}, "th": {"colspan", "rowspan", "scope"}, "td": {"colspan", "rowspan", "scope"}}
_LOCALE = re.compile(r"[a-z]{2,3}(?:-[a-z0-9]+)*$")
_SECURE_UPLOAD_OPEN = os.open in os.supports_dir_fd and all(hasattr(os, name) for name in ("O_NOFOLLOW", "O_DIRECTORY", "O_NONBLOCK"))


class _CommunityHTMLValidator(HTMLParser):
    def __init__(self, subdomain: str | None) -> None:
        super().__init__(convert_charrefs=True); self.subdomain, self.valid, self.stack, self._mention = subdomain, True, [], []
    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._start(tag, attrs, False)
    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._start(tag, attrs, True)
    def _start(self, tag: str, attrs: list[tuple[str, str | None]], self_closing: bool) -> None:
        if self._mention: self.valid = False; return
        if tag not in _HTML_TAGS or (self_closing and tag not in _VOID_HTML_TAGS) or len({name for name, _ in attrs}) != len(attrs): self.valid = False; return
        values = dict(attrs)
        if set(values) - _HTML_ATTRIBUTES.get(tag, set()) or any(value is None for value in values.values()): self.valid = False; return
        if tag == "a" and not self._safe_link(values.get("href", "")): self.valid = False; return
        if tag == "img" and not self._safe_image(values.get("src", "")): self.valid = False; return
        if tag in {"img", "th", "td"} and any(name in values and not values[name].isdigit() for name in {"width", "height", "colspan", "rowspan"}): self.valid = False; return
        if tag not in _VOID_HTML_TAGS: self.stack.append(tag)
        if tag == "x-zendesk-user": self._mention.append("")
    def handle_endtag(self, tag: str) -> None:
        if tag in _VOID_HTML_TAGS or not self.stack or self.stack[-1] != tag: self.valid = False; return
        self.stack.pop()
        if tag == "x-zendesk-user" and (not self._mention or not self._mention.pop().strip().isdigit()): self.valid = False
    def handle_data(self, data: str) -> None:
        if self.stack and self.stack[-1] == "x-zendesk-user" and self._mention: self._mention[-1] += data
    def handle_comment(self, data: str) -> None: self.valid = False
    def handle_decl(self, decl: str) -> None: self.valid = False
    def unknown_decl(self, data: str) -> None: self.valid = False
    def _safe_link(self, value: str) -> bool: return urlsplit(value).scheme in {"http", "https", "mailto"}
    def _safe_image(self, value: str) -> bool:
        if valid_user_image_path(value): return True
        parsed = urlsplit(value)
        return self.subdomain is not None and parsed.scheme == "https" and parsed.netloc == f"{self.subdomain}.zendesk.com" and not parsed.query and not parsed.fragment and not any(character.isspace() or ord(character) < 32 for character in value) and valid_user_image_path(parsed.path)

def _content_display(record):
    if not isinstance(record, dict): return record
    fields = {key: record[key] for key in ("title", "name", "description", "body", "details") if isinstance(record.get(key), str)}
    if not fields: return record
    plain = {}; unavailable = []
    for key, value in fields.items():
        if key not in {"body", "details"}:
            plain[key] = value
            continue
        parser = HTMLText()
        try:
            parser.feed(value); parser.close()
        except (AssertionError, ValueError):
            unavailable.append(key)
            continue
        plain[key] = "\n".join(line for line in "".join(parser.parts).splitlines() if line).strip()
    return {**record, "untrusted_user_content": True, "plain_text": plain, **({"plain_text_unavailable": unavailable} if unavailable else {})}


class CommunityClient(Protocol):
    def get(self, path: str, *, params: dict[str, str] | None = None) -> dict[str, object]: ...
    def request(self, method: str, path: str, *, json_body: dict[str, object] | None = None) -> dict[str, object]: ...
    def upload_presigned(self, url: str, headers: dict[str, str], content: bytes | Iterator[bytes]) -> dict[str, object]: ...

class CommunityTools:
    def __init__(self, client: CommunityClient | None, settings: Settings | None = None, approvals: ApprovalStore | None = None) -> None: self._client, self._settings, self._approvals = client, settings, approvals
    def list_posts(self, *, topic_id: int | None = None, user_id: int | str | None = None, status: str | None = None, sort_by: str | None = None, cursor: str | None = None, limit: int = 100) -> dict[str, object]:
        if (topic_id is not None and user_id is not None) or (topic_id is not None and not self._valid_id(topic_id, "topic_id")) or (user_id is not None and user_id != "me" and not self._valid_id(user_id, "user_id")) or (status is not None and status not in {"planned", "not_planned", "completed", "answered", "none"}) or (sort_by is not None and sort_by not in {"created_at", "edited_at", "updated_at", "recent_activity", "votes", "comments"}): return failure(ErrorCode.VALIDATION_ERROR, "valid post scope and filters are required")
        path = f"/api/v2/community/topics/{topic_id}/posts.json" if topic_id is not None else f"/api/v2/community/users/{user_id}/posts.json" if user_id is not None else "/api/v2/community/posts.json"
        filters = {name: value for name, value in {"filter_by": status, "sort_by": sort_by}.items() if value is not None}
        return collect_cursor(self._get, path, "posts", limit, cursor, filters=filters)
    def search_posts(self, query: str, *, limit: int = 100, cursor: str | None = None) -> dict[str, object]:
        if not isinstance(query, str) or not query.strip(): return failure(ErrorCode.VALIDATION_ERROR, "query must be a non-empty string")
        return collect_offset(self._get, "/api/v2/help_center/community_posts/search.json", "results", limit, cursor, filters={"query": query.strip()}, max_results=1000, page_size=25)
    def get_post(self, post_id: int) -> dict[str, object]:
        if not isinstance(post_id, int) or isinstance(post_id, bool) or post_id < 1: return failure(ErrorCode.VALIDATION_ERROR, "post_id must be a positive integer")
        return self._get(f"/api/v2/community/posts/{post_id}.json")
    def create_post(self, topic_id: int, title: str, details: str, *, author_id: int | None = None, created_at: str | None = None, notify_subscribers: bool = False, execution_mode: str = "preview", approval_request_id: str | None = None, approval_token: str | None = None) -> dict[str, object]:
        if not self._valid_id(topic_id, "topic_id") or not isinstance(title, str) or not title.strip() or not isinstance(details, str) or not details.strip() or not self._valid_html(details) or not isinstance(notify_subscribers, bool) or (author_id is not None and not self._valid_id(author_id, "author_id")) or not _valid_timestamp(created_at): return failure(ErrorCode.VALIDATION_ERROR, "valid post fields, notify_subscribers, author_id, and created_at are required")
        post: dict[str, object] = {"topic_id": topic_id, "title": title.strip(), "details": details.strip()}
        if author_id is not None: post["author_id"] = author_id
        if created_at is not None: post["created_at"] = created_at
        payload = {"post": post, "notify_subscribers": notify_subscribers}; risks = (WriteRisk.PUBLIC, WriteRisk.IMPERSONATION) if author_id is not None or created_at is not None else WriteRisk.PUBLIC
        result = self._approved_request("zendesk_create_community_post", payload, "POST", "/api/v2/community/posts.json", payload, risks, execution_mode, approval_request_id, approval_token)
        if execution_mode == "preview" and result.get("ok") and notify_subscribers: result["data"].update({"topic_id": topic_id, **self._notification_metadata(f"/api/v2/community/topics/{topic_id}.json", "topic")})
        return result
    def create_comment(self, post_id: int, body: str, *, author_id: int | None = None, created_at: str | None = None, notify_subscribers: bool = False, execution_mode: str = "preview", approval_request_id: str | None = None, approval_token: str | None = None) -> dict[str, object]:
        if not self._valid_id(post_id, "post_id") or not isinstance(body, str) or not body.strip() or not self._valid_html(body) or not isinstance(notify_subscribers, bool) or (author_id is not None and not self._valid_id(author_id, "author_id")) or not _valid_timestamp(created_at): return failure(ErrorCode.VALIDATION_ERROR, "valid comment fields, notify_subscribers, author_id, and created_at are required")
        comment: dict[str, object] = {"body": body.strip()}
        if author_id is not None: comment["author_id"] = author_id
        if created_at is not None: comment["created_at"] = created_at
        payload = {"post_id": post_id, "comment": comment, "notify_subscribers": notify_subscribers}; body_payload = {"comment": comment, "notify_subscribers": notify_subscribers}; risks = (WriteRisk.PUBLIC, WriteRisk.IMPERSONATION) if author_id is not None or created_at is not None else WriteRisk.PUBLIC
        result = self._approved_request("zendesk_create_community_comment", payload, "POST", f"/api/v2/community/posts/{post_id}/comments.json", body_payload, risks, execution_mode, approval_request_id, approval_token)
        if execution_mode == "preview" and result.get("ok") and notify_subscribers: result["data"].update({"post_id": post_id, **self._notification_metadata(f"/api/v2/community/posts/{post_id}.json", "post")})
        return result
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
    def list_comments(self, post_id: int | None = None, *, user_id: int | str | None = None, cursor: str | None = None, limit: int = 100) -> dict[str, object]:
        if (post_id is None) == (user_id is None) or (post_id is not None and not self._valid_id(post_id, "post_id")) or (user_id is not None and user_id != "me" and not self._valid_id(user_id, "user_id")): return failure(ErrorCode.VALIDATION_ERROR, "exactly one valid post_id or user_id is required")
        path = f"/api/v2/community/posts/{post_id}/comments.json" if post_id is not None else f"/api/v2/community/users/{user_id}/comments.json"
        return collect_cursor(self._get, path, "comments", limit, cursor)
    def get_comment(self, comment_id: int, *, post_id: int | None = None, locale: str | None = None) -> dict[str, object]:
        if not self._valid_id(comment_id, "comment_id") or (post_id is not None and not self._valid_id(post_id, "post_id")) or (locale is not None and (post_id is None or not isinstance(locale, str) or not _LOCALE.fullmatch(locale))): return failure(ErrorCode.VALIDATION_ERROR, "valid comment_id, post_id, and locale are required")
        if post_id is None: return self._by_id("/api/v2/community/comments/{id}.json", comment_id, "comment_id")
        path = f"/api/v2/help_center/{locale}/community/posts/{post_id}/comments/{comment_id}.json" if locale is not None else f"/api/v2/community/posts/{post_id}/comments/{comment_id}.json"
        return self._get(path)
    def list_topics(self, *, cursor: str | None = None, limit: int = 100) -> dict[str, object]:
        return collect_cursor(self._get, "/api/v2/community/topics.json", "topics", limit, cursor)
    def get_topic(self, topic_id: int) -> dict[str, object]: return self._by_id("/api/v2/community/topics/{id}.json", topic_id, "topic_id")
    def list_votes(self, post_id: int | None = None, *, comment_id: int | None = None, user_id: int | str | None = None, cursor: str | None = None, limit: int = 100) -> dict[str, object]:
        if comment_id is not None and (post_id is None or not self._valid_id(comment_id, "comment_id")):
            return failure(ErrorCode.VALIDATION_ERROR, "comment_id must be a positive integer with post_id")
        if (post_id is None) == (user_id is None) or (post_id is not None and not self._valid_id(post_id, "post_id")) or (user_id is not None and user_id != "me" and not self._valid_id(user_id, "user_id")) or not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 1000:
            return failure(ErrorCode.VALIDATION_ERROR, "exactly one valid post_id or user_id and a limit from 1 to 1000 are required")
        if post_id is not None:
            if comment_id is not None:
                return collect_cursor(self._get, f"/api/v2/community/posts/{post_id}/comments/{comment_id}/votes", "votes", limit, cursor)
            return collect_cursor(self._get, f"/api/v2/community/posts/{post_id}/votes.json", "votes", limit, cursor)
        state = _decode_vote_cursor(cursor, user_id)
        if state is None: return failure(ErrorCode.VALIDATION_ERROR, "cursor is invalid for this user")
        after, skip, scanned, items, seen = state["after"], state["skip"], 0, [], set()
        if after is not None: seen.add(after)
        while scanned < 1000:
            page_size = min(100, 1000 - scanned)
            params = {"page[size]": str(page_size)}
            if after is not None: params["page[after]"] = after
            result = self._get(f"/api/v2/help_center/users/{user_id}/votes.json", params)
            if not result.get("ok"): return result
            data = result.get("data"); votes = data.get("votes") if isinstance(data, dict) else None; meta = data.get("meta", {}) if isinstance(data, dict) else None
            if not isinstance(votes, list) or len(votes) > page_size or any(not isinstance(vote, dict) for vote in votes) or not isinstance(meta, dict) or not isinstance(meta.get("has_more"), bool): return failure(ErrorCode.UPSTREAM_ERROR, "Zendesk returned an invalid vote page")
            next_after = meta.get("after_cursor")
            if meta["has_more"] and (not isinstance(next_after, str) or not next_after or next_after in seen): return failure(ErrorCode.UPSTREAM_ERROR, "Zendesk returned an invalid vote cursor")
            scanned += len(votes)
            community_votes = [vote for vote in votes if isinstance(vote, dict) and vote.get("item_type") in {"Post", "PostComment"}]
            if skip > len(community_votes): return failure(ErrorCode.UPSTREAM_ERROR, "Zendesk returned an invalid vote cursor")
            remaining = limit - len(items)
            if len(community_votes) - skip > remaining:
                items.extend(community_votes[skip:skip + remaining])
                return {"ok": True, "items": items, "has_more": True, "next_cursor": _encode_vote_cursor(user_id, after, skip + remaining), "truncated": True, "scanned_count": scanned}
            items.extend(community_votes[skip:]); skip = 0
            if not meta.get("has_more"): return {"ok": True, "items": items, "has_more": False, "next_cursor": None, "truncated": False, "scanned_count": scanned}
            if len(items) == limit: return {"ok": True, "items": items, "has_more": True, "next_cursor": _encode_vote_cursor(user_id, next_after, 0), "truncated": True, "scanned_count": scanned}
            seen.add(next_after); after = next_after
            if not votes: break
        return {"ok": True, "items": items, "has_more": True, "next_cursor": _encode_vote_cursor(user_id, after, 0), "truncated": True, "scanned_count": scanned}
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
    def list_post_subscriptions(self, post_id: int, *, limit: int = 100, cursor: str | None = None) -> dict[str, object]:
        path = self._subscription_path("post", post_id)
        return collect_cursor(self._get, path, "subscriptions", limit, cursor) if path else failure(ErrorCode.VALIDATION_ERROR, "post_id must be a positive integer")
    def list_topic_subscriptions(self, topic_id: int, *, limit: int = 100, cursor: str | None = None) -> dict[str, object]:
        path = self._subscription_path("topic", topic_id)
        return collect_cursor(self._get, path, "subscriptions", limit, cursor) if path else failure(ErrorCode.VALIDATION_ERROR, "topic_id must be a positive integer")
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
    def list_user_subscriptions(self, user_id: int | str, direction: str = "followers", *, cursor: str | None = None, limit: int = 100) -> dict[str, object]:
        path = self._user_subscription_path(user_id)
        if path is None or direction not in {"followers", "followings"}: return failure(ErrorCode.VALIDATION_ERROR, "valid user_id and direction are required")
        return collect_cursor(self._get, path, "user_subscriptions", limit, cursor, filters={"type": direction})
    def upsert_user_subscription(self, user_id: int | str, followed_id: int, *, include_comments: bool = False, execution_mode: str = "preview", approval_request_id: str | None = None, approval_token: str | None = None) -> dict[str, object]:
        if execution_mode not in ("preview", "apply"): return failure(ErrorCode.VALIDATION_ERROR, "execution_mode must be preview or apply")
        path = self._user_subscription_path(user_id)
        if path is None or not self._valid_id(followed_id, "followed_id") or not isinstance(include_comments, bool): return failure(ErrorCode.VALIDATION_ERROR, "valid user subscription fields are required")
        payload = {"user_id": user_id, "followed_id": followed_id, "include_comments": include_comments}
        body = {"user_subscription": {"followed_id": followed_id, "include_comments": include_comments}}
        risk: WriteRisk | tuple[WriteRisk, ...] = WriteRisk.PUBLIC if user_id == "me" else (WriteRisk.PUBLIC, WriteRisk.IMPERSONATION)
        current = self._find_user_subscription(path, followed_id)
        if not current.get("ok"): return current
        existing = current["data"]["subscription"]
        if isinstance(existing, dict) and existing.get("include_comments") is include_comments:
            return success({"current_subscription": existing, "subscription": existing, "operation_state": "not_applied", "idempotent": True, "outbound_write": False})
        proposed = {"followed_id": followed_id, "include_comments": include_comments}
        if execution_mode == "preview":
            if self._approvals is None: return failure(ErrorCode.NOT_CONFIGURED, "Zendesk approval store is not configured")
            risks = risk if isinstance(risk, tuple) else (risk,)
            return success({"approval_request_id": self._approvals.create("zendesk_upsert_user_subscription", payload), "execution_mode": "preview", "current_subscription": existing, "proposed_subscription": proposed, "operation_state": "not_applied", **{item.value: True for item in risks}, "outbound_write": False})
        if execution_mode != "apply": return failure(ErrorCode.VALIDATION_ERROR, "execution_mode must be preview or apply")
        if self._settings is None: return failure(ErrorCode.WRITE_DISABLED, "Zendesk writes are disabled")
        for item in risk if isinstance(risk, tuple) else (risk,):
            if (blocked := check_write_permission(self._settings, item)) is not None: return blocked
        if self._approvals is None or not isinstance(approval_request_id, str) or not isinstance(approval_token, str) or not self._approvals.consume(approval_request_id, "zendesk_upsert_user_subscription", payload, approval_token): return failure(ErrorCode.APPROVAL_REQUIRED, "a matching local approval is required")
        if self._client is None: return failure(ErrorCode.NOT_CONFIGURED, "Zendesk write client is not configured")
        written = self._client.request("POST", path, json_body=body)
        if not written.get("ok"): return written
        observed = self._find_user_subscription(path, followed_id)
        if not observed.get("ok"): return failure(ErrorCode.OUTCOME_UNKNOWN, "subscription write succeeded but read-back failed", operation_state="unknown")
        subscription = observed["data"]["subscription"]
        if not isinstance(subscription, dict) or subscription.get("include_comments") is not include_comments: return failure(ErrorCode.OUTCOME_UNKNOWN, "subscription write could not be verified", operation_state="unknown")
        return success({"current_subscription": existing, "subscription": subscription, "operation_state": "applied", "idempotent": False}, request_id=written.get("request_id"), operation_state="applied")
    def delete_user_subscription(self, user_id: int | str, subscription_id: int, *, execution_mode: str = "preview", approval_request_id: str | None = None, approval_token: str | None = None) -> dict[str, object]:
        path = self._user_subscription_path(user_id, subscription_id)
        if path is None: return failure(ErrorCode.VALIDATION_ERROR, "valid user_id and subscription_id are required")
        risk: WriteRisk | tuple[WriteRisk, ...] = WriteRisk.DESTRUCTIVE if user_id == "me" else (WriteRisk.DESTRUCTIVE, WriteRisk.IMPERSONATION)
        return self._approved_request("zendesk_delete_user_subscription", {"user_id": user_id, "subscription_id": subscription_id}, "DELETE", path, None, risk, execution_mode, approval_request_id, approval_token)
    def list_badge_categories(self, brand_id: int | None = None, *, limit: int = 100, cursor: str | None = None) -> dict[str, object]:
        if brand_id is not None and not self._valid_id(brand_id, "brand_id"): return failure(ErrorCode.VALIDATION_ERROR, "brand_id must be a positive integer")
        return collect_array(self._get, "/api/v2/gather/badge_categories", "badge_categories", limit, cursor, filters={"brand_id": str(brand_id)} if brand_id is not None else None)
    def get_badge_category(self, category_id: str) -> dict[str, object]:
        if not self._valid_tag_id(category_id): return failure(ErrorCode.VALIDATION_ERROR, "category_id must be a non-empty path-safe string")
        return self._get(f"/api/v2/gather/badge_categories/{category_id}")
    def create_badge_category(self, brand_id: int, name: str, slug: str, *, execution_mode: str = "preview", approval_request_id: str | None = None, approval_token: str | None = None) -> dict[str, object]:
        if not self._valid_id(brand_id, "brand_id") or not isinstance(name, str) or not name.strip() or not self._valid_tag_id(slug): return failure(ErrorCode.VALIDATION_ERROR, "valid brand_id, name, and slug are required")
        payload = {"badge_category": {"brand_id": brand_id, "name": name.strip(), "slug": slug}}
        return self._approved_request("zendesk_create_badge_category", payload, "POST", "/api/v2/gather/badge_categories", payload, WriteRisk.PUBLIC, execution_mode, approval_request_id, approval_token)
    def delete_badge_category(self, category_id: str, *, execution_mode: str = "preview", approval_request_id: str | None = None, approval_token: str | None = None) -> dict[str, object]:
        if not self._valid_tag_id(category_id): return failure(ErrorCode.VALIDATION_ERROR, "category_id must be a non-empty path-safe string")
        return self._approved_request("zendesk_delete_badge_category", {"category_id": category_id}, "DELETE", f"/api/v2/gather/badge_categories/{category_id}", None, WriteRisk.DESTRUCTIVE, execution_mode, approval_request_id, approval_token)
    def list_badges(self, brand_id: int | None = None, *, limit: int = 100, cursor: str | None = None) -> dict[str, object]:
        if brand_id is not None and not self._valid_id(brand_id, "brand_id"): return failure(ErrorCode.VALIDATION_ERROR, "brand_id must be a positive integer")
        return collect_array(self._get, "/api/v2/gather/badges", "badges", limit, cursor, filters={"brand_id": str(brand_id)} if brand_id is not None else None)
    def get_badge(self, badge_id: str) -> dict[str, object]:
        if not self._valid_tag_id(badge_id): return failure(ErrorCode.VALIDATION_ERROR, "badge_id must be a non-empty path-safe string")
        return self._get(f"/api/v2/gather/badges/{badge_id}")
    def create_badge(self, badge_category_id: str, name: str, description: str, *, icon_upload_id: str | None = None, execution_mode: str = "preview", approval_request_id: str | None = None, approval_token: str | None = None) -> dict[str, object]:
        values: dict[str, object] = {"badge_category_id": badge_category_id, "name": name, "description": description}
        if icon_upload_id is not None: values["icon_upload_id"] = icon_upload_id
        payload = self._badge_payload(values)
        if payload is None or "badge_category_id" not in payload["badge"]: return failure(ErrorCode.VALIDATION_ERROR, "valid badge_category_id, name, and description are required")
        return self._approved_request("zendesk_create_badge", payload, "POST", "/api/v2/gather/badges", payload, WriteRisk.PUBLIC, execution_mode, approval_request_id, approval_token)
    def update_badge(self, badge_id: str, badge: dict[str, object], *, execution_mode: str = "preview", approval_request_id: str | None = None, approval_token: str | None = None) -> dict[str, object]:
        payload = self._badge_payload(badge)
        if not self._valid_tag_id(badge_id) or payload is None: return failure(ErrorCode.VALIDATION_ERROR, "badge_id and a valid badge update are required")
        return self._approved_request("zendesk_update_badge", {"badge_id": badge_id, **payload}, "PUT", f"/api/v2/gather/badges/{badge_id}", payload, WriteRisk.PUBLIC, execution_mode, approval_request_id, approval_token)
    def delete_badge(self, badge_id: str, *, execution_mode: str = "preview", approval_request_id: str | None = None, approval_token: str | None = None) -> dict[str, object]:
        if not self._valid_tag_id(badge_id): return failure(ErrorCode.VALIDATION_ERROR, "badge_id must be a non-empty path-safe string")
        if execution_mode not in {"preview", "apply"}: return failure(ErrorCode.VALIDATION_ERROR, "execution_mode must be preview or apply")
        if execution_mode == "apply":
            if self._settings is None: return failure(ErrorCode.WRITE_DISABLED, "Zendesk writes are disabled")
            if (blocked := check_write_permission(self._settings, WriteRisk.DESTRUCTIVE)) is not None: return blocked
        result = self._get("/api/v2/gather/badge_assignments", {"badge_id": badge_id})
        if not result.get("ok"): return result
        data = result.get("data")
        assignments = data.get("badge_assignments") if isinstance(data, dict) else None
        if not isinstance(assignments, list) or any(not isinstance(item, dict) or not self._valid_tag_id(item.get("id")) or item.get("badge_id") != badge_id for item in assignments):
            return failure(ErrorCode.UPSTREAM_ERROR, "Zendesk returned invalid cascade assignments")
        identifiers = sorted(item["id"] for item in assignments)
        if len(set(identifiers)) != len(identifiers): return failure(ErrorCode.UPSTREAM_ERROR, "Zendesk returned duplicate cascade assignments")
        payload = {"badge_id": badge_id, "cascade_assignment_count": len(identifiers), "cascade_snapshot_sha256": hashlib.sha256(json.dumps(identifiers).encode()).hexdigest(), "irreversible": True}
        result = self._approved_request("zendesk_delete_badge", payload, "DELETE", f"/api/v2/gather/badges/{badge_id}", None, WriteRisk.DESTRUCTIVE, execution_mode, approval_request_id, approval_token)
        if execution_mode == "preview" and result.get("ok"): result["data"].update(payload)
        return result
    def list_badge_assignments(self, *, user_id: int | None = None, badge_id: str | None = None, badge_category_id: str | None = None, brand_id: int | None = None, limit: int = 100, cursor: str | None = None) -> dict[str, object]:
        if any(value is not None and not self._valid_id(value, name) for value, name in ((user_id, "user_id"), (brand_id, "brand_id"))) or any(value is not None and not self._valid_tag_id(value) for value in (badge_id, badge_category_id)): return failure(ErrorCode.VALIDATION_ERROR, "badge assignment filters must be valid")
        params = {name: str(value) for name, value in {"user_id": user_id, "badge_id": badge_id, "badge_category_id": badge_category_id, "brand_id": brand_id}.items() if value is not None}
        return collect_array(self._get, "/api/v2/gather/badge_assignments", "badge_assignments", limit, cursor, filters=params or None)
    def create_badge_assignment(self, badge_id: str, user_id: int, *, execution_mode: str = "preview", approval_request_id: str | None = None, approval_token: str | None = None) -> dict[str, object]:
        if not self._valid_tag_id(badge_id) or not self._valid_id(user_id, "user_id"): return failure(ErrorCode.VALIDATION_ERROR, "valid badge_id and user_id are required")
        payload = {"badge_assignment": {"badge_id": badge_id, "user_id": str(user_id)}}
        return self._approved_request("zendesk_assign_badge", payload, "POST", "/api/v2/gather/badge_assignments", payload, (WriteRisk.PUBLIC, WriteRisk.IMPERSONATION), execution_mode, approval_request_id, approval_token)
    def delete_badge_assignment(self, assignment_id: str, *, execution_mode: str = "preview", approval_request_id: str | None = None, approval_token: str | None = None) -> dict[str, object]:
        if not self._valid_tag_id(assignment_id): return failure(ErrorCode.VALIDATION_ERROR, "assignment_id must be a non-empty path-safe string")
        return self._approved_request("zendesk_unassign_badge", {"assignment_id": assignment_id}, "DELETE", f"/api/v2/gather/badge_assignments/{assignment_id}", None, (WriteRisk.DESTRUCTIVE, WriteRisk.IMPERSONATION), execution_mode, approval_request_id, approval_token)
    def upload_user_image(self, image_path: str, content_type: str, brand_id: int, *, execution_mode: str = "preview", approval_request_id: str | None = None, approval_token: str | None = None) -> dict[str, object]:
        loaded = self._load_user_image(image_path, content_type, brand_id)
        return self._upload_image(loaded, "zendesk_upload_community_image", execution_mode, approval_request_id, approval_token)
    def upload_badge_icon(self, image_path: str, content_type: str, *, execution_mode: str = "preview", approval_request_id: str | None = None, approval_token: str | None = None) -> dict[str, object]:
        loaded = self._load_user_image(image_path, content_type, 1, allowed_content_types={"image/svg+xml", "image/png", "image/jpeg", "image/gif"})
        if not isinstance(loaded, dict): loaded[0].pop("brand_id")
        return self._upload_image(loaded, "zendesk_upload_badge_icon", execution_mode, approval_request_id, approval_token)
    def _upload_image(self, loaded: tuple[dict[str, object], BinaryIO] | dict[str, object], tool: str, execution_mode: str, approval_request_id: str | None, approval_token: str | None) -> dict[str, object]:
        if isinstance(loaded, dict): return loaded
        payload, content = loaded
        with content:
            if execution_mode == "preview":
                if self._approvals is None: return failure(ErrorCode.NOT_CONFIGURED, "Zendesk approval store is not configured")
                return success({"approval_request_id": self._approvals.create(tool, payload), "execution_mode": "preview", "external_upload": True, "outbound_write": False, **payload})
            if execution_mode != "apply": return failure(ErrorCode.VALIDATION_ERROR, "execution_mode must be preview or apply")
            if self._settings is None or (blocked := check_write_permission(self._settings, WriteRisk.EXTERNAL_UPLOAD)) is not None: return blocked or failure(ErrorCode.WRITE_DISABLED, "Zendesk external uploads are disabled")
            if self._approvals is None or not isinstance(approval_request_id, str) or not isinstance(approval_token, str) or not self._approvals.consume(approval_request_id, tool, payload, approval_token): return failure(ErrorCode.APPROVAL_REQUIRED, "a matching local approval is required")
            if self._client is None or not hasattr(self._client, "upload_presigned"): return failure(ErrorCode.NOT_CONFIGURED, "Zendesk upload client is not configured")
            badge = tool == "zendesk_upload_badge_icon"
            prepared = self._client.request("POST", "/api/v2/gather/badges/icon_uploads" if badge else "/api/v2/guide/user_images/uploads", json_body={"content_type": payload["content_type"], "file_size": payload["file_size"]})
            if not prepared.get("ok"): return self._upload_failure(prepared, "prepare_upload")
            upload = self._nested_data(prepared, "badge_icon_upload" if badge else "upload")
            if upload is None: return failure(ErrorCode.UPSTREAM_ERROR, "Zendesk returned an invalid image upload response", operation_state="unknown")
            url, headers, identifier = upload.get("url"), upload.get("headers"), upload.get("id" if badge else "token")
            if not isinstance(url, str) or not isinstance(headers, dict) or not (self._valid_tag_id(identifier) if badge else isinstance(identifier, str) and bool(identifier.strip())) or any(not isinstance(name, str) or not isinstance(value, str) for name, value in headers.items()): return failure(ErrorCode.UPSTREAM_ERROR, "Zendesk returned an invalid image upload response", operation_state="unknown")
            size = str(payload["file_size"])
            if any(name.lower() == "transfer-encoding" or (name.lower() == "content-length" and value != size) for name, value in headers.items()):
                return failure(ErrorCode.UPSTREAM_ERROR, "Zendesk returned conflicting upload framing", operation_state="unknown")
            headers = {name: value for name, value in headers.items() if name.lower() != "content-length"}
            headers["Content-Length"] = size
            uploaded = self._client.upload_presigned(url, headers, iter(lambda: content.read(65536), b""))
            if not uploaded.get("ok"): return self._upload_failure(uploaded, "upload_binary")
            if badge: return success({"badge_icon_upload_id": identifier}, request_id=prepared.get("request_id"), operation_state="applied")
            created = self._client.request("POST", "/api/v2/guide/user_images", json_body={"token": identifier, "brand_id": payload["brand_id"]})
            if not created.get("ok"): return self._upload_failure(created, "create_image_path")
            image = self._nested_data(created, "user_image")
            path = image.get("path") if image is not None else None
            if not valid_user_image_path(path):
                return failure(ErrorCode.UPSTREAM_ERROR, "Zendesk returned an invalid user image path", operation_state="unknown")
            return created
    @staticmethod
    def _upload_failure(result: dict[str, object], stage: str) -> dict[str, object]:
        error = result["error"]
        return {**result, "error": {**error, "details": {**error.get("details", {}), "upload_stage": stage}}}

    def search_content_tags(self, prefix: str, *, cursor: str | None = None, limit: int = 100) -> dict[str, object]:
        if not isinstance(prefix, str): return failure(ErrorCode.VALIDATION_ERROR, "prefix must be a string")
        return collect_cursor(self._get, "/api/v2/guide/content_tags", "records", limit, cursor, filters={"filter[name_prefix]": prefix}, page_size=30)
    def count_content_tags(self) -> dict[str, object]:
        result = self._get("/api/v2/guide/content_tags/count")
        if not result.get("ok"): return result
        data = result.get("data")
        if not isinstance(data, dict) or type(data.get("value")) is not int or data["value"] < 0:
            return failure(ErrorCode.UPSTREAM_ERROR, "Zendesk returned an invalid content tag count")
        return result
    def get_content_tag(self, tag_id: str) -> dict[str, object]:
        if not self._valid_tag_id(tag_id): return failure(ErrorCode.VALIDATION_ERROR, "tag_id must be a non-empty path-safe string")
        return self._get(f"/api/v2/guide/content_tags/{tag_id}")
    def create_content_tag(self, name: str, *, execution_mode: str = "preview", approval_request_id: str | None = None, approval_token: str | None = None) -> dict[str, object]:
        payload = self._content_tag_payload(name)
        if payload is None: return failure(ErrorCode.VALIDATION_ERROR, "name must be a non-empty string")
        return self._approved_request("zendesk_create_content_tag", payload, "POST", "/api/v2/guide/content_tags", payload, WriteRisk.PUBLIC, execution_mode, approval_request_id, approval_token)
    def update_content_tag(self, tag_id: str, name: str, *, execution_mode: str = "preview", approval_request_id: str | None = None, approval_token: str | None = None) -> dict[str, object]:
        payload = self._content_tag_payload(name)
        if not self._valid_tag_id(tag_id) or payload is None: return failure(ErrorCode.VALIDATION_ERROR, "tag_id and name must be valid")
        return self._approved_request("zendesk_update_content_tag", {"tag_id": tag_id, **payload}, "PUT", f"/api/v2/guide/content_tags/{tag_id}", payload, WriteRisk.PUBLIC, execution_mode, approval_request_id, approval_token)
    def delete_content_tag(self, tag_id: str, *, execution_mode: str = "preview", approval_request_id: str | None = None, approval_token: str | None = None) -> dict[str, object]:
        if not self._valid_tag_id(tag_id): return failure(ErrorCode.VALIDATION_ERROR, "tag_id must be a non-empty path-safe string")
        return self._approved_request("zendesk_delete_content_tag", {"tag_id": tag_id}, "DELETE", f"/api/v2/guide/content_tags/{tag_id}", None, WriteRisk.DESTRUCTIVE, execution_mode, approval_request_id, approval_token)
    def _by_id(self, template: str, value: int, name: str) -> dict[str, object]:
        if not self._valid_id(value, name): return failure(ErrorCode.VALIDATION_ERROR, f"{name} must be a positive integer")
        return self._get(template.format(id=value))
    @staticmethod
    def _valid_id(value: object, name: str) -> bool: return isinstance(value, int) and not isinstance(value, bool) and value > 0
    @staticmethod
    def _valid_tag_id(value: object) -> bool:
        return isinstance(value, str) and bool(value) and value not in {".", ".."} and not any(char in "/?#\\%" or char.isspace() or ord(char) < 32 or ord(char) == 127 for char in value)
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
            if not isinstance(normalized[name], str) or (name == "title" and not normalized[name].strip()) or (name == "details" and not self._valid_html(normalized[name])): return None
            normalized[name] = normalized[name].strip()
        if "topic_id" in normalized and not self._valid_id(normalized["topic_id"], "topic_id"): return None
        if "status" in normalized and normalized["status"] not in {"planned", "not_planned", "answered", "completed"}: return None
        if any(name in normalized and not isinstance(normalized[name], bool) for name in {"closed", "featured", "pinned"}): return None
        if "content_tag_ids" in normalized and (not isinstance(normalized["content_tag_ids"], list) or not all(self._valid_id(value, "content_tag_id") or self._valid_tag_id(value) for value in normalized["content_tag_ids"])): return None
        return {"post": normalized}
    def _comment_payload(self, comment: dict[str, object]) -> dict[str, object] | None:
        if not isinstance(comment, dict) or not comment or set(comment) - {"body", "official"}: return None
        normalized = dict(comment)
        if "body" in normalized and (not isinstance(normalized["body"], str) or not normalized["body"].strip() or not self._valid_html(normalized["body"])): return None
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
    def _badge_payload(self, badge: object) -> dict[str, object] | None:
        if not isinstance(badge, dict) or not badge or set(badge) - {"badge_category_id", "name", "description", "icon_upload_id"}: return None
        normalized = dict(badge)
        if "badge_category_id" in normalized and not self._valid_tag_id(normalized["badge_category_id"]): return None
        if "name" in normalized and (not isinstance(normalized["name"], str) or not normalized["name"].strip()): return None
        if "name" in normalized: normalized["name"] = normalized["name"].strip()
        if "description" in normalized and not isinstance(normalized["description"], str): return None
        if "icon_upload_id" in normalized and normalized["icon_upload_id"] is not None and not self._valid_tag_id(normalized["icon_upload_id"]): return None
        return {"badge": normalized}
    def _valid_html(self, value: str) -> bool:
        validator = _CommunityHTMLValidator(self._settings.subdomain if self._settings else None)
        try: validator.feed(value); validator.close()
        except (AssertionError, ValueError): return False
        return validator.valid and not validator.stack
    def _load_user_image(self, image_path: object, content_type: object, brand_id: object, *, allowed_content_types: set[str] | None = None) -> tuple[dict[str, object], BinaryIO] | dict[str, object]:
        if not isinstance(image_path, str) or not image_path or content_type not in (allowed_content_types or {"image/jpeg", "image/png", "image/gif"}) or not self._valid_id(brand_id, "brand_id"): return failure(ErrorCode.VALIDATION_ERROR, "valid image_path, content_type, and brand_id are required")
        if self._settings is None or self._settings.upload_root is None: return failure(ErrorCode.NOT_CONFIGURED, "ZENDESK_UPLOAD_ROOT is required for local image uploads")
        if not _SECURE_UPLOAD_OPEN: return failure(ErrorCode.UNSUPPORTED, "secure local image uploads are not supported on this platform")
        content = None
        try:
            root = self._settings.upload_root.resolve(strict=True)
            if not root.is_dir(): raise ValueError
            candidate = Path(image_path).expanduser()
            candidate = candidate if candidate.is_absolute() else root / candidate
            relative = candidate.relative_to(root)
            if not relative.parts or ".." in relative.parts: raise ValueError
            with ExitStack() as opened:
                directory = os.open(root.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                opened.callback(os.close, directory)
                for part in (*root.parts[1:], *relative.parts[:-1]):
                    directory = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
                    opened.callback(os.close, directory)
                descriptor = os.open(relative.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
                opened.callback(os.close, descriptor)
                if not stat.S_ISREG(os.fstat(descriptor).st_mode): raise ValueError
                content = TemporaryFile(mode="w+b")
                size = 0; digest = hashlib.sha256()
                with os.fdopen(descriptor, "rb", closefd=False) as source:
                    while chunk := source.read(65536):
                        size += len(chunk)
                        if size > 2_000_000: raise ValueError
                        digest.update(chunk); content.write(chunk)
                content.seek(0)
        except (OSError, ValueError):
            if content is not None: content.close()
            return failure(ErrorCode.VALIDATION_ERROR, "image_path must be a regular non-symlink file under ZENDESK_UPLOAD_ROOT and at most 2 MB")
        return ({"filename": relative.name, "content_type": content_type, "file_size": size, "sha256": digest.hexdigest(), "brand_id": str(brand_id)}, content)
    @staticmethod
    def _nested_data(result: dict[str, object], key: str) -> dict[str, object] | None:
        data = result.get("data") if isinstance(result, dict) else None
        value = data.get(key) if isinstance(data, dict) else None
        return value if isinstance(value, dict) else None
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
        if WriteRisk.IMPERSONATION in risks and tool in ("zendesk_create_community_post", "zendesk_create_community_comment"):
            identity = self._get("/api/v2/users/me.json")
            if not identity.get("ok"): return identity
            user = self._nested_data(identity, "user")
            if user is None or not self._valid_id(user.get("id"), "user_id") or user.get("role") != "admin":
                return failure(ErrorCode.PERMISSION_DENIED, "Community author and creation-time impersonation requires an administrator")
        written = self._client.request(method, path, json_body=json_body)
        if not written.get("ok") or method not in {"POST", "PUT"}: return written
        for key, field in (("post", "details"), ("comment", "body")):
            if not isinstance(json_body, dict) or not isinstance(json_body.get(key), dict) or field not in json_body[key]: continue
            resource = self._nested_data(written, key)
            identifier = approval_payload.get(f"{key}_id") if method == "PUT" else resource.get("id") if resource else None
            incomplete = failure(ErrorCode.PARTIAL_SUCCESS, "content write succeeded but saved HTML could not be verified; inspect the resource before retrying", operation_state="applied", request_id=written.get("request_id"), details={"read_back_verified": False})
            if not self._valid_id(identifier, "id"): return incomplete
            read_path = path if method == "PUT" else f"{path.removesuffix('.json')}/{identifier}.json"
            incomplete["error"]["details"].update({"resource_id": identifier, "resource_path": read_path})
            observed = self._get(read_path)
            saved = self._nested_data(observed, key) if observed.get("ok") else None
            if saved is None or not self._valid_id(saved.get("id"), "id") or saved["id"] != identifier or not isinstance(saved.get(field), str): return incomplete
            return success({**written["data"], key: saved, "read_back_verified": True}, request_id=written.get("request_id"), operation_state="applied")
        return written
    def _get(self, path: str, params: dict[str, str] | None = None) -> dict[str, object]:
        if self._client is None: return failure(ErrorCode.NOT_CONFIGURED, "Zendesk is not configured")
        result = self._client.get(path, params=params)
        if not result.get("ok") or not isinstance(result.get("data"), dict): return result
        data = {key: [_content_display(item) for item in value] if isinstance(value, list) else _content_display(value) for key, value in result["data"].items()}
        return {**result, "data": data}
    def _notification_metadata(self, path: str, key: str) -> dict[str, object]:
        resource = self._nested_data(self._get(path), key)
        followers = resource.get("follower_count") if resource is not None else None
        return {"follower_count": followers} if isinstance(followers, int) and not isinstance(followers, bool) and followers >= 0 else {"recipient_count_unknown": True}
    def _cursor_page(self, path: str, params: dict[str, str], item_key: str) -> dict[str, object]:
        result = self._get(path, params)
        if not result.get("ok"): return result
        data = result.get("data"); items = data.get(item_key) if isinstance(data, dict) else None; meta = data.get("meta", {}) if isinstance(data, dict) else None
        if not isinstance(items, list) or not isinstance(meta, dict): return failure(ErrorCode.UPSTREAM_ERROR, "Zendesk returned an invalid cursor page")
        has_more = bool(meta.get("has_more"))
        next_cursor = meta.get("after_cursor") if has_more else None
        if has_more and (not isinstance(next_cursor, str) or not next_cursor or next_cursor == params.get("page[after]")):
            return failure(ErrorCode.UPSTREAM_ERROR, "Zendesk returned an invalid continuation cursor")
        return {"ok": True, "items": items, "has_more": has_more, "next_cursor": next_cursor, "truncated": False}
    def _find_user_subscription(self, path: str, followed_id: int) -> dict[str, object]:
        after: str | None = None; seen: set[str] = set(); scanned = 0
        while scanned < 1000:
            page_size = min(100, 1000 - scanned)
            params = {"type": "followings", "page[size]": str(page_size)}
            if after is not None: params["page[after]"] = after
            result = self._get(path, params)
            if not result.get("ok"): return result
            data = result.get("data"); subscriptions = data.get("user_subscriptions") if isinstance(data, dict) else None; meta = data.get("meta", {}) if isinstance(data, dict) else None
            if not isinstance(subscriptions, list) or not isinstance(meta, dict) or len(subscriptions) > page_size or any(not isinstance(subscription, dict) for subscription in subscriptions) or not isinstance(meta.get("has_more"), bool): return failure(ErrorCode.UPSTREAM_ERROR, "Zendesk returned an invalid user subscription page")
            scanned += len(subscriptions)
            if any(not self._valid_id(subscription.get("followed_id"), "followed_id") for subscription in subscriptions):
                return failure(ErrorCode.UPSTREAM_ERROR, "Zendesk returned an invalid followed user ID")
            found = next((subscription for subscription in subscriptions if isinstance(subscription, dict) and subscription.get("followed_id") == followed_id), None)
            if found is not None: return success({"subscription": found})
            if not meta.get("has_more"): return success({"subscription": None})
            if not subscriptions: return failure(ErrorCode.UPSTREAM_ERROR, "empty intermediate page prevents determining the current subscription")
            next_after = meta.get("after_cursor")
            if not isinstance(next_after, str) or not next_after or next_after in seen: return failure(ErrorCode.UPSTREAM_ERROR, "Zendesk returned an invalid user subscription cursor")
            seen.add(next_after); after = next_after
        return failure(ErrorCode.UPSTREAM_ERROR, "too many user subscriptions to safely determine the current setting")


def _valid_timestamp(value: object) -> bool:
    if value is None: return True
    if not isinstance(value, str): return False
    try: return datetime.fromisoformat(value.replace("Z", "+00:00")).tzinfo is not None
    except ValueError: return False


def _encode_vote_cursor(user_id: int | str, after: str | None, skip: int) -> str:
    raw = json.dumps({"user_id": str(user_id), "after": after, "skip": skip}, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode_vote_cursor(cursor: object, user_id: int | str) -> dict[str, object] | None:
    if cursor is None: return {"after": None, "skip": 0}
    if not isinstance(cursor, str) or not cursor: return None
    try: value = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
    except (ValueError, UnicodeDecodeError): return None
    if not isinstance(value, dict) or value.get("user_id") != str(user_id) or (value.get("after") is not None and (not isinstance(value.get("after"), str) or not value["after"])) or not isinstance(value.get("skip"), int) or isinstance(value.get("skip"), bool) or value["skip"] < 0: return None
    return {"after": value["after"], "skip": value["skip"]}
