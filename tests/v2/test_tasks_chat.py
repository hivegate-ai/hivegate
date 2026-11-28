import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import anthropic
import httpx
import pytest
import toolkits
from agno.db.in_memory import InMemoryDb
from agno.tools import Toolkit
from anthropic.types import ContentBlockDeltaEvent, ContentBlockStopEvent, MessageStopEvent, ToolUseBlock
from fastapi import FastAPI

from agents import agent as agent_factory
from agents.claude_refusal import RefusalAwareClaude
from agents.hitl import approved_targets, unavailable_targets
from api.routes.v2 import agents
from api.services.models import PullPromptResponse
from db.agent_info_crud import AgentConfig
from tests.test_hitl import _ScriptedClaude
from tests.workspace_suite.helpers import make_mock_client
from toolkits import tasks
from toolkits.calendar import CalendarToolkit
from toolkits.tasks import GOOGLE_TASKS, MICROSOFT_TODO
from toolkits.token_cache import TokenCache
from workspace_suite.providers.google_tasks import GoogleTasksProvider
from workspace_suite.providers.microsoft_todo import MicrosoftTodoProvider

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
    # A commit reads the token rows through its own session; here both lookups agree.
    monkeypatch.setattr(
        agents, "has_user_tokens_batch", lambda db, user, keys: agents.has_access_tokens_batch(user, keys)
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
@pytest.mark.parametrize("approve", [True, False])
@pytest.mark.parametrize("stream", [True, False])
async def test_a_failed_model_call_on_commit_answers_like_chat_and_drops_the_run(app, monkeypatch, approve, stream):
    provider_http, dispatched, _ = _serve_tasks_chat(
        monkeypatch, ToolUseBlock(type="tool_use", id="toolu_update", name="update_task", input=UPDATE)
    )
    request = {**OWNER, "stream": stream}

    with provider_http:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as gateway:
            response = await gateway.post(
                CHAT, json={**request, "message": "Update task", "timezone": "UTC", "locale": "en"}
            )
            assert response.status_code == 200, response.text
            [(run_id, cached)] = agents._run_cache.items()
            [tool] = cached.run.tools

            # The provider fails on the call that follows the decision.
            def fail(**kwargs):
                sent = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
                raise anthropic.InternalServerError(
                    "secret-provider-detail", response=httpx.Response(500, request=sent), body=None
                )

            model = agent_factory.create_model()
            model.get_async_client = lambda: SimpleNamespace(messages=SimpleNamespace(create=fail, stream=fail))
            decision = {"tool_call_id": tool.tool_call_id, "confirmed": approve}
            reply = await gateway.post(
                CHAT + "/commit", json={**request, "run_id": run_id, "updated_tools": [decision]}
            )

    if stream:
        assert reply.status_code == 200, reply.text
        frames = reply.text.strip().split("\n\n")
        assert frames[-1].startswith("event: error\n")
        error = json.loads(frames[-1].split("data: ", 1)[1])
    else:
        assert reply.status_code == 502, reply.text
        error = reply.json()
    assert error["error_code"] == "model_provider_error"
    assert "secret-provider-detail" not in reply.text
    # The run is dropped, and an approved call ran before the model failed.
    assert run_id not in agents._run_cache
    assert dispatched == ([PATCHED] if approve else [])


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [True, False])
@pytest.mark.parametrize("confirmed", [True, False])
@pytest.mark.parametrize("changed", ["api_key", "user_id", "session_id", "agent_id"])
async def test_a_commit_from_another_chat_is_refused_and_leaves_the_run(app, monkeypatch, changed, confirmed, stream):
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
            [(run_id, cached)] = agents._run_cache.items()
            [tool] = cached.run.tools
            decision = {"tool_call_id": tool.tool_call_id, "confirmed": confirmed}
            commit = {**request, "run_id": run_id, "updated_tools": [decision]}
            foreign = {
                **commit,
                "updated_tools": [{**decision, "tool_args": {"list_id": "other"}, "confirmation_note": "x"}],
            }

            path = CHAT
            if changed == "api_key":
                app.dependency_overrides[agents.get_api_key] = lambda: SimpleNamespace(id=2)
            elif changed == "agent_id":
                path = CHAT.replace("tasks-agent", "other-agent")
            else:
                foreign[changed] = "mallory"
            refused = await gateway.post(path + "/commit", json=foreign)

            assert refused.status_code == 403, refused.text
            assert agents._run_cache == {run_id: cached}
            assert (tool.confirmed, tool.confirmation_note, tool.tool_args) == (None, None, UPDATE)
            assert dispatched == []
            assert len(model_requests) == 1

            # The chat that started the run can still decide it.
            app.dependency_overrides[agents.get_api_key] = lambda: SimpleNamespace(id=1)
            reply = await gateway.post(CHAT + "/commit", json=commit)
            assert reply.status_code == 200, reply.text

    assert dispatched == ([PATCHED] if confirmed else [])


