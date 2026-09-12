import httpx
import pytest

from zendesk_mcp_server.auth import ApiTokenAuthorization
from zendesk_mcp_server.client import ZendeskClient
from zendesk_mcp_server.config import ConfigurationError, Settings


@pytest.fixture
def settings():
    return Settings.load(
        {
            "ZENDESK_SUBDOMAIN": "acme",
            "ZENDESK_EMAIL": "agent@example.test",
            "ZENDESK_API_TOKEN": "token",
        }
    )


@pytest.fixture
def authorization():
    return ApiTokenAuthorization(email="agent@example.test", token="token")


def test_rate_limit_is_shared_between_clients_and_expires(settings, authorization, monkeypatch):
    import zendesk_mcp_server.client as module
    clock = [100.0]
    monkeypatch.setattr(module.time, "monotonic", lambda: clock[0])
    calls = []
    def handler(request):
        calls.append(request.url.host)
        if len(calls) == 1: return httpx.Response(429, headers={"Retry-After": "60"})
        return httpx.Response(200, json={})
    first = ZendeskClient(settings, authorization, transport=httpx.MockTransport(handler))
    second = ZendeskClient(settings, authorization, transport=httpx.MockTransport(handler))
    assert first.request("POST", "/api/v2/tickets.json")["error"]["code"] == "rate_limited"
    result = second.get("/api/v2/users/me.json")
    assert result["error"]["code"] == "rate_limited"
    assert result["error"]["operation_state"] == "not_applied"
    assert len(calls) == 1
    other = Settings.load({"ZENDESK_SUBDOMAIN": "other", "ZENDESK_EMAIL": "a@example.test", "ZENDESK_API_TOKEN": "token"})
    assert ZendeskClient(other, authorization, transport=httpx.MockTransport(handler)).get("/api/v2/users/me.json")["ok"]
    clock[0] = 160.0
    assert second.get("/api/v2/users/me.json")["ok"]
    assert len(calls) == 3


@pytest.mark.parametrize("retry_after,expected_calls", [(20, 2), (60, 1)])
def test_image_download_respects_total_sleep_budget(settings, authorization, retry_after, expected_calls):
    calls = []; sleeps = []
    def handler(request):
        calls.append(request)
        return httpx.Response(429, headers={"Retry-After": str(retry_after)})
    client = ZendeskClient(settings, authorization, transport=httpx.MockTransport(handler), sleep=sleeps.append)
    try:
        result = client.download_help_center_image("https://acme.zendesk.com/hc/user_images/file.png", max_bytes=10)
        assert result["error"]["code"] == "rate_limited"
        assert len(calls) == expected_calls
        assert sum(sleeps) <= 30
    finally:
        client.close()


@pytest.mark.parametrize("problem", [429, 503, "timeout"])
@pytest.mark.parametrize("image", [False, True])
def test_download_retries_transient_failures(settings, authorization, problem, image):
    calls = []; sleeps = []
    def handler(request):
        calls.append(request)
        if len(calls) < 3:
            if problem == "timeout": raise httpx.ReadTimeout("temporary")
            return httpx.Response(problem, headers={"Retry-After": "0"})
        return httpx.Response(200, content=b"image", headers={"Content-Type": "image/png"})
    client = ZendeskClient(settings, authorization, transport=httpx.MockTransport(handler), sleep=sleeps.append)
    try:
        result = client.download_help_center_image("https://acme.zendesk.com/hc/user_images/file.png", max_bytes=10) if image else client.download_attachment("https://acme.zendesk.com/attachments/file", max_bytes=10)
        assert result["ok"] is True
        assert result["data"]["content"] == b"image"
        assert len(calls) == 3
        assert len(sleeps) == 2 and sum(sleeps) <= 30
    finally:
        client.close()


