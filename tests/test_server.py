import asyncio
import importlib
import json

from mcp import types


def test_conversation_mcp_preserves_limit_cursor_and_untrusted_content(monkeypatch):
    from zendesk_mcp_server.contracts import success
    from zendesk_mcp_server.tools.tickets import TicketTools
    module = importlib.import_module("zendesk_mcp_server.server")
    class Client:
        def get(self, path, *, params=None):
            assert path == "/api/v2/tickets/7/comments.json"
            assert params == {"page[size]": "1", "page[after]": "second", "include": "users"}
            return success({"comments": [{"id": 2, "body": "untrusted", "public": False}], "meta": {"has_more": False}})
    monkeypatch.setattr(module, "build_ticket_tools", lambda _: TicketTools(Client()))
    server = module.create_server({})
    request = types.CallToolRequest(params=types.CallToolRequestParams(name="zendesk_get_ticket_conversation", arguments={"ticket_id": 7, "limit": 1, "cursor": "second"}))
    result = asyncio.run(server.request_handlers[types.CallToolRequest](request))
    assert result.root.structuredContent == {"ok": True, "data": {"comments": [{"id": 2, "body": "untrusted", "public": False, "untrusted_user_content": True, "side": "unknown"}], "has_more": False, "next_cursor": None, "truncated": False}}


def test_scope_expansion_requires_reauthorization_in_factories_and_gate(monkeypatch):
    from zendesk_mcp_server import server as module
    from zendesk_mcp_server.config import ConfigurationError
    def load(_):
        raise ConfigurationError("oauth_relogin_required", "OAuth scope expansion requires login again")
    monkeypatch.setattr(module.Settings, "load", load)
    readers = (module.build_connection_status, module.build_ticket_tools,
               module.build_metadata_tools, module.build_guide_tools, module.build_community_tools,
               lambda env: module._capability_gate(env, "zendesk_get_ticket"))
    for reader in readers:
        result = reader({})
        assert result["error"]["code"] == "reauthorization_required"
        assert result["error"]["operation_state"] == "not_applied"
        assert result["error"]["retryable"] is False


def test_connection_status_redacts_invalid_integration_field_settings():
    from zendesk_mcp_server.server import build_connection_status
    secret_like = "sensitive-setting-marker"
    for values in ({"ZENDESK_GIT_ZEN_FIELD_ID": "9" * 5000},
                   {"ZENDESK_GIT_ZEN_FIELD_ID": secret_like},
                   {"ZENDESK_TIME_TRACKING_TOTAL_FIELD_ID": secret_like, "ZENDESK_TIME_TRACKING_LAST_FIELD_ID": "34"},
                   {"ZENDESK_TIME_TRACKING_TOTAL_FIELD_ID": "12"}):
        result = build_connection_status({"ZENDESK_SUBDOMAIN": "example", **values}, probe=True)
        assert result["ok"] is False
        assert result["error"]["code"] == "validation_error"
        assert result["error"]["operation_state"] == "not_applied"
        assert secret_like not in json.dumps(result)
        assert "9" * 100 not in json.dumps(result)


def test_badge_icon_mcp_preserves_omitted_replacement_and_null(monkeypatch, tmp_path):
    from zendesk_mcp_server.approvals import ApprovalStore
    module = importlib.import_module("zendesk_mcp_server.server")
    writes = []
    class Client:
        def __init__(self, *args, **kwargs): pass
        def request(self, method, path, *, json_body=None):
            writes.append((method, path, json_body))
            return {"ok": True, "data": {"badge": {"id": "badge-1"}}}
    monkeypatch.setattr(module, "ZendeskClient", Client)
    env = {"ZENDESK_SUBDOMAIN": "example", "ZENDESK_EMAIL": "test@example.test", "ZENDESK_API_TOKEN": "test-only", "ZENDESK_CAPABILITIES": "community,badges", "ZENDESK_WRITE_MODE": "standard", "ZENDESK_ENABLE_PUBLIC_WRITES": "true", "ZENDESK_APPROVAL_STORE": str(tmp_path / "approvals.json")}
    server = module.create_server(env)
    def call(arguments):
        request = types.CallToolRequest(params=types.CallToolRequestParams(name="zendesk_update_badge", arguments=arguments))
        return asyncio.run(server.request_handlers[types.CallToolRequest](request)).root.structuredContent
    for change in ({"name": "Renamed"}, {"icon_upload_id": "uploaded-icon"}, {"icon_upload_id": None}):
        arguments = {"badge_id": "badge-1", **change}
        preview = call(arguments)
        assert preview["ok"] is True
        store = ApprovalStore.from_environment(env)
        identifier = preview["data"]["approval_request_id"]
        assert store.preview(identifier)["payload"]["badge"] == change
        token = store.approve(identifier)
        assert call({**arguments, "execution_mode": "apply", "approval_request_id": identifier, "approval_token": token})["ok"] is True
    assert writes == [("PUT", "/api/v2/gather/badges/badge-1", {"badge": change}) for change in ({"name": "Renamed"}, {"icon_upload_id": "uploaded-icon"}, {"icon_upload_id": None})]


def test_badge_delete_mcp_previews_full_cascade_in_local_approval(monkeypatch, tmp_path):
    from zendesk_mcp_server.approvals import ApprovalStore
    module = importlib.import_module("zendesk_mcp_server.server")
    reads = []
    class Client:
        def __init__(self, *args, **kwargs): pass
        def get(self, path, *, params=None):
            reads.append((path, params))
            return {"ok": True, "data": {"badge_assignments": [{"id": f"assignment-{i}", "badge_id": "badge-1", "user_id": str(i)} for i in range(1001)]}}
        def request(self, *args, **kwargs):
            raise AssertionError("preview must not write to Zendesk")
    monkeypatch.setattr(module, "ZendeskClient", Client)
    env = {"ZENDESK_SUBDOMAIN": "example", "ZENDESK_EMAIL": "test@example.test", "ZENDESK_API_TOKEN": "test-only", "ZENDESK_CAPABILITIES": "community,badges", "ZENDESK_APPROVAL_STORE": str(tmp_path / "approvals.json")}
    server = module.create_server(env)
    request = types.CallToolRequest(params=types.CallToolRequestParams(name="zendesk_delete_badge", arguments={"badge_id": "badge-1"}))
    result = asyncio.run(server.request_handlers[types.CallToolRequest](request)).root.structuredContent
    assert result["ok"] is True
    data = result["data"]
    assert data["cascade_assignment_count"] == 1001
    assert data["irreversible"] is True and data["outbound_write"] is False
    approved = ApprovalStore.from_environment(env).preview(data["approval_request_id"])
    assert approved["account"] == "example"
    assert approved["payload"] == {key: data[key] for key in ("badge_id", "cascade_assignment_count", "cascade_snapshot_sha256", "irreversible")}
    assert reads == [("/api/v2/gather/badge_assignments", {"badge_id": "badge-1"})]


def test_custom_object_mcp_search_and_csv_artifact_use_real_factories(monkeypatch, tmp_path):
    import csv
    from pathlib import Path
    from urllib.parse import unquote, urlsplit
    module = importlib.import_module("zendesk_mcp_server.server")
    ticket = {"id": 7, "custom_fields": [{"id": 10, "value": "01GCSJW391QVSC80GYDH7E93Q6"}]}
    record = {"id": "01GCSJW391QVSC80GYDH7E93Q6", "custom_object_fields": {"serial": "example"}}
    class Client:
        def __init__(self, *args, **kwargs): pass
        def get(self, path, *, params=None):
            if path == "/api/v2/search.json": return {"ok": True, "data": {"results": [ticket], "next_page": None}}
            if path == "/api/v2/search/export.json": return {"ok": True, "data": {"results": [ticket], "meta": {"has_more": False}}}
            if path == "/api/v2/ticket_fields.json": return {"ok": True, "data": {"ticket_fields": [{"id": 10, "relationship_target_type": "zen:custom_object:asset"}], "meta": {"has_more": False}}}
            assert path == f"/api/v2/custom_objects/asset/records/{record['id']}.json"
            return {"ok": True, "data": {"custom_object_record": record}}
        def request(self, *args, **kwargs): raise AssertionError("Reads must not mutate Zendesk")
    monkeypatch.setattr(module, "ZendeskClient", Client)
    server = module.create_server({"ZENDESK_SUBDOMAIN": "example", "ZENDESK_EMAIL": "test@example.test", "ZENDESK_API_TOKEN": "test-only", "ZENDESK_CAPABILITIES": "support,custom_objects", "ZENDESK_ATTACHMENT_CACHE_ROOT": str(tmp_path / "attachments")})
    arguments = {"query": "status:open", "projection": {"fields": ["id"], "include_custom_objects": ["asset"]}}
    async def call(name, args):
        return (await server.request_handlers[types.CallToolRequest](types.CallToolRequest(params=types.CallToolRequestParams(name=name, arguments=args)))).root
    search = asyncio.run(call("zendesk_search_tickets", arguments))
    assert search.structuredContent["items"] == [{"id": 7, "custom_objects": {"asset": [record]}}]
    exported = asyncio.run(call("zendesk_export_tickets", {**arguments, "format": "csv"}))
    resource = next(content for content in exported.content if isinstance(content, types.ResourceLink))
    path = Path(unquote(urlsplit(str(resource.uri)).path))
    assert path.resolve().is_relative_to(tmp_path.resolve())
    with path.open(newline="") as stream:
        assert list(csv.DictReader(stream))[0]["asset.serial"] == "example"


