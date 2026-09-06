import asyncio
import json
import os
import sys


def main():
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
    if len(sys.argv) > 1:
        raise SystemExit("usage: zendesk [approve <approval_request_id>]")
    from .server import main as run_server

    asyncio.run(run_server())


__all__ = ["main"]
