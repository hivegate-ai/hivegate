"""Every agent chat path meters the run exactly once, attributed to the URL's agent,
the caller's tenant and the model asked for - including runs that fail."""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from db.db_models import AgentInfoDB
from tests.test_utils import create_test_client

AGENT = {
    "id": "pf-cash-manager",
    "name": "Cash Manager",
    "description": "test",
    "prompt_service_id": "cash-1",
    "tags": "[]",
    "version": "1",
}
REQUEST = {
    "message": "how much cash did I spend?",
    "model": "claude-sonnet-5-5",
    "user_id": "u1",
    "session_id": "s1",
    "tenant_profile": {"tenant_id": "tenant-9", "name": "T", "website": ""},
    "timezone": "UTC",
    "locale": "en-US",
}


class Event:
    def __init__(self, data):
        self.data = data

    def to_dict(self):
        return dict(self.data)


@patch.dict("os.environ", {"PROMPT_STORAGE_BACKEND": "service"})
class UsageWiringTest(unittest.TestCase):
    def setUp(self):
        from fastapi.testclient import TestClient

        from api.routes.v2.agents import _agent_cache, _cache_lock, _run_cache
        from api.routes.v2_router import get_v2_router

        with _cache_lock:
            _agent_cache.clear()
            _run_cache.clear()
        _, self.app = create_test_client()
        self.app.include_router(get_v2_router())
        self.client = TestClient(self.app)
        patch("api.routes.v2.agents.get_agent_info", return_value=AgentInfoDB(**AGENT)).start()
        prompts = patch("api.routes.v2.agents.prompts_client").start()
        prompts.get_prompt.return_value = SimpleNamespace(
            template="You manage cash.", name="cash_manager", description="Cash"
        )
        self.record = patch("api.routes.v2.agents.record_usage", new_callable=AsyncMock).start()
        self.record.return_value = {"cost_usd": 0.0123, "status": "completed"}
        self.addCleanup(patch.stopall)

    def _agent(self, arun):
        agent = MagicMock()
        agent.arun = arun
        agent.model = SimpleNamespace(id="claude-sonnet-5-5", provider="Anthropic")
        patch("api.routes.v2.agents.get_agent_impl", return_value=agent).start()
        return agent

    def _collector(self):
        self.assertEqual(self.record.await_count, 1)
        return self.record.await_args.args[0]

    def test_non_streaming_records_once_and_returns_the_summary(self):
        async def arun(*a, **k):
            from agno.metrics import RunMetrics

            return SimpleNamespace(
                content="ok",
                status="completed",
                run_id="r1",
                tools=[],
                metrics=RunMetrics(input_tokens=10, output_tokens=2, total_tokens=12),
            )

        self._agent(arun)
        resp = self.client.post("/v2/agents/pf-cash-manager/chat", json={**REQUEST, "stream": False})
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["token_usage"]["cost_usd"], 0.0123)
        c = self._collector()
        self.assertEqual(
            (c.ctx.agent_id, c.ctx.tenant_id, c.ctx.request_model, c.run_id),
            ("pf-cash-manager", "tenant-9", "claude-sonnet-5-5", "r1"),
        )
        self.assertEqual(c.metrics["input_tokens"], 10)

    def test_a_failing_non_streaming_run_is_still_recorded(self):
        async def arun(*a, **k):
            raise RuntimeError("credit balance is too low")

        self._agent(arun)
        resp = self.client.post("/v2/agents/pf-cash-manager/chat", json={**REQUEST, "stream": False})
        self.assertEqual(resp.status_code, 500)
        c = self._collector()
        self.assertEqual(c.status, "error")
        self.assertIn("credit balance", c.error)

    def test_streaming_records_once_with_the_streamed_metrics(self):
        events = [
            {"event": "RunStarted", "run_id": "r2"},
            {"event": "ModelRequestCompleted", "run_id": "r2", "model": "claude-sonnet-5-5", "input_tokens": 40},
            {"event": "ToolCallStarted", "run_id": "r2"},
            {"event": "RunContent", "run_id": "r2", "content": "hi"},
            # no `content` on the completion - the case the old capture missed
            {"event": "RunCompleted", "run_id": "r2", "metrics": {"input_tokens": 40, "output_tokens": 4}},
        ]

        def arun(*a, **k):
            async def gen():
                for e in events:
                    yield Event(e)

            return gen()

        self._agent(arun)
        resp = self.client.post("/v2/agents/pf-cash-manager/chat", json={**REQUEST, "stream": True})
        self.assertEqual(resp.status_code, 200)
        _ = resp.text  # drain the stream
        c = self._collector()
        self.assertEqual((c.metrics["input_tokens"], c.model_requests, c.tool_calls, c.run_id), (40, 1, 1, "r2"))
        self.assertEqual(c.ctx.agent_id, "pf-cash-manager")

    def test_a_stream_that_dies_midway_is_recorded_as_an_error(self):
        def arun(*a, **k):
            async def gen():
                yield Event({"event": "ModelRequestCompleted", "model": "claude-sonnet-5-5", "input_tokens": 99})
                raise RuntimeError("upstream 400")

            return gen()

        self._agent(arun)
        resp = self.client.post("/v2/agents/pf-cash-manager/chat", json={**REQUEST, "stream": True})
        _ = resp.text
        c = self._collector()
        self.assertEqual((c.status, c.request_tokens["input_tokens"]), ("error", 99))


if __name__ == "__main__":
    unittest.main()
