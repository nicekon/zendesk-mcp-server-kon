import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from zendesk_mcp_server.auth import (
    ApiTokenAuthorization,
    OAuthStateStore,
    OAuthTokenStore,
    OAuthTokens,
)
from zendesk_mcp_server.config import ConfigurationError


def test_oauth_store_writes_user_only_file(tmp_path: Path):
    path = tmp_path / "oauth.json"
    store = OAuthTokenStore(path)

    store.save(OAuthTokens(access_token="access", refresh_token="refresh", expires_at=10))

    assert path.stat().st_mode & 0o777 == 0o600
    assert store.load().refresh_token == "refresh"
    assert not list(tmp_path.glob(".oauth.json.*"))


def test_oauth_store_rejects_group_readable_file(tmp_path: Path):
    path = tmp_path / "oauth.json"
    path.write_text('{"access_token":"access","refresh_token":"refresh","expires_at":10}')
    path.chmod(0o640)

    with pytest.raises(ConfigurationError, match="permissions"):
        OAuthTokenStore(path).load()


def test_api_token_header_uses_basic_auth_not_a_bearer_token():
    provider = ApiTokenAuthorization(email="agent@example.test", token="token")

    header = provider.headers()["Authorization"]
    assert header.startswith("Basic ")
    assert "token" not in header


def test_oauth_tokens_expire_with_a_safety_margin():
    assert OAuthTokens("access", "refresh", 1059).is_expired(now=1000) is True
    assert OAuthTokens("access", "refresh", 1061).is_expired(now=1000) is False


def test_oauth_refresh_response_rotates_tokens():
    from zendesk_mcp_server.auth import oauth_tokens_from_refresh_response

    assert oauth_tokens_from_refresh_response({"access_token": "new", "refresh_token": "rotated", "expires_in": 3600}, now=100) == OAuthTokens("new", "rotated", 3700)


def test_oauth_refresh_payload_uses_refresh_token_grant():
    from zendesk_mcp_server.auth import oauth_refresh_payload

    assert oauth_refresh_payload("client", "secret", "refresh") == {"grant_type": "refresh_token", "client_id": "client", "client_secret": "secret", "refresh_token": "refresh"}


def test_public_oauth_refresh_omits_client_secret():
    from zendesk_mcp_server.auth import oauth_refresh_payload

    assert oauth_refresh_payload("client", "", "refresh") == {
        "grant_type": "refresh_token",
        "client_id": "client",
        "refresh_token": "refresh",
    }


def test_pkce_challenge_matches_rfc_7636_vector():
    from zendesk_mcp_server.auth import pkce_challenge

    assert pkce_challenge("dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk") == "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"


def test_public_oauth_request_and_exchange_use_pkce_without_secret(tmp_path: Path):
    from zendesk_mcp_server.auth import create_oauth_authorization_request, exchange_oauth_authorization_code

    verifier = "a" * 43
    state_store = OAuthStateStore(tmp_path / "state.json")
    token_store = OAuthTokenStore(tmp_path / "tokens.json")
    request = create_oauth_authorization_request(
        "acme",
        "client",
        "http://127.0.0.1:3000/oauth/callback",
        ("tickets:read",),
        state_store,
        now=100,
        code_verifier=verifier,
    )
    query = parse_qs(urlsplit(request["authorization_url"]).query)
    payloads = []

    exchange_oauth_authorization_code(
        lambda payload: payloads.append(payload) or {"access_token": "access", "refresh_token": "refresh", "expires_in": 300},
        "client",
        "",
        "code",
        request["state"],
        "http://127.0.0.1:3000/oauth/callback",
        ("tickets:read",),
        state_store,
        token_store,
        now=101,
        code_verifier=verifier,
    )

    assert query["code_challenge_method"] == ["S256"]
    assert query["code_challenge"] == ["ZtNPunH49FD35FWYhT5Tv8I7vRKQJ8uxMaL0_9eHjNA"]
    assert payloads == [{
        "grant_type": "authorization_code",
        "code": "code",
        "client_id": "client",
        "redirect_uri": "http://127.0.0.1:3000/oauth/callback",
        "scope": "tickets:read",
        "code_verifier": verifier,
    }]


