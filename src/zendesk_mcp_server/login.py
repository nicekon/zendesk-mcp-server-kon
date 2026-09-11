"""Interactive OAuth login through a temporary loopback callback."""

from __future__ import annotations

import hmac
import secrets
import time
import webbrowser
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlsplit

import httpx

from .auth import (
    OAuthTokens,
    create_oauth_authorization_request,
    exchange_oauth_authorization_code,
    save_connection,
    _oauth_refresh_requester,
)
from .config import AuthMode, ConfigurationError, Settings


class _LoopbackHTTPServer(HTTPServer):
    request_timeout = 1.0

    def get_request(self):
        request, address = super().get_request()
        request.settimeout(self.request_timeout)
        return request, address


class _LoginStateStore:
    def __init__(self) -> None:
        self._state: str | None = None
        self._redirect_uri = ""
        self._scopes: tuple[str, ...] = ()
        self._expires_at = 0

    def create(self, redirect_uri: str, scopes: tuple[str, ...], *, now: int) -> str:
        self._state = secrets.token_urlsafe(32)
        self._redirect_uri = redirect_uri
        self._scopes = scopes
        self._expires_at = now + 600
        return self._state

    def consume(self, state: str, redirect_uri: str, scopes: tuple[str, ...], *, now: int) -> None:
        valid = (
            self._state is not None
            and hmac.compare_digest(self._state, state)
            and hmac.compare_digest(self._redirect_uri, redirect_uri)
            and self._scopes == scopes
            and now <= self._expires_at
        )
        self._state = None
        if not valid:
            raise ConfigurationError("invalid_oauth_state", "OAuth state, redirect URI, or scopes are invalid")


def login(
    settings: Settings,
    *,
    port: int = 3000,
    timeout: float = 180,
    browser_open: Callable[[str], bool] = webbrowser.open,
    token_requester: Callable[[dict[str, str]], object] | None = None,
    user_requester: Callable[[str, str], object] | None = None,
    now: Callable[[], int] = lambda: int(time.time()),
) -> None:
    if settings.auth_mode is not AuthMode.OAUTH or settings.oauth is None or settings.oauth.client_kind != "public" or settings.subdomain is None:
        raise ConfigurationError("invalid_oauth_configuration", "Login requires public OAuth configuration")
    if not isinstance(port, int) or isinstance(port, bool) or not 0 <= port <= 65535 or timeout <= 0:
        raise ConfigurationError("invalid_oauth_callback", "OAuth callback port or timeout is invalid")

    result: dict[str, str] = {}
    expected_state = ""

    class CallbackHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            parsed = urlsplit(self.path)
            query = parse_qs(parsed.query, keep_blank_values=True)
            expected_host = f"127.0.0.1:{self.server.server_port}"
            allowed_query = {"state", "code"} if "code" in query else {"state", "error"}
            valid_shape = (
                self.headers.get("Host") == expected_host
                and parsed.path == "/oauth/callback"
                and not parsed.fragment
                and set(query) == allowed_query
                and len(query.get("state", [])) == 1
                and query["state"][0].isascii()
                and hmac.compare_digest(query["state"][0], expected_state)
                and ((len(query.get("code", [])) == 1 and "error" not in query) or (len(query.get("error", [])) == 1 and "code" not in query))
                and all(value for values in query.values() for value in values)
            )
            if not valid_shape:
                self._respond(400, "Invalid OAuth callback. You can return to the terminal.")
                return
            if "error" in query:
                result["error"] = query["error"][0]
                self._respond(400, "Zendesk access was not approved. You can return to the terminal.")
                return
            result.update(code=query["code"][0], state=query["state"][0])
            self._respond(200, "Zendesk authorization received. You can close this tab.")

        def _respond(self, status: int, message: str) -> None:
            body = message.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            return

    try:
        server = _LoopbackHTTPServer(("127.0.0.1", port), CallbackHandler)
        server.request_timeout = min(1.0, timeout)
    except OSError as error:
        raise ConfigurationError("oauth_callback_unavailable", "OAuth callback port is unavailable") from error

    try:
        redirect_uri = f"http://127.0.0.1:{server.server_port}/oauth/callback"
        verifier = secrets.token_urlsafe(64)
        state_store = _LoginStateStore()
        request = create_oauth_authorization_request(
            settings.subdomain,
            settings.oauth.client_id,
            redirect_uri,
            settings.oauth.scopes,
            state_store,
            now=now(),
            code_verifier=verifier,
        )
        expected_state = request["state"]
        if not browser_open(request["authorization_url"]):
            print(f"Open this URL to continue: {request['authorization_url']}")

        deadline = time.monotonic() + timeout
        while not result and time.monotonic() < deadline:
            server.timeout = min(0.1, max(0.001, deadline - time.monotonic()))
            server.handle_request()
        if not result:
            raise ConfigurationError("oauth_callback_timeout", "OAuth login timed out")
        if "error" in result:
            raise ConfigurationError("oauth_access_denied", "Zendesk access was not approved")

        tokens = exchange_oauth_authorization_code(
            token_requester or _oauth_refresh_requester(settings.subdomain),
            settings.oauth.client_id,
            "",
            result["code"],
            result["state"],
            redirect_uri,
            settings.oauth.scopes,
            state_store,
            None,
            now=now(),
            code_verifier=verifier,
        )
        verified = (user_requester or _request_current_user)(settings.subdomain, tokens.access_token)
        if not isinstance(verified, dict) or not isinstance(verified.get("user"), dict) or not verified["user"].get("id"):
            raise ConfigurationError("oauth_probe_failed", "OAuth user verification failed")
        save_connection(settings, tokens)
    finally:
        server.server_close()


def _request_current_user(subdomain: str, access_token: str) -> object:
    try:
        response = httpx.get(
            f"https://{subdomain}.zendesk.com/api/v2/users/me.json",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=httpx.Timeout(connect=5.0, read=30.0, write=30.0, pool=5.0),
        )
        response.raise_for_status()
        return response.json()
    except (httpx.HTTPError, ValueError) as error:
        raise ConfigurationError("oauth_probe_failed", "OAuth user verification failed") from error
