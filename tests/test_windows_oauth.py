"""Native NTFS integration tests: never use production Zendesk credentials."""
import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor, TimeoutError

import pytest

from zendesk_mcp_server.auth import OAuthTokenStore, OAuthTokens, refresh_and_store_oauth_tokens
from zendesk_mcp_server.config import ConfigurationError

pytestmark = pytest.mark.skipif(os.name != "nt", reason="native Windows ACL integration")


def security(path):
    import win32security as ws
    return ws.GetNamedSecurityInfo(str(path), ws.SE_FILE_OBJECT,
                                   ws.OWNER_SECURITY_INFORMATION | ws.DACL_SECURITY_INFORMATION)


def test_windows_token_roundtrip_creates_protected_user_only_acl(tmp_path):
    import win32api
    import win32con
    import win32security as ws
    path = tmp_path / "connection.json"
    store = OAuthTokenStore(path)
    store.save(OAuthTokens("access", "refresh", 1234))
    assert store.load() == OAuthTokens("access", "refresh", 1234)
    token = ws.OpenProcessToken(win32api.GetCurrentProcess(), win32con.TOKEN_QUERY)
    try:
        user = ws.GetTokenInformation(token, ws.TokenUser)[0]
    finally:
        token.Close()
    descriptor = security(path)
    assert descriptor.GetSecurityDescriptorOwner() == user
    assert descriptor.GetSecurityDescriptorControl()[0] & ws.SE_DACL_PROTECTED
    acl = descriptor.GetSecurityDescriptorDacl()
    assert acl is not None and acl.GetAceCount() > 0
    allowed = {ws.ConvertSidToStringSid(user), "S-1-5-18"}
    for i in range(acl.GetAceCount()):
        ace = acl.GetAce(i)
        assert ace[0][0] == ws.ACCESS_ALLOWED_ACE_TYPE
        assert ws.ConvertSidToStringSid(ace[2]) in allowed
    assert not list(tmp_path.glob("*.tmp"))


@pytest.mark.parametrize("kind", ["everyone", "null", "inheritable"])
def test_windows_rejects_exposed_token_without_repairing_it(tmp_path, kind):
    import win32con
    import win32security as ws
    path = tmp_path / "oauth.json"
    store = OAuthTokenStore(path)
    store.save(OAuthTokens("secret", "refresh", 1234))
    acl = security(path).GetSecurityDescriptorDacl()
    flags = ws.DACL_SECURITY_INFORMATION | ws.PROTECTED_DACL_SECURITY_INFORMATION
    if kind == "everyone":
        acl.AddAccessAllowedAce(ws.ACL_REVISION, win32con.GENERIC_READ,
                               ws.CreateWellKnownSid(ws.WinWorldSid))
    elif kind == "null":
        acl = None
    else:
        flags = ws.DACL_SECURITY_INFORMATION | ws.UNPROTECTED_DACL_SECURITY_INFORMATION
    ws.SetNamedSecurityInfo(str(path), ws.SE_FILE_OBJECT, flags, None, None, acl, None)
    before = path.read_bytes()
    for operation in (store.load, lambda: store.save(OAuthTokens("new", "new-refresh", 1234))):
        with pytest.raises(ConfigurationError) as error:
            operation()
        assert error.value.code == "unsafe_oauth_permissions"
    assert path.read_bytes() == before


def test_windows_rejects_hardlinked_token_and_lock_without_touching_target(tmp_path):
    path = tmp_path / "oauth.json"
    store = OAuthTokenStore(path)
    store.save(OAuthTokens("old", "refresh", 1234))
    alias = tmp_path / "alias.json"
    os.link(path, alias)
    with pytest.raises(ConfigurationError):
        store.load()
    lock = tmp_path / ".oauth.json.lock"
    os.link(path, lock)
    before = path.read_bytes()
    with pytest.raises(ConfigurationError):
        with store.refresh_lock():
            pytest.fail("unsafe lock was acquired")
    assert path.read_bytes() == before


def test_windows_concurrent_refresh_rotates_once_and_preserves_connection_metadata(tmp_path):
    path = tmp_path / "connection.json"
    store = OAuthTokenStore(path)
    store._save_values({"access_token": "old", "refresh_token": "refresh", "expires_at": 1,
                        "subdomain": "acme", "client_id": "public-client"})
    barrier = threading.Barrier(2)
    calls = []
    def request(payload):
        calls.append(payload)
        return {"access_token": "new", "refresh_token": "rotated", "expires_in": 300}
    def refresh():
        barrier.wait(timeout=5)
        return refresh_and_store_oauth_tokens(store, request, "public-client", "", now=100,
                                              expected_access_token="old")
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: refresh(), range(2)))
    assert all(result.access_token == "new" for result in results)
    assert len(calls) == 1
    assert store.load().refresh_token == "rotated"
    assert json.loads(path.read_text())["client_id"] == "public-client"


