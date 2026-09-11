"""Configuration parsing for the Zendesk MCP server."""

from __future__ import annotations

import json
import os
import re
import stat
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Mapping


_SUBDOMAIN_PATTERN = re.compile(r"[a-z0-9][a-z0-9-]{0,62}")
_CHECK_POSIX_PERMISSIONS = os.name != "nt"
_CAPABILITIES = frozenset({"support", "operations", "guide", "community", "csat", "custom_objects", "git_zen", "time_tracking", "badges"})
_OAUTH_SCOPES = {
    "support": {"tickets:read", "users:read", "groups:read", "organizations:read", "brands:read", "ticket_attachments:read"},
    "operations": {"ticket_views:read", "macros:read", "triggers:read"},
    "guide": {"hc:read"},
    "community": {"hc:read"},
    "csat": {"satisfaction_ratings:read"},
    "custom_objects": {"custom_objects:read"},
    "badges": {"hc:read"},
}
_AUTH_ENV_NAMES = frozenset({
    "ZENDESK_SUBDOMAIN", "ZENDESK_AUTH_MODE", "ZENDESK_EMAIL",
    "ZENDESK_API_TOKEN", "ZENDESK_API_KEY", "ZENDESK_OAUTH_CLIENT_ID",
    "ZENDESK_OAUTH_CLIENT_SECRET", "ZENDESK_OAUTH_CLIENT_KIND",
    "ZENDESK_OAUTH_TOKEN_STORE",
})


class AuthMode(str, Enum):
    AUTO = "auto"
    API_TOKEN = "api_token"
    OAUTH = "oauth"


