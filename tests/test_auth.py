from pathlib import Path

import pytest

from zendesk_mcp_server.auth import (
    ApiTokenAuthorization,
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