def test_time_tracking_app_mcp_loads_field_settings_and_dispatches(monkeypatch):
    module = importlib.import_module("zendesk_mcp_server.server")
    calls = []
    class Client:
        def __init__(self, *args, **kwargs): pass
        def get(self, path, *, params=None):
            assert path == "/api/v2/tickets/7.json"
            return {"ok": True, "data": {"ticket": {"updated_at": "2026-09-12T00:00:00Z", "custom_fields": [{"id": 12, "value": 60}, {"id": 34, "value": 10}]}}}
        def request(self, method, path, *, json_body=None):
            calls.append((method, path, json_body))
            return {"ok": True, "data": {"ticket": {"id": 7}}}
    monkeypatch.setattr(module, "ZendeskClient", Client)
    server = module.create_server({"ZENDESK_SUBDOMAIN": "example", "ZENDESK_EMAIL": "test@example.test", "ZENDESK_API_TOKEN": "test-only", "ZENDESK_CAPABILITIES": "support,time_tracking", "ZENDESK_WRITE_MODE": "standard", "ZENDESK_TIME_TRACKING_TOTAL_FIELD_ID": "12", "ZENDESK_TIME_TRACKING_LAST_FIELD_ID": "34"})
    def call(name, arguments):
        request = types.CallToolRequest(params=types.CallToolRequestParams(name=name, arguments=arguments))
        return asyncio.run(server.request_handlers[types.CallToolRequest](request)).root.structuredContent
    assert call("zendesk_get_time_tracking", {"ticket_id": 7})["data"]["total_time_spent_sec"] == 60
    assert call("zendesk_log_time", {"ticket_id": 7, "time_spent": "2m", "note": "Work"})["ok"] is True
    assert calls == [("PUT", "/api/v2/tickets/7.json", {"ticket": {"custom_fields": [{"id": 12, "value": 180}, {"id": 34, "value": 120}], "comment": {"body": "Work", "public": False}, "safe_update": True, "updated_stamp": "2026-09-12T00:00:00Z"}})]


def test_time_tracking_mcp_preserves_scan_limit_and_cursor(monkeypatch):
    from zendesk_mcp_server.contracts import success
    from zendesk_mcp_server.tools.tickets import TicketTools
    module = importlib.import_module("zendesk_mcp_server.server")
    class Client:
        def get(self, path, *, params=None):
            assert path == "/api/v2/tickets/7/audits.json"
            assert params == {"page[size]": "1", "page[after]": "next", "include_boundary_indicators": "true"}
            return success({"audits": [{"id": 2, "metadata": {"custom": {"time_spent": "1m"}}}], "meta": {"has_more": False}})
    monkeypatch.setattr(module, "build_ticket_tools", lambda _: TicketTools(Client()))
    server = module.create_server({"ZENDESK_CAPABILITIES": "support,time_tracking"})
    request = types.CallToolRequest(params=types.CallToolRequestParams(name="zendesk_get_time_tracking", arguments={"ticket_id": 7, "limit": 1, "cursor": "next"}))
    result = asyncio.run(server.request_handlers[types.CallToolRequest](request))
    assert result.root.structuredContent == {"ok": True, "data": {"entries": [{"audit_id": 2, "author_id": None, "created_at": None, "time_spent": "1m"}], "has_more": False, "next_cursor": None, "truncated": False}}


def test_ticket_search_mcp_preserves_cursor_projection_and_global_cap(monkeypatch):
    from zendesk_mcp_server.contracts import success
    from zendesk_mcp_server.tools.tickets import TicketTools
    module = importlib.import_module("zendesk_mcp_server.server")
    calls = []
    class Client:
        def get(self, path, *, params=None):
            calls.append((path, params))
            assert path == "/api/v2/search.json"
            assert params == {"query": "type:ticket status:open", "per_page": "100", "page": "10"}
            return success({"results": [{"id": i, "subject": "excluded"} for i in range(900, 1000)], "next_page": "https://untrusted.example/next"})
    monkeypatch.setattr(module, "build_ticket_tools", lambda _: TicketTools(Client()))
    server = module.create_server({})
    request = types.CallToolRequest(params=types.CallToolRequestParams(name="zendesk_search_tickets", arguments={"query": "status:open", "limit": 150, "cursor": "950", "projection": {"fields": ["id"]}}))
    result = asyncio.run(server.request_handlers[types.CallToolRequest](request))
    data = result.root.structuredContent
    assert data == {"ok": True, "items": [{"id": i} for i in range(950, 1000)], "has_more": True, "next_cursor": None, "next_page": None, "truncated": True}
    assert len(calls) == 1


def test_guide_mcp_calls_preserve_pagination_arguments(monkeypatch):
    from zendesk_mcp_server.contracts import success
    from zendesk_mcp_server.tools.guide import GuideTools
    module = importlib.import_module("zendesk_mcp_server.server")
    cases = [
        ("list_help_center_categories", {}, "/api/v2/help_center/categories.json", "categories", {}),
        ("list_help_center_sections", {}, "/api/v2/help_center/sections.json", "sections", {}),
        ("list_help_center_categories", {"locale": "ko"}, "/api/v2/help_center/ko/categories.json", "categories", {}),
        ("list_help_center_sections", {"locale": "ko"}, "/api/v2/help_center/ko/sections.json", "sections", {}),
        ("list_help_center_categories", {"sort_by": "position", "sort_order": "desc"}, "/api/v2/help_center/categories.json", "categories", {"sort_by": "position", "sort_order": "desc"}),
        ("list_help_center_sections", {"locale": "ko", "category_id": "cat-A", "sort_by": "updated_at", "sort_order": "asc"}, "/api/v2/help_center/ko/categories/cat-A/sections.json", "sections", {"sort_by": "updated_at", "sort_order": "asc"}),
        ("list_user_segments", {"built_in": False, "applicable": True}, "/api/v2/help_center/user_segments/applicable.json", "user_segments", {"built_in": "false"}),
        ("get_satisfaction_ratings", {}, "/api/v2/satisfaction_ratings.json", "satisfaction_ratings", {}),
        ("list_csat", {"backend": "legacy", "score": "good"}, "/api/v2/satisfaction_ratings.json", "satisfaction_ratings", {"score": "good"}),
        ("list_csat", {"backend": "survey", "ticket_id": 9}, "/api/v2/guide/survey_responses", "survey_responses", {"filter[subject_zrns]": "zen:ticket:9"}),
        ("list_permission_groups", {}, "/api/v2/guide/permission_groups.json", "permission_groups", None),
        ("search_help_center_articles", {"query": "billing"}, "/api/v2/help_center/articles/search.json", "results", None),
    ]
    for name, arguments, endpoint, key, filters in cases:
        class Client:
            def get(self, path, *, params=None):
                if path == "/api/v2/help_center/locales.json":
                    return success({"locales": ["ko"]})
                assert path == endpoint
                if filters is None:
                    expected = {"per_page": "100", "page": "1"}
                    if key == "results": expected["query"] = "billing"
                    assert params == expected
                    return success({key: [{"id": 8}, {"id": 9}], "next_page": None})
                assert params == {**filters, "page[size]": "1", "page[after]": "1"}
                return success({key: [{"id": 9}], "meta": {"has_more": False}})
        monkeypatch.setattr(module, "build_guide_tools", lambda _: GuideTools(Client()))
        server = module.create_server({"ZENDESK_CAPABILITIES": "guide,csat"})
        request = types.CallToolRequest(params=types.CallToolRequestParams(name=f"zendesk_{name}", arguments={**arguments, "limit": 1, "cursor": "1"}))
        result = asyncio.run(server.request_handlers[types.CallToolRequest](request))
        expected_result = {"ok": True, "items": [{"id": 9}], "has_more": False, "next_cursor": None, "truncated": False}
        if name in {"get_satisfaction_ratings", "list_csat"}: expected_result["untrusted_user_content"] = True
        assert result.root.structuredContent == expected_result, name


def test_mcp_article_all_translations_returns_metadata_and_image_content(monkeypatch):
    from zendesk_mcp_server.contracts import success
    from zendesk_mcp_server.tools.guide import GuideTools
    module = importlib.import_module("zendesk_mcp_server.server")
    class Client:
        def get(self, path, *, params=None):
            if path.endswith("locales.json"): return success({"locales": ["ko"]})
            if path.endswith("translations.json"):
                return success({"translations": [{"locale": "ko", "body": '<img src="https://acme.zendesk.com/hc/user_images/one.png">'}], "meta": {"has_more": False}})
            assert params == {"include": "users,sections,categories"}
            return success({"article": {"id": 7, "author_id": 9}, "users": [{"id": 9, "name": "Author"}]})
        def download_help_center_image(self, url, *, max_bytes, subdomain):
            return success({"content": b"image", "content_type": "image/png"})
    monkeypatch.setattr(module, "build_guide_tools", lambda _: GuideTools(Client()))
    server = module.create_server({"ZENDESK_CAPABILITIES": "guide"})
    request = types.CallToolRequest(params=types.CallToolRequestParams(name="zendesk_get_help_center_article", arguments={"article_id": 7, "locale": "all", "embed_images": True, "include_metadata": True}))
    result = asyncio.run(server.request_handlers[types.CallToolRequest](request))
    assert result.root.structuredContent["data"]["metadata"] == {"author_name": "Author", "category_name": None, "section_name": None}
    assert result.root.structuredContent["data"]["translations"][0]["locale"] == "ko"
    assert result.root.structuredContent["data"]["untrusted_user_content"] is True
    assert any(isinstance(item, types.ImageContent) for item in result.root.content)