@pytest.mark.parametrize("status,state", [(401, "not_applied"), (403, "not_applied"), (429, "not_applied"), (500, "unknown")])
def test_presigned_upload_rejection_state_without_replay(settings, authorization, monkeypatch, status, state):
    import zendesk_mcp_server.client as module
    monkeypatch.setattr(module, "_is_public_https_url", lambda _: True)
    calls = []
    def handler(request):
        calls.append(request)
        assert "authorization" not in request.headers
        return httpx.Response(status, headers={"Retry-After": "1"})
    client = ZendeskClient(settings, authorization, transport=httpx.MockTransport(handler))
    try:
        result = client.upload_presigned("https://uploads.example.test/image", {}, b"image")
        assert result["ok"] is False
        assert result["error"]["operation_state"] == state
        assert len(calls) == 1
    finally:
        client.close()


@pytest.mark.parametrize("image", [False, True])
def test_download_rate_limit_blocks_following_api_call(settings, authorization, image):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(429, headers={"Retry-After": "60"}) if len(calls) == 1 else httpx.Response(200, json={})
    client = ZendeskClient(settings, authorization, transport=httpx.MockTransport(handler))
    try:
        result = client.download_help_center_image("https://acme.zendesk.com/hc/user_images/file.png", max_bytes=10) if image else client.download_attachment("https://acme.zendesk.com/attachments/file", max_bytes=10)
        assert result["error"]["code"] == "rate_limited"
        assert client.get("/api/v2/users/me.json")["error"]["code"] == "rate_limited"
        assert len(calls) == 1
    finally:
        client.close()


@pytest.mark.parametrize("image", [False, True])
def test_download_discards_partial_body_and_closes_before_retry(settings, authorization, image):
    closed = []; calls = []
    class BrokenBody(httpx.SyncByteStream):
        def __iter__(self):
            yield b"partial"
            raise httpx.ReadTimeout("interrupted")
        def close(self):
            closed.append(True)
    def handler(request):
        calls.append(request)
        return httpx.Response(200, stream=BrokenBody()) if len(calls) == 1 else httpx.Response(200, content=b"complete")
    def sleep(delay):
        assert closed == [True]
    client = ZendeskClient(settings, authorization, transport=httpx.MockTransport(handler), sleep=sleep)
    try:
        result = client.download_help_center_image("https://acme.zendesk.com/hc/user_images/file.png", max_bytes=10) if image else client.download_attachment("https://acme.zendesk.com/attachments/file", max_bytes=10)
        assert result["data"]["content"] == b"complete"
        assert result["data"]["size"] == 8
        assert len(calls) == 2
        assert closed == [True]
    finally:
        client.close()


def test_attachment_cdn_retry_never_restores_authorization(settings, authorization, monkeypatch):
    import zendesk_mcp_server.client as module
    monkeypatch.setattr(module, "_is_public_https_url", lambda _: True)
    calls = []; sleeps = []
    def handler(request):
        calls.append(request.url.host)
        if request.url.host == "acme.zendesk.com":
            assert "authorization" in request.headers
            return httpx.Response(302, headers={"Location": "https://cdn.example.test/file"})
        assert "authorization" not in request.headers
        if len(calls) == 2: return httpx.Response(503)
        return httpx.Response(200, content=b"file")
    client = ZendeskClient(settings, authorization, transport=httpx.MockTransport(handler), sleep=sleeps.append)
    try:
        assert client.download_attachment("https://acme.zendesk.com/attachments/file", max_bytes=10)["data"]["content"] == b"file"
        assert calls == ["acme.zendesk.com", "cdn.example.test", "cdn.example.test"]
        assert len(sleeps) == 1
    finally:
        client.close()


