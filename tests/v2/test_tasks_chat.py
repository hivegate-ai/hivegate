import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
import toolkits
from agno.db.in_memory import InMemoryDb
from anthropic.types import ContentBlockDeltaEvent, ContentBlockStopEvent, MessageStopEvent, ToolUseBlock
from fastapi import FastAPI

from agents import agent as agent_factory
from agents.claude_refusal import RefusalAwareClaude
from api.routes.v2 import agents
from api.services.models import PullPromptResponse
from db.agent_info_crud import AgentConfig
from tests.test_hitl import _ScriptedClaude
from tests.workspace_suite.helpers import make_mock_client
from toolkits import tasks
from toolkits.calendar import CalendarToolkit
from toolkits.tasks import GOOGLE_TASKS
from toolkits.token_cache import TokenCache
from workspace_suite.providers.google_tasks import GoogleTasksProvider

CHAT = "/v2/agents/tasks-agent/chat"
OWNER = {"user_id": "alice", "session_id": "chat-session"}


@pytest.fixture
def app():
    app = FastAPI()
    app.include_router(agents.v2_agents_router, prefix="/v2")
    app.dependency_overrides[agents.get_db] = lambda: None
    app.dependency_overrides[agents.get_api_key] = lambda: SimpleNamespace(id=1)
    return app


UPDATE = {"list_id": "list", "task_id": "task", "changes": {"notes": None, "title": "null"}}
PATCHED = ("PATCH", "/tasks/v1/lists/list/tasks/task", {"title": "null", "notes": None})


def _serve_tasks_chat(monkeypatch, *tool_calls):
    """Route cached native chat through real agno and TasksToolkit to a mocked Google Tasks API.

    The scripted model proposes `tool_calls` once. Returns the provider HTTP client, the
    (method, path, body) of every request that reached the provider, and the keyword arguments
    of every request that reached the model.
    """
    dispatched = []
    model_requests = []

    def task_request(request):
        assert request.headers["Authorization"] == "Bearer synthetic-chat-token"
        body = json.loads(request.content) if request.content else None
        dispatched.append((request.method, request.url.path, body))
        if request.method == "DELETE":
            return httpx.Response(204)
        return httpx.Response(200, json={"id": "task", "title": "null", "status": "needsAction"})

    provider_http = make_mock_client(task_request)
    monkeypatch.setitem(
        tasks._TASKS_PROVIDERS, GOOGLE_TASKS, lambda config: GoogleTasksProvider(config, http=provider_http)
    )
    monkeypatch.setattr(toolkits, "token_cache", TokenCache())
    scripted = _ScriptedClaude(*tool_calls)

    async def create(**kwargs):
        model_requests.append(kwargs)
        if not scripted.calls:
            tools = {item["name"]: item for item in kwargs["tools"]}
            schema = tools["update_task"]["input_schema"]
            assert schema["required"] == ["list_id", "task_id", "changes"]
            assert schema["properties"]["changes"]["properties"]["notes"]["anyOf"] == [
                {"type": "string"},
                {"type": "null"},
            ]
        return scripted.create(**kwargs)

    @asynccontextmanager
    async def stream_reply(**kwargs):
        message = await create(**kwargs)

        async def events():
            for index, block in enumerate(message.content):
                if block.type == "tool_use":
                    yield ContentBlockStopEvent(type="content_block_stop", index=index, content_block=block)
                else:
                    yield ContentBlockDeltaEvent(
                        type="content_block_delta", index=index, delta={"type": "text_delta", "text": block.text}
                    )
            yield MessageStopEvent(type="message_stop", message=message)

        yield events()

    model = RefusalAwareClaude(id="claude-sonnet-5-5", api_key="offline-test")
    model.get_async_client = lambda: SimpleNamespace(messages=SimpleNamespace(create=create, stream=stream_reply))
    monkeypatch.setenv("AGNO_TELEMETRY", "false")
    db = InMemoryDb()
    monkeypatch.setattr(agent_factory, "get_agent_db", lambda table: db)
    monkeypatch.setattr(agent_factory, "get_knowledge_service", lambda: SimpleNamespace(get_dynamic_kb=lambda: None))
    monkeypatch.setattr(agent_factory, "create_model", lambda **kwargs: model)
    monkeypatch.setattr(agent_factory.api_settings, "enable_guardrails", False)
    prompt = PullPromptResponse(name="tasks", template="Manage tasks.", tags=["available_tools:tasks"])
    config = AgentConfig(enable_memory=False, enable_history=False)
    monkeypatch.setattr(agents, "get_agent_info", lambda db, agent_id: SimpleNamespace(prompt_service_id="one"))
    monkeypatch.setattr(agents, "get_agent_config", lambda db_agent: config)
    monkeypatch.setattr(agents, "_get_agent_prompt", lambda db, agent_id, prompt_id: prompt)
    monkeypatch.setattr(agents, "fetch_access_token", lambda user, key: "synthetic-chat-token")
    monkeypatch.setattr(
        agents, "has_access_tokens_batch", lambda user, keys: {key: key == GOOGLE_TASKS for key in keys}
    )
    monkeypatch.setattr(agents, "record_usage", AsyncMock(return_value=None))
    monkeypatch.setattr(agents, "_agent_cache", {})
    monkeypatch.setattr(agents, "_run_cache", {})
    return provider_http, dispatched, model_requests


