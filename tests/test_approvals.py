import sys
import json

import pytest

from zendesk_mcp_server import main
from zendesk_mcp_server.approvals import ApprovalStore
from zendesk_mcp_server.auth import OAuthTokens


def test_approval_token_is_bound_to_one_payload_and_consumed_once(tmp_path):
    store = ApprovalStore(tmp_path / "approvals.json", now=lambda: 100)
    request_id = store.create("zendesk_post_public_reply", {"ticket_id": 9, "body": "Hello"})
    token = store.approve(request_id)

    assert store.consume(request_id, "zendesk_post_public_reply", {"ticket_id": 9, "body": "Hello"}, token) is True
    assert store.consume(request_id, "zendesk_post_public_reply", {"ticket_id": 9, "body": "Hello"}, token) is False


def test_approval_expires_at_five_minutes_without_extending_on_approval(tmp_path):
    clock = [100]
    store = ApprovalStore(tmp_path / "approvals.json", now=lambda: clock[0])
    payload = {"ticket_id": 9, "body": "Hello"}
    request_id = store.create("zendesk_post_public_reply", payload)
    clock[0] = 399
    token = store.approve(request_id)
    clock[0] = 400
    assert store.consume(request_id, "zendesk_post_public_reply", payload, token) is False
    with pytest.raises(ValueError, match="missing or expired"):
        store.approve(request_id)


def test_approval_token_cannot_be_reused_for_a_different_payload(tmp_path):
    store = ApprovalStore(tmp_path / "approvals.json", now=lambda: 100)
    request_id = store.create("zendesk_post_public_reply", {"ticket_id": 9, "body": "Hello"})
    token = store.approve(request_id)

    assert store.consume(request_id, "zendesk_post_public_reply", {"ticket_id": 9, "body": "Changed"}, token) is False


def test_approval_token_is_bound_to_its_zendesk_account(tmp_path):
    path = tmp_path / "approvals.json"
    acme = ApprovalStore(path, account="acme", now=lambda: 100)
    request_id = acme.create("zendesk_post_public_reply", {"ticket_id": 9, "body": "Hello"})
    token = acme.approve(request_id)

    other = ApprovalStore(path, account="other", now=lambda: 100)
    with pytest.raises(ValueError, match="missing or expired"):
        other.preview(request_id)
    with pytest.raises(ValueError, match="missing or expired"):
        other.approve(request_id)
    assert other.consume(
        request_id, "zendesk_post_public_reply", {"ticket_id": 9, "body": "Hello"}, token
    ) is False
    assert acme.consume(request_id, "zendesk_post_public_reply", {"ticket_id": 9, "body": "Hello"}, token) is True


def test_saved_oauth_approval_is_bound_to_resolved_account(tmp_path):
    from zendesk_mcp_server.auth import save_connection
    from zendesk_mcp_server.config import Settings, saved_connection_path

    def connect(account):
        settings = Settings.load({
            "ZENDESK_SUBDOMAIN": account,
            "ZENDESK_AUTH_MODE": "oauth",
            "ZENDESK_OAUTH_CLIENT_KIND": "public",
            "ZENDESK_OAUTH_CLIENT_ID": "client",
            "ZENDESK_OAUTH_TOKEN_STORE": str(saved_connection_path()),
        })
        save_connection(settings, OAuthTokens("access", "refresh", 999))

    connect("acme")
    store = ApprovalStore.from_environment({})
    payload = {"ticket_id": 9, "body": "Hello"}
    request_id = store.create("zendesk_post_public_reply", payload)
    assert store.preview(request_id)["account"] == "acme"
    token = store.approve(request_id)
    connect("other")
    other = ApprovalStore.from_environment({})
    assert other.consume(request_id, "zendesk_post_public_reply", payload, token) is False
    with pytest.raises(ValueError, match="missing or expired"):
        other.approve(request_id)


def test_approval_preview_returns_the_exact_stored_payload(tmp_path):
    store = ApprovalStore(tmp_path / "approvals.json", now=lambda: 100)
    request_id = store.create("zendesk_post_public_reply", {"ticket_id": 9, "body": "Hello"})

    assert store.preview(request_id) == {
        "account": None,
        "tool": "zendesk_post_public_reply",
        "payload": {"ticket_id": 9, "body": "Hello"},
    }


def test_approve_cli_displays_the_preview_before_issuing_a_token(tmp_path, monkeypatch, capsys):
    store = ApprovalStore(tmp_path / "approvals.json")
    request_id = store.create("zendesk_post_public_reply", {"ticket_id": 9, "body": "Hello"})
    monkeypatch.setenv("ZENDESK_APPROVAL_STORE", str(store.path))
    monkeypatch.setattr(sys, "argv", ["zendesk", "approve", request_id])
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _: "yes")

    main()

    assert '"payload": {' in capsys.readouterr().out


def test_oauth_start_cli_prints_a_state_bound_authorization_url(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("ZENDESK_SUBDOMAIN", "acme")
    monkeypatch.setenv("ZENDESK_AUTH_MODE", "oauth")
    monkeypatch.setenv("ZENDESK_OAUTH_CLIENT_ID", "client")
    monkeypatch.setenv("ZENDESK_OAUTH_CLIENT_SECRET", "secret")
    monkeypatch.setenv("ZENDESK_OAUTH_TOKEN_STORE", str(tmp_path / "oauth.json"))
    monkeypatch.setattr(sys, "argv", ["zendesk", "oauth-start", "https://app.example.test/callback"])

    main()

    assert "https://acme.zendesk.com/oauth/authorizations/new?" in capsys.readouterr().out


def test_oauth_finish_cli_reads_the_code_from_an_interactive_terminal(tmp_path, monkeypatch):
    import zendesk_mcp_server.auth as auth

    monkeypatch.setenv("ZENDESK_SUBDOMAIN", "acme")
    monkeypatch.setenv("ZENDESK_AUTH_MODE", "oauth")
    monkeypatch.setenv("ZENDESK_OAUTH_CLIENT_ID", "client")
    monkeypatch.setenv("ZENDESK_OAUTH_CLIENT_SECRET", "secret")
    monkeypatch.setenv("ZENDESK_OAUTH_TOKEN_STORE", str(tmp_path / "oauth.json"))
    monkeypatch.setattr(sys, "argv", ["zendesk", "oauth-finish", "https://app.example.test/callback", "state"])
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("getpass.getpass", lambda _: "code")
    calls = []
    monkeypatch.setattr(auth, "exchange_settings_oauth_authorization_code", lambda settings, code, state, redirect_uri: calls.append((code, state, redirect_uri)) or OAuthTokens("access", "refresh", 3600))

    main()

    assert calls == [("code", "state", "https://app.example.test/callback")]


def test_check_cli_reports_unconfigured_state_without_network_or_secrets(monkeypatch, capsys):
    for name in ("ZENDESK_SUBDOMAIN", "ZENDESK_EMAIL", "ZENDESK_API_TOKEN", "ZENDESK_OAUTH_CLIENT_ID", "ZENDESK_OAUTH_CLIENT_SECRET", "ZENDESK_OAUTH_TOKEN_STORE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(sys, "argv", ["zendesk", "check"])

    main()

    assert json.loads(capsys.readouterr().out) == {
        "ok": True,
        "data": {
            "configured": False,
            "auth_mode": None,
            "write_mode": "read_only",
            "active_write_gates": [],
            "capabilities": ["community", "guide", "operations", "support"],
        },
    }
