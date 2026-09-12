from zendesk_mcp_server.approvals import ApprovalStore
from zendesk_mcp_server.contracts import success
from zendesk_mcp_server.config import Settings
from zendesk_mcp_server.tools.tickets import TicketTools
import io
import pytest
import os
import sys
import time
import zipfile
from pathlib import Path
from pypdf import PdfWriter


def test_export_name_collision_preserves_existing_result(tmp_path, monkeypatch):
    from zendesk_mcp_server.tools.tickets import _cache_ticket_export
    user_root = tmp_path / str(os.getuid()); user_root.mkdir()
    existing = user_root / "ticket-export-collision.json"
    existing.write_bytes(b"original export")
    monkeypatch.setattr("zendesk_mcp_server.tools.tickets.secrets.token_hex", lambda _: "collision")
    result = _cache_ticket_export(tmp_path, "json", b"new export")
    assert result["ok"] is False
    assert existing.read_bytes() == b"original export"
    assert list(user_root.iterdir()) == [existing]


def test_export_stream_failure_removes_only_its_own_partial_file(tmp_path, monkeypatch):
    from zendesk_mcp_server.tools.tickets import _cache_ticket_export
    def broken_content():
        yield b"["
        raise ValueError("private serialization contents")
    result = _cache_ticket_export(tmp_path, "json", broken_content())
    assert result["error"]["code"] == "upstream_error"
    assert "private serialization contents" not in str(result)
    user_root = tmp_path / str(os.getuid())
    assert list(user_root.iterdir()) == []
    monkeypatch.setattr("zendesk_mcp_server.tools.tickets.secrets.token_hex", lambda _: "collision")
    existing = user_root / ".ticket-export-collision.json.tmp"
    existing.write_bytes(b"existing export")
    result = _cache_ticket_export(tmp_path, "json", b"[]")
    assert result["ok"] is False
    assert existing.read_bytes() == b"existing export"


