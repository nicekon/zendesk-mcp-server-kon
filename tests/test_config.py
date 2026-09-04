import pytest

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
    assert settings.connection_status() == {
        "configured": True,
        "auth_mode": "api_token",
        "write_mode": "read_only",
        "subdomain": "acme",
    }
    assert "secret" not in repr(settings.connection_status())
    assert "agent@example.test" not in repr(settings.connection_status())


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


def test_auto_prefers_complete_oauth_without_api_token():
    settings = Settings.load(
        {
            "ZENDESK_SUBDOMAIN": "acme",
            "ZENDESK_OAUTH_CLIENT_ID": "client-id",
            "ZENDESK_OAUTH_CLIENT_SECRET": "client-secret",
            "ZENDESK_OAUTH_TOKEN_STORE": "/tmp/zendesk-oauth.json",
        }
    )

    assert settings.auth_mode is AuthMode.OAUTH


def test_empty_environment_is_unconfigured_not_an_import_error():
    settings = Settings.load({})

    assert settings.connection_status() == {
        "configured": False,
        "auth_mode": None,
        "write_mode": "read_only",
    }


def test_deprecated_api_key_requires_explicit_migration():
    with pytest.raises(ConfigurationError, match="ZENDESK_API_TOKEN"):
        Settings.load(
            {
                "ZENDESK_SUBDOMAIN": "acme",
                "ZENDESK_EMAIL": "agent@example.test",
                "ZENDESK_API_KEY": "old-secret",
            }
        )
