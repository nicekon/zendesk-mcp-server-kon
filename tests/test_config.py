import pytest
from pathlib import Path

from zendesk_mcp_server.config import AuthMode, ConfigurationError, Settings


def test_time_tracking_fields_require_distinct_positive_pair():
    total = "ZENDESK_TIME_TRACKING_TOTAL_FIELD_ID"
    last = "ZENDESK_TIME_TRACKING_LAST_FIELD_ID"
    settings = Settings.load({total: "12", last: "34"})
    assert settings.time_tracking_total_field_id == 12
    assert settings.time_tracking_last_field_id == 34
    defaults = Settings.load({})
    assert defaults.time_tracking_total_field_id is None
    assert defaults.time_tracking_last_field_id is None
    for values in ({total: "12"}, {last: "34"}, {total: "12", last: "12"},
                   *({total: bad, last: "34"} for bad in ("", "0", "-1", "1.5", "true", "1_2", "１２", "9" * 5000))):
        with pytest.raises(ConfigurationError) as error:
            Settings.load(values)
        assert error.value.code == "invalid_time_tracking_fields"


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


def test_public_oauth_configuration_does_not_require_client_secret(tmp_path):
    settings = Settings.load(
        {
            "ZENDESK_SUBDOMAIN": "acme",
            "ZENDESK_AUTH_MODE": "oauth",
            "ZENDESK_OAUTH_CLIENT_KIND": "public",
            "ZENDESK_OAUTH_CLIENT_ID": "client-id",
            "ZENDESK_OAUTH_TOKEN_STORE": str(tmp_path / "oauth.json"),
        }
    )

    assert settings.auth_mode is AuthMode.OAUTH
    assert settings.oauth is not None
    assert settings.oauth.client_kind == "public"
    assert settings.oauth.client_secret == ""


def test_saved_oauth_connection_is_reused_when_auth_environment_is_empty(tmp_path, monkeypatch):
    from zendesk_mcp_server.auth import OAuthTokens, save_connection

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    path = tmp_path / ".config" / "zendesk-mcp-server" / "connection.json"
    login_settings = Settings.load({
        "ZENDESK_SUBDOMAIN": "acme",
        "ZENDESK_AUTH_MODE": "oauth",
        "ZENDESK_OAUTH_CLIENT_KIND": "public",
        "ZENDESK_OAUTH_CLIENT_ID": "client-id",
        "ZENDESK_OAUTH_TOKEN_STORE": str(path),
    })
    save_connection(login_settings, OAuthTokens("access", "refresh", 999))

    settings = Settings.load({})

    assert settings.auth_mode is AuthMode.OAUTH
    assert settings.subdomain == "acme"
    assert settings.oauth is not None
    assert settings.oauth.client_kind == "public"
    assert settings.oauth.token_store_path == path


def test_explicit_auth_environment_never_mixes_with_saved_connection(tmp_path, monkeypatch):
    from zendesk_mcp_server.auth import OAuthTokens, save_connection

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    path = tmp_path / ".config" / "zendesk-mcp-server" / "connection.json"
    saved = Settings.load({
        "ZENDESK_SUBDOMAIN": "acme",
        "ZENDESK_AUTH_MODE": "oauth",
        "ZENDESK_OAUTH_CLIENT_KIND": "public",
        "ZENDESK_OAUTH_CLIENT_ID": "client-id",
        "ZENDESK_OAUTH_TOKEN_STORE": str(path),
    })
    save_connection(saved, OAuthTokens("access", "refresh", 999))

    settings = Settings.load({"ZENDESK_SUBDOMAIN": "other"})

    assert settings.subdomain == "other"
    assert settings.auth_mode is None


def test_saved_connection_requires_relogin_before_scope_expansion(tmp_path, monkeypatch):
    from zendesk_mcp_server.auth import OAuthTokens, save_connection

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    path = tmp_path / ".config" / "zendesk-mcp-server" / "connection.json"
    saved = Settings.load({
        "ZENDESK_SUBDOMAIN": "acme",
        "ZENDESK_AUTH_MODE": "oauth",
        "ZENDESK_OAUTH_CLIENT_KIND": "public",
        "ZENDESK_OAUTH_CLIENT_ID": "client-id",
        "ZENDESK_OAUTH_TOKEN_STORE": str(path),
    })
    save_connection(saved, OAuthTokens("access", "refresh", 999))

    with pytest.raises(ConfigurationError) as error:
        Settings.load({"ZENDESK_WRITE_MODE": "standard"})
    assert error.value.code == "oauth_relogin_required"


def test_saved_connection_rejects_group_readable_permissions(tmp_path, monkeypatch):
    from zendesk_mcp_server.auth import OAuthTokens, save_connection

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    path = tmp_path / ".config" / "zendesk-mcp-server" / "connection.json"
    settings = Settings.load({
        "ZENDESK_SUBDOMAIN": "acme",
        "ZENDESK_AUTH_MODE": "oauth",
        "ZENDESK_OAUTH_CLIENT_KIND": "public",
        "ZENDESK_OAUTH_CLIENT_ID": "client-id",
        "ZENDESK_OAUTH_TOKEN_STORE": str(path),
    })
    save_connection(settings, OAuthTokens("access", "refresh", 999))
    path.chmod(0o640)

    with pytest.raises(ConfigurationError) as error:
        Settings.load({})
    assert error.value.code == "unsafe_oauth_permissions"


