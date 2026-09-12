# OAuth Migration and Rollback Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the local credential migration and schema-only rollback required by PRD 16.2 and 17, without touching the user's live connection during development.

**Architecture:** Explicit CLI commands operate on named files only. Reuse OAuthTokenStore atomic persistence and refresh locking; validate source identity and token shape before writing. Rollback transforms the current credentials, never restores credentials from a backup.

**Tech Stack:** Python 3.10+, stdlib, existing pytest and OAuthTokenStore.

**Spec:** `docs/superpowers/specs/2026-09-04-unified-zendesk-mcp-design.md`, sections 16.2 and 17.

## Implementation evidence (2026-09-12)

- CLI conversion/rollback, private file validation, interrupted publication resume,
  current rotated-token preservation, conflicting-schema rejection and redacted
  output are implemented in `migration.py` and connected to `zendesk`.
- Publication uses an atomic no-clobber hard link from a fully fsynced temporary
  file, rather than replacing an existing destination. Repeat completed migrations
  refuse the existing destination; interrupted migrations reuse a matching backup.
- Backup expiry cleanup is lazy at next token use after seven days, not a timer
  while the server is stopped. Successful rollback cleans immediately or reports
  `backup_cleanup_pending` without misreporting the applied conversion as a failure.
- Independent review found a shared refresh-lock symlink/chmod vulnerability.
  A failing regression reproduced it; the shared lock now rejects symlinks,
  hardlinks and special files before chmod. The reviewer confirmed the fix.
- Full source suite: **889 passed**. A freshly built wheel installed into
  `/tmp/zendesk-migration-install.CDMR1L/venv`, with site-packages import asserted
  and repository pythonpath disabled, also passed the full **889 tests**.
- No real credential migration, Zendesk request, merge or release was performed.
  The original checklist below records the plan; these results are local evidence,
  not approval of the entire PRD or real-account rollback training.

## Global Constraints

- No production credential changes or network calls during development/tests.
- No auth fallback, broad scope expansion, automatic login or token output.
- Files containing credentials must be user-only; unsupported private storage platforms fail closed.
- Preserve source bytes on failed migration; do not overwrite an unrelated destination.
- Expired/unsupported legacy token shapes require reauthorization rather than fabricated expiry.

## Task 1: Explicit migration and rollback commands

**Files:** Create `src/zendesk_mcp_server/migration.py` and `tests/test_migration.py`; modify `src/zendesk_mcp_server/__init__.py`, `tests/test_cli.py`, `docs/migration.md`.

**Interfaces:** `migrate_oauth(source: Path, destination: Path, *, now: int) -> dict` and `rollback_oauth(source: Path, destination: Path, *, now: int) -> dict`. Return status metadata only, no token or client secret. CLI names: `migrate-oauth SOURCE DESTINATION`, `rollback-oauth SOURCE DESTINATION`.

- [ ] Inspect pinned upstream config writer and local Settings/OAuthTokenStore loaders before choosing the exact converted schema. The legacy access-token key is `oauth_token`; local key is `access_token`. Preserve client identity, current refresh token and expiry; never invent Public client status for a confidential client.
- [ ] Add a synthetic-file roundtrip test that fails before implementation:

```python
def test_rollback_uses_current_rotated_tokens(tmp_path):
    import json, os
    from zendesk_mcp_server.migration import migrate_oauth, rollback_oauth
    source = tmp_path / "legacy.json"
    source.write_text(json.dumps({"subdomain": "example", "client_id": "client", "client_secret": "secret", "oauth_token": "old-access", "refresh_token": "old-refresh", "expires_at": 2000}))
    os.chmod(source, 0o600)
    target = tmp_path / "current.json"
    migrate_oauth(source, target, now=1000)
    current = json.loads(target.read_text())
    current.update(access_token="new-access", refresh_token="new-refresh")
    target.write_text(json.dumps(current))
    restored = tmp_path / "rollback.json"
    rollback_oauth(target, restored, now=1001)
    value = json.loads(restored.read_text())
    assert value["oauth_token"] == "new-access"
    assert value["refresh_token"] == "new-refresh"
```

- [ ] Run `uv run pytest -q tests/test_migration.py`; confirm missing implementation failure.
- [ ] Implement explicit file validation, source locking, 0600 backup before conversion, atomic output, idempotent detection and collision refusal. Backup cleanup applies only to tool-owned validated backup metadata, at seven days or successful rollback; never read backup credentials for rollback. Unknown identity/schema/expiry must fail with a redacted reauthorization error.
- [ ] Add tests for private permissions, malformed JSON, symlinks, source/destination aliasing, interrupted output preserving source, destination collision, current credential rotation, expired backup rejection/cleanup and redacted CLI output. Run tests using synthetic paths only.
- [ ] Expose CLI commands without credential arguments; document stop-old-server prerequisite, explicit source/destination, backup lifetime, unsupported/non-expiring legacy cases, and configuration of the resulting store. Existing login remains unchanged.
- [ ] Run `uv run pytest -q`, build and isolated installed CLI checks. Review the bounded diff before commit. Do not claim real-user migration or full release completion.

## Acceptance boundary

This task fills a code omission rather than treating reauthentication advice as a migration tool. Image upload and Badges product detection are independent and must not block this local implementation. No release or live credential migration is authorized by this plan.
