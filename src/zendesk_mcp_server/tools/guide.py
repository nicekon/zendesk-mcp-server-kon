"""Zendesk Guide, Help Center, and CSAT tools."""

from __future__ import annotations

import re
import json
import tempfile
from datetime import datetime
from html.parser import HTMLParser
from typing import Callable, Protocol
from urllib.parse import quote, urlsplit

from ..approvals import ApprovalStore
from ..config import Settings
from ..contracts import ErrorCode, failure, success
from ..pagination import collect_complete_cursor, collect_cursor, collect_offset
from ..write_policy import WriteRisk, check_write_permission
from .tickets import _cache_ticket_export, _clean_export_cache, _stream_ticket_export

_LOCALE = re.compile(r"[a-z]{2,3}(?:-[a-z0-9]+)*$")
_CSAT_SCORES = {"offered", "unoffered", "received", "received_with_comment", "received_without_comment", "good", "good_with_comment", "good_without_comment", "bad", "bad_with_comment", "bad_without_comment"}
_EMBEDDABLE_IMAGE_TYPES = {"image/png", "image/jpeg", "image/gif", "image/webp"}
_MAX_EMBEDDED_IMAGE_BYTES = 20 * 1024 * 1024


class GuideClient(Protocol):
    def get(self, path: str, *, params: dict[str, str] | None = None) -> dict[str, object]: ...
    def get_for_subdomain(self, subdomain: str, path: str, *, params: dict[str, str] | None = None) -> dict[str, object]: ...
    def request(self, method: str, path: str, *, json_body: dict[str, object] | None = None) -> dict[str, object]: ...
    def request_for_subdomain(self, subdomain: str, method: str, path: str, *, json_body: dict[str, object] | None = None) -> dict[str, object]: ...
    def download_help_center_image(self, image_url: str, *, max_bytes: int, subdomain: str | None = None) -> dict[str, object]: ...


