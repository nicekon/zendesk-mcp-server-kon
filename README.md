# Zendesk MCP

<!-- mcp-name: io.github.nicekon/zendesk-mcp-server-kon -->

[한국어](README.ko.md)

Connect Zendesk to an AI app such as Codex to **find and summarize tickets, help articles, and community discussions**.

- “Find recent unresolved tickets.”
- “Summarize this ticket and draft a reply.”
- “Find help articles about refunds.”

New installations can update tickets and create Community posts or comments after OAuth login. Community writes always require a preview and a one-time local approval; destructive, impersonated, and external-upload actions stay disabled by default.

## Before you start

- You need a Zendesk account and an AI app that supports MCP. This guide uses **Codex**.
- Install [Git](https://git-scm.com/downloads) and [uv](https://docs.astral.sh/uv/getting-started/installation/). uv runs this tool for you.
- Run commands in **your computer’s terminal**, not in the AI chat. On macOS, open Terminal.
- Ask your administrator for your **Zendesk subdomain and OAuth client identifier**.

Browser login below supports macOS and Linux.
**On Windows, use [API-token setup](docs/advanced-setup.md#api-token).**

## Quick install

This uses the tested source revision directly; it does not clone the repository or install a permanent `zendesk` command. The commit SHA pins the server source. To update, replace it with a reviewed newer SHA.

## 1. Log in to Zendesk

Replace the two example values with those from your administrator:

- `your-company`: for `your-company.zendesk.com`, use only `your-company`, not the full address or a custom help-center domain.
- `your-client-id`: your **OAuth client identifier**, not your email or API token.

```bash
uvx --from 'git+https://github.com/nicekon/zendesk-mcp-server-kon.git@e829b165d0fe99d48266cddd26fb8448ff476fed' zendesk login --subdomain your-company --client-id your-client-id
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
Default login requests the read access needed for search plus `tickets:write` and `hc:write` for the default ticket and Community capabilities. The `read` permission needed for search also permits other data reads allowed by the user’s role.
See [advanced setup](docs/advanced-setup.md) for additional configuration.

</details>

## 2. Check the connection

```bash
uvx --from 'git+https://github.com/nicekon/zendesk-mcp-server-kon.git@e829b165d0fe99d48266cddd26fb8448ff476fed' zendesk check --probe
```

Look for `"ok": true`, `"configured": true`, and `"verified_user"` in the result.
For errors, see the table below. **Never paste tokens or passwords into chat.**

## 3. Connect Codex

```bash
codex mcp add zendesk_mcp -- uvx --from 'git+https://github.com/nicekon/zendesk-mcp-server-kon.git@e829b165d0fe99d48266cddd26fb8448ff476fed' zendesk
codex mcp list
```

If `zendesk_mcp` appears, it is registered. Reopen Codex and ask “Check my Zendesk connection” in a new conversation.
If the registration command is unavailable or you use another app, see [app connection settings](docs/advanced-setup.md#connect-app).

## Alternative: source installation

Use this only when you need a local checkout for development or a local change:

```bash
git clone https://github.com/nicekon/zendesk-mcp-server-kon.git
cd zendesk-mcp-server-kon
uv tool install .
```

With a source installation, use `zendesk` instead of the `uvx --from ... zendesk` commands above. If `zendesk` is not found, run `uv tool update-shell` and open a new terminal.

## Troubleshooting

| Problem | What to check |
| --- | --- |
| `uvx` is not found | Install [uv](https://docs.astral.sh/uv/getting-started/installation/), then open a new terminal. |
| `zendesk` is not found after source installation | Run `uv tool update-shell`, then open a new terminal. |
| Login does not finish | Check the browser consent page. If it timed out, run login again. |
| `401` or `authentication_failed` | Log in again. For API tokens, check the subdomain, email, and token. |
| `403` or `permission_denied` | Ask your administrator to check access to that feature. |
| `not_configured` or `unsupported` | Extra configuration or a different Zendesk plan may be needed. |
| Editing is blocked | Check whether `ZENDESK_WRITE_MODE=read_only` was set. `ZENDESK_ENABLE_PUBLIC_WRITES=false` blocks Community posts and comments but not ordinary ticket edits. |
| `oauth_relogin_required` | This OAuth connection was created without a scope now required by the active capabilities. Run `zendesk login` again with the same capability settings; tokens are never changed automatically. |

Some features require extra setup. Availability depends on your Zendesk plan and user permissions.
**Image uploads still have an unresolved real-account permission issue.**

## More information

- [API tokens, write access, and optional features](docs/advanced-setup.md)
- [Migrate or roll back an existing installation](docs/migration.md)
- [Full feature and API reference](docs/capability-manifest.md)
- [Development and release guide](docs/releasing.md)

License: [Apache-2.0](LICENSE)
