"""Zendesk Guide, Help Center, and CSAT tools."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Protocol

from ..approvals import ApprovalStore
from ..config import Settings
from ..contracts import ErrorCode, failure, success
from ..write_policy import WriteRisk, check_write_permission
from .tickets import _cache_ticket_export, _clean_export_cache, _serialize_ticket_export

_LOCALE = re.compile(r"[a-z]{2,3}(?:-[a-z0-9]+)*$")
_CSAT_SCORES = {"offered", "unoffered", "received", "received_with_comment", "received_without_comment", "good", "good_with_comment", "good_without_comment", "bad", "bad_with_comment", "bad_without_comment"}


class GuideClient(Protocol):
    def get(self, path: str, *, params: dict[str, str] | None = None) -> dict[str, object]: ...
    def get_for_subdomain(self, subdomain: str, path: str, *, params: dict[str, str] | None = None) -> dict[str, object]: ...
    def request(self, method: str, path: str, *, json_body: dict[str, object] | None = None) -> dict[str, object]: ...


class GuideTools:
    def __init__(self, client: GuideClient | None, settings: Settings | None = None, approvals: ApprovalStore | None = None, brand_subdomain: str | None = None) -> None: self._client, self._settings, self._approvals, self._brand_subdomain = client, settings, approvals, brand_subdomain
    def list_locales(self, *, brand_id: int | None = None) -> dict[str, object]:
        scoped = self._for_brand(brand_id); return scoped if isinstance(scoped, dict) else scoped._get("/api/v2/help_center/locales.json")
    def list_categories(self, *, brand_id: int | None = None) -> dict[str, object]:
        scoped = self._for_brand(brand_id); return scoped if isinstance(scoped, dict) else scoped._get("/api/v2/help_center/categories.json")
    def list_sections(self, *, brand_id: int | None = None) -> dict[str, object]:
        scoped = self._for_brand(brand_id); return scoped if isinstance(scoped, dict) else scoped._get("/api/v2/help_center/sections.json")
    def get_satisfaction_ratings(self) -> dict[str, object]: return self._get("/api/v2/satisfaction_ratings.json")
    def list_csat(self, backend: str = "auto", *, score: str | None = None, ticket_id: int | None = None, responder_ids: list[int] | None = None, created_at_start: str | None = None, created_at_end: str | None = None) -> dict[str, object]:
        if backend not in {"auto", "legacy", "survey"}: return failure(ErrorCode.VALIDATION_ERROR, "backend must be auto, legacy, or survey")
        if backend == "auto": backend = "legacy" if score is not None else "survey"
        start = _epoch(created_at_start, milliseconds=backend == "survey"); end = _epoch(created_at_end, milliseconds=backend == "survey")
        if (created_at_start is not None and start is None) or (created_at_end is not None and end is None) or (start is not None and end is not None and start > end): return failure(ErrorCode.VALIDATION_ERROR, "CSAT dates must be ordered ISO-8601 timestamps with timezone")
        if backend == "legacy":
            if ticket_id is not None or responder_ids is not None or (score is not None and score not in _CSAT_SCORES): return failure(ErrorCode.VALIDATION_ERROR, "legacy CSAT accepts only a valid score and date range")
            return self._get("/api/v2/satisfaction_ratings.json", {key: value for key, value in {"score": score, "start_time": str(start) if start is not None else None, "end_time": str(end) if end is not None else None}.items() if value is not None} or None)
        if score is not None or (ticket_id is not None and not self._valid_id(ticket_id)) or (responder_ids is not None and (not isinstance(responder_ids, list) or not responder_ids or any(not self._valid_id(value) for value in responder_ids))): return failure(ErrorCode.VALIDATION_ERROR, "survey CSAT accepts ticket_id, responder_ids, and date range only")
        return self._get("/api/v2/guide/survey_responses.json", {key: value for key, value in {"filter[subject_zrns]": f"zen:ticket:{ticket_id}" if ticket_id is not None else None, "filter[responder_ids]": ",".join(str(value) for value in responder_ids) if responder_ids is not None else None, "filter[created_at_start]": str(start) if start is not None else None, "filter[created_at_end]": str(end) if end is not None else None}.items() if value is not None} or None)
    def export_csat(self, backend: str = "auto", *, score: str | None = None, ticket_id: int | None = None, responder_ids: list[int] | None = None, created_at_start: str | None = None, created_at_end: str | None = None, output_format: str = "json") -> dict[str, object]:
        if output_format not in {"json", "csv"}: return failure(ErrorCode.VALIDATION_ERROR, "output_format must be json or csv")
        result = self.list_csat(backend, score=score, ticket_id=ticket_id, responder_ids=responder_ids, created_at_start=created_at_start, created_at_end=created_at_end)
        if not result.get("ok"): return result
        data = result.get("data")
        items = data.get("satisfaction_ratings") if isinstance(data, dict) else None
        if not isinstance(items, list): items = data.get("survey_responses") if isinstance(data, dict) else None
        if not isinstance(items, list): return failure(ErrorCode.UPSTREAM_ERROR, "Zendesk returned an invalid CSAT export response")
        if self._settings is None or self._settings.attachment_cache_root is None: return failure(ErrorCode.NOT_CONFIGURED, "Zendesk export cache is not configured")
        root = self._settings.attachment_cache_root.parent / "exports"
        _clean_export_cache(root)
        cached = _cache_ticket_export(root, output_format, _serialize_ticket_export(items, output_format), filename_prefix="csat-export")
        if not cached.get("ok"): return cached
        return success({"format": output_format, "item_count": len(items), **cached["data"]})
    def list_permission_groups(self) -> dict[str, object]: return self._get("/api/v2/guide/permission_groups.json")
    def list_user_segments(self, *, built_in: bool | None = None, applicable: bool = False) -> dict[str, object]:
        if not isinstance(applicable, bool) or (built_in is not None and not isinstance(built_in, bool)): return failure(ErrorCode.VALIDATION_ERROR, "built_in and applicable must be booleans")
        return self._get("/api/v2/help_center/user_segments/applicable.json" if applicable else "/api/v2/help_center/user_segments.json", {"built_in": str(built_in).lower()} if built_in is not None else None)
    def search_articles(self, query: str) -> dict[str, object]:
        if not isinstance(query, str) or not query.strip(): return failure(ErrorCode.VALIDATION_ERROR, "query must be a non-empty string")
        return self._get("/api/v2/help_center/articles/search.json", {"query": query.strip()})
    def export_articles(self, locale: str, max_articles: int = 100000, *, brand_id: int | None = None) -> dict[str, object]:
        scoped = self._for_brand(brand_id)
        if isinstance(scoped, dict): return scoped
        if scoped is not self: return scoped.export_articles(locale, max_articles)
        if not isinstance(locale, str) or not _LOCALE.fullmatch(locale) or not isinstance(max_articles, int) or isinstance(max_articles, bool) or not 1 <= max_articles <= 100000: return failure(ErrorCode.VALIDATION_ERROR, "locale and max_articles must be valid")
        articles: list[object] = []; cursor: str | None = None; seen: set[str] = set()
        while len(articles) < max_articles:
            params = {"page[size]": str(min(100, max_articles - len(articles)))}
            if cursor is not None: params["page[after]"] = cursor
            result = self._get(f"/api/v2/help_center/{locale}/articles.json", params)
            if not result.get("ok"): return result
            data = result.get("data"); page = data.get("articles") if isinstance(data, dict) else None; meta = data.get("meta") if isinstance(data, dict) else None
            if not isinstance(page, list) or not isinstance(meta, dict): return failure(ErrorCode.UPSTREAM_ERROR, "Zendesk returned an invalid article export page")
            articles.extend(page)
            if not meta.get("has_more"): return success({"articles": articles, "truncated": False})
            cursor = meta.get("after_cursor")
            if not isinstance(cursor, str) or not cursor or cursor in seen: return failure(ErrorCode.UPSTREAM_ERROR, "Zendesk returned an invalid article export cursor")
            seen.add(cursor)
        return success({"articles": articles, "truncated": True})
    def export_article_artifact(self, locale: str, max_articles: int = 100000, *, brand_id: int | None = None, output_format: str = "json") -> dict[str, object]:
        if output_format not in {"json", "csv"}: return failure(ErrorCode.VALIDATION_ERROR, "output_format must be json or csv")
        result = self.export_articles(locale, max_articles, brand_id=brand_id)
        if not result.get("ok"): return result
        data = result.get("data")
        articles = data.get("articles") if isinstance(data, dict) else None
        if not isinstance(articles, list): return failure(ErrorCode.UPSTREAM_ERROR, "Zendesk returned an invalid article export response")
        if self._settings is None or self._settings.attachment_cache_root is None: return failure(ErrorCode.NOT_CONFIGURED, "Zendesk export cache is not configured")
        root = self._settings.attachment_cache_root.parent / "exports"
        _clean_export_cache(root)
        cached = _cache_ticket_export(root, output_format, _serialize_ticket_export(articles, output_format), filename_prefix="help-center-export")
        if not cached.get("ok"): return cached
        return success({"format": output_format, "item_count": len(articles), "truncated": data.get("truncated", False), **cached["data"]})
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
            risks = (WriteRisk.STANDARD,)
        else:
            if title is None: return success({"translation": current, "outbound_write": False})
            operation, translation = "update", {"title": title.strip(), "draft": True}
            risks = (WriteRisk.STANDARD, WriteRisk.PUBLIC, WriteRisk.DESTRUCTIVE) if current.get("draft") is not True else (WriteRisk.STANDARD,)
        payload = {"article_id": article_id, "locale": locale, "operation": operation, "translation": translation}
        if execution_mode == "preview":
            if self._approvals is None: return failure(ErrorCode.NOT_CONFIGURED, "Zendesk approval store is not configured")
            return success({"approval_request_id": self._approvals.create("zendesk_upsert_article_translation", payload), "execution_mode": "preview", "outbound_write": False, **{risk.value: True for risk in risks}})
        if execution_mode != "apply": return failure(ErrorCode.VALIDATION_ERROR, "execution_mode must be preview or apply")
        if self._settings is None: return failure(ErrorCode.WRITE_DISABLED, "Zendesk writes are disabled")
        for risk in risks:
            if (blocked := check_write_permission(self._settings, risk)) is not None: return blocked
        locale_check = self._validate_active_locale(locale)
        if locale_check is not None: return locale_check
        if self._approvals is None or not isinstance(approval_request_id, str) or not isinstance(approval_token, str) or not self._approvals.consume(approval_request_id, "zendesk_upsert_article_translation", payload, approval_token): return failure(ErrorCode.APPROVAL_REQUIRED, "a matching local approval is required")
        if self._client is None: return failure(ErrorCode.NOT_CONFIGURED, "Zendesk is not configured")
        path = f"/api/v2/help_center/articles/{article_id}/translations.json" if operation == "create" else f"/api/v2/help_center/articles/{article_id}/translations/{locale}.json"
        return self._client.request("POST" if operation == "create" else "PUT", path, json_body={"translation": translation})
    def replace_article_translation_body(self, article_id: int, locale: str, body: str, *, execution_mode: str = "preview", approval_request_id: str | None = None, approval_token: str | None = None) -> dict[str, object]:
        if not self._valid_id(article_id) or not isinstance(locale, str) or not _LOCALE.fullmatch(locale) or not isinstance(body, str): return failure(ErrorCode.VALIDATION_ERROR, "valid article_id, locale, and body are required")
        current = self._get_translation(article_id, locale)
        if current is None or not current.get("ok", True): return current or failure(ErrorCode.NOT_FOUND, "translation not found")
        risks = (WriteRisk.DESTRUCTIVE, WriteRisk.PUBLIC) if current.get("draft") is not True else (WriteRisk.DESTRUCTIVE,)
        payload = {"article_id": article_id, "locale": locale, "body": body}
        if execution_mode == "preview":
            if self._approvals is None: return failure(ErrorCode.NOT_CONFIGURED, "Zendesk approval store is not configured")
            return success({"approval_request_id": self._approvals.create("zendesk_replace_article_translation_body", payload), "execution_mode": "preview", "content_block_replacement_warning": True, "outbound_write": False, **{risk.value: True for risk in risks}})
        if execution_mode != "apply": return failure(ErrorCode.VALIDATION_ERROR, "execution_mode must be preview or apply")
        if self._settings is None: return failure(ErrorCode.WRITE_DISABLED, "Zendesk writes are disabled")
        for risk in risks:
            if (blocked := check_write_permission(self._settings, risk)) is not None: return blocked
        locale_check = self._validate_active_locale(locale)
        if locale_check is not None: return locale_check
        if self._approvals is None or not isinstance(approval_request_id, str) or not isinstance(approval_token, str) or not self._approvals.consume(approval_request_id, "zendesk_replace_article_translation_body", payload, approval_token): return failure(ErrorCode.APPROVAL_REQUIRED, "a matching local approval is required")
        if self._client is None: return failure(ErrorCode.NOT_CONFIGURED, "Zendesk is not configured")
        return self._client.request("PUT", f"/api/v2/help_center/articles/{article_id}/translations/{locale}.json", json_body={"translation": {"body": body}})
    def publish_article(self, article_id: int, locale: str, *, execution_mode: str = "preview", approval_request_id: str | None = None, approval_token: str | None = None) -> dict[str, object]:
        if not self._valid_id(article_id) or not isinstance(locale, str) or not _LOCALE.fullmatch(locale): return failure(ErrorCode.VALIDATION_ERROR, "valid article_id and locale are required")
        current = self._get_translation(article_id, locale)
        if current is None or not current.get("ok", True): return current or failure(ErrorCode.NOT_FOUND, "translation not found")
        if current.get("draft") is False: return success({"translation": current, "outbound_write": False})
        payload = {"article_id": article_id, "locale": locale}
        if execution_mode == "preview":
            if self._approvals is None: return failure(ErrorCode.NOT_CONFIGURED, "Zendesk approval store is not configured")
            return success({"approval_request_id": self._approvals.create("zendesk_publish_help_center_article", payload), "execution_mode": "preview", "public": True, "outbound_write": False})
        if execution_mode != "apply": return failure(ErrorCode.VALIDATION_ERROR, "execution_mode must be preview or apply")
        if self._settings is None or (blocked := check_write_permission(self._settings, WriteRisk.PUBLIC)) is not None: return blocked or failure(ErrorCode.WRITE_DISABLED, "Zendesk public writes are disabled")
        locale_check = self._validate_active_locale(locale)
        if locale_check is not None: return locale_check
        if self._approvals is None or not isinstance(approval_request_id, str) or not isinstance(approval_token, str) or not self._approvals.consume(approval_request_id, "zendesk_publish_help_center_article", payload, approval_token): return failure(ErrorCode.APPROVAL_REQUIRED, "a matching local approval is required")
        if self._client is None: return failure(ErrorCode.NOT_CONFIGURED, "Zendesk is not configured")
        updated = self._client.request("PUT", f"/api/v2/help_center/articles/{article_id}/translations/{locale}.json", json_body={"translation": {"draft": False}})
        if not updated.get("ok"): return updated
        read_back = self._get_translation(article_id, locale)
        return success({"translation": read_back}) if isinstance(read_back, dict) and "ok" not in read_back else read_back
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
    def _for_brand(self, brand_id: int | None) -> "GuideTools | dict[str, object]":
        if brand_id is None: return self
        if not self._valid_id(brand_id): return failure(ErrorCode.VALIDATION_ERROR, "brand_id must be a positive integer")
        result = self._get(f"/api/v2/brands/{brand_id}.json")
        if not result.get("ok"): return result
        data = result.get("data"); brand = data.get("brand") if isinstance(data, dict) else None
        subdomain = brand.get("subdomain") if isinstance(brand, dict) else None
        if not isinstance(subdomain, str) or not subdomain or brand.get("has_help_center") is not True: return failure(ErrorCode.UNSUPPORTED, "brand does not have an enabled Help Center")
        return GuideTools(self._client, self._settings, self._approvals, subdomain)
    def _get(self, path: str, params: dict[str, str] | None = None) -> dict[str, object]:
        if self._client is None: return failure(ErrorCode.NOT_CONFIGURED, "Zendesk is not configured")
        if self._brand_subdomain is not None:
            getter = getattr(self._client, "get_for_subdomain", None)
            if not callable(getter): return failure(ErrorCode.UNSUPPORTED, "Zendesk client does not support brand-scoped Help Center reads")
            return getter(self._brand_subdomain, path, params=params)
        return self._client.get(path, params=params)


def _epoch(value: str | None, *, milliseconds: bool) -> int | None:
    if value is None: return None
    if not isinstance(value, str): return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None: return None
        epoch = int(parsed.timestamp())
        return epoch * 1000 if milliseconds else epoch
    except ValueError: return None
