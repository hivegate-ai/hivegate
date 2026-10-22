"""An out-of-credits provider error reaches the client as a plain message, not a JSON dump.

agno reports a model failure as an in-band RunError whose content is str(exception); for
Anthropic that is the SDK's "Error code: 400 - {...}" string, which the cash helper's chat
showed verbatim. The streamer replaces it, while token_usage keeps the provider's wording.
"""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from agents.provider_models import BILLING_ERROR_CODE, BILLING_ERROR_MESSAGE
from api.routes.v2.agents import ChatRequest
from tests.test_provider_models import ANTHROPIC_NO_CREDITS

REQUEST = {
    "message": "add 50 shekels for coffee",
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


def _agent(events):
    def arun(*a, **k):
        async def gen():
            for e in events:
                yield Event(e)

        return gen()

    agent = MagicMock()
    agent.id = "pf-cash-manager"
    agent.arun = arun
    agent.model = SimpleNamespace(id="claude-sonnet-5-5", provider="Anthropic")
    return agent


def _run(events):
    from api.routes.v2.agents import chat_response_streamer_v2

    recorded = []

    async def record(collector):
        recorded.append(collector)

    async def drain():
        stream = chat_response_streamer_v2(_agent(events), ChatRequest(**REQUEST), db=None)
        return [chunk async for chunk in stream]

    with patch("api.routes.v2.agents.record_usage", record):
        chunks = asyncio.run(drain())
    payloads = [json.loads(c.split("data: ", 1)[1]) for c in chunks]
    return payloads, recorded[0]


def test_billing_run_error_is_humanized_for_the_client():
    payloads, collector = _run(
        [
            {"event": "RunStarted", "run_id": "r1"},
            {
                "event": "RunError",
                "run_id": "r1",
                "content": ANTHROPIC_NO_CREDITS,
                "error_type": "model_provider_error",
            },
        ]
    )
    error = payloads[-1]
    assert error["content"] == BILLING_ERROR_MESSAGE
    assert error["error_code"] == BILLING_ERROR_CODE
    assert "Error code: 400" not in json.dumps(payloads)
    assert "credit balance is too low" in collector.error  # the detail stays server-side


def test_other_run_errors_pass_through():
    payloads, _ = _run([{"event": "RunError", "run_id": "r1", "content": "MCP tool call timed out"}])
    assert payloads[-1]["content"] == "MCP tool call timed out"
    assert "error_code" not in payloads[-1]
