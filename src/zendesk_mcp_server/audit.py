"""Redacted local audit metadata for MCP tool calls."""

from __future__ import annotations

import json
import logging
import os
import stat
import time
from collections.abc import Mapping
from pathlib import Path


class AuditLog:
    def __init__(self, path: Path) -> None:
        self.path = path

    @classmethod
    def from_environment(cls, environ: Mapping[str, str]) -> "AuditLog":
        configured = environ.get("ZENDESK_AUDIT_LOG")
        path = Path(configured).expanduser() if configured else Path.home() / ".local" / "state" / "zendesk-mcp" / "audit.jsonl"
        return cls(path)

    def record(self, tool: str, risk_class: str, arguments: Mapping[str, object] | None, result: Mapping[str, object], *, started_at: float) -> None:
        if not callable(getattr(os, "fchmod", None)):
            logging.getLogger(__name__).warning("Local audit log unsupported: private file permissions unavailable; event not persisted")
            return
        event = {
            "timestamp": int(time.time()),
            "tool": tool,
            "risk_class": risk_class,
            "resource_id": _resource_id(arguments),
            "request_id": _request_id(result),
            "result_code": "ok" if result.get("ok") is True else _error_code(result),
            "latency_ms": round((time.monotonic() - started_at) * 1000),
        }
        try:
            self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            if self.path.is_symlink() or self.path.parent.is_symlink(): return
            descriptor = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0), 0o600)
            try:
                info = os.fstat(descriptor)
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1: return
                os.fchmod(descriptor, 0o600)
                os.write(descriptor, (json.dumps(event, separators=(",", ":")) + "\n").encode())
            finally:
                os.close(descriptor)
        except OSError:
            pass


def _resource_id(arguments: Mapping[str, object] | None) -> int | str | None:
    if not isinstance(arguments, Mapping):
        return None
    for key in ("ticket_id", "article_id", "post_id", "comment_id", "topic_id", "subscription_id", "vote_id", "badge_id", "tag_id", "assignment_id"):
        value = arguments.get(key)
        if isinstance(value, (int, str)) and not isinstance(value, bool):
            return value
    return None


def _request_id(result: Mapping[str, object]) -> str | None:
    value = result.get("request_id")
    if isinstance(value, str):
        return value
    error = result.get("error")
    value = error.get("request_id") if isinstance(error, Mapping) else None
    return value if isinstance(value, str) else None


def _error_code(result: Mapping[str, object]) -> str:
    error = result.get("error")
    value = error.get("code") if isinstance(error, Mapping) else None
    return value if isinstance(value, str) else "unknown_error"