@asynccontextmanager
async def _paused_update(app, monkeypatch, connected, stream):
    """A chat paused on update_task, with Google and Microsoft both mocked.

    `connected` is the set of connected integration keys, which a test can change. Yields the
    gateway client, the chat request, the paused run id and the writes that reached each provider.
    """
    provider_http, google, _ = _serve_tasks_chat(
        monkeypatch, ToolUseBlock(type="tool_use", id="toolu_update", name="update_task", input=UPDATE)
    )
    microsoft = []

    def todo_request(request):
        microsoft.append((request.method, request.url.path))
        return httpx.Response(200, json={"id": "task", "title": "null", "status": "notStarted"})

    todo_http = make_mock_client(todo_request)
    monkeypatch.setitem(
        tasks._TASKS_PROVIDERS, MICROSOFT_TODO, lambda config: MicrosoftTodoProvider(config, http=todo_http)
    )
    monkeypatch.setattr(agents, "has_access_tokens_batch", lambda user, keys: {key: key in connected for key in keys})
    message = {**OWNER, "stream": stream, "message": "Update task", "timezone": "UTC", "locale": "en"}

    with provider_http, todo_http:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as gateway:
            response = await gateway.post(CHAT, json=message)
            assert response.status_code == 200, response.text
            [run_id] = agents._run_cache
            assert google == microsoft == []
            yield gateway, message, run_id, {GOOGLE_TASKS: google, MICROSOFT_TODO: microsoft}


