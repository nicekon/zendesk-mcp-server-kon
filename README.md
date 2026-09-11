# Zendesk MCP Server KON

<!-- mcp-name: io.github.nicekon/zendesk-mcp-server-kon -->

[한국어](README.ko.md)

[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)

A Zendesk Model Context Protocol server implementing the evolving
[unified Zendesk MCP PRD](docs/superpowers/specs/2026-09-04-unified-zendesk-mcp-design.md).
The [capability manifest](docs/capability-manifest.md) records the official
Zendesk contracts behind Community tools.
See the [release procedure](docs/releasing.md) for wheel, MCPB, and MCP Registry
publication.
See [migration and rollback](docs/migration.md) before replacing an existing
KON or OAuth installation.

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
Run it with the same `ZENDESK_SUBDOMAIN` as the MCP server; approvals are bound
to that tenant and cannot be reused elsewhere.

Attachment downloads accept only the attachment identified by its ticket and
attachment ID, require Zendesk's safe malware result, and use a managed cache.
Set `ZENDESK_ATTACHMENT_CACHE_ROOT` to relocate it. Secure local image uploads
require `ZENDESK_UPLOAD_ROOT`.

## Installation and setup

This repository has not published an immutable end-user release yet. After the
release commit is pushed, a source installation works as follows:

```bash
git clone https://github.com/nicekon/zendesk-mcp-server-kon.git
cd zendesk-mcp-server-kon
uv tool install .
zendesk --help
```

If uv reports that its executable directory is not on `PATH`, run
`uv tool update-shell` and open a new terminal. `uv tool install` creates the
`zendesk` command in an isolated environment; `uv sync` below is for repository
development only.

### Browser OAuth login

A Zendesk administrator first creates a **Public** OAuth client for the tenant
and registers this exact redirect URI:

```text
http://127.0.0.1:3000/oauth/callback
```

The client must allow the read scopes requested by the enabled capabilities.
The administrator distributes the client identifier, not a client secret.
Each user then runs:

```bash
zendesk login --subdomain your-zendesk-subdomain --client-id your-client-identifier
zendesk check --probe
```

`zendesk login` temporarily listens on `127.0.0.1:3000`, opens the system
browser, verifies the callback state and PKCE code, verifies the Zendesk user,
stores the connection under `~/.config/zendesk-mcp-server/connection.json`, and
closes the listener. The user does not copy an authorization code. Use `--port`
only when the same alternate redirect URI is registered in Zendesk.

Find the installed executable with `command -v zendesk` on macOS/Linux or
`where zendesk` on Windows. For Codex, register that absolute path and verify
the entry:

```bash
codex mcp add zendesk -- /absolute/path/to/zendesk
codex mcp list
```

Other MCP clients commonly use the equivalent JSON configuration:

```json
{
  "mcpServers": {
    "zendesk": {
      "command": "/absolute/path/to/zendesk"
    }
  }
}
```

The MCP process reads the saved connection without relying on terminal
environment variables. It never opens a browser while running over stdio.
Uninstalling the package does not revoke Zendesk access. Revoke the OAuth grant
in Zendesk and remove the exact local connection file when the connection is no
longer needed.

### API-token configuration

API-token authentication remains available. Provide all three variables to the
MCP process:

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

Validate configuration without a network request using `zendesk check`; add
`--probe` to verify the current Zendesk user without printing secrets.

```bash
zendesk check
```

The legacy confidential OAuth flow remains available for server-side setups.
Configure `ZENDESK_SUBDOMAIN`, `ZENDESK_AUTH_MODE=oauth`,
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
uv sync --group dev
uv run pytest -v
uv build
```

See the [foundation implementation plan](docs/superpowers/plans/2026-09-04-unified-zendesk-mcp-foundation.md)
for the active scope and the PRD for the phased feature roadmap.
