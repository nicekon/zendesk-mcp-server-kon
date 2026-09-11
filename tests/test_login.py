import threading
import socket
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit
from urllib.request import urlopen

import pytest

from zendesk_mcp_server.auth import OAuthTokenStore
from zendesk_mcp_server.config import ConfigurationError, Settings


def _settings(path: Path) -> Settings:
    return Settings.load({
        "ZENDESK_SUBDOMAIN": "acme",
        "ZENDESK_AUTH_MODE": "oauth",
        "ZENDESK_OAUTH_CLIENT_KIND": "public",
        "ZENDESK_OAUTH_CLIENT_ID": "client",
        "ZENDESK_OAUTH_TOKEN_STORE": str(path),
    })


def _approve_in_browser(authorization_url: str) -> bool:
    query = parse_qs(urlsplit(authorization_url).query)
    redirect = urlsplit(query["redirect_uri"][0])
    callback = urlunsplit((
        redirect.scheme,
        redirect.netloc,
        redirect.path,
        urlencode({"code": "authorization-code", "state": query["state"][0]}),
        "",
    ))
    threading.Thread(target=lambda: urlopen(callback, timeout=2).read(), daemon=True).start()
    return True


def test_login_receives_loopback_callback_and_saves_verified_connection(tmp_path: Path):
    from zendesk_mcp_server.login import login

    token_payloads = []

    login(
        _settings(tmp_path / "connection.json"),
        port=0,
        timeout=2,
        browser_open=_approve_in_browser,
        token_requester=lambda payload: token_payloads.append(payload) or {
            "access_token": "access", "refresh_token": "refresh", "expires_in": 300},
        user_requester=lambda subdomain, token: {"user": {"id": 7, "role": "admin"}},
        now=lambda: 100,
    )

    assert token_payloads[0]["grant_type"] == "authorization_code"
    assert "client_secret" not in token_payloads[0]
    assert len(token_payloads[0]["code_verifier"]) >= 43
    assert OAuthTokenStore(tmp_path / "connection.json").load().access_token == "access"


def test_login_does_not_replace_connection_when_user_probe_fails(tmp_path: Path):
    from zendesk_mcp_server.auth import OAuthTokens, save_connection
    from zendesk_mcp_server.login import login

    path = tmp_path / "connection.json"
    settings = _settings(path)
    save_connection(settings, OAuthTokens("old", "old-refresh", 999))

    with pytest.raises(ConfigurationError, match="verification"):
        login(
            settings,
            port=0,
            timeout=2,
            browser_open=_approve_in_browser,
            token_requester=lambda _: {"access_token": "new", "refresh_token": "new-refresh", "expires_in": 300},
            user_requester=lambda subdomain, token: (_ for _ in ()).throw(ConfigurationError("probe_failed", "OAuth user verification failed")),
            now=lambda: 100,
        )

    assert OAuthTokenStore(path).load().access_token == "old"


def test_login_times_out_and_closes_listener(tmp_path: Path):
    from zendesk_mcp_server.login import login

    with pytest.raises(ConfigurationError, match="timed out"):
        login(
            _settings(tmp_path / "connection.json"),
            port=0,
            timeout=0.01,
            browser_open=lambda _: True,
            token_requester=lambda _: pytest.fail("unexpected token exchange"),
            user_requester=lambda *_: pytest.fail("unexpected probe"),
        )


def test_slow_callback_connection_cannot_extend_login_timeout(tmp_path: Path):
    from zendesk_mcp_server.login import login

    clients = []
    errors = []

    def hold_incomplete_request(authorization_url: str) -> bool:
        redirect = urlsplit(parse_qs(urlsplit(authorization_url).query)["redirect_uri"][0])
        client = socket.create_connection(("127.0.0.1", redirect.port), timeout=1)
        client.sendall(b"GET /oauth/callback HTTP/1.1")
        clients.append(client)
        return True

    def run_login() -> None:
        try:
            login(
                _settings(tmp_path / "connection.json"),
                port=0,
                timeout=0.05,
                browser_open=hold_incomplete_request,
                token_requester=lambda _: pytest.fail("unexpected token exchange"),
                user_requester=lambda *_: pytest.fail("unexpected probe"),
            )
        except ConfigurationError as error:
            errors.append(error)

    thread = threading.Thread(target=run_login, daemon=True)
    thread.start()
    thread.join(0.3)
    completed_within_deadline = not thread.is_alive()
    for client in clients:
        client.close()
    thread.join(1)

    assert completed_within_deadline is True
    assert errors[0].code == "oauth_callback_timeout"
