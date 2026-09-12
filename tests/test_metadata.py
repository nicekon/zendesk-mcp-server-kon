from zendesk_mcp_server.contracts import success
from zendesk_mcp_server.tools.metadata import MetadataTools
import pytest


@pytest.mark.parametrize("active", [True, False, None])
@pytest.mark.parametrize("name,key,options", [
    ("list_ticket_forms", "ticket_forms", {}),
    ("list_triggers", "triggers", {"category_id": "10026", "sort": "position", "sort_order": "desc"}),
])
def test_metadata_filters_survive_pagination(active, name, key, options):
    class Client:
        def get(self, path, *, params=None):
            assert path == f"/api/v2/{key}.json"
            expected = dict(options)
            if active is not None:
                expected["active"] = "true" if active else "false"
            after = params.get("page[after]")
            assert params == {**expected, "page[size]": "1" if after else "2", **({"page[after]": "next"} if after else {})}
            return success({key: [{"id": 2 if after else 1}], "meta": {"has_more": not after, "after_cursor": "next"}})
    result = getattr(MetadataTools(Client()), name)(limit=2, active=active, **options)
    assert result["items"] == [{"id": 1}, {"id": 2}]
    assert result["has_more"] is False


@pytest.mark.parametrize("options", [{"active": 1}, {"active": "false"}, {"category_id": ""}, {"category_id": 1}, {"sort": "usage_24h"}, {"sort": []}, {"sort_order": "DESC"}])
def test_trigger_filters_reject_invalid_values_before_request(options):
    result = MetadataTools(None).list_triggers(**options)
    assert result["error"]["code"] == "validation_error"


def test_form_filter_rejects_non_boolean_before_request():
    assert MetadataTools(None).list_ticket_forms(active=0)["error"]["code"] == "validation_error"


class StubClient:
    def __init__(self):
        self.paths = []

    def get(self, path, *, params=None):
        self.paths.append((path, params))
        return success({"users": [{"id": 1}], "next_page": None, "groups": [{"id": 2}], "brands": [{"id": 3}], "meta": {"has_more": False}})


def test_user_search_resumes_inside_page_and_crosses_page_boundary():
    class Client:
        def get(self, path, *, params=None):
            assert path == "/api/v2/users/search.json"
            assert params["query"] == "agent"
            assert params["per_page"] == "100"
            page = int(params["page"])
            assert page in (1, 2)
            return success({"users": [{"id": i} for i in range((page-1)*100, page*100)], "next_page": "https://untrusted.example/ignored"})
    t = MetadataTools(Client())
    r = t.search_users("agent", limit=3, cursor="99")
    assert r == {"ok": True, "items": [{"id": 99}, {"id": 100}, {"id": 101}], "has_more": True, "next_cursor": "102", "truncated": True}
    for cursor in ("10000", "-1", "bad", True):
        assert t.search_users("agent", cursor=cursor)["error"]["code"] == "validation_error"


def test_user_search_empty_pages_stop_at_api_ceiling():
    class Client:
        def get(self, path, *, params=None):
            assert params["page"] == "100"
            return success({"users": [], "next_page": "https://untrusted.example/ignored"})
    assert MetadataTools(Client()).search_users("agent", cursor="9900") == {"ok": True, "items": [], "has_more": True, "next_cursor": None, "truncated": True}


def test_groups_collect_pages_and_return_resumable_cursor():
    class PagesClient:
        def get(self, path, *, params=None):
            assert path == "/api/v2/groups.json"
            after = params.get("page[after]")
            return success({"groups": [{"id": 2 if after else 1}], "meta": {"has_more": True, "after_cursor": "last" if after else "next"}})
    result = MetadataTools(PagesClient()).list_groups(limit=2)
    assert result == {"ok": True, "items": [{"id": 1}, {"id": 2}], "has_more": True, "next_cursor": "last", "truncated": True}


def test_groups_handle_empty_final_page_and_reject_repeated_cursor():
    for repeated in (False, True):
        class PagesClient:
            calls = 0
            def get(self, path, *, params=None):
                self.calls += 1
                if self.calls == 1:
                    return success({"groups": [{"id": 1}], "meta": {"has_more": True, "after_cursor": "next"}})
                assert params["page[after]"] == "next"
                return success({"groups": [], "meta": {"has_more": repeated, "after_cursor": "next"}, "links": {"next": "https://untrusted.example/groups"}})
        client = PagesClient()
        result = MetadataTools(client).list_groups()
        assert client.calls == 2
        if repeated: assert result["error"]["code"] == "upstream_error"
        else: assert result == {"ok": True, "items": [{"id": 1}], "has_more": False, "next_cursor": None, "truncated": False}


def test_macro_list_resumes_cursor_with_standard_envelope():
    class MacroClient:
        def get(self, path, *, params=None):
            assert path == "/api/v2/macros.json"
            assert params == {"page[size]": "1", "page[after]": "next"}
            return success({"macros": [{"id": 7}], "meta": {"has_more": False}})
    assert MetadataTools(MacroClient()).list_macros(limit=1, cursor="next") == {"ok": True, "items": [{"id": 7}], "has_more": False, "next_cursor": None, "truncated": False}


