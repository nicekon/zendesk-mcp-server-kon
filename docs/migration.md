# Migration and rollback

## Knowledge base resource output

The opt-in `zendesk://knowledge-base` resource now returns a JSON manifest,
not inline article arrays. Each `data.locales` entry contains `locale`,
`item_count`, `truncated`, and a serialized MCP ResourceLink in `resource`.
Read the managed JSON file at `resource.uri` for that locale's articles.
Update consumers of `data.locales[].articles` accordingly. The manifest is
cached for one hour; export files use the existing 24-hour cache. The shared
100,000-article limit still applies across locales. No caller-chosen output
path is accepted.

## Existing KON configuration

### Multiple custom-object lookup fields

Ticket projection now retains distinct records from every ticket lookup field
targeting the requested object, instead of only the last field. Null references
are skipped and duplicate record references are fetched once per ticket/object.
JSON keeps the records in `custom_objects.<key>`. CSV retains `<key>.<field>`
column names: a single record remains a scalar, while multiple records use JSON
arrays in each cell, with `null` for a missing field to preserve record alignment.
Nested `custom_object_fields` entries also become `<key>.<field>` CSV columns.
If a custom field collides with a record attribute, it uses
`<key>.custom_object_fields.<field>` instead. The original nested JSON cell is
retained for compatibility; JSON exports are unchanged.

### User subscription verification

User-subscription upserts verify `include_comments` by reading the subscription
back after the single POST. If the value is absent or is not a JSON boolean,
the server returns `outcome_unknown`, not verified success. Do not automatically
repeat the write: it may already have applied. Check the subscription in Zendesk
before deciding whether another approved change is needed.

