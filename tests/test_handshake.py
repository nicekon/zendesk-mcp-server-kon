import asyncio
import sys

from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


def test_unconfigured_server_completes_an_mcp_handshake_without_network_access():
    async def handshake():
        parameters = StdioServerParameters(
            command=sys.executable,
            args=["-c", "from zendesk_mcp_server import main; main()"],
            env={},
        )
        async with stdio_client(parameters) as (read, write):
            async with ClientSession(read, write) as session:
                result = await session.initialize()
                return result.serverInfo.name

    assert asyncio.run(handshake()) == "Zendesk"
