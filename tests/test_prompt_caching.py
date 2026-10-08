"""Claude requests from the gateway carry Anthropic prompt-cache breakpoints.

Checked on the wire: a real anthropic client with a fake HTTP transport records the
JSON body agno sends, so this fails if agno or the SDK stop passing the markers.
"""

import json

import httpx2 as httpx
from anthropic import Anthropic
from agno.models.message import Message

from agents import Model
from agents.model_factory import create_model

REPLY = {
    "id": "msg_1",
    "type": "message",
    "role": "assistant",
    "model": "claude-sonnet-4-6",
    "content": [{"type": "text", "text": "ok"}],
    "stop_reason": "end_turn",
    "stop_sequence": None,
    "usage": {"input_tokens": 10, "output_tokens": 1, "cache_read_input_tokens": 900, "cache_creation_input_tokens": 0},
}
TOOL = {
    "type": "function",
    "function": {
        "name": "get_budget_tree",
        "description": "Read a budget",
        "parameters": {"type": "object", "properties": {"month": {"type": "string"}}, "required": ["month"]},
    },
}


def send(model, messages, tools=None):
    bodies = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json=REPLY)

    model.client = Anthropic(api_key="k", http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    response = model.invoke(messages=messages, assistant_message=Message(role="assistant"), tools=tools)
    return bodies[0], response


def test_system_prompt_and_conversation_are_cached():
    model = create_model(Model.claude_sonnet_4_6, anthropic_api_key="k")
    body, response = send(
        model,
        [
            Message(role="system", content="You are the Budget Architect. " * 50),
            Message(role="user", content="build my budget"),
        ],
        tools=[TOOL],
    )
    # tools + system prefix: the marker on the system block covers both
    assert body["system"][-1]["cache_control"] == {"type": "ephemeral"}
    # automatic caching of the growing conversation
    assert body["cache_control"] == {"type": "ephemeral"}
    assert body["tools"][0]["name"] == "get_budget_tree"
    assert response.response_usage.cache_read_tokens == 900


def test_models_do_not_share_the_cache_settings_dict():
    a = create_model(Model.claude_sonnet_4_6, anthropic_api_key="k")
    b = create_model(Model.claude_sonnet_4_6, anthropic_api_key="k")
    a.request_params["cache_control"]["ttl"] = "1h"
    assert "ttl" not in b.request_params["cache_control"]


def _sse(*events):
    return "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events).encode()


def test_streaming_requests_are_cached_too():
    """The gateway streams; agno uses the SDK's messages.stream helper for that."""
    import asyncio

    from anthropic import AsyncAnthropic

    start = {**REPLY, "content": [], "stop_reason": None}
    stream = _sse(
        {"type": "message_start", "message": start},
        {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "ok"}},
        {"type": "content_block_stop", "index": 0},
        {
            "type": "message_delta",
            "delta": {"stop_reason": "end_turn", "stop_sequence": None},
            "usage": {"output_tokens": 1},
        },
        {"type": "message_stop"},
    )
    bodies = []

    async def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(200, content=stream, headers={"content-type": "text/event-stream"})

    model = create_model(Model.claude_sonnet_4_6, anthropic_api_key="k")
    model.async_client = AsyncAnthropic(
        api_key="k", http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )

    async def run():
        messages = [
            Message(role="system", content="You are the Budget Architect. " * 50),
            Message(role="user", content="hi"),
        ]
        return [r async for r in model.ainvoke_stream(messages=messages, assistant_message=Message(role="assistant"))]

    asyncio.run(run())
    assert bodies[0]["stream"] is True
    assert bodies[0]["cache_control"] == {"type": "ephemeral"}
    assert bodies[0]["system"][-1]["cache_control"] == {"type": "ephemeral"}
