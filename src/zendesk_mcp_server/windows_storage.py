"""Private OAuth files on Windows, checked through the handle used for I/O.

New files have a protected user/LocalSystem DACL before any secret is written.
Existing files are validated, never silently repaired. Import only on Windows.
"""
from __future__ import annotations

import json
import secrets
from contextlib import contextmanager
from pathlib import Path

from .config import ConfigurationError


def require_support() -> None:
    try:
        import win32security  # noqa: F401
        import win32file  # noqa: F401
        import win32api  # noqa: F401
        import win32con  # noqa: F401
        import pywintypes  # noqa: F401
    except ImportError:
        raise ConfigurationError("unsupported", "Windows OAuth storage requires pywin32") from None


def _user_sid():
    import win32api
    import win32con
    import win32security as ws
    token = ws.OpenProcessToken(win32api.GetCurrentProcess(), win32con.TOKEN_QUERY)
    try:
        return ws.GetTokenInformation(token, ws.TokenUser)[0]
    finally:
        token.Close()


def _security_attributes():
    import pywintypes
    import win32file
    import win32security as ws
    user = _user_sid()
    acl = ws.ACL()
    for sid in (user, ws.CreateWellKnownSid(ws.WinLocalSystemSid)):
        acl.AddAccessAllowedAce(ws.ACL_REVISION, win32file.FILE_ALL_ACCESS, sid)
    descriptor = ws.SECURITY_DESCRIPTOR()
    descriptor.SetSecurityDescriptorOwner(user, False)
    descriptor.SetSecurityDescriptorDacl(True, acl, False)
    descriptor.SetSecurityDescriptorControl(ws.SE_DACL_PROTECTED, ws.SE_DACL_PROTECTED)
    attributes = pywintypes.SECURITY_ATTRIBUTES()
    attributes.SECURITY_DESCRIPTOR = descriptor
    attributes.bInheritHandle = False
    return attributes


def _validate(handle) -> None:
    import win32con
    import win32file
    import win32security as ws
    info = win32file.GetFileInformationByHandle(handle)
    if (win32file.GetFileType(handle) != win32file.FILE_TYPE_DISK
            or info[0] & (win32con.FILE_ATTRIBUTE_REPARSE_POINT | win32con.FILE_ATTRIBUTE_DIRECTORY)
            or info[7] != 1):
        raise ConfigurationError("unsafe_oauth_permissions", "OAuth storage must be a regular file with a single link")
    descriptor = ws.GetSecurityInfo(handle, ws.SE_FILE_OBJECT,
                                    ws.OWNER_SECURITY_INFORMATION | ws.DACL_SECURITY_INFORMATION)
    user = _user_sid()
    trusted = {ws.ConvertSidToStringSid(user), "S-1-5-18"}
    acl = descriptor.GetSecurityDescriptorDacl()
    if (ws.ConvertSidToStringSid(descriptor.GetSecurityDescriptorOwner()) not in trusted
            or not descriptor.GetSecurityDescriptorControl()[0] & ws.SE_DACL_PROTECTED
            or acl is None or acl.GetAceCount() == 0):
        raise ConfigurationError("unsafe_oauth_permissions", "OAuth file permissions must be user-only")
    for index in range(acl.GetAceCount()):
        ace = acl.GetAce(index)
        if (ace[0][0] != ws.ACCESS_ALLOWED_ACE_TYPE
                or ws.ConvertSidToStringSid(ace[2]) not in trusted):
            raise ConfigurationError("unsafe_oauth_permissions", "OAuth file permissions must be user-only")


@contextmanager
def _open(path: Path, *, create: bool = False, lock: bool = False):
    require_support()
    import pywintypes
    import win32con
    import win32file
    handle = None
    try:
        access = win32con.GENERIC_READ | win32con.READ_CONTROL
        if create or lock:
            access |= win32con.GENERIC_WRITE
        sharing = win32con.FILE_SHARE_READ
        if lock:
            # Lock file must not be replaced while any process is using it.
            sharing |= win32con.FILE_SHARE_WRITE
        disposition = win32con.CREATE_NEW if create else win32con.OPEN_EXISTING
        handle = win32file.CreateFile(str(path), access, sharing,
                                     _security_attributes() if create else None,
                                     disposition, win32file.FILE_FLAG_OPEN_REPARSE_POINT, None)
        _validate(handle)
        yield handle
    except pywintypes.error as error:
        if error.winerror in (2, 3):
            raise FileNotFoundError("OAuth file is missing") from None
        if error.winerror in (80, 183):
            raise FileExistsError("OAuth file already exists") from None
        raise ConfigurationError("unsafe_oauth_permissions", "Cannot securely access Windows OAuth storage") from None
    finally:
        if handle is not None:
            handle.Close()


def read_json(path: Path):
    import win32file
    with _io_lock(path), _open(path) as handle:
        size = win32file.GetFileSize(handle)
        if size > 1_000_000:
            raise ConfigurationError("invalid_oauth_tokens", "OAuth file is too large")
        data = win32file.ReadFile(handle, size)[1] if size else b""
        return json.loads(data.decode("utf-8"))


def validate_file(path: Path) -> None:
    with _io_lock(path), _open(path):
        pass


def write_json(path: Path, values: dict[str, object]) -> None:
    # Windows cannot replace a destination held by another reader. Serialize
    # short local I/O separately from the refresh lock, which also covers HTTP.
    with _io_lock(path):
        _write_json(path, values)


def _io_lock(path: Path):
    return locked_file(path.with_name(f".{path.name}.io.lock"))


def _write_json(path: Path, values: dict[str, object]) -> None:
    import win32file
    path.parent.mkdir(parents=True, exist_ok=True)
    # Check before replacing a previous connection; never repair an exposed file.
    try:
        with _open(path):
            pass
    except FileNotFoundError:
        pass
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(16)}.tmp")
    created = False
    try:
        with _open(temporary, create=True) as handle:
            created = True
            data = json.dumps(values, separators=(",", ":")).encode("utf-8")
            _, written = win32file.WriteFile(handle, data)
            if written != len(data):
                raise ConfigurationError("invalid_oauth_tokens", "OAuth file write was incomplete")
            win32file.FlushFileBuffers(handle)
        # Recheck the destination after writing the complete private replacement.
        try:
            with _open(path):
                pass
        except FileNotFoundError:
            pass
        temporary.replace(path)
    finally:
        if created:
            temporary.unlink(missing_ok=True)


@contextmanager
def locked_file(path: Path):
    """Create a private stable lock inode and hold a native cross-process lock."""
    import pywintypes
    import win32con
    import win32file
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with _open(path, create=True, lock=True):
            pass
    except FileExistsError:
        pass
    with _open(path, lock=True) as handle:
        overlapped = pywintypes.OVERLAPPED()
        # LockFileEx waits indefinitely, unlike msvcrt's bounded retry loop.
        win32file.LockFileEx(handle, win32con.LOCKFILE_EXCLUSIVE_LOCK, 1, 0, overlapped)
        try:
            yield
        finally:
            win32file.UnlockFileEx(handle, 1, 0, overlapped)