def test_mcp_scoped_export_uses_default_locale_and_returns_file(monkeypatch, tmp_path):
    from zendesk_mcp_server.contracts import success
    from zendesk_mcp_server.config import Settings
    from zendesk_mcp_server.tools.guide import GuideTools
    module = importlib.import_module("zendesk_mcp_server.server")
    class Client:
        def get(self, path, *, params=None):
            if path == "/api/v2/help_center/locales.json":
                return success({"locales": ["ko"], "default_locale": "ko"})
            assert path == "/api/v2/help_center/ko/sections/sec-A/articles.json"
            return success({"articles": [{"id": "a", "body": "본문"}], "meta": {"has_more": False}})
    settings = Settings.load({"ZENDESK_ATTACHMENT_CACHE_ROOT": str(tmp_path / "attachments")})
    monkeypatch.setattr(module, "build_guide_tools", lambda _: GuideTools(Client(), settings))
    server = module.create_server({"ZENDESK_CAPABILITIES": "guide"})
    request = types.CallToolRequest(params=types.CallToolRequestParams(name="zendesk_export_help_center_articles", arguments={"section_id": "sec-A", "category_id": 9, "format": "csv"}))
    result = asyncio.run(server.request_handlers[types.CallToolRequest](request))
    assert result.root.structuredContent["ok"] is True
    assert result.root.structuredContent["data"]["item_count"] == 1
    assert any(isinstance(item, types.ResourceLink) for item in result.root.content)


def test_mcp_unified_article_search_accepts_filter_only_query_and_opaque_cursor(monkeypatch):
    from zendesk_mcp_server.contracts import success
    from zendesk_mcp_server.tools.guide import GuideTools
    module = importlib.import_module("zendesk_mcp_server.server")
    class Client:
        def get(self, path, *, params=None):
            if path == "/api/v2/brands/7.json":
                return success({"brand": {"subdomain": "one", "has_help_center": True}})
            assert path == "/api/v2/guide/search"
            assert params == {"filter[locales]": "ko", "filter[content_types]": "ARTICLE", "filter[brand_ids]": "7", "filter[category_ids]": "cat-A", "filter[section_ids]": "sec-B", "page[size]": "1", "page[after]": "opaque=="}
            return success({"results": [{"title": "Guide", "type": "ARTICLE"}], "meta": {"has_more": False}})
        def get_for_subdomain(self, subdomain, path, *, params=None):
            assert subdomain == "one" and path == "/api/v2/help_center/locales.json"
            return success({"locales": ["ko"]})
    monkeypatch.setattr(module, "build_guide_tools", lambda _: GuideTools(Client()))
    server = module.create_server({"ZENDESK_CAPABILITIES": "guide"})
    request = types.CallToolRequest(params=types.CallToolRequestParams(name="zendesk_search_help_center_articles", arguments={"locales": ["ko"], "brand_ids": [7], "category_ids": ["cat-A"], "section_ids": ["sec-B"], "limit": 1, "cursor": "opaque=="}))
    result = asyncio.run(server.request_handlers[types.CallToolRequest](request))
    assert result.root.structuredContent == {"ok": True, "items": [{"title": "Guide", "type": "ARTICLE"}], "has_more": False, "next_cursor": None, "truncated": False}


def test_metadata_mcp_calls_preserve_pagination_arguments(monkeypatch):
    from zendesk_mcp_server.contracts import success
    from zendesk_mcp_server.tools.metadata import MetadataTools
    module = importlib.import_module("zendesk_mcp_server.server")
    cases = [
        ("list_groups", {}, "/api/v2/groups.json", "groups"),
        ("list_group_users", {"group_id": 4}, "/api/v2/groups/4/users.json", "users"),
        ("list_brands", {}, "/api/v2/brands.json", "brands"),
        ("list_ticket_fields", {}, "/api/v2/ticket_fields.json", "ticket_fields"),
        ("list_ticket_forms", {}, "/api/v2/ticket_forms.json", "ticket_forms"),
        ("list_ticket_forms", {"active": False}, "/api/v2/ticket_forms.json", "ticket_forms"),
        ("list_views", {}, "/api/v2/views.json", "views"),
        ("list_view_tickets", {"view_id": 4}, "/api/v2/views/4/tickets.json", "tickets"),
        ("list_macros", {}, "/api/v2/macros.json", "macros"),
        ("list_triggers", {}, "/api/v2/triggers.json", "triggers"),
        ("list_triggers", {"active": False, "category_id": "10026", "sort": "position", "sort_order": "desc"}, "/api/v2/triggers.json", "triggers"),
        ("list_triggers", {"include_usage": True}, "/api/v2/triggers.json", "triggers"),
        ("search_users", {"query": "agent"}, "/api/v2/users/search.json", "users"),
        ("search_users", {"query": "agent", "agents_only": True, "exact_name": True}, "/api/v2/users/search.json", "users"),
        ("list_custom_statuses", {}, "/api/v2/custom_statuses.json", "custom_statuses"),
    ]
    for name, arguments, endpoint, key in cases:
        class Client:
            def get(self, path, *, params=None):
                assert path == endpoint
                if name == "search_users":
                    assert params == {"query": "agent", "per_page": "100", "page": "1"}
                    return success({key: [{"id": 8}, {"id": 9}], "next_page": None})
                if name == "list_custom_statuses":
                    assert params is None
                    return success({key: [{"id": 8}, {"id": 9}]})
                filters = {key: (str(value).lower() if isinstance(value, bool) else value) for key, value in arguments.items() if key in {"active", "category_id", "sort", "sort_order"}}
                if arguments.get("include_usage"):
                    filters["include"] = "usage_1h,usage_24h,usage_7d,usage_30d"
                assert params == {**filters, "page[size]": "1", "page[after]": "1"}
                return success({key: [{"id": 9}], "meta": {"has_more": False}})
        monkeypatch.setattr(module, "build_metadata_tools", lambda _: MetadataTools(Client()))
        server = module.create_server({"ZENDESK_CAPABILITIES": "operations"})
        request = types.CallToolRequest(params=types.CallToolRequestParams(name=f"zendesk_{name}", arguments={**arguments, "limit": 1, "cursor": "1"}))
        result = asyncio.run(server.request_handlers[types.CallToolRequest](request))
        assert result.root.structuredContent == {"ok": True, "items": [] if arguments.get("agents_only") else [{"id": 9}], "has_more": False, "next_cursor": None, "truncated": False}, name


def test_ticket_list_exposes_resume_cursor():
    from zendesk_mcp_server.server import build_tools
    tool = next(tool for tool in build_tools() if tool.name == "zendesk_list_tickets")
    assert tool.inputSchema["properties"]["cursor"] == {"type": "string", "minLength": 1}


def test_mcp_conversation_log_source_and_cursor_are_forwarded(monkeypatch):
    from zendesk_mcp_server.contracts import success
    from zendesk_mcp_server.tools.tickets import TicketTools
    module = importlib.import_module("zendesk_mcp_server.server")
    class Client:
        def get(self, path, *, params=None):
            assert path == "/api/v2/tickets/9/conversation_log"
            assert params == {"sort": "created_at", "page[size]": "1", "page[after]": "next"}
            return success({"events": [{"id": "message-b"}], "meta": {"has_more": False}})
    monkeypatch.setattr(module, "build_ticket_tools", lambda _: TicketTools(Client()))
    server = module.create_server({"ZENDESK_CAPABILITIES": "support"})
    request = types.CallToolRequest(params=types.CallToolRequestParams(name="zendesk_get_ticket_conversation", arguments={"ticket_id": 9, "source": "conversation_log", "limit": 1, "cursor": "next"}))
    result = asyncio.run(server.request_handlers[types.CallToolRequest](request))
    assert result.root.structuredContent["data"]["events"] == [{"id": "message-b", "untrusted_user_content": True}]


def test_mcp_conversation_auto_selects_log_from_ticket_origin(monkeypatch):
    from zendesk_mcp_server.contracts import success
    from zendesk_mcp_server.tools.tickets import TicketTools
    module = importlib.import_module("zendesk_mcp_server.server")
    calls = []
    class Client:
        def get(self, path, *, params=None):
            calls.append(path)
            if path == "/api/v2/tickets/9.json": return success({"ticket": {"id": 9, "from_messaging_channel": True}})
            assert path == "/api/v2/tickets/9/conversation_log"
            return success({"events": [], "meta": {"has_more": False}})
    monkeypatch.setattr(module, "build_ticket_tools", lambda _: TicketTools(Client()))
    server = module.create_server({"ZENDESK_CAPABILITIES": "support"})
    request = types.CallToolRequest(params=types.CallToolRequestParams(name="zendesk_get_ticket_conversation", arguments={"ticket_id": 9, "source": "auto"}))
    result = asyncio.run(server.request_handlers[types.CallToolRequest](request))
    assert result.root.structuredContent["data"]["source"] == "conversation_log"
    assert calls == ["/api/v2/tickets/9.json", "/api/v2/tickets/9/conversation_log"]


def test_mcp_assignment_passes_email_selector_to_resolution(monkeypatch):
    from zendesk_mcp_server.config import Settings
    from zendesk_mcp_server.contracts import success
    from zendesk_mcp_server.tools.tickets import TicketTools
    module = importlib.import_module("zendesk_mcp_server.server")
    writes = []
    class Client:
        def get(self, path, *, params=None):
            assert path == "/api/v2/users/me.json"
            return success({"user": {"id": 7, "role": "admin", "suspended": False}})
        def request(self, method, path, *, json_body=None):
            writes.append((method, path, json_body))
            return success({"ticket": {"id": 9}})
    monkeypatch.setattr(module, "build_ticket_tools", lambda _: TicketTools(Client(), Settings.load({"ZENDESK_WRITE_MODE": "standard"})))
    server = module.create_server({"ZENDESK_CAPABILITIES": "support", "ZENDESK_WRITE_MODE": "standard"})
    request = types.CallToolRequest(params=types.CallToolRequestParams(name="zendesk_assign_ticket", arguments={"ticket_id": 9, "assignee_email": "me"}))
    result = asyncio.run(server.request_handlers[types.CallToolRequest](request))
    assert result.root.structuredContent["ok"] is True
    assert writes == [("PUT", "/api/v2/tickets/9.json", {"ticket": {"assignee_id": 7}})]


