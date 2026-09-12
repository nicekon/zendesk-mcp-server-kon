import pytest
import json
import os

from zendesk_mcp_server.config import ConfigurationError
from zendesk_mcp_server.migration import convert_record, rollback_record


def legacy():
    return {"subdomain": "example", "client_id": "client", "client_secret": "secret", "oauth_token": "old-access", "refresh_token": "old-refresh", "expires_at": 2000}


def test_schema_roundtrip_preserves_current_rotated_credentials():
    original = legacy()
    current = convert_record(original, now=1000)
    assert current["access_token"] == "old-access" and "oauth_token" not in current
    assert current["client_secret"] == "secret"
    assert "client_kind" not in current and "scopes" not in current
    current.update(access_token="new-access", refresh_token="new-refresh", expires_at=3000)
    restored = rollback_record(current, now=1001)
    assert restored == {**original, "oauth_token": "new-access", "refresh_token": "new-refresh", "expires_at": 3000}
    assert original == legacy()
    assert current["access_token"] == "new-access"


@pytest.mark.parametrize("change", [{"expires_at": None}, {"expires_at": True}, {"expires_at": 999}, {"refresh_token": ""}, {"subdomain": "evil.test/path"}, {"client_id": ""}, {"access_token": "conflicting"}])
def test_legacy_conversion_rejects_unsafe_or_unconvertible_records(change):
    with pytest.raises(ConfigurationError) as caught:
        convert_record({**legacy(), **change}, now=1000)
    assert "old-access" not in str(caught.value) and "secret" not in str(caught.value)


def test_rollback_refuses_unknown_schema_and_expired_current_token():
    with pytest.raises(ConfigurationError):
        rollback_record({"access_token": "unknown"}, now=1000)
    current = convert_record(legacy(), now=1000)
    current["expires_at"] = 999
    with pytest.raises(ConfigurationError):
        rollback_record(current, now=1000)


def test_rollback_rejects_a_conflicting_managed_connection_schema():
    current = convert_record(legacy(), now=1000)
    current["schema_version"] = 99
    with pytest.raises(ConfigurationError):
        rollback_record(current, now=1000)


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "fifo"])
def test_migration_rejects_unsafe_lock_without_changing_source_permissions(tmp_path, kind):
    from zendesk_mcp_server.migration import migrate_oauth
    source = tmp_path / "legacy.json"
    source.write_text(json.dumps(legacy())); source.chmod(0o644)
    lock = tmp_path / ".legacy.json.lock"
    if kind == "symlink": lock.symlink_to(source)
    elif kind == "hardlink": os.link(source, lock)
    else: os.mkfifo(lock)
    target = tmp_path / "current.json"
    with pytest.raises(ConfigurationError):
        migrate_oauth(source, target, now=1000)
    assert source.stat().st_mode & 0o777 == 0o644
    assert not target.exists()


def test_file_migration_preserves_source_and_rolls_back_rotated_tokens(tmp_path):
    from zendesk_mcp_server.migration import migrate_oauth, rollback_oauth
    from zendesk_mcp_server.auth import OAuthTokenStore, OAuthTokens
    source = tmp_path / "legacy.json"
    source.write_text(json.dumps(legacy())); source.chmod(0o600)
    original = source.read_bytes()
    target = tmp_path / "current.json"
    result = migrate_oauth(source, target, now=1000)
    assert result["ok"] and "old-access" not in json.dumps(result)
    assert source.read_bytes() == original
    assert target.stat().st_mode & 0o777 == 0o600
    assert (tmp_path / "current.json.migration-backup.json").stat().st_mode & 0o777 == 0o600
    OAuthTokenStore(target).save(OAuthTokens("rotated-access", "rotated-refresh", 3000))
    restored = tmp_path / "restored.json"
    rollback_oauth(target, restored, now=1001)
    assert json.loads(restored.read_text())["oauth_token"] == "rotated-access"
    assert json.loads(restored.read_text())["refresh_token"] == "rotated-refresh"
    assert not (tmp_path / "current.json.migration-backup.json").exists()


@pytest.mark.parametrize("case", ["public_source", "source_symlink", "existing_target", "same_file", "target_symlink"])
def test_file_migration_rejects_unsafe_paths_without_overwrite(tmp_path, case):
    from zendesk_mcp_server.migration import migrate_oauth
    source = tmp_path / "legacy.json"
    source.write_text(json.dumps(legacy())); source.chmod(0o600)
    target = tmp_path / "current.json"
    if case == "public_source": source.chmod(0o644)
    elif case == "source_symlink":
        link = tmp_path / "link.json"; link.symlink_to(source); source = link
    elif case == "existing_target": target.write_text("preserve")
    elif case == "same_file": target = source
    elif case == "target_symlink": target.symlink_to(source)
    original = source.read_bytes()
    with pytest.raises(ConfigurationError): migrate_oauth(source, target, now=1000)
    assert source.read_bytes() == original
    if case == "existing_target": assert target.read_text() == "preserve"


