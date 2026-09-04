from zendesk_mcp_server.config import Settings
from zendesk_mcp_server.write_policy import WriteRisk, check_write_permission


def test_standard_writes_require_standard_write_mode():
    result = check_write_permission(Settings.load({}), WriteRisk.STANDARD)

    assert result["error"]["code"] == "write_disabled"


def test_public_writes_require_a_separate_gate():
    settings = Settings.load({"ZENDESK_WRITE_MODE": "standard"})

    result = check_write_permission(settings, WriteRisk.PUBLIC)

    assert result["error"]["code"] == "write_disabled"
