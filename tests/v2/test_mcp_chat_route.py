"""Integration tests for native MCP tools on the v2 chat path.

Covers the connect -> ensure-ready -> attach(extra_tools) -> run flow, the structured
error contracts on connect failure (streaming SSE error event / non-streaming 503), the
empty-toolset guard, and that non-MCP agents bypass the MCP path entirely.
"""

import json
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
        import os

        os.environ["SKIP_DB_TABLE_CHECK"] = "true"
        os.environ["PROMPT_STORAGE_BACKEND"] = "service"

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

    def _agent_with_mcp_servers(self, servers: list) -> dict:
        config_json = json.dumps({"worker_config": {"mcp_servers": servers}})
        data = dict(self.agent_data)
        data["config"] = config_json
        return data

    def _run(self, toolkits, impl_capture=None, stream=False, agent_data=None):
        envp, storep, promptsp, infop = _patches()
        with envp, storep, promptsp as mock_prompts, infop as mock_info:
            mock_info.return_value = AgentInfoDB(**(agent_data or self.agent_data))
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

    def test_stdio_transport_rejection(self):
        agent_data = self._agent_with_mcp_servers([{"name": "git", "type": "stdio", "command": "npx"}])
        resp = self._run([], agent_data=agent_data, stream=False)
        self.assertEqual(resp.status_code, 400)
        detail = resp.json()["detail"]
        self.assertIn("git", detail)
        self.assertIn("stdio", detail)
        self.assertIn("'http', 'streamable-http'", detail)

    def test_sse_transport_rejection(self):
        agent_data = self._agent_with_mcp_servers([{"name": "legacy", "type": "sse", "url": "https://example.com/sse"}])
        resp = self._run([], agent_data=agent_data, stream=False)
        self.assertEqual(resp.status_code, 400)
        detail = resp.json()["detail"]
        self.assertIn("legacy", detail)
        self.assertIn("sse", detail)
        self.assertIn("'http', 'streamable-http'", detail)

    def test_unsupported_arbitrary_transport_rejection(self):
        agent_data = self._agent_with_mcp_servers([{"name": "custom", "type": "grpc", "url": "localhost:50051"}])
        resp = self._run([], agent_data=agent_data, stream=False)
        self.assertEqual(resp.status_code, 400)
        detail = resp.json()["detail"]
        self.assertIn("custom", detail)
        self.assertIn("grpc", detail)
        self.assertIn("'http', 'streamable-http'", detail)

    def test_missing_http_url_rejection(self):
        agent_data = self._agent_with_mcp_servers([{"name": "missing-url", "type": "http"}])
        resp = self._run([], agent_data=agent_data, stream=False)
        self.assertEqual(resp.status_code, 400)
        detail = resp.json()["detail"]
        self.assertIn("missing-url", detail)
        self.assertIn("http", detail)
        self.assertIn("'http', 'streamable-http'", detail)
        self.assertIn("http:// or https://", detail)

    def test_empty_http_url_rejection(self):
        agent_data = self._agent_with_mcp_servers([{"name": "empty-url", "type": "http", "url": "   "}])
        resp = self._run([], agent_data=agent_data, stream=False)
        self.assertEqual(resp.status_code, 400)
        detail = resp.json()["detail"]
        self.assertIn("empty-url", detail)
        self.assertIn("http", detail)
        self.assertIn("'http', 'streamable-http'", detail)
        self.assertIn("http:// or https://", detail)

    def test_invalid_url_format_rejection(self):
        agent_data = self._agent_with_mcp_servers([{"name": "bad-format", "type": "http", "url": "not-a-valid-url"}])
        resp = self._run([], agent_data=agent_data, stream=False)
        self.assertEqual(resp.status_code, 400)
        detail = resp.json()["detail"]
        self.assertIn("bad-format", detail)
        self.assertIn("not-a-valid-url", detail)
        self.assertIn("http:// or https://", detail)

    def test_unsupported_url_scheme_rejection(self):
        agent_data = self._agent_with_mcp_servers(
            [{"name": "ftp-server", "type": "http", "url": "ftp://example.com/mcp"}]
        )
        resp = self._run([], agent_data=agent_data, stream=False)
        self.assertEqual(resp.status_code, 400)
        detail = resp.json()["detail"]
        self.assertIn("ftp-server", detail)
        self.assertIn("ftp://example.com/mcp", detail)
        self.assertIn("http:// or https://", detail)

    def test_mixed_http_and_stdio_configuration_rejection(self):
        agent_data = self._agent_with_mcp_servers(
            [
                {"name": "github", "type": "http", "url": "https://api.github.com/mcp"},
                {"name": "git", "type": "stdio", "command": "npx"},
            ]
        )
        resp = self._run([], agent_data=agent_data, stream=False)
        self.assertEqual(resp.status_code, 400)
        detail = resp.json()["detail"]
        self.assertIn("git", detail)
        self.assertIn("stdio", detail)
        self.assertIn("'http', 'streamable-http'", detail)

    def test_streaming_request_rejected_before_sse_starts(self):
        agent_data = self._agent_with_mcp_servers([{"name": "git", "type": "stdio", "command": "npx"}])
        resp = self._run([], agent_data=agent_data, stream=True)
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.headers.get("content-type"), "application/json")
        detail = resp.json()["detail"]
        self.assertIn("git", detail)
        self.assertIn("stdio", detail)
        self.assertIn("'http', 'streamable-http'", detail)

    def test_non_mcp_agent_with_empty_servers_continues_working(self):
        cap = {}
        agent_data = self._agent_with_mcp_servers([])
        resp = self._run([], impl_capture=cap, stream=False, agent_data=agent_data)
        self.assertEqual(resp.status_code, 200)
        self.assertIn(cap["extra_tools"], (None, "ABSENT"))

    def test_valid_http_url(self):
        fake = _FakeMCP()
        cap = {}
        agent_data = self._agent_with_mcp_servers(
            [{"name": "local-mcp", "type": "http", "url": "http://localhost:8000/mcp"}]
        )
        resp = self._run([fake], impl_capture=cap, stream=False, agent_data=agent_data)
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(fake.entered)
        self.assertTrue(fake.exited)
        self.assertEqual(cap["extra_tools"], [fake])

    def test_valid_https_url(self):
        fake = _FakeMCP()
        cap = {}
        agent_data = self._agent_with_mcp_servers(
            [{"name": "cloud-mcp", "type": "http", "url": "https://api.example.com/mcp"}]
        )
        resp = self._run([fake], impl_capture=cap, stream=False, agent_data=agent_data)
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(fake.entered)
        self.assertTrue(fake.exited)
        self.assertEqual(cap["extra_tools"], [fake])

    def test_valid_http_mcp_configuration_continues_working(self):
        fake = _FakeMCP()
        cap = {}
        agent_data = self._agent_with_mcp_servers([{"name": "pf", "type": "http", "url": "https://example.com/mcp"}])
        resp = self._run([fake], impl_capture=cap, stream=False, agent_data=agent_data)
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(fake.entered)
        self.assertTrue(fake.exited)
        self.assertEqual(cap["extra_tools"], [fake])

    def test_valid_streamable_http_mcp_configuration_continues_working(self):
        fake = _FakeMCP()
        cap = {}
        agent_data = self._agent_with_mcp_servers(
            [{"name": "pf", "type": "streamable-http", "url": "https://example.com/mcp"}]
        )
        resp = self._run([fake], impl_capture=cap, stream=False, agent_data=agent_data)
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(fake.entered)
        self.assertTrue(fake.exited)
        self.assertEqual(cap["extra_tools"], [fake])

    def test_validate_mcp_servers_for_chat_unit(self):
        from fastapi import HTTPException

        from api.routes.v2.agents import validate_mcp_servers_for_chat
        from supervisor.models import MCPServerConfig, WorkerConfig

        # None config
        validate_mcp_servers_for_chat(None)

        # WorkerConfig None or empty
        mock_cfg = MagicMock()
        mock_cfg.worker_config = None
        validate_mcp_servers_for_chat(mock_cfg)

        mock_cfg.worker_config = MagicMock(mcp_servers=[])
        validate_mcp_servers_for_chat(mock_cfg)

        # Valid HTTP dict
        mock_cfg.worker_config = MagicMock(
            mcp_servers=[{"name": "dict-http", "type": "http", "url": "http://127.0.0.1:8000/mcp"}]
        )
        validate_mcp_servers_for_chat(mock_cfg)

        # Valid HTTPS dict
        mock_cfg.worker_config = MagicMock(
            mcp_servers=[{"name": "dict-https", "type": "streamable-http", "url": "https://example.com/mcp"}]
        )
        validate_mcp_servers_for_chat(mock_cfg)

        # Valid MCPServerConfig objects
        wc_valid = WorkerConfig(
            mcp_servers=[
                MCPServerConfig(name="obj-http", type="http", url="http://localhost:8000"),
                MCPServerConfig(name="obj-https", type="streamable-http", url="https://api.github.com/mcp"),
            ]
        )
        validate_mcp_servers_for_chat(MagicMock(worker_config=wc_valid))

        # Unsupported transport raises HTTPException 400 (dict)
        mock_cfg.worker_config = MagicMock(mcp_servers=[{"name": "test-stdio", "type": "stdio"}])
        with self.assertRaises(HTTPException) as cm:
            validate_mcp_servers_for_chat(mock_cfg)
        self.assertEqual(cm.exception.status_code, 400)
        self.assertIn("stdio", cm.exception.detail)
        self.assertIn("test-stdio", cm.exception.detail)
        self.assertIn("'http', 'streamable-http'", cm.exception.detail)

        # Unsupported transport raises HTTPException 400 (MCPServerConfig object)
        wc_stdio = WorkerConfig(mcp_servers=[MCPServerConfig(name="obj-stdio", type="stdio", command="npx")])
        with self.assertRaises(HTTPException) as cm:
            validate_mcp_servers_for_chat(MagicMock(worker_config=wc_stdio))
        self.assertEqual(cm.exception.status_code, 400)
        self.assertIn("stdio", cm.exception.detail)
        self.assertIn("obj-stdio", cm.exception.detail)

        # Unsupported URL scheme raises HTTPException 400 (dict)
        mock_cfg.worker_config = MagicMock(
            mcp_servers=[{"name": "dict-ftp", "type": "http", "url": "ftp://example.com"}]
        )
        with self.assertRaises(HTTPException) as cm:
            validate_mcp_servers_for_chat(mock_cfg)
        self.assertEqual(cm.exception.status_code, 400)
        self.assertIn("dict-ftp", cm.exception.detail)
        self.assertIn("ftp://example.com", cm.exception.detail)
        self.assertIn("http:// or https://", cm.exception.detail)

        # Unsupported URL scheme raises HTTPException 400 (MCPServerConfig object)
        wc_ftp = WorkerConfig(mcp_servers=[MCPServerConfig(name="obj-ftp", type="http", url="ftp://example.com")])
        with self.assertRaises(HTTPException) as cm:
            validate_mcp_servers_for_chat(MagicMock(worker_config=wc_ftp))
        self.assertEqual(cm.exception.status_code, 400)
        self.assertIn("obj-ftp", cm.exception.detail)
        self.assertIn("ftp://example.com", cm.exception.detail)
        self.assertIn("http:// or https://", cm.exception.detail)

        # Invalid URL format raises HTTPException 400 (dict)
        mock_cfg.worker_config = MagicMock(mcp_servers=[{"name": "dict-bad", "type": "http", "url": "invalid url"}])
        with self.assertRaises(HTTPException) as cm:
            validate_mcp_servers_for_chat(mock_cfg)
        self.assertEqual(cm.exception.status_code, 400)
        self.assertIn("dict-bad", cm.exception.detail)
        self.assertIn("http:// or https://", cm.exception.detail)

        # Invalid URL format raises HTTPException 400 (MCPServerConfig object)
        wc_bad = WorkerConfig(mcp_servers=[MCPServerConfig(name="obj-bad", type="http", url="http://")])
        with self.assertRaises(HTTPException) as cm:
            validate_mcp_servers_for_chat(MagicMock(worker_config=wc_bad))
        self.assertEqual(cm.exception.status_code, 400)
        self.assertIn("obj-bad", cm.exception.detail)
        self.assertIn("http:// or https://", cm.exception.detail)

        # Missing or empty URL raises HTTPException 400 (MCPServerConfig object)
        wc_empty = WorkerConfig(mcp_servers=[MCPServerConfig(name="obj-empty", type="http", url=None)])
        with self.assertRaises(HTTPException) as cm:
            validate_mcp_servers_for_chat(MagicMock(worker_config=wc_empty))
        self.assertEqual(cm.exception.status_code, 400)
        self.assertIn("obj-empty", cm.exception.detail)
        self.assertIn("http:// or https://", cm.exception.detail)


if __name__ == "__main__":
    unittest.main()