def test_csat_schemas_discriminate_backend_filters():
    from jsonschema import Draft202012Validator
    from zendesk_mcp_server.server import build_tools
    for tool in build_tools():
        if tool.name not in ("zendesk_list_csat", "zendesk_export_satisfaction_ratings"): continue
        validator = Draft202012Validator(tool.inputSchema)
        for arguments in ({}, {"backend": "legacy", "score": "good", "start_time": "2026-09-01T00:00:00Z"}, {"backend": "survey", "ticket_id": 9, "responder_ids": [7]}, {"backend": "auto", "score": "bad"}, {"ticket_id": 9}):
            assert validator.is_valid(arguments), arguments
        for arguments in ({"backend": "legacy", "ticket_id": 9}, {"backend": "legacy", "responder_ids": [7]}, {"backend": "survey", "score": "good"}, {"score": "good", "ticket_id": 9}, {"score": "invalid"}, {"responder_ids": []}, {"backend": "unknown"}, {"backend": "legacy", "created_at_start": "2026-09-01T00:00:00Z"}, {"backend": "survey", "start_time": "2026-09-01T00:00:00Z"}, {"start_time": "2026-09-01T00:00:00Z", "created_at_end": "2026-09-02T00:00:00Z"}, {"start_tim": "ignored typo"}):
            assert not validator.is_valid(arguments), arguments


def test_csat_mcp_rejects_cross_backend_filters_before_client_build(monkeypatch):
    module = importlib.import_module("zendesk_mcp_server.server")
    def forbidden(_): raise AssertionError("Client must not be built for invalid input")
    monkeypatch.setattr(module, "build_guide_tools", forbidden)
    server = module.create_server({"ZENDESK_CAPABILITIES": "csat"})
    for name in ("zendesk_list_csat", "zendesk_export_satisfaction_ratings"):
        request = types.CallToolRequest(params=types.CallToolRequestParams(name=name, arguments={"backend": "legacy", "ticket_id": 9}))
        result = asyncio.run(server.request_handlers[types.CallToolRequest](request))
        assert result.root.isError is True
        assert "Client must not" not in str(result.root.content)


def test_csat_mcp_dates_reach_list_and_export_adapters(monkeypatch, tmp_path):
    from zendesk_mcp_server.config import Settings
    from zendesk_mcp_server.contracts import success
    from zendesk_mcp_server.tools.guide import GuideTools
    module = importlib.import_module("zendesk_mcp_server.server")
    for name in ("zendesk_list_csat", "zendesk_export_satisfaction_ratings"):
        for backend in ("legacy", "survey", "auto"):
            calls = []
            class Client:
                def get(self, path, *, params=None):
                    calls.append(path)
                    if path == "/api/v2/account/settings.json":
                        return success({"settings": {"active_features": {"customer_satisfaction": True, "customer_satisfaction_survey": False}}})
                    if backend == "survey":
                        assert params["filter[created_at_start]"] == "1788220800000"
                        key = "survey_responses"
                    else:
                        assert params["start_time"] == "1788220800"
                        assert params["end_time"] == "1788307200"
                        key = "satisfaction_ratings"
                    return success({key: [], "meta": {"has_more": False}})
            settings = Settings.load({"ZENDESK_ATTACHMENT_CACHE_ROOT": str(tmp_path / "attachments")})
            monkeypatch.setattr(module, "build_guide_tools", lambda _: GuideTools(Client(), settings))
            server = module.create_server({"ZENDESK_CAPABILITIES": "csat"})
            dates = {"created_at_start": "2026-09-01T09:00:00+09:00"} if backend == "survey" else {"start_time": "2026-09-01T09:00:00+09:00", "end_time": "2026-09-02T00:00:00Z"}
            request = types.CallToolRequest(params=types.CallToolRequestParams(name=name, arguments={"backend": backend, **dates}))
            result = asyncio.run(server.request_handlers[types.CallToolRequest](request))
            assert result.root.structuredContent["ok"] is True
            assert len(calls) == (2 if backend == "auto" else 1)


def test_attachment_download_result_includes_a_resource_link():
    from zendesk_mcp_server.server import attachment_download_content

    content = attachment_download_content(
        {
            "ok": True,
            "data": {
                "ticket_id": 7,
                "attachment_id": 5,
                "cache_path": "/private/cache/5/attachment",
                "content_type": "text/plain",
            },
        }
    )

    assert isinstance(content[0], types.TextContent)
    assert isinstance(content[1], types.ResourceLink)
    assert str(content[1].uri) == "file:///private/cache/5/attachment"
    assert content[1].mimeType == "text/plain"


def test_attachment_image_result_uses_image_content():
    from zendesk_mcp_server.server import attachment_inspection_content

    content = attachment_inspection_content(
        {"ok": True, "data": {"ticket_id": 7, "attachment_id": 5, "kind": "image", "mime_type": "image/png", "width": 1, "height": 1, "image_data": "iVBORw0KGgo="}}
    )

    assert isinstance(content[0], types.TextContent)
    assert "image_data" not in content[0].text
    assert isinstance(content[1], types.ImageContent)
    assert content[1].mimeType == "image/png"


def test_help_center_article_image_result_uses_image_content():
    from zendesk_mcp_server.server import help_center_article_content

    content = help_center_article_content(
        {"ok": True, "data": {"article": {"id": 3}, "images": [{"src": "https://acme.zendesk.com/hc/user_images/image.png", "content": b"image", "content_type": "image/png"}]}}
    )

    assert isinstance(content[0], types.TextContent)
    assert '"content":' not in content[0].text
    assert isinstance(content[1], types.ImageContent)
    assert content[1].data == "aW1hZ2U="
    assert content[1].mimeType == "image/png"


def test_ticket_export_result_includes_a_resource_link_without_its_cache_path():
    from zendesk_mcp_server.server import ticket_export_content

    content = ticket_export_content({"ok": True, "data": {"format": "csv", "cache_path": "/private/cache/exports/tickets.csv", "item_count": 2, "truncated": False, "size": 42}})

    assert isinstance(content[0], types.TextContent)
    assert "cache_path" not in str(content[0].text)
    assert isinstance(content[1], types.ResourceLink)
    assert str(content[1].uri) == "file:///private/cache/exports/tickets.csv"
    assert content[1].mimeType == "text/csv"
    assert content[1].size == 42


def test_csat_export_result_includes_a_resource_link_without_its_cache_path():
    from zendesk_mcp_server.server import csat_export_content

    content = csat_export_content({"ok": True, "data": {"format": "json", "cache_path": "/private/cache/exports/csat.json", "item_count": 2}})

    assert isinstance(content[0], types.TextContent)
    assert "cache_path" not in str(content[0].text)
    assert isinstance(content[1], types.ResourceLink)
    assert content[1].name == "Zendesk CSAT export (json)"


def test_help_center_export_result_includes_a_resource_link_without_its_cache_path():
    from zendesk_mcp_server.server import help_center_export_content

    content = help_center_export_content({"ok": True, "data": {"format": "csv", "cache_path": "/private/cache/exports/articles.csv", "item_count": 2}})

    assert isinstance(content[1], types.ResourceLink)
    assert content[1].name == "Zendesk Help Center export (csv)"


def test_knowledge_base_resource_is_registered_only_when_enabled():
    from zendesk_mcp_server.server import create_server

    disabled = create_server({})
    enabled = create_server({"ZENDESK_ENABLE_KNOWLEDGE_BASE_RESOURCE": "true"})

    assert types.ListResourcesRequest not in disabled.request_handlers
    result = asyncio.run(enabled.request_handlers[types.ListResourcesRequest](types.ListResourcesRequest()))
    assert str(result.root.resources[0].uri) == "zendesk://knowledge-base"


def test_knowledge_base_resource_reuses_locale_article_exports(monkeypatch):
    from zendesk_mcp_server import server as server_module
    from zendesk_mcp_server.contracts import success

    class GuideExport:
        def list_locales(self): return success({"locales": ["en-us"]})
        def export_article_artifact(self, locale, max_articles=100000): return success({"item_count": 1, "cache_path": "/tmp/kb.json", "format": "json", "truncated": False})

    monkeypatch.setattr(server_module, "build_guide_tools", lambda _: GuideExport())
    server = server_module.create_server({"ZENDESK_ENABLE_KNOWLEDGE_BASE_RESOURCE": "true"})
    request = types.ReadResourceRequest(params=types.ReadResourceRequestParams(uri="zendesk://knowledge-base"))

    result = asyncio.run(server.request_handlers[types.ReadResourceRequest](request))

    import json
    data = json.loads(result.root.contents[0].text)["data"]
    entry = data["locales"][0]
    assert entry["locale"] == "en-us" and entry["item_count"] == 1
    assert entry["resource"]["type"] == "resource_link"
    assert entry["resource"]["uri"] == "file:///tmp/kb.json"
    assert "articles" not in entry and "cache_path" not in entry
    assert data["truncated"] is False


def test_knowledge_base_manifest_points_to_real_streamed_export(tmp_path, monkeypatch):
    import json
    from pathlib import Path
    from urllib.parse import urlsplit, unquote
    from zendesk_mcp_server import server as module
    from zendesk_mcp_server.config import Settings
    from zendesk_mcp_server.contracts import success
    from zendesk_mcp_server.tools.guide import GuideTools
    class Client:
        def get(self, path, *, params=None):
            if path.endswith("locales.json"): return success({"locales": ["en-us"]})
            assert path == "/api/v2/help_center/en-us/articles.json"
            return success({"articles": [{"id": 7, "body": "article body"}], "meta": {"has_more": False}})
    guide = GuideTools(Client(), Settings.load({"ZENDESK_ATTACHMENT_CACHE_ROOT": str(tmp_path / "attachments")}))
    monkeypatch.setattr(module, "build_guide_tools", lambda _: guide)
    server = module.create_server({"ZENDESK_ENABLE_KNOWLEDGE_BASE_RESOURCE": "true"})
    request = types.ReadResourceRequest(params=types.ReadResourceRequestParams(uri="zendesk://knowledge-base"))
    result = asyncio.run(server.request_handlers[types.ReadResourceRequest](request))
    text = result.root.contents[0].text
    entry = json.loads(text)["data"]["locales"][0]
    path = Path(unquote(urlsplit(entry["resource"]["uri"]).path))
    assert path.is_relative_to(tmp_path)
    assert json.loads(path.read_text()) == [{"id": 7, "body": "article body"}]
    assert entry["item_count"] == 1 and "article body" not in text
    assert entry["size"] == path.stat().st_size
    assert entry["resource"]["size"] == path.stat().st_size
    assert entry["mime_type"] == "application/json"