@pytest.mark.asyncio
@pytest.mark.parametrize("approve", [True, False])
@pytest.mark.parametrize("stream", [True, False])
async def test_chat_approval_with_real_agno_and_tasks_toolkit(app, monkeypatch, approve, stream):
    provider_http, dispatched, model_requests = _serve_tasks_chat(
        monkeypatch, ToolUseBlock(type="tool_use", id="toolu_update", name="update_task", input=UPDATE)
    )
    request = {**OWNER, "stream": stream}

    with provider_http:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as gateway:
            response = await gateway.post(
                CHAT, json={**request, "message": "Update task", "timezone": "UTC", "locale": "en"}
            )
            assert response.status_code == 200, response.text
            if stream:
                events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
                paused = next(event for event in events if event.get("event") == "RunPaused")
            else:
                paused = response.json()
                assert paused["status"].lower() == "paused"
            [tool] = paused["tools"]
            assert tool["tool_args"] == UPDATE
            assert dispatched == []

            reply = await gateway.post(
                CHAT + "/commit",
                json={
                    **request,
                    "run_id": paused["run_id"],
                    "updated_tools": [{**tool, "confirmed": approve, "confirmation_note": "Not this one"}],
                },
            )
            assert reply.status_code == 200, reply.text
            # A declined call resumes the run too: the model hears about it and replies.
            if stream:
                assert "RunCompleted" in reply.text
            else:
                assert reply.json()["status"] == "completed"
                assert reply.json()["content"] == "done"

    assert dispatched == ([PATCHED] if approve else [])
    [result] = [
        block
        for message in model_requests[-1]["messages"]
        if isinstance(message["content"], list)
        for block in message["content"]
        if block.get("type") == "tool_result"
    ]
    assert result["tool_use_id"] == "toolu_update"
    # The model is told why the call didn't run.
    assert (result["content"] == "Not this one") is (not approve)


@pytest.mark.asyncio
async def test_a_call_skipped_in_an_approved_batch_never_reaches_the_provider(app, monkeypatch):
    provider_http, dispatched, _ = _serve_tasks_chat(
        monkeypatch,
        ToolUseBlock(type="tool_use", id="toolu_update", name="update_task", input=UPDATE),
        ToolUseBlock(type="tool_use", id="toolu_delete", name="delete_task", input={"list_id": "list", "task_id": "x"}),
    )
    request = {**OWNER, "stream": False}

    with provider_http:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as gateway:
            response = await gateway.post(
                CHAT, json={**request, "message": "Tidy up", "timezone": "UTC", "locale": "en"}
            )
            assert response.status_code == 200, response.text
            paused = response.json()
            update, skipped = paused["tools"]
            assert (update["tool_name"], skipped["tool_name"]) == ("update_task", "delete_task")

            # A client that submits only decided calls leaves a skipped delete_task out of the batch.
            reply = await gateway.post(
                CHAT + "/commit",
                json={**request, "run_id": paused["run_id"], "updated_tools": [{**update, "confirmed": True}]},
            )
            assert reply.status_code == 200, reply.text

    assert dispatched == [PATCHED]