def test_cache_uses_validated_windows_sid_without_getuid(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from zendesk_mcp_server.tools.tickets import _cache_ticket_export
    monkeypatch.delattr(os, "getuid", raising=False)
    monkeypatch.setenv("SystemRoot", str(tmp_path))
    def whoami(command, **kwargs):
        assert command == [str(tmp_path / "System32" / "whoami.exe"), "/user", "/fo", "csv", "/nh"]
        return SimpleNamespace(stdout=b'"DOMAIN\\user","S-1-5-21-123-456-789-1001"\r\n')
    monkeypatch.setattr("zendesk_mcp_server.tools.tickets.subprocess.run", whoami)
    result = _cache_ticket_export(tmp_path, "json", b"[]")
    assert Path(result["data"]["cache_path"]).parent.name == "S-1-5-21-123-456-789-1001"
    monkeypatch.setattr("zendesk_mcp_server.tools.tickets.subprocess.run", lambda *a, **k: SimpleNamespace(stdout=b'"user","../../other"'))
    assert _cache_ticket_export(tmp_path, "json", b"[]")["ok"] is False


def test_windows_identity_failure_never_falls_back_to_shared_cache(tmp_path, monkeypatch):
    import subprocess
    from zendesk_mcp_server.tools.tickets import _cache_attachment, _cache_ticket_export, _clean_attachment_cache, _clean_export_cache
    monkeypatch.delattr(os, "getuid", raising=False)
    monkeypatch.setenv("SystemRoot", str(tmp_path))
    sentinel = tmp_path / "other-user"
    sentinel.mkdir()
    keep = sentinel / "keep.json"
    keep.write_text("existing")
    for error in (subprocess.TimeoutExpired("whoami", 5), subprocess.CalledProcessError(1, "whoami"), FileNotFoundError()):
        def unavailable(*args, **kwargs): raise error
        monkeypatch.setattr("zendesk_mcp_server.tools.tickets.subprocess.run", unavailable)
        assert _cache_attachment(tmp_path, 1, b"private")["error"]["code"] == "upstream_error"
        assert _cache_ticket_export(tmp_path, "json", b"[]")["error"]["code"] == "upstream_error"
        _clean_attachment_cache(tmp_path)
        _clean_export_cache(tmp_path)
        assert list(tmp_path.iterdir()) == [sentinel]
        assert keep.read_text() == "existing"


class StubClient:
    def __init__(self, responses):
        self.responses = responses
        self.paths = []

    def get(self, path, *, params=None):
        self.paths.append((path, params))
        return self.responses[path]


def test_conversation_log_preserves_bot_rich_content_and_private_comments():
    events = [
        {"id": "event-a", "type": "Messaging::ConversationMessage", "author": {"type": "bot", "display_name": "Helper"}, "content": {"type": "text", "text": "Hello", "actions": [{"reply": {"text": "Human", "payload": "route"}}]}, "attachments": [], "metadata": {}, "reference": "zen:sunco:conversation_message:a", "created_at": "2026-09-01T00:00:00Z"},
        {"id": "event-b", "type": "Comment", "author": {"type": "agent", "display_name": "Agent"}, "content": {"type": "html", "body": "<p>Private</p>"}, "attachments": [{"id": 3, "inline": True}], "metadata": {"public": False}, "reference": "zen:ticket_event:3", "created_at": "2026-09-01T00:01:00Z"},
    ]
    class Client:
        def get(self, path, *, params=None):
            assert path == "/api/v2/tickets/9/conversation_log"
            assert params["sort"] == "created_at"
            after = params.get("page[after]")
            assert after in (None, "next")
            return success({"events": [events[1 if after else 0]], "meta": {"has_more": not after, "after_cursor": "next"}, "links": {"next": "https://untrusted.invalid/ignored"}})
    result = TicketTools(Client()).get_conversation(9, source="conversation_log", limit=2)
    assert result["data"] == {"source": "conversation_log", "events": [{**event, "untrusted_user_content": True} for event in events], "has_more": False, "next_cursor": None, "truncated": False}


def test_conversation_log_errors_do_not_fall_back_to_comments():
    from zendesk_mcp_server.contracts import failure, ErrorCode
    error = failure(ErrorCode.PERMISSION_DENIED, "denied")
    class Client:
        def get(self, path, *, params=None):
            assert path.endswith("/conversation_log")
            return error
    assert TicketTools(Client()).get_conversation(9, source="conversation_log") == error
    assert TicketTools(None).get_conversation(9, source="unknown")["error"]["code"] == "validation_error"


class MutationStub:
    def __init__(self):
        self.calls = []
        self.get_paths = []

    def request(self, method, path, *, json_body=None):
        self.calls.append((method, path, json_body))
        return success({"ticket": {"id": 9}})

    def get(self, path, *, params=None):
        self.get_paths.append((path, params))
        return success({"ticket": {"id": 9, "tags": ["billing"], "updated_at": "2026-09-01T00:00:00Z"}})


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
        return success({"meta": {"has_more": False}, "comments": [{"id": 3, "attachments": [{"id": 5, "file_name": "log.txt", "size": 12, "content_url": "https://acme.zendesk.com/attachments/token/log", "malware_scan_result": "malware_not_found"}]}]})
    def download_attachment(self, content_url, *, max_bytes, store):
        self.downloads.append((content_url, max_bytes))
        stored = store(iter([self.content]))
        return success({**stored["data"], "content_type": self.content_type, "size": len(self.content)}) if stored.get("ok") else stored


def test_get_ticket_rejects_zero_without_a_client():
    result = TicketTools(None).get_ticket(0)

    assert result["error"]["code"] == "validation_error"


def test_ticket_list_collects_total_limit_across_pages():
    calls = []
    class Client:
        def get(self, path, *, params=None):
            calls.append(params)
            assert path == "/api/v2/tickets.json"
            if params.get("page[after]") == "next":
                assert params["page[size]"] == "50"
                return success({"tickets": [{"id": i} for i in range(100, 150)], "meta": {"has_more": False}})
            assert params["page[size]"] == "100"
            return success({"tickets": [{"id": i} for i in range(100)], "meta": {"has_more": True, "after_cursor": "next"}})
    result = TicketTools(Client()).list_tickets(150)
    assert [item["id"] for item in result["items"]] == list(range(150))
    assert result["has_more"] is False and len(calls) == 2


def test_ticket_list_can_resume_and_clear_final_cursor():
    client = StubClient({"/api/v2/tickets.json": success({"tickets": [], "meta": {"has_more": False, "after_cursor": "next"}})})
    result = TicketTools(client).list_tickets(2, cursor="next")
    assert client.paths == [("/api/v2/tickets.json", {"page[size]": "2", "page[after]": "next"})]
    assert result == {"ok": True, "items": [], "has_more": False, "next_cursor": None, "truncated": False}


def test_list_tickets_normalizes_cursor_response():
    client = StubClient(
        {
            "/api/v2/tickets.json": success(
                {"tickets": [{"id": 1}], "meta": {"has_more": True, "after_cursor": "next"}}
            )
        }
    )

    result = TicketTools(client).list_tickets(limit=1)

    assert result["items"] == [{"id": 1}]
    assert result["has_more"] is True
    assert result["next_cursor"] == "next"
    assert result["truncated"] is True
    assert client.paths == [("/api/v2/tickets.json", {"page[size]": "1"})]


def test_conversation_and_consumers_include_later_comments():
    class Client:
        def get(self, path, *, params=None):
            if path.endswith("/7.json"): return success({"ticket": {"id": 7, "subject": "Subject"}})
            if params and params.get("page[after]") == "next":
                return success({"comments": [{"id": 2, "body": "later", "attachments": [{"id": 5}]}], "meta": {"has_more": False}})
            return success({"comments": [{"id": 1, "body": "first"}], "meta": {"has_more": True, "after_cursor": "next"}})
    tools = TicketTools(Client())
    assert [item["id"] for item in tools.get_conversation(7)["data"]["comments"]] == [1, 2]
    first = tools.get_conversation(7, limit=1)["data"]
    assert first["truncated"] is True and first["next_cursor"] == "next"
    second = tools.get_conversation(7, limit=1, cursor=first["next_cursor"])["data"]
    assert second["comments"][0]["id"] == 2 and second["has_more"] is False
    assert tools.list_attachments(7)["data"]["attachments"][0]["id"] == 5
    assert "later" in tools.ticket_to_issue_context(7)["data"]["markdown"]


def test_conversation_consumers_propagate_later_failure_without_download():
    from zendesk_mcp_server.contracts import failure, ErrorCode
    class Client:
        def get(self, path, *, params=None):
            if path.endswith("/7.json"): return success({"ticket": {"id": 7}})
            if params.get("page[after]") == "next": return failure(ErrorCode.PERMISSION_DENIED, "denied")
            return success({"comments": [{"id": 1, "attachments": []}], "meta": {"has_more": True, "after_cursor": "next"}})
        def download_attachment(self, *args, **kwargs): raise AssertionError("must not download after incomplete ownership lookup")
    tools = TicketTools(Client())
    for result in (tools.list_attachments(7), tools.download_attachment(7, 5), tools.ticket_to_issue_context(7)):
        assert result["ok"] is False
        assert result["error"]["code"] == "permission_denied"


def test_conversation_sides_use_sideloaded_roles_not_visibility():
    class Client:
        def get(self, path, *, params=None):
            assert params["include"] == "users"
            return success({"comments": [{"id": i, "author_id": i, "public": True} for i in range(1, 5)], "users": [{"id": 1, "role": "end-user"}, {"id": 2, "role": "agent"}, {"id": 3, "role": "admin"}], "meta": {"has_more": False}})
    comments = TicketTools(Client()).get_conversation(7)["data"]["comments"]
    assert [c["side"] for c in comments] == ["customer", "agent", "agent", "unknown"]
    assert [c.get("author_role") for c in comments] == ["end-user", "agent", "admin", None]
    assert all(c["public"] is True for c in comments)


def test_conversation_names_use_sideloads_without_role_or_name_guessing():
    class Client:
        def get(self, path, *, params=None):
            return success({"comments": [{"author_id": 1}, {"author_id": 2}, {"author_id": 3}, {"author_id": True}], "users": [{"id": 1, "name": "Alex", "role": "agent"}, {"id": 2, "name": "Sam"}, {"id": 3, "name": None}], "meta": {"has_more": False}})
    comments = TicketTools(Client()).get_conversation(7)["data"]["comments"]
    assert [item.get("author_name") for item in comments] == ["Alex", "Sam", None, None]
    assert comments[1]["side"] == "unknown"


def test_conversation_image_placeholders_preserve_raw_html_in_both_sources():
    html = '<p>Before &amp; after<img src="https://untrusted.invalid/x" onerror="alert(1)">tail</p><p><IMG alt="Screenshot" src="cid:1" /></p>'
    for source in ("comments", "conversation_log"):
        record = {"id": 1, "html_body": html} if source == "comments" else {"id": "event-a", "content": {"type": "html", "body": html}}
        key = "comments" if source == "comments" else "events"
        class Client:
            def get(self, path, *, params=None):
                assert path == ("/api/v2/tickets/7/comments.json" if source == "comments" else "/api/v2/tickets/7/conversation_log")
                return success({key: [record], "meta": {"has_more": False}})
        result = TicketTools(Client()).get_conversation(7, source=source)["data"][key][0]
        assert result["display_text"] == "Before & after[image]tail\n[image: Screenshot]"
        assert all(result[field] == value for field, value in record.items())
        assert result["untrusted_user_content"] is True


def test_malformed_image_html_does_not_break_conversation_read():
    html = '<img src="cid:1"><![invalid]>'
    client = StubClient({"/api/v2/tickets/7/comments.json": success({"comments": [{"id": 1, "html_body": html}], "meta": {"has_more": False}})})
    result = TicketTools(client).get_conversation(7)
    assert result["ok"] is True
    assert result["data"]["comments"][0]["html_body"] == html


def test_conversation_auto_uses_messaging_flag_and_reports_resolved_source():
    for messaging in (True, False):
        class Client:
            def get(self, path, *, params=None):
                if path == "/api/v2/tickets/7.json":
                    return success({"ticket": {"id": 7, "from_messaging_channel": messaging}})
                assert path == ("/api/v2/tickets/7/conversation_log" if messaging else "/api/v2/tickets/7/comments.json")
                return success({("events" if messaging else "comments"): [], "meta": {"has_more": False}})
        result = TicketTools(Client()).get_conversation(7, source="auto")
        assert result["ok"] is True
        assert result["data"]["source"] == ("conversation_log" if messaging else "comments")


def test_conversation_auto_cannot_guess_or_reinterpret_a_cursor():
    for value in (None, 1, "false"):
        class Client:
            def get(self, path, *, params=None):
                assert path == "/api/v2/tickets/7.json"
                return success({"ticket": {"id": 7, "from_messaging_channel": value}})
        assert TicketTools(Client()).get_conversation(7, source="auto")["error"]["code"] == "upstream_error"
    assert TicketTools(None).get_conversation(7, source="auto", cursor="next")["error"]["code"] == "validation_error"


def test_conversation_roles_across_pages_preserve_raw_fields_and_unknown_authors():
    class Client:
        def get(self, path, *, params=None):
            assert params["include"] == "users"
            if params.get("page[after]") == "next":
                return success({"comments": [{"author_id": 2, "side": "original", "author_role": "original"}, {"author_id": []}, {"author_id": True}], "users": [{"id": 2, "role": "agent"}, "Deleted user", {"id": [], "role": "admin"}], "meta": {"has_more": False}})
            return success({"comments": [{"author_id": 1, "public": False}], "users": [{"id": 1, "role": "end-user"}], "meta": {"has_more": True, "after_cursor": "next"}})
    comments = TicketTools(Client()).get_conversation(7)["data"]["comments"]
    assert [c["side"] for c in comments] == ["customer", "original", "unknown", "unknown"]
    assert comments[0]["public"] is False
    assert comments[1]["author_role"] == "original"
    assert all("author_role" not in c for c in comments[2:])


def test_conversation_rejects_malformed_sideload_collection():
    class Client:
        def get(self, path, *, params=None):
            return success({"comments": [], "users": {}, "meta": {"has_more": False}})
    result = TicketTools(Client()).get_conversation(7)
    assert result["ok"] is False
    assert result["error"]["code"] == "upstream_error"


def test_conversation_normalizes_channel_without_losing_original_fields():
    channels = ["email", "web", "api", "mobile", "chat", "messaging", "whatsapp", "future_channel", 44]
    comments = [{"id": i, "author_id": 7, "public": False, "side": "original", "metadata": {"via": {"channel": channel}}, "body": "text"} for i, channel in enumerate(channels)]
    class Client:
        def get(self, path, *, params=None): return success({"comments": comments, "meta": {"has_more": False}})
    result = TicketTools(Client()).get_conversation(7)["data"]["comments"]
    assert [item["channel"] for item in result] == channels
    for original, item in zip(comments, result):
        assert all(item[key] == value for key, value in original.items())
        assert item["untrusted_user_content"] is True


def test_conversation_marks_customer_text_untrusted():
    client = StubClient(
        {
            "/api/v2/tickets/7/comments.json": success(
                {"comments": [{"id": 3, "body": "hello", "html_body": "<p>hello</p>"}], "meta": {"has_more": False}}
            )
        }
    )

    result = TicketTools(client).get_conversation(7)

    assert result["data"]["comments"][0]["untrusted_user_content"] is True


def test_ticket_list_keeps_sort_across_cursor_pages():
    class Client:
        def get(self, path, *, params=None):
            assert params["sort"] == "-updated_at"
            if params.get("page[after]") == "next":
                return success({"tickets": [{"id": 1}], "meta": {"has_more": False}})
            return success({"tickets": [{"id": 2}], "meta": {"has_more": True, "after_cursor": "next"}})
    assert TicketTools(Client()).list_tickets(2, sort="-updated_at")["items"] == [{"id": 2}, {"id": 1}]
    assert TicketTools(Client()).list_tickets(2, sort="created_at")["error"]["code"] == "validation_error"


def test_time_tracking_app_reads_fields_and_updates_with_conflict_guard():
    class Client(MutationStub):
        def get(self, path, *, params=None):
            assert path == "/api/v2/tickets/9.json"
            return success({"ticket": {"id": 9, "updated_at": "2026-09-12T00:00:00Z", "custom_fields": [{"id": 12, "value": "60"}, {"id": 34, "value": None}]}})
    client = Client()
    tools = TicketTools(client, Settings.load({"ZENDESK_WRITE_MODE": "standard", "ZENDESK_TIME_TRACKING_TOTAL_FIELD_ID": "12", "ZENDESK_TIME_TRACKING_LAST_FIELD_ID": "34"}))
    result = tools.get_time_tracking(9)
    assert result["data"]["backend"] == "custom_fields"
    assert result["data"]["total_time_spent_sec"] == 60
    assert result["data"]["time_spent_last_update_sec"] == 0
    assert tools.log_time(9, "1m2s", "Investigation")["ok"] is True
    assert client.calls == [("PUT", "/api/v2/tickets/9.json", {"ticket": {"custom_fields": [{"id": 12, "value": 122}, {"id": 34, "value": 62}], "comment": {"body": "Investigation", "public": False}, "safe_update": True, "updated_stamp": "2026-09-12T00:00:00Z"}})]


def test_time_tracking_app_rejects_bad_snapshot_and_never_replays_conflicts():
    class Client(MutationStub):
        value = 60
        timestamp = "2026-09-12T00:00:00Z"
        def get(self, path, *, params=None):
            return success({"ticket": {"updated_at": self.timestamp, "custom_fields": [{"id": 12, "value": self.value}, {"id": 34, "value": 0}]}})
        def request(self, method, path, *, json_body=None):
            self.calls.append((method, path, json_body))
            return {"ok": False, "error": {"code": "conflict", "operation_state": "not_applied"}}
    config = {"ZENDESK_WRITE_MODE": "standard", "ZENDESK_TIME_TRACKING_TOTAL_FIELD_ID": "12", "ZENDESK_TIME_TRACKING_LAST_FIELD_ID": "34"}
    client = Client()
    tools = TicketTools(client, Settings.load(config))
    for bad in (True, -1, 1.5, "-2", "oops", [], {}):
        client.value = bad
        assert tools.log_time(9, "1m", "Work")["error"]["code"] == "upstream_error"
    client.value = 60
    client.timestamp = None
    assert tools.log_time(9, "1m", "Work")["error"]["code"] == "upstream_error"
    assert client.calls == []
    client.timestamp = "2026-09-12T00:00:00Z"
    assert tools.log_time(9, "1m", "Work")["error"]["code"] == "conflict"
    assert len(client.calls) == 1
    readonly = TicketTools(client, Settings.load({**config, "ZENDESK_WRITE_MODE": "read_only"}))
    assert readonly.log_time(9, "1m", "Work")["error"]["code"] == "write_disabled"
    assert len(client.calls) == 1


def test_time_tracking_app_missing_or_duplicate_fields_do_not_fallback():
    settings = Settings.load({"ZENDESK_TIME_TRACKING_TOTAL_FIELD_ID": "12", "ZENDESK_TIME_TRACKING_LAST_FIELD_ID": "34"})
    for fields in (None, [], [{"id": 12, "value": 0}], [{"id": 12, "value": 0}, {"id": 12, "value": 1}, {"id": 34, "value": 0}], [{"id": "12", "value": 0}, {"id": 34, "value": 0}]):
        client = StubClient({"/api/v2/tickets/9.json": success({"ticket": {"custom_fields": fields}})})
        result = TicketTools(client, settings).get_time_tracking(9)
        assert result["error"]["code"] == "upstream_error"


def test_time_tracking_malformed_field_entries_block_writes():
    class Client(MutationStub):
        extra = None
        def get(self, path, *, params=None):
            return success({"ticket": {"updated_at": "2026-09-12T00:00:00Z", "custom_fields": [{"id": 12, "value": 60}, {"id": 34, "value": 0}, self.extra]}})
    client = Client()
    tools = TicketTools(client, Settings.load({"ZENDESK_WRITE_MODE": "standard", "ZENDESK_TIME_TRACKING_TOTAL_FIELD_ID": "12", "ZENDESK_TIME_TRACKING_LAST_FIELD_ID": "34"}))
    for extra in ({"id": 12}, {"id": 34}, None, "invalid"):
        client.extra = extra
        result = tools.log_time(9, "1m", "Work")
        assert result["ok"] is False and result["error"]["code"] == "upstream_error"
        assert client.calls == []


def test_time_tracking_rejects_unbounded_or_non_ascii_duration_before_io():
    client = MutationStub()
    tools = TicketTools(client, Settings.load({"ZENDESK_WRITE_MODE": "standard", "ZENDESK_TIME_TRACKING_TOTAL_FIELD_ID": "12", "ZENDESK_TIME_TRACKING_LAST_FIELD_ID": "34"}))
    for duration in ("9" * 5000 + "h", "1９h"):
        result = tools.log_time(9, duration, "Work")
        assert result["error"]["code"] == "validation_error"
    assert client.calls == []
    assert client.get_paths == []


def test_time_tracking_resumes_after_audits_without_time_metadata():
    class Client:
        def get(self, path, *, params=None):
            assert path == "/api/v2/tickets/9/audits.json"
            assert params["include_boundary_indicators"] == "true"
            if params.get("page[after]") == "next":
                return success({"audits": [{"id": 2, "metadata": {"custom": {"time_spent": "2m"}}}], "meta": {"has_more": False}})
            return success({"audits": [{"id": 1, "metadata": {}}], "meta": {"has_more": True, "after_cursor": "next"}})
    tools = TicketTools(Client())
    first = tools.get_time_tracking(9, limit=1)["data"]
    assert first == {"entries": [], "has_more": True, "next_cursor": "next", "truncated": True}
    second = tools.get_time_tracking(9, limit=1, cursor=first["next_cursor"])["data"]
    assert second["entries"][0]["time_spent"] == "2m"
    assert second["has_more"] is False
    assert tools.get_time_tracking(9)["data"]["entries"] == second["entries"]


def test_time_tracking_reads_audit_metadata_and_logs_with_an_internal_note():
    class TimeClient(MutationStub):
        def get(self, path, *, params=None):
            self.get_paths.append((path, params))
            return success({"audits": [{"id": 4, "created_at": "2026-09-06T00:00:00Z", "author_id": 2, "metadata": {"custom": {"time_spent": "4m12s"}}}], "meta": {"has_more": False}})

    client = TimeClient()
    tools = TicketTools(client, Settings.load({"ZENDESK_WRITE_MODE": "standard"}))

    assert tools.get_time_tracking(9)["data"]["entries"] == [{"audit_id": 4, "created_at": "2026-09-06T00:00:00Z", "author_id": 2, "time_spent": "4m12s"}]
    assert tools.log_time(9, "4m12s", "Investigated login issue")["ok"] is True
    assert client.get_paths == [("/api/v2/tickets/9/audits.json", {"page[size]": "100", "include_boundary_indicators": "true"})]
    assert client.calls == [("PUT", "/api/v2/tickets/9.json", {"ticket": {"comment": {"body": "Investigated login issue", "public": False}, "metadata": {"time_spent": "4m12s"}}})]


def test_git_zen_distinguishes_empty_links_from_invalid_field_responses():
    settings = Settings.load({"ZENDESK_GIT_ZEN_FIELD_ID": "7"})
    cases = [({}, "upstream_error"), ({"ticket": {}}, "upstream_error"),
             ({"ticket": {"custom_fields": []}}, "not_configured"),
             ({"ticket": {"custom_fields": [{"id": 7, "value": 42}]}}, "upstream_error"),
             ({"ticket": {"custom_fields": [{"id": 7}]}}, "upstream_error"),
             ({"ticket": {"custom_fields": [{"id": 7, "value": ""}, {"id": 7, "value": ""}]}}, "upstream_error")]
    for data, code in cases:
        client = StubClient({"/api/v2/tickets/9.json": success(data)})
        assert TicketTools(client, settings).get_git_zen_links(9)["error"]["code"] == code
    for value in (None, "", "No linked issue"):
        client = StubClient({"/api/v2/tickets/9.json": success({"ticket": {"custom_fields": [{"id": 7, "value": value}]}})})
        assert TicketTools(client, settings).get_git_zen_links(9) == success({"links": []})


def test_git_zen_extracts_github_commit_links_without_markdown_delimiters():
    commit = "https://github.com/acme/app/commit/" + "a1" * 20
    client = StubClient({"/api/v2/tickets/9.json": success({"ticket": {"custom_fields": [{"id": 7, "value": f"[commit]({commit}) {commit} https://github.com/acme/app/issues/4"}]}})})
    result = TicketTools(client, Settings.load({"ZENDESK_GIT_ZEN_FIELD_ID": "7"})).get_git_zen_links(9)
    assert result["data"]["links"] == [commit, "https://github.com/acme/app/issues/4"]


def test_git_zen_gitlab_links_exclude_markup_delimiters():
    links = ["https://gitlab.com/acme/team/app/-/issues/4", "https://gitlab.com/acme/team/app/-/merge_requests/3", "https://gitlab.com/acme/team/app/-/commit/" + "b2" * 20]
    text = f'[issue]({links[0]}), <a href="{links[1]}">MR</a> <{links[2]}>'
    client = StubClient({"/api/v2/tickets/9.json": success({"ticket": {"custom_fields": [{"id": 7, "value": text}]}})})
    result = TicketTools(client, Settings.load({"ZENDESK_GIT_ZEN_FIELD_ID": "7"})).get_git_zen_links(9)
    assert result["data"]["links"] == links


def test_git_zen_links_are_extracted_only_from_the_configured_ticket_field():
    client = StubClient({"/api/v2/tickets/9.json": success({"ticket": {"custom_fields": [{"id": 7, "value": "https://github.com/acme/app/issues/4 https://gitlab.com/acme/app/-/merge_requests/3"}, {"id": 8, "value": "https://github.com/acme/app/pull/9"}]}})})

    result = TicketTools(client, Settings.load({"ZENDESK_GIT_ZEN_FIELD_ID": "7"})).get_git_zen_links(9)

    assert result["data"]["links"] == ["https://github.com/acme/app/issues/4", "https://gitlab.com/acme/app/-/merge_requests/3"]


def test_ticket_attachment_metadata_is_extracted_from_comments():
    client = StubClient({"/api/v2/tickets/7/comments.json": success({"meta": {"has_more": False}, "comments": [{"id": 3, "attachments": [{"id": 5, "file_name": "log.txt", "size": 12, "malware_scan_result": "malware_not_found"}]}]})})

    result = TicketTools(client).list_attachments(7)

    assert result["data"]["attachments"] == [{"id": 5, "file_name": "log.txt", "size": 12, "malware_scan_result": "malware_not_found", "ticket_id": 7, "comment_id": 3, "untrusted_user_content": True}]


def test_attachment_download_requires_safe_scan_and_size():
    assert TicketTools.attachment_is_safe_to_download({"size": 20 * 1024 * 1024, "malware_scan_result": "malware_not_found"}) is True
    assert TicketTools.attachment_is_safe_to_download({"size": 20 * 1024 * 1024 + 1, "malware_scan_result": "malware_not_found"}) is False
    assert TicketTools.attachment_is_safe_to_download({"size": 1, "malware_scan_result": "not_scanned"}) is False


def test_attachment_download_rejects_unscanned_content_before_downloading():
    client = AttachmentDownloadStub()
    client.get = lambda *_args, **_kwargs: success({"meta": {"has_more": False}, "comments": [{"id": 3, "attachments": [{"id": 5, "size": 12, "content_url": "https://acme.zendesk.com/attachments/token/log", "malware_scan_result": "not_scanned"}]}]})

    result = TicketTools(client).download_attachment(7, 5)

    assert result["error"]["code"] == "unsafe_attachment"
    assert client.downloads == []


def test_issue_context_stops_after_ticket_lookup_failure():
    from zendesk_mcp_server.contracts import ErrorCode, failure
    for code in (ErrorCode.NOT_FOUND, ErrorCode.PERMISSION_DENIED, ErrorCode.RATE_LIMITED):
        expected = failure(code, "lookup failed")
        class Client:
            def get(self, path, *, params=None):
                assert path == "/api/v2/tickets/7.json", "failed ticket lookup must not trigger conversation requests"
                return expected
        assert TicketTools(Client()).ticket_to_issue_context(7) == expected


def test_issue_context_preserves_conversation_attribution_and_trust_marker():
    client = StubClient({"/api/v2/tickets/7.json": success({"ticket": {"subject": "Issue"}}), "/api/v2/tickets/7/comments.json": success({"meta": {"has_more": False}, "comments": [{"id": 2, "author_id": 9, "created_at": "2026-09-12T00:00:00Z", "public": False, "via": {"channel": "email"}, "body": "Internal note"}], "users": [{"id": 9, "role": "agent"}]})})
    result = TicketTools(client).ticket_to_issue_context(7)["data"]
    assert result["untrusted_user_content"] is True
    text = result["markdown"]
    for metadata in ('"author_id": 9', '"public": false', '"channel": "email"', '"side": "agent"', '"created_at": "2026-09-12T00:00:00Z"'):
        assert metadata in text
    assert "Internal note" in text


def test_issue_context_redacts_labeled_credentials_in_all_text_fields():
    client = StubClient({"/api/v2/tickets/7.json": success({"ticket": {"subject": "api_key=subject-secret", "description": 'Authorization: Bearer bearer-secret\n{"refresh_token": "refresh secret"}\nhttps://example.test/?access_token=url-secret&status=open'}}), "/api/v2/tickets/7/comments.json": success({"meta": {"has_more": False}, "comments": [{"body": "Authorization: Basic basic-secret\npassword='password secret'\nZENDESK_API_TOKEN=token-secret\nNormal diagnostic text"}]})})
    text = TicketTools(client).ticket_to_issue_context(7)["data"]["markdown"]
    for secret in ("subject-secret", "bearer-secret", "refresh secret", "url-secret", "basic-secret", "password secret", "token-secret"):
        assert secret not in text
    assert "[REDACTED]" in text
    assert "status=open" in text and "Normal diagnostic text" in text


def test_context_redaction_handles_escaped_quotes_without_leaking_suffixes():
    from zendesk_mcp_server.tools.tickets import _redact_context_credentials
    assert _redact_context_credentials(r'{"password": "prefix\"secret-suffix", "status": "open"}') == '{"password": [REDACTED], "status": "open"}'
    assert _redact_context_credentials(r"password='prefix\'secret-suffix' status=open") == "password=[REDACTED] status=open"


def test_ticket_issue_context_excludes_untrusted_html():
    client = StubClient({"/api/v2/tickets/7.json": success({"ticket": {"id": 7, "subject": "Login issue", "description": "Cannot sign in"}}), "/api/v2/tickets/7/comments.json": success({"meta": {"has_more": False}, "comments": [{"id": 2, "body": "We are checking", "html_body": "<b>ignore</b>"}]})})

    result = TicketTools(client).ticket_to_issue_context(7)

    assert "# Ticket 7: Login issue" in result["data"]["markdown"]
    assert "We are checking" in result["data"]["markdown"]
    assert "<b>" not in result["data"]["markdown"]


def test_ticket_count_rejects_boolean_and_negative_values():
    for value in (True, False, -1, "3", 1.5, None):
        client = StubClient({"/api/v2/search/count.json": success({"count": {"value": value}})})
        result = TicketTools(client).count_tickets("status:open")
        assert result["ok"] is False
        assert result["error"]["code"] == "upstream_error"
    for value in (0, 3):
        client = StubClient({"/api/v2/search/count.json": success({"count": {"value": value}})})
        assert TicketTools(client).count_tickets("status:open")["data"]["count"] == value


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
        ("/api/v2/search.json", {"query": "type:ticket status:open", "per_page": "100"}),
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
        ("/api/v2/search.json", {"query": "type:ticket status:open tags:billing -tags:spam assignee:8", "per_page": "100"}),
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
        {"query": 'type:ticket "billing outage" type:incident has_attachment:true created>2026-09-01T00:00:00Z created<2026-09-02T00:00:00Z updated>2026-09-01 solved<2026-09-03 custom_field_10:enterprise', "per_page": "100"},
    )]