class ConfigurationError(ValueError):
    """Raised when supplied settings are unsafe or incomplete."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class OAuthConfig:
    client_id: str
    client_secret: str
    token_store_path: Path
    scopes: tuple[str, ...]
    client_kind: str = "confidential"


@dataclass(frozen=True)
class Settings:
    subdomain: str | None
    auth_mode: AuthMode | None
    write_mode: str
    public_writes_enabled: bool = False
    destructive_writes_enabled: bool = False
    impersonation_enabled: bool = False
    external_uploads_enabled: bool = False
    upload_root: Path | None = None
    attachment_cache_root: Path | None = None
    knowledge_base_resource_enabled: bool = False
    email: str | None = None
    api_token: str | None = None
    oauth: OAuthConfig | None = None
    capabilities: frozenset[str] = frozenset({"support", "operations", "guide", "community"})
    git_zen_field_id: int | None = None

    @classmethod
    def load(
        cls,
        environ: Mapping[str, str],
        oauth_config_path: Path | None = None,
    ) -> "Settings":
        values = dict(environ)
        saved_scopes: frozenset[str] | None = None
        if not _AUTH_ENV_NAMES.intersection(values):
            path = saved_connection_path()
            if path.exists():
                saved = _load_saved_connection(path)
                values.update({
                    "ZENDESK_SUBDOMAIN": saved["subdomain"],
                    "ZENDESK_AUTH_MODE": "oauth",
                    "ZENDESK_OAUTH_CLIENT_KIND": saved["client_kind"],
                    "ZENDESK_OAUTH_CLIENT_ID": saved["client_id"],
                    "ZENDESK_OAUTH_TOKEN_STORE": str(path),
                })
                saved_scopes = frozenset(saved["scopes"])
        environ = values
        if environ.get("ZENDESK_API_KEY"):
            raise ConfigurationError(
                "deprecated_configuration",
                "ZENDESK_API_KEY is no longer supported; use ZENDESK_API_TOKEN",
            )

        requested_mode = _parse_auth_mode(environ.get("ZENDESK_AUTH_MODE", "auto"))
        write_mode = environ.get("ZENDESK_WRITE_MODE", "read_only")
        if write_mode not in {"read_only", "standard"}:
            raise ConfigurationError("invalid_write_mode", "ZENDESK_WRITE_MODE is invalid")
        public_writes_enabled = _parse_bool(environ, "ZENDESK_ENABLE_PUBLIC_WRITES")
        destructive_writes_enabled = _parse_bool(environ, "ZENDESK_ENABLE_DESTRUCTIVE_WRITES")
        impersonation_enabled = _parse_bool(environ, "ZENDESK_ENABLE_IMPERSONATION")
        external_uploads_enabled = _parse_bool(environ, "ZENDESK_ENABLE_EXTERNAL_UPLOADS")
        upload_root = environ.get("ZENDESK_UPLOAD_ROOT")
        attachment_cache_root = environ.get("ZENDESK_ATTACHMENT_CACHE_ROOT")
        knowledge_base_resource_enabled = _parse_bool(environ, "ZENDESK_ENABLE_KNOWLEDGE_BASE_RESOURCE")
        capabilities = frozenset(value.strip() for value in environ.get("ZENDESK_CAPABILITIES", "support,operations,guide,community").split(",") if value.strip())
        if not capabilities <= _CAPABILITIES: raise ConfigurationError("invalid_capabilities", "Zendesk capabilities contain an unknown value")
        try: git_zen_field_id = int(environ["ZENDESK_GIT_ZEN_FIELD_ID"]) if "ZENDESK_GIT_ZEN_FIELD_ID" in environ else None
        except ValueError as error: raise ConfigurationError("invalid_git_zen_field", "ZENDESK_GIT_ZEN_FIELD_ID must be a positive integer") from error
        if git_zen_field_id is not None and git_zen_field_id < 1: raise ConfigurationError("invalid_git_zen_field", "ZENDESK_GIT_ZEN_FIELD_ID must be a positive integer")

        subdomain = environ.get("ZENDESK_SUBDOMAIN")
        if subdomain is not None:
            subdomain = subdomain.strip().lower()
            if not _SUBDOMAIN_PATTERN.fullmatch(subdomain):
                raise ConfigurationError("invalid_subdomain", "ZENDESK_SUBDOMAIN is invalid")

        email = environ.get("ZENDESK_EMAIL")
        api_token = environ.get("ZENDESK_API_TOKEN")
        api_present = any((email, api_token))
        api_complete = all((subdomain, email, api_token))
        if api_present and not api_complete:
            raise ConfigurationError(
                "incomplete_api_token",
                "API token configuration requires subdomain, email, and token",
            )

        oauth_kind = environ.get("ZENDESK_OAUTH_CLIENT_KIND", "confidential").strip().lower()
        if oauth_kind not in {"public", "confidential"}:
            raise ConfigurationError("invalid_oauth_client_kind", "ZENDESK_OAUTH_CLIENT_KIND is invalid")
        oauth_client_id = environ.get("ZENDESK_OAUTH_CLIENT_ID")
        oauth_client_secret = environ.get("ZENDESK_OAUTH_CLIENT_SECRET")
        oauth_token_store = str(oauth_config_path) if oauth_config_path else environ.get("ZENDESK_OAUTH_TOKEN_STORE")
        oauth_present = any((oauth_client_id, oauth_client_secret, oauth_token_store, "ZENDESK_OAUTH_CLIENT_KIND" in environ))
        oauth_complete = all((oauth_client_id, oauth_token_store)) and (oauth_kind == "public" or bool(oauth_client_secret))
        if oauth_present and not oauth_complete:
            raise ConfigurationError("incomplete_oauth", "incomplete OAuth configuration")

        oauth = None
        if oauth_complete:
            if subdomain is None:
                raise ConfigurationError("incomplete_oauth", "incomplete OAuth configuration")
            oauth = OAuthConfig(
                client_id=oauth_client_id or "",
                client_secret=oauth_client_secret or "",
                token_store_path=Path(oauth_token_store or ""),
                scopes=_oauth_scopes(capabilities, write_mode, public_writes_enabled, destructive_writes_enabled, impersonation_enabled, external_uploads_enabled),
                client_kind=oauth_kind,
            )
            if saved_scopes is not None and not set(oauth.scopes) <= saved_scopes:
                raise ConfigurationError("oauth_relogin_required", "OAuth scope expansion requires login again")

        selected_mode = _select_auth_mode(
            requested_mode=requested_mode,
            api_complete=api_complete,
            oauth_complete=oauth_complete,
        )

        return cls(
            subdomain=subdomain,
            auth_mode=selected_mode,
            write_mode=write_mode,
            public_writes_enabled=public_writes_enabled,
            destructive_writes_enabled=destructive_writes_enabled,
            impersonation_enabled=impersonation_enabled,
            external_uploads_enabled=external_uploads_enabled,
            upload_root=Path(upload_root).expanduser() if upload_root else None,
            attachment_cache_root=Path(attachment_cache_root).expanduser() if attachment_cache_root else Path.home() / ".cache" / "zendesk-mcp-server" / "attachments",
            knowledge_base_resource_enabled=knowledge_base_resource_enabled,
            email=email if selected_mode is AuthMode.API_TOKEN else None,
            api_token=api_token if selected_mode is AuthMode.API_TOKEN else None,
            oauth=oauth if selected_mode is AuthMode.OAUTH else None,
            capabilities=capabilities,
            git_zen_field_id=git_zen_field_id,
        )

    def connection_status(self) -> dict[str, object]:
        if self.auth_mode is None:
            return {
                "configured": False,
                "auth_mode": None,
                "write_mode": self.write_mode,
                "active_write_gates": self.active_write_gates(),
                "capabilities": sorted(self.capabilities),
            }

        return {
            "configured": True,
            "auth_mode": self.auth_mode.value,
            "write_mode": self.write_mode,
            "active_write_gates": self.active_write_gates(),
            "capabilities": sorted(self.capabilities),
            "subdomain": self.subdomain,
        }

    def active_write_gates(self) -> list[str]:
        gates = ["standard"] if self.write_mode == "standard" else []
        if self.public_writes_enabled:
            gates.append("public")
        if self.destructive_writes_enabled:
            gates.append("destructive")
        if self.impersonation_enabled:
            gates.append("impersonation")
        if self.external_uploads_enabled:
            gates.append("external_upload")
        return gates

    def has_capability(self, name: str) -> bool:
        return name in self.capabilities


def _parse_auth_mode(value: str) -> AuthMode:
    try:
        return AuthMode(value.strip().lower())
    except ValueError as error:
        raise ConfigurationError("invalid_auth_mode", "ZENDESK_AUTH_MODE is invalid") from error


def _select_auth_mode(
    *,
    requested_mode: AuthMode,
    api_complete: bool,
    oauth_complete: bool,
) -> AuthMode | None:
    if requested_mode is AuthMode.AUTO:
        if oauth_complete:
            return AuthMode.OAUTH
        if api_complete:
            return AuthMode.API_TOKEN
        return None

    if requested_mode is AuthMode.API_TOKEN and api_complete:
        return AuthMode.API_TOKEN
    if requested_mode is AuthMode.OAUTH and oauth_complete:
        return AuthMode.OAUTH

    raise ConfigurationError(
        "missing_credentials",
        f"{requested_mode.value} authentication is not fully configured",
    )


def _parse_bool(environ: Mapping[str, str], name: str) -> bool:
    value = environ.get(name, "false").strip().lower()
    if value in {"true", "1"}:
        return True
    if value in {"false", "0"}:
        return False
    raise ConfigurationError("invalid_boolean", f"{name} must be true or false")


def _oauth_scopes(capabilities: frozenset[str], write_mode: str, public: bool, destructive: bool, impersonation: bool, external_upload: bool) -> tuple[str, ...]:
    scopes = set().union(*(_OAUTH_SCOPES.get(capability, set()) for capability in capabilities))
    if write_mode == "standard": scopes.add("tickets:write")
    if public or destructive or external_upload: scopes.add("hc:write")
    if impersonation: scopes.add("impersonate")
    return tuple(sorted(scopes))


def saved_connection_path() -> Path:
    return Path.home() / ".config" / "zendesk-mcp-server" / "connection.json"


def _has_unsafe_permissions(path: Path) -> bool:
    return _CHECK_POSIX_PERMISSIONS and bool(stat.S_IMODE(path.stat().st_mode) & 0o077)


def _load_saved_connection(path: Path) -> dict[str, object]:
    try:
        if _has_unsafe_permissions(path):
            raise ConfigurationError("unsafe_oauth_permissions", "OAuth connection file permissions must be user-only")
        value = json.loads(path.read_text(encoding="utf-8"))
    except ConfigurationError:
        raise
    except (OSError, ValueError, json.JSONDecodeError) as error:
        raise ConfigurationError("invalid_oauth_tokens", "Saved OAuth connection is invalid") from error
    if (
        not isinstance(value, dict)
        or value.get("schema_version") != 1
        or not isinstance(value.get("subdomain"), str)
        or not _SUBDOMAIN_PATTERN.fullmatch(value["subdomain"])
        or not isinstance(value.get("client_id"), str)
        or not value["client_id"]
        or value.get("client_kind") != "public"
        or not isinstance(value.get("scopes"), list)
        or not value["scopes"]
        or any(not isinstance(scope, str) or not scope for scope in value["scopes"])
    ):
        raise ConfigurationError("invalid_oauth_tokens", "Saved OAuth connection is invalid")
    return value
