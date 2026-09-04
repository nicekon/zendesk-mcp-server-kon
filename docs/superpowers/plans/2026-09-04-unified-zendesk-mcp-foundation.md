# Unified Zendesk MCP Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace import-time credential handling with a testable, read-only-by-default Zendesk MCP foundation that supports API token and OAuth configuration without sending a network request during handshake.

**Architecture:** Keep `server.py` to MCP registration and lifecycle only. Add small `config`, `contracts`, `auth`, and `client` modules: configuration selects exactly one credential mode, contracts normalize outcomes, auth creates only approved headers, and the client owns hostname validation, timeout, and one transport path. This phase exposes `zendesk_get_connection_status`; feature-domain tools are added in subsequent plans on top of this foundation.

**Tech Stack:** Python 3.10–3.12, MCP SDK, Pydantic, httpx, pytest, uv/hatchling.

**Spec:** `docs/superpowers/specs/2026-09-04-unified-zendesk-mcp-design.md`

## Global Constraints

- Target Python 3.10–3.12; no syntax or stdlib API newer than Python 3.10.
- Use one direct `httpx.Client`; do not retain Zenpy as a second request transport.
- Default configuration is read-only and unconfigured startup must perform zero network calls.
- API token remains environment-only; OAuth tokens live in a user-only file with mode `0600` and atomic replacement.
- In `auto`, complete OAuth selects OAuth; partial OAuth is a configuration error; authentication failure never falls back to another mode.
- Never log tokens, secrets, Authorization headers, ticket/article bodies, or email addresses.
- Transport uses connect 5s, read/write 30s, pool 5s. Reads retry at most twice; writes never auto-retry.
- Keep each task independently tested and committed. Do not add placeholders for Support, Guide, CSAT, or Community tools before their implementation plans.

---

## File Structure

| Path | Responsibility |
| --- | --- |
| `pyproject.toml` | Python/support matrix, direct dependencies, pytest development dependency and test configuration |
| `src/zendesk_mcp_server/config.py` | Parse environment/file configuration, choose credential mode, redact status |
| `src/zendesk_mcp_server/contracts.py` | Stable success/list/error envelope constructors and typed error codes |
| `src/zendesk_mcp_server/auth.py` | API-token/OAuth authorization header providers and atomic OAuth token store |
| `src/zendesk_mcp_server/client.py` | Validated tenant URL construction, timeout, retry, HTTP-to-contract error conversion |
| `src/zendesk_mcp_server/server.py` | Lazy app construction, configuration-free handshake, connection-status tool |
| `src/zendesk_mcp_server/__init__.py` | Side-effect-free console entry point |
| `tests/test_config.py` | Configuration and credential-selection contracts |
| `tests/test_auth.py` | Token-file permissions, atomic storage, header behavior |
| `tests/test_contracts.py` | Envelope and error mapping contracts |
| `tests/test_client.py` | Tenant host validation, read retry, write no-retry, HTTP error mapping |
| `tests/test_server.py` | Import/handshake safety and connection-status registration |
| `README.md`, `README.ko.md` | New configuration names and read-only startup behavior |

## Task 1: Establish the Python and pytest test harness

**Files:**
- Modify: `pyproject.toml`
- Create: `tests/test_smoke.py`

**Interfaces:**
- Produces a `pytest` test command: `uv run pytest`.
- Produces package compatibility metadata: `requires-python = ">=3.10"`.

- [ ] **Step 1: Write the failing smoke test**

```python
def test_package_can_import_without_zendesk_credentials(monkeypatch):
    monkeypatch.delenv("ZENDESK_SUBDOMAIN", raising=False)
    monkeypatch.delenv("ZENDESK_EMAIL", raising=False)
    monkeypatch.delenv("ZENDESK_API_TOKEN", raising=False)
    monkeypatch.delenv("ZENDESK_API_KEY", raising=False)

    import zendesk_mcp_server

    assert callable(zendesk_mcp_server.main)
```

- [ ] **Step 2: Run the test to verify the current failure**

Run: `uv run pytest tests/test_smoke.py -v`

Expected: FAIL because pytest is not configured/installed or because importing the package raises the current missing-credential error.

