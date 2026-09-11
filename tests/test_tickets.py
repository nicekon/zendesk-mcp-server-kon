from zendesk_mcp_server.approvals import ApprovalStore
from zendesk_mcp_server.contracts import success
from zendesk_mcp_server.config import Settings
from zendesk_mcp_server.tools.tickets import TicketTools
import io
import os
import time
import zipfile
from pathlib import Path
from pypdf import PdfWriter


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
        if path == "/api/v2/tickets/9.json":
            return success({"ticket": {"id": 9, "status": "open", "updated_at": "2026-09-11T00:00:00Z"}})
        if path == "/api/v2/macros/4.json":
            return success({"macro": {"actions": [{"field": "comment_mode_is_public", "value": True}]}})
        return success({"result": {"ticket": {"status": "pending", "comment": {"body": "We are checking", "public": True}}}})


class RiskyMacroStub(MutationStub):
    def get(self, path, *, params=None):
        self.get_paths.append((path, params))
        if path == "/api/v2/tickets/9.json":
            return success({"ticket": {"id": 9, "status": "open", "updated_at": "2026-09-11T00:00:00Z"}})
        if path == "/api/v2/macros/4.json":
            return success({"macro": {"actions": [
                {"field": "comment_mode_is_public", "value": True},
                {"field": "notification_user", "value": ["3", "Subject", "Body"]},
                {"field": "author_id", "value": "3"},
                {"field": "status", "value": "closed"},
            ]}})
        return success({"result": {"ticket": {"status": "pending"}}})


class InvalidMacroStub(MutationStub):
    def get(self, path, *, params=None):
        self.get_paths.append((path, params))
        if path == "/api/v2/tickets/9.json":
            return success({"ticket": {"id": 9, "status": "open", "updated_at": "2026-09-11T00:00:00Z"}})
        if path == "/api/v2/macros/4.json":
            return success({"macro": {"actions": [{"field": []}]}})
        return success({"result": {"ticket": {"status": "pending"}}})


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


def test_time_tracking_reads_audit_metadata_and_logs_with_an_internal_note():
    class TimeClient(MutationStub):
        def get(self, path, *, params=None):
            self.get_paths.append((path, params))
            return success({"audits": [{"id": 4, "created_at": "2026-09-06T00:00:00Z", "author_id": 2, "metadata": {"custom": {"time_spent": "4m12s"}}}]})

    client = TimeClient()
    tools = TicketTools(client, Settings.load({"ZENDESK_WRITE_MODE": "standard"}))

    assert tools.get_time_tracking(9)["data"]["entries"] == [{"audit_id": 4, "created_at": "2026-09-06T00:00:00Z", "author_id": 2, "time_spent": "4m12s"}]
    assert tools.log_time(9, "4m12s", "Investigated login issue")["ok"] is True
    assert client.get_paths == [("/api/v2/tickets/9/audits.json", None)]
    assert client.calls == [("PUT", "/api/v2/tickets/9.json", {"ticket": {"comment": {"body": "Investigated login issue", "public": False}, "metadata": {"time_spent": "4m12s"}}})]


def test_git_zen_links_are_extracted_only_from_the_configured_ticket_field():
    client = StubClient({"/api/v2/tickets/9.json": success({"ticket": {"custom_fields": [{"id": 7, "value": "https://github.com/acme/app/issues/4 https://gitlab.com/acme/app/-/merge_requests/3"}, {"id": 8, "value": "https://github.com/acme/app/pull/9"}]}})})

    result = TicketTools(client, Settings.load({"ZENDESK_GIT_ZEN_FIELD_ID": "7"})).get_git_zen_links(9)

    assert result["data"]["links"] == ["https://github.com/acme/app/issues/4", "https://gitlab.com/acme/app/-/merge_requests/3"]