def test_knowledge_base_shares_total_limit_across_locales(monkeypatch):
    import json
    from zendesk_mcp_server import server as server_module
    from zendesk_mcp_server.contracts import success
    class GuideExport:
        calls = []
        def list_locales(self): return success({"locales": ["en-us", "ko", "ja"]})
        def export_article_artifact(self, locale, max_articles=100000):
            self.calls.append((locale, max_articles))
            count = 99999 if locale == "en-us" else max_articles
            return success({"item_count": count, "cache_path": "/tmp/kb.json", "truncated": False})
    guide = GuideExport()
    monkeypatch.setattr(server_module, "build_guide_tools", lambda _: guide)
    server = server_module.create_server({"ZENDESK_ENABLE_KNOWLEDGE_BASE_RESOURCE": "true"})
    request = types.ReadResourceRequest(params=types.ReadResourceRequestParams(uri="zendesk://knowledge-base"))
    result = asyncio.run(server.request_handlers[types.ReadResourceRequest](request))
    body = json.loads(result.root.contents[0].text)
    assert guide.calls == [("en-us", 100000), ("ko", 1)]
    assert sum(item["item_count"] for item in body["data"]["locales"]) == 100000
    assert body["data"]["truncated"] is True


def test_knowledge_base_preserves_truncation_and_rejects_invalid_export(monkeypatch):
    import json
    from zendesk_mcp_server import server as server_module
    from zendesk_mcp_server.contracts import success
    for payload in ({"item_count": 1, "cache_path": "/tmp/kb.json", "truncated": True}, {"item_count": "invalid", "truncated": False}):
        class GuideExport:
            def list_locales(self): return success({"locales": ["en-us"]})
            def export_article_artifact(self, locale, max_articles=100000): return success(payload)
        monkeypatch.setattr(server_module, "build_guide_tools", lambda _: GuideExport())
        server = server_module.create_server({"ZENDESK_ENABLE_KNOWLEDGE_BASE_RESOURCE": "true"})
        request = types.ReadResourceRequest(params=types.ReadResourceRequestParams(uri="zendesk://knowledge-base"))
        result = asyncio.run(server.request_handlers[types.ReadResourceRequest](request))
        body = json.loads(result.root.contents[0].text)
        if payload["truncated"]:
            assert body["data"]["truncated"] is True
            assert body["data"]["locales"][0]["truncated"] is True
        else:
            assert body["error"]["code"] == "upstream_error"


def test_knowledge_base_resource_caches_its_export_for_one_hour(monkeypatch):
    from zendesk_mcp_server import server as server_module
    from zendesk_mcp_server.contracts import success
    clock = {"wall": 10000, "elapsed": 0}
    monkeypatch.setattr(server_module.time, "time", lambda: clock["wall"])
    monkeypatch.setattr(server_module.time, "monotonic", lambda: clock["elapsed"])

    class GuideExport:
        calls = 0
        def list_locales(self): self.calls += 1; return success({"locales": ["en-us"]})
        def export_article_artifact(self, locale, max_articles=100000): self.calls += 1; return success({"item_count": 0, "cache_path": "/tmp/kb.json", "truncated": False})

    guide = GuideExport()
    monkeypatch.setattr(server_module, "build_guide_tools", lambda _: guide)
    server = server_module.create_server({"ZENDESK_ENABLE_KNOWLEDGE_BASE_RESOURCE": "true"})
    request = types.ReadResourceRequest(params=types.ReadResourceRequestParams(uri="zendesk://knowledge-base"))

    asyncio.run(server.request_handlers[types.ReadResourceRequest](request))
    clock.update(wall=13599, elapsed=3599)
    asyncio.run(server.request_handlers[types.ReadResourceRequest](request))

    assert guide.calls == 2
    clock.update(wall=9000, elapsed=3600)
    asyncio.run(server.request_handlers[types.ReadResourceRequest](request))
    assert guide.calls == 4


def test_ticket_search_tools_accept_the_shared_structured_filter():
    from zendesk_mcp_server.server import build_tools

    tools = {tool.name: tool for tool in build_tools()}

    for name in ("zendesk_search_tickets", "zendesk_count_tickets", "zendesk_export_tickets"):
        assert tools[name].inputSchema["properties"]["query"]["oneOf"][1]["type"] == "object"
    for name in ("zendesk_search_tickets", "zendesk_export_tickets"):
        assert set(tools[name].inputSchema["properties"]["projection"]["properties"]) == {"fields", "include_custom_objects"}


def test_macro_preview_has_a_canonical_tool_registration():
    from zendesk_mcp_server.server import build_tools

    tools = {tool.name: tool for tool in build_tools()}

    assert tools["zendesk_preview_macro"].inputSchema["required"] == ["ticket_id", "macro_id"]


def test_csat_export_has_a_canonical_tool_registration():
    from zendesk_mcp_server.server import build_tools

    tools = {tool.name: tool for tool in build_tools()}

    assert tools["zendesk_export_satisfaction_ratings"].inputSchema["properties"]["format"]["enum"] == ["json", "csv"]


def test_help_center_export_accepts_an_artifact_format():
    from zendesk_mcp_server.server import build_tools

    tools = {tool.name: tool for tool in build_tools()}

    assert tools["zendesk_export_help_center_articles"].inputSchema["properties"]["format"]["enum"] == ["json", "csv"]


def test_help_center_listing_tools_accept_a_brand_id():
    from zendesk_mcp_server.server import build_tools

    tools = {tool.name: tool for tool in build_tools()}

    for name in ("zendesk_list_help_center_locales", "zendesk_list_help_center_categories", "zendesk_list_help_center_sections", "zendesk_export_help_center_articles"):
        assert tools[name].inputSchema["properties"]["brand_id"]["minimum"] == 1


def test_help_center_search_and_article_read_accept_brand_scope():
    from zendesk_mcp_server.server import build_tools

    tools = {tool.name: tool for tool in build_tools()}

    assert tools["zendesk_search_help_center_articles"].inputSchema["properties"]["brand_id"]["minimum"] == 1
    assert tools["zendesk_search_help_center_articles"].inputSchema["properties"]["locale"]["minLength"] == 2
    assert tools["zendesk_get_help_center_article"].inputSchema["properties"]["brand_id"]["minimum"] == 1
    assert tools["zendesk_get_help_center_article"].inputSchema["properties"]["locale"]["minLength"] == 2
    assert tools["zendesk_get_help_center_article"].inputSchema["properties"]["embed_images"]["default"] is False


def test_every_tool_declares_mcp_risk_annotations():
    from zendesk_mcp_server.server import build_tools

    tools = {tool.name: tool for tool in build_tools()}

    assert all(tool.annotations is not None and tool.annotations.readOnlyHint is not None and tool.annotations.destructiveHint is not None and tool.annotations.idempotentHint is not None and tool.annotations.openWorldHint is not None for tool in tools.values())
    assert tools["zendesk_get_ticket"].annotations.readOnlyHint is True
    assert tools["zendesk_delete_community_post"].annotations.destructiveHint is True
    assert tools["zendesk_remove_ticket_tag"].annotations.destructiveHint is False
    assert tools["zendesk_update_ticket"].annotations.destructiveHint is True
    assert tools["zendesk_set_ticket_status"].annotations.destructiveHint is True
    assert tools["zendesk_create_ticket"].annotations.readOnlyHint is False


def test_every_tool_declares_the_standard_result_schema():
    from zendesk_mcp_server.server import build_tools

    tools = build_tools()

    assert all(tool.outputSchema is not None for tool in tools)
    assert all(tool.outputSchema["required"] == ["ok"] for tool in tools)


def test_mcp_catalog_matches_frozen_input_output_and_risk_contracts():
    from pathlib import Path
    from zendesk_mcp_server.server import create_server

    # Intentional contract changes require reviewing the affected fixture entry
    # against the PRD and migration notes, not regenerating the whole baseline.
    expected = json.loads((Path(__file__).parent / "fixtures/tool-contracts.json").read_text())
    server = create_server({})
    result = asyncio.run(server.request_handlers[types.ListToolsRequest](types.ListToolsRequest()))
    tools = result.root.tools
    assert len(tools) == len(expected) == 101
    assert {tool.name for tool in tools} == set(expected)
    for tool in tools:
        actual = {"inputSchema": tool.inputSchema, "outputSchema": tool.outputSchema,
                  "annotations": tool.annotations.model_dump(mode="json", exclude_none=True)}
        assert actual == expected[tool.name], tool.name


def test_help_center_article_create_accepts_brand_scope():
    from zendesk_mcp_server.server import build_tools

    tools = {tool.name: tool for tool in build_tools()}

    assert tools["zendesk_create_help_center_article"].inputSchema["properties"]["brand_id"]["minimum"] == 1
    assert tools["zendesk_create_help_center_article"].inputSchema["properties"]["section_id"]["oneOf"][1]["type"] == "string"