def test_windows_without_acl_support_refuses_oauth_reads_and_writes(tmp_path, monkeypatch):
    import zendesk_mcp_server.config as config
    from zendesk_mcp_server.auth import OAuthTokenStore, OAuthTokens, save_connection

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    path = tmp_path / ".config" / "zendesk-mcp-server" / "connection.json"
    settings = Settings.load({
        "ZENDESK_SUBDOMAIN": "acme",
        "ZENDESK_AUTH_MODE": "oauth",
        "ZENDESK_OAUTH_CLIENT_KIND": "public",
        "ZENDESK_OAUTH_CLIENT_ID": "client-id",
        "ZENDESK_OAUTH_TOKEN_STORE": str(path),
    })
    save_connection(settings, OAuthTokens("access", "refresh", 999))
    path.chmod(0o666)
    monkeypatch.setattr(config, "_CHECK_POSIX_PERMISSIONS", False)

    for operation in (lambda: Settings.load({}), lambda: OAuthTokenStore(path).load(), lambda: OAuthTokenStore(tmp_path / "new.json").save(OAuthTokens("new", "refresh", 999))):
        with pytest.raises(ConfigurationError) as error:
            operation()
        assert error.value.code == "unsupported"
    assert not (tmp_path / "new.json").exists()


@pytest.mark.parametrize("capability,extra,expected", [
    ("custom_objects", {}, {"custom_objects:read", "account_settings:read"}),
    ("git_zen", {}, {"tickets:read"}),
    ("time_tracking", {}, {"tickets:read"}),
    ("time_tracking", {"ZENDESK_WRITE_MODE": "standard"}, {"tickets:read", "tickets:write"}),
    ("guide", {"ZENDESK_WRITE_MODE": "standard", "ZENDESK_ENABLE_PUBLIC_WRITES": "true"}, {"brands:read", "hc:read", "hc:write"}),
    ("git_zen", {"ZENDESK_WRITE_MODE": "standard", "ZENDESK_ENABLE_PUBLIC_WRITES": "true"}, {"tickets:read"}),
])
def test_conditional_oauth_scopes_follow_active_read_and_write_tools(capability, extra, expected):
    settings = Settings.load({"ZENDESK_SUBDOMAIN": "acme", "ZENDESK_AUTH_MODE": "oauth", "ZENDESK_OAUTH_CLIENT_KIND": "public", "ZENDESK_OAUTH_CLIENT_ID": "client", "ZENDESK_OAUTH_TOKEN_STORE": "/tmp/not-read.json", "ZENDESK_CAPABILITIES": capability, **extra})
    assert set(settings.oauth.scopes) == expected


def test_operations_oauth_covers_its_endpoints_without_support_capability():
    settings = Settings.load({"ZENDESK_SUBDOMAIN": "acme", "ZENDESK_AUTH_MODE": "oauth", "ZENDESK_OAUTH_CLIENT_KIND": "public", "ZENDESK_OAUTH_CLIENT_ID": "client", "ZENDESK_OAUTH_TOKEN_STORE": "/tmp/not-read.json", "ZENDESK_CAPABILITIES": "operations"})
    assert set(settings.oauth.scopes) == {"account_settings:read", "users:read", "groups:read", "organizations:read", "brands:read", "tickets:read", "ticket_views:read", "macros:read", "triggers:read"}


@pytest.mark.parametrize("capability,removed_scope", [("operations", "account_settings:read"), ("support", "read")])
def test_old_grant_requires_relogin_without_rewriting_tokens(tmp_path, monkeypatch, capability, removed_scope):
    from dataclasses import replace
    from zendesk_mcp_server.auth import OAuthTokens, save_connection
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    path = tmp_path / ".config" / "zendesk-mcp-server" / "connection.json"
    settings = Settings.load({"ZENDESK_SUBDOMAIN": "acme", "ZENDESK_AUTH_MODE": "oauth", "ZENDESK_OAUTH_CLIENT_KIND": "public", "ZENDESK_OAUTH_CLIENT_ID": "client", "ZENDESK_OAUTH_TOKEN_STORE": str(path), "ZENDESK_CAPABILITIES": capability})
    old = replace(settings, oauth=replace(settings.oauth, scopes=tuple(scope for scope in settings.oauth.scopes if scope != removed_scope)))
    save_connection(old, OAuthTokens("access", "refresh", 999))
    before = path.read_bytes()
    with pytest.raises(ConfigurationError) as error:
        Settings.load({"ZENDESK_CAPABILITIES": capability})
    assert error.value.code == "oauth_relogin_required"
    assert path.read_bytes() == before


def test_oauth_scopes_are_limited_to_enabled_capabilities_and_gates():
    settings = Settings.load(
        {
            "ZENDESK_SUBDOMAIN": "acme",
            "ZENDESK_AUTH_MODE": "oauth",
            "ZENDESK_OAUTH_CLIENT_ID": "client-id",
            "ZENDESK_OAUTH_CLIENT_SECRET": "client-secret",
            "ZENDESK_OAUTH_TOKEN_STORE": "/tmp/zendesk-oauth.json",
            "ZENDESK_CAPABILITIES": "support,guide,csat",
            "ZENDESK_WRITE_MODE": "standard",
            "ZENDESK_ENABLE_PUBLIC_WRITES": "true",
        }
    )

    assert settings.oauth is not None
    assert settings.oauth.scopes == (
        "brands:read",
        "groups:read",
        "hc:read",
        "hc:write",
        "organizations:read",
        "read",
        "satisfaction_ratings:read",
        "ticket_attachments:read",
        "tickets:read",
        "tickets:write",
        "users:read",
    )


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
    assert Settings.load({"ZENDESK_CAPABILITIES": "support,custom_objects"}).has_capability("custom_objects") is True
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