def test_ticket_attachment_metadata_is_extracted_from_comments():
    client = StubClient({"/api/v2/tickets/7/comments.json": success({"comments": [{"id": 3, "attachments": [{"id": 5, "file_name": "log.txt", "size": 12, "malware_scan_result": "malware_not_found"}]}]})})

    result = TicketTools(client).list_attachments(7)

    assert result["data"]["attachments"] == [{"id": 5, "file_name": "log.txt", "size": 12, "malware_scan_result": "malware_not_found", "ticket_id": 7, "comment_id": 3, "untrusted_user_content": True}]


def test_attachment_download_requires_safe_scan_and_size():
    assert TicketTools.attachment_is_safe_to_download({"size": 20 * 1024 * 1024, "malware_scan_result": "malware_not_found"}) is True
    assert TicketTools.attachment_is_safe_to_download({"size": 20 * 1024 * 1024 + 1, "malware_scan_result": "malware_not_found"}) is False
    assert TicketTools.attachment_is_safe_to_download({"size": 1, "malware_scan_result": "not_scanned"}) is False


def test_attachment_download_rejects_unscanned_content_before_downloading():
    client = AttachmentDownloadStub()
    client.get = lambda *_args, **_kwargs: success({"comments": [{"id": 3, "attachments": [{"id": 5, "size": 12, "content_url": "https://acme.zendesk.com/attachments/token/log", "malware_scan_result": "not_scanned"}]}]})

    result = TicketTools(client).download_attachment(7, 5)

    assert result["error"]["code"] == "unsafe_attachment"
    assert client.downloads == []


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


def test_structured_ticket_filter_uses_the_same_serializer_for_search_count_and_export():
    query = {
        "status": "open",
        "tags": {"include": ["billing"], "exclude": ["spam"]},
        "assignee": {"kind": "id", "value": 8},
    }
    client = StubClient(
        {
            "/api/v2/search.json": success({"results": [], "next_page": None}),
            "/api/v2/search/count.json": success({"count": {"value": 0}}),
            "/api/v2/search/export.json": success({"results": [], "meta": {"has_more": False}}),
        }
    )

    TicketTools(client).search_tickets(query)
    TicketTools(client).count_tickets(query)
    TicketTools(client).export_tickets(query)

    assert client.paths == [
        ("/api/v2/search.json", {"query": "type:ticket status:open tags:billing -tags:spam assignee:8", "page[size]": "100"}),
        ("/api/v2/search/count.json", {"query": "type:ticket status:open tags:billing -tags:spam assignee:8"}),
        ("/api/v2/search/export.json", {"filter[type]": "ticket", "query": "status:open tags:billing -tags:spam assignee:8", "page[size]": "100"}),
    ]


def test_structured_ticket_filter_serializes_documented_ticket_ranges_and_custom_fields():
    client = StubClient({"/api/v2/search.json": success({"results": [], "next_page": None})})

    TicketTools(client).search_tickets({
        "text": "billing outage",
        "type": "incident",
        "has_attachment": True,
        "created": {"after": "2026-09-01T00:00:00Z", "before": "2026-09-02T00:00:00Z"},
        "updated": {"after": "2026-09-01"},
        "solved": {"before": "2026-09-03"},
        "custom_fields": [{"id": 10, "value": "enterprise"}],
    })

    assert client.paths == [(
        "/api/v2/search.json",
        {"query": 'type:ticket "billing outage" type:incident has_attachment:true created>2026-09-01T00:00:00Z created<2026-09-02T00:00:00Z updated>2026-09-01 solved<2026-09-03 custom_field_10:enterprise', "page[size]": "100"},
    )]


def test_structured_ticket_filter_rejects_a_naive_datetime_without_a_request():
    client = StubClient({})

    result = TicketTools(client).search_tickets({"created": {"after": "2026-09-01T00:00:00"}})

    assert result["error"]["code"] == "validation_error"
    assert client.paths == []


def test_structured_ticket_filter_resolves_an_exact_user_email_before_searching():
    client = StubClient({
        "/api/v2/users/search.json": success({"users": [{"id": 8, "email": "agent@example.test"}]}),
        "/api/v2/search.json": success({"results": [], "next_page": None}),
    })

    TicketTools(client).search_tickets({"assignee": {"kind": "email", "value": "agent@example.test"}})

    assert client.paths == [
        ("/api/v2/users/search.json", {"query": "agent@example.test"}),
        ("/api/v2/search.json", {"query": "type:ticket assignee:8", "page[size]": "100"}),
    ]


