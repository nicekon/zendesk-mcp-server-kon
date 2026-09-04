import asyncio


def main():
    from .server import main as run_server

    asyncio.run(run_server())


__all__ = ["main"]
