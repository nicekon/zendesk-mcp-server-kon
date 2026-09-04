import importlib


def test_server_import_does_not_require_credentials(monkeypatch):
    for name in (
        "ZENDESK_SUBDOMAIN",
        "ZENDESK_EMAIL",
        "ZENDESK_API_TOKEN",
        "ZENDESK_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)

    module = importlib.import_module("zendesk_mcp_server.server")

    assert callable(module.create_server)


def test_connection_status_is_available_without_configuration():
    from zendesk_mcp_server.server import build_connection_status

    assert build_connection_status({}) == {
        "ok": True,
        "data": {"configured": False, "auth_mode": None, "write_mode": "read_only"},
    }


def test_connection_status_does_not_expose_credentials():
    from zendesk_mcp_server.server import build_connection_status

    result = build_connection_status(
        {
            "ZENDESK_SUBDOMAIN": "acme",
            "ZENDESK_EMAIL": "agent@example.test",
            "ZENDESK_API_TOKEN": "secret",
        }
    )

    assert result["data"]["auth_mode"] == "api_token"
    assert "secret" not in repr(result)
    assert "agent@example.test" not in repr(result)


def test_foundation_registers_only_connection_status_tool():
    from zendesk_mcp_server.server import build_tools

    assert [tool.name for tool in build_tools()] == ["zendesk_get_connection_status"]
