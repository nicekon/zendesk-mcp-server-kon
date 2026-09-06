def test_package_can_import_without_zendesk_credentials(monkeypatch):
    for name in (
        "ZENDESK_SUBDOMAIN",
        "ZENDESK_EMAIL",
        "ZENDESK_API_TOKEN",
        "ZENDESK_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)

    import zendesk_mcp_server

    assert callable(zendesk_mcp_server.main)


def test_readme_declares_the_mcp_registry_name():
    from pathlib import Path

    readme = Path(__file__).parents[1] / "README.md"
    assert "<!-- mcp-name: io.github.nicekon/zendesk-mcp-server-kon -->" in readme.read_text()
