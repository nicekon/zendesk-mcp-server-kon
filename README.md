# Zendesk MCP

<!-- mcp-name: io.github.nicekon/zendesk-mcp-server-kon -->

[한국어](README.ko.md)

Connect Zendesk to an AI app such as Codex to **find and summarize tickets, help articles, and community discussions**.

- “Find recent unresolved tickets.”
- “Summarize this ticket and draft a reply.”
- “Find help articles about refunds.”

The default is **read-only**. Drafting a reply does not send it to a customer.
An administrator must configure editing and sending separately.

## Before you start

- You need a Zendesk account and an AI app that supports MCP. This guide uses **Codex**.
- Install [Git](https://git-scm.com/downloads) and [uv](https://docs.astral.sh/uv/getting-started/installation/). uv installs this tool for you.
- Run commands in **your computer’s terminal**, not in the AI chat. On macOS, open Terminal.
- Ask your administrator for your **Zendesk subdomain and OAuth client identifier**.

This is a source installation. Browser login below supports macOS and Linux.
**On Windows, use [API-token setup](docs/advanced-setup.md#api-token).**

## 1. Install

Run these lines one at a time:

```bash
git clone https://github.com/nicekon/zendesk-mcp-server-kon.git
cd zendesk-mcp-server-kon
uv tool install .
zendesk --help
```

If usage instructions appear, installation is complete. If `zendesk` is not found,
run `uv tool update-shell` and open a new terminal.

## 2. Log in to Zendesk

Replace the two example values with those from your administrator:

- `your-company`: for `your-company.zendesk.com`, use only `your-company`, not the full address or a custom help-center domain.
- `your-client-id`: your **OAuth client identifier**, not your email or API token.

```bash
zendesk login --subdomain your-company --client-id your-client-id
```

Sign in and allow access in the browser that opens. You do not need to copy a code or start a separate server.
The connection is saved on your computer, so you do not need to log in every time.

<details>
<summary>Administrators: prepare the first connection</summary>

Create a **Public OAuth client** in Zendesk and register this exact redirect address:

```text
http://127.0.0.1:3000/oauth/callback
```

Share the subdomain and client identifier with users, not a client secret.
Default login requests read access. The `read` permission needed for search also permits other data reads allowed by the user’s role.
See [advanced setup](docs/advanced-setup.md) for additional configuration.

</details>

## 3. Check the connection

```bash
zendesk check --probe
```

Look for `"ok": true`, `"configured": true`, and `"verified_user"` in the result.
For errors, see the table below. **Never paste tokens or passwords into chat.**

## 4. Connect Codex

In a terminal with Codex CLI installed, find the executable:

```bash
command -v zendesk
```

Replace `/absolute/path/to/zendesk` below with the printed path. Keep the quotes.

```bash
codex mcp add zendesk_mcp -- "/absolute/path/to/zendesk"
codex mcp list
```

If `zendesk_mcp` appears, it is registered. Reopen Codex and ask “Check my Zendesk connection” in a new conversation.
If the registration command is unavailable or you use another app, see [app connection settings](docs/advanced-setup.md#connect-app).

## Troubleshooting

| Problem | What to check |
| --- | --- |
| Command not found | Run `uv tool update-shell`, then open a new terminal. |
| Login does not finish | Check the browser consent page. If it timed out, run login again. |
| `401` or `authentication_failed` | Log in again. For API tokens, check the subdomain, email, and token. |
| `403` or `permission_denied` | Ask your administrator to check access to that feature. |
| `not_configured` or `unsupported` | Extra configuration or a different Zendesk plan may be needed. |
| Reading works but editing does not | Read-only is the default, not an error. |

Some features require extra setup. Availability depends on your Zendesk plan and user permissions.
**Image uploads still have an unresolved real-account permission issue.**

## More information

- [API tokens, write access, and optional features](docs/advanced-setup.md)
- [Migrate or roll back an existing installation](docs/migration.md)
- [Full feature and API reference](docs/capability-manifest.md)
- [Development and release guide](docs/releasing.md)

License: [Apache-2.0](LICENSE)
