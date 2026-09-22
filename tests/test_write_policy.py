from zendesk_mcp_server.config import Settings
from zendesk_mcp_server.write_policy import WriteRisk, check_write_permission


def test_default_policy_allows_standard_and_public_writes_only():
    settings = Settings.load({})

    assert check_write_permission(settings, WriteRisk.STANDARD) is None
    assert check_write_permission(settings, WriteRisk.PUBLIC) is None
    for risk in (WriteRisk.DESTRUCTIVE, WriteRisk.IMPERSONATION, WriteRisk.EXTERNAL_UPLOAD):
        assert check_write_permission(settings, risk)["error"]["code"] == "write_disabled"


def test_explicit_opt_outs_disable_only_the_requested_write_capabilities():
    read_only = Settings.load({"ZENDESK_WRITE_MODE": "read_only", "ZENDESK_ENABLE_PUBLIC_WRITES": "true"})
    public_disabled = Settings.load({"ZENDESK_WRITE_MODE": "standard", "ZENDESK_ENABLE_PUBLIC_WRITES": "false"})

    assert read_only.active_write_gates() == []
    assert check_write_permission(read_only, WriteRisk.STANDARD)["error"]["code"] == "write_disabled"
    assert check_write_permission(read_only, WriteRisk.PUBLIC)["error"]["code"] == "write_disabled"

    assert public_disabled.active_write_gates() == ["standard"]
    assert check_write_permission(public_disabled, WriteRisk.STANDARD) is None
    assert check_write_permission(public_disabled, WriteRisk.PUBLIC)["error"]["code"] == "write_disabled"
