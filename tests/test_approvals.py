import sys

from zendesk_mcp_server import main
from zendesk_mcp_server.approvals import ApprovalStore


def test_approval_token_is_bound_to_one_payload_and_consumed_once(tmp_path):
    store = ApprovalStore(tmp_path / "approvals.json", now=lambda: 100)
    request_id = store.create("zendesk_post_public_reply", {"ticket_id": 9, "body": "Hello"})
    token = store.approve(request_id)

    assert store.consume(request_id, "zendesk_post_public_reply", {"ticket_id": 9, "body": "Hello"}, token) is True
    assert store.consume(request_id, "zendesk_post_public_reply", {"ticket_id": 9, "body": "Hello"}, token) is False


def test_approval_token_cannot_be_reused_for_a_different_payload(tmp_path):
    store = ApprovalStore(tmp_path / "approvals.json", now=lambda: 100)
    request_id = store.create("zendesk_post_public_reply", {"ticket_id": 9, "body": "Hello"})
    token = store.approve(request_id)

    assert store.consume(request_id, "zendesk_post_public_reply", {"ticket_id": 9, "body": "Changed"}, token) is False


def test_approval_preview_returns_the_exact_stored_payload(tmp_path):
    store = ApprovalStore(tmp_path / "approvals.json", now=lambda: 100)
    request_id = store.create("zendesk_post_public_reply", {"ticket_id": 9, "body": "Hello"})

    assert store.preview(request_id) == {
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