def test_named_ticket_user_filter_returns_candidate_ids_when_ambiguous():
    client = StubClient({"/api/v2/users/search.json": success({"users": [{"id": 8, "email": "agent@example.test"}, {"id": 9, "email": "agent@example.test"}]})})

    result = TicketTools(client).search_tickets({"assignee": {"kind": "email", "value": "agent@example.test"}})

    assert result["error"]["details"] == {"candidate_ids": [8, 9]}


def test_structured_ticket_filter_resolves_organization_name_exactly():
    client = StubClient({
        "/api/v2/organizations/search.json": success({"organizations": [{"id": 12, "name": "Acme"}]}),
        "/api/v2/search.json": success({"results": [], "next_page": None}),
    })

    TicketTools(client).search_tickets({"organization": {"kind": "name", "value": "Acme"}})

    assert client.paths == [
        ("/api/v2/organizations/search.json", {"name": "Acme"}),
        ("/api/v2/search.json", {"query": "type:ticket organization:12", "page[size]": "100"}),
    ]


def test_structured_ticket_filter_serializes_brand_group_and_form_ids():
    client = StubClient({"/api/v2/search.json": success({"results": [], "next_page": None})})

    TicketTools(client).search_tickets({"brand": {"kind": "id", "value": 2}, "group": {"kind": "id", "value": 3}, "form": {"kind": "id", "value": 4}})

    assert client.paths == [("/api/v2/search.json", {"query": "type:ticket brand:2 group:3 form:4", "page[size]": "100"})]


def test_structured_ticket_filter_resolves_brand_name():
    client = StubClient({"/api/v2/brands.json": success({"brands": [{"id": 2, "name": "Acme"}]}), "/api/v2/search.json": success({"results": [], "next_page": None})})
    TicketTools(client).search_tickets({"brand": {"kind": "name", "value": "Acme"}})
    assert client.paths == [("/api/v2/brands.json", {"page[size]": "100"}), ("/api/v2/search.json", {"query": "type:ticket brand:2", "page[size]": "100"})]


def test_structured_ticket_filter_resolves_group_name():
    client = StubClient({"/api/v2/groups.json": success({"groups": [{"id": 3, "name": "Support"}]}), "/api/v2/search.json": success({"results": [], "next_page": None})})
    TicketTools(client).search_tickets({"group": {"kind": "name", "value": "Support"}})
    assert client.paths == [("/api/v2/groups.json", {"page[size]": "100"}), ("/api/v2/search.json", {"query": "type:ticket group:3", "page[size]": "100"})]


def test_structured_ticket_filter_resolves_form_name():
    client = StubClient({"/api/v2/ticket_forms.json": success({"ticket_forms": [{"id": 4, "name": "Incident"}]}), "/api/v2/search.json": success({"results": [], "next_page": None})})

    TicketTools(client).search_tickets({"form": {"kind": "name", "value": "Incident"}})

    assert client.paths == [("/api/v2/ticket_forms.json", {"page[size]": "100"}), ("/api/v2/search.json", {"query": "type:ticket form:4", "page[size]": "100"})]


def test_custom_object_projection_requires_its_capability():
    settings = Settings.load({})
    result = TicketTools(StubClient({}), settings).search_tickets("status:open", projection={"include_custom_objects": ["asset"]})
    assert result["error"]["code"] == "unsupported"


def test_custom_object_projection_validates_its_keys():
    result = TicketTools(None).search_tickets("status:open", projection={"unknown": []})
    assert result["error"]["code"] == "validation_error"
    assert TicketTools(None).search_tickets("status:open", projection={"include_custom_objects": []})["error"]["code"] == "validation_error"


def test_ticket_projection_selects_requested_ticket_fields():
    client = StubClient({"/api/v2/search.json": success({"results": [{"id": 1, "subject": "Login", "description": "Cannot sign in"}], "next_page": None})})

    result = TicketTools(client).search_tickets("status:open", projection={"fields": ["id", "subject"]})

    assert result["items"] == [{"id": 1, "subject": "Login"}]


