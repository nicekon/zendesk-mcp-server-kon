from zendesk_mcp_server.contracts import ErrorCode, failure, success


def test_success_has_stable_envelope():
    assert success({"id": 42}, request_id="req-1") == {
        "ok": True,
        "data": {"id": 42},
        "request_id": "req-1",
    }


def test_failure_never_uses_a_success_string_for_an_error():
    result = failure(ErrorCode.NOT_CONFIGURED, "Zendesk is not configured")

    assert result["ok"] is False
    assert result["error"]["code"] == "not_configured"
    assert result["error"]["operation_state"] == "not_applied"


def test_approval_required_is_a_stable_error_code():
    result = failure(ErrorCode.APPROVAL_REQUIRED, "approval required")

    assert result["error"]["code"] == "approval_required"


def test_standard_contract_includes_partial_and_unsafe_attachment_errors():
    assert ErrorCode.PARTIAL_SUCCESS.value == "partial_success"
    assert ErrorCode.UNSAFE_ATTACHMENT.value == "unsafe_attachment"