def test_structured_ticket_filter_rejects_a_naive_datetime_without_a_request():
    client = StubClient({})

    result = TicketTools(client).search_tickets({"created": {"after": "2026-09-01T00:00:00"}})

    assert result["error"]["code"] == "validation_error"
    assert client.paths == []


def test_structured_ticket_filter_resolves_an_exact_user_email_before_searching():
    client = StubClient({
        "/api/v2/users/search.json": success({"users": [{"id": 8, "email": "agent@example.test"}], "next_page": None}),
        "/api/v2/search.json": success({"results": [], "next_page": None}),
    })

    TicketTools(client).search_tickets({"assignee": {"kind": "email", "value": "agent@example.test"}})

    assert client.paths == [
        ("/api/v2/users/search.json", {"query": "agent@example.test", "per_page": "100", "page": "1"}),
        ("/api/v2/search.json", {"query": "type:ticket assignee:8", "per_page": "100"}),
    ]


def test_user_resolver_rejects_ambiguity_on_later_page_for_all_queries():
    class Client:
        def get(self, path, *, params=None):
            assert path == "/api/v2/users/search.json", "Ambiguous user must not reach ticket query"
            page = params.get("page", "1")
            assert params["query"] == "Same"
            return success({"users": [{"id": 8 if page == "1" else 9, "name": "Same"}], "next_page": "https://untrusted.invalid/ignored" if page == "1" else None})
    tools = TicketTools(Client())
    for method in (tools.search_tickets, tools.count_tickets, tools.export_tickets):
        result = method({"requester": {"kind": "name", "value": "Same"}})
        assert result["error"]["code"] == "validation_error"
        assert result["error"]["details"]["candidate_ids"] == [8, 9]