@pytest.mark.parametrize(
    ("tag", "factories", "tools", "expected"),
    [
        ("available_tools:Tasks", agents.CHAT_TOOLKIT_FACTORIES, None, [("alice", "tasks", False)]),
        ("available_tools:calendar", agents.CHAT_TOOLKIT_FACTORIES, None, []),
        # A chat request's `tools` can keep Tasks or narrow it away.
        ("available_tools:tasks,calendar", agents.CHAT_TOOLKIT_FACTORIES, ["tasks"], [("alice", "tasks", False)]),
        ("available_tools:tasks,calendar", agents.CHAT_TOOLKIT_FACTORIES, ["calendar"], []),
        ("available_tools:tasks", agents.CHAT_TOOLKIT_FACTORIES, [], []),
        # Entry points that pass no factories (MCP chat, teams, supervisor) keep their tools.
        ("available_tools:tasks", None, None, []),
    ],
)
def test_agent_offers_tasks_when_the_entry_point_and_prompt_enable_it(monkeypatch, tag, factories, tools, expected):
    monkeypatch.setattr(agent_factory, "get_agent_db", lambda table: InMemoryDb())
    monkeypatch.setattr(agent_factory, "get_knowledge_service", lambda: SimpleNamespace(get_dynamic_kb=lambda: None))
    monkeypatch.setattr(agent_factory, "create_model", lambda **kwargs: RefusalAwareClaude(id="claude-sonnet-5-5"))
    monkeypatch.setattr(agent_factory.api_settings, "enable_guardrails", False)
    monkeypatch.setattr(agents, "has_access_tokens_batch", lambda user, keys: dict.fromkeys(keys, False))
    agent = agent_factory.get_agent(
        prompt=PullPromptResponse(name="tasks", template="Manage tasks.", tags=[tag]),
        user_id="alice",
        session_id="chat-session",
        organizer_email="",
        tenant_id="tenant",
        fetch_token_func=lambda user, key: None,
        config=AgentConfig(enable_memory=False, enable_history=False),
        tools=tools,
        toolkit_factories=factories,
    )

    for hook in agent.pre_hooks or []:
        hook(agent, None)
    selected = [t for t in agent.tools or [] if isinstance(t, tasks.TasksToolkit)]
    assert [(t.user_id, t.service_name, t.auth) for t in selected] == expected


@pytest.mark.asyncio
async def test_direct_toolkit_route_refuses_tasks(app, monkeypatch):
    # get_agent is mocked, so this proves the 403 itself; native chat puts Tasks in the cached
    # agent's tools, which is the agent this route looks up.
    toolkit = tasks.TasksToolkit("alice", GOOGLE_TASKS, fetch_token_func=lambda user, key: "synthetic-token")
    delete = AsyncMock()
    monkeypatch.setattr(toolkit, "delete_task", delete)
    agent_lookup = AsyncMock(return_value=(SimpleNamespace(tools=[toolkit]), None, "", None))
    monkeypatch.setattr(agents, "get_agent", agent_lookup)
    monkeypatch.setattr(agents, "_toolkit_execution_cache", {})

    params = {
        "toolkit_name": "TasksToolkit",
        "method_name": "delete_task",
        "list_id": "list",
        "task_id": "task",
        "skip_confirmation": "true",
        **OWNER,
    }
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as gateway:
        run = await gateway.get("/v2/agents/tasks-agent/toolkit/run", params=params)

    assert run.status_code == 403
    assert run.json() == {"detail": "Use chat for this toolkit."}
    delete.assert_not_called()
    assert agents._toolkit_execution_cache == {}


class PlainToolkit:
    """A tool without allow_direct_invocation, like the Agno tools agents also carry."""

    def ping(self, **kwargs):
        return {"pong": kwargs}


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["plain", "calendar"])
async def test_direct_toolkit_route_still_runs_other_toolkits(app, monkeypatch, kind):
    if kind == "plain":
        toolkit, name, method = PlainToolkit(), "PlainToolkit", "ping"
    else:
        toolkit = CalendarToolkit(
            user_id="alice",
            organizer_email="alice@example.com",
            service_name="google_calendar",
            fetch_token_func=lambda user, key: "synthetic-token",
        )
        name, method = "CalendarToolkit", "list_events"
        monkeypatch.setattr(toolkit, method, lambda **kwargs: {"pong": kwargs})
    monkeypatch.setattr(agents, "get_agent", AsyncMock(return_value=(SimpleNamespace(tools=[toolkit]), None, "", None)))

    params = {"toolkit_name": name, "method_name": method, "skip_confirmation": "true", "limit": "2", **OWNER}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as gateway:
        run = await gateway.get("/v2/agents/tasks-agent/toolkit/run", params=params)

    assert run.status_code == 200, run.text
    assert run.json()["result"] == {"pong": {"limit": 2}}
