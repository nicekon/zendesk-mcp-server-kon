import asyncio
import json
import logging
import os
from typing import Any, Dict

from cachetools.func import ttl_cache
from mcp.server import InitializationOptions, NotificationOptions
from mcp.server import Server, types
from mcp.server.stdio import stdio_server
from pydantic import AnyUrl

from zendesk_mcp_server.zendesk_client import ZendeskClient

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger("zendesk-mcp-server")
logger.info("zendesk mcp server started")

# Get environment variables
subdomain = os.environ.get("ZENDESK_SUBDOMAIN")
email = os.environ.get("ZENDESK_EMAIL")
api_key = os.environ.get("ZENDESK_API_KEY")

if not all([subdomain, email, api_key]):
    raise ValueError(
        "Missing required environment variables. Please set ZENDESK_SUBDOMAIN, ZENDESK_EMAIL, and ZENDESK_API_KEY"
    )

zendesk_client = ZendeskClient(
    subdomain=subdomain,
    email=email,
    token=api_key
)

server = Server("Zendesk Server")

TICKET_ANALYSIS_TEMPLATE = """
You are a helpful Zendesk support analyst. You've been asked to analyze ticket #{ticket_id}.

Please fetch the ticket info and comments to analyze it and provide:
1. A summary of the issue
2. The current status and timeline
3. Key points of interaction

Remember to be professional and focus on actionable insights.
"""

COMMENT_DRAFT_TEMPLATE = """
You are a helpful Zendesk support agent. You need to draft a response to ticket #{ticket_id}.

Please fetch the ticket info, comments and knowledge base to draft a professional and helpful response that:
1. Acknowledges the customer's concern
2. Addresses the specific issues raised
3. Provides clear next steps or ask for specific details need to proceed
4. Maintains a friendly and professional tone
5. Ask for confirmation before commenting on the ticket

The response should be formatted well and ready to be posted as a comment.
"""


@server.list_prompts()
async def handle_list_prompts() -> list[types.Prompt]:
    """List available prompts"""
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
        )
    ]


@server.get_prompt()
async def handle_get_prompt(name: str, arguments: Dict[str, str] | None) -> types.GetPromptResult:
    """Handle prompt requests"""
    if not arguments or "ticket_id" not in arguments:
        raise ValueError("Missing required argument: ticket_id")

    ticket_id = int(arguments["ticket_id"])
    try:
        if name == "analyze-ticket":
            prompt = TICKET_ANALYSIS_TEMPLATE.format(
                ticket_id=ticket_id
            )
            description = f"Analysis prompt for ticket #{ticket_id}"

        elif name == "draft-ticket-response":
            prompt = COMMENT_DRAFT_TEMPLATE.format(
                ticket_id=ticket_id
            )
            description = f"Response draft prompt for ticket #{ticket_id}"

        else:
            raise ValueError(f"Unknown prompt: {name}")

        return types.GetPromptResult(
            description=description,
            messages=[
                types.PromptMessage(
                    role="user",
                    content=types.TextContent(type="text", text=prompt.strip()),
                )
            ],
        )

    except Exception as e:
        logger.error(f"Error generating prompt: {e}")
        raise