def test_user_reference_variants_reach_all_three_ticket_consumers():
    variants = [({"kind": "me"}, "me"), ({"kind": "none"}, "none"), ({"kind": "id", "value": 8}, "8"), ({"kind": "name", "value": "Alex"}, "8"), ({"kind": "email", "value": "alex@example.test"}, "8"), ({"kind": "phone", "value": "+821012345678"}, "8")]
    for field in ("assignee", "requester"):
        for reference, expected in variants:
            for method in ("search_tickets", "count_tickets", "export_tickets"):
                calls = []
                class Client:
                    def get(self, path, *, params=None):
                        calls.append(path)
                        if path == "/api/v2/users/search.json":
                            assert params["query"] == reference["value"]
                            return success({"users": [{"id": 8, "name": "Alex", "email": "alex@example.test", "phone": "+821012345678"}], "next_page": None})
                        assert params["query"] == ("" if method == "export_tickets" else "type:ticket ") + f"{field}:{expected}"
                        return success({"results": [], "next_page": None, "meta": {"has_more": False}, "count": {"value": 0}})
                assert getattr(TicketTools(Client()), method)({field: reference})["ok"] is True
                assert len(calls) == (2 if reference["kind"] in ("name", "email", "phone") else 1)


def test_user_resolver_cannot_discard_malformed_candidates_to_claim_uniqueness():
    for invalid_id in (None, True, 0, "9"):
        class Client:
            def get(self, path, *, params=None):
                assert path == "/api/v2/users/search.json"
                return success({"users": [{"id": 8, "name": "Alex"}, {"id": invalid_id, "name": "Alex"}], "next_page": None})
        for method in ("search_tickets", "count_tickets", "export_tickets"):
            result = getattr(TicketTools(Client()), method)({"assignee": {"kind": "name", "value": "Alex"}})
            assert result["error"]["code"] == "upstream_error"


def test_named_ticket_user_filter_returns_candidate_ids_when_ambiguous():
    client = StubClient({"/api/v2/users/search.json": success({"users": [{"id": 8, "email": "agent@example.test"}, {"id": 9, "email": "agent@example.test"}], "next_page": None})})

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
        ("/api/v2/search.json", {"query": "type:ticket organization:12", "per_page": "100"}),
    ]


