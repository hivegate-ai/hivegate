"""A client that hangs up mid-stream still gets its run metered, as `cancelled`.

Starlette cancels a disconnected StreamingResponse through an anyio cancel scope, and
inside a cancelled scope every await raises again - including the usage write in the
streamer's `finally`. These tests drive chat_response_streamer_v2 the two ways a stream
is torn down (cancel scope, and aclose) with a record stub that has to await before it
stores anything, the way the real thread-pool write does.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import anyio

from api.routes.v2.agents import ChatRequest
from api.services import usage

REQUEST = {
    "message": "where is my order?",
    "model": "claude-sonnet-5-5",
    "user_id": "u1",
    "session_id": "s1",
    "tenant_profile": {"tenant_id": "tenant-9", "name": "T", "website": ""},
    "timezone": "UTC",
    "locale": "en-US",
}

# One model request completes, a second one starts, some text streams - and then the
# provider is still generating when the client goes away.
EVENTS = [
    {"event": "RunStarted", "run_id": "r1"},
    {"event": "ModelRequestStarted", "run_id": "r1"},
    {"event": "ModelRequestCompleted", "run_id": "r1", "model": "claude-sonnet-5-5", "input_tokens": 40},
    {"event": "ModelRequestStarted", "run_id": "r1"},
    {"event": "RunContent", "run_id": "r1", "content": "Your order"},
]


class Event:
    def __init__(self, data):
        self.data = data

    def to_dict(self):
        return dict(self.data)


def _agent():
    def arun(*a, **k):
        async def gen():
            for e in EVENTS:
                yield Event(e)
            await asyncio.sleep(3600)  # the model is still generating
            yield Event({"event": "RunCompleted", "run_id": "r1"})

        return gen()

    agent = MagicMock()
    agent.id = "support-agent"
    agent.arun = arun
    agent.model = SimpleNamespace(id="claude-sonnet-5-5", provider="Anthropic")
    return agent


class Recorder:
    """Stands in for usage.record: awaits first, like the thread-pool write."""

    def __init__(self):
        self.recorded = []

    async def __call__(self, collector):
        await asyncio.sleep(0.01)
        self.recorded.append(collector)


def _streamer():
    from api.routes.v2.agents import chat_response_streamer_v2

    return chat_response_streamer_v2(_agent(), ChatRequest(**REQUEST), db=None)


async def _drain_background():
    for _ in range(50):
        if not usage._background:
            return
        await asyncio.sleep(0.01)


def _assert_cancelled_with_partial_counts(recorder):
    assert len(recorder.recorded) == 1, "the run was not recorded"
    c = recorder.recorded[0]
    assert c.status == "cancelled"
    assert "client disconnected" in c.error and "1 model request(s) in flight" in c.error
    assert c.request_tokens["input_tokens"] == 40  # the completed request is still counted
    row = c.build_row()
    assert row["status"] == "cancelled" and row["prompt_tokens"] == 40


def test_a_disconnect_through_the_cancel_scope_is_still_recorded_as_cancelled():
    recorder = Recorder()

    async def scenario():
        with patch("api.routes.v2.agents.record_usage", recorder):
            with anyio.CancelScope() as scope:
                seen = 0
                async for _ in _streamer():
                    seen += 1
                    if seen == len(EVENTS):
                        scope.cancel()  # what Starlette does when the client goes away
            await _drain_background()

    asyncio.run(scenario())
    _assert_cancelled_with_partial_counts(recorder)


def test_a_closed_stream_is_still_recorded_as_cancelled():
    recorder = Recorder()

    async def scenario():
        with patch("api.routes.v2.agents.record_usage", recorder):
            stream = _streamer()
            for _ in EVENTS:
                await stream.__anext__()
            await stream.aclose()
            await _drain_background()

    asyncio.run(scenario())
    _assert_cancelled_with_partial_counts(recorder)


def test_without_the_shield_the_write_is_lost():
    """Pins why shielded() exists: a plain await inside a cancelled scope never finishes."""
    recorder = Recorder()

    async def scenario():
        with anyio.CancelScope() as scope:
            scope.cancel()
            await recorder(usage.UsageCollector(usage.UsageContext(agent_id="a")))
        with anyio.CancelScope() as scope:
            scope.cancel()
            await usage.shielded(recorder(usage.UsageCollector(usage.UsageContext(agent_id="b"))))
        await _drain_background()

    asyncio.run(scenario())
    assert [c.ctx.agent_id for c in recorder.recorded] == ["b"]
