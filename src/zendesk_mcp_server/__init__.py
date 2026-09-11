import asyncio
import argparse
import getpass
import json
import os
import sys
import time


def main():
    if len(sys.argv) == 1:
        from .server import main as run_server

        asyncio.run(run_server())
        return

    parser = argparse.ArgumentParser(prog="zendesk", description="Zendesk MCP server and authentication utility")
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser("check", help="show configuration status")
    check.add_argument("--probe", action="store_true", help="verify the current Zendesk user")
    approve = commands.add_parser("approve", help="approve one previewed write")
    approve.add_argument("approval_request_id")
    oauth_start = commands.add_parser("oauth-start", help="start the legacy manual OAuth flow")
    oauth_start.add_argument("redirect_uri")
    oauth_finish = commands.add_parser("oauth-finish", help="finish the legacy manual OAuth flow")
    oauth_finish.add_argument("redirect_uri")
    oauth_finish.add_argument("state")
    login_parser = commands.add_parser("login", help="connect through the browser without copying an authorization code")
    login_parser.add_argument("--subdomain", required=True)
    login_parser.add_argument("--client-id", required=True)
    login_parser.add_argument("--port", type=int, default=3000)
    args = parser.parse_args()

    if args.command == "check":
        from .server import build_connection_status

        print(json.dumps(build_connection_status(os.environ, probe=args.probe), ensure_ascii=False))
        return
    if args.command == "approve":
        from .approvals import ApprovalStore

        if not sys.stdin.isatty():
            raise SystemExit("approval requires an interactive terminal")
        store = ApprovalStore.from_environment(os.environ)
        print(json.dumps(store.preview(args.approval_request_id), ensure_ascii=False, indent=2))
        if input("Approve this exact request? [y/N] ").strip().lower() not in {"y", "yes"}:
            raise SystemExit("approval cancelled")
        print(store.approve(args.approval_request_id))
        return
    if args.command == "oauth-start":
        from .auth import create_settings_oauth_authorization_request, oauth_state_store
        from .config import Settings

        settings = Settings.load(os.environ)
        request = create_settings_oauth_authorization_request(settings, args.redirect_uri, oauth_state_store(settings), now=int(time.time()))
        print(json.dumps({"authorization_url": request["authorization_url"]}, ensure_ascii=False, indent=2))
        return
    if args.command == "oauth-finish":
        from .auth import exchange_settings_oauth_authorization_code
        from .config import Settings

        if not sys.stdin.isatty():
            raise SystemExit("OAuth completion requires an interactive terminal")
        exchange_settings_oauth_authorization_code(Settings.load(os.environ), getpass.getpass("Authorization code: "), args.state, args.redirect_uri)
        print("OAuth token stored.")
        return
    if args.command == "login":
        from .config import ConfigurationError, Settings, saved_connection_path
        from .login import login

        try:
            settings = Settings.load({
                "ZENDESK_SUBDOMAIN": args.subdomain,
                "ZENDESK_AUTH_MODE": "oauth",
                "ZENDESK_OAUTH_CLIENT_KIND": "public",
                "ZENDESK_OAUTH_CLIENT_ID": args.client_id,
                "ZENDESK_OAUTH_TOKEN_STORE": str(saved_connection_path()),
            })
            login(settings, port=args.port)
        except ConfigurationError as error:
            raise SystemExit(str(error)) from None
        print("Zendesk OAuth login completed.")


__all__ = ["main"]
