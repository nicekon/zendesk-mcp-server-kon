from zendesk_mcp_server.contracts import success
from zendesk_mcp_server.tools.guide import GuideTools


class StubClient:
    def __init__(self): self.paths = []
    def get(self, path, *, params=None):
        self.paths.append((path, params))
        return success({"articles": [{"id": 3}]})


def test_guide_and_csat_reads_use_fixed_endpoints():
    client = StubClient()
    tools = GuideTools(client)

    tools.list_categories()
    tools.list_sections()
    tools.search_articles("billing")
    tools.get_article(3)
    tools.get_satisfaction_ratings()

    assert client.paths == [
        ("/api/v2/help_center/categories.json", None),
        ("/api/v2/help_center/sections.json", None),
        ("/api/v2/help_center/articles/search.json", {"query": "billing"}),
        ("/api/v2/help_center/articles/3.json", None),
        ("/api/v2/satisfaction_ratings.json", None),
    ]
