"""Zendesk Guide, Help Center, and CSAT tools."""

from __future__ import annotations

import re
from typing import Protocol

from ..approvals import ApprovalStore
from ..config import Settings
from ..contracts import ErrorCode, failure, success
from ..write_policy import WriteRisk, check_write_permission

_LOCALE = re.compile(r"[a-z]{2,3}(?:-[a-z0-9]+)*$")


class GuideClient(Protocol):
    def get(self, path: str, *, params: dict[str, str] | None = None) -> dict[str, object]: ...
    def request(self, method: str, path: str, *, json_body: dict[str, object] | None = None) -> dict[str, object]: ...


class GuideTools:
    def __init__(self, client: GuideClient | None, settings: Settings | None = None, approvals: ApprovalStore | None = None) -> None: self._client, self._settings, self._approvals = client, settings, approvals
    def list_locales(self) -> dict[str, object]: return self._get("/api/v2/help_center/locales.json")
    def list_categories(self) -> dict[str, object]: return self._get("/api/v2/help_center/categories.json")
    def list_sections(self) -> dict[str, object]: return self._get("/api/v2/help_center/sections.json")
    def get_satisfaction_ratings(self) -> dict[str, object]: return self._get("/api/v2/satisfaction_ratings.json")
    def list_permission_groups(self) -> dict[str, object]: return self._get("/api/v2/guide/permission_groups.json")
    def list_user_segments(self, *, built_in: bool | None = None, applicable: bool = False) -> dict[str, object]:
        if not isinstance(applicable, bool) or (built_in is not None and not isinstance(built_in, bool)): return failure(ErrorCode.VALIDATION_ERROR, "built_in and applicable must be booleans")
        return self._get("/api/v2/help_center/user_segments/applicable.json" if applicable else "/api/v2/help_center/user_segments.json", {"built_in": str(built_in).lower()} if built_in is not None else None)
    def search_articles(self, query: str) -> dict[str, object]:
        if not isinstance(query, str) or not query.strip(): return failure(ErrorCode.VALIDATION_ERROR, "query must be a non-empty string")
        return self._get("/api/v2/help_center/articles/search.json", {"query": query.strip()})
    def get_article(self, article_id: int) -> dict[str, object]:
        if not self._valid_id(article_id): return failure(ErrorCode.VALIDATION_ERROR, "article_id must be a positive integer")
        return self._get(f"/api/v2/help_center/articles/{article_id}.json")
    def create_article(self, section_id: int, locale: str, title: str, body: str, *, labels: list[str] | None = None, position: int | None = None, permission_group_id: int | None = None, user_segment_id: int | None = None, draft: bool = True, notify_subscribers: bool = False, execution_mode: str = "preview", approval_request_id: str | None = None, approval_token: str | None = None) -> dict[str, object]:
        payload = self._article_payload(section_id, locale, title, body, labels, position, permission_group_id, user_segment_id, draft, notify_subscribers)
        if payload is None: return failure(ErrorCode.VALIDATION_ERROR, "valid draft article fields are required; publish with zendesk_publish_help_center_article")
        risks = (WriteRisk.STANDARD, WriteRisk.PUBLIC) if notify_subscribers else (WriteRisk.STANDARD,)
        if execution_mode == "preview":
            if self._approvals is None: return failure(ErrorCode.NOT_CONFIGURED, "Zendesk approval store is not configured")
            return success({"approval_request_id": self._approvals.create("zendesk_create_help_center_article", payload), "execution_mode": "preview", "outbound_write": False, **{risk.value: True for risk in risks}})
        if execution_mode != "apply": return failure(ErrorCode.VALIDATION_ERROR, "execution_mode must be preview or apply")
        if self._settings is None: return failure(ErrorCode.WRITE_DISABLED, "Zendesk writes are disabled")
        for risk in risks:
            if (blocked := check_write_permission(self._settings, risk)) is not None: return blocked
        locale_check = self._validate_active_locale(locale)
        if locale_check is not None: return locale_check
        if self._approvals is None or not isinstance(approval_request_id, str) or not isinstance(approval_token, str) or not self._approvals.consume(approval_request_id, "zendesk_create_help_center_article", payload, approval_token): return failure(ErrorCode.APPROVAL_REQUIRED, "a matching local approval is required")
        if self._client is None: return failure(ErrorCode.NOT_CONFIGURED, "Zendesk is not configured")
        return self._client.request("POST", f"/api/v2/help_center/sections/{section_id}/articles.json", json_body={"article": payload["article"], "notify_subscribers": notify_subscribers})
    def upsert_article_translation(self, article_id: int, locale: str, *, title: str | None = None, body: str | None = None, draft: bool = True, execution_mode: str = "preview", approval_request_id: str | None = None, approval_token: str | None = None) -> dict[str, object]:
        if not self._valid_id(article_id) or not isinstance(locale, str) or not _LOCALE.fullmatch(locale) or not isinstance(draft, bool) or not draft or (title is not None and (not isinstance(title, str) or not title.strip())) or (body is not None and not isinstance(body, str)): return failure(ErrorCode.VALIDATION_ERROR, "valid draft translation fields are required; publish with zendesk_publish_help_center_article")
        current = self._get_translation(article_id, locale)
        if isinstance(current, dict) and not current.get("ok", True):
            if current.get("error", {}).get("code") != ErrorCode.NOT_FOUND.value: return current
            current = None
        if current is not None and body is not None: return failure(ErrorCode.ARTICLE_BODY_REPLACE_REQUIRED, "existing translation bodies require zendesk_replace_article_translation_body")
        if current is None:
            if title is None or body is None: return failure(ErrorCode.VALIDATION_ERROR, "new translations require title and body")
            operation, translation = "create", {"locale": locale, "title": title.strip(), "body": body, "draft": True}
        else:
            if title is None: return success({"translation": current, "outbound_write": False})
            operation, translation = "update", {"title": title.strip(), "draft": True}
        payload = {"article_id": article_id, "locale": locale, "operation": operation, "translation": translation}
        if execution_mode == "preview":
            if self._approvals is None: return failure(ErrorCode.NOT_CONFIGURED, "Zendesk approval store is not configured")
            return success({"approval_request_id": self._approvals.create("zendesk_upsert_article_translation", payload), "execution_mode": "preview", "standard": True, "outbound_write": False})
        if execution_mode != "apply": return failure(ErrorCode.VALIDATION_ERROR, "execution_mode must be preview or apply")
        if self._settings is None or (blocked := check_write_permission(self._settings, WriteRisk.STANDARD)) is not None: return blocked or failure(ErrorCode.WRITE_DISABLED, "Zendesk writes are disabled")
        locale_check = self._validate_active_locale(locale)
        if locale_check is not None: return locale_check
        if self._approvals is None or not isinstance(approval_request_id, str) or not isinstance(approval_token, str) or not self._approvals.consume(approval_request_id, "zendesk_upsert_article_translation", payload, approval_token): return failure(ErrorCode.APPROVAL_REQUIRED, "a matching local approval is required")
        if self._client is None: return failure(ErrorCode.NOT_CONFIGURED, "Zendesk is not configured")
        path = f"/api/v2/help_center/articles/{article_id}/translations.json" if operation == "create" else f"/api/v2/help_center/articles/{article_id}/translations/{locale}.json"
        return self._client.request("POST" if operation == "create" else "PUT", path, json_body={"translation": translation})
    def _article_payload(self, section_id: object, locale: object, title: object, body: object, labels: object, position: object, permission_group_id: object, user_segment_id: object, draft: object, notify_subscribers: object) -> dict[str, object] | None:
        if not self._valid_id(section_id) or not isinstance(locale, str) or not _LOCALE.fullmatch(locale) or not isinstance(title, str) or not title.strip() or not isinstance(body, str) or not isinstance(draft, bool) or not draft or not isinstance(notify_subscribers, bool): return None
        article: dict[str, object] = {"title": title.strip(), "body": body, "locale": locale, "draft": True}
        if labels is not None:
            if not isinstance(labels, list) or not all(isinstance(label, str) and label.strip() for label in labels): return None
            article["label_names"] = [label.strip() for label in labels]
        if position is not None:
            if not isinstance(position, int) or isinstance(position, bool) or position < 0: return None
            article["position"] = position
        for name, value in (("permission_group_id", permission_group_id), ("user_segment_id", user_segment_id)):
            if value is not None:
                if not self._valid_id(value): return None
                article[name] = value
        return {"section_id": section_id, "article": article, "notify_subscribers": notify_subscribers}
    def _validate_active_locale(self, locale: str) -> dict[str, object] | None:
        result = self.list_locales(); data = result.get("data") if isinstance(result, dict) else None; locales = data.get("locales") if isinstance(data, dict) else None
        if not result.get("ok"): return result
        return None if isinstance(locales, list) and locale in locales else failure(ErrorCode.VALIDATION_ERROR, "locale is not enabled for this Help Center")
    def _get_translation(self, article_id: int, locale: str) -> dict[str, object] | None:
        result = self._get(f"/api/v2/help_center/articles/{article_id}/translations/{locale}.json")
        if not result.get("ok"): return result
        data = result.get("data"); translation = data.get("translation") if isinstance(data, dict) else None
        return translation if isinstance(translation, dict) else failure(ErrorCode.UPSTREAM_ERROR, "Zendesk returned an invalid translation response")
    @staticmethod
    def _valid_id(value: object) -> bool: return isinstance(value, int) and not isinstance(value, bool) and value > 0
    def _get(self, path: str, params: dict[str, str] | None = None) -> dict[str, object]:
        if self._client is None: return failure(ErrorCode.NOT_CONFIGURED, "Zendesk is not configured")
        return self._client.get(path, params=params)
