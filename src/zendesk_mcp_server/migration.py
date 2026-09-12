"""Offline OAuth schema conversion; never refresh or restore backup tokens."""

import json
import os
import stat
import tempfile
from pathlib import Path

from .auth import OAuthTokenStore
from .config import ConfigurationError, _SUBDOMAIN_PATTERN, require_private_file_support


def _validate_record(value: object, token_key: str, now: int) -> dict:
    if (
        not isinstance(value, dict)
        or any(not isinstance(value.get(key), str) or not value[key].strip()
               for key in ("subdomain", "client_id", token_key, "refresh_token"))
        or not _SUBDOMAIN_PATTERN.fullmatch(value["subdomain"])
        or type(value.get("expires_at")) is not int
        or value["expires_at"] <= now + 60
        or ("client_secret" in value and not isinstance(value["client_secret"], str))
    ):
        raise ConfigurationError("reauthorization_required", "OAuth record cannot be safely converted; reauthorize instead")
    return dict(value)


def convert_record(value: object, *, now: int) -> dict:
    result = _validate_record(value, "oauth_token", now)
    if any(key in result for key in ("access_token", "schema_version", "_zendesk_migration")):
        raise ConfigurationError("invalid_oauth_tokens", "OAuth record has conflicting schema fields")
    result["access_token"] = result.pop("oauth_token")
    result["_zendesk_migration"] = {"source_format": "michaelrice-8313e117", "version": 1}
    return result


def rollback_record(value: object, *, now: int) -> dict:
    result = _validate_record(value, "access_token", now)
    if (result.get("_zendesk_migration") != {"source_format": "michaelrice-8313e117", "version": 1}
            or any(key in result for key in ("oauth_token", "schema_version"))):
        raise ConfigurationError("reauthorization_required", "OAuth schema is not a supported migration; reauthorize instead")
    result.pop("_zendesk_migration")
    result["oauth_token"] = result.pop("access_token")
    return result


def cleanup_expired_backup(token_path: Path, *, now: int) -> None:
    """Remove only this converter's expired sidecar, on the next token use."""
    token_path = token_path.expanduser().absolute()
    token_path = token_path.parent.resolve() / token_path.name
    backup = token_path.with_name(token_path.name + ".migration-backup.json")
    try:
        saved = _read_private(backup)
    except (OSError, ValueError, TypeError):
        return
    if (saved.get("format") == "zendesk-migration-backup-1"
            and saved.get("destination") == str(token_path)
            and type(saved.get("created_at")) is int
            and now - saved["created_at"] >= 7 * 86400):
        try:
            backup.unlink()
        except OSError:
            raise ConfigurationError("invalid_oauth_configuration", "Expired OAuth migration backup could not be removed") from None


def _read_private(path: Path) -> dict:
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or info.st_size > 1_000_000:
            raise ConfigurationError("unsafe_oauth_permissions", "OAuth source must be a small user-only regular file")
        value = json.load(stream)
    if not isinstance(value, dict):
        raise ConfigurationError("invalid_oauth_tokens", "OAuth source must contain a JSON object")
    return value


def _publish_new(path: Path, value: dict) -> None:
    # Atomic no-clobber publication; never replace a concurrently created destination.
    with tempfile.TemporaryDirectory(prefix=".oauth-migration-", dir=path.parent) as directory:
        temporary = Path(directory) / "record.json"
        OAuthTokenStore(temporary)._save_values(value)
        os.link(temporary, path)


def _file_paths(source: Path, destination: Path) -> tuple[Path, Path]:
    source = source.expanduser().absolute()
    destination = destination.expanduser().absolute()
    # Resolve parent aliases (including macOS /tmp), but reject final-component symlinks.
    source = source.parent.resolve(strict=True) / source.name
    destination = destination.parent.resolve(strict=True) / destination.name
    if source.is_symlink() or destination.exists() or destination.is_symlink() or source == destination:
        raise ConfigurationError("invalid_oauth_configuration", "Use a regular source and a new, distinct destination")
    return source, destination


def _convert_file(source: Path, destination: Path, *, now: int, rollback: bool) -> dict:
    require_private_file_support()
    cleanup_pending = False
    try:
        source, destination = _file_paths(source, destination)
        with OAuthTokenStore(source).refresh_lock():
            original = _read_private(source)
            converted = rollback_record(original, now=now) if rollback else convert_record(original, now=now)
            backup = (source if rollback else destination).with_name((source if rollback else destination).name + ".migration-backup.json")
            if not rollback:
                if backup.exists() or backup.is_symlink():
                    saved = _read_private(backup)
                    if (saved.get("format") != "zendesk-migration-backup-1"
                            or saved.get("destination") != str(destination)
                            or saved.get("original") != original
                            or type(saved.get("created_at")) is not int
                            or not 0 <= now - saved["created_at"] < 7 * 86400):
                        raise ConfigurationError("invalid_oauth_configuration", "Migration backup conflicts with source; no files overwritten")
                else:
                    _publish_new(backup, {"format": "zendesk-migration-backup-1", "destination": str(destination), "created_at": now, "original": original})
            _publish_new(destination, converted)
            if rollback and (backup.exists() or backup.is_symlink()):
                try:
                    saved = _read_private(backup)
                    if saved.get("format") != "zendesk-migration-backup-1" or saved.get("destination") != str(source):
                        raise ValueError("unrecognized backup")
                    backup.unlink()
                except (OSError, ValueError, TypeError):
                    cleanup_pending = True
        return {"ok": True, "data": {"operation": "rollback" if rollback else "migration", "credentials_printed": False, "backup_cleanup_pending": cleanup_pending}}
    except ConfigurationError:
        raise
    except (OSError, ValueError, TypeError):
        raise ConfigurationError("invalid_oauth_configuration", "OAuth file conversion failed; source was not overwritten") from None


def migrate_oauth(source: Path, destination: Path, *, now: int) -> dict:
    return _convert_file(source, destination, now=now, rollback=False)


def rollback_oauth(source: Path, destination: Path, *, now: int) -> dict:
    return _convert_file(source, destination, now=now, rollback=True)
