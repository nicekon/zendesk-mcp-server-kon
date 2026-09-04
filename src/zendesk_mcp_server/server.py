"""MCP registration and lifecycle for the Zendesk server."""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Mapping

from mcp.server import InitializationOptions, NotificationOptions, Server, types
from mcp.server.stdio import stdio_server

from .auth import build_authorization
from .client import ZendeskClient
from .config import ConfigurationError, Settings
from .contracts import ErrorCode, failure, success
from .tools.tickets import TicketTools


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
            name="zendesk_get_ticket_conversation",
            description="Retrieve a ticket conversation without making changes.",
            inputSchema={"type": "object", "properties": {"ticket_id": {"type": "integer", "minimum": 1}}, "required": ["ticket_id"]},
        ),
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
    return TicketTools(ZendeskClient(settings, authorization))


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
        elif name in {
            "zendesk_list_tickets",
            "zendesk_search_tickets",
            "zendesk_count_tickets",
            "zendesk_get_ticket",
            "zendesk_get_ticket_conversation",
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
