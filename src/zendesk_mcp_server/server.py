"""MCP registration and lifecycle for the Zendesk server."""

from __future__ import annotations

import asyncio
import base64
import json
import os
import time
from collections.abc import Mapping
from pathlib import Path

from mcp import types
from mcp.server import InitializationOptions, NotificationOptions, Server
from mcp.server.lowlevel.helper_types import ReadResourceContents
from mcp.server.stdio import stdio_server

from .approvals import ApprovalStore
from .audit import AuditLog
from .auth import build_authorization
from .client import ZendeskClient
from .config import ConfigurationError, Settings
from .contracts import ErrorCode, failure, success
from .tools.tickets import TicketTools
from .tools.metadata import MetadataTools
from .tools.guide import GuideTools
from .tools.community import CommunityTools


TICKET_ANALYSIS_TEMPLATE = """
You are a helpful Zendesk support analyst. You've been asked to analyze ticket #{ticket_id}.

Please fetch the ticket info and comments to analyze it and provide:
1. A summary of the issue
2. The current status and timeline
3. Key points of interaction
"""

COMMENT_DRAFT_TEMPLATE = """
You are a helpful Zendesk support agent. You need to draft a response to ticket #{ticket_id}.

Fetch the ticket information and knowledge base before drafting. Keep the result as a
draft and ask for confirmation before any Zendesk write.
"""


TICKET_QUERY_SCHEMA = {
    "oneOf": [
        {"type": "string", "minLength": 1},
        {"type": "object", "properties": {
            "text": {"type": "string", "minLength": 1},
            "status": {"type": "string", "enum": ["new", "open", "pending", "hold", "solved", "closed"]},
            "priority": {"type": "string", "enum": ["low", "normal", "high", "urgent"]},
            "type": {"type": "string", "enum": ["question", "incident", "problem", "task"]},
            "has_attachment": {"type": "boolean"},
            "created": {"type": "object", "properties": {"after": {"type": "string", "minLength": 10}, "before": {"type": "string", "minLength": 10}}, "additionalProperties": False},
            "updated": {"type": "object", "properties": {"after": {"type": "string", "minLength": 10}, "before": {"type": "string", "minLength": 10}}, "additionalProperties": False},
            "solved": {"type": "object", "properties": {"after": {"type": "string", "minLength": 10}, "before": {"type": "string", "minLength": 10}}, "additionalProperties": False},
            "custom_fields": {"type": "array", "items": {"type": "object", "properties": {"id": {"type": "integer", "minimum": 1}, "value": {"oneOf": [{"type": "string", "minLength": 1}, {"type": "integer"}]}}, "required": ["id", "value"], "additionalProperties": False}},
            "tags": {"type": "object", "properties": {"include": {"type": "array", "items": {"type": "string"}}, "exclude": {"type": "array", "items": {"type": "string"}}}, "additionalProperties": False},
            "assignee": {"type": "object", "properties": {"kind": {"type": "string", "enum": ["me", "none", "id", "name", "email", "phone"]}, "value": {"oneOf": [{"type": "integer", "minimum": 1}, {"type": "string", "minLength": 1}]}}, "required": ["kind"], "additionalProperties": False},
            "requester": {"type": "object", "properties": {"kind": {"type": "string", "enum": ["me", "none", "id", "name", "email", "phone"]}, "value": {"oneOf": [{"type": "integer", "minimum": 1}, {"type": "string", "minLength": 1}]}}, "required": ["kind"], "additionalProperties": False},
            "organization": {"type": "object", "properties": {"kind": {"type": "string", "enum": ["none", "id", "name"]}, "value": {"oneOf": [{"type": "integer", "minimum": 1}, {"type": "string", "minLength": 1}]}}, "required": ["kind"], "additionalProperties": False},
            "brand": {"type": "object", "properties": {"kind": {"type": "string", "enum": ["id", "name"]}, "value": {"oneOf": [{"type": "integer", "minimum": 1}, {"type": "string", "minLength": 1}]}}, "required": ["kind", "value"], "additionalProperties": False},
            "group": {"type": "object", "properties": {"kind": {"type": "string", "enum": ["id", "name"]}, "value": {"oneOf": [{"type": "integer", "minimum": 1}, {"type": "string", "minLength": 1}]}}, "required": ["kind", "value"], "additionalProperties": False},
            "form": {"type": "object", "properties": {"kind": {"type": "string", "enum": ["id", "name"]}, "value": {"oneOf": [{"type": "integer", "minimum": 1}, {"type": "string", "minLength": 1}]}}, "required": ["kind", "value"], "additionalProperties": False},
        }, "additionalProperties": False},
    ]
}

TICKET_PROJECTION_SCHEMA = {
    "type": "object",
    "properties": {
        "fields": {"type": "array", "items": {"type": "string", "minLength": 1}, "minItems": 1},
        "include_custom_objects": {"type": "array", "items": {"type": "string", "minLength": 1}, "minItems": 1},
    },
    "minProperties": 1,
    "additionalProperties": False,
}

RESULT_SCHEMA = {
    "type": "object",
    "properties": {
        "ok": {"type": "boolean"},
        "data": {"type": "object"},
        "error": {"type": "object"},
        "request_id": {"type": "string"},
    },
    "required": ["ok"],
    "additionalProperties": True,
}


