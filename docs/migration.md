# Migration and rollback

## Existing KON configuration

No legacy tool aliases are installed. Use the canonical names below and update
automations before replacing the old server.

| Previous name | Canonical tool |
| --- | --- |
| `get_ticket` | `zendesk_get_ticket` |
| `get_ticket_comments` | `zendesk_get_ticket_conversation` |
| `create_ticket_comment(public=true)` | `zendesk_post_public_reply` |
| `create_ticket_comment(public=false)` | `zendesk_post_internal_note` |
| `get_community_posts` | `zendesk_list_community_posts` |
| `get_community_post_comments` | `zendesk_list_community_comments` |
| `create_community_post_comment` | `zendesk_create_community_comment` |
| `update_community_post_comment` | `zendesk_update_community_comment` |
| `update_community_post` | `zendesk_update_community_post` |
| `get_community_topics` | `zendesk_list_community_topics` |

Replace `ZENDESK_API_KEY` with `ZENDESK_API_TOKEN`; do not set both. Verify the
environment with `zendesk check` before restarting the MCP server.

## OAuth transition

For a new local installation, create a Public OAuth client with
`http://127.0.0.1:3000/oauth/callback` and run `zendesk login --subdomain ...
--client-id ...`. The saved connection is shared by later stdio MCP processes.
No client secret or manual authorization-code copy is used.

Keep an existing OAuth token file as a user-only backup. Configure a new,
separate `ZENDESK_OAUTH_TOKEN_STORE`, complete `zendesk oauth-start` and
`zendesk oauth-finish`, then verify the connection. Never copy an access or
refresh token into chat, command arguments, or repository files.

After the new store has refreshed successfully, do not restore an old token
file: refresh-token rotation can make it stale. If the new store is unusable,
return to API-token authentication or reauthorize OAuth instead of restoring a
backup older than seven days.

## Package rollback and write recovery

Pin the previous known-good package release, retain the current OAuth token
store, and run `zendesk check` before restarting. A package rollback never
reverses Zendesk writes already applied. For an `outcome_unknown` response,
inspect the specific Zendesk resource manually before retrying; do not infer
success from a timeout or connection failure.
