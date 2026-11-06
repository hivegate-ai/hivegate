import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock, call

import pytest
from agno.db.base import SessionType
from fastapi import HTTPException

from api.routes.v2 import agents as agents_routes
from api.services.models import PullPromptResponse
from tests.test_utils import create_test_client

AGENT_ID = "assistant-v2"
CLEAR_URL = f"/v2/agents/{AGENT_ID}/session/clear"


@pytest.fixture
def clear_api(monkeypatch):
    monkeypatch.setenv("AUTH_DISABLED", "true")
    client, app = create_test_client()
    app.include_router(agents_routes.v2_agents_router, prefix="/v2")

    storage = MagicMock(spec=["delete_session", "get_session", "get_sessions"])
    storage.delete_session.return_value = True
    storage.get_session.side_effect = lambda session_id, user_id=None, **kwargs: {
        "session_id": session_id,
        "agent_id": "Support Helper",
        "user_id": user_id,
    }
    storage.get_sessions.return_value = ([], 0)
    get_storage = MagicMock(return_value=storage)
    get_info = MagicMock(return_value=SimpleNamespace(id=AGENT_ID, prompt_service_id="support-prompt"))
    get_prompt = MagicMock(return_value=PullPromptResponse(name="Support Helper", template="Help the user."))
    monkeypatch.setattr(agents_routes, "get_agent_info", get_info)
    monkeypatch.setattr(agents_routes, "_get_agent_prompt", get_prompt)
    monkeypatch.setattr(agents_routes, "get_agent_db", get_storage)

    yield SimpleNamespace(
        client=client,
        storage=storage,
        get_storage=get_storage,
        get_info=get_info,
        get_prompt=get_prompt,
    )
    client.close()


def test_clear_session_uses_the_chat_prompt_table(clear_api):
    response = clear_api.client.post(CLEAR_URL, json={"session_id": "session-1"})

    assert response.status_code == 200
    assert response.json()["status"] == "success"
    clear_api.get_storage.assert_called_once_with("a_support_helper_agent")
    clear_api.storage.get_session.assert_called_once_with(
        session_id="session-1", user_id=None, deserialize=False, runs_limit=0
    )
    clear_api.storage.delete_session.assert_called_once_with(session_id="session-1", user_id=None)
    clear_api.storage.get_sessions.assert_not_called()


def test_clear_session_intersects_the_session_and_user(clear_api):
    response = clear_api.client.post(CLEAR_URL, json={"session_id": "session-1", "user_id": "owner-1"})

    assert response.status_code == 200
    clear_api.storage.get_session.assert_called_once_with(
        session_id="session-1", user_id="owner-1", deserialize=False, runs_limit=0
    )
    clear_api.storage.delete_session.assert_called_once_with(session_id="session-1", user_id="owner-1")


@pytest.mark.parametrize("user_id", [None, "different-owner"])
def test_missing_or_differently_owned_session_does_not_report_success(clear_api, user_id):
    clear_api.storage.delete_session.return_value = False
    request = {"session_id": "session-1"}
    if user_id is not None:
        request["user_id"] = user_id

    response = clear_api.client.post(CLEAR_URL, json=request)

    assert response.status_code == 404
    clear_api.storage.delete_session.assert_called_once_with(session_id="session-1", user_id=user_id)


@pytest.mark.parametrize("stored_agent_name", [None, "support-helper"])
@pytest.mark.parametrize("user_id", [None, "owner-1"])
def test_missing_or_colliding_agent_session_is_not_deleted(clear_api, stored_agent_name, user_id):
    clear_api.storage.get_session.side_effect = None
    clear_api.storage.get_session.return_value = (
        {"session_id": "session-1", "agent_id": stored_agent_name, "user_id": user_id}
        if stored_agent_name is not None
        else None
    )
    payload = {"session_id": "session-1"}
    if user_id is not None:
        payload["user_id"] = user_id

    response = clear_api.client.post(CLEAR_URL, json=payload)

    assert response.status_code == 404
    clear_api.storage.get_session.assert_called_once_with(
        session_id="session-1", user_id=user_id, deserialize=False, runs_limit=0
    )
    clear_api.storage.delete_session.assert_not_called()


def test_clear_user_sessions_filters_both_listing_and_deletion(clear_api):
    clear_api.storage.get_sessions.return_value = (
        [{"session_id": "session-1"}, {"session_id": "session-2"}],
        2,
    )

    response = clear_api.client.post(CLEAR_URL, json={"user_id": "owner-1"})

    assert response.status_code == 200
    assert response.json()["status"] == "success"
    clear_api.storage.get_sessions.assert_called_once_with(
        session_type=SessionType.AGENT,
        user_id="owner-1",
        component_id="Support Helper",
        deserialize=False,
        include_runs=False,
    )
    assert clear_api.storage.get_session.call_args_list == [
        call(session_id="session-1", user_id="owner-1", deserialize=False, runs_limit=0),
        call(session_id="session-2", user_id="owner-1", deserialize=False, runs_limit=0),
    ]
    assert clear_api.storage.delete_session.call_args_list == [
        call(session_id="session-1", user_id="owner-1"),
        call(session_id="session-2", user_id="owner-1"),
    ]


