"""Stable, JSON-serializable MCP result contracts."""

from __future__ import annotations

from enum import Enum
from typing import Literal, Mapping


class ErrorCode(str, Enum):
    AUTHENTICATION_FAILED = "authentication_failed"
    AUTHENTICATION_RETRY_REQUIRED = "authentication_retry_required"
    REAUTHORIZATION_REQUIRED = "reauthorization_required"
    PERMISSION_DENIED = "permission_denied"
    NOT_FOUND = "not_found"
    CONFLICT = "conflict"
    VALIDATION_ERROR = "validation_error"
    RATE_LIMITED = "rate_limited"
    UPSTREAM_ERROR = "upstream_error"
    TIMEOUT = "timeout"
    UNSUPPORTED = "unsupported"
    NOT_CONFIGURED = "not_configured"
    WRITE_DISABLED = "write_disabled"
    APPROVAL_REQUIRED = "approval_required"
    ARTICLE_BODY_REPLACE_REQUIRED = "article_body_replace_required"
    CURSOR_EXPIRED = "cursor_expired"
    UNSAFE_ATTACHMENT = "unsafe_attachment"
    PARTIAL_SUCCESS = "partial_success"
    OUTCOME_UNKNOWN = "outcome_unknown"


def success(
    data: Mapping[str, object],
    *,
    request_id: str | None = None,
    operation_state: Literal["applied", "partial", "unknown"] | None = None,
) -> dict[str, object]:
    result: dict[str, object] = {"ok": True, "data": dict(data)}
    if request_id is not None:
        result["request_id"] = request_id
    if operation_state is not None:
        result["operation_state"] = operation_state
    return result


def failure(
    code: ErrorCode,
    message: str,
    *,
    retryable: bool = False,
    operation_state: str = "not_applied",
    request_id: str | None = None,
    details: Mapping[str, object] | None = None,
) -> dict[str, object]:
    error: dict[str, object] = {
        "code": code.value,
        "message": message,
        "retryable": retryable,
        "operation_state": operation_state,
    }
    if request_id is not None:
        error["request_id"] = request_id
    if details is not None:
        error["details"] = dict(details)
    return {"ok": False, "error": error}
