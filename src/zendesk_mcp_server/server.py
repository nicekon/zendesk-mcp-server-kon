"""MCP registration and lifecycle for the Zendesk server."""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Mapping

from mcp.server import InitializationOptions, NotificationOptions, Server, types
from mcp.server.stdio import stdio_server

from .approvals import ApprovalStore
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


def build_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="zendesk_get_connection_status",
            description="Report Zendesk configuration without exposing credentials or making a network request.",
            inputSchema={"type": "object", "properties": {}},
        ),
        types.Tool(
            name="zendesk_list_tickets",
            description="List Zendesk tickets without making changes.",
            inputSchema={"type": "object", "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 100}}},
        ),
        types.Tool(
            name="zendesk_search_tickets",
            description="Search Zendesk Support tickets without making changes.",
            inputSchema={"type": "object", "properties": {"query": {"type": "string", "minLength": 1}, "limit": {"type": "integer", "minimum": 1, "maximum": 100}}, "required": ["query"]},
        ),
        types.Tool(
            name="zendesk_count_tickets",
            description="Count Zendesk Support tickets matching a search query without making changes.",
            inputSchema={"type": "object", "properties": {"query": {"type": "string", "minLength": 1}}, "required": ["query"]},
        ),
        types.Tool(name="zendesk_export_tickets", description="Start a ticket-only Zendesk Search Export without making changes.", inputSchema={"type": "object", "properties": {"query": {"type": "string", "minLength": 1}}, "required": ["query"]}),
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
            description="Retrieve a ticket conversation without making changes.",
            inputSchema={"type": "object", "properties": {"ticket_id": {"type": "integer", "minimum": 1}}, "required": ["ticket_id"]},
        ),
        types.Tool(name="zendesk_list_ticket_attachments", description="List attachment metadata for a Zendesk ticket without downloading content.", inputSchema={"type": "object", "properties": {"ticket_id": {"type": "integer", "minimum": 1}}, "required": ["ticket_id"]}),
        types.Tool(name="zendesk_ticket_to_issue_context", description="Convert a ticket and its plain-text conversation into Markdown issue context.", inputSchema={"type": "object", "properties": {"ticket_id": {"type": "integer", "minimum": 1}}, "required": ["ticket_id"]}),
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
        types.Tool(name="zendesk_search_users", description="Search Zendesk users without making changes.", inputSchema={"type": "object", "properties": {"query": {"type": "string", "minLength": 1}}, "required": ["query"]}),
        types.Tool(name="zendesk_list_groups", description="List Zendesk groups without making changes.", inputSchema={"type": "object", "properties": {}}),
        types.Tool(name="zendesk_list_group_users", description="List users in a Zendesk group without making changes.", inputSchema={"type": "object", "properties": {"group_id": {"type": "integer", "minimum": 1}}, "required": ["group_id"]}),
        types.Tool(name="zendesk_get_organization", description="Get a Zendesk organization without making changes.", inputSchema={"type": "object", "properties": {"organization_id": {"type": "integer", "minimum": 1}}, "required": ["organization_id"]}),
        types.Tool(name="zendesk_list_brands", description="List Zendesk brands without making changes.", inputSchema={"type": "object", "properties": {}}),
        types.Tool(name="zendesk_list_ticket_fields", description="List Zendesk ticket fields without making changes.", inputSchema={"type": "object", "properties": {}}),
        types.Tool(name="zendesk_list_ticket_forms", description="List Zendesk ticket forms without making changes.", inputSchema={"type": "object", "properties": {}}),
        types.Tool(name="zendesk_list_custom_statuses", description="List Zendesk custom statuses without making changes.", inputSchema={"type": "object", "properties": {}}),
        types.Tool(name="zendesk_list_views", description="List Zendesk views without making changes.", inputSchema={"type": "object", "properties": {}}),
        types.Tool(name="zendesk_get_view", description="Get a Zendesk view without making changes.", inputSchema={"type": "object", "properties": {"view_id": {"type": "integer", "minimum": 1}}, "required": ["view_id"]}),
        types.Tool(name="zendesk_list_view_tickets", description="List tickets currently matching a Zendesk view without making changes.", inputSchema={"type": "object", "properties": {"view_id": {"type": "integer", "minimum": 1}}, "required": ["view_id"]}),
        types.Tool(name="zendesk_list_macros", description="List Zendesk macros without making changes.", inputSchema={"type": "object", "properties": {}}),
        types.Tool(name="zendesk_list_triggers", description="List Zendesk triggers without making changes.", inputSchema={"type": "object", "properties": {}}),
        types.Tool(name="zendesk_list_help_center_categories", description="List Help Center categories without making changes.", inputSchema={"type": "object", "properties": {}}),
        types.Tool(name="zendesk_list_help_center_sections", description="List Help Center sections without making changes.", inputSchema={"type": "object", "properties": {}}),
        types.Tool(name="zendesk_search_help_center_articles", description="Search Help Center articles without making changes.", inputSchema={"type": "object", "properties": {"query": {"type": "string", "minLength": 1}}, "required": ["query"]}),
        types.Tool(name="zendesk_get_help_center_article", description="Get a Help Center article without making changes.", inputSchema={"type": "object", "properties": {"article_id": {"type": "integer", "minimum": 1}}, "required": ["article_id"]}),
        types.Tool(name="zendesk_get_satisfaction_ratings", description="List Zendesk satisfaction ratings without making changes.", inputSchema={"type": "object", "properties": {}}),
        types.Tool(name="zendesk_list_guide_permission_groups", description="List Guide management permission groups without making changes.", inputSchema={"type": "object", "properties": {}}),
        types.Tool(name="zendesk_list_guide_user_segments", description="List Guide user segments without making changes.", inputSchema={"type": "object", "properties": {"built_in": {"type": "boolean"}, "applicable": {"type": "boolean", "default": False}}}),
        types.Tool(name="zendesk_list_community_posts", description="List Community posts without making changes.", inputSchema={"type": "object", "properties": {}}),
        types.Tool(name="zendesk_search_community_posts", description="Search Community posts without making changes.", inputSchema={"type": "object", "properties": {"query": {"type": "string", "minLength": 1}}, "required": ["query"]}),
        types.Tool(name="zendesk_get_community_post", description="Get a Community post without making changes.", inputSchema={"type": "object", "properties": {"post_id": {"type": "integer", "minimum": 1}}, "required": ["post_id"]}),
        types.Tool(name="zendesk_create_community_post", description="Preview or create a public Community post. Apply requires public-write gate and local approval.", inputSchema={"type": "object", "properties": {"topic_id": {"type": "integer", "minimum": 1}, "title": {"type": "string", "minLength": 1}, "details": {"type": "string", "minLength": 1}, "execution_mode": {"type": "string", "enum": ["preview", "apply"], "default": "preview"}, "approval_request_id": {"type": "string"}, "approval_token": {"type": "string"}}, "required": ["topic_id", "title", "details"]}),
        types.Tool(name="zendesk_update_community_post", description="Preview or update public Community post fields. Apply requires public-write gate and local approval.", inputSchema={"type": "object", "properties": {"post_id": {"type": "integer", "minimum": 1}, "title": {"type": "string", "minLength": 1}, "details": {"type": "string"}, "topic_id": {"type": "integer", "minimum": 1}, "status": {"type": "string", "enum": ["planned", "not_planned", "answered", "completed"]}, "closed": {"type": "boolean"}, "featured": {"type": "boolean"}, "pinned": {"type": "boolean"}, "content_tag_ids": {"type": "array", "items": {"type": "integer", "minimum": 1}}, "execution_mode": {"type": "string", "enum": ["preview", "apply"], "default": "preview"}, "approval_request_id": {"type": "string"}, "approval_token": {"type": "string"}}, "required": ["post_id"]}),
        types.Tool(name="zendesk_delete_community_post", description="Preview or delete a Community post. Apply requires destructive-write gate and local approval.", inputSchema={"type": "object", "properties": {"post_id": {"type": "integer", "minimum": 1}, "execution_mode": {"type": "string", "enum": ["preview", "apply"], "default": "preview"}, "approval_request_id": {"type": "string"}, "approval_token": {"type": "string"}}, "required": ["post_id"]}),
        types.Tool(name="zendesk_create_community_comment", description="Preview or create a public Community comment. Apply requires public-write gate and local approval.", inputSchema={"type": "object", "properties": {"post_id": {"type": "integer", "minimum": 1}, "body": {"type": "string", "minLength": 1}, "execution_mode": {"type": "string", "enum": ["preview", "apply"], "default": "preview"}, "approval_request_id": {"type": "string"}, "approval_token": {"type": "string"}}, "required": ["post_id", "body"]}),
        types.Tool(name="zendesk_update_community_comment", description="Preview or update a public Community comment. Apply requires public-write gate and local approval.", inputSchema={"type": "object", "properties": {"post_id": {"type": "integer", "minimum": 1}, "comment_id": {"type": "integer", "minimum": 1}, "body": {"type": "string", "minLength": 1}, "official": {"type": "boolean"}, "execution_mode": {"type": "string", "enum": ["preview", "apply"], "default": "preview"}, "approval_request_id": {"type": "string"}, "approval_token": {"type": "string"}}, "required": ["post_id", "comment_id"]}),
        types.Tool(name="zendesk_delete_community_comment", description="Preview or delete a Community comment. Apply requires destructive-write gate and local approval.", inputSchema={"type": "object", "properties": {"post_id": {"type": "integer", "minimum": 1}, "comment_id": {"type": "integer", "minimum": 1}, "execution_mode": {"type": "string", "enum": ["preview", "apply"], "default": "preview"}, "approval_request_id": {"type": "string"}, "approval_token": {"type": "string"}}, "required": ["post_id", "comment_id"]}),
        types.Tool(name="zendesk_create_community_topic", description="Preview or create a public Community topic. Apply requires public-write gate and local approval.", inputSchema={"type": "object", "properties": {"name": {"type": "string", "minLength": 1}, "description": {"type": "string"}, "execution_mode": {"type": "string", "enum": ["preview", "apply"], "default": "preview"}, "approval_request_id": {"type": "string"}, "approval_token": {"type": "string"}}, "required": ["name", "description"]}),
        types.Tool(name="zendesk_update_community_topic", description="Preview or update a public Community topic. Apply requires public-write gate and local approval.", inputSchema={"type": "object", "properties": {"topic_id": {"type": "integer", "minimum": 1}, "name": {"type": "string", "minLength": 1}, "description": {"type": "string"}, "manageable_by": {"type": "string", "enum": ["staff", "managers"]}, "position": {"type": "integer", "minimum": 0}, "user_segment_id": {"type": "integer", "minimum": 1}, "execution_mode": {"type": "string", "enum": ["preview", "apply"], "default": "preview"}, "approval_request_id": {"type": "string"}, "approval_token": {"type": "string"}}, "required": ["topic_id"]}),
        types.Tool(name="zendesk_delete_community_topic", description="Preview or delete a Community topic. Apply requires destructive-write gate and local approval.", inputSchema={"type": "object", "properties": {"topic_id": {"type": "integer", "minimum": 1}, "execution_mode": {"type": "string", "enum": ["preview", "apply"], "default": "preview"}, "approval_request_id": {"type": "string"}, "approval_token": {"type": "string"}}, "required": ["topic_id"]}),
        types.Tool(name="zendesk_list_community_votes", description="List votes for a Community post without making changes.", inputSchema={"type": "object", "properties": {"post_id": {"type": "integer", "minimum": 1}}, "required": ["post_id"]}),
        types.Tool(name="zendesk_get_community_vote", description="Get a Community vote without making changes.", inputSchema={"type": "object", "properties": {"vote_id": {"type": "integer", "minimum": 1}}, "required": ["vote_id"]}),
        types.Tool(name="zendesk_upvote_community_content", description="Preview or upvote a Community post or comment. Apply requires public-write gate and local approval.", inputSchema={"type": "object", "properties": {"content_type": {"type": "string", "enum": ["post", "post_comment"]}, "post_id": {"type": "integer", "minimum": 1}, "comment_id": {"type": "integer", "minimum": 1}, "execution_mode": {"type": "string", "enum": ["preview", "apply"], "default": "preview"}, "approval_request_id": {"type": "string"}, "approval_token": {"type": "string"}}, "required": ["content_type", "post_id"]}),
        types.Tool(name="zendesk_downvote_community_content", description="Preview or downvote a Community post or comment. Apply requires public-write gate and local approval.", inputSchema={"type": "object", "properties": {"content_type": {"type": "string", "enum": ["post", "post_comment"]}, "post_id": {"type": "integer", "minimum": 1}, "comment_id": {"type": "integer", "minimum": 1}, "execution_mode": {"type": "string", "enum": ["preview", "apply"], "default": "preview"}, "approval_request_id": {"type": "string"}, "approval_token": {"type": "string"}}, "required": ["content_type", "post_id"]}),
        types.Tool(name="zendesk_remove_community_vote", description="Preview or remove a Community vote. Apply requires destructive-write gate and local approval.", inputSchema={"type": "object", "properties": {"vote_id": {"type": "integer", "minimum": 1}, "execution_mode": {"type": "string", "enum": ["preview", "apply"], "default": "preview"}, "approval_request_id": {"type": "string"}, "approval_token": {"type": "string"}}, "required": ["vote_id"]}),
        types.Tool(name="zendesk_list_content_subscriptions", description="List subscriptions for a Community post or topic without making changes.", inputSchema={"type": "object", "properties": {"content_type": {"type": "string", "enum": ["post", "topic"]}, "content_id": {"type": "integer", "minimum": 1}}, "required": ["content_type", "content_id"]}),
        types.Tool(name="zendesk_get_content_subscription", description="Get a Community content subscription without making changes.", inputSchema={"type": "object", "properties": {"content_type": {"type": "string", "enum": ["post", "topic"]}, "content_id": {"type": "integer", "minimum": 1}, "subscription_id": {"type": "integer", "minimum": 1}}, "required": ["content_type", "content_id", "subscription_id"]}),
        types.Tool(name="zendesk_create_content_subscription", description="Preview or create a Community content subscription. Apply requires public-write gate and local approval.", inputSchema={"type": "object", "properties": {"content_type": {"type": "string", "enum": ["post", "topic"]}, "content_id": {"type": "integer", "minimum": 1}, "include_comments": {"type": "boolean", "default": False}, "execution_mode": {"type": "string", "enum": ["preview", "apply"], "default": "preview"}, "approval_request_id": {"type": "string"}, "approval_token": {"type": "string"}}, "required": ["content_type", "content_id"]}),
        types.Tool(name="zendesk_update_content_subscription", description="Preview or update a topic subscription's comment setting. Apply requires public-write gate and local approval.", inputSchema={"type": "object", "properties": {"topic_id": {"type": "integer", "minimum": 1}, "subscription_id": {"type": "integer", "minimum": 1}, "include_comments": {"type": "boolean"}, "execution_mode": {"type": "string", "enum": ["preview", "apply"], "default": "preview"}, "approval_request_id": {"type": "string"}, "approval_token": {"type": "string"}}, "required": ["topic_id", "subscription_id", "include_comments"]}),
        types.Tool(name="zendesk_delete_content_subscription", description="Preview or delete a Community content subscription. Apply requires destructive-write gate and local approval.", inputSchema={"type": "object", "properties": {"content_type": {"type": "string", "enum": ["post", "topic"]}, "content_id": {"type": "integer", "minimum": 1}, "subscription_id": {"type": "integer", "minimum": 1}, "execution_mode": {"type": "string", "enum": ["preview", "apply"], "default": "preview"}, "approval_request_id": {"type": "string"}, "approval_token": {"type": "string"}}, "required": ["content_type", "content_id", "subscription_id"]}),
        types.Tool(name="zendesk_list_community_comments", description="List comments for a Community post without making changes.", inputSchema={"type": "object", "properties": {"post_id": {"type": "integer", "minimum": 1}}, "required": ["post_id"]}),
        types.Tool(name="zendesk_get_community_comment", description="Get a Community comment without making changes.", inputSchema={"type": "object", "properties": {"comment_id": {"type": "integer", "minimum": 1}}, "required": ["comment_id"]}),
        types.Tool(name="zendesk_list_community_topics", description="List Community topics without making changes.", inputSchema={"type": "object", "properties": {}}),
        types.Tool(name="zendesk_get_community_topic", description="Get a Community topic without making changes.", inputSchema={"type": "object", "properties": {"topic_id": {"type": "integer", "minimum": 1}}, "required": ["topic_id"]}),
        types.Tool(name="zendesk_search_content_tags", description="Search Community content tags without making changes.", inputSchema={"type": "object", "properties": {"prefix": {"type": "string", "default": ""}}}),
        types.Tool(name="zendesk_count_content_tags", description="Count Community content tags without making changes.", inputSchema={"type": "object", "properties": {}}),
        types.Tool(name="zendesk_get_content_tag", description="Get a Community content tag without making changes.", inputSchema={"type": "object", "properties": {"tag_id": {"type": "string", "minLength": 1}}, "required": ["tag_id"]}),
        types.Tool(name="zendesk_create_content_tag", description="Preview or create a Community content tag. Apply requires public-write gate and local approval.", inputSchema={"type": "object", "properties": {"name": {"type": "string", "minLength": 1}, "execution_mode": {"type": "string", "enum": ["preview", "apply"], "default": "preview"}, "approval_request_id": {"type": "string"}, "approval_token": {"type": "string"}}, "required": ["name"]}),
        types.Tool(name="zendesk_update_content_tag", description="Preview or update a Community content tag. Apply requires public-write gate and local approval.", inputSchema={"type": "object", "properties": {"tag_id": {"type": "string", "minLength": 1}, "name": {"type": "string", "minLength": 1}, "execution_mode": {"type": "string", "enum": ["preview", "apply"], "default": "preview"}, "approval_request_id": {"type": "string"}, "approval_token": {"type": "string"}}, "required": ["tag_id", "name"]}),
        types.Tool(name="zendesk_delete_content_tag", description="Preview or delete a Community content tag. Apply requires destructive-write gate and local approval.", inputSchema={"type": "object", "properties": {"tag_id": {"type": "string", "minLength": 1}, "execution_mode": {"type": "string", "enum": ["preview", "apply"], "default": "preview"}, "approval_request_id": {"type": "string"}, "approval_token": {"type": "string"}}, "required": ["tag_id"]}),
        types.Tool(name="zendesk_list_user_subscriptions", description="List Community user subscriptions without making changes.", inputSchema={"type": "object", "properties": {"user_id": {"oneOf": [{"type": "integer", "minimum": 1}, {"type": "string", "enum": ["me"]}]}, "direction": {"type": "string", "enum": ["followers", "followings"], "default": "followers"}}, "required": ["user_id"]}),
        types.Tool(name="zendesk_upsert_user_subscription", description="Preview or create/update a Community user subscription. Self-subscription needs public-write approval; another user also needs impersonation gate.", inputSchema={"type": "object", "properties": {"user_id": {"oneOf": [{"type": "integer", "minimum": 1}, {"type": "string", "enum": ["me"]}]}, "followed_id": {"type": "integer", "minimum": 1}, "include_comments": {"type": "boolean", "default": False}, "execution_mode": {"type": "string", "enum": ["preview", "apply"], "default": "preview"}, "approval_request_id": {"type": "string"}, "approval_token": {"type": "string"}}, "required": ["user_id", "followed_id"]}),
        types.Tool(name="zendesk_delete_user_subscription", description="Preview or delete a Community user subscription. Self-delete needs destructive approval; another user also needs impersonation gate.", inputSchema={"type": "object", "properties": {"user_id": {"oneOf": [{"type": "integer", "minimum": 1}, {"type": "string", "enum": ["me"]}]}, "subscription_id": {"type": "integer", "minimum": 1}, "execution_mode": {"type": "string", "enum": ["preview", "apply"], "default": "preview"}, "approval_request_id": {"type": "string"}, "approval_token": {"type": "string"}}, "required": ["user_id", "subscription_id"]}),
        types.Tool(name="zendesk_list_badge_categories", description="List Gather badge categories without making changes.", inputSchema={"type": "object", "properties": {"brand_id": {"type": "integer", "minimum": 1}}}),
        types.Tool(name="zendesk_get_badge_category", description="Get a Gather badge category without making changes.", inputSchema={"type": "object", "properties": {"category_id": {"type": "string", "minLength": 1}}, "required": ["category_id"]}),
        types.Tool(name="zendesk_create_badge_category", description="Preview or create a Gather badge category. Apply requires public-write gate and local approval.", inputSchema={"type": "object", "properties": {"brand_id": {"type": "integer", "minimum": 1}, "name": {"type": "string", "minLength": 1}, "slug": {"type": "string", "minLength": 1}, "execution_mode": {"type": "string", "enum": ["preview", "apply"], "default": "preview"}, "approval_request_id": {"type": "string"}, "approval_token": {"type": "string"}}, "required": ["brand_id", "name", "slug"]}),
        types.Tool(name="zendesk_delete_badge_category", description="Preview or delete an empty Gather badge category. Apply requires destructive-write gate and local approval.", inputSchema={"type": "object", "properties": {"category_id": {"type": "string", "minLength": 1}, "execution_mode": {"type": "string", "enum": ["preview", "apply"], "default": "preview"}, "approval_request_id": {"type": "string"}, "approval_token": {"type": "string"}}, "required": ["category_id"]}),
        types.Tool(name="zendesk_list_badges", description="List Gather badges without making changes.", inputSchema={"type": "object", "properties": {"brand_id": {"type": "integer", "minimum": 1}}}),
        types.Tool(name="zendesk_get_badge", description="Get a Gather badge without making changes.", inputSchema={"type": "object", "properties": {"badge_id": {"type": "string", "minLength": 1}}, "required": ["badge_id"]}),
        types.Tool(name="zendesk_create_badge", description="Preview or create a Gather badge. Apply requires public-write gate and local approval.", inputSchema={"type": "object", "properties": {"badge_category_id": {"type": "string", "minLength": 1}, "name": {"type": "string", "minLength": 1}, "description": {"type": "string"}, "icon_upload_id": {"type": "string", "minLength": 1}, "execution_mode": {"type": "string", "enum": ["preview", "apply"], "default": "preview"}, "approval_request_id": {"type": "string"}, "approval_token": {"type": "string"}}, "required": ["badge_category_id", "name", "description"]}),
        types.Tool(name="zendesk_update_badge", description="Preview or update a Gather badge. Apply requires public-write gate and local approval.", inputSchema={"type": "object", "properties": {"badge_id": {"type": "string", "minLength": 1}, "badge_category_id": {"type": "string", "minLength": 1}, "name": {"type": "string", "minLength": 1}, "description": {"type": "string"}, "icon_upload_id": {"type": ["string", "null"], "minLength": 1}, "execution_mode": {"type": "string", "enum": ["preview", "apply"], "default": "preview"}, "approval_request_id": {"type": "string"}, "approval_token": {"type": "string"}}, "required": ["badge_id"]}),
        types.Tool(name="zendesk_delete_badge", description="Preview or delete a Gather badge and its assignments. Apply requires destructive-write gate and local approval.", inputSchema={"type": "object", "properties": {"badge_id": {"type": "string", "minLength": 1}, "execution_mode": {"type": "string", "enum": ["preview", "apply"], "default": "preview"}, "approval_request_id": {"type": "string"}, "approval_token": {"type": "string"}}, "required": ["badge_id"]}),
        types.Tool(name="zendesk_list_badge_assignments", description="List Gather badge assignments without making changes.", inputSchema={"type": "object", "properties": {"user_id": {"type": "integer", "minimum": 1}, "badge_id": {"type": "string", "minLength": 1}, "badge_category_id": {"type": "string", "minLength": 1}, "brand_id": {"type": "integer", "minimum": 1}}}),
        types.Tool(name="zendesk_create_badge_assignment", description="Preview or assign a Gather badge to a user. Apply requires public-write and impersonation gates plus local approval.", inputSchema={"type": "object", "properties": {"badge_id": {"type": "string", "minLength": 1}, "user_id": {"type": "integer", "minimum": 1}, "execution_mode": {"type": "string", "enum": ["preview", "apply"], "default": "preview"}, "approval_request_id": {"type": "string"}, "approval_token": {"type": "string"}}, "required": ["badge_id", "user_id"]}),
        types.Tool(name="zendesk_delete_badge_assignment", description="Preview or remove a Gather badge assignment. Apply requires destructive and impersonation gates plus local approval.", inputSchema={"type": "object", "properties": {"assignment_id": {"type": "string", "minLength": 1}, "execution_mode": {"type": "string", "enum": ["preview", "apply"], "default": "preview"}, "approval_request_id": {"type": "string"}, "approval_token": {"type": "string"}}, "required": ["assignment_id"]}),
        types.Tool(name="zendesk_upload_community_user_image", description="Preview or safely upload a Community user image from ZENDESK_UPLOAD_ROOT. Apply requires external-upload gate and local approval.", inputSchema={"type": "object", "properties": {"image_path": {"type": "string", "minLength": 1}, "content_type": {"type": "string", "enum": ["image/jpeg", "image/png", "image/gif"]}, "brand_id": {"type": "integer", "minimum": 1}, "execution_mode": {"type": "string", "enum": ["preview", "apply"], "default": "preview"}, "approval_request_id": {"type": "string"}, "approval_token": {"type": "string"}}, "required": ["image_path", "content_type", "brand_id"]}),
        types.Tool(name="zendesk_upload_badge_icon", description="Preview or safely upload a Gather badge icon from ZENDESK_UPLOAD_ROOT. Apply requires external-upload gate and local approval.", inputSchema={"type": "object", "properties": {"image_path": {"type": "string", "minLength": 1}, "content_type": {"type": "string", "enum": ["image/svg+xml", "image/jpeg", "image/png", "image/gif"]}, "execution_mode": {"type": "string", "enum": ["preview", "apply"], "default": "preview"}, "approval_request_id": {"type": "string"}, "approval_token": {"type": "string"}}, "required": ["image_path", "content_type"]}),
    ]


