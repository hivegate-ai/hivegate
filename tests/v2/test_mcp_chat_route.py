"""Integration tests for native MCP tools on the v2 chat path.

Covers the connect -> ensure-ready -> attach(extra_tools) -> run flow, the structured
error contracts on connect failure (streaming SSE error event / non-streaming 503), the
empty-toolset guard, and that non-MCP agents bypass the MCP path entirely.
"""

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from db.db_models import AgentInfoDB
from tests.test_utils import create_test_client


class _FakeMCP:
    """Stand-in for an agno MCPTools: an async context manager that 'connects' on enter."""

    def __init__(self, fail_connect: bool = False, empty: bool = False):
        self._fail = fail_connect
        self._empty = empty
        self._initialized = False
        self.functions: dict = {}
        self.entered = False
        self.exited = False

    async def __aenter__(self):
        if self._fail:
            raise ConnectionError("mcp connect boom")
        self.entered = True
        self._initialized = True
        self.functions = {} if self._empty else {"get_rules": object(), "classify": object()}
        return self

    async def __aexit__(self, *exc):
        self.exited = True
        return False


def _patches():
    """Common patch stack for the chat route (prompt fetch + db lookup + token store)."""
    return (
        patch.dict("os.environ", {"PROMPT_STORAGE_BACKEND": "service"}),
        patch("api.routes.v2.agents.record_usage", new_callable=AsyncMock, return_value=None),
        patch("api.routes.v2.agents.prompts_client"),
        patch("api.routes.v2.agents.get_agent_info"),
    )


class TestMcpChatRoute(unittest.TestCase):
    def setUp(self):
        from api.routes.v2.agents import _agent_cache, _cache_lock, _run_cache

        with _cache_lock:
            _agent_cache.clear()
            _run_cache.clear()

        self.client, self.app = create_test_client()
        from api.routes.v2_router import get_v2_router

        self.app.include_router(get_v2_router())
        from fastapi.testclient import TestClient

        self.client = TestClient(self.app)
        self.agent_data = {
            "id": "mcp-agent",
            "name": "MCP Agent",
            "description": "d",
            "prompt_service_id": "p-123",
            "tags": '["v2"]',
            "version": "2.0",
        }

    def _mock_prompt(self):
        p = MagicMock()
        p.template = "You are a budget classifier."
        p.name = "mcp-agent"
        p.description = "d"
        return p

    def _mock_agent(self):
        agent = MagicMock()

        async def arun(*args, **kwargs):
            r = MagicMock()
            r.content = "ok"
            r.status = "completed"
            r.run_id = "r1"
            r.tools = []
            r.metrics = None
            return r

        agent.arun = arun
        return agent

    def _body(self, stream=False):
        return {
            "message": "backtest my rules",
            "stream": stream,
            "model": "gemini-2.5-pro",
            "user_id": "u1",
            "session_id": "s1",
            "user_profile": {"profile_id": "pf", "email": "", "full_name": "T", "role": "user", "tenant_id": "t1"},
            "timezone": "UTC",
            "locale": "en-US",
        }

    def _run(self, toolkits, impl_capture=None, stream=False):
        envp, storep, promptsp, infop = _patches()
        with envp, storep, promptsp as mock_prompts, infop as mock_info:
            mock_info.return_value = AgentInfoDB(**self.agent_data)
            mock_prompts.get_prompt.return_value = self._mock_prompt()

            def impl(**kwargs):
                if impl_capture is not None:
                    impl_capture["extra_tools"] = kwargs.get("extra_tools", "ABSENT")
                return self._mock_agent()

            with (
                patch("api.routes.v2.agents.build_mcp_toolkits", return_value=toolkits),
                patch("api.routes.v2.agents.get_agent_impl", side_effect=impl),
            ):
                return self.client.post("/v2/agents/mcp-agent/chat", json=self._body(stream=stream))

    def test_nonstream_connects_and_passes_extra_tools(self):
        fake = _FakeMCP()
        cap = {}
        resp = self._run([fake], impl_capture=cap, stream=False)
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(fake.entered, "MCP toolkit was connected")
        self.assertTrue(fake.exited, "MCP toolkit was disconnected after the run")
        self.assertEqual(cap["extra_tools"], [fake], "connected toolkit reached get_agent")

    def test_nonstream_connect_failure_returns_503(self):
        resp = self._run([_FakeMCP(fail_connect=True)], stream=False)
        self.assertEqual(resp.status_code, 503)

    def test_nonstream_empty_toolset_returns_503(self):
        # Connects but exposes no tools -> ensure_mcp_ready must fail loud.
        resp = self._run([_FakeMCP(empty=True)], stream=False)
        self.assertEqual(resp.status_code, 503)

    def test_stream_connect_failure_yields_sse_error(self):
        resp = self._run([_FakeMCP(fail_connect=True)], stream=True)
        self.assertEqual(resp.status_code, 200)  # SSE status already flushed
        self.assertIn("error", resp.text.lower())
        self.assertIn("unavailable", resp.text.lower())

    def test_non_mcp_agent_bypasses_mcp_path(self):
        cap = {}
        resp = self._run([], impl_capture=cap, stream=False)
        self.assertEqual(resp.status_code, 200)
        # The original (non-MCP) path builds the agent without extra_tools.
        self.assertIn(cap["extra_tools"], (None, "ABSENT"))


if __name__ == "__main__":
    unittest.main()
