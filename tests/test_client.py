import httpx
import pytest

from zendesk_mcp_server.auth import ApiTokenAuthorization
from zendesk_mcp_server.client import ZendeskClient
from zendesk_mcp_server.config import Settings


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
        (403, "permission_denied"),
        (404, "not_found"),
        (409, "conflict"),
        (422, "validation_error"),
    ],
)
def test_client_maps_known_http_errors(settings, authorization, status_code, error_code):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, json={"error": "bad request"}, request=request)

    client = ZendeskClient(settings, authorization, transport=httpx.MockTransport(handler))

    assert client.get("/api/v2/users/me.json")["error"]["code"] == error_code


def test_client_rejects_an_absolute_or_foreign_path(settings, authorization):
    client = ZendeskClient(settings, authorization, transport=httpx.MockTransport(lambda request: None))

    result = client.get("https://other.example/api/v2/users/me.json")

    assert result["error"]["code"] == "validation_error"


class RefreshingAuthorization:
    def __init__(self): self.token, self.refreshes = "old", 0
    def headers(self): return {"Authorization": f"Bearer {self.token}"}
    def refresh(self): self.token = "new"; self.refreshes += 1


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


def test_presigned_upload_never_sends_zendesk_authorization(settings, authorization, monkeypatch):
    monkeypatch.setattr("zendesk_mcp_server.client.socket.getaddrinfo", lambda *args, **kwargs: [(0, 0, 0, "", ("8.8.8.8", 443))])

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers.get("Authorization") is None
        assert request.headers["Content-Type"] == "image/png"
        assert request.content == b"image"
        return httpx.Response(200, request=request)

    client = ZendeskClient(settings, authorization, transport=httpx.MockTransport(handler))

    assert client.upload_presigned("https://cdn.example.test/upload", {"Content-Type": "image/png"}, b"image")["ok"] is True


def test_presigned_upload_rejects_private_hosts(settings, authorization):
    client = ZendeskClient(settings, authorization, transport=httpx.MockTransport(lambda request: None))

    assert client.upload_presigned("https://127.0.0.1/upload", {"Content-Type": "image/png"}, b"image")["error"]["code"] == "validation_error"
