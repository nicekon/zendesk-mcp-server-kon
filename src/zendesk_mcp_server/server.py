"""MCP registration and lifecycle for the Zendesk server."""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Mapping

from mcp.server import InitializationOptions, NotificationOptions, Server, types
from mcp.server.stdio import stdio_server

from .config import ConfigurationError, Settings
from .contracts import ErrorCode, failure, success


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
        )
    ]


def build_connection_status(environ: Mapping[str, str]) -> dict[str, object]:
    try:
        return success(Settings.load(environ).connection_status())
    except ConfigurationError as error:
        return failure(ErrorCode.VALIDATION_ERROR, str(error))


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
        del arguments
        if name == "zendesk_get_connection_status":
            result = build_connection_status(environment)
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