def _decision(run_id, stream, confirmed=True):
    return {
        **OWNER,
        "stream": stream,
        "run_id": run_id,
        "updated_tools": [{"tool_call_id": "toolu_update", "confirmed": confirmed}],
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [True, False])
async def test_an_approved_call_reaches_the_provider_it_was_planned_for(app, monkeypatch, stream):
    connected = {MICROSOFT_TODO}
    async with _paused_update(app, monkeypatch, connected, stream) as (gateway, message, run_id, writes):
        assert [tool.toolkit for tool in agents._run_cache[run_id].run.tools] == [MICROSOFT_TODO]

        # Google is now first choice, and another message moves the shared agent to it.
        connected.add(GOOGLE_TASKS)
        assert (await gateway.post(CHAT, json=message)).status_code == 200
        [shared] = agents._agent_cache.values()
        tools = list(shared.tools)
        assert [t.service_name for t in tools if isinstance(t, tasks.TasksToolkit)] == [GOOGLE_TASKS]

        reply = await gateway.post(CHAT + "/commit", json=_decision(run_id, stream))
        assert reply.status_code == 200, reply.text

        assert len(writes[MICROSOFT_TODO]) == 1
        assert writes[GOOGLE_TASKS] == []
        # The chat itself stays on Google.
        assert list(agents._agent_cache.values()) == [shared]
        assert shared.tools == tools


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [True, False])
@pytest.mark.parametrize("another_message", [True, False])
@pytest.mark.parametrize("now_connected", [[], [MICROSOFT_TODO]])
async def test_an_approval_for_a_disconnected_provider_runs_nothing(
    app, monkeypatch, now_connected, another_message, stream
):
    connected = {GOOGLE_TASKS}
    async with _paused_update(app, monkeypatch, connected, stream) as (gateway, message, run_id, writes):
        # A cached token outlives its connection.
        toolkits.token_cache.get_token(GOOGLE_TASKS, "alice", lambda: "synthetic-chat-token")
        connected.clear()
        connected.update(now_connected)
        if another_message:
            assert (await gateway.post(CHAT, json=message)).status_code == 200

        refused = await gateway.post(CHAT + "/commit", json=_decision(run_id, stream))
        assert refused.status_code == 409, refused.text
        assert "send your request again" in refused.json()["detail"]

        # The run is released: reconnecting doesn't revive the approval.
        connected.add(GOOGLE_TASKS)
        again = await gateway.post(CHAT + "/commit", json=_decision(run_id, stream))
        assert again.status_code == 410, again.text
        assert writes == {GOOGLE_TASKS: [], MICROSOFT_TODO: []}


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [True, False])
async def test_a_decline_needs_no_connection(app, monkeypatch, stream):
    connected = {GOOGLE_TASKS}
    async with _paused_update(app, monkeypatch, connected, stream) as (gateway, _, run_id, writes):
        connected.clear()
        reply = await gateway.post(CHAT + "/commit", json=_decision(run_id, stream, confirmed=False))
        assert reply.status_code == 200, reply.text
        assert writes == {GOOGLE_TASKS: [], MICROSOFT_TODO: []}


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [True, False])
async def test_a_failed_connection_lookup_keeps_the_run(app, monkeypatch, stream):
    async with _paused_update(app, monkeypatch, {GOOGLE_TASKS}, stream) as (gateway, _, run_id, writes):
        with monkeypatch.context() as outage:
            outage.setattr(agents, "has_user_tokens_batch", Mock(side_effect=RuntimeError("database down")))
            failed = await gateway.post(CHAT + "/commit", json=_decision(run_id, stream))
        assert failed.status_code == 500, failed.text
        assert writes[GOOGLE_TASKS] == []

        reply = await gateway.post(CHAT + "/commit", json=_decision(run_id, stream))
        assert reply.status_code == 200, reply.text
        assert writes[GOOGLE_TASKS] == [PATCHED]


def test_only_approved_calls_need_their_target():
    calls = [
        SimpleNamespace(toolkit="approved", confirmed=True, result=None),
        SimpleNamespace(toolkit="already run", confirmed=True, result="done"),
        SimpleNamespace(toolkit="declined", confirmed=False, result=None),
        SimpleNamespace(toolkit="undecided", confirmed=None, result=None),
        SimpleNamespace(toolkit=None, confirmed=True, result=None),  # a plain function
    ]
    assert approved_targets(SimpleNamespace(tools=calls)) == {"approved"}
    assert approved_targets(SimpleNamespace(tools=None)) == set()


def test_a_target_is_unavailable_when_disconnected_or_not_among_the_runs_tools():
    google = tasks.TasksToolkit("alice", GOOGLE_TASKS)
    prompt = tasks.TasksToolkit("alice", "tasks", auth=False)
    asked = []

    def connections(keys):
        asked.append(keys)
        return {GOOGLE_TASKS: True}

    tools = [google, Toolkit(name="plain"), prompt, print]
    assert unavailable_targets({GOOGLE_TASKS, "plain", "tasks"}, tools, connections) == set()
    # Only toolkits with an account behind them are looked up.
    assert asked == [[GOOGLE_TASKS]]
    assert unavailable_targets({GOOGLE_TASKS, MICROSOFT_TODO}, tools, connections) == {MICROSOFT_TODO}
    assert unavailable_targets({GOOGLE_TASKS}, tools, lambda keys: {}) == {GOOGLE_TASKS}
    # Tools that aren't a list can't be checked, so an approved call is refused.
    assert unavailable_targets({GOOGLE_TASKS}, lambda: tools, connections) == {GOOGLE_TASKS}
    assert unavailable_targets(set(), lambda: tools, connections) == set()


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
