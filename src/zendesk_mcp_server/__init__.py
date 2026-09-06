import asyncio
import getpass
import json
import os
import sys
import time


def main():
    if len(sys.argv) == 2 and sys.argv[1] == "check":
        from .server import build_connection_status

        print(json.dumps(build_connection_status(os.environ), ensure_ascii=False))
        return
    if len(sys.argv) == 3 and sys.argv[1] == "approve":
        from .approvals import ApprovalStore

        if not sys.stdin.isatty():
            raise SystemExit("approval requires an interactive terminal")
        store = ApprovalStore.from_environment(os.environ)
        print(json.dumps(store.preview(sys.argv[2]), ensure_ascii=False, indent=2))
        if input("Approve this exact request? [y/N] ").strip().lower() not in {"y", "yes"}:
            raise SystemExit("approval cancelled")
        print(store.approve(sys.argv[2]))
        return
    if len(sys.argv) == 3 and sys.argv[1] == "oauth-start":
        from .auth import create_settings_oauth_authorization_request, oauth_state_store
        from .config import Settings

        settings = Settings.load(os.environ)
        request = create_settings_oauth_authorization_request(settings, sys.argv[2], oauth_state_store(settings), now=int(time.time()))
        print(json.dumps({"authorization_url": request["authorization_url"]}, ensure_ascii=False, indent=2))
        return
    if len(sys.argv) == 4 and sys.argv[1] == "oauth-finish":
        from .auth import exchange_settings_oauth_authorization_code
        from .config import Settings

        if not sys.stdin.isatty():
            raise SystemExit("OAuth completion requires an interactive terminal")
        exchange_settings_oauth_authorization_code(Settings.load(os.environ), getpass.getpass("Authorization code: "), sys.argv[3], sys.argv[2])
        print("OAuth token stored.")
        return
    if len(sys.argv) > 1:
        raise SystemExit("usage: zendesk [check | approve <approval_request_id> | oauth-start <redirect_uri> | oauth-finish <redirect_uri> <state>]")
    from .server import main as run_server

    asyncio.run(run_server())


__all__ = ["main"]
