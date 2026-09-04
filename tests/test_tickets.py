from zendesk_mcp_server.contracts import success
from zendesk_mcp_server.config import Settings
from zendesk_mcp_server.tools.tickets import TicketTools


class StubClient:
    def __init__(self, responses):
        self.responses = responses
        self.paths = []

    def get(self, path, *, params=None):
        self.paths.append((path, params))
        return self.responses[path]


class MutationStub:
    def __init__(self):
        self.calls = []

    def request(self, method, path, *, json_body=None):
        self.calls.append((method, path, json_body))
        return success({"ticket": {"id": 9}})


def test_get_ticket_rejects_zero_without_a_client():
    result = TicketTools(None).get_ticket(0)

    assert result["error"]["code"] == "validation_error"


def test_list_tickets_normalizes_cursor_response():
    client = StubClient(
        {
            "/api/v2/tickets.json": success(
                {"tickets": [{"id": 1}], "meta": {"has_more": True, "after_cursor": "next"}}
            )
        }
    )

    result = TicketTools(client).list_tickets(limit=2)

    assert result["items"] == [{"id": 1}]
    assert result["has_more"] is True
    assert result["next_cursor"] == "next"
    assert result["truncated"] is False
    assert client.paths == [("/api/v2/tickets.json", {"page[size]": "2"})]


def test_conversation_marks_customer_text_untrusted():
    client = StubClient(
        {
            "/api/v2/tickets/7/comments.json": success(
                {"comments": [{"id": 3, "body": "hello", "html_body": "<p>hello</p>"}]}
            )
        }
    )

    result = TicketTools(client).get_conversation(7)

    assert result["data"]["comments"][0]["untrusted_user_content"] is True


def test_search_and_count_use_ticket_query():
    client = StubClient(
        {
            "/api/v2/search.json": success({"results": [{"id": 2}], "next_page": None}),
            "/api/v2/search/count.json": success({"count": {"value": 1, "refreshed_at": "now"}}),
        }
    )

    assert TicketTools(client).search_tickets("status:open")["items"] == [{"id": 2}]
    assert TicketTools(client).count_tickets("status:open")["data"]["count"] == 1
    assert client.paths == [
        ("/api/v2/search.json", {"query": "type:ticket status:open", "page[size]": "100"}),
        ("/api/v2/search/count.json", {"query": "type:ticket status:open"}),
    ]


def test_search_rejects_blank_query_and_non_integer_limit_without_a_client():
    tools = TicketTools(None)

    assert tools.search_tickets("", 10)["error"]["code"] == "validation_error"
    assert tools.search_tickets("status:open", True)["error"]["code"] == "validation_error"


def test_create_ticket_requires_write_mode_before_a_client_is_used():
    result = TicketTools(None, Settings.load({})).create_ticket(
        requester_id=7,
        subject="Need help",
        description="Details",
    )

    assert result["error"]["code"] == "write_disabled"


def test_create_ticket_uses_a_validated_standard_write_payload():
    client = MutationStub()
    settings = Settings.load({"ZENDESK_WRITE_MODE": "standard"})

    result = TicketTools(client, settings).create_ticket(
        requester_id=7,
        subject="Need help",
        description="Details",
        tags=["billing", "priority"],
        priority="high",
    )

    assert result["data"]["ticket"]["id"] == 9
    assert client.calls == [
        (
            "POST",
            "/api/v2/tickets.json",
            {
                "ticket": {
                    "requester_id": 7,
                    "subject": "Need help",
                    "comment": {"body": "Details"},
                    "tags": ["billing", "priority"],
                    "priority": "high",
                }
            },
        )
    ]