def test_user_with_no_sessions_does_not_report_success(clear_api):
    response = clear_api.client.post(CLEAR_URL, json={"user_id": "owner-1"})

    assert response.status_code == 404
    clear_api.storage.delete_session.assert_not_called()


def test_user_sessions_that_disappear_before_deletion_do_not_report_success(clear_api):
    clear_api.storage.get_sessions.return_value = ([{"session_id": "session-1"}], 1)
    clear_api.storage.delete_session.return_value = False

    response = clear_api.client.post(CLEAR_URL, json={"user_id": "owner-1"})

    assert response.status_code == 404


def test_user_sessions_that_disappear_or_move_to_a_colliding_agent_are_not_deleted(clear_api):
    clear_api.storage.get_sessions.return_value = (
        [{"session_id": "already-gone"}, {"session_id": "another-agent"}],
        2,
    )
    clear_api.storage.get_session.side_effect = [
        None,
        {"session_id": "another-agent", "agent_id": "support-helper", "user_id": "owner-1"},
    ]

    response = clear_api.client.post(CLEAR_URL, json={"user_id": "owner-1"})

    assert response.status_code == 404
    assert clear_api.storage.get_session.call_count == 2
    clear_api.storage.delete_session.assert_not_called()


def test_user_clear_skips_a_colliding_agent_and_deletes_only_the_selected_agent(clear_api):
    clear_api.storage.get_sessions.return_value = (
        [{"session_id": "another-agent"}, {"session_id": "matching-agent"}],
        2,
    )
    clear_api.storage.get_session.side_effect = [
        {"session_id": "another-agent", "agent_id": "support-helper", "user_id": "owner-1"},
        {"session_id": "matching-agent", "agent_id": "Support Helper", "user_id": "owner-1"},
    ]

    response = clear_api.client.post(CLEAR_URL, json={"user_id": "owner-1"})

    assert response.status_code == 200
    assert "1 session" in response.json()["message"]
    clear_api.storage.delete_session.assert_called_once_with(session_id="matching-agent", user_id="owner-1")


def test_user_clear_succeeds_if_a_remaining_session_is_actually_deleted(clear_api):
    clear_api.storage.get_sessions.return_value = (
        [{"session_id": "already-gone"}, {"session_id": "still-present"}],
        2,
    )
    clear_api.storage.delete_session.side_effect = [False, True]

    response = clear_api.client.post(CLEAR_URL, json={"user_id": "owner-1"})

    assert response.status_code == 200
    assert clear_api.storage.delete_session.call_count == 2


def test_partial_user_clear_reports_failure_and_the_completed_count(clear_api):
    clear_api.storage.get_sessions.return_value = (
        [{"session_id": "session-1"}, {"session_id": "session-2"}],
        2,
    )
    clear_api.storage.delete_session.side_effect = [True, RuntimeError("private-database-password")]

    response = clear_api.client.post(CLEAR_URL, json={"user_id": "owner-1"})

    assert response.status_code == 500
    detail = response.json()["detail"]
    assert "1" in detail
    assert "session" in detail.lower()
    assert "private-database-password" not in response.text
    assert clear_api.storage.delete_session.call_count == 2


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"session_id": None, "user_id": None},
        {"session_id": ""},
        {"user_id": " \t "},
        {"session_id": " \n ", "user_id": "owner-1"},
        {"session_id": "session-1", "user_id": ""},
    ],
)
def test_missing_or_blank_selectors_are_rejected_without_touching_storage(clear_api, payload):
    response = clear_api.client.post(CLEAR_URL, json=payload)

    assert response.status_code == 400
    clear_api.get_storage.assert_not_called()
    clear_api.storage.delete_session.assert_not_called()
    clear_api.storage.get_session.assert_not_called()
    clear_api.storage.get_sessions.assert_not_called()


def test_unknown_agent_does_not_resolve_a_prompt_or_touch_storage(clear_api):
    clear_api.get_info.return_value = None

    response = clear_api.client.post(CLEAR_URL, json={"session_id": "session-1"})

    assert response.status_code == 404
    clear_api.get_prompt.assert_not_called()
    clear_api.get_storage.assert_not_called()


def test_unavailable_prompt_does_not_touch_storage(clear_api):
    clear_api.get_prompt.side_effect = HTTPException(status_code=500, detail="Unable to load the agent prompt")

    response = clear_api.client.post(CLEAR_URL, json={"session_id": "session-1"})

    assert response.status_code == 500
    clear_api.get_storage.assert_not_called()


