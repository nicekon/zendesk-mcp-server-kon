import pytest

from zendesk_mcp_server.contracts import success
from zendesk_mcp_server.pagination import collect_complete_cursor, collect_cursor, collect_offset


@pytest.mark.parametrize("total", [100000, 100001])
def test_complete_cursor_distinguishes_exact_cap_from_incomplete_scan(total):
    calls = []
    def get(path, *, params=None):
        start = int(params.get("page[after]", "0"))
        end = min(total, start + int(params["page[size]"]))
        calls.append((start, end))
        return success({"items": [{"id": i} for i in range(start, end)], "meta": {"has_more": end < total, "after_cursor": str(end)}})
    result = collect_complete_cursor(get, "/api/v2/example.json", "items")
    assert len(calls) == 1000 and calls[-1][1] == 100000
    if total == 100000:
        assert result["ok"] is True and len(result["items"]) == total
        assert result["has_more"] is False
    else:
        assert result["error"]["code"] == "upstream_error" and "items" not in result


def test_complete_cursor_does_not_treat_stalled_page_as_complete():
    calls = []
    def get(path, *, params=None):
        calls.append(params)
        return success({"items": [], "meta": {"has_more": True, "after_cursor": "next"}})
    result = collect_complete_cursor(get, "/api/v2/example.json", "items")
    assert result["error"]["code"] == "upstream_error" and len(calls) == 1


@pytest.mark.parametrize("prefix", [[], [{"id": 1}]])
def test_empty_nonterminal_cursor_page_yields_resumable_partial_result(prefix):
    calls = []
    def get(path, *, params=None):
        calls.append(params)
        after = params.get("page[after]")
        if prefix and after is None:
            return success({"items": prefix, "meta": {"has_more": True, "after_cursor": "empty"}})
        if after != "resume":
            return success({"items": [], "meta": {"has_more": True, "after_cursor": "resume"}})
        return success({"items": [{"id": 2}], "meta": {"has_more": False}})

    result = collect_cursor(get, "/api/v2/example.json", "items", 100, None)
    assert result == {"ok": True, "items": prefix, "has_more": True, "next_cursor": "resume", "truncated": True}
    assert len(calls) == (2 if prefix else 1)
    resumed = collect_cursor(get, "/api/v2/example.json", "items", 100, result["next_cursor"])
    assert resumed == {"ok": True, "items": [{"id": 2}], "has_more": False, "next_cursor": None, "truncated": False}


@pytest.mark.parametrize("collect", [collect_cursor, collect_offset])
def test_lists_reject_non_object_items(collect):
    for invalid in (None, "record", 7, [], True):
        def get(path, *, params=None):
            return success({"items": [{"id": 1}, invalid], "meta": {"has_more": False}, "next_page": None})
        result = collect(get, "/api/v2/example.json", "items", 100, None)
        assert result["ok"] is False
        assert result["error"]["code"] == "upstream_error"


def test_offset_selection_never_exceeds_result_cap():
    def get(path, *, params=None):
        return success({"items": [{"id": i} for i in range(900, 1000)], "next_page": None})
    result = collect_offset(get, "/api/v2/example.json", "items", 100, "940", max_results=950)
    assert [item["id"] for item in result["items"]] == list(range(940, 950))
    assert result["next_cursor"] is None and result["truncated"] is True


def test_cursor_list_rejects_malformed_later_page_without_partial_success():
    def get(path, *, params=None):
        if "page[after]" not in params:
            return success({"items": [{"id": 1}], "meta": {"has_more": True, "after_cursor": "next"}})
        return success({"items": [None], "meta": {"has_more": False}})
    result = collect_cursor(get, "/api/v2/example.json", "items", 2, None)
    assert result["ok"] is False
    assert "items" not in result