def test_ticket_export_projection_selects_requested_ticket_fields():
    client = StubClient({"/api/v2/search/export.json": success({"results": [{"id": 1, "subject": "Login", "description": "Cannot sign in"}], "meta": {"has_more": False}})})

    result = TicketTools(client).export_tickets("status:open", projection={"fields": ["subject"]})

    assert result["data"]["items"] == [{"subject": "Login"}]


def test_ticket_export_writes_a_json_artifact_to_its_managed_cache(tmp_path):
    settings = Settings.load({"ZENDESK_ATTACHMENT_CACHE_ROOT": str(tmp_path / "attachments")})
    client = StubClient({"/api/v2/search/export.json": success({"results": [{"id": 1, "subject": "Login"}], "meta": {"has_more": False}})})

    result = TicketTools(client, settings).export_tickets("status:open", output_format="json")

    artifact = result["data"]
    assert artifact["format"] == "json"
    assert artifact["item_count"] == 1
    assert "items" not in artifact
    assert Path(artifact["cache_path"]).read_text() == '[{"id":1,"subject":"Login"}]'


def test_ticket_export_flattens_custom_objects_into_csv_columns(tmp_path):
    settings = Settings.load({"ZENDESK_ATTACHMENT_CACHE_ROOT": str(tmp_path / "attachments")})
    client = StubClient({"/api/v2/search/export.json": success({"results": [{"id": 1, "subject": "Login", "custom_objects": {"asset": [{"id": "9", "name": "Mac"}]}}], "meta": {"has_more": False}})})

    result = TicketTools(client, settings).export_tickets("status:open", output_format="csv")

    assert Path(result["data"]["cache_path"]).read_text() == "id,subject,asset.id,asset.name\n1,Login,9,Mac\n"


def test_custom_object_projection_nests_ticket_lookup_records():
    settings = Settings.load({"ZENDESK_CAPABILITIES": "support,custom_objects"})
    client = StubClient({
        "/api/v2/search.json": success({"results": [{"id": 1, "custom_fields": [{"id": 10, "value": "99"}]}], "next_page": None}),
        "/api/v2/ticket_fields.json": success({"ticket_fields": [{"id": 10, "relationship_target_type": "zen:custom_object:asset"}]}),
        "/api/v2/custom_objects/asset/records/99.json": success({"custom_object_record": {"id": "99"}}),
    })
    result = TicketTools(client, settings).search_tickets("status:open", projection={"include_custom_objects": ["asset"]})
    assert result["items"][0]["custom_objects"] == {"asset": [{"id": "99"}]}


def test_ticket_export_uses_dedicated_export_type_filter():
    client = StubClient({"/api/v2/search/export.json": success({"results": [{"id": 1}], "meta": {"has_more": True, "after_cursor": "next"}})})

    result = TicketTools(client).export_tickets("status:open", limit=50)

    assert result["data"]["items"] == [{"id": 1}]
    assert result["data"]["has_more"] is True
    assert result["data"]["next_cursor"] != "next"
    assert client.paths == [("/api/v2/search/export.json", {"filter[type]": "ticket", "query": "status:open", "page[size]": "50"})]


def test_ticket_export_uses_an_opaque_cursor_to_resume_the_same_query():
    first = StubClient({"/api/v2/search/export.json": success({"results": [{"id": 1}], "meta": {"has_more": True, "after_cursor": "zendesk-next"}})})
    cursor = TicketTools(first).export_tickets("status:open")["data"]["next_cursor"]
    second = StubClient({"/api/v2/search/export.json": success({"results": [{"id": 2}], "meta": {"has_more": False}})})

    TicketTools(second).export_tickets("status:open", cursor=cursor)

    assert cursor != "zendesk-next"
    assert second.paths == [("/api/v2/search/export.json", {"filter[type]": "ticket", "query": "status:open", "page[size]": "100", "page[after]": "zendesk-next"})]