@pytest.mark.parametrize("oversized", [False, True])
def test_streamed_cache_is_atomic_on_download_failure(settings, authorization, tmp_path, oversized):
    from pathlib import Path
    from zendesk_mcp_server.tools.tickets import _cache_attachment
    calls = []; closed = []
    root = tmp_path / "cache"
    class Body(httpx.SyncByteStream):
        def __iter__(self):
            yield b"x" * 65536
            if oversized: yield b"x" * 65536
            else: raise httpx.ReadTimeout("interrupted")
        def close(self): closed.append(True)
    def handler(request):
        calls.append(request)
        return httpx.Response(200, stream=Body()) if len(calls) == 1 else httpx.Response(200, content=b"complete")
    def sleep(delay):
        assert closed == [True]
        assert not list(root.rglob("*.tmp"))
        assert not list(root.rglob("attachment"))
    client = ZendeskClient(settings, authorization, transport=httpx.MockTransport(handler), sleep=sleep)
    try:
        r = client.download_attachment("https://acme.zendesk.com/attachments/file", max_bytes=70000, store=lambda chunks: _cache_attachment(root, 5, chunks))
        if oversized:
            assert r["error"]["code"] == "validation_error"
            assert len(calls) == 1
            assert not list(root.rglob("attachment"))
        else:
            assert r["ok"]
            assert Path(r["data"]["cache_path"]).read_bytes() == b"complete"
            assert len(calls) == 2
        assert not list(root.rglob("*.tmp"))
    finally:
        client.close()


def test_attachment_download_streams_into_cache(settings, authorization, tmp_path):
    from zendesk_mcp_server.tools.tickets import _cache_attachment
    from pathlib import Path
    class Body(httpx.SyncByteStream):
        def __iter__(self):
            for _ in range(4): yield b"x" * 65536
    client = ZendeskClient(settings, authorization, transport=httpx.MockTransport(lambda request: httpx.Response(200, stream=Body())))
    def store(chunks):
        assert not isinstance(chunks, bytes)
        return _cache_attachment(tmp_path / "cache", 5, chunks)
    try:
        result = client.download_attachment("https://acme.zendesk.com/attachments/file", max_bytes=300000, store=store)
        assert result["ok"]
        assert "content" not in result["data"]
        assert Path(result["data"]["cache_path"]).stat().st_size == 262144
        assert result["data"]["size"] == 262144
    finally:
        client.close()


def test_tenant_downloads_observe_shared_cooldown(settings, authorization):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(429, headers={"Retry-After": "60"})
    client = ZendeskClient(settings, authorization, transport=httpx.MockTransport(handler))
    try:
        client.request("POST", "/api/v2/tickets.json")
        for result in (
            client.download_attachment("https://acme.zendesk.com/attachments/file", max_bytes=10),
            client.download_help_center_image("https://acme.zendesk.com/hc/user_images/file.png", max_bytes=10),
        ):
            assert result["error"]["code"] == "rate_limited"
            assert result["error"]["operation_state"] == "not_applied"
        assert len(calls) == 1
    finally:
        client.close()


def test_concurrent_rate_limits_preserve_longest_deadline(settings, authorization, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier, Event
    import zendesk_mcp_server.client as module
    clock = [100.0]
    monkeypatch.setattr(module.time, "monotonic", lambda: clock[0])
    entered = Barrier(2)
    long_recorded = Event()
    def handler(request):
        entered.wait(timeout=5)
        if request.url.path.endswith("short"):
            assert long_recorded.wait(timeout=5)
            return httpx.Response(429, headers={"Retry-After": "10"})
        return httpx.Response(429, headers={"Retry-After": "60"})
    def send(kind):
        client = ZendeskClient(settings, authorization, transport=httpx.MockTransport(handler))
        try:
            result = client.request("POST", f"/api/v2/{kind}")
            assert result["error"]["code"] == "rate_limited"
        finally:
            if kind == "long": long_recorded.set()
            client.close()
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(send, kind) for kind in ("short", "long")]
        for future in futures: future.result(timeout=10)
    calls = []
    def success_handler(request):
        calls.append(request)
        return httpx.Response(200, json={})
    client = ZendeskClient(settings, authorization, transport=httpx.MockTransport(success_handler))
    try:
        clock[0] = 120.0
        assert client.get("/api/v2/users/me.json")["error"]["code"] == "rate_limited"
        assert calls == []
        clock[0] = 160.0
        assert client.get("/api/v2/users/me.json")["ok"]
        assert len(calls) == 1
    finally:
        client.close()