def test_oauth_redirect_with_invalid_loopback_port_is_a_configuration_error(tmp_path: Path):
    from zendesk_mcp_server.auth import create_oauth_authorization_request

    with pytest.raises(ConfigurationError) as error:
        create_oauth_authorization_request(
            "acme", "client", "http://127.0.0.1:not-a-port/oauth/callback",
            ("tickets:read",), OAuthStateStore(tmp_path / "state.json"), now=100,
            code_verifier="a" * 43,
        )
    assert error.value.code == "invalid_oauth_authorization"


def test_refresh_oauth_tokens_uses_injected_requester():
    from zendesk_mcp_server.auth import refresh_oauth_tokens
    assert refresh_oauth_tokens(lambda payload: {"access_token": "new", "refresh_token": payload["refresh_token"], "expires_in": 100}, "id", "secret", "refresh", now=1).access_token == "new"


def test_refresh_and_store_rotates_the_token_file(tmp_path: Path):
    from zendesk_mcp_server.auth import refresh_and_store_oauth_tokens
    store = OAuthTokenStore(tmp_path / "oauth.json")
    store.save(OAuthTokens("old", "refresh", 1))
    assert refresh_and_store_oauth_tokens(store, lambda _: {"access_token": "new", "refresh_token": "rotated", "expires_in": 100}, "id", "secret", now=1).access_token == "new"
    assert store.load().refresh_token == "rotated"


def test_refresh_preserves_saved_connection_identity(tmp_path: Path):
    from zendesk_mcp_server.auth import save_connection
    from zendesk_mcp_server.config import Settings

    path = tmp_path / "connection.json"
    settings = Settings.load({
        "ZENDESK_SUBDOMAIN": "acme",
        "ZENDESK_AUTH_MODE": "oauth",
        "ZENDESK_OAUTH_CLIENT_KIND": "public",
        "ZENDESK_OAUTH_CLIENT_ID": "client",
        "ZENDESK_OAUTH_TOKEN_STORE": str(path),
    })
    save_connection(settings, OAuthTokens("old", "refresh", 1))

    OAuthTokenStore(path).save(OAuthTokens("new", "rotated", 999))

    saved = json.loads(path.read_text())
    assert saved["schema_version"] == 1
    assert saved["subdomain"] == "acme"
    assert saved["client_id"] == "client"
    assert saved["access_token"] == "new"


def test_refresh_reuses_a_concurrently_rotated_token(tmp_path: Path):
    from zendesk_mcp_server.auth import refresh_and_store_oauth_tokens
    store = OAuthTokenStore(tmp_path / "oauth.json"); store.save(OAuthTokens("old", "refresh", 1)); calls = 0
    def request(_):
        nonlocal calls
        calls += 1
        return {"access_token": "new", "refresh_token": "rotated", "expires_in": 100}

    first = refresh_and_store_oauth_tokens(store, request, "id", "secret", now=1, expected_access_token="old")
    second = refresh_and_store_oauth_tokens(store, request, "id", "secret", now=1, expected_access_token="old")

    assert first.access_token == second.access_token == "new"
    assert calls == 1


def test_oauth_authorization_code_state_is_single_use_and_binds_redirect(tmp_path: Path):
    from zendesk_mcp_server.auth import OAuthStateStore, create_oauth_authorization_request, exchange_oauth_authorization_code
    state_store = OAuthStateStore(tmp_path / "oauth-state.json"); token_store = OAuthTokenStore(tmp_path / "oauth.json")
    request = create_oauth_authorization_request("acme", "client", "https://app.example.test/callback", ("tickets:read",), state_store, now=100)
    query = parse_qs(urlsplit(request["authorization_url"]).query)

    assert query["state"] == [request["state"]]
    tokens = exchange_oauth_authorization_code(lambda payload: {"access_token": "access", "refresh_token": "refresh", "expires_in": 300}, "client", "secret", "code", request["state"], "https://app.example.test/callback", ("tickets:read",), state_store, token_store, now=101)
    assert tokens.access_token == "access"
    with pytest.raises(ConfigurationError, match="state"):
        exchange_oauth_authorization_code(lambda _: pytest.fail("unexpected exchange"), "client", "secret", "code", request["state"], "https://app.example.test/callback", ("tickets:read",), state_store, token_store, now=102)


