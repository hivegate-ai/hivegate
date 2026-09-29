"""The gateway can actually open an MCP connection, not just build the toolkit object.

agno 3 opens MCP connections through `fastmcp`, which it treats as an optional extra
(`agno[mcp]`) and imports lazily - at connect time, not when MCPTools is constructed.
Upgrading to agno 3 without it built and constructed fine, passed every other test, and
then failed every MCP connection in production with `fastmcp not installed`: both
gateway agents use MCP tools, and the classifier went dark until the image was rolled
back. So this test does the part that failed - it serves a real MCP server over
streamable HTTP and connects to it through build_mcp_toolkits, the gateway's own code
path, then lists and calls a tool.
"""

import asyncio
import socket
import subprocess
import sys
import textwrap
import time
import unittest
import urllib.request
from types import SimpleNamespace

SERVER = textwrap.dedent(
    """
    import sys
    from fastmcp import FastMCP

    mcp = FastMCP("gateway-smoke")

    @mcp.tool
    def add(a: int, b: int) -> int:
        '''Add two integers.'''
        return a + b

    mcp.run(transport="http", host="127.0.0.1", port=int(sys.argv[1]), path="/mcp/")
    """
)


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class LiveMcpConnectionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.port = _free_port()
        cls.server = subprocess.Popen(
            [sys.executable, "-c", SERVER, str(cls.port)], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE
        )
        deadline = time.time() + 30
        while time.time() < deadline:
            if cls.server.poll() is not None:
                raise RuntimeError(f"MCP test server exited: {cls.server.stderr.read().decode()[-500:]}")
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{cls.port}/mcp/", timeout=1)
                break
            except urllib.error.HTTPError:
                break  # the server is up; MCP endpoints reject a bare GET
            except OSError:
                time.sleep(0.2)
        else:
            raise RuntimeError("MCP test server did not start")

    @classmethod
    def tearDownClass(cls):
        cls.server.terminate()
        cls.server.wait(timeout=10)

    def test_connects_lists_and_calls_a_tool_through_the_gateway_path(self):
        from agents.agent import build_mcp_toolkits

        config = SimpleNamespace(
            worker_config=SimpleNamespace(
                mcp_servers=[
                    SimpleNamespace(
                        name="smoke",
                        type="http",
                        url=f"http://127.0.0.1:{self.port}/mcp/",
                        headers={},
                        command=None,
                        args=[],
                        env={},
                    )
                ]
            )
        )
        [toolkit] = build_mcp_toolkits(config)

        async def use():
            async with toolkit:
                names = set(toolkit.functions)
                result = await toolkit.functions["add"].entrypoint(a=2, b=3)
                return names, result

        names, result = asyncio.run(use())
        self.assertIn("add", names)
        self.assertIn("5", str(getattr(result, "content", result)))


if __name__ == "__main__":
    unittest.main()
