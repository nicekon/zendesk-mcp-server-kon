"""Configuration parsing for the Zendesk MCP server."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Mapping


_SUBDOMAIN_PATTERN = re.compile(r"[a-z0-9][a-z0-9-]{0,62}")


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
    email: str | None = None
    api_token: str | None = None
    oauth: OAuthConfig | None = None

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
            email=email if selected_mode is AuthMode.API_TOKEN else None,
            api_token=api_token if selected_mode is AuthMode.API_TOKEN else None,
            oauth=oauth if selected_mode is AuthMode.OAUTH else None,
        )

    def connection_status(self) -> dict[str, object]:
        if self.auth_mode is None:
            return {
                "configured": False,
                "auth_mode": None,
                "write_mode": self.write_mode,
            }

        return {
            "configured": True,
            "auth_mode": self.auth_mode.value,
            "write_mode": self.write_mode,
            "subdomain": self.subdomain,
        }


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
