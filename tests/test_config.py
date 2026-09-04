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
        "active_write_gates": [],
        "capabilities": ["community", "guide", "operations", "support"],
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
        "active_write_gates": [],
        "capabilities": ["community", "guide", "operations", "support"],
    }


def test_capabilities_default_and_reject_unknown_values():
    assert Settings.load({}).capabilities == frozenset({"support", "operations", "guide", "community"})
    with pytest.raises(ConfigurationError, match="capabilities"):
        Settings.load({"ZENDESK_CAPABILITIES": "support,unknown"})


def test_deprecated_api_key_requires_explicit_migration():
    with pytest.raises(ConfigurationError, match="ZENDESK_API_TOKEN"):
        Settings.load(
            {
                "ZENDESK_SUBDOMAIN": "acme",
                "ZENDESK_EMAIL": "agent@example.test",
                "ZENDESK_API_KEY": "old-secret",
            }
        )


def test_write_gates_are_disabled_by_default_and_redacted_in_status():
    settings = Settings.load(
        {
            "ZENDESK_SUBDOMAIN": "acme",
            "ZENDESK_EMAIL": "agent@example.test",
            "ZENDESK_API_TOKEN": "secret",
            "ZENDESK_WRITE_MODE": "standard",
            "ZENDESK_ENABLE_PUBLIC_WRITES": "true",
        }
    )

    assert settings.public_writes_enabled is True
    assert settings.destructive_writes_enabled is False
    assert settings.connection_status()["active_write_gates"] == ["standard", "public"]


def test_external_upload_root_is_local_configuration_not_status_data(tmp_path):
    root = tmp_path / "uploads"
    settings = Settings.load({"ZENDESK_UPLOAD_ROOT": str(root)})

    assert settings.upload_root == root
    assert "uploads" not in repr(settings.connection_status())


def test_attachment_cache_root_is_local_configuration_not_status_data(tmp_path):
    root = tmp_path / "attachments"
    settings = Settings.load({"ZENDESK_ATTACHMENT_CACHE_ROOT": str(root)})

    assert settings.attachment_cache_root == root
    assert "attachments" not in repr(settings.connection_status())
