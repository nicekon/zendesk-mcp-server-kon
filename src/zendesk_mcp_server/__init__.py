import asyncio
import os
import sys


def main():
    if len(sys.argv) == 3 and sys.argv[1] == "approve":
        from .approvals import ApprovalStore

        print(ApprovalStore.from_environment(os.environ).approve(sys.argv[2]))
        return
    if len(sys.argv) > 1:
        raise SystemExit("usage: zendesk [approve <approval_request_id>]")
    from .server import main as run_server

    asyncio.run(run_server())


__all__ = ["main"]
