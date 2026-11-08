import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from agno.exceptions import ModelAuthenticationError, ModelProviderError
from agno.run.agent import RunOutput
from agno.run.base import RunStatus
from agno.run.team import TeamRunOutput
from fastapi.testclient import TestClient

from tests.test_utils import create_test_client

MODEL = "gemini-2.5-pro"
RAW_ERROR = '{"error":{"message":"API key not valid; secret-provider-token","code":400}}'
BODY = {"message": "Hi", "model": MODEL, "user_id": "u1", "session_id": "s1", "timezone": "UTC", "locale": "en"}
STREAM_ERROR_TYPES = [
    "model_provider_error",
    "model_authentication_error",
    "model_refusal_error",
    pytest.param("future_provider_error", id="unrecognized_type"),
    pytest.param(None, id="missing_type"),
]


class Event:
    def __init__(self, **data):
        self.data = data

    def to_dict(self):
        return dict(self.data)


class MCP:
    def __init__(self):
        self._initialized = False
        self.functions = {}
        self.exited = False

    async def __aenter__(self):
        self._initialized = True
        self.functions = {"lookup": object()}
        return self

    async def __aexit__(self, *exc):
        self.exited = True


class Runner:
    def __init__(self, result=None, events=(), exception=None):
        self.id = "provider-test"
        self.model = SimpleNamespace(id=MODEL, provider="Google")
        self.result = result
        self.events = events
        self.exception = exception
        self.closed = False

    def arun(self, *args, stream=False, **kwargs):
        async def complete():
            if self.exception:
                raise self.exception
            return self.result

        async def generate():
            try:
                for event in self.events:
                    yield Event(**event)
                if self.exception:
                    raise self.exception
            finally:
                self.closed = True

        return generate() if stream else complete()


@pytest.fixture(params=["agent", "mcp", "team"])
def route(request, monkeypatch):
    from api.routes.v2 import agents, teams
    from api.routes.v2_router import get_v2_router

    monkeypatch.setenv("AUTH_DISABLED", "true")
    agents._agent_cache.clear()
    agents._run_cache.clear()
    teams._team_run_cache.clear()
    _, app = create_test_client()
    app.include_router(get_v2_router())
    client = TestClient(app)
    toolkit = MCP()
    recorded = []

    async def record(collector):
        recorded.append(collector)

    monkeypatch.setattr(agents, "record_usage", record)
    monkeypatch.setattr(teams, "record_usage", record)
    monkeypatch.setattr(
        agents,
        "get_agent",
        AsyncMock(return_value=(None, SimpleNamespace(name="test", template="Help", description=""), "key", None)),
    )
    monkeypatch.setattr(agents, "build_mcp_toolkits", lambda config: [toolkit] if request.param == "mcp" else [])
    monkeypatch.setattr(teams, "get_team_info", lambda *args: SimpleNamespace(name="Test", mode="coordinate"))
    monkeypatch.setattr(teams, "get_team_agents", lambda *args: [SimpleNamespace(agent_id="member", order_index=1)])
    monkeypatch.setattr(teams, "get_agent", lambda **kwargs: object())
    monkeypatch.setattr(teams, "get_db_url", lambda: "postgresql://unused")

    def post(runner, stream=False, model=MODEL):
        monkeypatch.setattr(agents, "get_agent_impl", lambda **kwargs: runner)
        monkeypatch.setattr(teams, "create_team", lambda **kwargs: runner)
        path = "/v2/teams/test/runs" if request.param == "team" else "/v2/agents/test/chat"
        return client.post(path, json={**BODY, "stream": stream, "model": model})

    yield SimpleNamespace(
        kind=request.param,
        post=post,
        recorded=recorded,
        toolkit=toolkit,
        output=TeamRunOutput if request.param == "team" else RunOutput,
        prefix="Team" if request.param == "team" else "",
        cache=teams._team_run_cache if request.param == "team" else agents._run_cache,
    )
    client.close()
    agents._agent_cache.clear()
    agents._run_cache.clear()
    teams._team_run_cache.clear()


def assert_safe_error(response):
    assert response.status_code == 502, response.text
    body = response.json()
    assert body["status"] == "error"
    assert MODEL in body["detail"]
    assert "key" in body["detail"].lower()
    assert "secret-provider-token" not in response.text
    assert RAW_ERROR not in response.text