def test_ticket_export_rejects_repeated_upstream_cursor():
    client = StubClient({"/api/v2/search/export.json": success({"results": [], "meta": {"has_more": True, "after_cursor": "same"}})})
    tools = TicketTools(client)
    cursor = tools.export_tickets("status:open")["data"]["next_cursor"]
    result = tools.export_tickets("status:open", cursor=cursor)
    assert result["ok"] is False
    assert result["error"]["code"] == "upstream_error"
    assert len(client.paths) == 2


def test_ticket_export_cursor_cannot_be_reused_for_another_query():
    client = StubClient({"/api/v2/search/export.json": success({"results": [], "meta": {"has_more": True, "after_cursor": "next"}})})
    tools = TicketTools(client)
    cursor = tools.export_tickets("status:open")["data"]["next_cursor"]
    result = tools.export_tickets("status:closed", cursor=cursor)
    assert result["error"]["code"] == "cursor_expired"
    assert len(client.paths) == 1


def test_ticket_export_rejects_an_expired_resume_cursor(monkeypatch):
    monkeypatch.setattr("zendesk_mcp_server.tools.tickets.time.time", lambda: 0)
    client = StubClient({"/api/v2/search/export.json": success({"results": [], "meta": {"has_more": True, "after_cursor": "zendesk-next"}})})
    cursor = TicketTools(client).export_tickets("status:open")["data"]["next_cursor"]
    monkeypatch.setattr("zendesk_mcp_server.tools.tickets.time.time", lambda: 3601)

    result = TicketTools(client).export_tickets("status:open", cursor=cursor)

    assert result["ok"] is False
    assert result["error"]["code"] == "cursor_expired"
    assert len(client.paths) == 1


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


def test_create_ticket_accepts_assignment_and_custom_fields():
    client = MutationStub()
    tools = TicketTools(client, Settings.load({"ZENDESK_WRITE_MODE": "standard"}))

    tools.create_ticket(requester_id=7, subject="Need help", description="Details", assignee_id=3, group_id=4, organization_id=5, custom_fields=[{"id": 12, "value": "gold"}])

    assert client.calls == [("POST", "/api/v2/tickets.json", {"ticket": {"requester_id": 7, "subject": "Need help", "comment": {"body": "Details"}, "assignee_id": 3, "group_id": 4, "organization_id": 5, "custom_fields": [{"id": 12, "value": "gold"}]}})]


def test_update_ticket_reuses_the_standard_write_guard_and_endpoint():
    client = MutationStub()
    settings = Settings.load({"ZENDESK_WRITE_MODE": "standard"})

    result = TicketTools(client, settings).update_ticket(9, status="pending", assignee_id=3)

    assert result["data"]["ticket"]["id"] == 9
    assert client.calls == [
        ("PUT", "/api/v2/tickets/9.json", {"ticket": {"status": "pending", "assignee_id": 3}})
    ]


def test_update_ticket_validates_custom_status_due_date_and_custom_fields():
    client = MutationStub()
    tools = TicketTools(client, Settings.load({"ZENDESK_WRITE_MODE": "standard"}))

    tools.update_ticket(9, custom_status_id=7, due_at="2026-09-05T12:00:00Z", custom_fields=[{"id": 12, "value": "gold"}])

    assert client.calls == [("PUT", "/api/v2/tickets/9.json", {"ticket": {"custom_status_id": 7, "due_at": "2026-09-05T12:00:00Z", "custom_fields": [{"id": 12, "value": "gold"}]}})]


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
        ("/api/v2/tickets/9.json", None),
        ("/api/v2/tickets/9/macros/4/apply.json", None),
        ("/api/v2/macros/4.json", None),
        ("/api/v2/tickets/9.json", None),
        ("/api/v2/tickets/9/macros/4/apply.json", None),
        ("/api/v2/macros/4.json", None),
    ]
    assert client.calls == [("PUT", "/api/v2/tickets/9.json", {"ticket": {"status": "pending", "comment": {"body": "We are checking", "public": True}, "safe_update": True, "updated_stamp": "2026-09-11T00:00:00Z"}})]
    assert result["data"]["ticket"]["id"] == 9