def test_migration_cli_roundtrip_is_offline_and_redacted(tmp_path, monkeypatch, capsys):
    import sys
    from zendesk_mcp_server import main
    source = tmp_path / "legacy.json"
    source.write_text(json.dumps(legacy())); source.chmod(0o600)
    current = tmp_path / "current.json"
    restored = tmp_path / "restored.json"
    monkeypatch.setattr("time.time", lambda: 1000)
    for command, origin, destination in [("migrate-oauth", source, current), ("rollback-oauth", current, restored)]:
        monkeypatch.setattr(sys, "argv", ["zendesk", command, str(origin), str(destination)])
        main()
        output = capsys.readouterr().out
        assert json.loads(output)["ok"] is True
        assert all(secret not in output for secret in ("old-access", "old-refresh", "secret"))
    assert json.loads(restored.read_text()) == legacy()


def test_migration_cli_errors_are_redacted(tmp_path, monkeypatch, capsys):
    import sys
    from zendesk_mcp_server import main
    source = tmp_path / "bad.json"
    source.write_text('secret-invalid-json'); source.chmod(0o600)
    monkeypatch.setattr(sys, "argv", ["zendesk", "migrate-oauth", str(source), str(tmp_path / "out.json")])
    with pytest.raises(SystemExit) as caught: main()
    assert caught.value.code == 1
    output = capsys.readouterr().out
    assert json.loads(output)["ok"] is False and "secret-invalid-json" not in output


def test_interrupted_migration_can_resume_without_replacing_backup(tmp_path, monkeypatch):
    from zendesk_mcp_server import migration
    source = tmp_path / "legacy.json"
    source.write_text(json.dumps(legacy())); source.chmod(0o600)
    target = tmp_path / "current.json"
    publish = migration._publish_new
    def interrupted(path, value):
        if path == target: raise OSError("simulated interruption")
        publish(path, value)
    with monkeypatch.context() as patch:
        patch.setattr(migration, "_publish_new", interrupted)
        with pytest.raises(ConfigurationError): migration.migrate_oauth(source, target, now=1000)
    backup = tmp_path / "current.json.migration-backup.json"
    before = backup.read_bytes()
    assert not target.exists() and json.loads(source.read_text()) == legacy()
    assert migration.migrate_oauth(source, target, now=1001)["ok"]
    assert backup.read_bytes() == before


def test_rollback_reports_applied_when_backup_cleanup_fails(tmp_path, monkeypatch):
    from pathlib import Path
    from zendesk_mcp_server.migration import migrate_oauth, rollback_oauth
    source = tmp_path / "legacy.json"
    source.write_text(json.dumps(legacy())); source.chmod(0o600)
    target = tmp_path / "current.json"
    migrate_oauth(source, target, now=1000)
    backup = tmp_path / "current.json.migration-backup.json"
    unlink = Path.unlink
    def denied(path, *args, **kwargs):
        if path == backup: raise PermissionError("secret error")
        return unlink(path, *args, **kwargs)
    monkeypatch.setattr(Path, "unlink", denied)
    restored = tmp_path / "restored.json"
    result = rollback_oauth(target, restored, now=1001)
    assert result["ok"] and result["data"]["backup_cleanup_pending"] is True
    assert json.loads(restored.read_text()) == legacy() and backup.exists()
    assert "secret" not in json.dumps(result)


def test_expired_migration_backup_is_cleaned_on_token_load(tmp_path, monkeypatch):
    from zendesk_mcp_server.auth import OAuthTokenStore
    from zendesk_mcp_server.migration import migrate_oauth
    source = tmp_path / "legacy.json"
    source.write_text(json.dumps(legacy())); source.chmod(0o600)
    target = tmp_path / "current.json"
    migrate_oauth(source, target, now=1000)
    backup = tmp_path / "current.json.migration-backup.json"
    monkeypatch.setattr("time.time", lambda: 1000 + 7 * 86400 - 1)
    assert OAuthTokenStore(target).load().access_token == "old-access"
    assert backup.exists()
    monkeypatch.setattr("time.time", lambda: 1000 + 7 * 86400)
    assert OAuthTokenStore(target).load().access_token == "old-access"
    assert not backup.exists()
    assert json.loads(source.read_text()) == legacy()


@pytest.mark.parametrize("case", ["symlink", "foreign", "malformed", "future"])
def test_backup_cleanup_never_removes_unrecognized_files(tmp_path, monkeypatch, case):
    from zendesk_mcp_server.auth import OAuthTokenStore
    from zendesk_mcp_server.migration import migrate_oauth
    source = tmp_path / "legacy.json"
    source.write_text(json.dumps(legacy())); source.chmod(0o600)
    target = tmp_path / "current.json"
    migrate_oauth(source, target, now=1000)
    backup = tmp_path / "current.json.migration-backup.json"
    if case == "symlink":
        backup.unlink(); backup.symlink_to(source)
    elif case == "malformed": backup.write_text("invalid")
    else:
        record = json.loads(backup.read_text())
        record.update({"destination": "elsewhere"} if case == "foreign" else {"created_at": 999999999})
        backup.write_text(json.dumps(record))
    before = backup.read_bytes()
    monkeypatch.setattr("time.time", lambda: 1000 + 7 * 86400)
    assert OAuthTokenStore(target).load().access_token == "old-access"
    assert backup.read_bytes() == before
