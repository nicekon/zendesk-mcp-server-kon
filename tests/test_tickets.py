from zendesk_mcp_server.contracts import success
from zendesk_mcp_server.tools.tickets import TicketTools


class StubClient:
    def __init__(self, responses):
        self.responses = responses
        self.paths = []

    def get(self, path, *, params=None):
        self.paths.append((path, params))
        return self.responses[path]


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
