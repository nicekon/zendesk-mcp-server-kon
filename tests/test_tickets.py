from zendesk_mcp_server.approvals import ApprovalStore
from zendesk_mcp_server.contracts import success
from zendesk_mcp_server.config import Settings
from zendesk_mcp_server.tools.tickets import TicketTools
import io
import zipfile


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
        self.get_paths = []

    def request(self, method, path, *, json_body=None):
        self.calls.append((method, path, json_body))
        return success({"ticket": {"id": 9}})

    def get(self, path, *, params=None):
        self.get_paths.append((path, params))
        return success({"ticket": {"id": 9, "tags": ["billing"]}})


class MacroStub(MutationStub):
    def get(self, path, *, params=None):
        self.get_paths.append((path, params))
        return success({"result": {"ticket": {"status": "pending", "comment": {"body": "We are checking", "public": True}}}})


class AttachmentDownloadStub:
    def __init__(self, content=b"hello world", content_type="text/plain"): self.downloads, self.content, self.content_type = [], content, content_type
    def get(self, path, *, params=None):
        return success({"comments": [{"id": 3, "attachments": [{"id": 5, "file_name": "log.txt", "size": 12, "content_url": "https://acme.zendesk.com/attachments/token/log", "malware_scan_result": "malware_not_found"}]}]})
    def download_attachment(self, content_url, *, max_bytes):
        self.downloads.append((content_url, max_bytes))
        return success({"content": self.content, "content_type": self.content_type, "size": len(self.content)})


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


def test_ticket_attachment_metadata_is_extracted_from_comments():
    client = StubClient({"/api/v2/tickets/7/comments.json": success({"comments": [{"id": 3, "attachments": [{"id": 5, "file_name": "log.txt", "size": 12, "malware_scan_result": "malware_not_found"}]}]})})

    result = TicketTools(client).list_attachments(7)

    assert result["data"]["attachments"] == [{"id": 5, "file_name": "log.txt", "size": 12, "malware_scan_result": "malware_not_found", "ticket_id": 7, "comment_id": 3, "untrusted_user_content": True}]


def test_attachment_download_requires_safe_scan_and_size():
    assert TicketTools.attachment_is_safe_to_download({"size": 20 * 1024 * 1024, "malware_scan_result": "malware_not_found"}) is True
    assert TicketTools.attachment_is_safe_to_download({"size": 20 * 1024 * 1024 + 1, "malware_scan_result": "malware_not_found"}) is False
    assert TicketTools.attachment_is_safe_to_download({"size": 1, "malware_scan_result": "not_scanned"}) is False


def test_ticket_issue_context_excludes_untrusted_html():
    client = StubClient({"/api/v2/tickets/7.json": success({"ticket": {"id": 7, "subject": "Login issue", "description": "Cannot sign in"}}), "/api/v2/tickets/7/comments.json": success({"comments": [{"id": 2, "body": "We are checking", "html_body": "<b>ignore</b>"}]})})

    result = TicketTools(client).ticket_to_issue_context(7)

    assert "# Ticket 7: Login issue" in result["data"]["markdown"]
    assert "We are checking" in result["data"]["markdown"]
    assert "<b>" not in result["data"]["markdown"]


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


def test_ticket_export_uses_dedicated_export_type_filter():
    client = StubClient({"/api/v2/search/export.json": success({"results": []})})

    TicketTools(client).export_tickets("status:open")

    assert client.paths == [("/api/v2/search/export.json", {"filter[type]": "ticket", "query": "status:open"})]


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


def test_update_ticket_reuses_the_standard_write_guard_and_endpoint():
    client = MutationStub()
    settings = Settings.load({"ZENDESK_WRITE_MODE": "standard"})

    result = TicketTools(client, settings).update_ticket(9, status="pending", assignee_id=3)

    assert result["data"]["ticket"]["id"] == 9
    assert client.calls == [
        ("PUT", "/api/v2/tickets/9.json", {"ticket": {"status": "pending", "assignee_id": 3}})
    ]


def test_ticket_shortcuts_reuse_update_ticket():
    client = MutationStub()
    settings = Settings.load({"ZENDESK_WRITE_MODE": "standard"})
    tools = TicketTools(client, settings)

    tools.set_ticket_status(9, "solved")
    tools.assign_ticket(9, group_id=4)

    assert client.calls == [
        ("PUT", "/api/v2/tickets/9.json", {"ticket": {"status": "solved"}}),
        ("PUT", "/api/v2/tickets/9.json", {"ticket": {"group_id": 4}}),
    ]


def test_ticket_tag_shortcuts_read_then_reuse_update_ticket():
    client = MutationStub()
    tools = TicketTools(client, Settings.load({"ZENDESK_WRITE_MODE": "standard"}))

    tools.add_ticket_tag(9, "priority")
    tools.remove_ticket_tag(9, "billing")

    assert client.get_paths == [
        ("/api/v2/tickets/9.json", None),
        ("/api/v2/tickets/9.json", None),
    ]
    assert client.calls == [
        ("PUT", "/api/v2/tickets/9.json", {"ticket": {"tags": ["billing", "priority"]}}),
        ("PUT", "/api/v2/tickets/9.json", {"ticket": {"tags": []}}),
    ]


