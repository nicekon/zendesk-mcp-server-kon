"""Single validated HTTP transport for Zendesk API requests."""

from __future__ import annotations

import ipaddress
import math
import random
import re
import socket
import time
from threading import Lock
from collections.abc import Callable, Mapping, Iterable, Iterator
from urllib.parse import urljoin, urlsplit

import httpx

from .auth import AuthorizationProvider
from .config import ConfigurationError, Settings
from .contracts import ErrorCode, failure, success


_SUBDOMAIN = re.compile(r"[a-z0-9][a-z0-9-]{0,62}$")
_COOLDOWNS: dict[str, float] = {}
_COOLDOWN_LOCK = Lock()


class _DownloadSizeExceeded(Exception):
    pass


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
        refreshed = False; sleep_total = 0.0; own_cooldown = 0.0
        for attempt in range(attempts):
            limited = self._cooldown_failure(own_cooldown if attempt else 0.0)
            if limited is not None: return limited
            try:
                response = self._client.request(
                    method,
                    url,
                    params=params,
                    json=json_body,
                    headers=self._authorization.headers(),
                )
            except httpx.TimeoutException:
                delay = self._retry_delay(attempt)
                if read_request and attempt < attempts - 1 and sleep_total + delay <= 30.0:
                    self._sleep(delay)
                    sleep_total += delay
                    continue
                return failure(
                    ErrorCode.TIMEOUT,
                    "Zendesk request timed out",
                    retryable=read_request,
                    operation_state="not_applied" if read_request else "unknown",
                )
            except httpx.HTTPError:
                delay = self._retry_delay(attempt)
                if read_request and attempt < attempts - 1 and sleep_total + delay <= 30.0:
                    self._sleep(delay)
                    sleep_total += delay
                    continue
                return failure(
                    ErrorCode.UPSTREAM_ERROR,
                    "Zendesk request could not be completed",
                    retryable=read_request,
                    operation_state="not_applied" if read_request else "unknown",
                )

            if response.is_success:
                try:
                    data = {} if response.status_code == 204 or method == "HEAD" else _response_data(response)
                except ValueError:
                    return failure(
                        ErrorCode.UPSTREAM_ERROR,
                        "Zendesk returned invalid JSON",
                        operation_state="not_applied" if read_request else "unknown",
                        request_id=_request_id(response),
                    )
                return success(data, request_id=_request_id(response))

            if response.status_code == 401 and not refreshed:
                refresh = getattr(self._authorization, "refresh", None)
                if callable(refresh):
                    try:
                        refresh()
                    except ConfigurationError:
                        return failure(ErrorCode.REAUTHORIZATION_REQUIRED, "Zendesk authentication refresh failed")
                    refreshed = True
                    if read_request: continue
                    return failure(ErrorCode.AUTHENTICATION_RETRY_REQUIRED, "Zendesk authentication refreshed; retry the write", retryable=True, operation_state="not_applied", request_id=_request_id(response))

            code = _error_code(response.status_code)
            retry_after = _retry_after(response)
            if response.status_code == 429:
                own_cooldown = self._record_cooldown(response)
            can_retry = read_request and attempt < attempts - 1 and (
                response.status_code >= 500
                or (response.status_code == 429 and retry_after is not None)
            )
            if can_retry:
                delay = retry_after if response.status_code == 429 else self._retry_delay(attempt)
                if response.status_code == 429 and delay is not None:
                    delay += random.uniform(0.0, min(0.25, 30.0 - delay))
                if delay is not None and sleep_total + delay <= 30.0:
                    self._sleep(delay); sleep_total += delay
                    continue

            message = f"Zendesk request failed with HTTP {response.status_code}"
            details = None
            if response.status_code == 403:
                try:
                    body = response.json()
                except ValueError:
                    body = None
                if isinstance(body, dict) and body.get("description") == "You are missing the following required scopes: read":
                    message = "Zendesk requires broad OAuth read scope for this endpoint; current resource-specific scope policy does not grant it"
                    details = {"required_scopes": ["read"]}
            return failure(
                code,
                message,
                retryable=read_request and (response.status_code == 429 or response.status_code >= 500),
                operation_state="not_applied" if read_request or response.status_code in {401, 403, 409, 412, 429} else "unknown",
                request_id=_request_id(response),
                details=details,
            )

        return failure(ErrorCode.UPSTREAM_ERROR, "Zendesk request could not be completed")

    def close(self) -> None:
        self._client.close()

    def upload_presigned(self, url: str, headers: Mapping[str, str], content: bytes | Iterator[bytes]) -> dict[str, object]:
        if not _is_public_https_url(url) or not isinstance(content, (bytes, Iterator)) or any(not isinstance(name, str) or not isinstance(value, str) for name, value in headers.items()) or any(name.lower() in {"authorization", "host"} for name in headers):
            return failure(ErrorCode.VALIDATION_ERROR, "presigned upload URL, headers, or content is unsafe")
        try:
            response = self._client.request("PUT", url, headers=dict(headers), content=content)
        except httpx.TimeoutException:
            return failure(ErrorCode.TIMEOUT, "Zendesk upload timed out", operation_state="unknown")
        except (httpx.HTTPError, OSError):
            return failure(ErrorCode.UPSTREAM_ERROR, "Zendesk upload could not be completed", operation_state="unknown")
        if response.is_success:
            return success({}, request_id=_request_id(response))
        return failure(_error_code(response.status_code), f"Zendesk upload failed with HTTP {response.status_code}", operation_state="not_applied" if response.status_code in {401, 403, 429} else "unknown", request_id=_request_id(response))

    def download_attachment(self, content_url: str, *, max_bytes: int, store: Callable[[Iterable[bytes]], dict[str, object]] | None = None) -> dict[str, object]:
        if not isinstance(max_bytes, int) or isinstance(max_bytes, bool) or max_bytes < 1 or not self._is_attachment_url(content_url):
            return failure(ErrorCode.VALIDATION_ERROR, "attachment URL or size limit is unsafe")
        return self._download_file(content_url, max_bytes, urlsplit(self._base_url).hostname, allow_redirects=True, store=store)

    def download_help_center_image(self, image_url: str, *, max_bytes: int, subdomain: str | None = None) -> dict[str, object]:
        if not isinstance(max_bytes, int) or isinstance(max_bytes, bool) or max_bytes < 1 or not self._is_help_center_image_url(image_url, subdomain):
            return failure(ErrorCode.VALIDATION_ERROR, "Help Center image URL or size limit is unsafe")
        return self._download_file(image_url, max_bytes, urlsplit(image_url).hostname, allow_redirects=False)

    def _download_file(self, url: str, max_bytes: int, authorized_host: str | None, *, allow_redirects: bool, store: Callable[[Iterable[bytes]], dict[str, object]] | None = None) -> dict[str, object]:
        sleep_total = 0.0; own_cooldown = 0.0; attempt = 0; redirects = 0; refreshed = False
        while attempt < 3:
            authenticated = urlsplit(url).hostname == authorized_host
            if authenticated:
                limited = self._cooldown_failure(own_cooldown)
                if limited is not None: return limited
            delay = self._retry_delay(attempt)
            try:
                with self._client.stream("GET", url, headers=self._authorization.headers() if authenticated else {}) as response:
                    if authenticated: own_cooldown = self._record_cooldown(response)
                    if authenticated and response.status_code == 401 and not refreshed and attempt < 2:
                        refresh = getattr(self._authorization, "refresh", None)
                        if callable(refresh):
                            response.close()
                            try:
                                refresh()
                            except ConfigurationError:
                                return failure(ErrorCode.REAUTHORIZATION_REQUIRED, "Zendesk authentication refresh failed")
                            refreshed = True
                            attempt += 1
                            continue
                    if response.is_redirect and allow_redirects:
                        redirects += 1
                        if redirects >= 3: return failure(ErrorCode.UPSTREAM_ERROR, "Zendesk attachment redirected too many times")
                        location = response.headers.get("Location")
                        url = urljoin(url, location) if location else ""
                        if not self._is_attachment_url(url): return failure(ErrorCode.VALIDATION_ERROR, "attachment redirect URL is unsafe")
                        continue
                    if not response.is_success:
                        retryable = response.status_code == 429 or response.status_code >= 500
                        result = failure(_error_code(response.status_code), f"Zendesk file download failed with HTTP {response.status_code}", retryable=retryable, request_id=_request_id(response))
                        if not retryable: return result
                        if response.status_code == 429:
                            delay = _retry_after(response)
                            if delay is None: return result
                            delay += random.uniform(0.0, min(0.25, 30.0-delay))
                    else:
                        try:
                            declared_size = int(response.headers.get("Content-Length", "0"))
                        except ValueError:
                            declared_size = 0
                        if declared_size > max_bytes: return failure(ErrorCode.VALIDATION_ERROR, "file exceeds the download size limit")
                        size = 0
                        def chunks():
                            nonlocal size
                            for chunk in response.iter_bytes(chunk_size=65536):
                                size += len(chunk)
                                if size > max_bytes: raise _DownloadSizeExceeded
                                yield chunk
                        if store is None:
                            data = {"content": b"".join(chunks())}
                        else:
                            stored = store(chunks())
                            if not stored.get("ok"): return stored
                            data = stored["data"]
                        return success({**data, "content_type": response.headers.get("Content-Type") or "application/octet-stream", "size": size}, request_id=_request_id(response))
            except _DownloadSizeExceeded:
                return failure(ErrorCode.VALIDATION_ERROR, "file exceeds the download size limit")
            except httpx.TimeoutException:
                result = failure(ErrorCode.TIMEOUT, "Zendesk file download timed out", retryable=True)
            except httpx.HTTPError:
                result = failure(ErrorCode.UPSTREAM_ERROR, "Zendesk file download could not be completed", retryable=True)
            if attempt == 2 or sleep_total + delay > 30.0: return result
            self._sleep(delay)
            sleep_total += delay
            attempt += 1

    def _record_cooldown(self, response: httpx.Response) -> float:
        if response.status_code != 429: return 0.0
        try:
            duration = float(response.headers.get("Retry-After", "nan"))
        except ValueError:
            return 0.0
        if not math.isfinite(duration) or duration < 0: return 0.0
        with _COOLDOWN_LOCK:
            deadline = time.monotonic() + duration
            _COOLDOWNS[self._base_url] = max(_COOLDOWNS.get(self._base_url, 0.0), deadline)
        return deadline

    def _cooldown_failure(self, own_cooldown: float = 0.0) -> dict[str, object] | None:
        with _COOLDOWN_LOCK:
            now = time.monotonic()
            for tenant, deadline in list(_COOLDOWNS.items()):
                if deadline <= now: del _COOLDOWNS[tenant]
            cooldown = _COOLDOWNS.get(self._base_url, 0.0)
        if cooldown > now and cooldown > own_cooldown:
            return failure(ErrorCode.RATE_LIMITED, "Zendesk tenant is in a shared rate-limit cooldown; retry later", retryable=True, operation_state="not_applied")
        return None

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
        if not isinstance(value, str): return False
        try: parsed = urlsplit(value); port = parsed.port
        except ValueError: return False
        return parsed.scheme == "https" and port in {None, 443} and not parsed.username and not parsed.password and (parsed.hostname == urlsplit(self._base_url).hostname or _is_public_https_url(value))

    def _is_help_center_image_url(self, value: object, subdomain: str | None) -> bool:
        if not isinstance(value, str): return False
        try: parsed = urlsplit(value); port = parsed.port
        except ValueError: return False
        host = f"{subdomain}.zendesk.com" if isinstance(subdomain, str) and _SUBDOMAIN.fullmatch(subdomain) else urlsplit(self._base_url).hostname
        return parsed.scheme == "https" and parsed.hostname == host and port in {None, 443} and not parsed.username and not parsed.password and parsed.path.startswith("/hc/user_images/")

    @staticmethod
    def _retry_delay(attempt: int) -> float:
        return min(30.0, float(2**attempt) + random.uniform(0.0, 0.25))


def _response_data(response: httpx.Response) -> dict[str, object]:
    payload = response.json()
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
    if not isinstance(value, str): return False
    try: parsed = urlsplit(value); port = parsed.port
    except ValueError: return False
    if parsed.scheme != "https" or port not in {None, 443} or not parsed.hostname or parsed.username or parsed.password:
        return False
    try:
        addresses = [ipaddress.ip_address(parsed.hostname)]
    except ValueError:
        try:
            addresses = [ipaddress.ip_address(record[4][0]) for record in socket.getaddrinfo(parsed.hostname, parsed.port or 443, type=socket.SOCK_STREAM)]
        except OSError:
            return False
    return bool(addresses) and all(address.is_global for address in addresses)