def test_organization_resolver_rejects_malformed_results_before_ticket_queries():
    for organizations in (None, {}, [None], [{"id": 12, "name": "Acme"}, {"name": "Acme"}], [{"id": True, "name": "Acme"}]):
        client = StubClient({"/api/v2/organizations/search.json": success({"organizations": organizations})})
        result = TicketTools(client).search_tickets({"organization": {"kind": "name", "value": "Acme"}})
        assert result["error"]["code"] == "upstream_error"
        assert len(client.paths) == 1


def test_structured_ticket_filter_serializes_brand_group_and_form_ids():
    client = StubClient({"/api/v2/search.json": success({"results": [], "next_page": None})})

    TicketTools(client).search_tickets({"brand": {"kind": "id", "value": 2}, "group": {"kind": "id", "value": 3}, "form": {"kind": "id", "value": 4}})

    assert client.paths == [("/api/v2/search.json", {"query": "type:ticket brand:2 group:3 form:4", "per_page": "100"})]


def test_structured_ticket_filter_resolves_brand_name():
    client = StubClient({"/api/v2/brands.json": success({"brands": [{"id": 2, "name": "Acme"}], "meta": {"has_more": False}}), "/api/v2/search.json": success({"results": [], "next_page": None})})
    TicketTools(client).search_tickets({"brand": {"kind": "name", "value": "Acme"}})
    assert client.paths == [("/api/v2/brands.json", {"page[size]": "100"}), ("/api/v2/search.json", {"query": "type:ticket brand:2", "per_page": "100"})]


def test_structured_ticket_filter_resolves_group_name():
    client = StubClient({"/api/v2/groups.json": success({"groups": [{"id": 3, "name": "Support"}], "meta": {"has_more": False}}), "/api/v2/search.json": success({"results": [], "next_page": None})})
    TicketTools(client).search_tickets({"group": {"kind": "name", "value": "Support"}})
    assert client.paths == [("/api/v2/groups.json", {"page[size]": "100"}), ("/api/v2/search.json", {"query": "type:ticket group:3", "per_page": "100"})]


def test_structured_ticket_filter_resolves_form_name():
    client = StubClient({"/api/v2/ticket_forms.json": success({"ticket_forms": [{"id": 4, "name": "Incident"}], "meta": {"has_more": False}}), "/api/v2/search.json": success({"results": [], "next_page": None})})

    TicketTools(client).search_tickets({"form": {"kind": "name", "value": "Incident"}})

    assert client.paths == [("/api/v2/ticket_forms.json", {"page[size]": "100"}), ("/api/v2/search.json", {"query": "type:ticket form:4", "per_page": "100"})]


def test_named_entity_resolvers_reject_incomplete_metadata():
    for field, path, key in (("brand", "/api/v2/brands.json", "brands"), ("group", "/api/v2/groups.json", "groups"), ("form", "/api/v2/ticket_forms.json", "ticket_forms")):
        for meta in (None, {}, {"has_more": 0}, {"has_more": "false"}):
            client = StubClient({path: success({key: [{"id": 3, "name": "Same"}], "meta": meta})})
            result = TicketTools(client).search_tickets({field: {"kind": "name", "value": "Same"}})
            assert result["error"]["code"] == "upstream_error"
            assert len(client.paths) == 1


def test_named_entity_resolvers_reject_malformed_ids_in_all_consumers():
    for field, endpoint, key in (("brand", "/api/v2/brands.json", "brands"), ("group", "/api/v2/groups.json", "groups"), ("form", "/api/v2/ticket_forms.json", "ticket_forms")):
        for method in ("search_tickets", "count_tickets", "export_tickets"):
            client = StubClient({endpoint: success({key: [{"id": 1, "name": "Same"}, {"id": None, "name": "Same"}], "meta": {"has_more": False}})})
            result = getattr(TicketTools(client), method)({field: {"kind": "name", "value": "Same"}})
            assert result["error"]["code"] == "upstream_error"
            assert len(client.paths) == 1


def test_entity_name_and_id_references_match_across_ticket_consumers():
    for field, endpoint, key in (("organization", "/api/v2/organizations/search.json", "organizations"), ("brand", "/api/v2/brands.json", "brands"), ("group", "/api/v2/groups.json", "groups"), ("form", "/api/v2/ticket_forms.json", "ticket_forms")):
        for kind, value in (("id", 12), ("name", "Acme")):
            for method in ("search_tickets", "count_tickets", "export_tickets"):
                calls = []
                class Client:
                    def get(self, path, *, params=None):
                        calls.append(path)
                        if path == endpoint:
                            return success({key: [{"id": 12, "name": "Acme"}], "meta": {"has_more": False}})
                        assert params["query"] == ("" if method == "export_tickets" else "type:ticket ") + f"{field}:12"
                        return success({"results": [], "next_page": None, "meta": {"has_more": False}, "count": {"value": 0}})
                assert getattr(TicketTools(Client()), method)({field: {"kind": kind, "value": value}})["ok"] is True
                assert len(calls) == (2 if kind == "name" else 1)


def test_custom_object_projection_requires_its_capability():
    settings = Settings.load({})
    result = TicketTools(StubClient({}), settings).search_tickets("status:open", projection={"include_custom_objects": ["asset"]})
    assert result["error"]["code"] == "unsupported"


def test_custom_object_projection_validates_its_keys():
    result = TicketTools(None).search_tickets("status:open", projection={"unknown": []})
    assert result["error"]["code"] == "validation_error"
    assert TicketTools(None).search_tickets("status:open", projection={"include_custom_objects": []})["error"]["code"] == "validation_error"
    for key in ("../asset", "asset?x", "asset#x", "asset%2fother", ".", ".."):
        assert TicketTools(None).search_tickets("status:open", projection={"include_custom_objects": [key]})["error"]["code"] == "validation_error"


def test_custom_object_projection_rejects_unsafe_record_paths():
    settings = Settings.load({"ZENDESK_CAPABILITIES": "support,custom_objects"})
    for record_id in ("../../users/me", "99?x", "99#x", "%2f", "a\\b", [], {}, True):
        client = StubClient({
            "/api/v2/search.json": success({"results": [{"id": 1, "custom_fields": [{"id": 10, "value": record_id}]}], "next_page": None}),
            "/api/v2/ticket_fields.json": success({"ticket_fields": [{"id": 10, "relationship_target_type": "zen:custom_object:asset"}], "meta": {"has_more": False}}),
        })
        result = TicketTools(client, settings).search_tickets("status:open", projection={"include_custom_objects": ["asset"]})
        assert result["error"]["code"] == "upstream_error"
        assert len(client.paths) == 2


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


def test_export_cache_reports_exact_streamed_size_and_mime(tmp_path):
    from zendesk_mcp_server.tools.tickets import _cache_ticket_export
    for output_format, mime in (("json", "application/json"), ("csv", "text/csv")):
        chunks = iter([b"abc", b"defg"])
        result = _cache_ticket_export(tmp_path, output_format, chunks)
        assert result["ok"] is True
        assert result["data"]["size"] == 7
        assert result["data"]["mime_type"] == mime


def test_export_cache_restricts_existing_cache_directories(tmp_path):
    from zendesk_mcp_server.tools.tickets import _cache_ticket_export
    root = tmp_path / "exports"
    user_root = root / str(os.getuid())
    user_root.mkdir(parents=True)
    root.chmod(0o755); user_root.chmod(0o755)
    result = _cache_ticket_export(root, "json", b"[]")
    assert result["ok"] is True
    assert root.stat().st_mode & 0o777 == 0o700
    assert user_root.stat().st_mode & 0o777 == 0o700
    assert Path(result["data"]["cache_path"]).stat().st_mode & 0o777 == 0o600


def test_export_cache_creates_missing_application_parent_on_first_use(tmp_path):
    from zendesk_mcp_server.tools.tickets import _cache_ticket_export
    root = tmp_path / "new-cache" / "zendesk-mcp-server" / "exports"
    result = _cache_ticket_export(root, "json", b"[]")
    assert result["ok"] is True
    assert Path(result["data"]["cache_path"]).read_bytes() == b"[]"
    assert root.parent.stat().st_mode & 0o777 == 0o700


def test_ticket_export_rejects_nonobject_results_before_projection(tmp_path):
    class Client:
        def get(self, path, *, params=None):
            return success({"results": [None], "meta": {"has_more": False}})
    tools = TicketTools(Client(), Settings.load({"ZENDESK_ATTACHMENT_CACHE_ROOT": str(tmp_path / "cache")}))
    for output_format in (None, "json"):
        result = tools.export_tickets("status:open", output_format=output_format)
        assert result["ok"] is False and result["error"]["code"] == "upstream_error"
    assert not list(tmp_path.rglob("*.json"))


def test_ticket_artifact_stops_on_nonprogressing_page_without_publishing(tmp_path):
    class Client:
        def __init__(self): self.calls = 0
        def get(self, path, *, params=None):
            self.calls += 1
            assert self.calls <= 2
            return success({"results": [{"id": 1}] if self.calls == 1 else [], "meta": {"has_more": True, "after_cursor": str(self.calls)}})
    tools = TicketTools(Client(), Settings.load({"ZENDESK_ATTACHMENT_CACHE_ROOT": str(tmp_path / "cache")}))
    result = tools.export_tickets("status:open", output_format="json")
    assert result["ok"] is False and result["error"]["code"] == "upstream_error"
    assert not list(tmp_path.rglob("*.json"))


