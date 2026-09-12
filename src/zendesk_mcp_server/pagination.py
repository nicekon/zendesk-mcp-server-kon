"""Bounded cursor and offset pagination shared by read-only lists."""

from typing import Callable

from .contracts import ErrorCode, failure


def collect_complete_cursor(get: Callable[..., dict[str, object]], path: str, key: str) -> dict[str, object]:
    items = []; cursor = None; seen = set()
    while len(items) < 100000:
        result = collect_cursor(get, path, key, min(1000, 100000 - len(items)), cursor)
        if not result.get("ok"): return result
        items.extend(result["items"])
        if not result["has_more"]: return {**result, "items": items}
        cursor = result["next_cursor"]
        if not result["items"] or cursor in seen: return failure(ErrorCode.UPSTREAM_ERROR, "complete metadata could not be determined")
        seen.add(cursor)
    return failure(ErrorCode.UPSTREAM_ERROR, "metadata exceeds the complete-read limit")


def collect_array(get: Callable[..., dict[str, object]], path: str, key: str, limit: int, cursor: str | None, *, filters: dict[str, str] | None = None) -> dict[str, object]:
    if type(limit) is not int or not 1 <= limit <= 1000 or (cursor is not None and (not isinstance(cursor, str) or not cursor.isascii() or not cursor.isdecimal() or len(cursor) > 10)):
        return failure(ErrorCode.VALIDATION_ERROR, "limit must be 1 to 1000 and cursor must be a non-negative list offset")
    result = get(path, params=filters)
    if not result.get("ok"): return result
    data = result.get("data")
    records = data.get(key) if isinstance(data, dict) else None
    if not isinstance(records, list) or any(not isinstance(item, dict) for item in records):
        return failure(ErrorCode.UPSTREAM_ERROR, "Zendesk returned an invalid complete list")
    offset = int(cursor) if cursor is not None else 0
    items = records[offset:offset + limit]
    end = offset + len(items)
    more = end < len(records)
    return {"ok": True, "items": items, "has_more": more, "next_cursor": str(end) if more else None, "truncated": more}


def collect_offset(get: Callable[..., dict[str, object]], path: str, key: str, limit: int, cursor: str | None, *, filters: dict[str, str] | None = None, max_results: int = 10000, page_size: int = 100) -> dict[str, object]:
    if type(limit) is not int or not 1 <= limit <= 1000 or (cursor is not None and (not isinstance(cursor, str) or not cursor.isascii() or not cursor.isdecimal() or len(cursor) > 4)):
        return failure(ErrorCode.VALIDATION_ERROR, "limit must be 1 to 1000 and cursor must be an offset below 10000")
    offset = int(cursor) if cursor is not None else 0
    if offset >= max_results:
        return failure(ErrorCode.VALIDATION_ERROR, "cursor exceeds the endpoint result limit")
    items = []
    while len(items) < limit and offset < max_results:
        next_offset = (offset // page_size + 1) * page_size
        result = get(path, params={**(filters or {}), "per_page": str(page_size), "page": str(offset // page_size + 1)})
        if not result.get("ok"): return result
        data = result.get("data")
        page = data.get(key) if isinstance(data, dict) else None
        if not isinstance(page, list) or len(page) > page_size or any(not isinstance(item, dict) for item in page) or "next_page" not in data or (data["next_page"] is not None and (not isinstance(data["next_page"], str) or not data["next_page"])):
            return failure(ErrorCode.UPSTREAM_ERROR, "Zendesk returned an invalid offset page")
        start = offset % page_size
        selected = page[start:start + min(limit - len(items), max_results - offset)]
        items.extend(selected)
        offset += len(selected)
        if start + len(selected) < len(page): break
        if data["next_page"] is None:
            return {"ok": True, "items": items, "has_more": False, "next_cursor": None, "truncated": False}
        offset = next_offset
    return {"ok": True, "items": items, "has_more": True, "next_cursor": str(offset) if offset < max_results else None, "truncated": True}


def collect_cursor(get: Callable[..., dict[str, object]], path: str, key: str, limit: int, cursor: str | None, *, filters: dict[str, str] | None = None, page_size: int = 100) -> dict[str, object]:
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 1000 or (cursor is not None and (not isinstance(cursor, str) or not cursor)):
        return failure(ErrorCode.VALIDATION_ERROR, "limit must be 1 to 1000 and cursor must be non-empty")
    items = []; seen = {cursor} if cursor else set()
    while len(items) < limit:
        size = min(page_size, limit-len(items))
        params = {**(filters or {}), "page[size]": str(size)}
        if cursor is not None: params["page[after]"] = cursor
        result = get(path, params=params)
        if not result.get("ok"): return result
        data = result.get("data")
        page = data.get(key) if isinstance(data, dict) else None
        meta = data.get("meta") if isinstance(data, dict) else None
        links = data.get("links") if isinstance(data, dict) else None
        if page == [] and isinstance(meta, dict) and all(name in meta and meta[name] is None for name in ("has_more", "after_cursor", "before_cursor")) and isinstance(links, dict) and "next" in links and links["next"] is None:
            meta = {**meta, "has_more": False}
        if not isinstance(page, list) or len(page) > size or any(not isinstance(item, dict) for item in page) or not isinstance(meta, dict) or not isinstance(meta.get("has_more"), bool):
            return failure(ErrorCode.UPSTREAM_ERROR, "Zendesk returned an invalid metadata page")
        items.extend(page)
        if not meta["has_more"]: return {"ok": True, "items": items, "has_more": False, "next_cursor": None, "truncated": False}
        cursor = meta.get("after_cursor")
        if not isinstance(cursor, str) or not cursor or cursor in seen:
            return failure(ErrorCode.UPSTREAM_ERROR, "Zendesk returned an invalid metadata cursor")
        seen.add(cursor)
        # Yield a resumable result instead of scanning unbounded empty pages.
        if not page: break
    return {"ok": True, "items": items, "has_more": True, "next_cursor": cursor, "truncated": True}
