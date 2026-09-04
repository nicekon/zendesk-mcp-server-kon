"""Single validated HTTP transport for Zendesk API requests."""

from __future__ import annotations

import random
import time
from collections.abc import Callable, Mapping
from urllib.parse import urlsplit

import httpx

from .auth import AuthorizationProvider
from .config import Settings
from .contracts import ErrorCode, failure, success


class ZendeskClient:
    def __init__(
        self,
        settings: Settings,
        authorization: AuthorizationProvider,
        *,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if settings.subdomain is None:
            raise ValueError("Zendesk subdomain is required")
        self._base_url = f"https://{settings.subdomain}.zendesk.com"
        self._authorization = authorization
        self._sleep = sleep
        self._client = httpx.Client(
            transport=transport,
            timeout=httpx.Timeout(connect=5.0, read=30.0, write=30.0, pool=5.0),
            follow_redirects=False,
        )

    def get(
        self,
        path: str,
        *,
        params: Mapping[str, str] | None = None,
    ) -> dict[str, object]:
        return self.request("GET", path, params=params)

    def request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, str] | None = None,
        json_body: Mapping[str, object] | None = None,
    ) -> dict[str, object]:
        method = method.upper()
        url = self._build_url(path)
        if url is None:
            return failure(ErrorCode.VALIDATION_ERROR, "Zendesk path must be tenant-relative")

        read_request = method in {"GET", "HEAD"}
        attempts = 3 if read_request else 1
        for attempt in range(attempts):
            try:
                response = self._client.request(
                    method,
                    url,
                    params=params,
                    json=json_body,
                    headers=self._authorization.headers(),
                )
            except httpx.TimeoutException:
                if read_request and attempt < attempts - 1:
                    self._sleep(self._retry_delay(attempt))
                    continue
                return failure(
                    ErrorCode.TIMEOUT,
                    "Zendesk request timed out",
                    retryable=read_request,
                    operation_state="not_applied" if read_request else "unknown",
                )
            except httpx.HTTPError:
                if read_request and attempt < attempts - 1:
                    self._sleep(self._retry_delay(attempt))
                    continue
                return failure(
                    ErrorCode.UPSTREAM_ERROR,
                    "Zendesk request could not be completed",
                    retryable=read_request,
                    operation_state="not_applied" if read_request else "unknown",
                )

            if response.is_success:
                return success(_response_data(response), request_id=_request_id(response))

            code = _error_code(response.status_code)
            retry_after = _retry_after(response)
            can_retry = read_request and attempt < attempts - 1 and (
                response.status_code >= 500
                or (response.status_code == 429 and retry_after is not None)
            )
            if can_retry:
                self._sleep(retry_after if response.status_code == 429 else self._retry_delay(attempt))
                continue

            return failure(
                code,
                f"Zendesk request failed with HTTP {response.status_code}",
                retryable=read_request and (response.status_code == 429 or response.status_code >= 500),
                operation_state="not_applied" if read_request else "unknown",
                request_id=_request_id(response),
            )

        return failure(ErrorCode.UPSTREAM_ERROR, "Zendesk request could not be completed")

    def close(self) -> None:
        self._client.close()

    def _build_url(self, path: str) -> str | None:
        parsed = urlsplit(path)
        if (
            parsed.scheme
            or parsed.netloc
            or parsed.query
            or not parsed.path.startswith("/")
            or parsed.path.startswith("//")
        ):
            return None
        return f"{self._base_url}{parsed.path}"

    @staticmethod
    def _retry_delay(attempt: int) -> float:
        return min(30.0, float(2**attempt) + random.uniform(0.0, 0.25))


def _response_data(response: httpx.Response) -> dict[str, object]:
    try:
        payload = response.json()
    except ValueError:
        return {}
    return payload if isinstance(payload, dict) else {"items": payload}


def _request_id(response: httpx.Response) -> str | None:
    return response.headers.get("x-zendesk-request-id")


def _retry_after(response: httpx.Response) -> float | None:
    try:
        value = float(response.headers["Retry-After"])
    except (KeyError, ValueError):
        return None
    return value if 0.0 <= value <= 30.0 else None


def _error_code(status_code: int) -> ErrorCode:
    if status_code in {400, 422}:
        return ErrorCode.VALIDATION_ERROR
    if status_code == 401:
        return ErrorCode.AUTHENTICATION_FAILED
    if status_code == 403:
        return ErrorCode.PERMISSION_DENIED
    if status_code == 404:
        return ErrorCode.NOT_FOUND
    if status_code in {409, 412}:
        return ErrorCode.CONFLICT
    if status_code == 429:
        return ErrorCode.RATE_LIMITED
    return ErrorCode.UPSTREAM_ERROR