- [ ] **Step 3: Add the minimal test dependency and package metadata**

Update `pyproject.toml` to:

```toml
requires-python = ">=3.10"
dependencies = [
    "httpx>=0.28.1",
    "mcp>=1.1.2",
    "pydantic>=2.10",
    "python-dotenv>=1.0.1",
]

[dependency-groups]
dev = ["pytest>=8.3"]

[tool.pytest.ini_options]
testpaths = ["tests"]
```

Do not remove `zenpy` until Task 6 removes the last runtime import.

- [ ] **Step 4: Sync and run the intentionally still-failing test**

Run: `uv sync --group dev && uv run pytest tests/test_smoke.py -v`

Expected: FAIL only because the current `server.py` raises when credentials are missing.

- [ ] **Step 5: Commit the harness-only change**

```bash
git add pyproject.toml uv.lock tests/test_smoke.py
git commit -m "test: add Zendesk MCP test harness"
```

## Task 2: Add explicit configuration and credential selection

**Files:**
- Create: `src/zendesk_mcp_server/config.py`
- Modify: `src/zendesk_mcp_server/__init__.py`
- Create: `tests/test_config.py`

**Interfaces:**
- Produces `AuthMode(str, Enum)` with `AUTO`, `API_TOKEN`, and `OAUTH`.
- Produces `Settings.load(environ: Mapping[str, str], oauth_config_path: Path | None = None) -> Settings`.
- Produces `Settings.connection_status() -> dict[str, object]`, which contains no secret or email value.
- Raises `ConfigurationError(code: str, message: str)` for invalid combinations.

- [ ] **Step 1: Write failing configuration tests**

```python
from zendesk_mcp_server.config import AuthMode, ConfigurationError, Settings


def test_auto_uses_api_token_when_no_oauth_settings_exist():
    settings = Settings.load(
        {
            "ZENDESK_SUBDOMAIN": "acme",
            "ZENDESK_EMAIL": "agent@example.test",
            "ZENDESK_API_TOKEN": "secret",
        }
    )

    assert settings.auth_mode is AuthMode.API_TOKEN
    assert settings.connection_status()["configured"] is True
    assert "secret" not in repr(settings.connection_status())


def test_auto_rejects_partial_oauth_without_token_fallback():
    with pytest.raises(ConfigurationError, match="incomplete OAuth"):
        Settings.load(
            {
                "ZENDESK_SUBDOMAIN": "acme",
                "ZENDESK_EMAIL": "agent@example.test",
                "ZENDESK_API_TOKEN": "secret",
                "ZENDESK_OAUTH_CLIENT_ID": "client-id",
            }
        )


def test_empty_environment_is_unconfigured_not_an_import_error():
    settings = Settings.load({})

    assert settings.connection_status() == {
        "configured": False,
        "auth_mode": None,
        "write_mode": "read_only",
    }
```

- [ ] **Step 2: Run the tests to verify they fail because the module is absent**

Run: `uv run pytest tests/test_config.py -v`

Expected: FAIL with `ModuleNotFoundError: zendesk_mcp_server.config`.

- [ ] **Step 3: Implement the smallest configuration model**

Implement frozen dataclasses and explicit parsing only. Required API-token fields are subdomain, email, and `ZENDESK_API_TOKEN`; read deprecated `ZENDESK_API_KEY` only to raise a migration-focused `ConfigurationError`, never as an active credential. OAuth is complete only when all of client ID, client secret, and token-store path are present. Normalize and validate subdomain with `^[a-z0-9][a-z0-9-]{0,62}$`.

Make the console entry point lazy in the same step: `zendesk_mcp_server.main()` imports
`server.main` only when the console command executes. This is the smallest change that
makes Task 1's import-safety test pass without changing the legacy server behavior yet.

```python
class ConfigurationError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class Settings:
    subdomain: str | None
    auth_mode: AuthMode | None
    write_mode: str

    @classmethod
    def load(cls, environ: Mapping[str, str], oauth_config_path: Path | None = None) -> "Settings":
        ...
```

- [ ] **Step 4: Run configuration tests**

