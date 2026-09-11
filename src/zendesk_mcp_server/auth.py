"""Authorization header providers and secure OAuth token persistence."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import stat
import tempfile
import time
from dataclasses import dataclass, field
from contextlib import contextmanager
from pathlib import Path
from typing import Protocol
from collections.abc import Callable
from urllib.parse import urlencode, urlsplit

import httpx

from .config import AuthMode, ConfigurationError, Settings
from .locking import exclusive_lock


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
    payload = {"grant_type": "refresh_token", "client_id": client_id, "refresh_token": refresh_token}
    if client_secret:
        payload["client_secret"] = client_secret
    return payload


def pkce_challenge(verifier: str) -> str:
    _validate_code_verifier(verifier)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def create_oauth_authorization_request(subdomain: str, client_id: str, redirect_uri: str, scopes: tuple[str, ...], state_store: "OAuthStateStore", *, now: int, code_verifier: str | None = None) -> dict[str, str]:
    _validate_authorization_inputs(subdomain, client_id, redirect_uri, scopes)
    state = state_store.create(redirect_uri, scopes, now=now)
    query = urlencode({"response_type": "code", "client_id": client_id, "redirect_uri": redirect_uri, "scope": " ".join(scopes), "state": state})
    if code_verifier is not None:
        query += "&" + urlencode({"code_challenge": pkce_challenge(code_verifier), "code_challenge_method": "S256"})
    return {"state": state, "authorization_url": f"https://{subdomain}.zendesk.com/oauth/authorizations/new?{query}"}


def create_settings_oauth_authorization_request(settings: Settings, redirect_uri: str, state_store: "OAuthStateStore", *, now: int, code_verifier: str | None = None) -> dict[str, str]:
    if settings.auth_mode is not AuthMode.OAUTH or settings.oauth is None or settings.subdomain is None:
        raise ConfigurationError("missing_oauth", "OAuth configuration is missing")
    if settings.oauth.client_kind == "public" and code_verifier is None:
        raise ConfigurationError("missing_pkce", "Public OAuth requires PKCE")
    return create_oauth_authorization_request(settings.subdomain, settings.oauth.client_id, redirect_uri, settings.oauth.scopes, state_store, now=now, code_verifier=code_verifier)


def oauth_state_store(settings: Settings) -> "OAuthStateStore":
    if settings.auth_mode is not AuthMode.OAUTH or settings.oauth is None:
        raise ConfigurationError("missing_oauth", "OAuth configuration is missing")
    path = settings.oauth.token_store_path
    return OAuthStateStore(path.with_name(f".{path.name}.state"))


def exchange_oauth_authorization_code(request: Callable[[dict[str, str]], object], client_id: str, client_secret: str, code: str, state: str, redirect_uri: str, scopes: tuple[str, ...], state_store: "OAuthStateStore", token_store: "OAuthTokenStore | None", *, now: int, code_verifier: str | None = None) -> OAuthTokens:
    if not isinstance(client_secret, str) or (not client_secret and code_verifier is None) or not isinstance(code, str) or not code:
        raise ConfigurationError("invalid_oauth_authorization", "OAuth authorization response is invalid")
    state_store.consume(state, redirect_uri, scopes, now=now)
    payload = {"grant_type": "authorization_code", "code": code, "client_id": client_id, "redirect_uri": redirect_uri, "scope": " ".join(scopes)}
    if client_secret:
        payload["client_secret"] = client_secret
    if code_verifier is not None:
        _validate_code_verifier(code_verifier)
        payload["code_verifier"] = code_verifier
    tokens = oauth_tokens_from_refresh_response(request(payload), now=now)
    if token_store is not None:
        token_store.save(tokens)
    return tokens


def exchange_settings_oauth_authorization_code(settings: Settings, code: str, state: str, redirect_uri: str, *, requester: Callable[[dict[str, str]], object] | None = None, now: int | None = None, code_verifier: str | None = None) -> OAuthTokens:
    if settings.auth_mode is not AuthMode.OAUTH or settings.oauth is None or settings.subdomain is None:
        raise ConfigurationError("missing_oauth", "OAuth configuration is missing")
    return exchange_oauth_authorization_code(
        requester or _oauth_refresh_requester(settings.subdomain),
        settings.oauth.client_id,
        settings.oauth.client_secret,
        code,
        state,
        redirect_uri,
        settings.oauth.scopes,
        oauth_state_store(settings),
        OAuthTokenStore(settings.oauth.token_store_path),
        now=int(time.time()) if now is None else now,
        code_verifier=code_verifier,
    )


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
        values: dict[str, object] = {}
        if self.path.exists():
            self._require_user_only_permissions()
            try:
                current = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(current, dict):
                    values.update(current)
            except (OSError, ValueError, json.JSONDecodeError):
                pass
        values.update({
            "access_token": tokens.access_token,
            "refresh_token": tokens.refresh_token,
            "expires_at": tokens.expires_at,
        })
        self._save_values(values)

    def _save_values(self, values: dict[str, object]) -> None:
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
                json.dump(values, temporary_file, separators=(",", ":"))
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
            with exclusive_lock(descriptor): yield
        finally:
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


def save_connection(settings: Settings, tokens: OAuthTokens) -> None:
    if settings.oauth is None or settings.subdomain is None or settings.oauth.client_kind != "public":
        raise ConfigurationError("invalid_oauth_configuration", "Only public OAuth connections can be saved")
    OAuthTokenStore(settings.oauth.token_store_path)._save_values({
        "schema_version": 1,
        "subdomain": settings.subdomain,
        "client_id": settings.oauth.client_id,
        "client_kind": settings.oauth.client_kind,
        "scopes": list(settings.oauth.scopes),
        "access_token": tokens.access_token,
        "refresh_token": tokens.refresh_token,
        "expires_at": tokens.expires_at,
    })


class OAuthStateStore:
    def __init__(self, path: Path) -> None: self.path = path

    def create(self, redirect_uri: str, scopes: tuple[str, ...], *, now: int) -> str:
        state = secrets.token_urlsafe(32)
        with self._lock():
            values = self._load()
            values[state] = {"redirect_uri": redirect_uri, "scopes": list(scopes), "expires_at": now + 600}
            self._save(values)
        return state

    def consume(self, state: str, redirect_uri: str, scopes: tuple[str, ...], *, now: int) -> None:
        if not isinstance(state, str) or not state:
            raise ConfigurationError("invalid_oauth_state", "OAuth state is invalid")
        with self._lock():
            values = self._load(); key = next((value for value in values if hmac.compare_digest(value, state)), None); record = values.pop(key, None) if key else None
            self._save(values)
        if not isinstance(record, dict) or record.get("expires_at", 0) < now or not hmac.compare_digest(str(record.get("redirect_uri", "")), redirect_uri) or record.get("scopes") != list(scopes):
            raise ConfigurationError("invalid_oauth_state", "OAuth state, redirect URI, or scopes are invalid")

    @contextmanager
    def _lock(self):
        self.path.parent.mkdir(parents=True, exist_ok=True); descriptor = os.open(self.path.with_name(f".{self.path.name}.lock"), os.O_RDWR | os.O_CREAT, 0o600)
        try:
            os.chmod(descriptor, 0o600)
            with exclusive_lock(descriptor): yield
        finally:
            os.close(descriptor)

    def _load(self) -> dict[str, object]:
        if not self.path.exists(): return {}
        try:
            if stat.S_IMODE(self.path.stat().st_mode) & 0o077: raise ConfigurationError("unsafe_oauth_permissions", "OAuth state file permissions must be user-only")
            value = json.loads(self.path.read_text(encoding="utf-8")); return value.get("states", {}) if isinstance(value, dict) and isinstance(value.get("states", {}), dict) else {}
        except ConfigurationError:
            raise
        except (OSError, ValueError, json.JSONDecodeError) as error:
            raise ConfigurationError("invalid_oauth_state", "OAuth state file is invalid") from error

    def _save(self, values: dict[str, object]) -> None:
        temporary = self.path.with_name(f".{self.path.name}.{secrets.token_hex(8)}.tmp")
        try:
            with open(temporary, "w", encoding="utf-8") as stream:
                os.chmod(temporary, 0o600); json.dump({"states": values}, stream, separators=(",", ":")); stream.flush(); os.fsync(stream.fileno())
            temporary.replace(self.path)
        finally:
            temporary.unlink(missing_ok=True)


def _validate_authorization_inputs(subdomain: object, client_id: object, redirect_uri: object, scopes: object) -> None:
    parsed = urlsplit(redirect_uri) if isinstance(redirect_uri, str) else None
    try:
        redirect_port = parsed.port if parsed is not None else None
    except ValueError:
        redirect_port = None
    secure_redirect = parsed is not None and parsed.scheme == "https" and bool(parsed.netloc)
    loopback_redirect = parsed is not None and parsed.scheme == "http" and parsed.hostname == "127.0.0.1" and redirect_port is not None
    if not isinstance(subdomain, str) or not subdomain or not isinstance(client_id, str) or not client_id or parsed is None or not (secure_redirect or loopback_redirect) or parsed.username or parsed.password or parsed.fragment or not isinstance(scopes, tuple) or not scopes or any(not isinstance(scope, str) or not scope for scope in scopes):
        raise ConfigurationError("invalid_oauth_authorization", "OAuth authorization configuration is invalid")


def _validate_code_verifier(verifier: object) -> None:
    if not isinstance(verifier, str) or not re.fullmatch(r"[A-Za-z0-9._~-]{43,128}", verifier):
        raise ConfigurationError("invalid_pkce", "OAuth PKCE verifier is invalid")


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