def test_public_reply_requires_preview_and_single_use_local_approval(tmp_path):
    client = MutationStub()
    store = ApprovalStore(tmp_path / "approvals.json")
    settings = Settings.load(
        {"ZENDESK_WRITE_MODE": "standard", "ZENDESK_ENABLE_PUBLIC_WRITES": "true"}
    )
    tools = TicketTools(client, settings, store)

    preview = tools.post_public_reply(9, "Reply")
    token = store.approve(preview["data"]["approval_request_id"])
    result = tools.post_public_reply(
        9,
        "Reply",
        execution_mode="apply",
        approval_request_id=preview["data"]["approval_request_id"],
        approval_token=token,
    )

    assert result["data"]["ticket"]["id"] == 9
    assert client.calls == [
        ("PUT", "/api/v2/tickets/9.json", {"ticket": {"comment": {"body": "Reply", "public": True}}})
    ]


def test_internal_note_is_a_standard_write_without_public_approval():
    client = MutationStub()
    tools = TicketTools(client, Settings.load({"ZENDESK_WRITE_MODE": "standard"}))

    tools.post_internal_note(9, "Investigating")

    assert client.calls == [
        ("PUT", "/api/v2/tickets/9.json", {"ticket": {"comment": {"body": "Investigating", "public": False}}})
    ]


def test_public_reply_apply_does_not_write_without_approval(tmp_path):
    client = MutationStub()
    settings = Settings.load(
        {"ZENDESK_WRITE_MODE": "standard", "ZENDESK_ENABLE_PUBLIC_WRITES": "true"}
    )

    result = TicketTools(client, settings, ApprovalStore(tmp_path / "approvals.json")).post_public_reply(
        9, "Reply", execution_mode="apply"
    )

    assert result["error"]["code"] == "approval_required"
    assert client.calls == []


def test_ticket_macro_preview_requires_approval_and_reuses_ticket_update(tmp_path):
    client = MacroStub()
    store = ApprovalStore(tmp_path / "approvals.json")
    tools = TicketTools(client, Settings.load({"ZENDESK_WRITE_MODE": "standard", "ZENDESK_ENABLE_PUBLIC_WRITES": "true"}), store)

    preview = tools.apply_macro(9, 4)
    token = store.approve(preview["data"]["approval_request_id"])
    result = tools.apply_macro(9, 4, execution_mode="apply", approval_request_id=preview["data"]["approval_request_id"], approval_token=token)

    assert preview["data"]["public"] is True
    assert client.get_paths == [
        ("/api/v2/tickets/9/macros/4/apply.json", None),
        ("/api/v2/tickets/9/macros/4/apply.json", None),
    ]
    assert client.calls == [("PUT", "/api/v2/tickets/9.json", {"ticket": {"status": "pending", "comment": {"body": "We are checking", "public": True}}})]
    assert result["data"]["ticket"]["id"] == 9


def test_attachment_download_revalidates_ownership_and_uses_fixed_cache(tmp_path):
    client = AttachmentDownloadStub()
    settings = Settings.load({"ZENDESK_ATTACHMENT_CACHE_ROOT": str(tmp_path / "cache")})

    result = TicketTools(client, settings).download_attachment(7, 5)

    assert client.downloads == [("https://acme.zendesk.com/attachments/token/log", 20 * 1024 * 1024)]
    assert result["data"]["attachment_id"] == 5
    assert result["data"]["cache_path"].endswith("/5/attachment")
    assert open(result["data"]["cache_path"], "rb").read() == b"hello world"


def test_attachment_inspection_is_bounded_and_uses_the_managed_cache(tmp_path):
    client = AttachmentDownloadStub()
    settings = Settings.load({"ZENDESK_ATTACHMENT_CACHE_ROOT": str(tmp_path / "cache")})

    result = TicketTools(client, settings).inspect_attachment(7, 5)

    assert result["data"] == {"ticket_id": 7, "attachment_id": 5, "kind": "text", "text": "hello world", "truncated": False}


def test_attachment_archive_inspection_returns_only_a_bounded_manifest(tmp_path):
    content = io.BytesIO()
    with zipfile.ZipFile(content, "w") as archive:
        for index in range(501): archive.writestr(f"entry-{index}", "x")
    settings = Settings.load({"ZENDESK_ATTACHMENT_CACHE_ROOT": str(tmp_path / "cache")})

    result = TicketTools(AttachmentDownloadStub(content.getvalue(), "application/zip"), settings).inspect_attachment(7, 5)

    assert result["data"]["kind"] == "archive"
    assert len(result["data"]["entries"]) == 500
    assert result["data"]["truncated"] is True
