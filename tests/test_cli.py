import json
import sys
from pathlib import Path

import pytest

from zendesk_mcp_server import main
from zendesk_mcp_server.config import AuthMode
from zendesk_mcp_server.config import ConfigurationError


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
