"""Configuration parsing for the Zendesk MCP server."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Mapping


_SUBDOMAIN_PATTERN = re.compile(r"[a-z0-9][a-z0-9-]{0,62}")
_CAPABILITIES = frozenset({"support", "operations", "guide", "community", "csat", "custom_objects", "git_zen", "time_tracking", "badges"})


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
    email: str | None = None
    api_token: str | None = None
    oauth: OAuthConfig | None = None
    capabilities: frozenset[str] = frozenset({"support", "operations", "guide", "community"})

    @classmethod
    def load(
        cls,
        environ: Mapping[str, str],
        oauth_config_path: Path | None = None,
    ) -> "Settings":
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
        capabilities = frozenset(value.strip() for value in environ.get("ZENDESK_CAPABILITIES", "support,operations,guide,community").split(",") if value.strip())
        if not capabilities <= _CAPABILITIES: raise ConfigurationError("invalid_capabilities", "Zendesk capabilities contain an unknown value")

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

        oauth_values = (
            environ.get("ZENDESK_OAUTH_CLIENT_ID"),
            environ.get("ZENDESK_OAUTH_CLIENT_SECRET"),
            str(oauth_config_path) if oauth_config_path else environ.get("ZENDESK_OAUTH_TOKEN_STORE"),
        )
        oauth_present = any(oauth_values)
        oauth_complete = all(oauth_values)
        if oauth_present and not oauth_complete:
            raise ConfigurationError("incomplete_oauth", "incomplete OAuth configuration")

        oauth = None
        if oauth_complete:
            if subdomain is None:
                raise ConfigurationError("incomplete_oauth", "incomplete OAuth configuration")
            oauth = OAuthConfig(
                client_id=oauth_values[0],
                client_secret=oauth_values[1],
                token_store_path=Path(oauth_values[2]),
            )

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
            email=email if selected_mode is AuthMode.API_TOKEN else None,
            api_token=api_token if selected_mode is AuthMode.API_TOKEN else None,
            oauth=oauth if selected_mode is AuthMode.OAUTH else None,
            capabilities=capabilities,
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