@pytest.mark.parametrize("operation", ["delete_session", "get_session", "get_sessions"])
def test_storage_failure_is_not_reported_as_success_or_exposed(clear_api, operation):
    getattr(clear_api.storage, operation).side_effect = RuntimeError("private-database-password")
    request = {"user_id": "owner-1"} if operation == "get_sessions" else {"session_id": "session-1"}

    response = clear_api.client.post(CLEAR_URL, json=request)

    assert response.status_code == 500
    assert "private-database-password" not in response.text


def test_unsupported_session_deletion_does_not_report_success(clear_api):
    clear_api.get_storage.return_value = object()

    response = clear_api.client.post(CLEAR_URL, json={"session_id": "session-1"})

    assert response.status_code == 500


@pytest.mark.parametrize("backend", ["postgres", "service"])
def test_clear_resolves_the_prompt_from_the_configured_backend(monkeypatch, backend):
    monkeypatch.setenv("AUTH_DISABLED", "true")
    monkeypatch.setenv("PROMPT_STORAGE_BACKEND", backend)
    client, app = create_test_client()
    app.include_router(agents_routes.v2_agents_router, prefix="/v2")
    monkeypatch.setattr(
        agents_routes,
        "get_agent_info",
        MagicMock(return_value=SimpleNamespace(id=AGENT_ID, prompt_service_id="support-prompt")),
    )
    prompt = PullPromptResponse(name="Support Helper", template="Help the user.")
    local_prompt = MagicMock(return_value=prompt)
    remote_prompt = MagicMock(return_value=prompt)
    monkeypatch.setattr(agents_routes, "_get_prompt_from_local_storage", local_prompt)
    monkeypatch.setattr(agents_routes.prompts_client, "get_prompt", remote_prompt)
    storage = MagicMock(spec=["delete_session", "get_session"])
    storage.delete_session.return_value = True
    storage.get_session.return_value = {
        "session_id": "session-1",
        "agent_id": "Support Helper",
        "user_id": None,
    }
    get_storage = MagicMock(return_value=storage)
    monkeypatch.setattr(agents_routes, "get_agent_db", get_storage)

    try:
        response = client.post(CLEAR_URL, json={"session_id": "session-1"})
    finally:
        client.close()

    assert response.status_code == 200
    get_storage.assert_called_once_with("a_support_helper_agent")
    if backend == "postgres":
        local_prompt.assert_called_once()
        remote_prompt.assert_not_called()
    else:
        remote_prompt.assert_called_once_with("support-prompt")
        local_prompt.assert_not_called()


def test_service_prompt_resolution_falls_back_to_local_storage(monkeypatch):
    monkeypatch.setenv("PROMPT_STORAGE_BACKEND", "service")
    remote_prompt = MagicMock(return_value=None)
    prompt = PullPromptResponse(name="Support Helper", template="Help the user.")
    local_prompt = MagicMock(return_value=prompt)
    monkeypatch.setattr(agents_routes.prompts_client, "get_prompt", remote_prompt)
    monkeypatch.setattr(agents_routes, "_get_prompt_from_local_storage", local_prompt)
    db = MagicMock()

    assert agents_routes._get_agent_prompt(db, AGENT_ID, "support-prompt") == prompt
    remote_prompt.assert_called_once_with("support-prompt")
    local_prompt.assert_called_once_with(db, "support-prompt")


def test_renaming_a_prompt_does_not_reuse_an_agent_for_the_old_storage(monkeypatch):
    db = MagicMock()
    monkeypatch.setattr(
        agents_routes,
        "get_agent_info",
        MagicMock(return_value=SimpleNamespace(id=AGENT_ID, prompt_service_id="support-prompt")),
    )
    monkeypatch.setattr(agents_routes, "get_agent_config", MagicMock())
    get_prompt = MagicMock(return_value=PullPromptResponse(name="Support Helper", template="Help the user."))
    monkeypatch.setattr(agents_routes, "_get_agent_prompt", get_prompt)
    monkeypatch.setattr(agents_routes, "_agent_cache", {})

    def lookup():
        return asyncio.run(agents_routes.get_agent(db, AGENT_ID, agents_routes.DEFAULT_MODEL, "owner-1", "session-1"))

    agent, _, original_key, _ = lookup()
    assert agent is None
    cached_agent = MagicMock(spec=agents_routes.Agent)
    agents_routes._agent_cache[original_key] = cached_agent

    unchanged_agent, _, unchanged_key, _ = lookup()
    assert unchanged_agent is cached_agent
    assert unchanged_key == original_key

    get_prompt.return_value = PullPromptResponse(name="Renamed Support Helper", template="Help the user.")
    renamed_agent, _, renamed_key, _ = lookup()

    assert renamed_agent is None
    assert renamed_key != original_key
