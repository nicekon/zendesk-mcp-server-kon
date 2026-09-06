# Zendesk MCP Server KON

[한국어](README.ko.md)

[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)

A Zendesk Model Context Protocol server implementing the evolving
[unified Zendesk MCP PRD](docs/superpowers/specs/2026-09-04-unified-zendesk-mcp-design.md).
The [capability manifest](docs/capability-manifest.md) records the official
Zendesk contracts behind Community tools.

## Current status

The current pre-release provides the following guarded domains:

- `zendesk_get_connection_status` reports configuration without secrets and,
  when credentials are configured, verifies the authenticated Zendesk user.
- Support: ticket reads, search/count/export, guarded mutations, comments,
  macro preview/apply, metadata, and safe attachment download/inspection.
- Guide and CSAT: locales, categories, sections, article search/export,
  permission metadata, ratings, draft article and translation workflows.
- Community: posts, comments, topics, votes, subscriptions, content tags,
  badges, and secure user-image/badge-icon upload flows.
- Conditional Support tools: ticket audit time tracking and Git-Zen link
  extraction from an explicitly configured custom field.

The server starts in `read_only` mode. Standard writes require
`ZENDESK_WRITE_MODE=standard`; public, destructive, impersonation, and external
upload operations require their separate `ZENDESK_ENABLE_*` gates. Any
preview/apply operation also requires a matching one-time local approval:

```bash
zendesk approve <approval_request_id>
```

This command requires an interactive terminal, displays the exact stored
preview, and issues the single-use token only after `yes` confirmation.

Attachment downloads accept only the attachment identified by its ticket and
attachment ID, require Zendesk's safe malware result, and use a managed cache.
Set `ZENDESK_ATTACHMENT_CACHE_ROOT` to relocate it. Secure local image uploads
require `ZENDESK_UPLOAD_ROOT`.

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

For OAuth, configure `ZENDESK_SUBDOMAIN`, `ZENDESK_AUTH_MODE=oauth`,
`ZENDESK_OAUTH_CLIENT_ID`, `ZENDESK_OAUTH_CLIENT_SECRET`, and a user-only
`ZENDESK_OAUTH_TOKEN_STORE` path. Then use the redirect URI registered in
Zendesk exactly in both commands:

```bash
zendesk oauth-start https://your-app.example/callback
zendesk oauth-finish https://your-app.example/callback <state>
```

`oauth-start` prints the authorization URL and saves its one-time state. After
Zendesk redirects back, pass the returned `state` to `oauth-finish`; it prompts
for the authorization code without echoing it and stores refreshed tokens with
user-only permissions.

Enable optional domains with `ZENDESK_CAPABILITIES`; disabled tools remain
listed but return `not_configured` before making a Zendesk request. Set
`ZENDESK_GIT_ZEN_FIELD_ID` only when `git_zen` is enabled. Time logging uses
Zendesk ticket audit metadata and always includes an internal note.

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