def test_ticket_artifact_collects_multiple_pages(tmp_path):
    import json
    class PagesClient:
        def get(self, path, *, params=None):
            assert params["filter[type]"] == "ticket"
            assert params["query"] == "status:open"
            if params.get("page[after]") == "second":
                return success({"results": [{"id": 2}], "meta": {"has_more": False}})
            return success({"results": [{"id": 1}], "meta": {"has_more": True, "after_cursor": "second"}})
    settings = Settings.load({"ZENDESK_ATTACHMENT_CACHE_ROOT": str(tmp_path / "attachments")})
    result = TicketTools(PagesClient(), settings).export_tickets("status:open", output_format="json")
    assert result["data"]["item_count"] == 2
    assert result["data"]["next_cursor"] is None
    assert result["data"]["truncated"] is False
    assert json.loads(Path(result["data"]["cache_path"]).read_text()) == [{"id": 1}, {"id": 2}]


def test_ticket_artifact_stops_at_cap_with_resumable_cursor(tmp_path):
    class PagesClient:
        calls = 0
        def get(self, path, *, params=None):
            self.calls += 1
            return success({"results": [{"id": 1}] * 1000, "meta": {"has_more": True, "after_cursor": str(self.calls)}})
    settings = Settings.load({"ZENDESK_ATTACHMENT_CACHE_ROOT": str(tmp_path / "attachments")})
    client = PagesClient(); tools = TicketTools(client, settings)
    result = tools.export_tickets("status:open", limit=1000, output_format="json")
    assert client.calls == 100
    assert result["data"]["item_count"] == 100000
    assert result["data"]["truncated"] is True
    resumed = tools.export_tickets("status:open", limit=1000, cursor=result["data"]["next_cursor"])
    assert resumed["ok"] is True
    assert client.calls == 101


def test_csv_export_marks_formula_like_strings_as_text_without_changing_json():
    import csv
    import json
    from zendesk_mcp_server.tools.tickets import _serialize_ticket_export
    values = ['=1+1', '+1', '-1', '@SUM(1,2)', '\t=1', '\r=1', '\n=1', '  =1', '＝1', '＋1', '－1', '＠SUM(1)', 'normal']
    items = [{"=header": value, "number": -3} for value in values]
    rows = list(csv.reader(io.StringIO(_serialize_ticket_export(items, "csv").decode(), newline="")))
    assert rows[0] == ["'=header", "number"]
    assert [row[0] for row in rows[1:]] == ["'" + value for value in values[:-1]] + ["normal"]
    assert all(row[1] == "-3" for row in rows[1:])
    assert json.loads(_serialize_ticket_export(items, "json")) == items


def test_ticket_export_flattens_custom_objects_into_csv_columns(tmp_path):
    settings = Settings.load({"ZENDESK_ATTACHMENT_CACHE_ROOT": str(tmp_path / "attachments")})
    client = StubClient({"/api/v2/search/export.json": success({"results": [{"id": 1, "subject": "Login", "custom_objects": {"asset": [{"id": "9", "name": "Mac"}]}}], "meta": {"has_more": False}})})

    result = TicketTools(client, settings).export_tickets("status:open", output_format="csv")

    assert Path(result["data"]["cache_path"]).read_text() == "id,subject,asset.id,asset.name\n1,Login,9,Mac\n"


def test_custom_object_csv_flattens_nested_fields_without_losing_collisions():
    import csv
    import io
    import json
    from zendesk_mcp_server.tools.tickets import _serialize_ticket_export
    records = [{"id": "99", "name": "Record", "custom_object_fields": {"serial": "=unsafe", "name": "Custom name"}}, {"id": "100", "custom_object_fields": {"serial": "safe"}}]
    item = {"id": 1, "custom_objects": {"asset": records}}
    single = list(csv.DictReader(io.StringIO(_serialize_ticket_export([{**item, "custom_objects": {"asset": records[:1]}}], "csv").decode())))[0]
    assert single["asset.serial"] == "'=unsafe"
    assert single["asset.name"] == "Record"
    assert single["asset.custom_object_fields.name"] == "Custom name"
    row = list(csv.DictReader(io.StringIO(_serialize_ticket_export([item], "csv").decode())))[0]
    assert json.loads(row["asset.serial"]) == ["=unsafe", "safe"]
    assert json.loads(row["asset.custom_object_fields.name"]) == ["Custom name", None]
    assert json.loads(_serialize_ticket_export([item], "json")) == [item]


def test_custom_object_projection_nests_ticket_lookup_records():
    settings = Settings.load({"ZENDESK_CAPABILITIES": "support,custom_objects"})
    client = StubClient({
        "/api/v2/search.json": success({"results": [{"id": 1, "custom_fields": [{"id": 10, "value": "99"}]}], "next_page": None}),
        "/api/v2/ticket_fields.json": success({"ticket_fields": [{"id": 10, "relationship_target_type": "zen:custom_object:asset"}], "meta": {"has_more": False}}),
        "/api/v2/custom_objects/asset/records/99.json": success({"custom_object_record": {"id": "99"}}),
    })
    result = TicketTools(client, settings).search_tickets("status:open", projection={"include_custom_objects": ["asset"]})
    assert result["items"][0]["custom_objects"] == {"asset": [{"id": "99"}]}


def test_custom_object_projection_keeps_all_lookup_records_and_csv_values():
    import json
    import csv
    import io
    from zendesk_mcp_server.tools.tickets import _serialize_ticket_export
    settings = Settings.load({"ZENDESK_CAPABILITIES": "support,custom_objects"})
    ticket = {"id": 1, "custom_fields": [{"id": 10, "value": "99"}, {"id": 11, "value": "100"}, {"id": 12, "value": "99"}, {"id": 13, "value": None}]}
    client = StubClient({
        "/api/v2/search.json": success({"results": [ticket], "next_page": None}),
        "/api/v2/search/export.json": success({"results": [ticket], "meta": {"has_more": False}}),
        "/api/v2/ticket_fields.json": success({"ticket_fields": [{"id": i, "relationship_target_type": "zen:custom_object:asset"} for i in range(10, 14)], "meta": {"has_more": False}}),
        "/api/v2/custom_objects/asset/records/99.json": success({"custom_object_record": {"id": "99", "name": "First"}}),
        "/api/v2/custom_objects/asset/records/100.json": success({"custom_object_record": {"id": "100"}}),
    })
    tools = TicketTools(client, settings)
    for method in (tools.search_tickets, tools.export_tickets):
        result = method("status:open", projection={"include_custom_objects": ["asset"]})
        items = result.get("items", result.get("data", {}).get("items"))
        assert items[0]["custom_objects"]["asset"] == [{"id": "99", "name": "First"}, {"id": "100"}]
        rows = list(csv.DictReader(io.StringIO(_serialize_ticket_export(items, "csv").decode())))
        assert json.loads(rows[0]["asset.id"]) == ["99", "100"]
        assert json.loads(rows[0]["asset.name"]) == ["First", None]
    assert sum(path.endswith("/99.json") for path, _ in client.paths) == 2


def test_custom_object_malformed_ticket_fields_do_not_fetch_records():
    settings = Settings.load({"ZENDESK_CAPABILITIES": "support,custom_objects"})
    for fields in (None, {}, [None], [{"id": [], "value": "99"}], [{"id": True, "value": "99"}], [{"id": 10}], [{"id": 10, "value": "99"}, {"id": 10, "value": "100"}]):
        ticket = {"id": 1, "custom_fields": fields}
        client = StubClient({
            "/api/v2/search.json": success({"results": [ticket], "next_page": None}),
            "/api/v2/search/export.json": success({"results": [ticket], "meta": {"has_more": False}}),
            "/api/v2/ticket_fields.json": success({"ticket_fields": [{"id": 10, "relationship_target_type": "zen:custom_object:asset"}], "meta": {"has_more": False}}),
        })
        tools = TicketTools(client, settings)
        for method in (tools.search_tickets, tools.export_tickets):
            result = method("status:open", projection={"include_custom_objects": ["asset"]})
            assert result["error"]["code"] == "upstream_error"
        assert len(client.paths) == 4


def test_custom_object_lookup_fields_follow_metadata_pages():
    class Client:
        def get(self, path, *, params=None):
            if path == "/api/v2/search.json":
                return success({"results": [{"id": 1, "custom_fields": [{"id": 10, "value": "99"}]}], "next_page": None})
            if path == "/api/v2/ticket_fields.json":
                assert params["page[size]"] == "100"
                if params.get("page[after]") == "next":
                    return success({"ticket_fields": [{"id": 10, "relationship_target_type": "zen:custom_object:asset"}], "meta": {"has_more": False}})
                return success({"ticket_fields": [{"id": 1}], "meta": {"has_more": True, "after_cursor": "next"}})
            assert path == "/api/v2/custom_objects/asset/records/99.json"
            return success({"custom_object_record": {"id": "99"}})
    result = TicketTools(Client(), Settings.load({"ZENDESK_CAPABILITIES": "support,custom_objects"})).search_tickets("status:open", projection={"include_custom_objects": ["asset"]})
    assert result["items"][0]["custom_objects"]["asset"] == [{"id": "99"}]


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


def test_search_collects_total_limit_and_resumes_inside_page():
    class Client:
        def get(self, path, *, params=None):
            assert path == "/api/v2/search.json"
            assert params["query"] == "type:ticket status:open"
            size = int(params["per_page"]); page = int(params.get("page", "1"))
            start = (page - 1) * size
            return success({"results": [{"id": i} for i in range(start, min(start + size, 200))], "next_page": "https://ignored.example/next" if start + size < 200 else None})
    tools = TicketTools(Client())
    first = tools.search_tickets("status:open", limit=150)
    assert len(first["items"]) == 150 and first["next_cursor"] == "150"
    second = tools.search_tickets("status:open", limit=150, cursor=first["next_cursor"])
    assert [item["id"] for item in second["items"]] == list(range(150, 200))
    assert second["has_more"] is False


