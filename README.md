# Zendesk MCP Server KON

[한국어](README.ko.md)

[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)

A Zendesk Model Context Protocol server. This branch is the first implementation
stage of the [unified Zendesk MCP PRD](docs/superpowers/specs/2026-09-04-unified-zendesk-mcp-design.md).
It establishes safe startup, explicit authentication selection, and one HTTP
transport before Support, Guide, CSAT, and Community tools are added.

## Current status

The current pre-release exposes these safe tools:

- `zendesk_get_connection_status` reports whether the server is configured. It
  never sends a network request and never returns an email address, token, or
  OAuth secret.
- `zendesk_list_tickets`, `zendesk_search_tickets`, `zendesk_count_tickets`,
  `zendesk_get_ticket`, and `zendesk_get_ticket_conversation` provide
  read-only Support access. Search is always limited to ticket records.
- `zendesk_create_ticket` and `zendesk_update_ticket` are standard writes. They
  are unavailable until
  `ZENDESK_WRITE_MODE=standard` is set, and their responses note that account
  automations can have additional side effects.
- `zendesk_post_internal_note` is a standard write. `zendesk_post_public_reply`
  defaults to a non-writing preview. To apply a public reply, enable
  `ZENDESK_ENABLE_PUBLIC_WRITES=true`, inspect and approve its request locally
  with `zendesk approve <approval_request_id>`, then submit the returned token
  with the unchanged payload.

The server starts in `read_only` mode. Help Center, CSAT, attachment, and
Community tools are intentionally not available until their corresponding
implementation stages are complete.

## Setup

Install the package:

```bash
uv sync --group dev
```

The server can start with no Zendesk credentials. To configure API-token
authentication, provide all three variables:

```json
{
  "mcpServers": {
    "zendesk": {
      "command": "uv",
      "args": ["--directory", "/path/to/zendesk-mcp-server-kon", "run", "zendesk"],
      "env": {
        "ZENDESK_SUBDOMAIN": "your-zendesk-subdomain",
        "ZENDESK_EMAIL": "your-zendesk-email",
        "ZENDESK_API_TOKEN": "your-zendesk-api-token"
      }
    }
  }
}
```

`ZENDESK_API_KEY` is no longer accepted. Migrate it to
`ZENDESK_API_TOKEN`; the server rejects the deprecated name instead of silently
using it.

`ZENDESK_AUTH_MODE` defaults to `auto`. It selects complete OAuth settings when
present, otherwise a complete API-token configuration. A partial OAuth
configuration is an error and never falls back to API token credentials.

## Prompts

- `analyze-ticket(ticket_id)`
- `draft-ticket-response(ticket_id)`

Prompts only generate guidance. They do not perform Zendesk writes.

## Development

```bash
uv run pytest -v
uv build
```

See the [foundation implementation plan](docs/superpowers/plans/2026-09-04-unified-zendesk-mcp-foundation.md)
for the active scope and the PRD for the phased feature roadmap.