def assert_stream_error(response):
    assert response.status_code == 200, response.text
    frames = response.text.strip().split("\n\n")
    assert frames[-1].startswith("event: error\n")
    error = json.loads(frames[-1].split("data: ", 1)[1])
    assert error["status"] == "error"
    assert error["error"]
    assert MODEL in error["detail"]
    assert "key" in error["detail"].lower()
    assert error.get("content") != RAW_ERROR
    assert "secret-provider-token" not in response.text
    return error


def error_event(route, error_type="model_provider_error"):
    event = {
        "event": route.prefix + "RunError",
        "run_id": "r1",
        "content": RAW_ERROR,
    }
    if error_type is not None:
        event["error_type"] = error_type
    return event


def test_failed_run_output_returns_non_2xx_and_still_records_usage(route):
    result = route.output(run_id="failed", status=RunStatus.error, content=RAW_ERROR)
    response = route.post(Runner(result=result))
    assert_safe_error(response)
    assert len(route.recorded) == 1
    assert route.recorded[0].status == "error"
    assert "failed" not in route.cache
    if route.kind == "mcp":
        assert route.toolkit.exited


@pytest.mark.parametrize(
    ("error_class", "status_code"),
    [(ModelProviderError, code) for code in (400, 401, 429, 503)] + [(ModelAuthenticationError, 401)],
)
def test_thrown_provider_error_returns_safe_gateway_error(route, error_class, status_code):
    response = route.post(Runner(exception=error_class(RAW_ERROR, status_code=status_code)))
    assert_safe_error(response)
    assert len(route.recorded) == 1
    assert "secret-provider-token" in route.recorded[0].error  # details stay server-side


@pytest.mark.parametrize("with_prelude", [False, True])
@pytest.mark.parametrize("error_type", STREAM_ERROR_TYPES)
def test_stream_failure_before_output_sends_terminal_error_event(route, with_prelude, error_type):
    events = []
    if with_prelude:
        events = [
            {"event": route.prefix + "RunStarted", "run_id": "r1"},
            {"event": route.prefix + "ModelRequestStarted", "run_id": "r1"},
        ]
    runner = Runner(events=[*events, error_event(route, error_type)])
    response = route.post(runner, stream=True)
    assert_stream_error(response)
    assert len(route.recorded) == 1
    assert route.recorded[0].status == "error"
    assert runner.closed
    if route.kind == "mcp":
        assert route.toolkit.exited


@pytest.mark.parametrize("error_class", [ModelProviderError, ModelAuthenticationError])
def test_thrown_provider_error_before_stream_output_sends_error_event(route, error_class):
    runner = Runner(
        events=[{"event": route.prefix + "RunStarted", "run_id": "r1"}],
        exception=error_class(RAW_ERROR, status_code=401),
    )
    assert_stream_error(route.post(runner, stream=True))
    assert len(route.recorded) == 1
    assert runner.closed


@pytest.mark.parametrize("error_type", STREAM_ERROR_TYPES)
def test_stream_failure_after_content_sends_terminal_error_event(route, error_type):
    runner = Runner(
        events=[
            {"event": route.prefix + "RunStarted", "run_id": "r1"},
            {"event": route.prefix + "RunContent", "run_id": "r1", "content": "Partial answer"},
            error_event(route, error_type),
            {"event": route.prefix + "RunContent", "run_id": "r1", "content": "Must not arrive after failure"},
            {"event": route.prefix + "RunCompleted", "run_id": "r1"},
        ]
    )
    response = route.post(runner, stream=True)
    assert response.status_code == 200
    assert "Partial answer" in response.text
    assert "secret-provider-token" not in response.text
    assert "Must not arrive after failure" not in response.text
    frames = response.text.strip().split("\n\n")
    assert frames[-1].startswith("event: error\n")
    error = json.loads(frames[-1].split("data: ", 1)[1])
    assert error["status"] == "error"
    assert error["error"]
    assert error["detail"]
    assert len(route.recorded) == 1
    assert route.recorded[0].status == "error"
    assert runner.closed


