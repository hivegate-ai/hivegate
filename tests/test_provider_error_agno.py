import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest
from agno.agent import Agent
from agno.exceptions import ModelProviderError
from agno.models.google import Gemini
from agno.models.response import ModelResponse
from agno.run.base import RunStatus
from agno.team import Team

from agents.provider_models import GuardedGemini

MODEL_ID = "gemini-3.8-flash"
PROVIDER_ERROR = '{"error":{"message":"API key not valid: synthetic-secret-key","code":400}}'


def _runner(kind, model, pre_hooks=None):
    if kind == "agent":
        return Agent(model=model, telemetry=False, stream_events=True, pre_hooks=pre_hooks)
    return Team(members=[], model=model, telemetry=False, stream_events=True, pre_hooks=pre_hooks)


@pytest.mark.parametrize("kind", ["agent", "team"])
@pytest.mark.parametrize("provider_status,expected_attempts", [(400, 1), (503, 3)])
def test_real_agno_failed_run_is_not_a_successful_reply(kind, provider_status, expected_attempts):
    """Use Agno's actual error swallowing and retry path, not a hand-built ERROR result."""
    from api.provider_errors import failed_run_response

    model = GuardedGemini(id=MODEL_ID, api_key="test-key", retries=2, delay_between_retries=0)
    calls = []
    provider_error = (
        PROVIDER_ERROR
        if provider_status == 400
        else '{"error":{"message":"Service unavailable: synthetic-secret-key","code":503}}'
    )

    async def invoke(self, **kwargs):
        calls.append(kwargs)
        raise ModelProviderError(
            message=provider_error,
            status_code=provider_status,
            model_name="Gemini",
            model_id=MODEL_ID,
        )

    with patch.object(Gemini, "ainvoke", invoke):
        result = asyncio.run(_runner(kind, model).arun("hello", stream=False))

    assert len(calls) == expected_attempts
    assert result.status == RunStatus.error
    assert "synthetic-secret-key" in result.content  # What an unchecked route would return.
    response = failed_run_response(result, model.id)
    assert response.status_code == 502
    payload = json.loads(response.body)
    assert payload["status"] == "error"
    assert MODEL_ID in payload["detail"]
    assert "synthetic-secret-key" not in response.body.decode()
    assert provider_error not in response.body.decode()


@pytest.mark.parametrize("kind", ["agent", "team"])
def test_real_agno_successful_json_error_content_is_still_an_answer(kind):
    """Error-looking text can be a legitimate answer; the run status decides failure."""
    from api.provider_errors import failed_run_response

    model = GuardedGemini(id=MODEL_ID, api_key="test-key")

    async def invoke(self, **kwargs):
        return ModelResponse(role="assistant", content=PROVIDER_ERROR)

    with patch.object(Gemini, "ainvoke", invoke):
        result = asyncio.run(_runner(kind, model).arun("show an example error response", stream=False))

    assert result.status == RunStatus.completed
    assert result.content == PROVIDER_ERROR
    assert failed_run_response(result, model.id) is None


@pytest.mark.parametrize("kind", ["agent", "team"])
@pytest.mark.parametrize("with_pre_hook", [False, True])
def test_real_agno_streamed_provider_error_uses_terminal_sse_error(kind, with_pre_hook):
    """An SDK failure becomes Agno events before reaching the gateway's ASGI response."""
    from fastapi.responses import StreamingResponse
    from api.routes.v2.agents import ChatRequest, chat_response_streamer_v2
    from api.routes.v2.teams import team_response_streamer

    model = GuardedGemini(id=MODEL_ID, api_key="test-key", retries=2, delay_between_retries=0)
    hooks_called = []

    async def pre_hook():
        assert messages[0]["type"] == "http.response.start"
        assert messages[0]["status"] == 200
        hooks_called.append("hook")

    runner = _runner(kind, model, pre_hooks=[pre_hook] if with_pre_hook else None)
    calls = []
    messages = []

    async def invoke_stream(self, **kwargs):
        assert messages[0]["type"] == "http.response.start"
        assert messages[0]["status"] == 200  # Headers are not held for the provider.
        calls.append(kwargs)
        raise ModelProviderError(message=PROVIDER_ERROR, status_code=400, model_name="Gemini", model_id=MODEL_ID)
        yield  # pragma: no cover - the provider stream fails before its first chunk.

    async def send(message):
        messages.append(message)

    if kind == "agent":
        request = ChatRequest(
            message="hello", model=MODEL_ID, user_id="u1", session_id="s1", timezone="UTC", locale="en"
        )
        stream = chat_response_streamer_v2(runner, request, db=None)
        usage_target = "api.routes.v2.agents.record_usage"
    else:
        stream = team_response_streamer(runner, "hello", db=None)
        usage_target = "api.routes.v2.teams.record_usage"
    response = StreamingResponse(stream, media_type="text/event-stream")

    with patch.object(Gemini, "ainvoke_stream", invoke_stream), patch(usage_target, new_callable=AsyncMock) as record:
        asyncio.run(response.stream_response(send))

    assert hooks_called == (["hook"] if with_pre_hook else [])
    assert len(calls) == 1
    assert messages[0]["type"] == "http.response.start"
    assert messages[0]["status"] == 200
    assert len([message for message in messages if message["type"] == "http.response.start"]) == 1
    frames = b"".join(message.get("body", b"") for message in messages).decode().strip().split("\n\n")
    assert frames[-1].startswith("event: error\n")
    payload = json.loads(frames[-1].split("data: ", 1)[1])
    assert payload["error"]
    assert payload.get("content") != PROVIDER_ERROR
    assert payload["status"] == "error"
    assert MODEL_ID in payload["detail"]
    assert "synthetic-secret-key" not in json.dumps(payload)
    assert record.await_count == 1
    collector = record.await_args.args[0]
    assert collector.status == "error"
    assert "synthetic-secret-key" in collector.error
