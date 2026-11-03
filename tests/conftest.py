from pathlib import Path
import os

import pytest


@pytest.fixture(autouse=True)
def isolate_saved_connection(tmp_path, monkeypatch):
    import zendesk_mcp_server.client as client
    monkeypatch.setattr(client, "_COOLDOWNS", {})
    monkeypatch.setattr(Path, "home", lambda: tmp_path)


@pytest.fixture
def private_permissions():
    def apply(path):
        if os.name != "nt":
            path.chmod(0o600)
            return
        import win32api
        import win32con
        import win32file
        import win32security as ws
        token = ws.OpenProcessToken(win32api.GetCurrentProcess(), win32con.TOKEN_QUERY)
        try:
            user = ws.GetTokenInformation(token, ws.TokenUser)[0]
        finally:
            token.Close()
        acl = ws.ACL()
        acl.AddAccessAllowedAce(ws.ACL_REVISION, win32file.FILE_ALL_ACCESS, user)
        acl.AddAccessAllowedAce(ws.ACL_REVISION, win32file.FILE_ALL_ACCESS,
                               ws.CreateWellKnownSid(ws.WinLocalSystemSid))
        ws.SetNamedSecurityInfo(str(path), ws.SE_FILE_OBJECT,
                                ws.DACL_SECURITY_INFORMATION | ws.PROTECTED_DACL_SECURITY_INFORMATION,
                                None, None, acl, None)
    return apply


@pytest.fixture
def public_permissions():
    def apply(path):
        if os.name != "nt":
            path.chmod(0o644)
            return
        import win32con
        import win32security as ws
        descriptor = ws.GetNamedSecurityInfo(str(path), ws.SE_FILE_OBJECT, ws.DACL_SECURITY_INFORMATION)
        acl = descriptor.GetSecurityDescriptorDacl()
        acl.AddAccessAllowedAce(ws.ACL_REVISION, win32con.GENERIC_READ,
                               ws.CreateWellKnownSid(ws.WinWorldSid))
        ws.SetNamedSecurityInfo(str(path), ws.SE_FILE_OBJECT, ws.DACL_SECURITY_INFORMATION,
                                None, None, acl, None)
    return apply