def test_read_retries_a_rate_limit_at_most_twice(settings, authorization):
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        assert str(request.url) == "https://acme.zendesk.com/api/v2/users/me.json"
        if calls < 3:
            return httpx.Response(429, headers={"Retry-After": "0"}, request=request)
        return httpx.Response(200, json={"users": []}, request=request)

    client = ZendeskClient(
        settings,
        authorization,
        transport=httpx.MockTransport(handler),
        sleep=lambda _: None,
    )

    assert client.get("/api/v2/users/me.json") == {
        "ok": True,
        "data": {"users": []},
    }
    assert calls == 3


def test_read_rate_limit_retries_never_sleep_more_than_thirty_seconds_total(settings, authorization):
    sleeps = []

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"Retry-After": "20"}, request=request)

    client = ZendeskClient(settings, authorization, transport=httpx.MockTransport(handler), sleep=sleeps.append)
    result = client.get("/api/v2/users/me.json")

    assert result["error"]["code"] == "rate_limited"
    assert sum(sleeps) <= 30


@pytest.mark.parametrize("exception", [httpx.ReadTimeout, httpx.ConnectError])
def test_mixed_rate_limit_and_network_failure_share_sleep_budget(settings, authorization, monkeypatch, exception):
    sleeps = []
    calls = []
    monkeypatch.setattr("zendesk_mcp_server.client.random.uniform", lambda *_: 0.1)

    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(429, headers={"Retry-After": "29"}, request=request)
        raise exception("network failure", request=request)

    client = ZendeskClient(settings, authorization, transport=httpx.MockTransport(handler), sleep=sleeps.append)
    result = client.get("/api/v2/users/me.json")
    assert result["ok"] is False
    assert len(calls) == 2
    assert sleeps == [29.1]
    assert sum(sleeps) <= 30


@pytest.mark.parametrize("body,required", [
    ({"error": "Forbidden", "description": "You are missing the following required scopes: read"}, True),
    ({"description": "private upstream content"}, False),
    (["private upstream content"], False),
])
def test_scope_denial_reports_broad_read_requirement_without_leaking_body(settings, authorization, body, required):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(403, json=body, request=request)
    client = ZendeskClient(settings, authorization, transport=httpx.MockTransport(handler))
    result = client.get("/api/v2/search.json")
    assert result["error"]["code"] == "permission_denied"
    assert result["error"].get("details", {}).get("required_scopes") == (["read"] if required else None)
    assert result["error"]["retryable"] is False
    assert "private upstream content" not in str(result)
    assert len(calls) == 1


def test_write_does_not_retry_timeout(settings, authorization):
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ReadTimeout("timed out", request=request)

    client = ZendeskClient(
        settings,
        authorization,
        transport=httpx.MockTransport(handler),
    )

    result = client.request("POST", "/api/v2/tickets.json", json_body={})

    assert result["error"]["code"] == "timeout"
    assert calls == 1


@pytest.mark.parametrize(
    ("status_code", "error_code"),
    [
        (400, "validation_error"),
        (401, "authentication_failed"),
        (403, "permission_denied"),
        (404, "not_found"),
        (409, "conflict"),
        (412, "conflict"),
        (422, "validation_error"),
        (429, "rate_limited"),
        (500, "upstream_error"),
        (503, "upstream_error"),
    ],
)
def test_client_maps_known_http_errors(settings, authorization, status_code, error_code):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, json={"error": "bad request"}, request=request)

    client = ZendeskClient(settings, authorization, transport=httpx.MockTransport(handler), sleep=lambda _: None)

    assert client.get("/api/v2/users/me.json")["error"]["code"] == error_code


@pytest.mark.parametrize("status,state", [(401, "not_applied"), (403, "not_applied"), (409, "not_applied"), (412, "not_applied"), (429, "not_applied"), (500, "unknown")])
def test_rejected_writes_report_known_state_without_retry(settings, authorization, status, state):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(status, request=request)
    client = ZendeskClient(settings, authorization, transport=httpx.MockTransport(handler))
    result = client.request("PUT", "/api/v2/tickets/9.json", json_body={"ticket": {"status": "pending"}})
    assert result["error"]["operation_state"] == state
    assert len(calls) == 1


