from zendesk_mcp_server.contracts import success
from zendesk_mcp_server.tools.metadata import MetadataTools


class StubClient:
    def __init__(self):
        self.paths = []

    def get(self, path, *, params=None):
        self.paths.append((path, params))
        return success({"users": [{"id": 1}], "groups": [{"id": 2}], "brands": [{"id": 3}]})


def test_metadata_reads_use_fixed_zendesk_endpoints():
    client = StubClient()
    tools = MetadataTools(client)

    assert tools.search_users("agent")["data"]["users"] == [{"id": 1}]
    assert tools.list_groups()["data"]["groups"] == [{"id": 2}]
    assert tools.list_brands()["data"]["brands"] == [{"id": 3}]
    assert client.paths == [
        ("/api/v2/users/search.json", {"query": "agent"}),
        ("/api/v2/groups.json", None),
        ("/api/v2/brands.json", None),
    ]


def test_view_macro_and_trigger_reads_use_fixed_endpoints():
    client = StubClient()
    tools = MetadataTools(client)

    tools.list_views()
    tools.get_view(4)
    tools.list_view_tickets(4)
    tools.list_macros()
    tools.list_triggers()

    assert client.paths == [
        ("/api/v2/views.json", None),
        ("/api/v2/views/4.json", None),
        ("/api/v2/views/4/tickets.json", None),
        ("/api/v2/macros.json", None),
        ("/api/v2/triggers.json", None),
    ]
