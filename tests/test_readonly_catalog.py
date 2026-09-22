"""Run against source or an installed wheel; never contact a Zendesk account."""

import asyncio

import httpx
from mcp import types

from zendesk_mcp_server.server import build_tools, create_server


def test_all_write_tools_block_apply_with_explicit_readonly_settings(monkeypatch, tmp_path):
    image = tmp_path / "image.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\n")
    attempts = []
    def send(_client, request, **kwargs):
        attempts.append(request.method)
        return httpx.Response(200, request=request, json={
            "result": {"ticket": {"comment": {"body": "preview", "public": False}}, "actions": []},
            "locales": ["en-us"], "default_locale": "en-us",
            "translation": {"id": 1, "draft": True, "body": "original"},
            "ticket": {"id": 1, "updated_at": "2026-09-01T00:00:00Z"},
            "macro": {"id": 1, "actions": []},
            "user_subscriptions": [], "meta": {"has_more": False},
        })
    monkeypatch.setattr(httpx.Client, "send", send)
    environment = {
        "ZENDESK_SUBDOMAIN": "readonly-smoke", "ZENDESK_EMAIL": "smoke@example.test", "ZENDESK_API_TOKEN": "not-a-real-token",
        "ZENDESK_WRITE_MODE": "read_only",
        "ZENDESK_CAPABILITIES": "support,operations,guide,community,csat,git_zen,time_tracking,badges",
        "ZENDESK_ATTACHMENT_CACHE_ROOT": str(tmp_path / "attachments"),
        "ZENDESK_APPROVAL_STORE": str(tmp_path / "approvals.json"),
        "ZENDESK_UPLOAD_ROOT": str(tmp_path),
    }
    server = create_server(environment)
    overrides = {
        "zendesk_update_ticket": {"subject": "smoke"},
        "zendesk_assign_ticket": {"assignee_id": 1},
        "zendesk_upsert_article_translation": {"title": "smoke"},
        "zendesk_update_community_post": {"title": "smoke"},
        "zendesk_update_community_comment": {"body": "smoke"},
        "zendesk_update_community_topic": {"name": "smoke"},
        "zendesk_update_badge": {"name": "smoke"},
        "zendesk_upload_community_image": {"image_path": str(image), "content_type": "image/png"},
        "zendesk_upload_badge_icon": {"image_path": str(image), "content_type": "image/png"},
    }
    def sample(schema):
        if "oneOf" in schema: return sample(schema["oneOf"][0])
        if "enum" in schema: return schema["enum"][0]
        if schema.get("type") == "integer": return 1
        if schema.get("type") == "boolean": return False
        return "smoke"
    checked = []; failures = []
    for tool in build_tools():
        if tool.annotations.readOnlyHint: continue
        properties = tool.inputSchema["properties"]
        arguments = {key: sample(properties[key]) for key in tool.inputSchema.get("required", [])}
        arguments.update(overrides.get(tool.name, {}))
        if "locale" in arguments: arguments["locale"] = "en-us"
        if "time_spent" in arguments: arguments["time_spent"] = "1h"
        if "execution_mode" in properties and tool.name not in ("zendesk_update_ticket", "zendesk_set_ticket_status"):
            arguments["execution_mode"] = "apply"
        request = types.CallToolRequest(params=types.CallToolRequestParams(name=tool.name, arguments=arguments))
        result = asyncio.run(server.request_handlers[types.CallToolRequest](request))
        content = result.root.structuredContent
        if not content or content.get("error", {}).get("code") != "write_disabled": failures.append((tool.name, content, str(result.root.content)))
        checked.append(tool.name)
    assert not failures
    assert len(checked) == 43
    assert not [method for method in attempts if method not in ("GET", "HEAD")], attempts