def test_search_non_divisor_limit_stays_inside_thousand_result_window():
    class Client:
        def get(self, path, *, params=None):
            size = int(params["per_page"])
            start = (int(params.get("page", "1")) - 1) * size
            assert start + size <= 1000
            return success({"results": [{"id": i} for i in range(start, start + size)], "next_page": "https://ignored.example/next"})
    tools = TicketTools(Client())
    cursor = None
    ids = []
    while True:
        result = tools.search_tickets("status:open", limit=99, cursor=cursor)
        ids.extend(item["id"] for item in result["items"])
        cursor = result["next_cursor"]
        if cursor is None:
            break
    assert ids == list(range(1000))
    assert result["has_more"] is True and result["truncated"] is True
    legacy = tools.search_tickets("status:open", limit=99, page=2)
    assert [item["id"] for item in legacy["items"]] == list(range(99, 198))
    assert legacy["next_page"] == 3


def test_search_uses_offset_page_and_enforces_thousand_result_limit():
    client = StubClient({"/api/v2/search.json": success({"results": [{"id": i} for i in range(100)], "next_page": "https://untrusted.example/next"})})
    tools = TicketTools(client)
    result = tools.search_tickets("status:open", page=2)
    assert client.paths == [("/api/v2/search.json", {"query": "type:ticket status:open", "per_page": "100", "page": "2"})]
    assert result["next_page"] == 3
    assert tools.search_tickets("status:open", page=11)["error"]["code"] == "validation_error"
    assert len(client.paths) == 1


def test_search_rejects_missing_results_and_oversized_pages():
    for data in ({}, {"results": [{"id": 1}, {"id": 2}], "next_page": None}):
        client = StubClient({"/api/v2/search.json": success(data)})
        result = TicketTools(client).search_tickets("status:open", limit=1)
        assert result["error"]["code"] == "upstream_error"


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
                    "comment": {"body": "Details", "public": False},
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

    assert client.calls == [("POST", "/api/v2/tickets.json", {"ticket": {"requester_id": 7, "subject": "Need help", "comment": {"body": "Details", "public": False}, "assignee_id": 3, "group_id": 4, "organization_id": 5, "custom_fields": [{"id": 12, "value": "gold"}]}})]


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


def test_ticket_closure_requires_preview_destructive_gate_and_payload_approval(tmp_path):
    for method in ("update_ticket", "set_ticket_status"):
        client = MutationStub()
        store = ApprovalStore(tmp_path / (method + ".json"))
        settings = Settings.load({"ZENDESK_WRITE_MODE": "standard"})
        tools = TicketTools(client, settings, store)
        preview = getattr(tools, method)(9, status="closed")
        assert client.calls == []
        request_id = preview["data"]["approval_request_id"]
        assert preview["data"]["destructive"] is True
        token = store.approve(request_id)
        options = {"execution_mode": "apply", "approval_request_id": request_id, "approval_token": token}
        assert getattr(tools, method)(9, status="closed", **options)["error"]["code"] == "write_disabled"
        assert client.calls == []
        tools = TicketTools(client, Settings.load({"ZENDESK_WRITE_MODE": "standard", "ZENDESK_ENABLE_DESTRUCTIVE_WRITES": "true"}), store)
        assert getattr(tools, method)(10, status="closed", **options)["error"]["code"] == "approval_required"
        assert client.calls == []
        assert getattr(tools, method)(9, status="closed", **options)["ok"] is True
        assert client.calls == [("PUT", "/api/v2/tickets/9.json", {"ticket": {"status": "closed"}})]
        assert getattr(tools, method)(9, status="closed", **options)["error"]["code"] == "approval_required"
        assert len(client.calls) == 1


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


def test_assignment_resolves_email_or_me_before_shared_update():
    for selector in ("me", "Agent@example.com"):
        class Client(MutationStub):
            def get(self, path, *, params=None):
                user = {"id": 7, "email": "agent@example.com", "role": "agent", "suspended": False}
                if selector == "me":
                    assert path == "/api/v2/users/me.json"
                    return success({"user": user})
                assert path == "/api/v2/users/search.json"
                assert params["query"] == "Agent@example.com"
                return success({"users": [user, {"id": 8, "email": "other@example.com", "role": "agent"}], "next_page": None})
        client = Client()
        result = TicketTools(client, Settings.load({"ZENDESK_WRITE_MODE": "standard"})).assign_ticket(9, assignee_email=selector, group_id=4)
        assert result["ok"] is True
        assert client.calls == [("PUT", "/api/v2/tickets/9.json", {"ticket": {"assignee_id": 7, "group_id": 4}})]


def test_assignment_resolution_rejects_ambiguous_ineligible_and_missing_users():
    for users in ([], [{"id": 7, "role": "end-user", "email": "a@example.com", "suspended": False}], [{"id": 7, "role": "agent", "email": "a@example.com", "suspended": True}], [{"id": i, "role": "agent", "email": "a@example.com", "suspended": False} for i in (7, 8)]):
        class Client(MutationStub):
            def get(self, path, *, params=None): return success({"users": users, "next_page": None})
        client = Client()
        result = TicketTools(client, Settings.load({"ZENDESK_WRITE_MODE": "standard"})).assign_ticket(9, assignee_email="a@example.com")
        assert result["ok"] is False
        assert client.calls == []


def test_assignment_selectors_validate_and_gate_before_lookup():
    class Client(MutationStub):
        def get(self, path, *, params=None): raise AssertionError("No lookup allowed")
    client = Client()
    tools = TicketTools(client, Settings.load({"ZENDESK_WRITE_MODE": "standard"}))
    for options in ({"assignee_email": "me", "assignee_id": 7}, {"assignee_email": "bad"}, {"assignee_email": "x@example.com role:admin"}, {"assignee_email": "me", "group_id": 0}):
        assert tools.assign_ticket(9, **options)["error"]["code"] == "validation_error"
    assert tools.assign_ticket(0, assignee_email="me")["error"]["code"] == "validation_error"
    assert TicketTools(client, Settings.load({})).assign_ticket(9, assignee_email="me")["error"]["code"] == "write_disabled"
    assert client.calls == []


def test_assignment_search_ceiling_and_late_failure_never_write():
    from zendesk_mcp_server.contracts import failure, ErrorCode
    for fail_late in (False, True):
        class Client(MutationStub):
            def get(self, path, *, params=None):
                page = int(params["page"])
                if fail_late and page == 2: return failure(ErrorCode.UPSTREAM_ERROR, "unavailable")
                assert page <= 100
                return success({"users": [{"id": 7, "email": "a@example.com", "role": "agent", "suspended": False}] if page == 1 else [], "next_page": "https://untrusted.invalid/ignored"})
        client = Client()
        result = TicketTools(client, Settings.load({"ZENDESK_WRITE_MODE": "standard"})).assign_ticket(9, assignee_email="a@example.com")
        assert result["error"]["code"] == "upstream_error"
        assert client.calls == []


def test_assignment_finds_exact_user_after_first_search_batch():
    class Client(MutationStub):
        def get(self, path, *, params=None):
            page = int(params["page"])
            assert 1 <= page <= 11
            if page == 11:
                return success({"users": [{"id": 7, "email": "a@example.com", "role": "admin", "suspended": False}], "next_page": None})
            return success({"users": [{"id": page * 100 + i, "email": "other@example.com"} for i in range(100)], "next_page": "https://untrusted.invalid/ignored"})
    client = Client()
    result = TicketTools(client, Settings.load({"ZENDESK_WRITE_MODE": "standard"})).assign_ticket(9, assignee_email="a@example.com")
    assert result["ok"] is True
    assert client.calls == [("PUT", "/api/v2/tickets/9.json", {"ticket": {"assignee_id": 7}})]


def test_ticket_tag_shortcuts_preserve_concurrent_changes():
    from zendesk_mcp_server.contracts import ErrorCode, failure
    class Client:
        def __init__(self):
            self.tags = ["billing"]
            self.calls = 0
        def get(self, path, *, params=None):
            snapshot = {"id": 9, "tags": self.tags.copy(), "updated_at": "2026-09-01T00:00:00Z"}
            self.tags.append("concurrent")
            return success({"ticket": snapshot})
        def request(self, method, path, *, json_body=None):
            self.calls += 1
            payload = json_body["ticket"]
            if payload.get("safe_update") is True and payload.get("updated_stamp") == "2026-09-01T00:00:00Z":
                return failure(ErrorCode.CONFLICT, "ticket changed")
            self.tags = payload["tags"]
            return success({"ticket": {"tags": self.tags}})
    for operation, tag in (("add_ticket_tag", "priority"), ("remove_ticket_tag", "billing")):
        client = Client()
        tools = TicketTools(client, Settings.load({"ZENDESK_WRITE_MODE": "standard"}))
        result = getattr(tools, operation)(9, tag)
        assert client.tags == ["billing", "concurrent"]
        assert result["error"]["code"] == "conflict"
        assert client.calls == 1


def test_ticket_tag_changes_reject_invalid_timestamps_without_writing():
    class Client(MutationStub):
        def get(self, path, *, params=None):
            return success({"ticket": {"tags": ["billing"], "updated_at": stamp}})
    for stamp in (None, "invalid", "2026-09-01", 123, {}):
        for operation, tag in (("add_ticket_tag", "priority"), ("remove_ticket_tag", "billing")):
            client = Client()
            tools = TicketTools(client, Settings.load({"ZENDESK_WRITE_MODE": "standard"}))
            result = getattr(tools, operation)(9, tag)
            assert result["error"]["code"] == "upstream_error"
            assert client.calls == []