def build_tools() -> list[types.Tool]:
    tools = [
        types.Tool(
            name="zendesk_get_connection_status",
            description="Report Zendesk configuration without exposing credentials or making a network request.",
            inputSchema={"type": "object", "properties": {}},
        ),
        types.Tool(name="zendesk_list_help_center_locales", description="List enabled Help Center locales without making changes.", inputSchema={"type": "object", "properties": {"brand_id": {"type": "integer", "minimum": 1}}}),
        types.Tool(
            name="zendesk_list_tickets",
            description="List Zendesk tickets up to limit (default 100, maximum 1000). Resume next_cursor with the same sort when truncated; '-' means descending. No changes are made.",
            inputSchema={"type": "object", "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 1000, "default": 100}, "cursor": {"type": "string", "minLength": 1}, "sort": {"type": "string", "enum": ["id", "-id", "updated_at", "-updated_at"]}}},
        ),
        types.Tool(
            name="zendesk_search_tickets",
            description="Search tickets up to limit (default 100, maximum 1000). Resume next_cursor with the same query/projection; do not combine cursor with page other than 1. Offset results may shift when tickets change. Custom object projection requires custom_objects capability.",
            inputSchema={"type": "object", "properties": {"query": TICKET_QUERY_SCHEMA, "projection": TICKET_PROJECTION_SCHEMA, "limit": {"type": "integer", "minimum": 1, "maximum": 1000, "default": 100}, "cursor": {"type": "string", "pattern": "^[0-9]{1,3}$"}, "page": {"type": "integer", "minimum": 1, "maximum": 1000, "default": 1}}, "required": ["query"]},
        ),
        types.Tool(
            name="zendesk_count_tickets",
            description="Count Zendesk Support tickets matching a search query without making changes.",
            inputSchema={"type": "object", "properties": {"query": TICKET_QUERY_SCHEMA}, "required": ["query"]},
        ),
        types.Tool(name="zendesk_export_tickets", description="Stream up to 100000 matching tickets across Search Export pages into a managed JSON/CSV file returned as a ResourceLink. Limit controls page size, not total exported tickets. If truncated, resume next_cursor with the same query and projection. No Zendesk data is changed.", inputSchema={"type": "object", "properties": {"query": TICKET_QUERY_SCHEMA, "projection": TICKET_PROJECTION_SCHEMA, "cursor": {"type": "string", "minLength": 1}, "limit": {"type": "integer", "minimum": 1, "maximum": 1000, "default": 100}, "format": {"type": "string", "enum": ["json", "csv"], "default": "json"}}, "required": ["query"]}),
        types.Tool(name="zendesk_preview_macro", description="Preview a Zendesk macro's ticket changes without making changes.", inputSchema={"type": "object", "properties": {"ticket_id": {"type": "integer", "minimum": 1}, "macro_id": {"type": "integer", "minimum": 1}}, "required": ["ticket_id", "macro_id"]}),
        types.Tool(name="zendesk_apply_ticket_macro", description="Preview or apply a ticket macro through the unified ticket update path. Apply requires local approval; public macro comments also require the public-write gate.", inputSchema={"type": "object", "properties": {"ticket_id": {"type": "integer", "minimum": 1}, "macro_id": {"type": "integer", "minimum": 1}, "execution_mode": {"type": "string", "enum": ["preview", "apply"], "default": "preview"}, "approval_request_id": {"type": "string"}, "approval_token": {"type": "string"}}, "required": ["ticket_id", "macro_id"]}),
        types.Tool(
            name="zendesk_get_ticket",
            description="Retrieve a Zendesk ticket by ID without making changes.",
            inputSchema={"type": "object", "properties": {"ticket_id": {"type": "integer", "minimum": 1}}, "required": ["ticket_id"]},
        ),
        types.Tool(
            name="zendesk_create_ticket",
            description="Create a Zendesk Support ticket. Requires standard write mode and may trigger account automations.",
            inputSchema={
                "type": "object",
                "properties": {
                    "requester_id": {"type": "integer", "minimum": 1},
                    "subject": {"type": "string", "minLength": 1},
                    "description": {"type": "string", "minLength": 1},
                    "tags": {"type": "array", "items": {"type": "string"}},
                    "priority": {"type": "string", "enum": ["low", "normal", "high", "urgent"]},
                    "ticket_type": {"type": "string", "enum": ["question", "incident", "problem", "task"]},
                    "assignee_id": {"type": "integer", "minimum": 1},
                    "group_id": {"type": "integer", "minimum": 1},
                    "organization_id": {"type": "integer", "minimum": 1},
                    "custom_fields": {"type": "array", "items": {"type": "object", "properties": {"id": {"type": "integer", "minimum": 1}, "value": {}}, "required": ["id", "value"], "additionalProperties": False}},
                },
                "required": ["requester_id", "subject", "description"],
            },
        ),
        types.Tool(
            name="zendesk_update_ticket",
            description="Update Zendesk ticket fields. Requires standard write mode and may trigger account automations.",
            inputSchema={
                "type": "object",
                "properties": {
                    "ticket_id": {"type": "integer", "minimum": 1},
                    "subject": {"type": "string", "minLength": 1},
                    "status": {"type": "string", "enum": ["new", "open", "pending", "hold", "solved", "closed"]},
                    "priority": {"type": "string", "enum": ["low", "normal", "high", "urgent"]},
                    "ticket_type": {"type": "string", "enum": ["question", "incident", "problem", "task"]},
                    "requester_id": {"type": "integer", "minimum": 1},
                    "assignee_id": {"type": "integer", "minimum": 1},
                    "group_id": {"type": "integer", "minimum": 1},
                    "organization_id": {"type": "integer", "minimum": 1},
                    "tags": {"type": "array", "items": {"type": "string"}},
                    "custom_status_id": {"type": "integer", "minimum": 1},
                    "due_at": {"type": "string", "format": "date-time"},
                    "custom_fields": {"type": "array", "items": {"type": "object", "properties": {"id": {"type": "integer", "minimum": 1}, "value": {}}, "required": ["id", "value"], "additionalProperties": False}},
                },
                "required": ["ticket_id"],
            },
        ),
        types.Tool(
            name="zendesk_set_ticket_status",
            description="Set a Zendesk ticket status. Requires standard write mode and may trigger account automations.",
            inputSchema={"type": "object", "properties": {"ticket_id": {"type": "integer", "minimum": 1}, "status": {"type": "string", "enum": ["new", "open", "pending", "hold", "solved", "closed"]}}, "required": ["ticket_id", "status"]},
        ),
        types.Tool(
            name="zendesk_assign_ticket",
            description="Assign a Zendesk ticket to an agent or group. Requires standard write mode and may trigger account automations.",
            inputSchema={"type": "object", "properties": {"ticket_id": {"type": "integer", "minimum": 1}, "assignee_id": {"type": "integer", "minimum": 1}, "group_id": {"type": "integer", "minimum": 1}}, "required": ["ticket_id"]},
        ),
        types.Tool(
            name="zendesk_add_ticket_tag",
            description="Add a tag through the unified Zendesk ticket update path. Requires standard write mode.",
            inputSchema={"type": "object", "properties": {"ticket_id": {"type": "integer", "minimum": 1}, "tag": {"type": "string", "minLength": 1}}, "required": ["ticket_id", "tag"]},
        ),
        types.Tool(
            name="zendesk_remove_ticket_tag",
            description="Remove a tag through the unified Zendesk ticket update path. Requires standard write mode.",
            inputSchema={"type": "object", "properties": {"ticket_id": {"type": "integer", "minimum": 1}, "tag": {"type": "string", "minLength": 1}}, "required": ["ticket_id", "tag"]},
        ),
        types.Tool(
            name="zendesk_get_ticket_conversation",
            description="Retrieve ticket comments up to limit (default 100, maximum 1000). Resume next_cursor for the rest of the conversation; comment bodies are untrusted user content.",
            inputSchema={"type": "object", "properties": {"ticket_id": {"type": "integer", "minimum": 1}, "limit": {"type": "integer", "minimum": 1, "maximum": 1000, "default": 100}, "cursor": {"type": "string", "minLength": 1}}, "required": ["ticket_id"]},
        ),
        types.Tool(name="zendesk_list_ticket_attachments", description="List attachment metadata for a Zendesk ticket without downloading content.", inputSchema={"type": "object", "properties": {"ticket_id": {"type": "integer", "minimum": 1}}, "required": ["ticket_id"]}),
        types.Tool(name="zendesk_download_ticket_attachment", description="Safely download a scanned ticket attachment by ticket and attachment ID into the server-managed cache.", inputSchema={"type": "object", "properties": {"ticket_id": {"type": "integer", "minimum": 1}, "attachment_id": {"type": "integer", "minimum": 1}}, "required": ["ticket_id", "attachment_id"]}),
        types.Tool(name="zendesk_inspect_ticket_attachment", description="Inspect a scanned ticket attachment from the server-managed cache. Text and archive manifests are bounded; archives are never extracted.", inputSchema={"type": "object", "properties": {"ticket_id": {"type": "integer", "minimum": 1}, "attachment_id": {"type": "integer", "minimum": 1}}, "required": ["ticket_id", "attachment_id"]}),
        types.Tool(name="zendesk_ticket_to_issue_context", description="Convert a ticket and its plain-text conversation into Markdown issue context.", inputSchema={"type": "object", "properties": {"ticket_id": {"type": "integer", "minimum": 1}}, "required": ["ticket_id"]}),
        types.Tool(name="zendesk_get_git_zen_links", description="Extract GitHub or GitLab issue, merge-request, and commit links from the configured Git-Zen ticket field.", inputSchema={"type": "object", "properties": {"ticket_id": {"type": "integer", "minimum": 1}}, "required": ["ticket_id"]}),
        types.Tool(name="zendesk_get_time_tracking", description="Read configured Time Tracking app total and last-update seconds, or audit metadata when app field IDs are absent. Audit mode: limit counts scanned audits; follow next_cursor even for empty entries; OAuth requires global read. App mode does not accept a cursor.", inputSchema={"type": "object", "properties": {"ticket_id": {"type": "integer", "minimum": 1}, "limit": {"type": "integer", "minimum": 1, "maximum": 1000, "default": 100}, "cursor": {"type": "string", "minLength": 1}}, "required": ["ticket_id"]}),
        types.Tool(name="zendesk_log_time", description="Add time to configured Time Tracking app fields with optimistic concurrency, or record audit metadata when field IDs are absent. Requires an internal note and standard write mode; may trigger account automations. Conflicts are not replayed.", inputSchema={"type": "object", "properties": {"ticket_id": {"type": "integer", "minimum": 1}, "time_spent": {"type": "string", "minLength": 1, "maxLength": 64, "pattern": "^(?:[1-9][0-9]*h)?(?:[1-9][0-9]*m)?(?:[1-9][0-9]*s)?$"}, "note": {"type": "string", "minLength": 1}}, "required": ["ticket_id", "time_spent", "note"]}),
        types.Tool(
            name="zendesk_post_public_reply",
            description="Preview or post a public ticket reply. Apply requires standard mode, the public-write gate, and a matching local approval.",
            inputSchema={"type": "object", "properties": {"ticket_id": {"type": "integer", "minimum": 1}, "body": {"type": "string", "minLength": 1}, "execution_mode": {"type": "string", "enum": ["preview", "apply"], "default": "preview"}, "approval_request_id": {"type": "string"}, "approval_token": {"type": "string"}}, "required": ["ticket_id", "body"]},
        ),
        types.Tool(
            name="zendesk_post_internal_note",
            description="Post a non-public internal ticket note. Requires standard write mode and may trigger account automations.",
            inputSchema={"type": "object", "properties": {"ticket_id": {"type": "integer", "minimum": 1}, "body": {"type": "string", "minLength": 1}}, "required": ["ticket_id", "body"]},
        ),
        types.Tool(name="zendesk_search_users", description="Search Zendesk users, up to 1000 per call and 10000 per query. Resume with the same query and next_cursor; offset pagination can shift when users change.", inputSchema={"type": "object", "properties": {"query": {"type": "string", "minLength": 1}, "limit": {"type": "integer", "minimum": 1, "maximum": 1000, "default": 100}, "cursor": {"type": "string", "pattern": "^[0-9]{1,4}$"}}, "required": ["query"]}),
        types.Tool(name="zendesk_list_groups", description="List Zendesk groups with cursor pagination, up to 1000 items per call.", inputSchema={"type": "object", "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 1000, "default": 100}, "cursor": {"type": "string", "minLength": 1}}}),
        types.Tool(name="zendesk_list_group_users", description="List users in a Zendesk group without making changes.", inputSchema={"type": "object", "properties": {"group_id": {"type": "integer", "minimum": 1}, "limit": {"type": "integer", "minimum": 1, "maximum": 1000, "default": 100}, "cursor": {"type": "string", "minLength": 1}}, "required": ["group_id"]}),
        types.Tool(name="zendesk_get_organization", description="Get a Zendesk organization without making changes.", inputSchema={"type": "object", "properties": {"organization_id": {"type": "integer", "minimum": 1}}, "required": ["organization_id"]}),
        types.Tool(name="zendesk_list_brands", description="List Zendesk brands without making changes.", inputSchema={"type": "object", "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 1000, "default": 100}, "cursor": {"type": "string", "minLength": 1}}}),
        types.Tool(name="zendesk_list_ticket_fields", description="List Zendesk ticket fields without making changes.", inputSchema={"type": "object", "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 1000, "default": 100}, "cursor": {"type": "string", "minLength": 1}}}),
        types.Tool(name="zendesk_list_ticket_forms", description="List Zendesk ticket forms without making changes.", inputSchema={"type": "object", "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 1000, "default": 100}, "cursor": {"type": "string", "minLength": 1}}}),
        types.Tool(name="zendesk_list_custom_statuses", description="List Zendesk custom statuses without making changes. Zendesk returns the full list; local offset cursors can shift when statuses change.", inputSchema={"type": "object", "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 1000, "default": 100}, "cursor": {"type": "string", "pattern": "^[0-9]{1,4}$"}}}),
        types.Tool(name="zendesk_list_views", description="List Zendesk views with cursor pagination.", inputSchema={"type": "object", "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 1000, "default": 100}, "cursor": {"type": "string", "minLength": 1}}}),
        types.Tool(name="zendesk_get_view", description="Get a Zendesk view without making changes.", inputSchema={"type": "object", "properties": {"view_id": {"type": "integer", "minimum": 1}}, "required": ["view_id"]}),
        types.Tool(name="zendesk_list_view_tickets", description="List tickets matching a Zendesk view with cursor pagination.", inputSchema={"type": "object", "properties": {"view_id": {"type": "integer", "minimum": 1}, "limit": {"type": "integer", "minimum": 1, "maximum": 1000, "default": 100}, "cursor": {"type": "string", "minLength": 1}}, "required": ["view_id"]}),
        types.Tool(name="zendesk_list_macros", description="List Zendesk macros with cursor pagination, up to 1000 items per call.", inputSchema={"type": "object", "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 1000, "default": 100}, "cursor": {"type": "string", "minLength": 1}}}),
        types.Tool(name="zendesk_list_triggers", description="List Zendesk triggers with cursor pagination.", inputSchema={"type": "object", "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 1000, "default": 100}, "cursor": {"type": "string", "minLength": 1}}}),
        types.Tool(name="zendesk_list_help_center_categories", description="List Help Center categories without making changes.", inputSchema={"type": "object", "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 1000, "default": 100}, "cursor": {"type": "string", "minLength": 1}, "brand_id": {"type": "integer", "minimum": 1}}}),
        types.Tool(name="zendesk_list_help_center_sections", description="List Help Center sections without making changes.", inputSchema={"type": "object", "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 1000, "default": 100}, "cursor": {"type": "string", "minLength": 1}, "brand_id": {"type": "integer", "minimum": 1}}}),
        types.Tool(name="zendesk_search_help_center_articles", description="Search Help Center articles up to 1000 results. Resume with identical query and filters; offset positions can shift when articles change.", inputSchema={"type": "object", "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 1000, "default": 100}, "cursor": {"type": "string", "pattern": "^[0-9]{1,3}$"}, "query": {"type": "string", "minLength": 1}, "brand_id": {"type": "integer", "minimum": 1}, "locale": {"type": "string", "minLength": 2}}, "required": ["query"]}),
        types.Tool(name="zendesk_export_help_center_articles", description="Export Help Center articles for one locale with cursor pagination, up to a bounded total.", inputSchema={"type": "object", "properties": {"locale": {"type": "string", "minLength": 2}, "brand_id": {"type": "integer", "minimum": 1}, "max_articles": {"type": "integer", "minimum": 1, "maximum": 100000}, "format": {"type": "string", "enum": ["json", "csv"], "default": "json"}}, "required": ["locale"]}),
        types.Tool(name="zendesk_get_help_center_article", description="Get a Help Center article or one validated-locale translation without making changes. Optional image embedding is limited to validated tenant Help Center images.", inputSchema={"type": "object", "properties": {"article_id": {"oneOf": [{"type": "integer", "minimum": 1}, {"type": "string", "minLength": 1}]}, "brand_id": {"type": "integer", "minimum": 1}, "locale": {"type": "string", "minLength": 2}, "embed_images": {"type": "boolean", "default": False}}, "required": ["article_id"]}),
        types.Tool(name="zendesk_get_satisfaction_ratings", description="List Zendesk satisfaction ratings without making changes.", inputSchema={"type": "object", "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 1000, "default": 100}, "cursor": {"type": "string", "minLength": 1}}}),
        types.Tool(name="zendesk_list_csat", description="List legacy or survey CSAT responses with backend-specific official filters.", inputSchema={"type": "object", "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 1000, "default": 100}, "cursor": {"type": "string", "minLength": 1}, "backend": {"type": "string", "enum": ["auto", "legacy", "survey"], "default": "auto"}, "score": {"type": "string"}, "ticket_id": {"type": "integer", "minimum": 1}, "responder_ids": {"type": "array", "items": {"type": "integer", "minimum": 1}}, "created_at_start": {"type": "string", "format": "date-time"}, "created_at_end": {"type": "string", "format": "date-time"}}}),
        types.Tool(name="zendesk_export_satisfaction_ratings", description="Export legacy or survey CSAT responses with backend-specific official filters.", inputSchema={"type": "object", "properties": {"backend": {"type": "string", "enum": ["auto", "legacy", "survey"], "default": "auto"}, "score": {"type": "string"}, "ticket_id": {"type": "integer", "minimum": 1}, "responder_ids": {"type": "array", "items": {"type": "integer", "minimum": 1}}, "created_at_start": {"type": "string", "format": "date-time"}, "created_at_end": {"type": "string", "format": "date-time"}, "format": {"type": "string", "enum": ["json", "csv"], "default": "json"}}}),
        types.Tool(name="zendesk_list_guide_permission_groups", description="List Guide management permission groups using offset pagination. Resume cursors can shift when groups change.", inputSchema={"type": "object", "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 1000, "default": 100}, "cursor": {"type": "string", "pattern": "^[0-9]{1,4}$"}}}),
        types.Tool(name="zendesk_list_guide_user_segments", description="List Guide user segments without making changes.", inputSchema={"type": "object", "properties": {"built_in": {"type": "boolean"}, "applicable": {"type": "boolean", "default": False}, "limit": {"type": "integer", "minimum": 1, "maximum": 1000, "default": 100}, "cursor": {"type": "string", "minLength": 1}}}),
        types.Tool(name="zendesk_create_help_center_article", description="Preview or create a draft Help Center article. Apply requires standard write mode and local approval; notifications additionally need the public-write gate.", inputSchema={"type": "object", "properties": {"section_id": {"oneOf": [{"type": "integer", "minimum": 1}, {"type": "string", "minLength": 1}]}, "brand_id": {"type": "integer", "minimum": 1}, "locale": {"type": "string", "minLength": 2}, "title": {"type": "string", "minLength": 1}, "body": {"type": "string"}, "labels": {"type": "array", "items": {"type": "string"}}, "position": {"type": "integer", "minimum": 0}, "permission_group_id": {"type": "integer", "minimum": 1}, "user_segment_id": {"type": "integer", "minimum": 1}, "draft": {"const": True, "default": True}, "notify_subscribers": {"type": "boolean", "default": False}, "execution_mode": {"type": "string", "enum": ["preview", "apply"], "default": "preview"}, "approval_request_id": {"type": "string"}, "approval_token": {"type": "string"}}, "required": ["section_id", "locale", "title", "body"]}),
        types.Tool(name="zendesk_upsert_article_translation", description="Preview or upsert a draft article translation. Existing bodies require the separate replacement tool.", inputSchema={"type": "object", "properties": {"article_id": {"oneOf": [{"type": "integer", "minimum": 1}, {"type": "string", "minLength": 1}]}, "brand_id": {"type": "integer", "minimum": 1}, "locale": {"type": "string", "minLength": 2}, "title": {"type": "string", "minLength": 1}, "body": {"type": "string"}, "draft": {"const": True, "default": True}, "execution_mode": {"type": "string", "enum": ["preview", "apply"], "default": "preview"}, "approval_request_id": {"type": "string"}, "approval_token": {"type": "string"}}, "required": ["article_id", "locale"]}),
        types.Tool(name="zendesk_replace_article_translation_body", description="Preview or replace an existing article translation body. Apply requires destructive approval and may also require public-write approval.", inputSchema={"type": "object", "properties": {"article_id": {"oneOf": [{"type": "integer", "minimum": 1}, {"type": "string", "minLength": 1}]}, "brand_id": {"type": "integer", "minimum": 1}, "locale": {"type": "string", "minLength": 2}, "body": {"type": "string"}, "execution_mode": {"type": "string", "enum": ["preview", "apply"], "default": "preview"}, "approval_request_id": {"type": "string"}, "approval_token": {"type": "string"}}, "required": ["article_id", "locale", "body"]}),
        types.Tool(name="zendesk_publish_help_center_article", description="Preview or publish one draft Help Center article translation. Apply requires public-write approval and reads the translation back.", inputSchema={"type": "object", "properties": {"article_id": {"oneOf": [{"type": "integer", "minimum": 1}, {"type": "string", "minLength": 1}]}, "brand_id": {"type": "integer", "minimum": 1}, "locale": {"type": "string", "minLength": 2}, "execution_mode": {"type": "string", "enum": ["preview", "apply"], "default": "preview"}, "approval_request_id": {"type": "string"}, "approval_token": {"type": "string"}}, "required": ["article_id", "locale"]}),
        types.Tool(name="zendesk_list_community_posts", description="List Community posts, collecting up to limit (default 100, maximum 1000). Resume next_cursor with the same scope and filters when truncated.", inputSchema={"type": "object", "properties": {"topic_id": {"type": "integer", "minimum": 1}, "user_id": {"oneOf": [{"type": "integer", "minimum": 1}, {"type": "string", "enum": ["me"]}]}, "status": {"type": "string", "enum": ["planned", "not_planned", "completed", "answered", "none"]}, "sort_by": {"type": "string", "enum": ["created_at", "edited_at", "updated_at", "recent_activity", "votes", "comments"]}, "cursor": {"type": "string", "minLength": 1}, "limit": {"type": "integer", "minimum": 1, "maximum": 1000, "default": 100}}}),
        types.Tool(name="zendesk_search_community_posts", description="Search Community posts with offset pagination (25 per page, maximum 1000 results). Resume next_cursor with the same query; changing data may shift offsets. truncated marks incomplete results.", inputSchema={"type": "object", "properties": {"query": {"type": "string", "minLength": 1}, "limit": {"type": "integer", "minimum": 1, "maximum": 1000, "default": 100}, "cursor": {"type": "string", "pattern": "^[0-9]{1,3}$"}}, "required": ["query"]}),
        types.Tool(name="zendesk_get_community_post", description="Get a Community post without making changes.", inputSchema={"type": "object", "properties": {"post_id": {"type": "integer", "minimum": 1}}, "required": ["post_id"]}),
        types.Tool(name="zendesk_create_community_post", description="Preview or create a public Community post. Notifications default off; author or creation-time impersonation needs its separate gate and local approval.", inputSchema={"type": "object", "properties": {"topic_id": {"type": "integer", "minimum": 1}, "title": {"type": "string", "minLength": 1}, "details": {"type": "string", "minLength": 1}, "author_id": {"type": "integer", "minimum": 1}, "created_at": {"type": "string", "format": "date-time"}, "notify_subscribers": {"type": "boolean", "default": False}, "execution_mode": {"type": "string", "enum": ["preview", "apply"], "default": "preview"}, "approval_request_id": {"type": "string"}, "approval_token": {"type": "string"}}, "required": ["topic_id", "title", "details"]}),
        types.Tool(name="zendesk_update_community_post", description="Preview or update public Community post fields. Apply requires public-write gate and local approval.", inputSchema={"type": "object", "properties": {"post_id": {"type": "integer", "minimum": 1}, "title": {"type": "string", "minLength": 1}, "details": {"type": "string"}, "topic_id": {"type": "integer", "minimum": 1}, "status": {"type": "string", "enum": ["planned", "not_planned", "answered", "completed"]}, "closed": {"type": "boolean"}, "featured": {"type": "boolean"}, "pinned": {"type": "boolean"}, "content_tag_ids": {"type": "array", "items": {"oneOf": [{"type": "integer", "minimum": 1}, {"type": "string", "minLength": 1}]}}, "execution_mode": {"type": "string", "enum": ["preview", "apply"], "default": "preview"}, "approval_request_id": {"type": "string"}, "approval_token": {"type": "string"}}, "required": ["post_id"]}),
        types.Tool(name="zendesk_delete_community_post", description="Preview or delete a Community post. Apply requires destructive-write gate and local approval.", inputSchema={"type": "object", "properties": {"post_id": {"type": "integer", "minimum": 1}, "execution_mode": {"type": "string", "enum": ["preview", "apply"], "default": "preview"}, "approval_request_id": {"type": "string"}, "approval_token": {"type": "string"}}, "required": ["post_id"]}),
        types.Tool(name="zendesk_create_community_comment", description="Preview or create a public Community comment. Notifications default off; author or creation-time impersonation needs its separate gate and local approval.", inputSchema={"type": "object", "properties": {"post_id": {"type": "integer", "minimum": 1}, "body": {"type": "string", "minLength": 1}, "author_id": {"type": "integer", "minimum": 1}, "created_at": {"type": "string", "format": "date-time"}, "notify_subscribers": {"type": "boolean", "default": False}, "execution_mode": {"type": "string", "enum": ["preview", "apply"], "default": "preview"}, "approval_request_id": {"type": "string"}, "approval_token": {"type": "string"}}, "required": ["post_id", "body"]}),
        types.Tool(name="zendesk_update_community_comment", description="Preview or update a public Community comment. Apply requires public-write gate and local approval.", inputSchema={"type": "object", "properties": {"post_id": {"type": "integer", "minimum": 1}, "comment_id": {"type": "integer", "minimum": 1}, "body": {"type": "string", "minLength": 1}, "official": {"type": "boolean"}, "execution_mode": {"type": "string", "enum": ["preview", "apply"], "default": "preview"}, "approval_request_id": {"type": "string"}, "approval_token": {"type": "string"}}, "required": ["post_id", "comment_id"]}),
        types.Tool(name="zendesk_delete_community_comment", description="Preview or delete a Community comment. Apply requires destructive-write gate and local approval.", inputSchema={"type": "object", "properties": {"post_id": {"type": "integer", "minimum": 1}, "comment_id": {"type": "integer", "minimum": 1}, "execution_mode": {"type": "string", "enum": ["preview", "apply"], "default": "preview"}, "approval_request_id": {"type": "string"}, "approval_token": {"type": "string"}}, "required": ["post_id", "comment_id"]}),
        types.Tool(name="zendesk_create_community_topic", description="Preview or create a public Community topic. Apply requires public-write gate and local approval.", inputSchema={"type": "object", "properties": {"name": {"type": "string", "minLength": 1}, "description": {"type": "string"}, "execution_mode": {"type": "string", "enum": ["preview", "apply"], "default": "preview"}, "approval_request_id": {"type": "string"}, "approval_token": {"type": "string"}}, "required": ["name", "description"]}),
        types.Tool(name="zendesk_update_community_topic", description="Preview or update a public Community topic. Apply requires public-write gate and local approval.", inputSchema={"type": "object", "properties": {"topic_id": {"type": "integer", "minimum": 1}, "name": {"type": "string", "minLength": 1}, "description": {"type": "string"}, "manageable_by": {"type": "string", "enum": ["staff", "managers"]}, "position": {"type": "integer", "minimum": 0}, "user_segment_id": {"type": "integer", "minimum": 1}, "execution_mode": {"type": "string", "enum": ["preview", "apply"], "default": "preview"}, "approval_request_id": {"type": "string"}, "approval_token": {"type": "string"}}, "required": ["topic_id"]}),
        types.Tool(name="zendesk_delete_community_topic", description="Preview or delete a Community topic. Apply requires destructive-write gate and local approval.", inputSchema={"type": "object", "properties": {"topic_id": {"type": "integer", "minimum": 1}, "execution_mode": {"type": "string", "enum": ["preview", "apply"], "default": "preview"}, "approval_request_id": {"type": "string"}, "approval_token": {"type": "string"}}, "required": ["topic_id"]}),
        types.Tool(name="zendesk_list_community_votes", description="List Community votes for one post or user (default 100, maximum 1000). User results include posts and post comments only, scanning at most 1000 raw votes per call. Resume next_cursor with the same scope.", inputSchema={"type": "object", "properties": {"post_id": {"type": "integer", "minimum": 1}, "user_id": {"oneOf": [{"type": "integer", "minimum": 1}, {"type": "string", "enum": ["me"]}]}, "cursor": {"type": "string", "minLength": 1}, "limit": {"type": "integer", "minimum": 1, "maximum": 1000, "default": 100}}, "anyOf": [{"required": ["post_id"]}, {"required": ["user_id"]}]}),
        types.Tool(name="zendesk_get_community_vote", description="Get a Community vote without making changes.", inputSchema={"type": "object", "properties": {"vote_id": {"type": "integer", "minimum": 1}}, "required": ["vote_id"]}),
        types.Tool(name="zendesk_upvote_community_content", description="Preview or upvote a Community post or comment. Apply requires public-write gate and local approval.", inputSchema={"type": "object", "properties": {"content_type": {"type": "string", "enum": ["post", "post_comment"]}, "post_id": {"type": "integer", "minimum": 1}, "comment_id": {"type": "integer", "minimum": 1}, "execution_mode": {"type": "string", "enum": ["preview", "apply"], "default": "preview"}, "approval_request_id": {"type": "string"}, "approval_token": {"type": "string"}}, "required": ["content_type", "post_id"]}),
        types.Tool(name="zendesk_downvote_community_content", description="Preview or downvote a Community post or comment. Apply requires public-write gate and local approval.", inputSchema={"type": "object", "properties": {"content_type": {"type": "string", "enum": ["post", "post_comment"]}, "post_id": {"type": "integer", "minimum": 1}, "comment_id": {"type": "integer", "minimum": 1}, "execution_mode": {"type": "string", "enum": ["preview", "apply"], "default": "preview"}, "approval_request_id": {"type": "string"}, "approval_token": {"type": "string"}}, "required": ["content_type", "post_id"]}),
        types.Tool(name="zendesk_remove_community_vote", description="Preview or remove a Community vote. Apply requires destructive-write gate and local approval.", inputSchema={"type": "object", "properties": {"vote_id": {"type": "integer", "minimum": 1}, "execution_mode": {"type": "string", "enum": ["preview", "apply"], "default": "preview"}, "approval_request_id": {"type": "string"}, "approval_token": {"type": "string"}}, "required": ["vote_id"]}),
        types.Tool(name="zendesk_list_content_subscriptions", description="List subscriptions for a Community post or topic (default 100, maximum 1000). Resume next_cursor with the same content type and ID when truncated.", inputSchema={"type": "object", "properties": {"content_type": {"type": "string", "enum": ["post", "topic"]}, "content_id": {"type": "integer", "minimum": 1}, "limit": {"type": "integer", "minimum": 1, "maximum": 1000, "default": 100}, "cursor": {"type": "string", "minLength": 1}}, "required": ["content_type", "content_id"]}),
        types.Tool(name="zendesk_get_content_subscription", description="Get a Community content subscription without making changes.", inputSchema={"type": "object", "properties": {"content_type": {"type": "string", "enum": ["post", "topic"]}, "content_id": {"type": "integer", "minimum": 1}, "subscription_id": {"type": "integer", "minimum": 1}}, "required": ["content_type", "content_id", "subscription_id"]}),
        types.Tool(name="zendesk_create_content_subscription", description="Preview or create a Community content subscription. Apply requires public-write gate and local approval.", inputSchema={"type": "object", "properties": {"content_type": {"type": "string", "enum": ["post", "topic"]}, "content_id": {"type": "integer", "minimum": 1}, "include_comments": {"type": "boolean", "default": False}, "execution_mode": {"type": "string", "enum": ["preview", "apply"], "default": "preview"}, "approval_request_id": {"type": "string"}, "approval_token": {"type": "string"}}, "required": ["content_type", "content_id"]}),
        types.Tool(name="zendesk_update_content_subscription", description="Preview or update a topic subscription's comment setting. Apply requires public-write gate and local approval.", inputSchema={"type": "object", "properties": {"topic_id": {"type": "integer", "minimum": 1}, "subscription_id": {"type": "integer", "minimum": 1}, "include_comments": {"type": "boolean"}, "execution_mode": {"type": "string", "enum": ["preview", "apply"], "default": "preview"}, "approval_request_id": {"type": "string"}, "approval_token": {"type": "string"}}, "required": ["topic_id", "subscription_id", "include_comments"]}),
        types.Tool(name="zendesk_delete_content_subscription", description="Preview or delete a Community content subscription. Apply requires destructive-write gate and local approval.", inputSchema={"type": "object", "properties": {"content_type": {"type": "string", "enum": ["post", "topic"]}, "content_id": {"type": "integer", "minimum": 1}, "subscription_id": {"type": "integer", "minimum": 1}, "execution_mode": {"type": "string", "enum": ["preview", "apply"], "default": "preview"}, "approval_request_id": {"type": "string"}, "approval_token": {"type": "string"}}, "required": ["content_type", "content_id", "subscription_id"]}),
        types.Tool(name="zendesk_list_community_comments", description="List comments for one Community post or user, collecting up to limit (default 100, maximum 1000). Resume next_cursor with the same scope when truncated.", inputSchema={"type": "object", "properties": {"post_id": {"type": "integer", "minimum": 1}, "user_id": {"oneOf": [{"type": "integer", "minimum": 1}, {"type": "string", "enum": ["me"]}]}, "cursor": {"type": "string", "minLength": 1}, "limit": {"type": "integer", "minimum": 1, "maximum": 1000, "default": 100}}}),
        types.Tool(name="zendesk_get_community_comment", description="Get a Community comment, optionally scoped to its post and locale, without making changes.", inputSchema={"type": "object", "properties": {"comment_id": {"type": "integer", "minimum": 1}, "post_id": {"type": "integer", "minimum": 1}, "locale": {"type": "string", "minLength": 2}}, "required": ["comment_id"]}),
        types.Tool(name="zendesk_list_community_topics", description="List Community topics, collecting up to limit (default 100, maximum 1000). Resume next_cursor when truncated.", inputSchema={"type": "object", "properties": {"cursor": {"type": "string", "minLength": 1}, "limit": {"type": "integer", "minimum": 1, "maximum": 1000, "default": 100}}}),
        types.Tool(name="zendesk_get_community_topic", description="Get a Community topic without making changes.", inputSchema={"type": "object", "properties": {"topic_id": {"type": "integer", "minimum": 1}}, "required": ["topic_id"]}),
        types.Tool(name="zendesk_search_content_tags", description="Search Community content tags, collecting up to limit (default 100, maximum 1000). Resume next_cursor with the same prefix when truncated.", inputSchema={"type": "object", "properties": {"prefix": {"type": "string", "default": ""}, "cursor": {"type": "string", "minLength": 1}, "limit": {"type": "integer", "minimum": 1, "maximum": 1000, "default": 100}}}),
        types.Tool(name="zendesk_count_content_tags", description="Count Community content tags without making changes.", inputSchema={"type": "object", "properties": {}}),
        types.Tool(name="zendesk_get_content_tag", description="Get a Community content tag without making changes.", inputSchema={"type": "object", "properties": {"tag_id": {"type": "string", "minLength": 1}}, "required": ["tag_id"]}),
        types.Tool(name="zendesk_create_content_tag", description="Preview or create a Community content tag. Apply requires public-write gate and local approval.", inputSchema={"type": "object", "properties": {"name": {"type": "string", "minLength": 1}, "execution_mode": {"type": "string", "enum": ["preview", "apply"], "default": "preview"}, "approval_request_id": {"type": "string"}, "approval_token": {"type": "string"}}, "required": ["name"]}),
        types.Tool(name="zendesk_update_content_tag", description="Preview or update a Community content tag. Apply requires public-write gate and local approval.", inputSchema={"type": "object", "properties": {"tag_id": {"type": "string", "minLength": 1}, "name": {"type": "string", "minLength": 1}, "execution_mode": {"type": "string", "enum": ["preview", "apply"], "default": "preview"}, "approval_request_id": {"type": "string"}, "approval_token": {"type": "string"}}, "required": ["tag_id", "name"]}),
        types.Tool(name="zendesk_delete_content_tag", description="Preview or delete a Community content tag. Apply requires destructive-write gate and local approval.", inputSchema={"type": "object", "properties": {"tag_id": {"type": "string", "minLength": 1}, "execution_mode": {"type": "string", "enum": ["preview", "apply"], "default": "preview"}, "approval_request_id": {"type": "string"}, "approval_token": {"type": "string"}}, "required": ["tag_id"]}),
        types.Tool(name="zendesk_list_user_subscriptions", description="List Community user subscriptions, collecting up to limit (default 100, maximum 1000). Resume next_cursor with the same user and direction when truncated.", inputSchema={"type": "object", "properties": {"user_id": {"oneOf": [{"type": "integer", "minimum": 1}, {"type": "string", "enum": ["me"]}]}, "direction": {"type": "string", "enum": ["followers", "followings"], "default": "followers"}, "cursor": {"type": "string", "minLength": 1}, "limit": {"type": "integer", "minimum": 1, "maximum": 1000, "default": 100}}, "required": ["user_id"]}),
        types.Tool(name="zendesk_upsert_user_subscription", description="Preview or create/update a Community user subscription. It is idempotent when include_comments already matches; self-subscription needs public-write approval and another user also needs impersonation gate.", inputSchema={"type": "object", "properties": {"user_id": {"oneOf": [{"type": "integer", "minimum": 1}, {"type": "string", "enum": ["me"]}]}, "followed_id": {"type": "integer", "minimum": 1}, "include_comments": {"type": "boolean", "default": False}, "execution_mode": {"type": "string", "enum": ["preview", "apply"], "default": "preview"}, "approval_request_id": {"type": "string"}, "approval_token": {"type": "string"}}, "required": ["user_id", "followed_id"]}),
        types.Tool(name="zendesk_delete_user_subscription", description="Preview or delete a Community user subscription. Self-delete needs destructive approval; another user also needs impersonation gate.", inputSchema={"type": "object", "properties": {"user_id": {"oneOf": [{"type": "integer", "minimum": 1}, {"type": "string", "enum": ["me"]}]}, "subscription_id": {"type": "integer", "minimum": 1}, "execution_mode": {"type": "string", "enum": ["preview", "apply"], "default": "preview"}, "approval_request_id": {"type": "string"}, "approval_token": {"type": "string"}}, "required": ["user_id", "subscription_id"]}),
        types.Tool(name="zendesk_list_badge_categories", description="List Gather badge categories (default 100, maximum 1000). Uses local offset over the complete upstream list; changing data may shift offsets. Resume with the same filters.", inputSchema={"type": "object", "properties": {"brand_id": {"type": "integer", "minimum": 1}, "limit": {"type": "integer", "minimum": 1, "maximum": 1000, "default": 100}, "cursor": {"type": "string", "pattern": "^[0-9]{1,10}$"}}}),
        types.Tool(name="zendesk_get_badge_category", description="Get a Gather badge category without making changes.", inputSchema={"type": "object", "properties": {"category_id": {"type": "string", "minLength": 1}}, "required": ["category_id"]}),
        types.Tool(name="zendesk_create_badge_category", description="Preview or create a Gather badge category. Apply requires public-write gate and local approval.", inputSchema={"type": "object", "properties": {"brand_id": {"type": "integer", "minimum": 1}, "name": {"type": "string", "minLength": 1}, "slug": {"type": "string", "minLength": 1}, "execution_mode": {"type": "string", "enum": ["preview", "apply"], "default": "preview"}, "approval_request_id": {"type": "string"}, "approval_token": {"type": "string"}}, "required": ["brand_id", "name", "slug"]}),
        types.Tool(name="zendesk_delete_badge_category", description="Preview or delete an empty Gather badge category. Apply requires destructive-write gate and local approval.", inputSchema={"type": "object", "properties": {"category_id": {"type": "string", "minLength": 1}, "execution_mode": {"type": "string", "enum": ["preview", "apply"], "default": "preview"}, "approval_request_id": {"type": "string"}, "approval_token": {"type": "string"}}, "required": ["category_id"]}),
        types.Tool(name="zendesk_list_badges", description="List Gather badges (default 100, maximum 1000). Uses local offset over the complete upstream list; changing data may shift offsets. Resume with the same filters.", inputSchema={"type": "object", "properties": {"brand_id": {"type": "integer", "minimum": 1}, "limit": {"type": "integer", "minimum": 1, "maximum": 1000, "default": 100}, "cursor": {"type": "string", "pattern": "^[0-9]{1,10}$"}}}),
        types.Tool(name="zendesk_get_badge", description="Get a Gather badge without making changes.", inputSchema={"type": "object", "properties": {"badge_id": {"type": "string", "minLength": 1}}, "required": ["badge_id"]}),
        types.Tool(name="zendesk_create_badge", description="Preview or create a Gather badge. Apply requires public-write gate and local approval.", inputSchema={"type": "object", "properties": {"badge_category_id": {"type": "string", "minLength": 1}, "name": {"type": "string", "minLength": 1}, "description": {"type": "string"}, "icon_upload_id": {"type": "string", "minLength": 1}, "execution_mode": {"type": "string", "enum": ["preview", "apply"], "default": "preview"}, "approval_request_id": {"type": "string"}, "approval_token": {"type": "string"}}, "required": ["badge_category_id", "name", "description"]}),
        types.Tool(name="zendesk_update_badge", description="Preview or update a Gather badge. Omit icon_upload_id to retain the icon, pass a new upload ID to replace it, or null to remove it. Apply requires public-write gate and local approval.", inputSchema={"type": "object", "properties": {"badge_id": {"type": "string", "minLength": 1}, "badge_category_id": {"type": "string", "minLength": 1}, "name": {"type": "string", "minLength": 1}, "description": {"type": "string"}, "icon_upload_id": {"type": ["string", "null"], "minLength": 1}, "execution_mode": {"type": "string", "enum": ["preview", "apply"], "default": "preview"}, "approval_request_id": {"type": "string"}, "approval_token": {"type": "string"}}, "required": ["badge_id"]}),
        types.Tool(name="zendesk_delete_badge", description="Preview irreversible deletion of a Gather badge and its assignments, including cascade count. Apply rechecks the assignment snapshot and requires destructive-write gate and matching local approval. Concurrent changes after the final check remain possible.", inputSchema={"type": "object", "properties": {"badge_id": {"type": "string", "minLength": 1}, "execution_mode": {"type": "string", "enum": ["preview", "apply"], "default": "preview"}, "approval_request_id": {"type": "string"}, "approval_token": {"type": "string"}}, "required": ["badge_id"]}),
        types.Tool(name="zendesk_list_badge_assignments", description="List Gather badge assignments (default 100, maximum 1000). Uses local offset over the complete upstream list; changing data may shift offsets. Resume with the same filters.", inputSchema={"type": "object", "properties": {"user_id": {"type": "integer", "minimum": 1}, "badge_id": {"type": "string", "minLength": 1}, "badge_category_id": {"type": "string", "minLength": 1}, "brand_id": {"type": "integer", "minimum": 1}, "limit": {"type": "integer", "minimum": 1, "maximum": 1000, "default": 100}, "cursor": {"type": "string", "pattern": "^[0-9]{1,10}$"}}}),
        types.Tool(name="zendesk_create_badge_assignment", description="Preview or assign a Gather badge to a user. Apply requires public-write and impersonation gates plus local approval.", inputSchema={"type": "object", "properties": {"badge_id": {"type": "string", "minLength": 1}, "user_id": {"type": "integer", "minimum": 1}, "execution_mode": {"type": "string", "enum": ["preview", "apply"], "default": "preview"}, "approval_request_id": {"type": "string"}, "approval_token": {"type": "string"}}, "required": ["badge_id", "user_id"]}),
        types.Tool(name="zendesk_delete_badge_assignment", description="Preview or remove a Gather badge assignment. Apply requires destructive and impersonation gates plus local approval.", inputSchema={"type": "object", "properties": {"assignment_id": {"type": "string", "minLength": 1}, "execution_mode": {"type": "string", "enum": ["preview", "apply"], "default": "preview"}, "approval_request_id": {"type": "string"}, "approval_token": {"type": "string"}}, "required": ["assignment_id"]}),
        types.Tool(name="zendesk_upload_community_user_image", description="Preview or safely upload a Community user image from ZENDESK_UPLOAD_ROOT. Apply requires external-upload gate and local approval.", inputSchema={"type": "object", "properties": {"image_path": {"type": "string", "minLength": 1}, "content_type": {"type": "string", "enum": ["image/jpeg", "image/png", "image/gif"]}, "brand_id": {"type": "integer", "minimum": 1}, "execution_mode": {"type": "string", "enum": ["preview", "apply"], "default": "preview"}, "approval_request_id": {"type": "string"}, "approval_token": {"type": "string"}}, "required": ["image_path", "content_type", "brand_id"]}),
        types.Tool(name="zendesk_upload_badge_icon", description="Preview or safely upload a Gather badge icon from ZENDESK_UPLOAD_ROOT. Apply requires external-upload gate and local approval.", inputSchema={"type": "object", "properties": {"image_path": {"type": "string", "minLength": 1}, "content_type": {"type": "string", "enum": ["image/svg+xml", "image/jpeg", "image/png", "image/gif"]}, "execution_mode": {"type": "string", "enum": ["preview", "apply"], "default": "preview"}, "approval_request_id": {"type": "string"}, "approval_token": {"type": "string"}}, "required": ["image_path", "content_type"]}),
    ]
    return [tool.model_copy(update={"annotations": _tool_annotations(tool.name), "outputSchema": RESULT_SCHEMA}) for tool in tools]


def _tool_annotations(name: str) -> types.ToolAnnotations:
    read_only = name == "zendesk_get_connection_status" or name.startswith(("zendesk_get_", "zendesk_list_", "zendesk_search_", "zendesk_count_", "zendesk_export_", "zendesk_preview_", "zendesk_download_", "zendesk_inspect_", "zendesk_ticket_to_issue_context"))
    destructive = "_delete_" in name or name in {"zendesk_remove_community_vote", "zendesk_replace_article_translation_body"}
    return types.ToolAnnotations(readOnlyHint=read_only, destructiveHint=destructive, idempotentHint=read_only or name == "zendesk_upsert_user_subscription", openWorldHint=name != "zendesk_get_connection_status")


def _tool_risk_class(name: str) -> str:
    annotations = _tool_annotations(name)
    if annotations.destructiveHint:
        return "destructive"
    return "read" if annotations.readOnlyHint else "write"


def _tool_capability(name: str) -> str | None:
    if name == "zendesk_get_connection_status": return None
    if name == "zendesk_get_git_zen_links": return "git_zen"
    if name in {"zendesk_get_time_tracking", "zendesk_log_time"}: return "time_tracking"
    if "badge" in name: return "badges"
    if name in {"zendesk_get_satisfaction_ratings", "zendesk_list_csat", "zendesk_export_satisfaction_ratings"}: return "csat"
    if any(value in name for value in ("community", "content_subscription", "content_tag", "user_subscription", "_vote", "upload_badge")): return "community"
    if any(value in name for value in ("help_center", "article_translation", "guide_")): return "guide"
    if name in {"zendesk_search_users", "zendesk_list_groups", "zendesk_list_group_users", "zendesk_get_organization", "zendesk_list_brands", "zendesk_list_ticket_fields", "zendesk_list_ticket_forms", "zendesk_list_custom_statuses", "zendesk_list_views", "zendesk_get_view", "zendesk_list_view_tickets", "zendesk_list_macros", "zendesk_list_triggers", "zendesk_preview_macro", "zendesk_apply_ticket_macro"}: return "operations"
    return "support"


def _capability_gate(environ: Mapping[str, str], name: str) -> dict[str, object] | None:
    try: settings = Settings.load(environ)
    except ConfigurationError as error: return _configuration_failure(error)
    capability = _tool_capability(name)
    return failure(ErrorCode.NOT_CONFIGURED, f"Zendesk {capability} capability is not enabled") if capability is not None and not settings.has_capability(capability) else None


def _configuration_failure(error: ConfigurationError) -> dict[str, object]:
    if error.code == "unsupported": return failure(ErrorCode.UNSUPPORTED, str(error))
    code = ErrorCode.REAUTHORIZATION_REQUIRED if error.code in {
        "missing_oauth_tokens", "invalid_oauth_tokens", "oauth_refresh_failed",
        "invalid_oauth_refresh", "oauth_refresh_unavailable", "oauth_relogin_required",
    } else ErrorCode.VALIDATION_ERROR
    return failure(code, str(error))


def build_connection_status(environ: Mapping[str, str], *, probe: bool = False) -> dict[str, object]:
    try:
        settings = Settings.load(environ)
        status = settings.connection_status()
        if not probe or settings.auth_mode is None: return success(status)
        authorization = build_authorization(settings)
        if authorization is None: return success(status)
        result = ZendeskClient(settings, authorization).get("/api/v2/users/me.json")
        if not result.get("ok"): return result
        data = result.get("data"); user = data.get("user") if isinstance(data, dict) else None
        if not isinstance(user, dict): return failure(ErrorCode.UPSTREAM_ERROR, "Zendesk returned an invalid authenticated user")
        return success({**status, "verified_user": {key: user.get(key) for key in ("id", "role")}})
    except ConfigurationError as error:
        return _configuration_failure(error)


def build_ticket_tools(environ: Mapping[str, str]) -> TicketTools | dict[str, object]:
    try:
        settings = Settings.load(environ)
        authorization = build_authorization(settings)
    except ConfigurationError as error:
        return _configuration_failure(error)
    if authorization is None:
        return failure(ErrorCode.NOT_CONFIGURED, "Zendesk is not configured")
    return TicketTools(ZendeskClient(settings, authorization), settings, ApprovalStore.from_environment(environ))


def build_metadata_tools(environ: Mapping[str, str]) -> MetadataTools | dict[str, object]:
    try:
        settings = Settings.load(environ)
        authorization = build_authorization(settings)
    except ConfigurationError as error:
        return _configuration_failure(error)
    if authorization is None:
        return failure(ErrorCode.NOT_CONFIGURED, "Zendesk is not configured")
    return MetadataTools(ZendeskClient(settings, authorization))


def build_guide_tools(environ: Mapping[str, str]) -> GuideTools | dict[str, object]:
    try:
        settings = Settings.load(environ)
        authorization = build_authorization(settings)
    except ConfigurationError as error:
        return _configuration_failure(error)
    if authorization is None:
        return failure(ErrorCode.NOT_CONFIGURED, "Zendesk is not configured")
    return GuideTools(ZendeskClient(settings, authorization), settings, ApprovalStore.from_environment(environ))


def build_community_tools(environ: Mapping[str, str]) -> CommunityTools | dict[str, object]:
    try:
        settings = Settings.load(environ); authorization = build_authorization(settings)
    except ConfigurationError as error: return _configuration_failure(error)
    if authorization is None: return failure(ErrorCode.NOT_CONFIGURED, "Zendesk is not configured")
    return CommunityTools(ZendeskClient(settings, authorization), settings, ApprovalStore.from_environment(environ))


def attachment_download_content(result: dict[str, object]) -> list[object]:
    data = result.get("data")
    if not result.get("ok") or not isinstance(data, dict) or not isinstance(data.get("cache_path"), str):
        return [types.TextContent(type="text", text=json.dumps(result))]
    path = Path(data["cache_path"])
    if not path.is_absolute():
        return [types.TextContent(type="text", text=json.dumps(result))]
    summary = {key: value for key, value in data.items() if key != "cache_path"}
    content_type = data.get("content_type") if isinstance(data.get("content_type"), str) else None
    return [
        types.TextContent(type="text", text=json.dumps({**result, "data": summary})),
        types.ResourceLink(
            type="resource_link",
            name=f"Zendesk ticket {data.get('ticket_id')} attachment {data.get('attachment_id')}",
            uri=path.as_uri(),
            mimeType=content_type,
        ),
    ]


def _export_content(result: dict[str, object], label: str) -> list[object]:
    data = result.get("data")
    if not result.get("ok") or not isinstance(data, dict) or not isinstance(data.get("cache_path"), str):
        return [types.TextContent(type="text", text=json.dumps(result))]
    path = Path(data["cache_path"])
    if not path.is_absolute(): return [types.TextContent(type="text", text=json.dumps(result))]
    output_format = data.get("format") if isinstance(data.get("format"), str) else "json"
    summary = {key: value for key, value in data.items() if key != "cache_path"}
    return [
        types.TextContent(type="text", text=json.dumps({**result, "data": summary})),
        types.ResourceLink(type="resource_link", name=f"{label} ({output_format})", uri=path.as_uri(), mimeType={"json": "application/json", "csv": "text/csv"}.get(output_format), size=data.get("size") if type(data.get("size")) is int and data["size"] >= 0 else None),
    ]


def ticket_export_content(result: dict[str, object]) -> list[object]: return _export_content(result, "Zendesk ticket export")


def csat_export_content(result: dict[str, object]) -> list[object]:
    return _export_content(result, "Zendesk CSAT export")


def help_center_export_content(result: dict[str, object]) -> list[object]: return _export_content(result, "Zendesk Help Center export")


def help_center_article_content(result: dict[str, object]) -> list[object]:
    data = result.get("data")
    images = data.get("images") if result.get("ok") and isinstance(data, dict) else None
    if not isinstance(images, list): return [types.TextContent(type="text", text=json.dumps(result))]
    summary_images = [{key: value for key, value in image.items() if key != "content"} for image in images if isinstance(image, dict)]
    content: list[object] = [types.TextContent(type="text", text=json.dumps({**result, "data": {**data, "images": summary_images}}))]
    for image in images:
        if not isinstance(image, dict) or not isinstance(image.get("content"), bytes) or not isinstance(image.get("content_type"), str): continue
        content.append(types.ImageContent(type="image", data=base64.b64encode(image["content"]).decode(), mimeType=image["content_type"]))
    return content


def attachment_inspection_content(result: dict[str, object]) -> list[object]:
    data = result.get("data")
    if not result.get("ok") or not isinstance(data, dict) or data.get("kind") != "image" or not isinstance(data.get("image_data"), str) or not isinstance(data.get("mime_type"), str):
        return [types.TextContent(type="text", text=json.dumps(result))]
    summary = {key: value for key, value in data.items() if key != "image_data"}
    return [
        types.TextContent(type="text", text=json.dumps({**result, "data": summary})),
        types.ImageContent(type="image", data=data["image_data"], mimeType=data["mime_type"]),
    ]


def create_server(environ: Mapping[str, str] | None = None) -> Server:
    environment = dict(os.environ) if environ is None else dict(environ)
    server = Server("Zendesk")
    audit_log = AuditLog.from_environment(environment)

    try:
        settings = Settings.load(environment)
        knowledge_base_enabled = settings.knowledge_base_resource_enabled and settings.has_capability("guide")
    except ConfigurationError:
        knowledge_base_enabled = False
    if knowledge_base_enabled:
        knowledge_base_cache: tuple[float, str] | None = None

        @server.list_resources()
        async def handle_list_resources() -> list[types.Resource]:
            return [types.Resource(name="Zendesk Knowledge Base", uri="zendesk://knowledge-base", description="Locale export manifest with managed file resource links; cached for one hour.", mimeType="application/json")]

        @server.read_resource()
        async def handle_read_resource(uri: object) -> list[ReadResourceContents]:
            nonlocal knowledge_base_cache
            if str(uri) != "zendesk://knowledge-base": raise ValueError("Unknown Zendesk resource")
            if knowledge_base_cache is not None and time.monotonic() - knowledge_base_cache[0] < 60 * 60:
                return [ReadResourceContents(knowledge_base_cache[1], "application/json")]
            tools = build_guide_tools(environment)
            if isinstance(tools, dict): return [ReadResourceContents(json.dumps(tools), "application/json")]
            locales_result = tools.list_locales()
            if not locales_result.get("ok"): return [ReadResourceContents(json.dumps(locales_result), "application/json")]
            locales_data = locales_result.get("data"); locales = locales_data.get("locales") if isinstance(locales_data, dict) else None
            if not isinstance(locales, list) or not all(isinstance(locale, str) for locale in locales): return [ReadResourceContents(json.dumps(failure(ErrorCode.UPSTREAM_ERROR, "Zendesk returned invalid Help Center locales")), "application/json")]
            exports = []; remaining = 100000; truncated = False
            for locale in locales:
                if remaining == 0:
                    truncated = True
                    break
                exported = tools.export_article_artifact(locale, max_articles=remaining)
                if not exported.get("ok"): return [ReadResourceContents(json.dumps(exported), "application/json")]
                data = exported.get("data")
                if not isinstance(data, dict) or type(data.get("item_count")) is not int or not 0 <= data["item_count"] <= remaining or not isinstance(data.get("truncated"), bool) or not isinstance(data.get("cache_path"), str) or not Path(data["cache_path"]).is_absolute():
                    return [ReadResourceContents(json.dumps(failure(ErrorCode.UPSTREAM_ERROR, "Zendesk returned an invalid knowledge base export")), "application/json")]
                remaining -= data["item_count"]
                resource = _export_content(exported, f"Zendesk knowledge base {locale}")[1]
                exports.append({"locale": locale, **{key: value for key, value in data.items() if key != "cache_path"}, "resource": resource.model_dump(mode="json", exclude_none=True)})
                truncated = truncated or data["truncated"]
            content = json.dumps(success({"locales": exports, "truncated": truncated}), ensure_ascii=False)
            knowledge_base_cache = time.monotonic(), content
            return [ReadResourceContents(content, "application/json")]

    @server.list_prompts()
    async def handle_list_prompts() -> list[types.Prompt]:
        return [
            types.Prompt(
                name="analyze-ticket",
                description="Analyze a Zendesk ticket and provide insights",
                arguments=[
                    types.PromptArgument(
                        name="ticket_id",
                        description="The ID of the ticket to analyze",
                        required=True,
                    )
                ],
            ),
            types.Prompt(
                name="draft-ticket-response",
                description="Draft a professional response to a Zendesk ticket",
                arguments=[
                    types.PromptArgument(
                        name="ticket_id",
                        description="The ID of the ticket to respond to",
                        required=True,
                    )
                ],
            ),
        ]

    @server.get_prompt()
    async def handle_get_prompt(
        name: str,
        arguments: dict[str, str] | None,
    ) -> types.GetPromptResult:
        if not arguments or not arguments.get("ticket_id"):
            raise ValueError("Missing required argument: ticket_id")
        templates = {
            "analyze-ticket": TICKET_ANALYSIS_TEMPLATE,
            "draft-ticket-response": COMMENT_DRAFT_TEMPLATE,
        }
        try:
            prompt = templates[name].format(ticket_id=arguments["ticket_id"])
        except KeyError as error:
            raise ValueError(f"Unknown prompt: {name}") from error
        return types.GetPromptResult(
            description=f"Zendesk prompt for ticket #{arguments['ticket_id']}",
            messages=[
                types.PromptMessage(
                    role="user",
                    content=types.TextContent(type="text", text=prompt.strip()),
                )
            ],
        )

    @server.list_tools()
    async def handle_list_tools() -> list[types.Tool]:
        return build_tools()

    @server.call_tool()
    async def handle_call_tool(
        name: str,
        arguments: dict[str, object] | None,
    ) -> list[object]:
        started_at = time.monotonic()

        def respond(result: dict[str, object]) -> tuple[list[object], dict[str, object]]:
            audit_log.record(name, _tool_risk_class(name), arguments, result, started_at=started_at)
            if name == "zendesk_download_ticket_attachment": return attachment_download_content(result), result
            if name == "zendesk_inspect_ticket_attachment": return attachment_inspection_content(result), result
            if name == "zendesk_export_tickets": return ticket_export_content(result), result
            if name == "zendesk_export_satisfaction_ratings": return csat_export_content(result), result
            if name == "zendesk_export_help_center_articles": return help_center_export_content(result), result
            if name == "zendesk_get_help_center_article": return help_center_article_content(result), result
            return [types.TextContent(type="text", text=json.dumps(result))], result

        if (blocked := _capability_gate(environment, name)) is not None:
            return respond(blocked)
        if name == "zendesk_get_connection_status":
            result = build_connection_status(environment, probe=True)
        elif name in {"zendesk_list_community_posts", "zendesk_search_community_posts", "zendesk_get_community_post", "zendesk_create_community_post", "zendesk_update_community_post", "zendesk_delete_community_post", "zendesk_create_community_comment", "zendesk_update_community_comment", "zendesk_delete_community_comment", "zendesk_create_community_topic", "zendesk_update_community_topic", "zendesk_delete_community_topic", "zendesk_list_community_votes", "zendesk_get_community_vote", "zendesk_upvote_community_content", "zendesk_downvote_community_content", "zendesk_remove_community_vote", "zendesk_list_content_subscriptions", "zendesk_get_content_subscription", "zendesk_create_content_subscription", "zendesk_update_content_subscription", "zendesk_delete_content_subscription", "zendesk_list_community_comments", "zendesk_get_community_comment", "zendesk_list_community_topics", "zendesk_get_community_topic", "zendesk_search_content_tags", "zendesk_count_content_tags", "zendesk_get_content_tag", "zendesk_create_content_tag", "zendesk_update_content_tag", "zendesk_delete_content_tag", "zendesk_list_user_subscriptions", "zendesk_upsert_user_subscription", "zendesk_delete_user_subscription", "zendesk_list_badge_categories", "zendesk_get_badge_category", "zendesk_create_badge_category", "zendesk_delete_badge_category", "zendesk_list_badges", "zendesk_get_badge", "zendesk_create_badge", "zendesk_update_badge", "zendesk_delete_badge", "zendesk_list_badge_assignments", "zendesk_create_badge_assignment", "zendesk_delete_badge_assignment", "zendesk_upload_community_user_image", "zendesk_upload_badge_icon"}:
            tools = build_community_tools(environment)
            if isinstance(tools, dict): result = tools
            elif name == "zendesk_list_community_posts":
                values = arguments or {}; result = tools.list_posts(topic_id=values.get("topic_id"), user_id=values.get("user_id"), status=values.get("status"), sort_by=values.get("sort_by"), cursor=values.get("cursor"), limit=values.get("limit", 100))
            elif name == "zendesk_search_community_posts":
                values = arguments or {}; result = tools.search_posts(values.get("query"), limit=values.get("limit", 100), cursor=values.get("cursor"))
            elif name == "zendesk_create_community_post":
                values = arguments or {}; result = tools.create_post(values.get("topic_id"), values.get("title"), values.get("details"), author_id=values.get("author_id"), created_at=values.get("created_at"), notify_subscribers=values.get("notify_subscribers", False), execution_mode=values.get("execution_mode", "preview"), approval_request_id=values.get("approval_request_id"), approval_token=values.get("approval_token"))
            elif name == "zendesk_update_community_post":
                values = arguments or {}; post = {field: values[field] for field in {"title", "details", "topic_id", "status", "closed", "featured", "pinned", "content_tag_ids"} if field in values}; result = tools.update_post(values.get("post_id"), post, execution_mode=values.get("execution_mode", "preview"), approval_request_id=values.get("approval_request_id"), approval_token=values.get("approval_token"))
            elif name == "zendesk_delete_community_post":
                values = arguments or {}; result = tools.delete_post(values.get("post_id"), execution_mode=values.get("execution_mode", "preview"), approval_request_id=values.get("approval_request_id"), approval_token=values.get("approval_token"))
            elif name == "zendesk_create_community_comment":
                values = arguments or {}; result = tools.create_comment(values.get("post_id"), values.get("body"), author_id=values.get("author_id"), created_at=values.get("created_at"), notify_subscribers=values.get("notify_subscribers", False), execution_mode=values.get("execution_mode", "preview"), approval_request_id=values.get("approval_request_id"), approval_token=values.get("approval_token"))
            elif name == "zendesk_update_community_comment":
                values = arguments or {}; comment = {field: values[field] for field in {"body", "official"} if field in values}; result = tools.update_comment(values.get("post_id"), values.get("comment_id"), comment, execution_mode=values.get("execution_mode", "preview"), approval_request_id=values.get("approval_request_id"), approval_token=values.get("approval_token"))
            elif name == "zendesk_delete_community_comment":
                values = arguments or {}; result = tools.delete_comment(values.get("post_id"), values.get("comment_id"), execution_mode=values.get("execution_mode", "preview"), approval_request_id=values.get("approval_request_id"), approval_token=values.get("approval_token"))
            elif name == "zendesk_create_community_topic":
                values = arguments or {}; result = tools.create_topic(values.get("name"), values.get("description"), execution_mode=values.get("execution_mode", "preview"), approval_request_id=values.get("approval_request_id"), approval_token=values.get("approval_token"))
            elif name == "zendesk_update_community_topic":
                values = arguments or {}; topic = {field: values[field] for field in {"name", "description", "manageable_by", "position", "user_segment_id"} if field in values}; result = tools.update_topic(values.get("topic_id"), topic, execution_mode=values.get("execution_mode", "preview"), approval_request_id=values.get("approval_request_id"), approval_token=values.get("approval_token"))
            elif name == "zendesk_delete_community_topic":
                values = arguments or {}; result = tools.delete_topic(values.get("topic_id"), execution_mode=values.get("execution_mode", "preview"), approval_request_id=values.get("approval_request_id"), approval_token=values.get("approval_token"))
            elif name == "zendesk_list_community_votes":
                values = arguments or {}; result = tools.list_votes(values.get("post_id"), user_id=values.get("user_id"), cursor=values.get("cursor"), limit=values.get("limit", 100))
            elif name == "zendesk_get_community_vote": result = tools.get_vote((arguments or {}).get("vote_id"))
            elif name in {"zendesk_upvote_community_content", "zendesk_downvote_community_content"}:
                values = arguments or {}; result = tools.cast_vote(values.get("content_type"), values.get("post_id"), values.get("comment_id"), "up" if name == "zendesk_upvote_community_content" else "down", execution_mode=values.get("execution_mode", "preview"), approval_request_id=values.get("approval_request_id"), approval_token=values.get("approval_token"))
            elif name == "zendesk_remove_community_vote":
                values = arguments or {}; result = tools.delete_vote(values.get("vote_id"), execution_mode=values.get("execution_mode", "preview"), approval_request_id=values.get("approval_request_id"), approval_token=values.get("approval_token"))
            elif name == "zendesk_list_content_subscriptions":
                values = arguments or {}
                if values.get("content_type") == "post": result = tools.list_post_subscriptions(values.get("content_id"), limit=values.get("limit", 100), cursor=values.get("cursor"))
                elif values.get("content_type") == "topic": result = tools.list_topic_subscriptions(values.get("content_id"), limit=values.get("limit", 100), cursor=values.get("cursor"))
                else: result = failure(ErrorCode.VALIDATION_ERROR, "content_type must be post or topic")
            elif name == "zendesk_get_content_subscription":
                values = arguments or {}; result = tools.get_subscription(values.get("content_type"), values.get("content_id"), values.get("subscription_id"))
            elif name == "zendesk_create_content_subscription":
                values = arguments or {}; result = tools.create_subscription(values.get("content_type"), values.get("content_id"), include_comments=values.get("include_comments", False), execution_mode=values.get("execution_mode", "preview"), approval_request_id=values.get("approval_request_id"), approval_token=values.get("approval_token"))
            elif name == "zendesk_update_content_subscription":
                values = arguments or {}; result = tools.update_subscription(values.get("topic_id"), values.get("subscription_id"), include_comments=values.get("include_comments"), execution_mode=values.get("execution_mode", "preview"), approval_request_id=values.get("approval_request_id"), approval_token=values.get("approval_token"))
            elif name == "zendesk_delete_content_subscription":
                values = arguments or {}; result = tools.delete_subscription(values.get("content_type"), values.get("content_id"), values.get("subscription_id"), execution_mode=values.get("execution_mode", "preview"), approval_request_id=values.get("approval_request_id"), approval_token=values.get("approval_token"))
            elif name == "zendesk_list_community_comments":
                values = arguments or {}; result = tools.list_comments(values.get("post_id"), user_id=values.get("user_id"), cursor=values.get("cursor"), limit=values.get("limit", 100))
            elif name == "zendesk_get_community_comment":
                values = arguments or {}; result = tools.get_comment(values.get("comment_id"), post_id=values.get("post_id"), locale=values.get("locale"))
            elif name == "zendesk_list_community_topics":
                values = arguments or {}; result = tools.list_topics(cursor=values.get("cursor"), limit=values.get("limit", 100))
            elif name == "zendesk_get_community_topic": result = tools.get_topic((arguments or {}).get("topic_id"))
            elif name == "zendesk_search_content_tags":
                values = arguments or {}; result = tools.search_content_tags(values.get("prefix", ""), cursor=values.get("cursor"), limit=values.get("limit", 100))
            elif name == "zendesk_count_content_tags": result = tools.count_content_tags()
            elif name == "zendesk_get_content_tag": result = tools.get_content_tag((arguments or {}).get("tag_id"))
            elif name == "zendesk_create_content_tag":
                values = arguments or {}; result = tools.create_content_tag(values.get("name"), execution_mode=values.get("execution_mode", "preview"), approval_request_id=values.get("approval_request_id"), approval_token=values.get("approval_token"))
            elif name == "zendesk_update_content_tag":
                values = arguments or {}; result = tools.update_content_tag(values.get("tag_id"), values.get("name"), execution_mode=values.get("execution_mode", "preview"), approval_request_id=values.get("approval_request_id"), approval_token=values.get("approval_token"))
            elif name == "zendesk_delete_content_tag":
                values = arguments or {}; result = tools.delete_content_tag(values.get("tag_id"), execution_mode=values.get("execution_mode", "preview"), approval_request_id=values.get("approval_request_id"), approval_token=values.get("approval_token"))
            elif name == "zendesk_list_user_subscriptions":
                values = arguments or {}; result = tools.list_user_subscriptions(values.get("user_id"), values.get("direction", "followers"), cursor=values.get("cursor"), limit=values.get("limit", 100))
            elif name == "zendesk_upsert_user_subscription":
                values = arguments or {}; result = tools.upsert_user_subscription(values.get("user_id"), values.get("followed_id"), include_comments=values.get("include_comments", False), execution_mode=values.get("execution_mode", "preview"), approval_request_id=values.get("approval_request_id"), approval_token=values.get("approval_token"))
            elif name == "zendesk_delete_user_subscription":
                values = arguments or {}; result = tools.delete_user_subscription(values.get("user_id"), values.get("subscription_id"), execution_mode=values.get("execution_mode", "preview"), approval_request_id=values.get("approval_request_id"), approval_token=values.get("approval_token"))
            elif name == "zendesk_list_badge_categories":
                values = arguments or {}; result = tools.list_badge_categories(values.get("brand_id"), limit=values.get("limit", 100), cursor=values.get("cursor"))
            elif name == "zendesk_get_badge_category": result = tools.get_badge_category((arguments or {}).get("category_id"))
            elif name == "zendesk_create_badge_category":
                values = arguments or {}; result = tools.create_badge_category(values.get("brand_id"), values.get("name"), values.get("slug"), execution_mode=values.get("execution_mode", "preview"), approval_request_id=values.get("approval_request_id"), approval_token=values.get("approval_token"))
            elif name == "zendesk_delete_badge_category":
                values = arguments or {}; result = tools.delete_badge_category(values.get("category_id"), execution_mode=values.get("execution_mode", "preview"), approval_request_id=values.get("approval_request_id"), approval_token=values.get("approval_token"))
            elif name == "zendesk_list_badges":
                values = arguments or {}; result = tools.list_badges(values.get("brand_id"), limit=values.get("limit", 100), cursor=values.get("cursor"))
            elif name == "zendesk_get_badge": result = tools.get_badge((arguments or {}).get("badge_id"))
            elif name == "zendesk_create_badge":
                values = arguments or {}; result = tools.create_badge(values.get("badge_category_id"), values.get("name"), values.get("description"), icon_upload_id=values.get("icon_upload_id"), execution_mode=values.get("execution_mode", "preview"), approval_request_id=values.get("approval_request_id"), approval_token=values.get("approval_token"))
            elif name == "zendesk_update_badge":
                values = arguments or {}; badge = {field: values[field] for field in {"badge_category_id", "name", "description", "icon_upload_id"} if field in values}; result = tools.update_badge(values.get("badge_id"), badge, execution_mode=values.get("execution_mode", "preview"), approval_request_id=values.get("approval_request_id"), approval_token=values.get("approval_token"))
            elif name == "zendesk_delete_badge":
                values = arguments or {}; result = tools.delete_badge(values.get("badge_id"), execution_mode=values.get("execution_mode", "preview"), approval_request_id=values.get("approval_request_id"), approval_token=values.get("approval_token"))
            elif name == "zendesk_list_badge_assignments":
                values = arguments or {}; result = tools.list_badge_assignments(user_id=values.get("user_id"), badge_id=values.get("badge_id"), badge_category_id=values.get("badge_category_id"), brand_id=values.get("brand_id"), limit=values.get("limit", 100), cursor=values.get("cursor"))
            elif name == "zendesk_create_badge_assignment":
                values = arguments or {}; result = tools.create_badge_assignment(values.get("badge_id"), values.get("user_id"), execution_mode=values.get("execution_mode", "preview"), approval_request_id=values.get("approval_request_id"), approval_token=values.get("approval_token"))
            elif name == "zendesk_delete_badge_assignment":
                values = arguments or {}; result = tools.delete_badge_assignment(values.get("assignment_id"), execution_mode=values.get("execution_mode", "preview"), approval_request_id=values.get("approval_request_id"), approval_token=values.get("approval_token"))
            elif name == "zendesk_upload_community_user_image":
                values = arguments or {}; result = tools.upload_user_image(values.get("image_path"), values.get("content_type"), values.get("brand_id"), execution_mode=values.get("execution_mode", "preview"), approval_request_id=values.get("approval_request_id"), approval_token=values.get("approval_token"))
            elif name == "zendesk_upload_badge_icon":
                values = arguments or {}; result = tools.upload_badge_icon(values.get("image_path"), values.get("content_type"), execution_mode=values.get("execution_mode", "preview"), approval_request_id=values.get("approval_request_id"), approval_token=values.get("approval_token"))
            else: result = tools.get_post((arguments or {}).get("post_id"))
        elif name in {"zendesk_list_help_center_locales", "zendesk_list_help_center_categories", "zendesk_list_help_center_sections", "zendesk_search_help_center_articles", "zendesk_export_help_center_articles", "zendesk_get_help_center_article", "zendesk_get_satisfaction_ratings", "zendesk_list_csat", "zendesk_export_satisfaction_ratings", "zendesk_list_guide_permission_groups", "zendesk_list_guide_user_segments", "zendesk_create_help_center_article", "zendesk_upsert_article_translation", "zendesk_replace_article_translation_body", "zendesk_publish_help_center_article"}:
            tools = build_guide_tools(environment)
            if isinstance(tools, dict): result = tools
            elif name == "zendesk_list_help_center_locales": result = tools.list_locales(brand_id=(arguments or {}).get("brand_id"))
            elif name == "zendesk_list_help_center_categories": result = tools.list_categories(brand_id=(arguments or {}).get("brand_id"), limit=(arguments or {}).get("limit", 100), cursor=(arguments or {}).get("cursor"))
            elif name == "zendesk_list_help_center_sections": result = tools.list_sections(brand_id=(arguments or {}).get("brand_id"), limit=(arguments or {}).get("limit", 100), cursor=(arguments or {}).get("cursor"))
            elif name == "zendesk_search_help_center_articles":
                values = arguments or {}; result = tools.search_articles(values.get("query"), brand_id=values.get("brand_id"), locale=values.get("locale"), limit=values.get("limit", 100), cursor=values.get("cursor"))
            elif name == "zendesk_export_help_center_articles":
                values = arguments or {}; result = tools.export_article_artifact(values.get("locale"), values.get("max_articles", 100000), brand_id=values.get("brand_id"), output_format=values.get("format", "json"))
            elif name == "zendesk_get_help_center_article":
                values = arguments or {}; result = tools.get_article(values.get("article_id"), brand_id=values.get("brand_id"), locale=values.get("locale"), embed_images=values.get("embed_images", False))
            elif name in {"zendesk_list_csat", "zendesk_export_satisfaction_ratings"}:
                values = arguments or {}
                result = tools.export_csat(values.get("backend", "auto"), score=values.get("score"), ticket_id=values.get("ticket_id"), responder_ids=values.get("responder_ids"), created_at_start=values.get("created_at_start"), created_at_end=values.get("created_at_end"), output_format=values.get("format", "json")) if name == "zendesk_export_satisfaction_ratings" else tools.list_csat(values.get("backend", "auto"), score=values.get("score"), ticket_id=values.get("ticket_id"), responder_ids=values.get("responder_ids"), created_at_start=values.get("created_at_start"), created_at_end=values.get("created_at_end"), limit=values.get("limit", 100), cursor=values.get("cursor"))
            elif name == "zendesk_list_guide_permission_groups": result = tools.list_permission_groups((arguments or {}).get("limit", 100), cursor=(arguments or {}).get("cursor"))
            elif name == "zendesk_list_guide_user_segments":
                values = arguments or {}; result = tools.list_user_segments(built_in=values.get("built_in"), applicable=values.get("applicable", False), limit=values.get("limit", 100), cursor=values.get("cursor"))
            elif name == "zendesk_create_help_center_article":
                values = arguments or {}; result = tools.create_article(values.get("section_id"), values.get("locale"), values.get("title"), values.get("body"), brand_id=values.get("brand_id"), labels=values.get("labels"), position=values.get("position"), permission_group_id=values.get("permission_group_id"), user_segment_id=values.get("user_segment_id"), draft=values.get("draft", True), notify_subscribers=values.get("notify_subscribers", False), execution_mode=values.get("execution_mode", "preview"), approval_request_id=values.get("approval_request_id"), approval_token=values.get("approval_token"))
            elif name == "zendesk_upsert_article_translation":
                values = arguments or {}; result = tools.upsert_article_translation(values.get("article_id"), values.get("locale"), brand_id=values.get("brand_id"), title=values.get("title"), body=values.get("body"), draft=values.get("draft", True), execution_mode=values.get("execution_mode", "preview"), approval_request_id=values.get("approval_request_id"), approval_token=values.get("approval_token"))
            elif name == "zendesk_replace_article_translation_body":
                values = arguments or {}; result = tools.replace_article_translation_body(values.get("article_id"), values.get("locale"), values.get("body"), brand_id=values.get("brand_id"), execution_mode=values.get("execution_mode", "preview"), approval_request_id=values.get("approval_request_id"), approval_token=values.get("approval_token"))
            elif name == "zendesk_publish_help_center_article":
                values = arguments or {}; result = tools.publish_article(values.get("article_id"), values.get("locale"), brand_id=values.get("brand_id"), execution_mode=values.get("execution_mode", "preview"), approval_request_id=values.get("approval_request_id"), approval_token=values.get("approval_token"))
            else: result = tools.get_satisfaction_ratings((arguments or {}).get("limit", 100), cursor=(arguments or {}).get("cursor"))
        elif name in {"zendesk_search_users", "zendesk_list_groups", "zendesk_list_group_users", "zendesk_get_organization", "zendesk_list_brands", "zendesk_list_ticket_fields", "zendesk_list_ticket_forms", "zendesk_list_custom_statuses", "zendesk_list_views", "zendesk_get_view", "zendesk_list_view_tickets", "zendesk_list_macros", "zendesk_list_triggers"}:
            tools = build_metadata_tools(environment)
            if isinstance(tools, dict):
                result = tools
            elif name == "zendesk_search_users": result = tools.search_users((arguments or {}).get("query"), (arguments or {}).get("limit", 100), cursor=(arguments or {}).get("cursor"))
            elif name == "zendesk_list_groups": result = tools.list_groups((arguments or {}).get("limit", 100), cursor=(arguments or {}).get("cursor"))
            elif name == "zendesk_list_group_users": result = tools.list_group_users((arguments or {}).get("group_id"), (arguments or {}).get("limit", 100), cursor=(arguments or {}).get("cursor"))
            elif name == "zendesk_get_organization": result = tools.get_organization((arguments or {}).get("organization_id"))
            elif name == "zendesk_list_brands": result = tools.list_brands((arguments or {}).get("limit", 100), cursor=(arguments or {}).get("cursor"))
            elif name == "zendesk_list_ticket_fields": result = tools.list_ticket_fields((arguments or {}).get("limit", 100), cursor=(arguments or {}).get("cursor"))
            elif name == "zendesk_list_ticket_forms": result = tools.list_ticket_forms((arguments or {}).get("limit", 100), cursor=(arguments or {}).get("cursor"))
            elif name == "zendesk_list_custom_statuses": result = tools.list_custom_statuses((arguments or {}).get("limit", 100), cursor=(arguments or {}).get("cursor"))
            elif name == "zendesk_list_views": result = tools.list_views((arguments or {}).get("limit", 100), cursor=(arguments or {}).get("cursor"))
            elif name == "zendesk_get_view": result = tools.get_view((arguments or {}).get("view_id"))
            elif name == "zendesk_list_view_tickets": result = tools.list_view_tickets((arguments or {}).get("view_id"), (arguments or {}).get("limit", 100), cursor=(arguments or {}).get("cursor"))
            elif name == "zendesk_list_macros": result = tools.list_macros((arguments or {}).get("limit", 100), cursor=(arguments or {}).get("cursor"))
            else: result = tools.list_triggers((arguments or {}).get("limit", 100), cursor=(arguments or {}).get("cursor"))
        elif name in {
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
            "zendesk_get_git_zen_links",
            "zendesk_get_time_tracking",
            "zendesk_log_time",
            "zendesk_post_public_reply",
            "zendesk_post_internal_note",
        }:
            tools = build_ticket_tools(environment)
            if isinstance(tools, dict):
                result = tools
            elif name == "zendesk_list_tickets":
                limit = (arguments or {}).get("limit", 100)
                result = tools.list_tickets(limit, cursor=(arguments or {}).get("cursor"), sort=(arguments or {}).get("sort")) if isinstance(limit, int) and not isinstance(limit, bool) else failure(ErrorCode.VALIDATION_ERROR, "limit must be an integer")
            elif name == "zendesk_search_tickets":
                query = (arguments or {}).get("query")
                limit = (arguments or {}).get("limit", 100)
                result = tools.search_tickets(query, limit, projection=(arguments or {}).get("projection"), page=(arguments or {}).get("page", 1), cursor=(arguments or {}).get("cursor")) if isinstance(limit, int) and not isinstance(limit, bool) else failure(ErrorCode.VALIDATION_ERROR, "limit must be an integer")
            elif name == "zendesk_count_tickets":
                result = tools.count_tickets((arguments or {}).get("query"))
            elif name == "zendesk_export_tickets":
                values = arguments or {}; result = tools.export_tickets(values.get("query"), cursor=values.get("cursor"), limit=values.get("limit", 100), projection=values.get("projection"), output_format=values.get("format", "json"))
            elif name == "zendesk_preview_macro":
                values = arguments or {}; result = tools.apply_macro(values.get("ticket_id"), values.get("macro_id"))
            elif name == "zendesk_apply_ticket_macro":
                values = arguments or {}
                result = tools.apply_macro(values.get("ticket_id"), values.get("macro_id"), execution_mode=values.get("execution_mode", "preview"), approval_request_id=values.get("approval_request_id"), approval_token=values.get("approval_token"))
            elif name == "zendesk_create_ticket":
                values = arguments or {}
                result = tools.create_ticket(
                    requester_id=values.get("requester_id"),
                    subject=values.get("subject"),
                    description=values.get("description"),
                    tags=values.get("tags"),
                    priority=values.get("priority"),
                    ticket_type=values.get("ticket_type"),
                    assignee_id=values.get("assignee_id"),
                    group_id=values.get("group_id"),
                    organization_id=values.get("organization_id"),
                    custom_fields=values.get("custom_fields"),
                )
            elif name == "zendesk_update_ticket":
                values = arguments or {}
                result = tools.update_ticket(
                    values.get("ticket_id"),
                    subject=values.get("subject"),
                    status=values.get("status"),
                    priority=values.get("priority"),
                    ticket_type=values.get("ticket_type"),
                    requester_id=values.get("requester_id"),
                    assignee_id=values.get("assignee_id"),
                    group_id=values.get("group_id"),
                    organization_id=values.get("organization_id"),
                    tags=values.get("tags"),
                    custom_status_id=values.get("custom_status_id"),
                    due_at=values.get("due_at"),
                    custom_fields=values.get("custom_fields"),
                )
            elif name == "zendesk_set_ticket_status":
                values = arguments or {}
                result = tools.set_ticket_status(values.get("ticket_id"), values.get("status"))
            elif name == "zendesk_assign_ticket":
                values = arguments or {}
                result = tools.assign_ticket(
                    values.get("ticket_id"),
                    assignee_id=values.get("assignee_id"),
                    group_id=values.get("group_id"),
                )
            elif name == "zendesk_add_ticket_tag":
                values = arguments or {}
                result = tools.add_ticket_tag(values.get("ticket_id"), values.get("tag"))
            elif name == "zendesk_remove_ticket_tag":
                values = arguments or {}
                result = tools.remove_ticket_tag(values.get("ticket_id"), values.get("tag"))
            elif name == "zendesk_post_public_reply":
                values = arguments or {}
                result = tools.post_public_reply(
                    values.get("ticket_id"),
                    values.get("body"),
                    execution_mode=values.get("execution_mode", "preview"),
                    approval_request_id=values.get("approval_request_id"),
                    approval_token=values.get("approval_token"),
                )
            elif name == "zendesk_post_internal_note":
                values = arguments or {}
                result = tools.post_internal_note(values.get("ticket_id"), values.get("body"))
            elif name == "zendesk_list_ticket_attachments":
                result = tools.list_attachments((arguments or {}).get("ticket_id"))
            elif name == "zendesk_download_ticket_attachment":
                values = arguments or {}
                result = tools.download_attachment(values.get("ticket_id"), values.get("attachment_id"))
            elif name == "zendesk_inspect_ticket_attachment":
                values = arguments or {}
                result = tools.inspect_attachment(values.get("ticket_id"), values.get("attachment_id"))
            elif name == "zendesk_ticket_to_issue_context":
                result = tools.ticket_to_issue_context((arguments or {}).get("ticket_id"))
            elif name == "zendesk_get_git_zen_links":
                result = tools.get_git_zen_links((arguments or {}).get("ticket_id"))
            elif name == "zendesk_get_time_tracking":
                result = tools.get_time_tracking((arguments or {}).get("ticket_id"), limit=(arguments or {}).get("limit", 100), cursor=(arguments or {}).get("cursor"))
            elif name == "zendesk_log_time":
                values = arguments or {}; result = tools.log_time(values.get("ticket_id"), values.get("time_spent"), values.get("note"))
            else:
                ticket_id = (arguments or {}).get("ticket_id")
                if not isinstance(ticket_id, int) or isinstance(ticket_id, bool):
                    result = failure(ErrorCode.VALIDATION_ERROR, "ticket_id must be a positive integer")
                elif name == "zendesk_get_ticket":
                    result = tools.get_ticket(ticket_id)
                else:
                    result = tools.get_conversation(ticket_id, limit=(arguments or {}).get("limit", 100), cursor=(arguments or {}).get("cursor"))
        else:
            result = failure(ErrorCode.NOT_FOUND, f"Unknown tool: {name}")
        return respond(result)

    return server


async def main() -> None:
    server = create_server()
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            initialization_options=InitializationOptions(
                server_name="Zendesk",
                server_version="1.0.0",
                capabilities=server.get_capabilities(
                    notification_options=NotificationOptions(),
                    experimental_capabilities={},
                ),
            ),
        )


if __name__ == "__main__":
    asyncio.run(main())