def test_macro_preview_sends_only_changed_writable_fields_and_update_stamp(tmp_path):
    class FullPreview(MacroStub):
        def get(self, path, *, params=None):
            if path == "/api/v2/tickets/9.json":
                return success({"ticket": {"id": 9, "status": "open", "subject": "Unchanged", "updated_at": "2026-09-11T00:00:00Z"}})
            if path.endswith("/apply.json"):
                return success({"result": {"ticket": {"id": 9, "status": "pending", "subject": "Unchanged", "url": "https://acme.zendesk.com/api/v2/tickets/9.json", "updated_at": "2026-09-11T00:00:00Z"}}})
            return super().get(path, params=params)

    client = FullPreview()
    store = ApprovalStore(tmp_path / "approvals.json")
    tools = TicketTools(client, Settings.load({"ZENDESK_WRITE_MODE": "standard", "ZENDESK_ENABLE_PUBLIC_WRITES": "true"}), store)
    preview = tools.apply_macro(9, 4)
    request_id = preview["data"]["approval_request_id"]
    result = tools.apply_macro(9, 4, execution_mode="apply", approval_request_id=request_id, approval_token=store.approve(request_id))
    assert result["ok"] is True
    assert client.calls == [("PUT", "/api/v2/tickets/9.json", {"ticket": {"status": "pending", "safe_update": True, "updated_stamp": "2026-09-11T00:00:00Z"}})]


def test_macro_rejects_approval_after_ticket_timestamp_changes(tmp_path):
    class ChangedTicket(MacroStub):
        stamp = "2026-09-11T00:00:00Z"
        def get(self, path, *, params=None):
            if path == "/api/v2/tickets/9.json":
                return success({"ticket": {"id": 9, "status": "open", "updated_at": self.stamp}})
            return super().get(path, params=params)

    client = ChangedTicket()
    store = ApprovalStore(tmp_path / "approvals.json")
    tools = TicketTools(client, Settings.load({"ZENDESK_WRITE_MODE": "standard", "ZENDESK_ENABLE_PUBLIC_WRITES": "true"}), store)
    request_id = tools.apply_macro(9, 4)["data"]["approval_request_id"]
    token = store.approve(request_id)
    client.stamp = "2026-09-11T00:01:00Z"
    result = tools.apply_macro(9, 4, execution_mode="apply", approval_request_id=request_id, approval_token=token)
    assert result["error"]["code"] == "approval_required"
    assert client.calls == []


def test_macro_unknown_write_outcome_includes_recovery_without_replay(tmp_path):
    from zendesk_mcp_server.contracts import failure, ErrorCode

    class TimedOutMacro(MacroStub):
        def request(self, method, path, *, json_body=None):
            self.calls.append((method, path, json_body))
            return failure(ErrorCode.TIMEOUT, "Zendesk request timed out", operation_state="unknown")

    client = TimedOutMacro()
    store = ApprovalStore(tmp_path / "approvals.json")
    tools = TicketTools(client, Settings.load({"ZENDESK_WRITE_MODE": "standard", "ZENDESK_ENABLE_PUBLIC_WRITES": "true"}), store)
    preview = tools.apply_macro(9, 4)
    request_id = preview["data"]["approval_request_id"]
    result = tools.apply_macro(9, 4, execution_mode="apply", approval_request_id=request_id, approval_token=store.approve(request_id))
    assert result["error"]["operation_state"] == "unknown"
    assert result["error"]["details"]["ticket_id"] == 9
    assert result["error"]["details"]["macro_id"] == 4
    assert result["error"]["details"]["recovery"] == "Inspect the ticket and its audits before creating a new approval; do not replay the macro automatically."
    assert len(client.calls) == 1