@pytest.mark.parametrize("method,state", [("GET", "not_applied"), ("PUT", "unknown")])
def test_invalid_success_json_is_not_reported_as_success(settings, authorization, method, state):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(200, content=b"<html>private upstream content</html>", request=request)
    client = ZendeskClient(settings, authorization, transport=httpx.MockTransport(handler))
    result = client.request(method, "/api/v2/tickets/9.json")
    assert result["ok"] is False
    assert result["error"]["code"] == "upstream_error"
    assert result["error"]["operation_state"] == state
    assert "private upstream content" not in str(result)
    assert len(calls) == 1


def test_no_content_delete_remains_successful(settings, authorization):
    client = ZendeskClient(settings, authorization, transport=httpx.MockTransport(lambda request: httpx.Response(204, request=request)))
    assert client.request("DELETE", "/api/v2/tickets/9.json") == {"ok": True, "data": {}}


def test_client_rejects_an_absolute_or_foreign_path(settings, authorization):
    client = ZendeskClient(settings, authorization, transport=httpx.MockTransport(lambda request: None))

    result = client.get("https://other.example/api/v2/users/me.json")

    assert result["error"]["code"] == "validation_error"


def test_client_can_scope_a_read_to_a_validated_brand_subdomain(settings, authorization):
    methods = []

    def handler(request: httpx.Request) -> httpx.Response:
        methods.append(request.method)
        assert str(request.url) == "https://brand-one.zendesk.com/api/v2/help_center/locales.json"
        return httpx.Response(200, json={"locales": ["en-us"]}, request=request)

    client = ZendeskClient(settings, authorization, transport=httpx.MockTransport(handler))

    assert client.get_for_subdomain("brand-one", "/api/v2/help_center/locales.json")["ok"] is True
    assert client.request_for_subdomain("brand-one", "POST", "/api/v2/help_center/locales.json", json_body={})["ok"] is True
    assert client.get_for_subdomain("not/a-host", "/api/v2/help_center/locales.json")["error"]["code"] == "validation_error"
    assert methods == ["GET", "POST"]


class RefreshingAuthorization:
    def __init__(self): self.token, self.refreshes = "old", 0
    def headers(self): return {"Authorization": f"Bearer {self.token}"}
    def refresh(self): self.token = "new"; self.refreshes += 1


@pytest.mark.parametrize("always_unauthorized", [False, True])
def test_download_401_refreshes_at_most_once(settings, always_unauthorized):
    authorization = RefreshingAuthorization()
    calls = []
    def handler(request):
        calls.append(request.headers["Authorization"])
        return httpx.Response(401) if always_unauthorized or len(calls) == 1 else httpx.Response(200, content=b"file")
    client = ZendeskClient(settings, authorization, transport=httpx.MockTransport(handler))
    try:
        r = client.download_attachment("https://acme.zendesk.com/attachments/file", max_bytes=10)
        assert r["ok"] is (not always_unauthorized)
        assert authorization.refreshes == 1
        assert calls == ["Bearer old", "Bearer new"]
    finally:
        client.close()


def test_cdn_401_does_not_refresh_zendesk_token(settings, monkeypatch):
    import zendesk_mcp_server.client as module
    monkeypatch.setattr(module, "_is_public_https_url", lambda _: True)
    authorization = RefreshingAuthorization()
    calls = []
    def handler(request):
        calls.append(request.url.host)
        if len(calls) == 1:
            return httpx.Response(302, headers={"Location": "https://cdn.example.test/file"})
        assert "authorization" not in request.headers
        return httpx.Response(401)
    client = ZendeskClient(settings, authorization, transport=httpx.MockTransport(handler))
    try:
        assert client.download_attachment("https://acme.zendesk.com/attachments/file", max_bytes=10)["error"]["code"] == "authentication_failed"
        assert authorization.refreshes == 0
        assert len(calls) == 2
    finally:
        client.close()


