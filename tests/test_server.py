import importlib


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
