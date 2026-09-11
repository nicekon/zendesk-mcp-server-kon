import json
import sys
from pathlib import Path

from zendesk_mcp_server import main
from zendesk_mcp_server.config import AuthMode


def test_login_command_builds_public_read_only_settings(tmp_path, monkeypatch):
    received = []
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr("zendesk_mcp_server.login.login", lambda settings, port: received.append((settings, port)))
    monkeypatch.setattr(sys, "argv", ["zendesk", "login", "--subdomain", "Acme", "--client-id", "mcp-client", "--port", "3456"])

    main()

    settings, port = received[0]
    assert settings.auth_mode is AuthMode.OAUTH
    assert settings.subdomain == "acme"
    assert settings.write_mode == "read_only"
    assert settings.oauth is not None
    assert settings.oauth.client_kind == "public"
    assert settings.oauth.client_secret == ""
    assert settings.oauth.token_store_path == tmp_path / ".config" / "zendesk-mcp-server" / "connection.json"
    assert port == 3456


def test_check_probe_requests_network_verification(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr("zendesk_mcp_server.server.build_connection_status", lambda environ, probe=False: calls.append(probe) or {"ok": True})
    monkeypatch.setattr(sys, "argv", ["zendesk", "check", "--probe"])

    main()

    assert calls == [True]
    assert json.loads(capsys.readouterr().out) == {"ok": True}


def test_help_explains_login_without_starting_mcp(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["zendesk", "--help"])

    try:
        main()
    except SystemExit as error:
        assert error.code == 0

    output = capsys.readouterr().out
    assert "login" in output
    assert "check" in output