def test_download_refresh_failure_is_redacted_and_not_replayed(settings):
    class Authorization(RefreshingAuthorization):
        def refresh(self):
            self.refreshes += 1
            raise ConfigurationError("oauth_refresh_failed", "private-token-value")
    authorization = Authorization(); calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(401)
    client = ZendeskClient(settings, authorization, transport=httpx.MockTransport(handler))
    try:
        result = client.download_help_center_image("https://acme.zendesk.com/hc/user_images/file.png", max_bytes=10)
        assert result["error"]["code"] == "reauthorization_required"
        assert "private-token-value" not in str(result)
        assert authorization.refreshes == 1
        assert len(calls) == 1
    finally:
        client.close()


def test_read_401_refreshes_and_replays_once(settings):
    authorization = RefreshingAuthorization(); calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(401 if request.headers["Authorization"] == "Bearer old" else 200, json={"users": []}, request=request)

    client = ZendeskClient(settings, authorization, transport=httpx.MockTransport(handler))

    assert client.get("/api/v2/users/me.json")["ok"] is True
    assert authorization.refreshes == 1
    assert calls == 2


def test_write_401_refreshes_without_replaying(settings):
    authorization = RefreshingAuthorization(); calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(401, request=request)

    client = ZendeskClient(settings, authorization, transport=httpx.MockTransport(handler))
    result = client.request("POST", "/api/v2/tickets.json", json_body={})

    assert result["error"]["code"] == "authentication_retry_required"
    assert result["error"]["operation_state"] == "not_applied"
    assert authorization.refreshes == 1
    assert calls == 1


def test_oauth_refresh_failure_requires_reauthorization(settings):
    class FailingRefreshAuthorization:
        def headers(self): return {"Authorization": "Bearer expired"}
        def refresh(self): raise ConfigurationError("oauth_refresh_failed", "OAuth token refresh failed")

    client = ZendeskClient(
        settings,
        FailingRefreshAuthorization(),
        transport=httpx.MockTransport(lambda request: httpx.Response(401, request=request)),
    )

    assert client.get("/api/v2/users/me.json")["error"]["code"] == "reauthorization_required"


def test_presigned_upload_never_sends_zendesk_authorization(settings, authorization, monkeypatch):
    monkeypatch.setattr("zendesk_mcp_server.client.socket.getaddrinfo", lambda *args, **kwargs: [(0, 0, 0, "", ("8.8.8.8", 443))])

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers.get("Authorization") is None
        assert request.headers["Content-Type"] == "image/png"
        assert request.content == b"image"
        return httpx.Response(200, request=request)

    client = ZendeskClient(settings, authorization, transport=httpx.MockTransport(handler))

    assert client.upload_presigned("https://cdn.example.test/upload", {"Content-Type": "image/png"}, b"image")["ok"] is True


@pytest.mark.parametrize("headers", [{}, {"Content-Length": "131072"}])
def test_presigned_upload_streams_chunks_without_auth_or_retry(settings, authorization, monkeypatch, headers):
    monkeypatch.setattr("zendesk_mcp_server.client.socket.getaddrinfo", lambda *args, **kwargs: [(0, 0, 0, "", ("8.8.8.8", 443))])
    events = []
    def chunks():
        events.append("first")
        yield b"a" * 65536
        events.append("second")
        yield b"b" * 65536
    class Transport(httpx.BaseTransport):
        def handle_request(self, request):
            assert events == []
            assert "authorization" not in request.headers
            if headers:
                assert request.headers["content-length"] == "131072"
                assert "transfer-encoding" not in request.headers
            for index, chunk in enumerate(request.stream):
                assert chunk == (b"a" if index == 0 else b"b") * 65536
                assert len(events) == index + 1
            return httpx.Response(503, request=request)
    client = ZendeskClient(settings, authorization, transport=Transport())
    result = client.upload_presigned("https://cdn.example.test/upload", headers, chunks())
    assert result["error"]["code"] == "upstream_error"
    assert result["error"]["operation_state"] == "unknown"
    assert events == ["first", "second"]