@server.list_tools()
async def handle_list_tools() -> list[types.Tool]:
    """List available Zendesk tools"""
    return [
        types.Tool(
            name="get_ticket",
            description="Retrieve a Zendesk ticket by its ID",
            inputSchema={
                "type": "object",
                "properties": {
                    "ticket_id": {
                        "type": "integer",
                        "description": "The ID of the ticket to retrieve"
                    }
                },
                "required": ["ticket_id"]
            }
        ),
        types.Tool(
            name="get_ticket_comments",
            description="Retrieve all comments for a Zendesk ticket by its ID",
            inputSchema={
                "type": "object",
                "properties": {
                    "ticket_id": {
                        "type": "integer",
                        "description": "The ID of the ticket to get comments for"
                    }
                },
                "required": ["ticket_id"]
            }
        ),
        types.Tool(
            name="create_ticket_comment",
            description="Create a new comment on an existing Zendesk ticket",
            inputSchema={
                "type": "object",
                "properties": {
                    "ticket_id": {
                        "type": "integer",
                        "description": "The ID of the ticket to comment on"
                    },
                    "comment": {
                        "type": "string",
                        "description": "The comment text/content to add"
                    },
                    "public": {
                        "type": "boolean",
                        "description": "Whether the comment should be public",
                        "default": True
                    }
                },
                "required": ["ticket_id", "comment"]
            }
        ),
        types.Tool(
            name="create_community_post_comment",
            description="Create a new comment on a community post",
            inputSchema={
                "type": "object",
                "properties": {
                    "post_id": {
                        "type": "integer",
                        "description": "ID of the post to comment on"
                    },
                    "body": {
                        "type": "string",
                        "description": "Comment content"
                    },
                    "author_id": {
                        "type": "integer",
                        "description": "Comment author ID (only available for Help Center administrators)",
                    },
                    "notify_subscribers": {
                        "type": "boolean",
                        "description": "Whether to notify subscribers",
                        "default": True
                    }
                },
                "required": ["post_id", "body"]
            }
        ),
        types.Tool(
            name="get_community_posts",
            description="Retrieve community posts with optional filtering and sorting",
            inputSchema={
                "type": "object",
                "properties": {
                    "filter_by": {
                        "type": "string",
                        "description": "Filter posts by status (planned, not_planned, completed, answered, none)",
                        "enum": ["planned", "not_planned", "completed", "answered", "none"]
                    },
                    "sort_by": {
                        "type": "string",
                        "description": "Sort posts by criteria (created_at, edited_at, updated_at, recent_activity, votes, comments)",
                        "enum": ["created_at", "edited_at", "updated_at", "recent_activity", "votes", "comments"]
                    }
                }
            }
        ),
        types.Tool(
            name="get_community_post_comments",
            description="Retrieve a community post and all its comments. Returns both the post details (title, content, author, status, etc.) and all associated comments.",
            inputSchema={
                "type": "object",
                "properties": {
                    "post_id": {
                        "type": "integer",
                        "description": "The ID of the post to retrieve comments for"
                    }
                },
                "required": ["post_id"]
            }
        ),
        types.Tool(
            name="get_community_topics",
            description="Retrieve all community topics. Returns a list of topics with their details including name, description, follower count, etc.",
            inputSchema={
                "type": "object",
                "properties": {}
            }
        ),
        types.Tool(
            name="update_community_post_comment",
            description="Update a comment on a community post",
            inputSchema={
                "type": "object",
                "properties": {
                    "post_id": {
                        "type": "integer",
                        "description": "ID of the post containing the comment"
                    },
                    "comment_id": {
                        "type": "integer",
                        "description": "ID of the comment to update"
                    },
                    "body": {
                        "type": "string",
                        "description": "Updated comment content"
                    }
                },
                "required": ["post_id", "comment_id", "body"]
            }
        ),
        types.Tool(
            name="update_community_post",
            description="Update a community post",
            inputSchema={
                "type": "object",
                "properties": {
                    "post_id": {
                        "type": "integer",
                        "description": "ID of the post to update"
                    },
                    "title": {
                        "type": "string",
                        "description": "Post title"
                    },
                    "details": {
                        "type": "string",
                        "description": "Post content (supports p, br, strong tags)"
                    },
                    "topic_id": {
                        "type": "integer",
                        "description": "ID of the topic this post belongs to"
                    },
                    "status": {
                        "type": "string",
                        "description": "Post status",
                        "enum": ["planned", "not_planned", "answered", "completed"]
                    }
                },
                "required": ["post_id"]
            }
        ),
    ]


