import asyncio
import importlib

from mcp import types


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


def test_ticket_export_result_includes_a_resource_link_without_its_cache_path():
    from zendesk_mcp_server.server import ticket_export_content

    content = ticket_export_content({"ok": True, "data": {"format": "csv", "cache_path": "/private/cache/exports/tickets.csv", "item_count": 2, "truncated": False}})

    assert isinstance(content[0], types.TextContent)
    assert "cache_path" not in str(content[0].text)
    assert isinstance(content[1], types.ResourceLink)
    assert str(content[1].uri) == "file:///private/cache/exports/tickets.csv"
    assert content[1].mimeType == "text/csv"


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
        def export_articles(self, locale): return success({"articles": [{"id": 1, "locale": locale}], "truncated": False})

    monkeypatch.setattr(server_module, "build_guide_tools", lambda _: GuideExport())
    server = server_module.create_server({"ZENDESK_ENABLE_KNOWLEDGE_BASE_RESOURCE": "true"})
    request = types.ReadResourceRequest(params=types.ReadResourceRequestParams(uri="zendesk://knowledge-base"))

    result = asyncio.run(server.request_handlers[types.ReadResourceRequest](request))

    assert result.root.contents[0].text == '{"ok": true, "data": {"locales": [{"locale": "en-us", "articles": [{"id": 1, "locale": "en-us"}]}]}}'


def test_knowledge_base_resource_caches_its_export_for_one_hour(monkeypatch):
    from zendesk_mcp_server import server as server_module
    from zendesk_mcp_server.contracts import success

    class GuideExport:
        calls = 0
        def list_locales(self): self.calls += 1; return success({"locales": ["en-us"]})
        def export_articles(self, locale): self.calls += 1; return success({"articles": [], "truncated": False})

    guide = GuideExport()
    monkeypatch.setattr(server_module, "build_guide_tools", lambda _: guide)
    server = server_module.create_server({"ZENDESK_ENABLE_KNOWLEDGE_BASE_RESOURCE": "true"})
    request = types.ReadResourceRequest(params=types.ReadResourceRequestParams(uri="zendesk://knowledge-base"))

    asyncio.run(server.request_handlers[types.ReadResourceRequest](request))
    asyncio.run(server.request_handlers[types.ReadResourceRequest](request))

    assert guide.calls == 2


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
        "zendesk_apply_ticket_macro",
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
        "zendesk_list_guide_permission_groups",
        "zendesk_list_guide_user_segments",
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
        "zendesk_create_badge_assignment",
        "zendesk_delete_badge_assignment",
        "zendesk_upload_community_user_image",
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
            "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 100},
        },
        "anyOf": [{"required": ["post_id"]}, {"required": ["user_id"]}],
    }


def test_community_post_comment_and_topic_lists_expose_cursor_controls():
    from zendesk_mcp_server.server import build_tools

    tools = {tool.name: tool for tool in build_tools()}

    for name in ("zendesk_list_community_posts", "zendesk_list_community_comments", "zendesk_list_community_topics"):
        properties = tools[name].inputSchema["properties"]
        assert properties["cursor"] == {"type": "string", "minLength": 1}
        assert properties["limit"] == {"type": "integer", "minimum": 1, "maximum": 100, "default": 100}


def test_user_subscription_list_exposes_cursor_controls():
    from zendesk_mcp_server.server import build_tools

    tool = next(item for item in build_tools() if item.name == "zendesk_list_user_subscriptions")

    assert tool.inputSchema["properties"]["cursor"] == {"type": "string", "minLength": 1}
    assert tool.inputSchema["properties"]["limit"] == {"type": "integer", "minimum": 1, "maximum": 100, "default": 100}


def test_content_tag_search_exposes_cursor_controls():
    from zendesk_mcp_server.server import build_tools

    tool = next(item for item in build_tools() if item.name == "zendesk_search_content_tags")

    assert tool.inputSchema["properties"]["cursor"] == {"type": "string", "minLength": 1}
    assert tool.inputSchema["properties"]["limit"] == {"type": "integer", "minimum": 1, "maximum": 100, "default": 100}