The [official response examples](https://developer.zendesk.com/api-reference/help_center/help-center-api/user_subscriptions/)
omit this field. A read-only check on 2026-09-12 also found it absent from all
eight returned follower records; the followings list was empty. This does not
prove what a nonempty followings response or a post-write response will contain.
No live subscription write was performed to test that boundary.

### CSV formula-like text

The shared CSV writer prefixes formula-like string values and headers with
an apostrophe, including leading whitespace/control characters and full-width
formula prefixes. Numeric values and JSON exports are unchanged. Programmatic
CSV consumers will see the apostrophe; choose JSON when exact strings matter.
This is defense in depth, not a guarantee across spreadsheet applications or
save/reopen cycles; see [OWASP CSV injection guidance](https://owasp.org/www-community/attacks/CSV_Injection).

### Ticket export page size versus total size

The MCP ticket-export tool writes a managed JSON/CSV file across multiple
Search Export pages, up to 100,000 tickets per artifact. Its `limit` controls
page size, unlike the total-result limit on interactive list/search tools.
Inspect `truncated` and resume `next_cursor` with the same query/projection.
The result is a ResourceLink, not an inline page of ticket records.

### Issue context attribution

Issue-context Markdown now includes per-comment ID, author ID/current role,
timestamp, channel, visibility, and side when available. The response also
sets `untrusted_user_content=true`, and the Markdown warns that Zendesk text
is data, not instructions. It still excludes the separate `html_body` field.
The generated Markdown redacts Bearer/Basic credential values and labeled
`zendesk_api_token`, `api_token`, `api_key`, `access_token`, `refresh_token`,
`client_secret`, and `password` assignments (including quoted values).
This is not a guarantee that arbitrary unlabeled secrets are detected;
review content before sharing it outside the support context. Ticket data
in Zendesk is not modified.

### Ticket list sorting

`zendesk_list_tickets` accepts `sort`: `id`, `-id`, `updated_at`, or
`-updated_at`. The minus prefix requests descending order. Keep the same
sort when resuming a cursor. Omitting it preserves the upstream default.
These four values passed limited live cursor requests; `created_at` was
rejected by the current endpoint and is not exposed as a supported option.

### Macro recipient safety

Macro `cc` actions and changed recipient/collaborator/CC/follower fields require
the public-write gate as well as local approval. This conservatively treats
recipient-list changes, including removals, as notification-sensitive.
Unchanged fields removed by the preview diff do not cause this escalation.
The [official actions reference](https://developer.zendesk.com/documentation/ticketing/reference-guides/actions-reference/)
defines `cc` as adding an agent or the current user to a ticket's CCs.

### Local audit logging on unsupported platforms

When descriptor-based private permissions (`os.fchmod`) are unavailable,
local audit persistence is unsupported: no audit directory/file is created,
and a static warning is logged without resource IDs or request contents.
The original MCP tool result is preserved. This does not provide Windows
audit persistence or change token/config permission checks; equivalent Windows
ACL support remains outstanding.

### Ticket conversation pagination

Conversation results retain `data.comments` and now include `has_more`,
`next_cursor`, and `truncated` within `data`. The default limit is 100,
maximum 1,000; follow the cursor to read the rest. Attachment metadata,
attachment ownership checks, and issue-context generation collect all
comment pages internally, failing rather than returning a partial result
if pagination stalls or exceeds the 100,000-comment safety ceiling.

Comments request side-loaded users and add `author_role` when the author
matches a returned user. A missing `side` becomes `customer` for end users,
`agent` for agents/admins, or `unknown` when the role cannot be determined.
This is the user's current role, not a historical role at comment creation.
Public/private visibility never determines the side; original comment fields
are preserved. Deleted or unavailable users do not trigger extra user lookups.

### Ticket closure approval

`zendesk_update_ticket` and `zendesk_set_ticket_status` no longer close a ticket
with standard-write permission alone. With `status=closed`, omission of
`execution_mode` creates a preview without an outbound write. Review and approve
the request with `zendesk approve <approval_request_id>`, then call the same
operation with `execution_mode=apply`, `approval_request_id` and `approval_token`.
Apply also requires standard write mode and `ZENDESK_ENABLE_DESTRUCTIVE_WRITES=true`.
The one-time approval binds the ticket ID and every submitted field; the status
shortcut uses the shared `zendesk_update_ticket` approval identity.

Other status/field updates keep their existing direct standard-write behavior;
approval options are rejected unless `status=closed`. Both tools now advertise
potentially destructive behavior to MCP clients. Real closure E2E is unverified.

### Ticket tag collision protection

Tag add/remove shortcuts now bind their update to the timestamp read with the
current tags, using `safe_update` and `updated_stamp`. A concurrent ticket change
returns `conflict` without automatic replay; inspect the current ticket before
requesting a fresh change. Invalid or missing timestamps stop the write, while
already-satisfied tag operations remain no-ops. This avoids replacing another
agent's tag changes with a stale snapshot.

### New ticket descriptions are internal notes

Ticket creation now explicitly sends `comment.public=false`. The description is
an internal note, not a public reply; this matches the standard-write safety
contract instead of relying on Zendesk defaults. Public replies still require
the dedicated public-reply tool, its write gate and local approval. Account
triggers may still send notifications for an internal note or field change.

### Approved Support search scope exception

When Support is enabled, OAuth now also requests broad `read` because Search and
Search Export reject the resource-specific grant. The user approved this policy
exception. `read` allows all GET resources available to the authenticated role,
not only ticket search. With Support disabled, broad `read` is not requested.
Broad `write` is never added; read-only defaults, per-operation write gates and
local human approvals are unchanged. API-token authentication is unchanged.

If the OAuth client's allowed scopes are restricted, its administrator must allow
`read`. Run the existing `zendesk login --subdomain ... --client-id ...` command
again and complete browser consent, then `zendesk check --probe`. Stored grants
are not silently expanded, rewritten or replaced by API-token fallback.

### Resource-specific scope corrections

Operations now requests the resource-specific read scopes needed by its own
tools even when Support is disabled: account settings (ticket fields), users,
groups, organizations, brands, tickets, views, macros, and triggers. It does not
request global `read` or `write`. Saved OAuth grants missing these permissions
require login again; credentials are not rewritten or silently expanded.
Git-Zen and Time Tracking now include `tickets:read` even when Support is disabled.
Guide includes `brands:read` to resolve `brand_id` to its Help Center host even
when Support and Operations are disabled.
Custom Objects includes `account_settings:read` for the ticket lookup-field
metadata needed by projection, even when Operations is disabled. This is in
addition to `custom_objects:read`; an older grant requires login again.
Ticket write scope is requested only for active Support, Operations, or Time
Tracking tools in standard write mode. Help Center write scope is requested only
when Guide, Community, or Badges is active and the corresponding write gate is on.
This corrects scope selection, not a guarantee that every endpoint is available
to every account or role. Search API's global-read requirement is handled by the
explicit Support exception above.

### Badge deletion previews

Badge deletion also deletes its assignments. The preview now includes
`cascade_assignment_count`, `irreversible: true`, and a hash of the assignment
IDs; these are bound to the local approval. Apply re-reads assignments and a
changed snapshot requires a new preview and approval. Failed or malformed
assignment reads cannot produce an approval with a misleading zero count.
The upstream API does not make the read and delete atomic: assignments can
still change after the final check. Avoid concurrent badge assignment changes
during deletion. No deleted badge or assignment is restored automatically.

### Time-tracking backends and pagination

For the Zendesk Time Tracking app, set both
`ZENDESK_TIME_TRACKING_TOTAL_FIELD_ID` and `ZENDESK_TIME_TRACKING_LAST_FIELD_ID`
to your account's existing Total time spent and Time spent last update field IDs.
Use distinct positive ASCII integer IDs (at most 20 digits); do not copy IDs
from another account. This does not install the app or create ticket fields.
Enable the `time_tracking` capability alongside your other required capabilities.

With both fields configured, `zendesk_get_time_tracking` returns
`backend: custom_fields`, `total_time_spent_sec`, `time_spent_last_update_sec`,
and the ticket's `updated_at`. Null field values represent zero recorded seconds;
missing, duplicate, negative, or invalid values are errors, not zero estimates.
This backend does not accept a cursor. `zendesk_log_time` keeps its `time_spent`
duration (for example `1h2m3s`, ASCII, maximum 64 characters) and required internal
`note`. It adds seconds to the total and sets last-update seconds in one guarded
ticket update. Standard write mode is required. A concurrent ticket modification
returns a conflict; it is not automatically retried. Inspect current ticket state
before retrying an uncertain write to avoid double counting.

With neither field configured, the existing audit-metadata backend remains active.
A partial or invalid pair is a configuration error, never a fallback.
The pagination behavior below applies only to that audit backend.

`zendesk_get_time_tracking` accepts `limit` (default 100, maximum 1,000)
and `cursor`. The limit counts scanned audits, not matching time entries.
`data.entries` is retained; `has_more`, `next_cursor`, and `truncated` are
added to `data`. Follow the cursor even when a batch contains no time entries.
The request includes `include_boundary_indicators=true` for cursor metadata.
The [Ticket Audits API](https://developer.zendesk.com/api-reference/ticketing/tickets/ticket_audits/)
documents global OAuth `read` as required and excludes archived tickets from
pagination support. This implementation does not expand OAuth scopes or infer
completion from missing pagination metadata. Archived-ticket behavior remains
unverified; malformed pagination returns an error rather than partial success.

### Ticket search pagination

`zendesk_search_tickets.limit` is now the total requested result count
(default 100, maximum 1,000), not just a single page size. Prefer `next_cursor`
with the same query and projection to resume, including within a page.
Legacy `page` remains supported with a page size of `min(limit, 100)`;
do not combine `cursor` with `page` other than 1. `next_page` is present only
when the continuation aligns with a page boundary. At the search API's
1,000-result ceiling, `has_more=true`, `truncated=true`, and no continuation
means use ticket export for further results. Offset positions can shift
when tickets change.

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

## Canonical tool names before 1.0.0

Successful outbound writes now include top-level `operation_state: "applied"`
as required by the result contract. Reads, previews and no-op results are not
marked applied. The existing nested subscription state remains for compatibility.
Publish verifies the requested locale's `draft=false` after the write; failed or
mismatched read-back returns non-retryable `outcome_unknown`, preserving the write
request ID when available. Inspect that translation before retrying.

Category and section list tools now accept an optional `locale` (for example,
`ko` or `en-us`). It must be enabled in the selected brand's Help Center.
Omitting it preserves the existing unlocalized list request. Resume a cursor
with the same brand and locale; refresh the MCP tool list to discover this input.

Both navigation tools accept `sort_by` (`position`, `created_at`, `updated_at`)
and `sort_order` (`asc`, `desc`). Section lists additionally accept `category_id`
to limit results to one category, with or without an explicit locale. All filters
remain fixed across pages; resume with the same category, brand, locale and sort.
Omitted sort parameters retain the API defaults. Invalid sort values and dot-only
category IDs are rejected before querying the brand. See official
[Categories](https://developer.zendesk.com/api-reference/help_center/help-center-api/categories/)
and [Sections](https://developer.zendesk.com/api-reference/help_center/help-center-api/sections/).

Help Center exports accept optional `category_id` and `section_id`. If both are
provided, the section scope takes precedence. IDs are kept as supplied and encoded
as path segments, not coerced to integers. Dot-only IDs (`.` or `..`) are rejected
by the common Help Center ID validator to prevent URL path normalization from
changing the requested scope. The selected brand and scope apply to
every exported page. Omit `locale` to use that brand's `default_locale` from the
enabled-locales API; missing/invalid default metadata is an error, not a fallback
to English. Explicit locales must also be enabled. Both the in-process export
and managed JSON/CSV file export use the same validated page reader.
The existing `max_articles` bound, streaming file storage and no-partial-file-on-
failure behavior are unchanged. See the official [article list routes](https://developer.zendesk.com/api-reference/help_center/help-center-api/articles/)
and [enabled/default locales](https://developer.zendesk.com/api-reference/help_center/help-center-api/translations/#list-enabled-locales-and-default-locale).

Article reads accept `locale="all"` to list translations for all enabled languages
of the selected brand. Disabled languages are excluded under the active-locale
policy. The server follows translation cursors to completion (shared complete-read
safety cap: 100,000 records), rejecting incomplete/error responses rather than
returning a partial list as complete. With `embed_images=true`, the existing
20 MB image budget is shared across the returned translations, not reset per
language. Images remain separate MCP image content, not rewritten HTML data URIs.

Set `include_metadata=true` to add `metadata.section_name`, `category_name` and
`author_name`, joined by IDs from official article sideloads. Missing/inaccessible
related names are null, not guessed. Metadata is read in the article endpoint's
default locale; it is not a promise of translated names for every translation.
The option defaults to false to preserve existing read calls and avoid fetching
unrequested related records. Raw IDs and normal article/translation bodies remain
unchanged; related names are also marked untrusted content. See official
[translation lists](https://developer.zendesk.com/api-reference/help_center/help-center-api/translations/#list-translations)
and [article sideloads](https://developer.zendesk.com/api-reference/help_center/help-center-api/articles/#show-article).

Article search now also supports Unified Search through a non-empty `locales`
array, with optional `query`, `brand_ids`, `category_ids` and `section_ids` arrays.
For example, `{"locales":["ko","en-us"],"brand_ids":[7,8],"limit":100}` searches
articles without text, using the API's internal ordering. Text queries are limited
to 500 characters. Category and section IDs retain their numeric/string identity;
commas inside a single ID are rejected rather than becoming additional filters.
The server verifies languages are enabled in at least one selected Help Center;
with no brand filter it discovers account brands first. Metadata lookup failures
are returned, not interpreted as permission to skip language validation.

Unified Search uses opaque cursors and at most 50 results per upstream page;
`limit` remains 1–1000 per tool call, and `next_cursor` resumes subsequent results.
The existing `query` + singular `locale`/`brand_id` path retains Article Search's
offset cursor and 1000-result ceiling. Do not combine singular and plural inputs,
move cursors between the two paths, or change filters while resuming.
Refresh the MCP catalog after updating. See the official
[Help Center Search API](https://developer.zendesk.com/api-reference/help_center/help-center-api/help_center_search/).

CSAT `backend=auto` now reads the account's `active_features` using
`GET /api/v2/account/settings.json` instead of guessing from the supplied filters.
Exactly one of `customer_satisfaction` and `customer_satisfaction_survey` must be
active. Missing/non-boolean metadata is an upstream error; neither or both active
returns `unsupported`. Authentication/permission failures are preserved without
trying another backend. Incompatible filters are rejected, never dropped.
An explicit `legacy` or `survey` skips detection and can retrieve historical data
even when that survey is no longer enabled. Use an explicit backend when resuming
across an account configuration change; do not move cursors between backends.

The `csat` capability now also requests `account_settings:read`. Existing OAuth
grants lacking this scope require login again; saved credentials are not rewritten
to pretend the scope was granted. API tokens still use the account user's access.
No CSAT settings are changed by detection. See the official
[Account Settings API](https://developer.zendesk.com/api-reference/ticketing/account-configuration/account_settings/).

The pre-release implementation used six names that differed from PRD section 8.
Clients must now use the canonical names below; the old names are not aliases.
Refresh the MCP tool list after updating. HTTP endpoints and write gates are unchanged.

| Previous pre-release name | Canonical name |
| --- | --- |
| `zendesk_apply_ticket_macro` | `zendesk_apply_macro` |
| `zendesk_list_guide_permission_groups` | `zendesk_list_permission_groups` |
| `zendesk_list_guide_user_segments` | `zendesk_list_user_segments` |
| `zendesk_create_badge_assignment` | `zendesk_assign_badge` |
| `zendesk_delete_badge_assignment` | `zendesk_unassign_badge` |
| `zendesk_upload_community_user_image` | `zendesk_upload_community_image` |

Pending approvals bound to an old name cannot authorize a new-name operation.
Run preview and the local approval command again; do not edit approval records.
The catalog still contains 101 tools, including the existing CSAT backend selector
and badge icon uploader now explicitly listed in section 8.

## Operations list filters

`zendesk_get_ticket_conversation` now accepts `source: "conversation_log"` to read messaging, bot and ticket events using the official conversation log endpoint.
This mode returns `data.events` (raw event fields plus the untrusted-content marker) and `data.source`; it does not flatten buttons, author objects, attachments or private metadata into comment text.
Events are requested in created-at order. Resume with the same source and returned cursor; never move a cursor between comments and conversation-log endpoints.
The default remains `comments`, including for attachment ownership checks. Log permission/rate-limit errors do not fall back to comments.
`source: "auto"` reads the ticket's boolean `from_messaging_channel` on the initial page: true selects the log, false selects comments. A missing or invalid flag stops with an error; there is no guessed fallback.
Auto returns the resolved `data.source`. Use that explicit value for every resumed request; auto rejects cursors to avoid moving continuation tokens across sources if ticket state changes.
Comment results now include `author_name` when the existing users sideload supplies it. Missing names are not guessed, and original fields remain intact.
HTML bodies containing images additionally receive `display_text`, with `[image]` or `[image: alt text]` markers in source order. Original HTML, rich content and attachments remain unchanged; no image URL is fetched.
This is an untrusted plain-text convenience field, not HTML sanitization. Malformed HTML that cannot be parsed retains its original data and sets `display_text_unavailable: true`.
No live conversation-log request was performed during implementation.
Reference: [Conversation Log](https://developer.zendesk.com/api-reference/ticketing/tickets/conversation_log/).

CSAT list/export input schemas now distinguish `legacy`, `survey`, and `auto` (the default).
Legacy rejects `ticket_id`/`responder_ids`; survey rejects `score`. Auto rejects a mixed score + survey-filter request before detecting the account backend.
Scores are restricted to the existing supported values, and responder lists must be non-empty.
Legacy dates now use timezone-bearing ISO-8601 `start_time`/`end_time` inputs (not epoch numbers); the adapter converts them to seconds. Replace pre-release legacy `created_at_start`/`created_at_end` calls with these canonical names.
Survey dates remain ISO-8601 `created_at_start`/`created_at_end`, converted to milliseconds. Auto accepts only the detected backend's date family. Mixed date families and unknown keys are rejected instead of ignored.
Explicit backend/filter mistakes are rejected by the MCP schema before a client is built; auto selection still requires runtime account detection and filter validation.

`zendesk_assign_ticket` additionally accepts `assignee_email`, containing a primary email address or literal `me`.
Do not combine it with `assignee_id`; `group_id` may still accompany either selector.
Email resolution checks all available search pages, compares the returned primary email case-insensitively, and requires one non-suspended agent/admin with a valid ID.
Secondary email identities are not resolved; use the primary address or explicit ID.
`me` resolves the authenticated user through `/api/v2/users/me.json`.
Read-only mode blocks before lookup. Ambiguous/missing/ineligible users, search failures, and the 10,000-candidate ceiling prevent mutation.
Resolved assignments use the existing standard-write update path and can still trigger Zendesk automations when actually enabled.
Local tests use simulated responses only; no production assignment was performed.

`zendesk_search_users` accepts `agents_only` and `exact_name` (both default false).
The first keeps users whose role is agent or admin; the second compares returned full names with the query using case-insensitive matching and trimming outer whitespace.
These are local filters on Zendesk search candidates, not new API search parameters. `limit` counts candidates inspected, not matches.
Keep the query and options unchanged when resuming. Empty matches may still have `has_more: true`; exhaust pages before considering uniqueness, and never choose the first of multiple matching names automatically.
The 10,000-candidate API ceiling and search indexing limitations still apply; this is not an exhaustive account-directory lookup.

`zendesk_list_ticket_forms` accepts optional `active`; omitting it keeps both active and inactive forms.
`zendesk_list_triggers` also accepts `active`, string `category_id`, `sort` (`alphabetical`, `created_at`, `updated_at`, `position`), and `sort_order` (`asc`, `desc`).
Keep these filters unchanged when resuming with a cursor.
Optional `include_usage: true` requests the 1-hour, 24-hour, 7-day and 30-day usage sideloads.
The default remains false. Zendesk requires Support Professional or Suite Growth or above for these statistics.
Inline statistics stay in `items`; any additional non-pagination response fields are retained, unchanged and in page order, in optional `sideloads`.
Missing values are not filled with zero and null values stay null. Errors are returned without retrying a less informative request.
The official reference does not give a usage response example: local tests prove lossless handling of simulated inline and root-level fields, not the exact live response shape.
See [Triggers](https://developer.zendesk.com/api-reference/ticketing/business-rules/triggers/) and [side-loading](https://developer.zendesk.com/documentation/api-basics/working-with-data/side_loading/).

### Community comment votes

`zendesk_list_community_votes` now accepts `post_id` plus `comment_id` to list votes for one post comment.
Post-only and user-only inputs are unchanged; user scope cannot be mixed with post/comment scope.
Keep both IDs unchanged when resuming `next_cursor`. This read-only operation reuses cursor pagination
and propagates Zendesk permission errors; it does not change votes or subscriptions.
See [Votes](https://developer.zendesk.com/api-reference/help_center/help-center-api/votes/).

### Community HTML write verification

Post/comment creation and updates containing `details`/`body` now read the saved resource after a successful write.
The returned body is Zendesk's current stored value, which may differ from the submitted HTML after normalization.
Successful verification returns `data.read_back_verified=true`; write request IDs are retained.
If the write succeeds but its resource cannot be verified, the tool returns `partial_success` with
`operation_state=applied`, `retryable=false`, and `read_back_verified=false` in error details.
When the resource ID is known, details include its fixed API path for inspection. Do not repeat the write automatically.
Preview, rejected writes and non-body updates do not perform this additional lookup.

### Community content presentation

Community read records with textual content retain their original fields and now include
`untrusted_user_content=true` and a `plain_text` map keyed by the original field name.
HTML `body`/`details` are converted without rendering or fetching images; image alt text is a placeholder.
Names, titles and descriptions remain literal text. List items, single resources and HTML write read-back
use the same presentation path. A parser failure retains raw HTML and lists the field in
`plain_text_unavailable` instead of claiming a successful conversion.
This is text extraction, not sanitization: both raw and derived values remain untrusted and must not be
executed, treated as instructions, or inserted as trusted HTML.