def test_help_center_translation_tools_accept_brand_scope():
    from zendesk_mcp_server.server import build_tools

    tools = {tool.name: tool for tool in build_tools()}

    for name in ("zendesk_upsert_article_translation", "zendesk_replace_article_translation_body", "zendesk_publish_help_center_article"):
        assert tools[name].inputSchema["properties"]["brand_id"]["minimum"] == 1
        assert tools[name].inputSchema["properties"]["article_id"]["oneOf"][1]["type"] == "string"


def test_server_import_does_not_require_credentials(monkeypatch):
    for name in (
        "ZENDESK_SUBDOMAIN",
        "ZENDESK_EMAIL",
        "ZENDESK_API_TOKEN",
        "ZENDESK_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)

    module = importlib.import_module("zendesk_mcp_server.server")

    assert callable(module.create_server)


def test_connection_status_is_available_without_configuration():
    from zendesk_mcp_server.server import build_connection_status

    assert build_connection_status({}) == {
        "ok": True,
        "data": {
            "configured": False,
            "auth_mode": None,
            "write_mode": "read_only",
            "active_write_gates": [],
            "capabilities": ["community", "guide", "operations", "support"],
        },
    }


def test_connection_status_does_not_expose_credentials():
    from zendesk_mcp_server.server import build_connection_status

    result = build_connection_status(
        {
            "ZENDESK_SUBDOMAIN": "acme",
            "ZENDESK_EMAIL": "agent@example.test",
            "ZENDESK_API_TOKEN": "secret",
        }
    )

    assert result["data"]["auth_mode"] == "api_token"
    assert "secret" not in repr(result)
    assert "agent@example.test" not in repr(result)


def test_oauth_authorization_failure_requires_reauthorization(monkeypatch):
    from zendesk_mcp_server import server as server_module
    from zendesk_mcp_server.config import ConfigurationError

    def fail_refresh(_):
        raise ConfigurationError("oauth_refresh_failed", "OAuth token refresh failed")

    monkeypatch.setattr(server_module, "build_authorization", fail_refresh)

    result = server_module.build_ticket_tools(
        {
            "ZENDESK_SUBDOMAIN": "acme",
            "ZENDESK_AUTH_MODE": "oauth",
            "ZENDESK_OAUTH_CLIENT_ID": "id",
            "ZENDESK_OAUTH_CLIENT_SECRET": "secret",
            "ZENDESK_OAUTH_TOKEN_STORE": "/private/oauth.json",
        }
    )

    assert result["error"]["code"] == "reauthorization_required"


def test_content_subscription_mcp_dispatch_preserves_scope_limit_and_cursor(monkeypatch):
    import zendesk_mcp_server.server as module
    from zendesk_mcp_server.contracts import success
    from zendesk_mcp_server.tools.community import CommunityTools

    calls = []
    class Client:
        def get(self, path, *, params=None):
            calls.append((path, params))
            return success({"subscriptions": [{"id": 7}], "meta": {"has_more": True, "after_cursor": "next"}})
    monkeypatch.setattr(module, "build_community_tools", lambda _: CommunityTools(Client()))
    server = module.create_server({"ZENDESK_CAPABILITIES": "community"})
    for kind in ("post", "topic"):
        request = types.CallToolRequest(params=types.CallToolRequestParams(name="zendesk_list_content_subscriptions", arguments={"content_type": kind, "content_id": 4, "limit": 1, "cursor": "before"}))
        result = asyncio.run(server.request_handlers[types.CallToolRequest](request)).root
        assert result.structuredContent == {"ok": True, "items": [{"id": 7}], "has_more": True, "next_cursor": "next", "truncated": True}
        assert calls[-1] == (f"/api/v2/community/{kind}s/4/subscriptions.json", {"page[size]": "1", "page[after]": "before"})
    assert len(calls) == 2


def test_badge_list_mcp_dispatch_preserves_local_cursor_and_filters(monkeypatch):
    import zendesk_mcp_server.server as module
    from zendesk_mcp_server.contracts import success
    from zendesk_mcp_server.tools.community import CommunityTools

    calls = []
    class Client:
        def get(self, path, *, params=None):
            calls.append((path, params))
            key = path.rsplit("/", 1)[-1]
            return success({key: [{"id": "first"}, {"id": "second"}, {"id": "third"}]})
    monkeypatch.setattr(module, "build_community_tools", lambda _: CommunityTools(Client()))
    server = module.create_server({"ZENDESK_CAPABILITIES": "community,badges"})
    for name, key, filters in (
        ("zendesk_list_badge_categories", "badge_categories", {"brand_id": 4}),
        ("zendesk_list_badges", "badges", {"brand_id": 4}),
        ("zendesk_list_badge_assignments", "badge_assignments", {"brand_id": 4, "user_id": 7, "badge_id": "badge-1", "badge_category_id": "category-1"}),
    ):
        request = types.CallToolRequest(params=types.CallToolRequestParams(name=name, arguments={**filters, "limit": 1, "cursor": "1"}))
        result = asyncio.run(server.request_handlers[types.CallToolRequest](request)).root
        assert result.structuredContent == {"ok": True, "items": [{"id": "second"}], "has_more": True, "next_cursor": "2", "truncated": True}
        assert calls[-1] == ("/api/v2/gather/" + key, {k: str(v) for k, v in filters.items()})
    assert len(calls) == 3


def test_badge_icon_update_preserves_omitted_null_and_replacement_through_mcp(tmp_path, monkeypatch):
    import zendesk_mcp_server.server as module
    from zendesk_mcp_server.approvals import ApprovalStore
    from zendesk_mcp_server.config import Settings
    from zendesk_mcp_server.contracts import success
    from zendesk_mcp_server.tools.community import CommunityTools

    calls = []
    class Client:
        def request(self, method, path, *, json_body=None):
            calls.append((method, path, json_body))
            return success({"badge": {"id": "badge-1"}})
    store = ApprovalStore(tmp_path / "approvals.json")
    settings = Settings.load({"ZENDESK_WRITE_MODE": "standard", "ZENDESK_ENABLE_PUBLIC_WRITES": "true"})
    community = CommunityTools(Client(), settings, store)
    monkeypatch.setattr(module, "build_community_tools", lambda _: community)
    server = module.create_server({"ZENDESK_CAPABILITIES": "community,badges"})
    handler = server.request_handlers[types.CallToolRequest]
    def call(arguments):
        request = types.CallToolRequest(params=types.CallToolRequestParams(name="zendesk_update_badge", arguments=arguments))
        return asyncio.run(handler(request)).root.structuredContent
    for update in ({"name": "Renamed"}, {"icon_upload_id": None}, {"icon_upload_id": "upload-1"}):
        arguments = {"badge_id": "badge-1", **update}
        before = len(calls)
        preview = call(arguments)
        assert preview["ok"] is True and len(calls) == before, preview
        request_id = preview["data"]["approval_request_id"]
        result = call({**arguments, "execution_mode": "apply", "approval_request_id": request_id, "approval_token": store.approve(request_id)})
        assert result["ok"] is True
        assert calls[-1] == ("PUT", "/api/v2/gather/badges/badge-1", {"badge": update})
        assert len(calls) == before + 1


def test_connection_status_tool_probes_the_authenticated_user(monkeypatch):
    import zendesk_mcp_server.server as module

    class Client:
        def __init__(self, *_): pass
        def get(self, path):
            assert path == "/api/v2/users/me.json"
            return {"ok": True, "data": {"user": {"id": 7, "role": "admin"}}}

    monkeypatch.setattr(module, "ZendeskClient", Client)
    server = module.create_server({"ZENDESK_SUBDOMAIN": "acme", "ZENDESK_EMAIL": "agent@example.test", "ZENDESK_API_TOKEN": "token"})
    request = types.CallToolRequest(params=types.CallToolRequestParams(name="zendesk_get_connection_status", arguments={}))

    result = asyncio.run(server.request_handlers[types.CallToolRequest](request))

    assert json.loads(result.root.content[0].text)["data"]["verified_user"] == {"id": 7, "role": "admin"}
    assert result.root.structuredContent["ok"] is True


def test_mcp_dispatch_forwards_ticket_pagination_inputs(monkeypatch):
    import zendesk_mcp_server.server as module
    from zendesk_mcp_server.tools.tickets import TicketTools

    calls = []
    class Client:
        def get(self, path, *, params=None):
            calls.append((path, params))
            if path == "/api/v2/search.json":
                return {"ok": True, "data": {"results": [], "next_page": None}}
            return {"ok": True, "data": {"tickets": [], "meta": {"has_more": False}}}
    monkeypatch.setattr(module, "build_ticket_tools", lambda _: TicketTools(Client()))
    server = module.create_server({"ZENDESK_SUBDOMAIN": "acme"})
    for name, arguments in (
        ("zendesk_list_tickets", {"limit": 2, "cursor": "next", "sort": "-updated_at"}),
        ("zendesk_search_tickets", {"query": "status:open", "limit": 2, "page": 3}),
    ):
        request = types.CallToolRequest(params=types.CallToolRequestParams(name=name, arguments=arguments))
        result = asyncio.run(server.request_handlers[types.CallToolRequest](request))
        assert result.root.structuredContent["ok"] is True
    assert calls == [
        ("/api/v2/tickets.json", {"page[size]": "2", "page[after]": "next", "sort": "-updated_at"}),
        ("/api/v2/search.json", {"query": "type:ticket status:open", "per_page": "2", "page": "3"}),
    ]


def test_audit_does_not_modify_symlink_or_hardlink_targets(tmp_path):
    import os
    import time
    from zendesk_mcp_server.audit import AuditLog
    target = tmp_path / "existing.txt"
    target.write_text("keep")
    target.chmod(0o644)
    for kind in ("symlink", "hardlink"):
        path = tmp_path / kind
        if kind == "symlink": path.symlink_to(target)
        else: os.link(target, path)
        AuditLog(path).record("probe", "read", {}, {"ok": True}, started_at=time.monotonic())
        assert target.read_text() == "keep"
        assert target.stat().st_mode & 0o777 == 0o644


def test_tool_call_survives_unsupported_audit_permissions(tmp_path, monkeypatch, caplog):
    import os
    from zendesk_mcp_server import server as server_module
    from zendesk_mcp_server.contracts import success
    monkeypatch.delattr(os, "fchmod", raising=False)
    monkeypatch.setattr(server_module, "build_connection_status", lambda *_args, **_kwargs: success({"configured": True}))
    path = tmp_path / "not-created" / "audit.jsonl"
    server = server_module.create_server({"ZENDESK_AUDIT_LOG": str(path)})
    request = types.CallToolRequest(params=types.CallToolRequestParams(name="zendesk_get_connection_status", arguments={}))
    result = asyncio.run(server.request_handlers[types.CallToolRequest](request))
    assert result.root.structuredContent == success({"configured": True})
    assert not path.parent.exists()
    assert "unsupported" in caplog.text and "audit" in caplog.text


def test_tool_call_writes_a_redacted_audit_event(tmp_path, monkeypatch):
    from zendesk_mcp_server import server as server_module
    from zendesk_mcp_server.contracts import success

    monkeypatch.setattr(server_module, "build_connection_status", lambda *_args, **_kwargs: success({"customer_body": "do not log"}))
    path = tmp_path / "audit.jsonl"
    server = server_module.create_server({
        "ZENDESK_AUDIT_LOG": str(path),
        "ZENDESK_SUBDOMAIN": "acme",
        "ZENDESK_EMAIL": "agent@example.test",
        "ZENDESK_API_TOKEN": "secret",
    })
    request = types.CallToolRequest(params=types.CallToolRequestParams(name="zendesk_get_connection_status", arguments={}))

    asyncio.run(server.request_handlers[types.CallToolRequest](request))

    event = json.loads(path.read_text())
    assert event["tool"] == "zendesk_get_connection_status"
    assert event["risk_class"] == "read"
    assert event["result_code"] == "ok"
    assert "data" not in event
    assert "secret" not in path.read_text()
    assert "do not log" not in path.read_text()


def test_disabled_capability_returns_before_building_its_tool_client(monkeypatch):
    import zendesk_mcp_server.server as module

    monkeypatch.setattr(module, "build_guide_tools", lambda _: (_ for _ in ()).throw(AssertionError("unexpected client build")))
    server = module.create_server({"ZENDESK_SUBDOMAIN": "acme", "ZENDESK_EMAIL": "agent@example.test", "ZENDESK_API_TOKEN": "token", "ZENDESK_CAPABILITIES": "support"})
    request = types.CallToolRequest(params=types.CallToolRequestParams(name="zendesk_list_help_center_categories", arguments={}))

    result = asyncio.run(server.request_handlers[types.CallToolRequest](request))

    assert json.loads(result.root.content[0].text)["error"]["code"] == "not_configured"


def test_enabled_conditional_ticket_tools_are_dispatched(monkeypatch):
    import zendesk_mcp_server.server as module
    from zendesk_mcp_server.contracts import success

    class TicketTools:
        def get_git_zen_links(self, ticket_id): return success({"tool": "git_zen", "ticket_id": ticket_id})
        def get_time_tracking(self, ticket_id, *, limit=100, cursor=None): return success({"tool": "time_tracking", "ticket_id": ticket_id})
        def log_time(self, ticket_id, time_spent, note): return success({"tool": "log_time", "ticket_id": ticket_id, "time_spent": time_spent, "note": note})

    monkeypatch.setattr(module, "build_ticket_tools", lambda _: TicketTools())
    server = module.create_server({"ZENDESK_CAPABILITIES": "support,git_zen,time_tracking"})
    handler = server.request_handlers[types.CallToolRequest]

    for name, arguments, expected in (
        ("zendesk_get_git_zen_links", {"ticket_id": 1}, "git_zen"),
        ("zendesk_get_time_tracking", {"ticket_id": 2}, "time_tracking"),
        ("zendesk_log_time", {"ticket_id": 3, "time_spent": "1h", "note": "work"}, "log_time"),
    ):
        request = types.CallToolRequest(params=types.CallToolRequestParams(name=name, arguments=arguments))
        result = asyncio.run(handler(request))
        assert result.root.structuredContent["data"]["tool"] == expected


def test_every_registered_tool_reaches_a_domain_dispatcher(monkeypatch):
    import zendesk_mcp_server.server as module
    from zendesk_mcp_server.contracts import success

    def value(schema):
        if "const" in schema: return schema["const"]
        if "enum" in schema: return schema["enum"][0]
        if "oneOf" in schema: return value(schema["oneOf"][0])
        if schema.get("type") == "integer": return max(1, schema.get("minimum", 1))
        if schema.get("type") == "boolean": return True
        if schema.get("type") == "array": return [value(schema["items"])] if schema.get("minItems") else []
        if schema.get("pattern") == "^[a-z]{2,3}(-[a-z0-9]+)*$": return "en-us"
        return "1h" if schema.get("pattern") else "value"

    def arguments(schema):
        properties = schema.get("properties", {})
        result = {name: value(properties[name]) for name in schema.get("required", [])}
        if schema.get("oneOf"):
            result.update({name: value(properties[name]) for name in schema["oneOf"][0].get("required", [])})
        for variant in schema.get("anyOf", []):
            result.update({name: value(properties[name]) for name in variant.get("required", []) if name not in result})
        return result

    class AnyTools:
        def __getattr__(self, name): return lambda *_args, **_kwargs: success({"handler": name})

    for builder in ("build_ticket_tools", "build_metadata_tools", "build_guide_tools", "build_community_tools"):
        monkeypatch.setattr(module, builder, lambda _: AnyTools())
    monkeypatch.setattr(module, "build_connection_status", lambda *_args, **_kwargs: success({"handler": "connection_status"}))
    server = module.create_server({"ZENDESK_CAPABILITIES": "support,operations,guide,community,csat,git_zen,time_tracking,badges"})
    handler = server.request_handlers[types.CallToolRequest]

    for tool in module.build_tools():
        request = types.CallToolRequest(params=types.CallToolRequestParams(name=tool.name, arguments=arguments(tool.inputSchema)))
        result = asyncio.run(handler(request))
        assert result.root.structuredContent is not None, tool.name
        assert result.root.structuredContent["ok"] is True, tool.name


def test_ticket_closure_mcp_dispatch_preserves_approval_options(tmp_path, monkeypatch):
    import asyncio
    from mcp import types
    import zendesk_mcp_server.server as module
    from zendesk_mcp_server.approvals import ApprovalStore
    from zendesk_mcp_server.config import Settings
    from zendesk_mcp_server.contracts import success
    from zendesk_mcp_server.tools.tickets import TicketTools
    class Client:
        def __init__(self): self.calls = []
        def request(self, method, path, *, json_body=None):
            self.calls.append((method, path, json_body))
            return success({"ticket": {"id": 9}})
    settings = Settings.load({"ZENDESK_WRITE_MODE": "standard", "ZENDESK_ENABLE_DESTRUCTIVE_WRITES": "true"})
    store = ApprovalStore(tmp_path / "approvals.json")
    client = Client()
    tools = TicketTools(client, settings, store)
    monkeypatch.setattr(module, "build_ticket_tools", lambda *_: tools)
    server = module.create_server({})
    handler = server.request_handlers[types.CallToolRequest]
    registered = {tool.name: tool for tool in module.build_tools()}
    def call(name, arguments):
        request = types.CallToolRequest(params=types.CallToolRequestParams(name=name, arguments=arguments))
        return asyncio.run(handler(request)).root.structuredContent
    for name in ("zendesk_update_ticket", "zendesk_set_ticket_status"):
        assert "execution_mode" in registered[name].inputSchema["properties"]
        before = len(client.calls)
        args = {"ticket_id": 9, "status": "closed"}
        if name == "zendesk_update_ticket": args["subject"] = "Approved subject"
        preview = call(name, args)
        assert len(client.calls) == before
        request_id = preview["data"]["approval_request_id"]
        token = store.approve(request_id)
        result = call(name, {**args, "execution_mode": "apply", "approval_request_id": request_id, "approval_token": token})
        assert result["ok"] is True and len(client.calls) == before + 1
        assert client.calls[-1][2] == {"ticket": {key: value for key, value in args.items() if key != "ticket_id"}}


def test_mcp_write_preserves_transport_operation_state_and_request_id(tmp_path, monkeypatch):
    import httpx
    from zendesk_mcp_server import server as module
    from zendesk_mcp_server.client import ZendeskClient
    calls = []
    def response(request):
        assert request.method == "PUT" and request.url.path == "/api/v2/tickets/9.json"
        assert json.loads(request.content) == {"ticket": {"subject": "Updated"}}
        calls.append(request)
        return httpx.Response(200, json={"ticket": {"id": 9, "subject": "Updated"}}, headers={"x-zendesk-request-id": "write-123"}, request=request)
    monkeypatch.setattr(module, "ZendeskClient", lambda settings, authorization: ZendeskClient(settings, authorization, transport=httpx.MockTransport(response)))
    server = module.create_server({"ZENDESK_SUBDOMAIN": "example", "ZENDESK_EMAIL": "test@example.test", "ZENDESK_API_TOKEN": "test-only", "ZENDESK_WRITE_MODE": "standard", "ZENDESK_AUDIT_LOG": str(tmp_path / "audit.jsonl")})
    request = types.CallToolRequest(params=types.CallToolRequestParams(name="zendesk_update_ticket", arguments={"ticket_id": 9, "subject": "Updated"}))
    result = asyncio.run(server.request_handlers[types.CallToolRequest](request)).root.structuredContent
    assert result["ok"] is True and result["operation_state"] == "applied"
    assert result["request_id"] == "write-123" and result["data"]["ticket"]["id"] == 9
    assert len(calls) == 1


def test_canonical_names_preserve_dispatch_capabilities_and_approval_identity(tmp_path, monkeypatch):
    from zendesk_mcp_server import server as module
    from zendesk_mcp_server.approvals import ApprovalStore
    from zendesk_mcp_server.contracts import success

    responses = {
        "/api/v2/guide/permission_groups.json": {"permission_groups": [{"id": 1}], "next_page": None},
        "/api/v2/help_center/user_segments.json": {"user_segments": [{"id": 2}], "meta": {"has_more": False}},
        "/api/v2/tickets/9.json": {"ticket": {"id": 9, "status": "open", "updated_at": "2026-09-11T00:00:00Z"}},
        "/api/v2/tickets/9/macros/4/apply.json": {"result": {"ticket": {"status": "pending"}}},
        "/api/v2/macros/4.json": {"macro": {"actions": [{"field": "status", "value": "pending"}]}},
    }
    class Client:
        def __init__(self, *args, **kwargs): pass
        def get(self, path, *, params=None): return success(responses[path])
        def request(self, *args, **kwargs): raise AssertionError("read-only must not write")
        def upload_presigned(self, *args, **kwargs): raise AssertionError("read-only must not upload")
    monkeypatch.setattr(module, "ZendeskClient", Client)
    (tmp_path / "image.png").write_bytes(b"test-image")
    cases = (
        ("zendesk_list_permission_groups", "guide", {}, 1),
        ("zendesk_list_user_segments", "guide", {}, 2),
        ("zendesk_apply_macro", "operations", {"ticket_id": 9, "macro_id": 4}, None),
        ("zendesk_assign_badge", "badges", {"badge_id": "badge-1", "user_id": 7}, None),
        ("zendesk_unassign_badge", "badges", {"assignment_id": "assignment-1"}, None),
        ("zendesk_upload_community_image", "community", {"image_path": "image.png", "content_type": "image/png", "brand_id": 1}, None),
    )
    for name, capability, arguments, item_id in cases:
        env = {"ZENDESK_SUBDOMAIN": "example", "ZENDESK_EMAIL": "test@example.test", "ZENDESK_API_TOKEN": "test-only", "ZENDESK_CAPABILITIES": capability, "ZENDESK_APPROVAL_STORE": str(tmp_path / "approvals.json"), "ZENDESK_UPLOAD_ROOT": str(tmp_path)}
        server = module.create_server(env)
        registered = asyncio.run(server.request_handlers[types.ListToolsRequest](types.ListToolsRequest())).root.tools
        tool = next(tool for tool in registered if tool.name == name)
        assert tool.annotations.readOnlyHint is (item_id is not None)
        assert tool.annotations.destructiveHint is (name == "zendesk_unassign_badge")
        def call(values):
            request = types.CallToolRequest(params=types.CallToolRequestParams(name=name, arguments=values))
            return asyncio.run(server.request_handlers[types.CallToolRequest](request)).root.structuredContent
        result = call(arguments)
        assert result["ok"] is True
        if item_id is not None:
            assert result["items"] == [{"id": item_id}]
        else:
            store = ApprovalStore.from_environment(env)
            request_id = result["data"]["approval_request_id"]
            assert store.preview(request_id)["tool"] == name
            token = store.approve(request_id)
            applied = call({**arguments, "execution_mode": "apply", "approval_request_id": request_id, "approval_token": token})
            assert applied["error"]["code"] == "write_disabled"


def test_support_read_tools_are_registered():
    from zendesk_mcp_server.server import build_tools

    assert [tool.name for tool in build_tools()] == [
        "zendesk_get_connection_status",
        "zendesk_list_help_center_locales",
        "zendesk_list_tickets",
        "zendesk_search_tickets",
        "zendesk_count_tickets",
        "zendesk_export_tickets",
        "zendesk_preview_macro",
        "zendesk_apply_macro",
        "zendesk_get_ticket",
        "zendesk_create_ticket",
        "zendesk_update_ticket",
        "zendesk_set_ticket_status",
        "zendesk_assign_ticket",
        "zendesk_add_ticket_tag",
        "zendesk_remove_ticket_tag",
        "zendesk_get_ticket_conversation",
        "zendesk_list_ticket_attachments",
        "zendesk_download_ticket_attachment",
        "zendesk_inspect_ticket_attachment",
        "zendesk_ticket_to_issue_context",
        "zendesk_get_git_zen_links",
        "zendesk_get_time_tracking",
        "zendesk_log_time",
        "zendesk_post_public_reply",
        "zendesk_post_internal_note",
        "zendesk_search_users",
        "zendesk_list_groups",
        "zendesk_list_group_users",
        "zendesk_get_organization",
        "zendesk_list_brands",
        "zendesk_list_ticket_fields",
        "zendesk_list_ticket_forms",
        "zendesk_list_custom_statuses",
        "zendesk_list_views",
        "zendesk_get_view",
        "zendesk_list_view_tickets",
        "zendesk_list_macros",
        "zendesk_list_triggers",
        "zendesk_list_help_center_categories",
        "zendesk_list_help_center_sections",
        "zendesk_search_help_center_articles",
        "zendesk_export_help_center_articles",
        "zendesk_get_help_center_article",
        "zendesk_get_satisfaction_ratings",
        "zendesk_list_csat",
        "zendesk_export_satisfaction_ratings",
        "zendesk_list_permission_groups",
        "zendesk_list_user_segments",
        "zendesk_create_help_center_article",
        "zendesk_upsert_article_translation",
        "zendesk_replace_article_translation_body",
        "zendesk_publish_help_center_article",
        "zendesk_list_community_posts",
        "zendesk_search_community_posts",
        "zendesk_get_community_post",
        "zendesk_create_community_post",
        "zendesk_update_community_post",
        "zendesk_delete_community_post",
        "zendesk_create_community_comment",
        "zendesk_update_community_comment",
        "zendesk_delete_community_comment",
        "zendesk_create_community_topic",
        "zendesk_update_community_topic",
        "zendesk_delete_community_topic",
        "zendesk_list_community_votes",
        "zendesk_get_community_vote",
        "zendesk_upvote_community_content",
        "zendesk_downvote_community_content",
        "zendesk_remove_community_vote",
        "zendesk_list_content_subscriptions",
        "zendesk_get_content_subscription",
        "zendesk_create_content_subscription",
        "zendesk_update_content_subscription",
        "zendesk_delete_content_subscription",
        "zendesk_list_community_comments",
        "zendesk_get_community_comment",
        "zendesk_list_community_topics",
        "zendesk_get_community_topic",
        "zendesk_search_content_tags",
        "zendesk_count_content_tags",
        "zendesk_get_content_tag",
        "zendesk_create_content_tag",
        "zendesk_update_content_tag",
        "zendesk_delete_content_tag",
        "zendesk_list_user_subscriptions",
        "zendesk_upsert_user_subscription",
        "zendesk_delete_user_subscription",
        "zendesk_list_badge_categories",
        "zendesk_get_badge_category",
        "zendesk_create_badge_category",
        "zendesk_delete_badge_category",
        "zendesk_list_badges",
        "zendesk_get_badge",
        "zendesk_create_badge",
        "zendesk_update_badge",
        "zendesk_delete_badge",
        "zendesk_list_badge_assignments",
        "zendesk_assign_badge",
        "zendesk_unassign_badge",
        "zendesk_upload_community_image",
        "zendesk_upload_badge_icon",
    ]


def test_community_vote_tool_accepts_post_or_user_cursor_pagination():
    from zendesk_mcp_server.server import build_tools

    tool = next(item for item in build_tools() if item.name == "zendesk_list_community_votes")

    assert tool.inputSchema == {
        "type": "object",
        "properties": {
            "post_id": {"type": "integer", "minimum": 1},
            "user_id": {"oneOf": [{"type": "integer", "minimum": 1}, {"type": "string", "enum": ["me"]}]},
            "cursor": {"type": "string", "minLength": 1},
            "limit": {"type": "integer", "minimum": 1, "maximum": 1000, "default": 100},
        },
        "anyOf": [{"required": ["post_id"]}, {"required": ["user_id"]}],
    }


def test_community_post_comment_and_topic_lists_expose_cursor_controls():
    from zendesk_mcp_server.server import build_tools

    tools = {tool.name: tool for tool in build_tools()}

    for name in ("zendesk_list_community_posts", "zendesk_list_community_comments", "zendesk_list_community_topics"):
        properties = tools[name].inputSchema["properties"]
        assert properties["cursor"] == {"type": "string", "minLength": 1}
        assert properties["limit"] == {"type": "integer", "minimum": 1, "maximum": 1000, "default": 100}


def test_user_subscription_list_exposes_cursor_controls():
    from zendesk_mcp_server.server import build_tools

    tool = next(item for item in build_tools() if item.name == "zendesk_list_user_subscriptions")

    assert tool.inputSchema["properties"]["cursor"] == {"type": "string", "minLength": 1}
    assert tool.inputSchema["properties"]["limit"] == {"type": "integer", "minimum": 1, "maximum": 1000, "default": 100}


def test_content_tag_search_exposes_cursor_controls():
    from zendesk_mcp_server.server import build_tools

    tool = next(item for item in build_tools() if item.name == "zendesk_search_content_tags")

    assert tool.inputSchema["properties"]["cursor"] == {"type": "string", "minLength": 1}
    assert tool.inputSchema["properties"]["limit"] == {"type": "integer", "minimum": 1, "maximum": 1000, "default": 100}


def test_community_comment_read_accepts_post_and_locale_scope():
    from zendesk_mcp_server.server import build_tools

    tools = {tool.name: tool for tool in build_tools()}

    assert tools["zendesk_get_community_comment"].inputSchema["properties"]["post_id"]["minimum"] == 1
    assert tools["zendesk_get_community_comment"].inputSchema["properties"]["locale"]["minLength"] == 2