def test_views_and_view_tickets_resume_cursor():
    for path, key in (("/api/v2/views.json", "views"), ("/api/v2/views/4/tickets.json", "tickets")):
        class Client:
            def get(self, requested, *, params=None):
                assert requested == path
                assert params == {"page[size]": "1", "page[after]": "next"}
                return success({key: [{"id": 7}], "meta": {"has_more": False}})
        tools = MetadataTools(Client())
        result = tools.list_views(limit=1, cursor="next") if key == "views" else tools.list_view_tickets(4, limit=1, cursor="next")
        assert result == {"ok": True, "items": [{"id": 7}], "has_more": False, "next_cursor": None, "truncated": False}


def test_empty_view_page_with_null_boundaries_is_terminal():
    class Client:
        def get(self, path, *, params=None):
            return success({"tickets": [], "meta": {"has_more": None, "after_cursor": None, "before_cursor": None}, "links": {"next": None, "prev": None}})
    assert MetadataTools(Client()).list_view_tickets(4) == {"ok": True, "items": [], "has_more": False, "next_cursor": None, "truncated": False}


def test_trigger_list_resumes_cursor():
    class Client:
        def get(self, path, *, params=None):
            assert path == "/api/v2/triggers.json"
            assert params == {"page[size]": "1", "page[after]": "next"}
            return success({"triggers": [{"id": 9}], "meta": {"has_more": False}})
    assert MetadataTools(Client()).list_triggers(1, cursor="next") == {"ok": True, "items": [{"id": 9}], "has_more": False, "next_cursor": None, "truncated": False}


def test_group_users_resume_cursor_and_validate_group_id():
    class Client:
        def get(self, path, *, params=None):
            assert path == "/api/v2/groups/4/users.json"
            assert params == {"page[size]": "1", "page[after]": "next"}
            return success({"users": [{"id": 9}], "meta": {"has_more": False}})
    tools = MetadataTools(Client())
    assert tools.list_group_users(4, 1, cursor="next") == {"ok": True, "items": [{"id": 9}], "has_more": False, "next_cursor": None, "truncated": False}
    for invalid in (True, 0, -1, "4", None):
        assert tools.list_group_users(invalid)["error"]["code"] == "validation_error"


def test_custom_statuses_return_bounded_resumable_list():
    class Client:
        def get(self, path, *, params=None):
            assert path == "/api/v2/custom_statuses.json"
            assert params is None
            return success({"custom_statuses": [{"id": 1}, {"id": 2}]})
    t = MetadataTools(Client())
    assert t.list_custom_statuses(limit=1) == {"ok": True, "items": [{"id": 1}], "has_more": True, "next_cursor": "1", "truncated": True}
    assert t.list_custom_statuses(limit=1, cursor="1") == {"ok": True, "items": [{"id": 2}], "has_more": False, "next_cursor": None, "truncated": False}
    for cursor in ("-1", "x", True):
        assert t.list_custom_statuses(cursor=cursor)["error"]["code"] == "validation_error"


def test_custom_statuses_reject_malformed_list():
    class Client:
        def get(self, path, *, params=None):
            return success({"custom_statuses": [None]})
    assert MetadataTools(Client()).list_custom_statuses()["error"]["code"] == "upstream_error"


def test_brand_and_field_lists_resume_cursor():
    for key in ("brands", "ticket_fields", "ticket_forms"):
        class Client:
            def get(self, path, *, params=None):
                assert path == f"/api/v2/{key}.json"
                assert params == {"page[size]": "1", "page[after]": "next"}
                return success({key: [{"id": 9}], "meta": {"has_more": False}})
        assert getattr(MetadataTools(Client()), f"list_{key}")(1, cursor="next") == {"ok": True, "items": [{"id": 9}], "has_more": False, "next_cursor": None, "truncated": False}


def test_metadata_reads_use_fixed_zendesk_endpoints():
    client = StubClient()
    tools = MetadataTools(client)

    assert tools.search_users("agent")["items"] == [{"id": 1}]
    assert tools.list_groups()["items"] == [{"id": 2}]
    assert tools.list_brands()["items"] == [{"id": 3}]
    assert client.paths == [
        ("/api/v2/users/search.json", {"query": "agent", "per_page": "100", "page": "1"}),
        ("/api/v2/groups.json", {"page[size]": "100"}),
        ("/api/v2/brands.json", {"page[size]": "100"}),
    ]


def test_view_macro_and_trigger_reads_use_fixed_endpoints():
    client = StubClient()
    tools = MetadataTools(client)

    tools.list_views()
    tools.get_view(4)
    tools.list_view_tickets(4)
    tools.list_macros()
    tools.list_triggers()

    assert client.paths == [
        ("/api/v2/views.json", {"page[size]": "100"}),
        ("/api/v2/views/4.json", None),
        ("/api/v2/views/4/tickets.json", {"page[size]": "100"}),
        ("/api/v2/macros.json", {"page[size]": "100"}),
        ("/api/v2/triggers.json", {"page[size]": "100"}),
    ]