def test_ticket_macro_raises_all_gates_from_its_actions(tmp_path):
    client = RiskyMacroStub()
    store = ApprovalStore(tmp_path / "approvals.json")
    tools = TicketTools(
        client,
        Settings.load({
            "ZENDESK_WRITE_MODE": "standard",
            "ZENDESK_ENABLE_PUBLIC_WRITES": "true",
            "ZENDESK_ENABLE_DESTRUCTIVE_WRITES": "true",
            "ZENDESK_ENABLE_IMPERSONATION": "true",
        }),
        store,
    )

    preview = tools.apply_macro(9, 4)
    token = store.approve(preview["data"]["approval_request_id"])
    result = tools.apply_macro(
        9,
        4,
        execution_mode="apply",
        approval_request_id=preview["data"]["approval_request_id"],
        approval_token=token,
    )

    assert preview["data"]["required_risks"] == ["standard", "public", "destructive", "impersonation"]
    assert result["ok"] is True
    assert client.calls == [("PUT", "/api/v2/tickets/9.json", {"ticket": {"status": "pending", "safe_update": True, "updated_stamp": "2026-09-11T00:00:00Z"}})]


def test_macro_public_risk_handles_string_actions_and_unspecified_visibility():
    from zendesk_mcp_server.tools.tickets import _macro_risks
    from zendesk_mcp_server.write_policy import WriteRisk

    assert WriteRisk.PUBLIC in _macro_risks({}, [{"field": "comment_mode_is_public", "value": "true"}])
    assert WriteRisk.PUBLIC in _macro_risks({"comment": {"body": "Hello"}}, [])
    assert WriteRisk.PUBLIC not in _macro_risks({"comment": {"body": "Hello", "public": False}}, [{"field": "comment_mode_is_public", "value": "false"}])


def test_ticket_macro_rejects_an_invalid_action_definition():
    result = TicketTools(InvalidMacroStub()).apply_macro(9, 4)

    assert result["error"]["code"] == "upstream_error"


def test_attachment_download_revalidates_ownership_and_uses_fixed_cache(tmp_path):
    client = AttachmentDownloadStub()
    settings = Settings.load({"ZENDESK_ATTACHMENT_CACHE_ROOT": str(tmp_path / "cache")})

    result = TicketTools(client, settings).download_attachment(7, 5)

    assert client.downloads == [("https://acme.zendesk.com/attachments/token/log", 20 * 1024 * 1024)]
    assert result["data"]["attachment_id"] == 5
    assert result["data"]["cache_path"].endswith("/5/attachment")
    assert open(result["data"]["cache_path"], "rb").read() == b"hello world"


def test_attachment_download_removes_expired_cache_before_reusing_it(tmp_path):
    client = AttachmentDownloadStub()
    settings = Settings.load({"ZENDESK_ATTACHMENT_CACHE_ROOT": str(tmp_path / "cache")})
    first = TicketTools(client, settings).download_attachment(7, 5)
    os.utime(first["data"]["cache_path"], (time.time() - 25 * 60 * 60, ) * 2)

    second = TicketTools(client, settings).download_attachment(7, 5)

    assert second["data"]["cache_hit"] is False


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


def test_attachment_pdf_inspection_runs_in_a_bounded_subprocess(tmp_path):
    document = io.BytesIO(); writer = PdfWriter(); writer.add_blank_page(width=72, height=72); writer.write(document)
    settings = Settings.load({"ZENDESK_ATTACHMENT_CACHE_ROOT": str(tmp_path / "cache")})

    result = TicketTools(AttachmentDownloadStub(document.getvalue(), "application/pdf"), settings).inspect_attachment(7, 5)

    assert result["error"]["code"] == "unsupported"


def test_attachment_image_inspection_requires_a_bounded_subprocess(tmp_path):
    image = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
    settings = Settings.load({"ZENDESK_ATTACHMENT_CACHE_ROOT": str(tmp_path / "cache")})

    result = TicketTools(AttachmentDownloadStub(image, "image/png"), settings).inspect_attachment(7, 5)

    if result["ok"]:
        assert result["data"].get("kind") == "image"
        assert result["data"].get("width") == 1
    else:
        assert result["error"]["code"] == "unsupported"
        assert result["error"]["message"] == "Image inspection requires a bounded parser subprocess"