def test_settings_oauth_authorization_request_uses_calculated_scopes(tmp_path: Path):
    from zendesk_mcp_server.auth import create_settings_oauth_authorization_request
    from zendesk_mcp_server.config import Settings

    settings = Settings.load({
        "ZENDESK_SUBDOMAIN": "acme",
        "ZENDESK_AUTH_MODE": "oauth",
        "ZENDESK_OAUTH_CLIENT_ID": "client",
        "ZENDESK_OAUTH_CLIENT_SECRET": "secret",
        "ZENDESK_OAUTH_TOKEN_STORE": str(tmp_path / "oauth.json"),
        "ZENDESK_CAPABILITIES": "guide",
    })
    request = create_settings_oauth_authorization_request(settings, "https://app.example.test/callback", OAuthStateStore(tmp_path / "state.json"), now=100)

    assert parse_qs(urlsplit(request["authorization_url"]).query)["scope"] == ["hc:read"]


def test_settings_oauth_code_exchange_reuses_the_configured_state_and_token_stores(tmp_path: Path):
    from zendesk_mcp_server.auth import (
        create_settings_oauth_authorization_request,
        exchange_settings_oauth_authorization_code,
        oauth_state_store,
    )
    from zendesk_mcp_server.config import Settings

    token_path = tmp_path / "oauth.json"
    settings = Settings.load({
        "ZENDESK_SUBDOMAIN": "acme",
        "ZENDESK_AUTH_MODE": "oauth",
        "ZENDESK_OAUTH_CLIENT_ID": "client",
        "ZENDESK_OAUTH_CLIENT_SECRET": "secret",
        "ZENDESK_OAUTH_TOKEN_STORE": str(token_path),
    })
    request = create_settings_oauth_authorization_request(
        settings,
        "https://app.example.test/callback",
        oauth_state_store(settings),
        now=100,
    )

    tokens = exchange_settings_oauth_authorization_code(
        settings,
        "code",
        request["state"],
        "https://app.example.test/callback",
        requester=lambda _: {"access_token": "access", "refresh_token": "refresh", "expires_in": 300},
        now=101,
    )

    assert tokens.access_token == "access"
    assert OAuthTokenStore(token_path).load().refresh_token == "refresh"


def test_oauth_state_store_rejects_group_readable_file(tmp_path: Path):
    from zendesk_mcp_server.auth import OAuthStateStore
    store = OAuthStateStore(tmp_path / "oauth-state.json")
    store.create("https://app.example.test/callback", ("tickets:read",), now=1)
    store.path.chmod(0o644)

    with pytest.raises(ConfigurationError, match="permissions"):
        store.create("https://app.example.test/callback", ("tickets:read",), now=2)


def test_build_authorization_refreshes_expired_oauth_tokens(tmp_path: Path):
    from zendesk_mcp_server.auth import build_authorization
    from zendesk_mcp_server.config import Settings

    path = tmp_path / "oauth.json"; OAuthTokenStore(path).save(OAuthTokens("old", "refresh", 1))
    settings = Settings.load({"ZENDESK_SUBDOMAIN": "acme", "ZENDESK_AUTH_MODE": "oauth", "ZENDESK_OAUTH_CLIENT_ID": "id", "ZENDESK_OAUTH_CLIENT_SECRET": "secret", "ZENDESK_OAUTH_TOKEN_STORE": str(path)})
    authorization = build_authorization(settings, oauth_requester=lambda payload: {"access_token": "new", "refresh_token": "rotated", "expires_in": 300}, now=100)

    assert authorization.headers() == {"Authorization": "Bearer new"}
    assert OAuthTokenStore(path).load().refresh_token == "rotated"


def test_build_authorization_keeps_unexpired_oauth_token(tmp_path: Path):
    from zendesk_mcp_server.auth import build_authorization
    from zendesk_mcp_server.config import Settings

    path = tmp_path / "oauth.json"; OAuthTokenStore(path).save(OAuthTokens("current", "refresh", 1000))
    settings = Settings.load({"ZENDESK_SUBDOMAIN": "acme", "ZENDESK_AUTH_MODE": "oauth", "ZENDESK_OAUTH_CLIENT_ID": "id", "ZENDESK_OAUTH_CLIENT_SECRET": "secret", "ZENDESK_OAUTH_TOKEN_STORE": str(path)})
    authorization = build_authorization(settings, oauth_requester=lambda _: pytest.fail("unexpected refresh"), now=100)

    assert authorization.headers() == {"Authorization": "Bearer current"}