def test_presigned_upload_read_failure_is_unknown_and_not_retried(settings, authorization, monkeypatch):
    monkeypatch.setattr("zendesk_mcp_server.client.socket.getaddrinfo", lambda *args, **kwargs: [(0, 0, 0, "", ("8.8.8.8", 443))])
    calls = []
    def chunks():
        yield b"partial"
        raise OSError("private file path")
    class Transport(httpx.BaseTransport):
        def handle_request(self, request):
            calls.append(request.method)
            for chunk in request.stream: pass
            raise AssertionError("incomplete upload")
    client = ZendeskClient(settings, authorization, transport=Transport())
    result = client.upload_presigned("https://cdn.example.test/upload", {}, chunks())
    assert result["error"]["code"] == "upstream_error"
    assert result["error"]["operation_state"] == "unknown"
    assert "private file path" not in str(result)
    assert calls == ["PUT"]


def test_presigned_upload_rejects_private_hosts(settings, authorization):
    client = ZendeskClient(settings, authorization, transport=httpx.MockTransport(lambda request: None))

    assert client.upload_presigned("https://127.0.0.1/upload", {"Content-Type": "image/png"}, b"image")["error"]["code"] == "validation_error"


def test_attachment_download_removes_authorization_before_cdn_redirect(settings, authorization, monkeypatch):
    monkeypatch.setattr("zendesk_mcp_server.client.socket.getaddrinfo", lambda *args, **kwargs: [(0, 0, 0, "", ("8.8.8.8", 443))])
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.host == "acme.zendesk.com":
            assert request.headers["Authorization"].startswith("Basic ")
            return httpx.Response(302, headers={"Location": "https://cdn.example.test/file"}, request=request)
        assert request.headers.get("Authorization") is None
        return httpx.Response(200, content=b"attachment", headers={"Content-Type": "text/plain"}, request=request)

    client = ZendeskClient(settings, authorization, transport=httpx.MockTransport(handler))
    result = client.download_attachment("https://acme.zendesk.com/attachments/token/file", max_bytes=20)

    assert result["data"]["content"] == b"attachment"
    assert result["data"]["content_type"] == "text/plain"
    assert len(calls) == 2


def test_attachment_download_rejects_non_https_tenant_url(settings, authorization):
    client = ZendeskClient(settings, authorization, transport=httpx.MockTransport(lambda request: None))

    assert client.download_attachment("http://acme.zendesk.com/attachments/token/file", max_bytes=20)["error"]["code"] == "validation_error"


@pytest.mark.parametrize("url", [
    "https://acme.zendesk.com:invalid/attachments/file",
    "https://acme.zendesk.com:8443/attachments/file",
    "https://cdn.example.test:invalid/file",
    "https://[broken/file",
    None,
])
def test_binary_transfer_rejects_malformed_urls_before_network(settings, authorization, url):
    def unexpected_request(request):
        pytest.fail("unsafe URL reached transport")
    client = ZendeskClient(settings, authorization, transport=httpx.MockTransport(unexpected_request))
    for result in (
        client.download_attachment(url, max_bytes=10),
        client.upload_presigned(url, {}, b"test"),
    ):
        assert result["error"]["code"] == "validation_error"


def test_help_center_image_download_allows_only_tenant_user_images(settings, authorization):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"].startswith("Basic ")
        return httpx.Response(200, content=b"image", headers={"Content-Type": "image/png"}, request=request)

    client = ZendeskClient(settings, authorization, transport=httpx.MockTransport(handler))

    result = client.download_help_center_image("https://acme.zendesk.com/hc/user_images/image.png", max_bytes=10)

    assert result["data"] == {"content": b"image", "content_type": "image/png", "size": 5}
    assert client.download_help_center_image("https://outside.example/image.png", max_bytes=10)["error"]["code"] == "validation_error"
    assert client.download_help_center_image("https://acme.zendesk.com:invalid/hc/user_images/image.png", max_bytes=10)["error"]["code"] == "validation_error"