def build_connection_status(environ: Mapping[str, str]) -> dict[str, object]:
    try:
        return success(Settings.load(environ).connection_status())
    except ConfigurationError as error:
        return failure(ErrorCode.VALIDATION_ERROR, str(error))


def build_ticket_tools(environ: Mapping[str, str]) -> TicketTools | dict[str, object]:
    try:
        settings = Settings.load(environ)
        authorization = build_authorization(settings)
    except ConfigurationError as error:
        return failure(ErrorCode.VALIDATION_ERROR, str(error))
    if authorization is None:
        return failure(ErrorCode.NOT_CONFIGURED, "Zendesk is not configured")
    return TicketTools(ZendeskClient(settings, authorization), settings, ApprovalStore.from_environment(environ))


def build_metadata_tools(environ: Mapping[str, str]) -> MetadataTools | dict[str, object]:
    try:
        settings = Settings.load(environ)
        authorization = build_authorization(settings)
    except ConfigurationError as error:
        return failure(ErrorCode.VALIDATION_ERROR, str(error))
    if authorization is None:
        return failure(ErrorCode.NOT_CONFIGURED, "Zendesk is not configured")
    return MetadataTools(ZendeskClient(settings, authorization))


def build_guide_tools(environ: Mapping[str, str]) -> GuideTools | dict[str, object]:
    try:
        settings = Settings.load(environ)
        authorization = build_authorization(settings)
    except ConfigurationError as error:
        return failure(ErrorCode.VALIDATION_ERROR, str(error))
    if authorization is None:
        return failure(ErrorCode.NOT_CONFIGURED, "Zendesk is not configured")
    return GuideTools(ZendeskClient(settings, authorization))