def test_ticket_tag_noops_and_read_only_never_write():
    client = MutationStub()
    tools = TicketTools(client, Settings.load({"ZENDESK_WRITE_MODE": "standard"}))
    assert tools.add_ticket_tag(9, "billing")["data"]["idempotent"] is True
    assert tools.remove_ticket_tag(9, "absent")["data"]["idempotent"] is True
    assert client.calls == []
    client.get_paths.clear()
    tools = TicketTools(client, Settings.load({}))
    assert tools.add_ticket_tag(9, "priority")["error"]["code"] == "write_disabled"
    assert tools.remove_ticket_tag(9, "billing")["error"]["code"] == "write_disabled"
    assert client.calls == [] and client.get_paths == []


def test_ticket_tag_shortcuts_bind_changes_to_read_timestamp():
    client = MutationStub()
    tools = TicketTools(client, Settings.load({"ZENDESK_WRITE_MODE": "standard"}))

    tools.add_ticket_tag(9, "priority")
    tools.remove_ticket_tag(9, "billing")

    assert client.get_paths == [
        ("/api/v2/tickets/9.json", None),
        ("/api/v2/tickets/9.json", None),
    ]
    assert client.calls == [
        ("PUT", "/api/v2/tickets/9.json", {"ticket": {"tags": ["billing", "priority"], "safe_update": True, "updated_stamp": "2026-09-01T00:00:00Z"}}),
        ("PUT", "/api/v2/tickets/9.json", {"ticket": {"tags": [], "safe_update": True, "updated_stamp": "2026-09-01T00:00:00Z"}}),
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


@pytest.mark.parametrize("code,state", [("timeout", "unknown"), ("partial_success", "partial")])
def test_macro_unknown_write_outcome_includes_recovery_without_replay(tmp_path, code, state):
    from zendesk_mcp_server.contracts import failure, ErrorCode

    class TimedOutMacro(MacroStub):
        def request(self, method, path, *, json_body=None):
            self.calls.append((method, path, json_body))
            return failure(ErrorCode(code), "Macro write did not fully complete", operation_state=state)

    client = TimedOutMacro()
    store = ApprovalStore(tmp_path / "approvals.json")
    tools = TicketTools(client, Settings.load({"ZENDESK_WRITE_MODE": "standard", "ZENDESK_ENABLE_PUBLIC_WRITES": "true"}), store)
    preview = tools.apply_macro(9, 4)
    request_id = preview["data"]["approval_request_id"]
    result = tools.apply_macro(9, 4, execution_mode="apply", approval_request_id=request_id, approval_token=store.approve(request_id))
    assert result["ok"] is False and result["error"]["code"] == code
    assert result["error"]["operation_state"] == state
    assert result["error"]["retryable"] is False
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


def test_macro_risks_include_effective_preview_impersonation_and_closure():
    from zendesk_mcp_server.tools.tickets import _macro_risks
    from zendesk_mcp_server.write_policy import WriteRisk
    for field, value in (("author_id", 7), ("created_at", "2026-09-12T00:00:00Z")):
        assert WriteRisk.IMPERSONATION in _macro_risks({"comment": {"public": False, field: value}}, [])
    assert WriteRisk.DESTRUCTIVE in _macro_risks({"status": "closed"}, [])


def test_macro_effective_preview_requires_gate_even_with_approval(tmp_path):
    for changes, risk in (({"comment": {"public": False, "author_id": 7, "body": "note"}}, "impersonation"), ({"status": "closed"}, "destructive"), ({"collaborator_ids": [7]}, "public")):
        class Client(MacroStub):
            def get(self, path, *, params=None):
                if path.endswith("/apply.json"): return success({"result": {"ticket": changes}})
                if path == "/api/v2/macros/4.json": return success({"macro": {"actions": []}})
                return super().get(path, params=params)
        client = Client()
        store = ApprovalStore(tmp_path / (risk + ".json"))
        tools = TicketTools(client, Settings.load({"ZENDESK_WRITE_MODE": "standard"}), store)
        preview = tools.apply_macro(9, 4)["data"]
        assert preview["required_risks"] == ["standard", risk]
        request_id = preview["approval_request_id"]
        result = tools.apply_macro(9, 4, execution_mode="apply", approval_request_id=request_id, approval_token=store.approve(request_id))
        assert result["error"]["code"] == "write_disabled"
        assert client.calls == []


def test_macro_recipient_changes_require_public_gate():
    from zendesk_mcp_server.tools.tickets import _macro_risks
    from zendesk_mcp_server.write_policy import WriteRisk
    assert WriteRisk.PUBLIC in _macro_risks({}, [{"field": "cc", "value": "current_user"}])
    for field in ("share_ticket", "follower"):
        assert WriteRisk.PUBLIC in _macro_risks({}, [{"field": field, "value": "7"}])
    for field in ("collaborator_ids", "email_cc_ids", "follower_ids", "recipient", "sharing_agreement_ids"):
        assert WriteRisk.PUBLIC in _macro_risks({field: []}, [])


def test_macro_preview_preserves_fields_alias(tmp_path):
    for fields in ({"id": 42, "value": "new"}, [{"id": 42, "value": "new"}]):
        class Client(MacroStub):
            def get(self, path, *, params=None):
                if path.endswith("/apply.json"): return success({"result": {"ticket": {"fields": fields}}})
                return super().get(path, params=params)
        client = Client()
        store = ApprovalStore(tmp_path / "approvals.json")
        tools = TicketTools(client, Settings.load({"ZENDESK_WRITE_MODE": "standard", "ZENDESK_ENABLE_PUBLIC_WRITES": "true"}), store)
        preview = tools.apply_macro(9, 4)["data"]
        assert preview["ticket"]["custom_fields"] == [{"id": 42, "value": "new"}]
        assert "fields" not in preview["ticket"]
        assert client.calls == []
        request_id = preview["approval_request_id"]
        applied = tools.apply_macro(9, 4, execution_mode="apply", approval_request_id=request_id, approval_token=store.approve(request_id))
        assert applied["ok"] is True
        assert client.calls == [("PUT", "/api/v2/tickets/9.json", {"ticket": {"custom_fields": [{"id": 42, "value": "new"}], "safe_update": True, "updated_stamp": "2026-09-11T00:00:00Z"}})]


def test_macro_invalid_fields_alias_never_creates_approval_or_writes(tmp_path):
    for fields in (None, "invalid", [{"id": True, "value": "bad"}], [{"id": 42}], [None]):
        class Client(MacroStub):
            def get(self, path, *, params=None):
                if path.endswith("/apply.json"): return success({"result": {"ticket": {"fields": fields}}})
                return super().get(path, params=params)
        client = Client()
        path = tmp_path / "approvals.json"
        result = TicketTools(client, approvals=ApprovalStore(path)).apply_macro(9, 4)
        assert result["error"]["code"] == "upstream_error"
        assert client.calls == []
        assert not path.exists()


def test_macro_rejects_invalid_canonical_custom_fields(tmp_path):
    for fields in (None, "bad", [{"id": True, "value": "bad"}], [{"id": 42}], [None]):
        class Client(MacroStub):
            def get(self, path, *, params=None):
                if path.endswith("/apply.json"): return success({"result": {"ticket": {"custom_fields": fields}}})
                return super().get(path, params=params)
        client = Client()
        path = tmp_path / "approvals.json"
        result = TicketTools(client, approvals=ApprovalStore(path)).apply_macro(9, 4)
        assert result["ok"] is False
        assert result["error"]["code"] == "upstream_error"
        assert client.calls == [] and not path.exists()


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

    if sys.platform.startswith("linux") or result["ok"]:
        assert result["ok"] is True
        assert result["data"]["kind"] == "pdf"
        assert result["data"]["page_count"] == 1
        assert result["data"]["text"] == ""
        assert result["data"]["truncated"] is False
    else:
        assert result["error"]["code"] == "unsupported"
        assert result["error"]["message"] == "PDF inspection requires a bounded parser subprocess"


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
def test_attachment_download_creates_missing_application_parent(tmp_path):
    root = tmp_path / "new-cache" / "zendesk-mcp-server" / "attachments"
    settings = Settings.load({"ZENDESK_ATTACHMENT_CACHE_ROOT": str(root)})
    result = TicketTools(AttachmentDownloadStub(b"hello world", "text/plain"), settings).download_attachment(7, 5)
    assert result["ok"] is True
    assert Path(result["data"]["cache_path"]).read_bytes() == b"hello world"
    assert root.parent.stat().st_mode & 0o777 == 0o700


def test_attachment_cache_rejects_symlink_inserted_at_publish(tmp_path, monkeypatch):
    import zendesk_mcp_server.tools.tickets as module
    target = tmp_path / "other"
    target.write_bytes(b"preserve")
    def raced_link(source, destination):
        destination.symlink_to(target)
        raise FileExistsError
    monkeypatch.setattr(module.os, "link", raced_link)
    result = module._cache_attachment(tmp_path / "cache", 5, iter([b"download"]))
    assert result["ok"] is False
    assert result["error"]["code"] == "validation_error"
    assert target.read_bytes() == b"preserve"
    assert not list((tmp_path / "cache").rglob("*.tmp"))


def test_attachment_cache_accepts_chunks_and_discards_failed_stream(tmp_path):
    from zendesk_mcp_server.tools.tickets import _cache_attachment
    result = _cache_attachment(tmp_path / "cache", 1, iter([b"one", b"two"]))
    assert result["ok"] is True
    from pathlib import Path
    assert Path(result["data"]["cache_path"]).read_bytes() == b"onetwo"
    def broken():
        yield b"partial"
        raise OSError("stream failed")
    failed = _cache_attachment(tmp_path / "cache", 2, broken())
    assert failed["ok"] is False
    assert not list((tmp_path / "cache").glob("*/2/attachment"))
    assert not list((tmp_path / "cache").rglob("*.tmp"))