Run: `uv run pytest tests/test_config.py -v`

Expected: PASS.

- [ ] **Step 5: Commit configuration parsing**

```bash
git add src/zendesk_mcp_server/config.py src/zendesk_mcp_server/__init__.py tests/test_config.py
git commit -m "feat: add explicit Zendesk configuration"
```

## Task 3: Define normalized outcomes and errors

**Files:**
- Create: `src/zendesk_mcp_server/contracts.py`
- Create: `tests/test_contracts.py`

**Interfaces:**
- Produces `ErrorCode(str, Enum)` including `validation_error`, `authentication_failed`, `permission_denied`, `not_found`, `conflict`, `rate_limited`, `upstream_error`, `timeout`, `not_configured`, and `write_disabled`.
- Produces `success(data: Mapping[str, object], request_id: str | None = None) -> dict[str, object]`.
- Produces `failure(code: ErrorCode, message: str, *, retryable: bool = False, operation_state: str = "not_applied") -> dict[str, object]`.

- [ ] **Step 1: Write failing envelope tests**

```python
from zendesk_mcp_server.contracts import ErrorCode, failure, success


def test_success_has_stable_envelope():
    assert success({"id": 42}, request_id="req-1") == {
        "ok": True,
        "data": {"id": 42},
        "request_id": "req-1",
    }


def test_failure_never_uses_a_success_string_for_an_error():
    result = failure(ErrorCode.NOT_CONFIGURED, "Zendesk is not configured")

    assert result["ok"] is False
    assert result["error"]["code"] == "not_configured"
    assert result["error"]["operation_state"] == "not_applied"
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_contracts.py -v`

Expected: FAIL with `ModuleNotFoundError` for `contracts`.

- [ ] **Step 3: Implement only the envelope constructors and enum**

Use plain dictionaries so MCP serialization stays direct. Do not add per-tool result classes yet.

- [ ] **Step 4: Run contract tests**

Run: `uv run pytest tests/test_contracts.py -v`

Expected: PASS.

- [ ] **Step 5: Commit normalized contracts**

```bash
git add src/zendesk_mcp_server/contracts.py tests/test_contracts.py
git commit -m "feat: add normalized MCP result contracts"
```

## Task 4: Implement safe authorization providers and OAuth token storage

**Files:**
- Create: `src/zendesk_mcp_server/auth.py`
- Create: `tests/test_auth.py`

**Interfaces:**
- Consumes: `Settings` and `ConfigurationError` from `config.py`.
- Produces `AuthorizationProvider.headers() -> dict[str, str]`.
- Produces `OAuthTokenStore.load() -> OAuthTokens` and `OAuthTokenStore.save(tokens: OAuthTokens) -> None`.
- Produces `build_authorization(settings: Settings) -> AuthorizationProvider | None`.

- [ ] **Step 1: Write failing authorization and file-safety tests**

```python
from pathlib import Path

from zendesk_mcp_server.auth import ApiTokenAuthorization, OAuthTokenStore, OAuthTokens


def test_oauth_store_writes_user_only_file(tmp_path: Path):
    path = tmp_path / "oauth.json"
    store = OAuthTokenStore(path)

    store.save(OAuthTokens(access_token="access", refresh_token="refresh", expires_at=10))

    assert path.stat().st_mode & 0o777 == 0o600
    assert store.load().refresh_token == "refresh"


def test_api_token_header_uses_basic_auth_not_a_bearer_token():
    provider = ApiTokenAuthorization(email="agent@example.test", token="token")

    assert provider.headers()["Authorization"].startswith("Basic ")
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_auth.py -v`

Expected: FAIL with `ModuleNotFoundError` for `auth`.

- [ ] **Step 3: Implement the narrow providers and store**

Use `base64.b64encode(f"{email}/token:{token}".encode("utf-8"))` for API token authorization. Serialize OAuth token fields as JSON; write to a same-directory temporary file with `0o600`, `fsync`, then `Path.replace`. Reject an OAuth file with permissions broader than `0o600` rather than silently using it. Do not implement browser authorization or refresh HTTP calls in this phase.

- [ ] **Step 4: Run authorization tests**