def test_windows_failed_write_preserves_old_tokens_and_removes_temporary(tmp_path, monkeypatch):
    import pywintypes
    import win32file
    path = tmp_path / "oauth.json"
    store = OAuthTokenStore(path)
    store.save(OAuthTokens("old", "old-refresh", 1234))
    before = path.read_bytes()
    def disk_full(*args):
        raise pywintypes.error(112, "WriteFile", "test disk full")
    monkeypatch.setattr(win32file, "WriteFile", disk_full)
    with pytest.raises(ConfigurationError):
        store.save(OAuthTokens("new", "new-refresh", 5678))
    assert path.read_bytes() == before
    assert not list(tmp_path.glob("*.tmp"))


def test_windows_lock_serializes_separate_processes(tmp_path):
    import subprocess
    import sys
    path = tmp_path / "oauth.json"
    code = (
        "import sys; from pathlib import Path; "
        "from zendesk_mcp_server.auth import OAuthTokenStore; "
        "print('started', flush=True)\n"
        "with OAuthTokenStore(Path(sys.argv[1])).refresh_lock():\n"
        " print('locked', flush=True)\n"
    )
    process = None
    try:
        with OAuthTokenStore(path).refresh_lock():
            process = subprocess.Popen([sys.executable, "-c", code, str(path)],
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            assert process.stdout.readline().strip() == "started"
            with pytest.raises(subprocess.TimeoutExpired):
                process.communicate(timeout=0.3)
        output, errors = process.communicate(timeout=5)
        assert process.returncode == 0, errors
        assert "locked" in output
    finally:
        if process is not None and process.poll() is None:
            process.kill()
            process.communicate(timeout=5)


def test_windows_reader_does_not_prevent_publishing_rotated_token(tmp_path, monkeypatch):
    from zendesk_mcp_server.windows_storage import read_json
    import win32file
    path = tmp_path / "oauth.json"
    store = OAuthTokenStore(path)
    store.save(OAuthTokens("old", "old-refresh", 1))
    reader_open = threading.Event()
    release_reader = threading.Event()
    rotated = threading.Event()
    original_read = win32file.ReadFile
    def held_read(*args):
        if threading.current_thread().name == "held-reader":
            reader_open.set()
            assert release_reader.wait(5)
        return original_read(*args)
    monkeypatch.setattr(win32file, "ReadFile", held_read)
    snapshots = []
    reader = threading.Thread(target=lambda: snapshots.append(read_json(path)), name="held-reader")
    def request(_):
        # Overlap a new reader with publication AFTER rotation has happened.
        reader.start()
        assert reader_open.wait(5)
        rotated.set()
        return {"access_token": "new", "refresh_token": "rotated", "expires_in": 300}
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(refresh_and_store_oauth_tokens, store, request,
                             "client", "", now=100, expected_access_token="old")
        try:
            assert rotated.wait(5)
            with pytest.raises(TimeoutError):
                future.result(timeout=0.2)
        finally:
            release_reader.set()
        assert future.result(timeout=5).refresh_token == "rotated"
        reader.join(timeout=5)
    assert snapshots[0]["access_token"] == "old"
    assert store.load().refresh_token == "rotated"


def test_windows_rejects_reparse_token_and_lock_without_following_target(tmp_path):
    target = tmp_path / "target.json"
    OAuthTokenStore(target).save(OAuthTokens("old", "refresh", 1234))
    link = tmp_path / "oauth.json"
    try:
        link.symlink_to(target)
    except OSError as error:
        if getattr(error, "winerror", None) == 1314:
            pytest.skip("Windows symlink privilege unavailable")
        raise
    before = target.read_bytes()
    store = OAuthTokenStore(link)
    with pytest.raises(ConfigurationError):
        store.load()
    with pytest.raises(ConfigurationError):
        store.save(OAuthTokens("new", "refresh", 9999))
    lock = tmp_path / ".other.json.lock"
    lock.symlink_to(target)
    with pytest.raises(ConfigurationError):
        with OAuthTokenStore(tmp_path / "other.json").refresh_lock():
            pytest.fail("reparse lock acquired")
    assert target.read_bytes() == before
