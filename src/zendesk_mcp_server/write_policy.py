"""Server-side write gates shared by every mutating tool."""

from __future__ import annotations

from enum import Enum

from .config import Settings
from .contracts import ErrorCode, failure


class WriteRisk(str, Enum):
    STANDARD = "standard"
    PUBLIC = "public"
    DESTRUCTIVE = "destructive"
    IMPERSONATION = "impersonation"
    EXTERNAL_UPLOAD = "external_upload"


def check_write_permission(settings: Settings, risk: WriteRisk) -> dict[str, object] | None:
    if settings.write_mode != "standard":
        return failure(ErrorCode.WRITE_DISABLED, "Zendesk writes require ZENDESK_WRITE_MODE=standard")
    enabled = {
        WriteRisk.STANDARD: True,
        WriteRisk.PUBLIC: settings.public_writes_enabled,
        WriteRisk.DESTRUCTIVE: settings.destructive_writes_enabled,
        WriteRisk.IMPERSONATION: settings.impersonation_enabled,
        WriteRisk.EXTERNAL_UPLOAD: settings.external_uploads_enabled,
    }[risk]
    if not enabled:
        return failure(ErrorCode.WRITE_DISABLED, f"Zendesk {risk.value} writes are disabled")
    return None