@server.call_tool()
async def handle_call_tool(
        name: str,
        arguments: dict[str, Any] | None
) -> list[types.TextContent]:
    """Handle Zendesk tool execution requests"""
    try:
        if not arguments:
            raise ValueError("Missing arguments")

        if name == "get_ticket":
            ticket = zendesk_client.get_ticket(arguments["ticket_id"])
            return [types.TextContent(
                type="text",
                text=json.dumps(ticket)
            )]

        elif name == "get_ticket_comments":
            comments = zendesk_client.get_ticket_comments(
                arguments["ticket_id"])
            return [types.TextContent(
                type="text",
                text=json.dumps(comments)
            )]

        elif name == "create_ticket_comment":
            public = arguments.get("public", True)
            result = zendesk_client.post_comment(
                ticket_id=arguments["ticket_id"],
                comment=arguments["comment"],
                public=public
            )
            return [types.TextContent(
                type="text",
                text=f"Comment created successfully: {result}"
            )]

        elif name == "create_community_post_comment":
            post_id = arguments["post_id"]
            body = arguments["body"]
            author_id = arguments.get("author_id")
            notify_subscribers = arguments.get("notify_subscribers", True)
            
            result = zendesk_client.create_community_post_comment(
                post_id=post_id,
                body=body,
                author_id=author_id,
                notify_subscribers=notify_subscribers
            )
            return [types.TextContent(
                type="text",
                text=json.dumps(result)
            )]

        elif name == "get_community_posts":
            filter_by = arguments.get("filter_by")
            sort_by = arguments.get("sort_by")
            posts = zendesk_client.get_community_posts(filter_by, sort_by)
            return [types.TextContent(
                type="text",
                text=json.dumps(posts)
            )]

        elif name == "get_community_post_comments":
            post_comments = zendesk_client.get_community_post_comments(
                arguments["post_id"])
            return [types.TextContent(
                type="text",
                text=json.dumps(post_comments)
            )]

        elif name == "get_community_topics":
            topics = zendesk_client.get_community_topics()
            return [types.TextContent(
                type="text",
                text=json.dumps(topics)
            )]

        elif name == "update_community_post_comment":
            result = zendesk_client.update_community_post_comment(
                post_id=arguments["post_id"],
                comment_id=arguments["comment_id"],
                body=arguments["body"]
            )
            return [types.TextContent(
                type="text",
                text=json.dumps(result)
            )]

        elif name == "update_community_post":
            result = zendesk_client.update_community_post(
                post_id=arguments["post_id"],
                title=arguments.get("title"),
                details=arguments.get("details"),
                topic_id=arguments.get("topic_id"),
                status=arguments.get("status")
            )
            return [types.TextContent(
                type="text",
                text=json.dumps(result)
            )]

        else:
            raise ValueError(f"Unknown tool: {name}")

    except Exception as e:
        logger.error(f"Error executing tool {name}: {e}")
        raise


@server.list_resources()
async def handle_list_resources() -> list[types.Resource]:
    logger.debug("Handling list_resources request")
    return [
        types.Resource(
            uri=AnyUrl("zendesk://knowledge-base"),
            name="Zendesk Knowledge Base",
            description="Access to Zendesk Help Center articles and sections",
            mimeType="application/json",
        )
    ]


@ttl_cache(ttl=3600)
def get_cached_kb():
    return zendesk_client.get_all_articles()


@server.read_resource()
async def handle_read_resource(uri: AnyUrl) -> str:
    logger.debug(f"Handling read_resource request for URI: {uri}")
    if uri.scheme != "zendesk":
        logger.error(f"Unsupported URI scheme: {uri.scheme}")
        raise ValueError(f"Unsupported URI scheme: {uri.scheme}")

    path = str(uri).replace("zendesk://", "")
    if path != "knowledge-base":
        logger.error(f"Unknown resource path: {path}")
        raise ValueError(f"Unknown resource path: {path}")

    try:
        kb_data = get_cached_kb()
        return json.dumps({
            "knowledge_base": kb_data,
            "metadata": {
                "sections": len(kb_data),
                "total_articles": sum(len(section['articles']) for section in kb_data.values()),
            }
        }, indent=2)
    except Exception as e:
        logger.error(f"Error fetching knowledge base: {e}")
        raise


async def main():
    # Run the server using stdin/stdout streams
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream=read_stream,
            write_stream=write_stream,
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
