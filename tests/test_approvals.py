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