class GuideTools:
    def __init__(self, client: GuideClient | None, settings: Settings | None = None, approvals: ApprovalStore | None = None, brand_subdomain: str | None = None) -> None: self._client, self._settings, self._approvals, self._brand_subdomain = client, settings, approvals, brand_subdomain
    def list_locales(self, *, brand_id: int | None = None) -> dict[str, object]:
        scoped = self._for_brand(brand_id); return scoped if isinstance(scoped, dict) else scoped._get("/api/v2/help_center/locales.json")
    def list_categories(self, *, brand_id: int | None = None, locale: str | None = None, limit: int = 100, cursor: str | None = None) -> dict[str, object]:
        return self._list_navigation("categories", brand_id, locale, limit, cursor)
    def list_sections(self, *, brand_id: int | None = None, locale: str | None = None, limit: int = 100, cursor: str | None = None) -> dict[str, object]:
        return self._list_navigation("sections", brand_id, locale, limit, cursor)
    def _list_navigation(self, key: str, brand_id: int | None, locale: str | None, limit: int, cursor: str | None) -> dict[str, object]:
        if locale is not None and (not isinstance(locale, str) or not _LOCALE.fullmatch(locale)):
            return failure(ErrorCode.VALIDATION_ERROR, "locale must be a valid Help Center locale")
        scoped = self._for_brand(brand_id)
        if isinstance(scoped, dict): return scoped
        if locale is not None:
            invalid = scoped._validate_active_locale(locale)
            if invalid is not None: return invalid
        prefix = f"{locale}/" if locale is not None else ""
        return collect_cursor(scoped._get, f"/api/v2/help_center/{prefix}{key}.json", key, limit, cursor)
    def get_satisfaction_ratings(self, limit: int = 100, *, cursor: str | None = None) -> dict[str, object]:
        return self.list_csat("legacy", limit=limit, cursor=cursor)
    def list_csat(self, backend: str = "auto", *, score: str | None = None, ticket_id: int | None = None, responder_ids: list[int] | None = None, created_at_start: str | None = None, created_at_end: str | None = None, limit: int = 100, cursor: str | None = None) -> dict[str, object]:
        backend = self._resolve_csat_backend(backend)
        if isinstance(backend, dict): return backend
        key = "satisfaction_ratings" if backend == "legacy" else "survey_responses"
        def get_page(path, *, params):
            return self._csat_page(backend, score=score, ticket_id=ticket_id, responder_ids=responder_ids, created_at_start=created_at_start, created_at_end=created_at_end, page_size=int(params["page[size]"]), cursor=params.get("page[after]"))
        result = collect_cursor(get_page, "", key, limit, cursor)
        return {**result, "untrusted_user_content": True} if result.get("ok") else result

    def _resolve_csat_backend(self, backend: str) -> str | dict[str, object]:
        if backend in ("legacy", "survey"): return backend
        if backend != "auto": return failure(ErrorCode.VALIDATION_ERROR, "backend must be auto, legacy, or survey")
        result = self._get("/api/v2/account/settings.json")
        if not result.get("ok"): return result
        data = result.get("data")
        settings = data.get("settings") if isinstance(data, dict) else None
        features = settings.get("active_features") if isinstance(settings, dict) else None
        legacy = features.get("customer_satisfaction") if isinstance(features, dict) else None
        survey = features.get("customer_satisfaction_survey") if isinstance(features, dict) else None
        if type(legacy) is not bool or type(survey) is not bool:
            return failure(ErrorCode.UPSTREAM_ERROR, "Account settings did not identify CSAT features; specify legacy or survey explicitly")
        if legacy == survey:
            return failure(ErrorCode.UNSUPPORTED, "No unique active CSAT backend; specify legacy or survey explicitly for historical data")
        return "legacy" if legacy else "survey"

    def _csat_page(self, backend: str, *, score: str | None = None, ticket_id: int | None = None, responder_ids: list[int] | None = None, created_at_start: str | None = None, created_at_end: str | None = None, page_size: int | None = None, cursor: str | None = None) -> dict[str, object]:
        if (page_size is not None and (not isinstance(page_size, int) or isinstance(page_size, bool) or not 1 <= page_size <= 100)) or (cursor is not None and (not isinstance(cursor, str) or not cursor or page_size is None)): return failure(ErrorCode.VALIDATION_ERROR, "CSAT page size and cursor must be valid")
        pagination = {"page[size]": str(page_size)} if page_size is not None else {}
        if cursor is not None: pagination["page[after]"] = cursor
        if backend not in {"legacy", "survey"}: return failure(ErrorCode.VALIDATION_ERROR, "CSAT page requires a resolved backend")
        if backend == "survey" and page_size is not None: pagination["page[size]"] = str(min(page_size, 50))
        start = _epoch(created_at_start, milliseconds=backend == "survey"); end = _epoch(created_at_end, milliseconds=backend == "survey")
        if (created_at_start is not None and start is None) or (created_at_end is not None and end is None) or (start is not None and end is not None and start > end): return failure(ErrorCode.VALIDATION_ERROR, "CSAT dates must be ordered ISO-8601 timestamps with timezone")
        if backend == "legacy":
            if ticket_id is not None or responder_ids is not None or (score is not None and score not in _CSAT_SCORES): return failure(ErrorCode.VALIDATION_ERROR, "legacy CSAT accepts only a valid score and date range")
            return self._get("/api/v2/satisfaction_ratings.json", {key: value for key, value in {**pagination, "score": score, "start_time": str(start) if start is not None else None, "end_time": str(end) if end is not None else None}.items() if value is not None} or None)
        if score is not None or (ticket_id is not None and not self._valid_id(ticket_id)) or (responder_ids is not None and (not isinstance(responder_ids, list) or not responder_ids or any(not self._valid_id(value) for value in responder_ids))): return failure(ErrorCode.VALIDATION_ERROR, "survey CSAT accepts ticket_id, responder_ids, and date range only")
        return self._get("/api/v2/guide/survey_responses", {key: value for key, value in {**pagination, "filter[subject_zrns]": f"zen:ticket:{ticket_id}" if ticket_id is not None else None, "filter[responder_ids]": ",".join(str(value) for value in responder_ids) if responder_ids is not None else None, "filter[created_at_start]": str(start) if start is not None else None, "filter[created_at_end]": str(end) if end is not None else None}.items() if value is not None} or None)
    def export_csat(self, backend: str = "auto", *, score: str | None = None, ticket_id: int | None = None, responder_ids: list[int] | None = None, created_at_start: str | None = None, created_at_end: str | None = None, output_format: str = "json") -> dict[str, object]:
        if output_format not in {"json", "csv"}: return failure(ErrorCode.VALIDATION_ERROR, "output_format must be json or csv")
        if self._settings is None or self._settings.attachment_cache_root is None: return failure(ErrorCode.NOT_CONFIGURED, "Zendesk export cache is not configured")
        backend = self._resolve_csat_backend(backend)
        if isinstance(backend, dict): return backend
        root = self._settings.attachment_cache_root.parent / "exports"
        _clean_export_cache(root)
        count = 0; cursor = None; seen: set[str] = set(); truncated = False
        key = "satisfaction_ratings" if backend == "legacy" else "survey_responses"
        try:
            with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as spool:
                while count < 100000:
                    result = self._csat_page(backend, score=score, ticket_id=ticket_id, responder_ids=responder_ids, created_at_start=created_at_start, created_at_end=created_at_end, page_size=min(100, 100000-count), cursor=cursor)
                    if not result.get("ok"): return result
                    data = result.get("data")
                    page = data.get(key) if isinstance(data, dict) else None
                    meta = data.get("meta") if isinstance(data, dict) else None
                    if not isinstance(page, list) or any(not isinstance(item, dict) for item in page) or not isinstance(meta, dict) or not isinstance(meta.get("has_more"), bool): return failure(ErrorCode.UPSTREAM_ERROR, "Zendesk returned an invalid CSAT export page")
                    if not page and meta["has_more"]: return failure(ErrorCode.UPSTREAM_ERROR, "Zendesk CSAT export returned a nonprogressing page")
                    remaining = 100000-count
                    spool.writelines(json.dumps(item, ensure_ascii=False, separators=(",", ":")) + "\n" for item in page[:remaining])
                    count += min(len(page), remaining)
                    truncated = len(page) > remaining or meta["has_more"]
                    if count == 100000 or not truncated: break
                    cursor = meta.get("after_cursor")
                    if not isinstance(cursor, str) or not cursor or cursor in seen: return failure(ErrorCode.UPSTREAM_ERROR, "Zendesk returned an invalid CSAT export cursor")
                    seen.add(cursor)
                    del result, data, page, meta
                def items():
                    spool.seek(0)
                    for line in spool: yield json.loads(line)
                cached = _cache_ticket_export(root, output_format, _stream_ticket_export(items, output_format), filename_prefix="csat-export")
        except OSError:
            return failure(ErrorCode.UPSTREAM_ERROR, "CSAT export spool could not be written")
        if not cached.get("ok"): return cached
        return success({"format": output_format, "item_count": count, "truncated": truncated, "untrusted_user_content": True, **cached["data"]})
    def list_permission_groups(self, limit: int = 100, *, cursor: str | None = None) -> dict[str, object]:
        return collect_offset(self._get, "/api/v2/guide/permission_groups.json", "permission_groups", limit, cursor)
    def list_user_segments(self, *, built_in: bool | None = None, applicable: bool = False, limit: int = 100, cursor: str | None = None) -> dict[str, object]:
        if not isinstance(applicable, bool) or (built_in is not None and not isinstance(built_in, bool)): return failure(ErrorCode.VALIDATION_ERROR, "built_in and applicable must be booleans")
        return collect_cursor(self._get, "/api/v2/help_center/user_segments/applicable.json" if applicable else "/api/v2/help_center/user_segments.json", "user_segments", limit, cursor, filters={"built_in": str(built_in).lower()} if built_in is not None else None)
    def search_articles(self, query: str | None = None, *, brand_id: int | None = None, locale: str | None = None, locales: list[str] | None = None, brand_ids: list[int] | None = None, category_ids: list[object] | None = None, section_ids: list[object] | None = None, limit: int = 100, cursor: str | None = None) -> dict[str, object]:
        if any(value is not None for value in (locales, brand_ids, category_ids, section_ids)):
            if locale is not None or brand_id is not None: return failure(ErrorCode.VALIDATION_ERROR, "Use plural locale and brand inputs together, not singular inputs")
            return self._search_unified_articles(query, locales, brand_ids, category_ids, section_ids, limit, cursor)
        if not isinstance(query, str) or not query.strip() or (brand_id is not None and not self._valid_id(brand_id)) or (locale is not None and (not isinstance(locale, str) or not _LOCALE.fullmatch(locale))): return failure(ErrorCode.VALIDATION_ERROR, "query, brand_id, and locale must be valid")
        if locale is not None:
            scoped = self._for_brand(brand_id)
            if isinstance(scoped, dict): return scoped
            locale_check = scoped._validate_active_locale(locale)
            if locale_check is not None: return locale_check
        return collect_offset(self._get, "/api/v2/help_center/articles/search.json", "results", limit, cursor, max_results=1000, filters={key: value for key, value in {"query": query.strip(), "brand_id": str(brand_id) if brand_id is not None else None, "locale": locale}.items() if value is not None})
    def _search_unified_articles(self, query: str | None, locales: list[str] | None, brand_ids: list[int] | None, category_ids: list[object] | None, section_ids: list[object] | None, limit: int, cursor: str | None) -> dict[str, object]:
        if (query is not None and (not isinstance(query, str) or not query.strip() or len(query) > 500)) or not isinstance(locales, list) or not locales or any(not isinstance(value, str) or not _LOCALE.fullmatch(value) for value in locales):
            return failure(ErrorCode.VALIDATION_ERROR, "Unified search requires non-empty valid locales and optional query of 1 to 500 characters")
        if type(limit) is not int or not 1 <= limit <= 1000 or (cursor is not None and (not isinstance(cursor, str) or not cursor)):
            return failure(ErrorCode.VALIDATION_ERROR, "limit must be 1 to 1000 and cursor must be non-empty")
        filters = {"filter[locales]": ",".join(locales), "filter[content_types]": "ARTICLE"}
        if query is not None: filters["query"] = query.strip()
        for key, values in (("brand_ids", brand_ids), ("category_ids", category_ids), ("section_ids", section_ids)):
            if values is None: continue
            if not isinstance(values, list) or not values or any(not self._valid_id(value) if key == "brand_ids" else _help_center_id(value) is None or "," in str(value) or not str(value).strip() for value in values):
                return failure(ErrorCode.VALIDATION_ERROR, f"{key} must be a non-empty list of valid IDs without commas")
            filters[f"filter[{key}]"] = ",".join(str(value) for value in values)
        selected_brands = brand_ids
        if selected_brands is None:
            result = collect_complete_cursor(self._get, "/api/v2/brands.json", "brands")
            if not result.get("ok"): return result
            if any(not self._valid_id(brand.get("id")) or type(brand.get("has_help_center")) is not bool for brand in result["items"]):
                return failure(ErrorCode.UPSTREAM_ERROR, "Zendesk returned invalid Help Center brand metadata")
            selected_brands = [brand["id"] for brand in result["items"] if brand["has_help_center"]]
        enabled = set()
        for identifier in dict.fromkeys(selected_brands):
            scoped = self._for_brand(identifier)
            if isinstance(scoped, dict): return scoped
            result = scoped.list_locales()
            if not result.get("ok"): return result
            data = result.get("data")
            available = data.get("locales") if isinstance(data, dict) else None
            if not isinstance(available, list) or any(not isinstance(value, str) for value in available):
                return failure(ErrorCode.UPSTREAM_ERROR, "Zendesk returned invalid Help Center locales")
            enabled.update(available)
            if brand_ids is None and set(locales) <= enabled: break
        if not set(locales) <= enabled:
            return failure(ErrorCode.VALIDATION_ERROR, "Requested locales are not enabled in the selected Help Centers")
        return collect_cursor(self._get, "/api/v2/guide/search", "results", limit, cursor, filters=filters, page_size=50)
    def export_articles(self, locale: str | None = None, max_articles: int = 100000, *, brand_id: int | None = None, category_id: object = None, section_id: object = None) -> dict[str, object]:
        articles: list[object] = []
        result = self._export_article_pages(locale, max_articles, articles.extend, brand_id=brand_id, category_id=category_id, section_id=section_id)
        if not result.get("ok"): return result
        return success({"articles": articles, "truncated": result["data"]["truncated"]})
    def _export_article_pages(self, locale: str | None, max_articles: int, consume: Callable[[list[object]], None], *, brand_id: int | None = None, category_id: object = None, section_id: object = None) -> dict[str, object]:
        if (locale is not None and (not isinstance(locale, str) or not _LOCALE.fullmatch(locale))) or type(max_articles) is not int or not 1 <= max_articles <= 100000 or any(value is not None and _help_center_id(value) is None for value in (category_id, section_id)):
            return failure(ErrorCode.VALIDATION_ERROR, "locale, scope IDs, and max_articles must be valid")
        scoped = self._for_brand(brand_id)
        if isinstance(scoped, dict): return scoped
        if scoped is not self: return scoped._export_article_pages(locale, max_articles, consume, category_id=category_id, section_id=section_id)
        locales_result = self.list_locales()
        if not locales_result.get("ok"): return locales_result
        data = locales_result.get("data")
        enabled = data.get("locales") if isinstance(data, dict) else None
        if not isinstance(enabled, list) or any(not isinstance(value, str) for value in enabled):
            return failure(ErrorCode.UPSTREAM_ERROR, "Zendesk returned invalid Help Center locales")
        if locale is None:
            locale = data.get("default_locale")
            if not isinstance(locale, str) or not _LOCALE.fullmatch(locale) or locale not in enabled:
                return failure(ErrorCode.UPSTREAM_ERROR, "Zendesk did not provide an enabled default Help Center locale")
        elif locale not in enabled: return failure(ErrorCode.VALIDATION_ERROR, "locale is not enabled for this Help Center")
        scope = f"sections/{_help_center_id(section_id)}/" if section_id is not None else f"categories/{_help_center_id(category_id)}/" if category_id is not None else ""
        count = 0; cursor: str | None = None; seen: set[str] = set()
        while count < max_articles:
            params = {"page[size]": str(min(100, max_articles - count))}
            if cursor is not None: params["page[after]"] = cursor
            result = self._get(f"/api/v2/help_center/{locale}/{scope}articles.json", params)
            if not result.get("ok"): return result
            data = result.get("data"); page = data.get("articles") if isinstance(data, dict) else None; meta = data.get("meta") if isinstance(data, dict) else None
            if not isinstance(page, list) or any(not isinstance(article, dict) for article in page) or not isinstance(meta, dict) or not isinstance(meta.get("has_more"), bool): return failure(ErrorCode.UPSTREAM_ERROR, "Zendesk returned an invalid article export page")
            if not page and meta["has_more"]: return failure(ErrorCode.UPSTREAM_ERROR, "Zendesk article export returned a nonprogressing page")
            remaining = max_articles - count
            consume(page[:remaining]); count += min(len(page), remaining)
            if len(page) > remaining: return success({"item_count": count, "truncated": True})
            if not meta.get("has_more"): return success({"item_count": count, "truncated": False})
            cursor = meta.get("after_cursor")
            if not isinstance(cursor, str) or not cursor or cursor in seen: return failure(ErrorCode.UPSTREAM_ERROR, "Zendesk returned an invalid article export cursor")
            seen.add(cursor)
            del result, data, page, meta
        return success({"item_count": count, "truncated": True})
    def export_article_artifact(self, locale: str | None = None, max_articles: int = 100000, *, brand_id: int | None = None, category_id: object = None, section_id: object = None, output_format: str = "json") -> dict[str, object]:
        if output_format not in {"json", "csv"}: return failure(ErrorCode.VALIDATION_ERROR, "output_format must be json or csv")
        if self._settings is None or self._settings.attachment_cache_root is None: return failure(ErrorCode.NOT_CONFIGURED, "Zendesk export cache is not configured")
        root = self._settings.attachment_cache_root.parent / "exports"
        _clean_export_cache(root)
        try:
            with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as spool:
                def consume(page):
                    for article in page: spool.write(json.dumps(article, ensure_ascii=False, separators=(",", ":")) + "\n")
                result = self._export_article_pages(locale, max_articles, consume, brand_id=brand_id, category_id=category_id, section_id=section_id)
                if not result.get("ok"): return result
                def items():
                    spool.seek(0)
                    for line in spool: yield json.loads(line)
                cached = _cache_ticket_export(root, output_format, _stream_ticket_export(items, output_format), filename_prefix="help-center-export")
        except OSError:
            return failure(ErrorCode.UPSTREAM_ERROR, "article export spool could not be written")
        if not cached.get("ok"): return cached
        return success({"format": output_format, **result["data"], **cached["data"]})
    def get_article(self, article_id: object, *, brand_id: int | None = None, locale: str | None = None, embed_images: bool = False, include_metadata: bool = False) -> dict[str, object]:
        identifier = _help_center_id(article_id)
        if identifier is None or not isinstance(embed_images, bool) or not isinstance(include_metadata, bool) or (locale is not None and (not isinstance(locale, str) or not _LOCALE.fullmatch(locale))): return failure(ErrorCode.VALIDATION_ERROR, "article_id, locale, embed_images and include_metadata must be valid")
        scoped = self._for_brand(brand_id)
        if isinstance(scoped, dict): return scoped
        if scoped is not self: return scoped.get_article(article_id, locale=locale, embed_images=embed_images, include_metadata=include_metadata)
        article_result = None; metadata = {}
        if include_metadata:
            article_result = self._get(f"/api/v2/help_center/articles/{identifier}.json", {"include": "users,sections,categories"})
            if not article_result.get("ok"): return article_result
            data = article_result.get("data")
            if not isinstance(data, dict) or not isinstance(data.get("article"), dict): return failure(ErrorCode.UPSTREAM_ERROR, "Zendesk returned invalid article metadata")
            metadata = {"metadata": _article_names(data), "untrusted_user_content": True}
        if locale == "all":
            locales_result = self.list_locales()
            if not locales_result.get("ok"): return locales_result
            data = locales_result.get("data")
            enabled = data.get("locales") if isinstance(data, dict) else None
            if not isinstance(enabled, list) or not enabled or any(not isinstance(value, str) or not _LOCALE.fullmatch(value) for value in enabled):
                return failure(ErrorCode.UPSTREAM_ERROR, "Zendesk returned invalid enabled locales")
            def get_page(path, *, params):
                return self._get(path, {**params, "locales": ",".join(enabled)})
            translations = collect_complete_cursor(get_page, f"/api/v2/help_center/articles/{identifier}/translations.json", "translations")
            if not translations.get("ok"): return translations
            if any(item.get("locale") not in enabled for item in translations["items"]):
                return failure(ErrorCode.UPSTREAM_ERROR, "Zendesk returned translations outside the requested locales")
            data = {"translations": [{**item, "untrusted_user_content": True} for item in translations["items"]], **metadata}
            if not embed_images: return success(data)
            images = self._embed_images("\n".join(item["body"] for item in translations["items"] if isinstance(item.get("body"), str)))
            return images if isinstance(images, dict) else success({**data, "images": images})
        if locale is not None:
            locale_check = self._validate_active_locale(locale)
            if locale_check is not None: return locale_check
            translation = self._get_translation(article_id, locale)
            if not isinstance(translation, dict) or "ok" in translation: return translation
            translation = {**translation, "untrusted_user_content": True}
            if not embed_images: return success({"translation": translation, **metadata})
            images = self._embed_images(translation.get("body"))
            return images if isinstance(images, dict) else success({"translation": translation, "images": images, **metadata})
        result = article_result if article_result is not None else self._get(f"/api/v2/help_center/articles/{identifier}.json")
        if not result.get("ok"): return result
        data = result.get("data"); article = data.get("article") if isinstance(data, dict) else None; body = article.get("body") if isinstance(article, dict) else None
        if isinstance(data, dict) and isinstance(article, dict):
            data = {**data, "article": {**article, "untrusted_user_content": True}, **metadata}
            result = success(data)
            article = data["article"]; body = article.get("body")
        if not embed_images: return result
        if not isinstance(body, str): return result
        images = self._embed_images(body)
        return images if isinstance(images, dict) else success({**data, "images": images})

    def _embed_images(self, body: object) -> list[dict[str, object]] | dict[str, object]:
        if not isinstance(body, str): return []
        downloader = getattr(self._client, "download_help_center_image", None)
        if not callable(downloader): return failure(ErrorCode.UNSUPPORTED, "Zendesk client does not support Help Center image downloads")
        images: list[dict[str, object]] = []; remaining = _MAX_EMBEDDED_IMAGE_BYTES
        for image_url in _help_center_image_urls(body):
            if remaining < 1: break
            downloaded = downloader(image_url, max_bytes=remaining, subdomain=self._brand_subdomain)
            image = downloaded.get("data") if isinstance(downloaded, dict) else None
            content = image.get("content") if isinstance(image, dict) else None
            content_type = image.get("content_type") if isinstance(image, dict) else None
            if not downloaded.get("ok") or not isinstance(content, bytes) or len(content) > remaining or content_type not in _EMBEDDABLE_IMAGE_TYPES: continue
            images.append({"src": image_url, "content": content, "content_type": content_type})
            remaining -= len(content)
        return images
    def create_article(self, section_id: object, locale: str, title: str, body: str, *, brand_id: int | None = None, labels: list[str] | None = None, position: int | None = None, permission_group_id: int | None = None, user_segment_id: int | None = None, draft: bool = True, notify_subscribers: bool = False, execution_mode: str = "preview", approval_request_id: str | None = None, approval_token: str | None = None) -> dict[str, object]:
        section_identifier = _help_center_id(section_id)
        payload = self._article_payload(section_id, locale, title, body, labels, position, permission_group_id, user_segment_id, draft, notify_subscribers)
        if section_identifier is None or payload is None or (brand_id is not None and not self._valid_id(brand_id)): return failure(ErrorCode.VALIDATION_ERROR, "valid draft article fields are required; publish with zendesk_publish_help_center_article")
        if brand_id is not None: payload["brand_id"] = brand_id
        risks = (WriteRisk.STANDARD, WriteRisk.PUBLIC) if notify_subscribers else (WriteRisk.STANDARD,)
        if execution_mode == "preview":
            if self._approvals is None: return failure(ErrorCode.NOT_CONFIGURED, "Zendesk approval store is not configured")
            return success({"approval_request_id": self._approvals.create("zendesk_create_help_center_article", payload), "execution_mode": "preview", "outbound_write": False, **({"recipient_count_unknown": True} if notify_subscribers else {}), **{risk.value: True for risk in risks}})
        if execution_mode != "apply": return failure(ErrorCode.VALIDATION_ERROR, "execution_mode must be preview or apply")
        if self._settings is None: return failure(ErrorCode.WRITE_DISABLED, "Zendesk writes are disabled")
        for risk in risks:
            if (blocked := check_write_permission(self._settings, risk)) is not None: return blocked
        scoped = self._for_brand(brand_id)
        if isinstance(scoped, dict): return scoped
        locale_check = scoped._validate_active_locale(locale)
        if locale_check is not None: return locale_check
        if self._approvals is None or not isinstance(approval_request_id, str) or not isinstance(approval_token, str) or not self._approvals.consume(approval_request_id, "zendesk_create_help_center_article", payload, approval_token): return failure(ErrorCode.APPROVAL_REQUIRED, "a matching local approval is required")
        return scoped._request("POST", f"/api/v2/help_center/sections/{section_identifier}/articles.json", {"article": payload["article"], "notify_subscribers": notify_subscribers})
    def upsert_article_translation(self, article_id: object, locale: str, *, brand_id: int | None = None, title: str | None = None, body: str | None = None, draft: bool = True, execution_mode: str = "preview", approval_request_id: str | None = None, approval_token: str | None = None) -> dict[str, object]:
        identifier = _help_center_id(article_id)
        if identifier is None or (brand_id is not None and not self._valid_id(brand_id)) or not isinstance(locale, str) or not _LOCALE.fullmatch(locale) or not isinstance(draft, bool) or not draft or (title is not None and (not isinstance(title, str) or not title.strip())) or (body is not None and not isinstance(body, str)): return failure(ErrorCode.VALIDATION_ERROR, "valid draft translation fields are required; publish with zendesk_publish_help_center_article")
        scoped = self._for_brand(brand_id)
        if isinstance(scoped, dict): return scoped
        current = scoped._get_translation(article_id, locale)
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
        payload = {"article_id": article_id, "locale": locale, "operation": operation, "translation": translation, "brand_id": brand_id}
        if execution_mode == "preview":
            if self._approvals is None: return failure(ErrorCode.NOT_CONFIGURED, "Zendesk approval store is not configured")
            return success({"approval_request_id": self._approvals.create("zendesk_upsert_article_translation", payload), "execution_mode": "preview", "outbound_write": False, **{risk.value: True for risk in risks}})
        if execution_mode != "apply": return failure(ErrorCode.VALIDATION_ERROR, "execution_mode must be preview or apply")
        if self._settings is None: return failure(ErrorCode.WRITE_DISABLED, "Zendesk writes are disabled")
        for risk in risks:
            if (blocked := check_write_permission(self._settings, risk)) is not None: return blocked
        locale_check = scoped._validate_active_locale(locale)
        if locale_check is not None: return locale_check
        if self._approvals is None or not isinstance(approval_request_id, str) or not isinstance(approval_token, str) or not self._approvals.consume(approval_request_id, "zendesk_upsert_article_translation", payload, approval_token): return failure(ErrorCode.APPROVAL_REQUIRED, "a matching local approval is required")
        path = f"/api/v2/help_center/articles/{identifier}/translations.json" if operation == "create" else f"/api/v2/help_center/articles/{identifier}/translations/{locale}.json"
        return scoped._request("POST" if operation == "create" else "PUT", path, {"translation": translation})
    def replace_article_translation_body(self, article_id: object, locale: str, body: str, *, brand_id: int | None = None, execution_mode: str = "preview", approval_request_id: str | None = None, approval_token: str | None = None) -> dict[str, object]:
        identifier = _help_center_id(article_id)
        if identifier is None or (brand_id is not None and not self._valid_id(brand_id)) or not isinstance(locale, str) or not _LOCALE.fullmatch(locale) or not isinstance(body, str): return failure(ErrorCode.VALIDATION_ERROR, "valid article_id, locale, and body are required")
        scoped = self._for_brand(brand_id)
        if isinstance(scoped, dict): return scoped
        current = scoped._get_translation(article_id, locale)
        if current is None or not current.get("ok", True): return current or failure(ErrorCode.NOT_FOUND, "translation not found")
        risks = (WriteRisk.DESTRUCTIVE, WriteRisk.PUBLIC) if current.get("draft") is not True else (WriteRisk.DESTRUCTIVE,)
        payload = {"article_id": article_id, "locale": locale, "body": body, "brand_id": brand_id}
        if execution_mode == "preview":
            if self._approvals is None: return failure(ErrorCode.NOT_CONFIGURED, "Zendesk approval store is not configured")
            return success({"approval_request_id": self._approvals.create("zendesk_replace_article_translation_body", payload), "execution_mode": "preview", "content_block_replacement_warning": True, "outbound_write": False, **{risk.value: True for risk in risks}})
        if execution_mode != "apply": return failure(ErrorCode.VALIDATION_ERROR, "execution_mode must be preview or apply")
        if self._settings is None: return failure(ErrorCode.WRITE_DISABLED, "Zendesk writes are disabled")
        for risk in risks:
            if (blocked := check_write_permission(self._settings, risk)) is not None: return blocked
        locale_check = scoped._validate_active_locale(locale)
        if locale_check is not None: return locale_check
        if self._approvals is None or not isinstance(approval_request_id, str) or not isinstance(approval_token, str) or not self._approvals.consume(approval_request_id, "zendesk_replace_article_translation_body", payload, approval_token): return failure(ErrorCode.APPROVAL_REQUIRED, "a matching local approval is required")
        return scoped._request("PUT", f"/api/v2/help_center/articles/{identifier}/translations/{locale}.json", {"translation": {"body": body}})
    def publish_article(self, article_id: object, locale: str, *, brand_id: int | None = None, execution_mode: str = "preview", approval_request_id: str | None = None, approval_token: str | None = None) -> dict[str, object]:
        identifier = _help_center_id(article_id)
        if identifier is None or (brand_id is not None and not self._valid_id(brand_id)) or not isinstance(locale, str) or not _LOCALE.fullmatch(locale): return failure(ErrorCode.VALIDATION_ERROR, "valid article_id and locale are required")
        scoped = self._for_brand(brand_id)
        if isinstance(scoped, dict): return scoped
        current = scoped._get_translation(article_id, locale)
        if current is None or not current.get("ok", True): return current or failure(ErrorCode.NOT_FOUND, "translation not found")
        if current.get("draft") is False: return success({"translation": current, "outbound_write": False})
        payload = {"article_id": article_id, "locale": locale, "brand_id": brand_id}
        if execution_mode == "preview":
            if self._approvals is None: return failure(ErrorCode.NOT_CONFIGURED, "Zendesk approval store is not configured")
            return success({"approval_request_id": self._approvals.create("zendesk_publish_help_center_article", payload), "execution_mode": "preview", "public": True, "outbound_write": False})
        if execution_mode != "apply": return failure(ErrorCode.VALIDATION_ERROR, "execution_mode must be preview or apply")
        if self._settings is None or (blocked := check_write_permission(self._settings, WriteRisk.PUBLIC)) is not None: return blocked or failure(ErrorCode.WRITE_DISABLED, "Zendesk public writes are disabled")
        locale_check = scoped._validate_active_locale(locale)
        if locale_check is not None: return locale_check
        if self._approvals is None or not isinstance(approval_request_id, str) or not isinstance(approval_token, str) or not self._approvals.consume(approval_request_id, "zendesk_publish_help_center_article", payload, approval_token): return failure(ErrorCode.APPROVAL_REQUIRED, "a matching local approval is required")
        updated = scoped._request("PUT", f"/api/v2/help_center/articles/{identifier}/translations/{locale}.json", {"translation": {"draft": False}})
        if not updated.get("ok"): return updated
        read_back = scoped._get_translation(article_id, locale)
        if not isinstance(read_back, dict) or "ok" in read_back or read_back.get("draft") is not False or read_back.get("locale") != locale:
            return failure(ErrorCode.OUTCOME_UNKNOWN, "publish write succeeded but the requested locale could not be verified; inspect it before retrying", operation_state="unknown", request_id=updated.get("request_id"), details={"article_id": article_id, "locale": locale})
        return success({"translation": read_back}, request_id=updated.get("request_id"), operation_state="applied")
    def _article_payload(self, section_id: object, locale: object, title: object, body: object, labels: object, position: object, permission_group_id: object, user_segment_id: object, draft: object, notify_subscribers: object) -> dict[str, object] | None:
        if _help_center_id(section_id) is None or not isinstance(locale, str) or not _LOCALE.fullmatch(locale) or not isinstance(title, str) or not title.strip() or not isinstance(body, str) or not isinstance(draft, bool) or not draft or not isinstance(notify_subscribers, bool): return None
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
    def _get_translation(self, article_id: object, locale: str) -> dict[str, object] | None:
        identifier = _help_center_id(article_id)
        if identifier is None: return failure(ErrorCode.VALIDATION_ERROR, "article_id must be a valid Help Center ID")
        result = self._get(f"/api/v2/help_center/articles/{identifier}/translations/{locale}.json")
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
    def _request(self, method: str, path: str, json_body: dict[str, object]) -> dict[str, object]:
        if self._client is None: return failure(ErrorCode.NOT_CONFIGURED, "Zendesk is not configured")
        if self._brand_subdomain is not None:
            requester = getattr(self._client, "request_for_subdomain", None)
            if not callable(requester): return failure(ErrorCode.UNSUPPORTED, "Zendesk client does not support brand-scoped Help Center writes")
            return requester(self._brand_subdomain, method, path, json_body=json_body)
        return self._client.request(method, path, json_body=json_body)


