import json
import sys
from pathlib import Path

import pytest

from zendesk_mcp_server import main
from zendesk_mcp_server.config import AuthMode
from zendesk_mcp_server.config import ConfigurationError


def test_login_command_builds_default_write_settings(tmp_path, monkeypatch):
    received = []
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr("zendesk_mcp_server.login.login", lambda settings, port: received.append((settings, port)))
    monkeypatch.setattr(sys, "argv", ["zendesk", "login", "--subdomain", "Acme", "--client-id", "mcp-client", "--port", "3456"])

    main()

    settings, port = received[0]
    assert settings.auth_mode is AuthMode.OAUTH
    assert settings.subdomain == "acme"
    assert settings.write_mode == "standard"
    assert settings.public_writes_enabled is True
    assert settings.oauth is not None
    assert {"tickets:write", "hc:write"} <= set(settings.oauth.scopes)
    assert settings.oauth.client_kind == "public"
    assert settings.oauth.client_secret == ""
    assert settings.oauth.token_store_path == tmp_path / ".config" / "zendesk-mcp-server" / "connection.json"
    assert port == 3456


@pytest.mark.parametrize("values,expected", [
    ({"ZENDESK_CAPABILITIES": "support", "ZENDESK_WRITE_MODE": "standard"}, ["brands:read", "groups:read", "organizations:read", "read", "ticket_attachments:read", "tickets:read", "tickets:write", "users:read"]),
    ({"ZENDESK_CAPABILITIES": "community", "ZENDESK_ENABLE_PUBLIC_WRITES": "true", "ZENDESK_ENABLE_IMPERSONATION": "true"}, ["hc:read", "hc:write", "impersonate", "users:read"]),
])
def test_login_requests_scopes_from_explicit_runtime_gates(tmp_path, monkeypatch, capsys, values, expected):
    received = []
    for key, value in values.items(): monkeypatch.setenv(key, value)
    monkeypatch.setenv("ZENDESK_AUTH_MODE", "api_token")
    monkeypatch.setenv("ZENDESK_API_TOKEN", "old-secret-token")
    monkeypatch.setenv("ZENDESK_OAUTH_CLIENT_SECRET", "old-client-secret")
    monkeypatch.setattr("zendesk_mcp_server.login.login", lambda settings, port: received.append(settings))
    monkeypatch.setattr(sys, "argv", ["zendesk", "login", "--subdomain", "acme", "--client-id", "public-client"])
    main()
    settings = received[0]
    assert list(settings.oauth.scopes) == expected
    assert settings.auth_mode is AuthMode.OAUTH and settings.oauth.client_secret == ""
    assert settings.oauth.client_id == "public-client"
    assert settings.oauth.token_store_path == tmp_path / ".config" / "zendesk-mcp-server" / "connection.json"
    assert not settings.oauth.token_store_path.exists()
    assert "old-secret" not in capsys.readouterr().out


def test_login_rejects_invalid_write_gate_before_opening_browser(monkeypatch):
    monkeypatch.setenv("ZENDESK_WRITE_MODE", "invalid")
    def unexpected(*args, **kwargs): raise AssertionError("must validate before login")
    monkeypatch.setattr("zendesk_mcp_server.login.login", unexpected)
    monkeypatch.setattr(sys, "argv", ["zendesk", "login", "--subdomain", "acme", "--client-id", "public-client"])
    with pytest.raises(SystemExit, match="ZENDESK_WRITE_MODE is invalid"):
        main()


def test_check_probe_requests_network_verification(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr("zendesk_mcp_server.server.build_connection_status", lambda environ, probe=False: calls.append(probe) or {"ok": True})
    monkeypatch.setattr(sys, "argv", ["zendesk", "check", "--probe"])

    main()

    assert calls == [True]
    assert json.loads(capsys.readouterr().out) == {"ok": True}


def test_check_configuration_failure_exits_nonzero_with_json(monkeypatch, capsys):
    monkeypatch.setenv("ZENDESK_API_KEY", "obsolete-test-value")
    monkeypatch.setattr(sys, "argv", ["zendesk", "check"])
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 1
    output = capsys.readouterr()
    assert json.loads(output.out)["ok"] is False
    assert "obsolete-test-value" not in output.out
    assert output.err == ""


def test_saved_scope_expansion_reaches_cli_and_mcp_without_network(tmp_path, monkeypatch, capsys):
    import asyncio
    from mcp import types
    from zendesk_mcp_server.auth import OAuthTokens, save_connection
    from zendesk_mcp_server.config import Settings
    from zendesk_mcp_server.server import create_server
    path = tmp_path / ".config" / "zendesk-mcp-server" / "connection.json"
    settings = Settings.load({"ZENDESK_SUBDOMAIN": "acme", "ZENDESK_AUTH_MODE": "oauth", "ZENDESK_CAPABILITIES": "guide", "ZENDESK_OAUTH_CLIENT_KIND": "public", "ZENDESK_OAUTH_CLIENT_ID": "client", "ZENDESK_OAUTH_TOKEN_STORE": str(path)})
    save_connection(settings, OAuthTokens("test-access", "test-refresh", 999))
    original = path.read_bytes()
    def no_network(*args, **kwargs):
        raise AssertionError("Scope expansion must fail before HTTP")
    monkeypatch.setattr("httpx.Client.send", no_network)
    monkeypatch.setattr(sys, "argv", ["zendesk", "check", "--probe"])
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 1
    cli = json.loads(capsys.readouterr().out)
    server = create_server({})
    request = types.CallToolRequest(params=types.CallToolRequestParams(name="zendesk_get_connection_status", arguments={}))
    result = asyncio.run(server.request_handlers[types.CallToolRequest](request))
    assert result.root.structuredContent == cli
    assert cli["error"]["code"] == "reauthorization_required"
    assert cli["error"]["operation_state"] == "not_applied"
    assert path.read_bytes() == original


def test_help_explains_login_without_starting_mcp(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["zendesk", "--help"])

    try:
        main()
    except SystemExit as error:
        assert error.code == 0

    output = capsys.readouterr().out
    assert "login" in output
    assert "check" in output


def test_login_configuration_error_exits_without_traceback(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(
        "zendesk_mcp_server.login.login",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ConfigurationError("oauth_callback_timeout", "OAuth login timed out")),
    )
    monkeypatch.setattr(sys, "argv", ["zendesk", "login", "--subdomain", "acme", "--client-id", "client"])

    with pytest.raises(SystemExit) as error:
        main()

    assert error.value.code == "OAuth login timed out"