Run: `uv run pytest tests/test_auth.py -v`

Expected: PASS.

- [ ] **Step 5: Commit safe authorization primitives**

```bash
git add src/zendesk_mcp_server/auth.py tests/test_auth.py
git commit -m "feat: add safe Zendesk authorization primitives"
```

## Task 5: Add the single Zendesk HTTP transport

**Files:**
- Create: `src/zendesk_mcp_server/client.py`
- Create: `tests/test_client.py`

**Interfaces:**
- Consumes: `AuthorizationProvider` and outcome constructors.
- Produces `ZendeskClient(settings: Settings, authorization: AuthorizationProvider, transport: httpx.BaseTransport | None = None, sleep: Callable[[float], None] = time.sleep)`.
- Produces `ZendeskClient.get(path: str, *, params: Mapping[str, str] | None = None) -> dict[str, object]`.
- Produces `ZendeskClient.request(method: str, path: str, *, json_body: Mapping[str, object] | None = None) -> dict[str, object]`.

- [ ] **Step 1: Write failing client behavior tests**

```python
import httpx

from zendesk_mcp_server.client import ZendeskClient


def test_read_retries_a_rate_limit_at_most_twice(settings, authorization):
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls < 3:
            return httpx.Response(429, headers={"Retry-After": "0"}, request=request)
        return httpx.Response(200, json={"users": []}, request=request)

    client = ZendeskClient(settings, authorization, transport=httpx.MockTransport(handler), sleep=lambda _: None)

    assert client.get("/api/v2/users/me.json")["ok"] is True
    assert calls == 3


def test_write_does_not_retry_timeout(settings, authorization):
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ReadTimeout("timed out", request=request)

    client = ZendeskClient(settings, authorization, transport=httpx.MockTransport(handler))

    assert client.request("POST", "/api/v2/tickets.json", json_body={})["error"]["code"] == "timeout"
    assert calls == 1
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_client.py -v`

Expected: FAIL because `client.py` does not exist.

- [ ] **Step 3: Implement the minimal transport**

Construct the base URL only from a validated subdomain as `https://{subdomain}.zendesk.com`. Reject absolute paths, paths not beginning with `/`, and a `Location`/pagination host that differs from the tenant hostname. Use an `httpx.Timeout(connect=5, read=30, write=30, pool=5)`. Map 400→`validation_error`, 401→`authentication_failed`, 403→`permission_denied`, 404→`not_found`, 409/412→`conflict`, 422→`validation_error`, 429→`rate_limited`, other 5xx→`upstream_error`, and `httpx.TimeoutException`→`timeout`.

For GET/HEAD only, make one initial request plus at most two retry attempts. Retry 429 only if a parsed `Retry-After` is between 0 and 30 seconds; retry transient 5xx/timeouts with bounded jitter. POST/PUT/PATCH/DELETE return a normalized error after one attempt.

- [ ] **Step 4: Add mapping and hostname tests, then run the full client file**

Add assertions for `400`, `403`, `404`, `409`, `422`, a foreign absolute path rejection, and retry exhaustion. Run: `uv run pytest tests/test_client.py -v`

Expected: PASS.

- [ ] **Step 5: Commit common transport**

```bash
git add src/zendesk_mcp_server/client.py tests/test_client.py
git commit -m "feat: add common Zendesk HTTP transport"
```

## Task 6: Make the MCP server lazy and expose connection status

**Files:**
- Modify: `src/zendesk_mcp_server/__init__.py`
- Modify: `src/zendesk_mcp_server/server.py`
- Create: `tests/test_server.py`
- Delete: `src/zendesk_mcp_server/zendesk_client.py`

**Interfaces:**
- Consumes: `Settings.load`, `success`, `failure`.
- Produces `create_server(environ: Mapping[str, str] | None = None) -> Server`.
- Produces `build_connection_status(environ: Mapping[str, str]) -> dict[str, object]`.
- Produces the sole foundation tool `zendesk_get_connection_status` with no required input.
- Preserves prompts but does not expose the KB resource until its planned client is implemented.

- [ ] **Step 1: Write failing server safety tests**