def _epoch(value: str | None, *, milliseconds: bool) -> int | None:
    if value is None: return None
    if not isinstance(value, str): return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None: return None
        epoch = int(parsed.timestamp())
        return epoch * 1000 if milliseconds else epoch
    except ValueError: return None


def _article_names(data: dict[str, object]) -> dict[str, object]:
    article = data["article"]
    def related(key, identifier):
        records = data.get(key)
        return next((item for item in records if isinstance(item, dict) and _help_center_id(identifier) is not None and _help_center_id(item.get("id")) == _help_center_id(identifier)), {}) if isinstance(records, list) else {}
    section = related("sections", article.get("section_id"))
    category = related("categories", section.get("category_id"))
    author = related("users", article.get("author_id"))
    return {key: item.get("name") if isinstance(item.get("name"), str) else None for key, item in (("section_name", section), ("category_name", category), ("author_name", author))}


def _help_center_id(value: object) -> str | None:
    if isinstance(value, int) and not isinstance(value, bool) and value > 0: return str(value)
    return quote(value, safe="") if isinstance(value, str) and value and value not in {".", ".."} else None


class _ImageSourceParser(HTMLParser):
    def __init__(self) -> None: super().__init__(); self.sources: list[str] = []
    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "img": self.sources.extend(value for name, value in attrs if name.lower() == "src" and isinstance(value, str))


def _help_center_image_urls(body: str) -> list[str]:
    parser = _ImageSourceParser(); parser.feed(body); parser.close()
    urls = []
    for source in parser.sources:
        try: parsed = urlsplit(source)
        except ValueError: continue
        if parsed.scheme == "https" and parsed.path.startswith("/hc/user_images/"): urls.append(source)
    return list(dict.fromkeys(urls))
