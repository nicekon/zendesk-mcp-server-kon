"""Local, single-use approvals for high-risk Zendesk writes."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import secrets
import tempfile
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path


class ApprovalStore:
    def __init__(self, path: Path, *, now: Callable[[], float] = time.time) -> None:
        self.path = path
        self._now = now

    def create(self, tool: str, payload: dict[str, object]) -> str:
        with self._locked():
            records = self._load()
            self._prune(records)
            request_id = str(uuid.uuid4())
            records[request_id] = {
                "tool": tool,
                "payload_hash": _payload_hash(payload),
                "expires_at": self._now() + 300,
                "approved": False,
                "consumed": False,
            }
            self._save(records)
        return request_id

    def approve(self, request_id: str) -> str:
        with self._locked():
            records = self._load()
            self._prune(records)
            record = records.get(request_id)
            if not isinstance(record, dict):
                raise ValueError("approval request is missing or expired")
            token = secrets.token_urlsafe(32)
            record["token_hash"] = _token_hash(token)
            record["approved"] = True
            self._save(records)
        return token

    def consume(self, request_id: str, tool: str, payload: dict[str, object], token: str) -> bool:
        with self._locked():
            records = self._load()
            self._prune(records)
            record = records.get(request_id)
            if not isinstance(record, dict):
                self._save(records)
                return False
            valid = (
                record.get("tool") == tool
                and record.get("payload_hash") == _payload_hash(payload)
                and record.get("approved") is True
                and record.get("consumed") is False
                and isinstance(record.get("token_hash"), str)
                and secrets.compare_digest(record["token_hash"], _token_hash(token))
            )
            if valid:
                record["consumed"] = True
                self._save(records)
            return valid

    def _load(self) -> dict[str, object]:
        if not self.path.exists():
            return {}
        if self.path.stat().st_mode & 0o077:
            raise ValueError("approval store permissions must be user-only")
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            raise ValueError("approval store is invalid") from error
        return value if isinstance(value, dict) else {}

    def _save(self, records: dict[str, object]) -> None:
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.path.parent, delete=False) as temporary:
            temporary_path = Path(temporary.name)
            os.chmod(temporary_path, 0o600)
            json.dump(records, temporary, separators=(",", ":"), sort_keys=True)
            temporary.flush()
            os.fsync(temporary.fileno())
        temporary_path.replace(self.path)

    def _prune(self, records: dict[str, object]) -> None:
        now = self._now()
        for request_id, record in list(records.items()):
            if not isinstance(record, dict) or record.get("expires_at", 0) <= now:
                records.pop(request_id)

    @contextmanager
    def _locked(self) -> Iterator[None]:
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        lock_path = self.path.with_suffix(f"{self.path.suffix}.lock")
        descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)


def _payload_hash(payload: dict[str, object]) -> str:
    encoded = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
