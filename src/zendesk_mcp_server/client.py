"""Single validated HTTP transport for Zendesk API requests."""

from __future__ import annotations

import ipaddress
import random
import re
import socket
import time
from collections.abc import Callable, Mapping
from urllib.parse import urljoin, urlsplit

import httpx

from .auth import AuthorizationProvider
from .config import ConfigurationError, Settings
from .contracts import ErrorCode, failure, success


_SUBDOMAIN = re.compile(r"[a-z0-9][a-z0-9-]{0,62}$")


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

    def get_for_subdomain(
        self,
        subdomain: str,
        path: str,
        *,
        params: Mapping[str, str] | None = None,
    ) -> dict[str, object]:
        return self.request("GET", path, params=params, subdomain=subdomain)

    def request_for_subdomain(
        self,
        subdomain: str,
        method: str,
        path: str,
        *,
        json_body: Mapping[str, object] | None = None,
    ) -> dict[str, object]:
        return self.request(method, path, json_body=json_body, subdomain=subdomain)

    def request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, str] | None = None,
        json_body: Mapping[str, object] | None = None,
        subdomain: str | None = None,
    ) -> dict[str, object]:
        method = method.upper()
        url = self._build_url(path, subdomain)
        if url is None:
            return failure(ErrorCode.VALIDATION_ERROR, "Zendesk path must be tenant-relative")

        read_request = method in {"GET", "HEAD"}
        attempts = 3 if read_request else 1
        refreshed = False; sleep_total = 0.0
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

            if response.status_code == 401 and not refreshed:
                refresh = getattr(self._authorization, "refresh", None)
                if callable(refresh):
                    try:
                        refresh()
                    except ConfigurationError:
                        return failure(ErrorCode.AUTHENTICATION_FAILED, "Zendesk authentication refresh failed")
                    refreshed = True
                    if read_request: continue
                    return failure(ErrorCode.AUTHENTICATION_RETRY_REQUIRED, "Zendesk authentication refreshed; retry the write", retryable=True, operation_state="not_applied", request_id=_request_id(response))

            code = _error_code(response.status_code)
            retry_after = _retry_after(response)
            can_retry = read_request and attempt < attempts - 1 and (
                response.status_code >= 500
                or (response.status_code == 429 and retry_after is not None)
            )
            if can_retry:
                delay = retry_after if response.status_code == 429 else self._retry_delay(attempt)
                if delay is not None and sleep_total + delay <= 30.0:
                    self._sleep(delay); sleep_total += delay
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

    def upload_presigned(self, url: str, headers: Mapping[str, str], content: bytes) -> dict[str, object]:
        if not _is_public_https_url(url) or not isinstance(content, bytes) or any(not isinstance(name, str) or not isinstance(value, str) for name, value in headers.items()) or any(name.lower() in {"authorization", "host"} for name in headers):
            return failure(ErrorCode.VALIDATION_ERROR, "presigned upload URL, headers, or content is unsafe")
        try:
            response = self._client.request("PUT", url, headers=dict(headers), content=content)
        except httpx.TimeoutException:
            return failure(ErrorCode.TIMEOUT, "Zendesk upload timed out", operation_state="unknown")
        except httpx.HTTPError:
            return failure(ErrorCode.UPSTREAM_ERROR, "Zendesk upload could not be completed", operation_state="unknown")
        if response.is_success:
            return success({}, request_id=_request_id(response))
        return failure(_error_code(response.status_code), f"Zendesk upload failed with HTTP {response.status_code}", operation_state="unknown", request_id=_request_id(response))

    def download_attachment(self, content_url: str, *, max_bytes: int) -> dict[str, object]:
        if not isinstance(max_bytes, int) or isinstance(max_bytes, bool) or max_bytes < 1 or not self._is_attachment_url(content_url):
            return failure(ErrorCode.VALIDATION_ERROR, "attachment URL or size limit is unsafe")
        url = content_url
        for _ in range(3):
            headers = self._authorization.headers() if urlsplit(url).hostname == urlsplit(self._base_url).hostname else {}
            try:
                with self._client.stream("GET", url, headers=headers) as response:
                    if response.is_redirect:
                        location = response.headers.get("Location")
                        url = urljoin(url, location) if location else ""
                        if not self._is_attachment_url(url):
                            return failure(ErrorCode.VALIDATION_ERROR, "attachment redirect URL is unsafe")
                        continue
                    if not response.is_success:
                        return failure(_error_code(response.status_code), f"Zendesk attachment download failed with HTTP {response.status_code}", retryable=response.status_code >= 500, request_id=_request_id(response))
                    try:
                        declared_size = int(response.headers.get("Content-Length", "0"))
                    except ValueError:
                        declared_size = 0
                    if declared_size > max_bytes:
                        return failure(ErrorCode.VALIDATION_ERROR, "attachment exceeds the download size limit")
                    content = bytearray()
                    for chunk in response.iter_bytes():
                        content.extend(chunk)
                        if len(content) > max_bytes:
                            return failure(ErrorCode.VALIDATION_ERROR, "attachment exceeds the download size limit")
                    return success({"content": bytes(content), "content_type": response.headers.get("Content-Type") or "application/octet-stream", "size": len(content)}, request_id=_request_id(response))
            except httpx.TimeoutException:
                return failure(ErrorCode.TIMEOUT, "Zendesk attachment download timed out", retryable=True)
            except httpx.HTTPError:
                return failure(ErrorCode.UPSTREAM_ERROR, "Zendesk attachment download could not be completed", retryable=True)
        return failure(ErrorCode.UPSTREAM_ERROR, "Zendesk attachment redirected too many times")

    def _build_url(self, path: str, subdomain: str | None = None) -> str | None:
        parsed = urlsplit(path)
        if (
            parsed.scheme
            or parsed.netloc
            or parsed.query
            or not parsed.path.startswith("/")
            or parsed.path.startswith("//")
        ):
            return None
        if subdomain is not None:
            if not isinstance(subdomain, str) or not _SUBDOMAIN.fullmatch(subdomain): return None
            return f"https://{subdomain}.zendesk.com{parsed.path}"
        return f"{self._base_url}{parsed.path}"

    def _is_attachment_url(self, value: str) -> bool:
        parsed = urlsplit(value)
        return parsed.scheme == "https" and not parsed.username and not parsed.password and (parsed.hostname == urlsplit(self._base_url).hostname or _is_public_https_url(value))

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


def _is_public_https_url(value: str) -> bool:
    parsed = urlsplit(value)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        return False
    try:
        addresses = [ipaddress.ip_address(parsed.hostname)]
    except ValueError:
        try:
            addresses = [ipaddress.ip_address(record[4][0]) for record in socket.getaddrinfo(parsed.hostname, parsed.port or 443, type=socket.SOCK_STREAM)]
        except OSError:
            return False
    return bool(addresses) and all(address.is_global for address in addresses)
