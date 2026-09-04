"""Authorization header providers and secure OAuth token persistence."""

from __future__ import annotations

import base64
import fcntl
import json
import os
import stat
import tempfile
import time
from dataclasses import dataclass, field
from contextlib import contextmanager
from pathlib import Path
from typing import Protocol
from collections.abc import Callable

import httpx

from .config import AuthMode, ConfigurationError, Settings


class AuthorizationProvider(Protocol):
    def headers(self) -> dict[str, str]:
        """Return the authorization header for one Zendesk API request."""


@dataclass(frozen=True)
class ApiTokenAuthorization:
    email: str = field(repr=False)
    token: str = field(repr=False)

    def headers(self) -> dict[str, str]:
        value = f"{self.email}/token:{self.token}".encode("utf-8")
        encoded = base64.b64encode(value).decode("ascii")
        return {"Authorization": f"Basic {encoded}"}


@dataclass(frozen=True)
class OAuthTokens:
    access_token: str = field(repr=False)
    refresh_token: str = field(repr=False)
    expires_at: int

    def is_expired(self, *, now: int) -> bool:
        return self.expires_at <= now + 60


@dataclass
class OAuthAuthorization:
    access_token: str = field(repr=False)
    refresher: Callable[[], str] | None = field(default=None, repr=False)

    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.access_token}"}

    def refresh(self) -> None:
        if self.refresher is None: raise ConfigurationError("oauth_refresh_unavailable", "OAuth refresh is unavailable")
        self.access_token = self.refresher()


def oauth_tokens_from_refresh_response(value: object, *, now: int, previous_refresh_token: str | None = None) -> OAuthTokens:
    if not isinstance(value, dict) or not isinstance(value.get("access_token"), str) or not isinstance(value.get("expires_in"), int) or value["expires_in"] <= 0:
        raise ConfigurationError("invalid_oauth_refresh", "OAuth refresh response is invalid")
    refresh_token = value.get("refresh_token", previous_refresh_token)
    if not isinstance(refresh_token, str): raise ConfigurationError("invalid_oauth_refresh", "OAuth refresh response is invalid")
    return OAuthTokens(value["access_token"], refresh_token, now + value["expires_in"])


def oauth_refresh_payload(client_id: str, client_secret: str, refresh_token: str) -> dict[str, str]:
    return {"grant_type": "refresh_token", "client_id": client_id, "client_secret": client_secret, "refresh_token": refresh_token}


def refresh_oauth_tokens(request: Callable[[dict[str, str]], object], client_id: str, client_secret: str, refresh_token: str, *, now: int) -> OAuthTokens:
    return oauth_tokens_from_refresh_response(request(oauth_refresh_payload(client_id, client_secret, refresh_token)), now=now, previous_refresh_token=refresh_token)


def refresh_and_store_oauth_tokens(store: OAuthTokenStore, request: Callable[[dict[str, str]], object], client_id: str, client_secret: str, *, now: int, expected_access_token: str | None = None) -> OAuthTokens:
    with store.refresh_lock():
        current = store.load()
        if expected_access_token is not None and current.access_token != expected_access_token:
            return current
        refreshed = refresh_oauth_tokens(request, client_id, client_secret, current.refresh_token, now=now)
        store.save(refreshed)
        return refreshed


class OAuthTokenStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> OAuthTokens:
        self._require_user_only_permissions()
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
            return OAuthTokens(
                access_token=str(value["access_token"]),
                refresh_token=str(value["refresh_token"]),
                expires_at=int(value["expires_at"]),
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise ConfigurationError("invalid_oauth_tokens", "OAuth token file is invalid") from error

    def save(self, tokens: OAuthTokens) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self.path.parent,
                prefix=f".{self.path.name}.",
                delete=False,
            ) as temporary_file:
                temporary_path = Path(temporary_file.name)
                os.chmod(temporary_path, 0o600)
                json.dump(
                    {
                        "access_token": tokens.access_token,
                        "refresh_token": tokens.refresh_token,
                        "expires_at": tokens.expires_at,
                    },
                    temporary_file,
                    separators=(",", ":"),
                )
                temporary_file.flush()
                os.fsync(temporary_file.fileno())
            temporary_path.replace(self.path)
        finally:
            if temporary_path is not None and temporary_path.exists():
                temporary_path.unlink()

    @contextmanager
    def refresh_lock(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(self.path.with_name(f".{self.path.name}.lock"), os.O_RDWR | os.O_CREAT, 0o600)
        try:
            os.chmod(descriptor, 0o600)
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

    def _require_user_only_permissions(self) -> None:
        try:
            mode = stat.S_IMODE(self.path.stat().st_mode)
        except FileNotFoundError as error:
            raise ConfigurationError("missing_oauth_tokens", "OAuth token file is missing") from error
        if mode & 0o077:
            raise ConfigurationError(
                "unsafe_oauth_permissions",
                "OAuth token file permissions must be user-only",
            )


def build_authorization(settings: Settings, *, oauth_requester: Callable[[dict[str, str]], object] | None = None, now: int | None = None) -> AuthorizationProvider | None:
    if settings.auth_mode is None:
        return None
    if settings.auth_mode is AuthMode.API_TOKEN:
        return ApiTokenAuthorization(email=settings.email or "", token=settings.api_token or "")
    if settings.oauth is None:
        raise ConfigurationError("missing_oauth", "OAuth configuration is missing")
    store = OAuthTokenStore(settings.oauth.token_store_path)
    tokens = store.load()
    current_time = int(time.time()) if now is None else now
    requester = oauth_requester or _oauth_refresh_requester(settings.subdomain or "")
    if tokens.is_expired(now=current_time): tokens = refresh_and_store_oauth_tokens(store, requester, settings.oauth.client_id, settings.oauth.client_secret, now=current_time, expected_access_token=tokens.access_token)

    def refresh() -> str:
        return refresh_and_store_oauth_tokens(store, requester, settings.oauth.client_id, settings.oauth.client_secret, now=int(time.time()), expected_access_token=authorization.access_token).access_token

    authorization = OAuthAuthorization(tokens.access_token, refresh)
    return authorization


def _oauth_refresh_requester(subdomain: str) -> Callable[[dict[str, str]], object]:
    def request(payload: dict[str, str]) -> object:
        try:
            response = httpx.post(f"https://{subdomain}.zendesk.com/oauth/tokens", json=payload, timeout=httpx.Timeout(connect=5.0, read=30.0, write=30.0, pool=5.0))
            response.raise_for_status()
            return response.json()
        except (httpx.HTTPError, ValueError) as error:
            raise ConfigurationError("oauth_refresh_failed", "OAuth token refresh failed") from error
    return request
