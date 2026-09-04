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
        types.Tool(name="zendesk_list_community_posts", description="List Community posts without making changes.", inputSchema={"type": "object", "properties": {}}),
        types.Tool(name="zendesk_search_community_posts", description="Search Community posts without making changes.", inputSchema={"type": "object", "properties": {"query": {"type": "string", "minLength": 1}}, "required": ["query"]}),
        types.Tool(name="zendesk_get_community_post", description="Get a Community post without making changes.", inputSchema={"type": "object", "properties": {"post_id": {"type": "integer", "minimum": 1}}, "required": ["post_id"]}),
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
    return CommunityTools(ZendeskClient(settings, authorization))


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
        elif name in {"zendesk_list_community_posts", "zendesk_search_community_posts", "zendesk_get_community_post"}:
            tools = build_community_tools(environment)
            if isinstance(tools, dict): result = tools
            elif name == "zendesk_list_community_posts": result = tools.list_posts()
            elif name == "zendesk_search_community_posts": result = tools.search_posts((arguments or {}).get("query"))
            else: result = tools.get_post((arguments or {}).get("post_id"))
        elif name in {"zendesk_list_help_center_categories", "zendesk_list_help_center_sections", "zendesk_search_help_center_articles", "zendesk_get_help_center_article", "zendesk_get_satisfaction_ratings"}:
            tools = build_guide_tools(environment)
            if isinstance(tools, dict): result = tools
            elif name == "zendesk_list_help_center_categories": result = tools.list_categories()
            elif name == "zendesk_list_help_center_sections": result = tools.list_sections()
            elif name == "zendesk_search_help_center_articles": result = tools.search_articles((arguments or {}).get("query"))
            elif name == "zendesk_get_help_center_article": result = tools.get_article((arguments or {}).get("article_id"))
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