@pytest.mark.parametrize("status", [RunStatus.completed, RunStatus.paused])
def test_successful_and_paused_results_keep_success_response(route, status):
    # A successful answer may legitimately contain provider-like JSON.
    response = route.post(Runner(result=route.output(run_id="ok", status=status, content=RAW_ERROR)))
    assert response.status_code == 200, response.text
    assert response.json()["content"] == RAW_ERROR
    assert response.json()["status"] == status.value
    assert len(route.recorded) == 1


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize(
    ("message", "code", "hint"),
    [
        ("credit balance is too low; secret-provider-token", "provider_billing", "credits"),
        (
            "Claude declined (stop_reason=refusal, category=cyber); secret-provider-token",
            "model_refusal_error",
            "refusal",
        ),
    ],
)
def test_billing_and_refusal_errors_keep_actionable_safe_messages(route, stream, message, code, hint):
    event = {**error_event(route), "content": message}
    runner = Runner(result=route.output(run_id="r1", status=RunStatus.error, content=message), events=[event])
    response = route.post(runner, stream=stream)
    assert response.status_code == (200 if stream else 502), response.text
    body = json.loads(response.text.strip().split("\n\n")[-1].split("data: ", 1)[1]) if stream else response.json()
    assert body["status"] == "error"
    assert body["error"]
    assert body["error_code"] == code
    assert hint in body["detail"]
    assert "secret-provider-token" not in response.text
    assert len(route.recorded) == 1


@pytest.mark.parametrize("route", ["team"], indirect=True)
@pytest.mark.parametrize("error_type", STREAM_ERROR_TYPES)
def test_team_member_error_does_not_fail_successful_parent(route, error_type):
    runner = Runner(
        events=[
            {"event": "TeamRunStarted", "run_id": "parent"},
            {
                **error_event(route, error_type),
                "event": "RunError",
                "parent_run_id": "parent",
                "run_id": "child",
            },
            {"event": "TeamRunContent", "run_id": "parent", "content": "Recovered answer"},
            {"event": "TeamRunCompleted", "run_id": "parent"},
        ]
    )
    response = route.post(runner, stream=True)
    assert response.status_code == 200
    assert "Recovered answer" in response.text
    assert "secret-provider-token" not in response.text
    assert "event: error\n" not in response.text
    assert len(route.recorded) == 1
    assert route.recorded[0].status == "completed"


def test_streaming_and_non_streaming_failures_have_the_same_detail(route):
    result = route.output(run_id="failed", status=RunStatus.error, content=RAW_ERROR)
    ordinary = route.post(Runner(result=result))
    streamed = route.post(Runner(events=[error_event(route)]), stream=True)
    assert_safe_error(ordinary)
    error = assert_stream_error(streamed)
    assert error["detail"] == ordinary.json()["detail"]
    assert len(route.recorded) == 2


@pytest.mark.parametrize("route", ["team"], indirect=True)
@pytest.mark.parametrize("thrown", [False, True])
def test_team_alias_has_the_same_error_detail_as_the_resolved_stream_model(route, thrown):
    resolved_model = "gemini-3.8-flash"
    result = route.output(run_id="failed", status=RunStatus.error, content=RAW_ERROR)
    failure = ModelProviderError(RAW_ERROR, status_code=400) if thrown else None
    ordinary_runner = Runner(result=result, exception=failure)
    streamed_runner = Runner(events=[] if thrown else [error_event(route)], exception=failure)
    ordinary_runner.model.id = streamed_runner.model.id = resolved_model
    ordinary = route.post(ordinary_runner, model="google:flash-latest")
    streamed = route.post(streamed_runner, stream=True, model="google:flash-latest")
    assert ordinary.status_code == 502
    assert streamed.status_code == 200
    error_frame = streamed.text.strip().split("\n\n")[-1]
    assert error_frame.startswith("event: error\n")
    error = json.loads(error_frame.split("data: ", 1)[1])
    assert ordinary.json()["detail"] == error["detail"]
    assert resolved_model in error["detail"]
    assert "secret-provider-token" not in ordinary.text + streamed.text


def test_stream_passes_through_unfamiliar_and_successful_events(route):
    events = [
        {"event": "FutureAgnoEvent", "run_id": "r1", "metadata": {"new_field": [1, 2]}},
        {"event": route.prefix + "RunContent", "run_id": "r1", "content": RAW_ERROR},
        {"event": route.prefix + "RunCompleted", "run_id": "r1"},
    ]
    response = route.post(Runner(events=events), stream=True)
    assert response.status_code == 200
    frames = response.text.strip().split("\n\n")
    assert all(frame.startswith("event: message\n") for frame in frames)
    assert [json.loads(frame.split("data: ", 1)[1]) for frame in frames] == events
    assert len(route.recorded) == 1
    assert route.recorded[0].status == "completed"