```python
import importlib

def test_server_import_does_not_require_credentials(monkeypatch):
    for name in ("ZENDESK_SUBDOMAIN", "ZENDESK_EMAIL", "ZENDESK_API_TOKEN", "ZENDESK_API_KEY"):
        monkeypatch.delenv(name, raising=False)

    module = importlib.import_module("zendesk_mcp_server.server")

    assert callable(module.create_server)


def test_connection_status_is_available_without_configuration():
    from zendesk_mcp_server.server import build_connection_status

    assert build_connection_status({}) == {
        "ok": True,
        "data": {"configured": False, "auth_mode": None, "write_mode": "read_only"},
    }
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_server.py tests/test_smoke.py -v`

Expected: FAIL because the legacy module imports Zenpy and raises at module import time.

- [ ] **Step 3: Replace import-time globals with application construction**

Remove `Zenpy`, `requests`, `cachetools`, global environment reads, and `logging.basicConfig` from module scope. Implement `create_server` that registers the two existing safe prompts and `zendesk_get_connection_status`; `handle_call_tool` returns a JSON `TextContent` envelope. `main()` calls `create_server()` only after stdio starts. A missing/invalid configuration returns structured status and never creates `ZendeskClient` or opens a socket.

- [ ] **Step 4: Remove the obsolete dual transport**

Delete `zendesk_client.py`, remove the direct `zenpy` dependency, lock with `uv lock`, and update the console entry point so importing `zendesk_mcp_server` does not import `server` eagerly.

- [ ] **Step 5: Run server and full suite**

Run: `uv sync --group dev && uv run pytest -v && uv run python -m compileall -q src`

Expected: PASS. The package import and MCP server module import succeed with no Zendesk variables set.

- [ ] **Step 6: Commit the lazy MCP foundation**

```bash
git add src/zendesk_mcp_server pyproject.toml uv.lock tests
git commit -m "feat: add safe MCP foundation"
```

## Task 7: Document the new safe startup and verify packaging

**Files:**
- Modify: `README.md`
- Modify: `README.ko.md`
- Modify: `docs/superpowers/specs/2026-09-04-unified-zendesk-mcp-design.md` only if an implementation decision differs from the approved PRD

**Interfaces:**
- Documents `ZENDESK_API_TOKEN` as the supported API-token setting.
- Documents that no credentials starts a read-only server and that `zendesk_get_connection_status` reports configuration without secret values.

- [ ] **Step 1: Review the obsolete setup instructions**

Confirm that both READMEs still require `ZENDESK_API_KEY` and describe the
legacy ticket and Community tools. Human-facing documentation is reviewed as
prose; do not add a brittle source-text test for it.

- [ ] **Step 2: Update both READMEs minimally**

Replace only obsolete configuration instructions. State that OAuth browser authorization, ticket tools, and Community tools are delivered in later phases; do not claim 1.0.0 is complete.

- [ ] **Step 3: Run package and handshake checks**

Run: `uv run pytest -v && uv build && uv run python -c "from zendesk_mcp_server.server import create_server; assert create_server({})"`

Expected: tests/build pass and server construction requires no Zendesk credentials or network access.

- [ ] **Step 4: Commit documentation and packaging evidence**

```bash
git add README.md README.ko.md
git commit -m "docs: describe safe Zendesk MCP setup"
```

## Self-Review

- **Spec coverage for this phase:** AUTH-01 through AUTH-07, HTTP-01 through HTTP-06, the standard success/error envelope, configuration-free handshake, Python 3.10–3.12 target, and initial read-only `zendesk_get_connection_status` are covered by Tasks 1–7.
- **Deferred deliberately:** ticket, Help Center, CSAT, attachment, Community, macro, prompt-resource, approval-token, and packaging-registry feature surfaces each depend on this transport and receive separate plans after Task 7 passes. No deferred surface is represented by a callable placeholder.
- **Placeholder scan:** every code task has a failing test, exact command, implementation boundary, verification command, and commit.
- **Type consistency:** `Settings`/`ConfigurationError` originate in `config.py`, authorization types in `auth.py`, normalized dictionaries in `contracts.py`, and `ZendeskClient` consumes only these declared interfaces.