def build_community_tools(environ: Mapping[str, str]) -> CommunityTools | dict[str, object]:
    try:
        settings = Settings.load(environ); authorization = build_authorization(settings)
    except ConfigurationError as error: return failure(ErrorCode.VALIDATION_ERROR, str(error))
    if authorization is None: return failure(ErrorCode.NOT_CONFIGURED, "Zendesk is not configured")
    return CommunityTools(ZendeskClient(settings, authorization), settings, ApprovalStore.from_environment(environ))


def create_server(environ: Mapping[str, str] | None = None) -> Server:
    environment = dict(os.environ) if environ is None else dict(environ)
    server = Server("Zendesk")

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
    ) -> list[types.TextContent]:
        if name == "zendesk_get_connection_status":
            result = build_connection_status(environment)
        elif name in {"zendesk_list_community_posts", "zendesk_search_community_posts", "zendesk_get_community_post", "zendesk_create_community_post", "zendesk_update_community_post", "zendesk_delete_community_post", "zendesk_create_community_comment", "zendesk_update_community_comment", "zendesk_delete_community_comment", "zendesk_create_community_topic", "zendesk_update_community_topic", "zendesk_delete_community_topic", "zendesk_list_community_votes", "zendesk_get_community_vote", "zendesk_upvote_community_content", "zendesk_downvote_community_content", "zendesk_remove_community_vote", "zendesk_list_content_subscriptions", "zendesk_get_content_subscription", "zendesk_create_content_subscription", "zendesk_update_content_subscription", "zendesk_delete_content_subscription", "zendesk_list_community_comments", "zendesk_get_community_comment", "zendesk_list_community_topics", "zendesk_get_community_topic", "zendesk_search_content_tags", "zendesk_count_content_tags", "zendesk_get_content_tag", "zendesk_create_content_tag", "zendesk_update_content_tag", "zendesk_delete_content_tag", "zendesk_list_user_subscriptions", "zendesk_upsert_user_subscription", "zendesk_delete_user_subscription", "zendesk_list_badge_categories", "zendesk_get_badge_category", "zendesk_create_badge_category", "zendesk_delete_badge_category", "zendesk_list_badges", "zendesk_get_badge", "zendesk_create_badge", "zendesk_update_badge", "zendesk_delete_badge", "zendesk_list_badge_assignments", "zendesk_create_badge_assignment", "zendesk_delete_badge_assignment", "zendesk_upload_community_user_image", "zendesk_upload_badge_icon"}:
            tools = build_community_tools(environment)
            if isinstance(tools, dict): result = tools
            elif name == "zendesk_list_community_posts": result = tools.list_posts()
            elif name == "zendesk_search_community_posts": result = tools.search_posts((arguments or {}).get("query"))
            elif name == "zendesk_create_community_post":
                values = arguments or {}; result = tools.create_post(values.get("topic_id"), values.get("title"), values.get("details"), execution_mode=values.get("execution_mode", "preview"), approval_request_id=values.get("approval_request_id"), approval_token=values.get("approval_token"))
            elif name == "zendesk_update_community_post":
                values = arguments or {}; post = {field: values[field] for field in {"title", "details", "topic_id", "status", "closed", "featured", "pinned", "content_tag_ids"} if field in values}; result = tools.update_post(values.get("post_id"), post, execution_mode=values.get("execution_mode", "preview"), approval_request_id=values.get("approval_request_id"), approval_token=values.get("approval_token"))
            elif name == "zendesk_delete_community_post":
                values = arguments or {}; result = tools.delete_post(values.get("post_id"), execution_mode=values.get("execution_mode", "preview"), approval_request_id=values.get("approval_request_id"), approval_token=values.get("approval_token"))
            elif name == "zendesk_create_community_comment":
                values = arguments or {}; result = tools.create_comment(values.get("post_id"), values.get("body"), execution_mode=values.get("execution_mode", "preview"), approval_request_id=values.get("approval_request_id"), approval_token=values.get("approval_token"))
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
            elif name == "zendesk_list_community_votes": result = tools.list_votes((arguments or {}).get("post_id"))
            elif name == "zendesk_get_community_vote": result = tools.get_vote((arguments or {}).get("vote_id"))
            elif name in {"zendesk_upvote_community_content", "zendesk_downvote_community_content"}:
                values = arguments or {}; result = tools.cast_vote(values.get("content_type"), values.get("post_id"), values.get("comment_id"), "up" if name == "zendesk_upvote_community_content" else "down", execution_mode=values.get("execution_mode", "preview"), approval_request_id=values.get("approval_request_id"), approval_token=values.get("approval_token"))
            elif name == "zendesk_remove_community_vote":
                values = arguments or {}; result = tools.delete_vote(values.get("vote_id"), execution_mode=values.get("execution_mode", "preview"), approval_request_id=values.get("approval_request_id"), approval_token=values.get("approval_token"))
            elif name == "zendesk_list_content_subscriptions":
                values = arguments or {}
                if values.get("content_type") == "post": result = tools.list_post_subscriptions(values.get("content_id"))
                elif values.get("content_type") == "topic": result = tools.list_topic_subscriptions(values.get("content_id"))
                else: result = failure(ErrorCode.VALIDATION_ERROR, "content_type must be post or topic")
            elif name == "zendesk_get_content_subscription":
                values = arguments or {}; result = tools.get_subscription(values.get("content_type"), values.get("content_id"), values.get("subscription_id"))
            elif name == "zendesk_create_content_subscription":
                values = arguments or {}; result = tools.create_subscription(values.get("content_type"), values.get("content_id"), include_comments=values.get("include_comments", False), execution_mode=values.get("execution_mode", "preview"), approval_request_id=values.get("approval_request_id"), approval_token=values.get("approval_token"))
            elif name == "zendesk_update_content_subscription":
                values = arguments or {}; result = tools.update_subscription(values.get("topic_id"), values.get("subscription_id"), include_comments=values.get("include_comments"), execution_mode=values.get("execution_mode", "preview"), approval_request_id=values.get("approval_request_id"), approval_token=values.get("approval_token"))
            elif name == "zendesk_delete_content_subscription":
                values = arguments or {}; result = tools.delete_subscription(values.get("content_type"), values.get("content_id"), values.get("subscription_id"), execution_mode=values.get("execution_mode", "preview"), approval_request_id=values.get("approval_request_id"), approval_token=values.get("approval_token"))
            elif name == "zendesk_list_community_comments": result = tools.list_comments((arguments or {}).get("post_id"))
            elif name == "zendesk_get_community_comment": result = tools.get_comment((arguments or {}).get("comment_id"))
            elif name == "zendesk_list_community_topics": result = tools.list_topics()
            elif name == "zendesk_get_community_topic": result = tools.get_topic((arguments or {}).get("topic_id"))
            elif name == "zendesk_search_content_tags": result = tools.search_content_tags((arguments or {}).get("prefix", ""))
            elif name == "zendesk_count_content_tags": result = tools.count_content_tags()
            elif name == "zendesk_get_content_tag": result = tools.get_content_tag((arguments or {}).get("tag_id"))
            elif name == "zendesk_create_content_tag":
                values = arguments or {}; result = tools.create_content_tag(values.get("name"), execution_mode=values.get("execution_mode", "preview"), approval_request_id=values.get("approval_request_id"), approval_token=values.get("approval_token"))
            elif name == "zendesk_update_content_tag":
                values = arguments or {}; result = tools.update_content_tag(values.get("tag_id"), values.get("name"), execution_mode=values.get("execution_mode", "preview"), approval_request_id=values.get("approval_request_id"), approval_token=values.get("approval_token"))
            elif name == "zendesk_delete_content_tag":
                values = arguments or {}; result = tools.delete_content_tag(values.get("tag_id"), execution_mode=values.get("execution_mode", "preview"), approval_request_id=values.get("approval_request_id"), approval_token=values.get("approval_token"))
            elif name == "zendesk_list_user_subscriptions":
                values = arguments or {}; result = tools.list_user_subscriptions(values.get("user_id"), values.get("direction", "followers"))
            elif name == "zendesk_upsert_user_subscription":
                values = arguments or {}; result = tools.upsert_user_subscription(values.get("user_id"), values.get("followed_id"), include_comments=values.get("include_comments", False), execution_mode=values.get("execution_mode", "preview"), approval_request_id=values.get("approval_request_id"), approval_token=values.get("approval_token"))
            elif name == "zendesk_delete_user_subscription":
                values = arguments or {}; result = tools.delete_user_subscription(values.get("user_id"), values.get("subscription_id"), execution_mode=values.get("execution_mode", "preview"), approval_request_id=values.get("approval_request_id"), approval_token=values.get("approval_token"))
            elif name == "zendesk_list_badge_categories": result = tools.list_badge_categories((arguments or {}).get("brand_id"))
            elif name == "zendesk_get_badge_category": result = tools.get_badge_category((arguments or {}).get("category_id"))
            elif name == "zendesk_create_badge_category":
                values = arguments or {}; result = tools.create_badge_category(values.get("brand_id"), values.get("name"), values.get("slug"), execution_mode=values.get("execution_mode", "preview"), approval_request_id=values.get("approval_request_id"), approval_token=values.get("approval_token"))
            elif name == "zendesk_delete_badge_category":
                values = arguments or {}; result = tools.delete_badge_category(values.get("category_id"), execution_mode=values.get("execution_mode", "preview"), approval_request_id=values.get("approval_request_id"), approval_token=values.get("approval_token"))
            elif name == "zendesk_list_badges": result = tools.list_badges((arguments or {}).get("brand_id"))
            elif name == "zendesk_get_badge": result = tools.get_badge((arguments or {}).get("badge_id"))
            elif name == "zendesk_create_badge":
                values = arguments or {}; result = tools.create_badge(values.get("badge_category_id"), values.get("name"), values.get("description"), icon_upload_id=values.get("icon_upload_id"), execution_mode=values.get("execution_mode", "preview"), approval_request_id=values.get("approval_request_id"), approval_token=values.get("approval_token"))
            elif name == "zendesk_update_badge":
                values = arguments or {}; badge = {field: values[field] for field in {"badge_category_id", "name", "description", "icon_upload_id"} if field in values}; result = tools.update_badge(values.get("badge_id"), badge, execution_mode=values.get("execution_mode", "preview"), approval_request_id=values.get("approval_request_id"), approval_token=values.get("approval_token"))
            elif name == "zendesk_delete_badge":
                values = arguments or {}; result = tools.delete_badge(values.get("badge_id"), execution_mode=values.get("execution_mode", "preview"), approval_request_id=values.get("approval_request_id"), approval_token=values.get("approval_token"))
            elif name == "zendesk_list_badge_assignments":
                values = arguments or {}; result = tools.list_badge_assignments(user_id=values.get("user_id"), badge_id=values.get("badge_id"), badge_category_id=values.get("badge_category_id"), brand_id=values.get("brand_id"))
            elif name == "zendesk_create_badge_assignment":
                values = arguments or {}; result = tools.create_badge_assignment(values.get("badge_id"), values.get("user_id"), execution_mode=values.get("execution_mode", "preview"), approval_request_id=values.get("approval_request_id"), approval_token=values.get("approval_token"))
            elif name == "zendesk_delete_badge_assignment":
                values = arguments or {}; result = tools.delete_badge_assignment(values.get("assignment_id"), execution_mode=values.get("execution_mode", "preview"), approval_request_id=values.get("approval_request_id"), approval_token=values.get("approval_token"))
            elif name == "zendesk_upload_community_user_image":
                values = arguments or {}; result = tools.upload_user_image(values.get("image_path"), values.get("content_type"), values.get("brand_id"), execution_mode=values.get("execution_mode", "preview"), approval_request_id=values.get("approval_request_id"), approval_token=values.get("approval_token"))
            elif name == "zendesk_upload_badge_icon":
                values = arguments or {}; result = tools.upload_badge_icon(values.get("image_path"), values.get("content_type"), execution_mode=values.get("execution_mode", "preview"), approval_request_id=values.get("approval_request_id"), approval_token=values.get("approval_token"))
            else: result = tools.get_post((arguments or {}).get("post_id"))
        elif name in {"zendesk_list_help_center_categories", "zendesk_list_help_center_sections", "zendesk_search_help_center_articles", "zendesk_get_help_center_article", "zendesk_get_satisfaction_ratings", "zendesk_list_guide_permission_groups", "zendesk_list_guide_user_segments"}:
            tools = build_guide_tools(environment)
            if isinstance(tools, dict): result = tools
            elif name == "zendesk_list_help_center_categories": result = tools.list_categories()
            elif name == "zendesk_list_help_center_sections": result = tools.list_sections()
            elif name == "zendesk_search_help_center_articles": result = tools.search_articles((arguments or {}).get("query"))
            elif name == "zendesk_get_help_center_article": result = tools.get_article((arguments or {}).get("article_id"))
            elif name == "zendesk_list_guide_permission_groups": result = tools.list_permission_groups()
            elif name == "zendesk_list_guide_user_segments":
                values = arguments or {}; result = tools.list_user_segments(built_in=values.get("built_in"), applicable=values.get("applicable", False))
            else: result = tools.get_satisfaction_ratings()
        elif name in {"zendesk_search_users", "zendesk_list_groups", "zendesk_list_group_users", "zendesk_get_organization", "zendesk_list_brands", "zendesk_list_ticket_fields", "zendesk_list_ticket_forms", "zendesk_list_custom_statuses", "zendesk_list_views", "zendesk_get_view", "zendesk_list_view_tickets", "zendesk_list_macros", "zendesk_list_triggers"}:
            tools = build_metadata_tools(environment)
            if isinstance(tools, dict):
                result = tools
            elif name == "zendesk_search_users": result = tools.search_users((arguments or {}).get("query"))
            elif name == "zendesk_list_groups": result = tools.list_groups()
            elif name == "zendesk_list_group_users": result = tools.list_group_users((arguments or {}).get("group_id"))
            elif name == "zendesk_get_organization": result = tools.get_organization((arguments or {}).get("organization_id"))
            elif name == "zendesk_list_brands": result = tools.list_brands()
            elif name == "zendesk_list_ticket_fields": result = tools.list_ticket_fields()
            elif name == "zendesk_list_ticket_forms": result = tools.list_ticket_forms()
            elif name == "zendesk_list_custom_statuses": result = tools.list_custom_statuses()
            elif name == "zendesk_list_views": result = tools.list_views()
            elif name == "zendesk_get_view": result = tools.get_view((arguments or {}).get("view_id"))
            elif name == "zendesk_list_view_tickets": result = tools.list_view_tickets((arguments or {}).get("view_id"))
            elif name == "zendesk_list_macros": result = tools.list_macros()
            else: result = tools.list_triggers()
        elif name in {
            "zendesk_list_tickets",
            "zendesk_search_tickets",
            "zendesk_count_tickets",
            "zendesk_export_tickets",
            "zendesk_get_ticket",
            "zendesk_create_ticket",
            "zendesk_update_ticket",
            "zendesk_set_ticket_status",
            "zendesk_assign_ticket",
            "zendesk_add_ticket_tag",
            "zendesk_remove_ticket_tag",
            "zendesk_get_ticket_conversation",
            "zendesk_list_ticket_attachments",
            "zendesk_ticket_to_issue_context",
            "zendesk_post_public_reply",
            "zendesk_post_internal_note",
        }:
            tools = build_ticket_tools(environment)
            if isinstance(tools, dict):
                result = tools
            elif name == "zendesk_list_tickets":
                limit = (arguments or {}).get("limit", 100)
                result = tools.list_tickets(limit) if isinstance(limit, int) and not isinstance(limit, bool) else failure(ErrorCode.VALIDATION_ERROR, "limit must be an integer")
            elif name == "zendesk_search_tickets":
                query = (arguments or {}).get("query")
                limit = (arguments or {}).get("limit", 100)
                result = tools.search_tickets(query, limit) if isinstance(limit, int) and not isinstance(limit, bool) else failure(ErrorCode.VALIDATION_ERROR, "limit must be an integer")
            elif name == "zendesk_count_tickets":
                result = tools.count_tickets((arguments or {}).get("query"))
            elif name == "zendesk_export_tickets":
                result = tools.export_tickets((arguments or {}).get("query"))
            elif name == "zendesk_create_ticket":
                values = arguments or {}
                result = tools.create_ticket(
                    requester_id=values.get("requester_id"),
                    subject=values.get("subject"),
                    description=values.get("description"),
                    tags=values.get("tags"),
                    priority=values.get("priority"),
                    ticket_type=values.get("ticket_type"),
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
            elif name == "zendesk_ticket_to_issue_context":
                result = tools.ticket_to_issue_context((arguments or {}).get("ticket_id"))
            else:
                ticket_id = (arguments or {}).get("ticket_id")
                if not isinstance(ticket_id, int) or isinstance(ticket_id, bool):
                    result = failure(ErrorCode.VALIDATION_ERROR, "ticket_id must be a positive integer")
                elif name == "zendesk_get_ticket":
                    result = tools.get_ticket(ticket_id)
                else:
                    result = tools.get_conversation(ticket_id)
        else:
            result = failure(ErrorCode.NOT_FOUND, f"Unknown tool: {name}")
        return [types.TextContent(type="text", text=json.dumps(result))]

    return server


async def main() -> None:
    server = create_server()
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            initialization_options=InitializationOptions(
                server_name="Zendesk",
                server_version="0.1.0",
                capabilities=server.get_capabilities(
                    notification_options=NotificationOptions(),
                    experimental_capabilities={},
                ),
            ),
        )


if __name__ == "__main__":
    asyncio.run(main())
