import json
import logging

import httpx
import pytest
import toolkits

from tests.workspace_suite.helpers import make_mock_client
from toolkits.tasks import GOOGLE_TASKS, MICROSOFT_TODO, TASKS_WRITE_TOOLS
from toolkits.token_cache import TokenCache


@pytest.fixture(autouse=True)
def isolated_token_cache(monkeypatch):
    cache = TokenCache()
    monkeypatch.setattr(toolkits, "token_cache", cache)


def connected_toolkit(service, http, *, user="alice", fetch=lambda user, key: "synthetic-token"):
    toolkit = toolkits.TasksToolkit(user, service, fetch_token_func=fetch)
    toolkit.tasks_service.provider.http = http
    return toolkit


def task_response(google):
    task = {"id": "task", "title": "null", "status": "completed"}
    if google:
        return {**task, "due": "2026-11-20T00:00:00Z", "completed": "2026-11-19T09:15:00Z"}
    return {
        **task,
        "dueDateTime": {"dateTime": "2026-11-20T00:00:00", "timeZone": "UTC"},
        "completedDateTime": {"dateTime": "2026-11-19T09:15:00", "timeZone": "UTC"},
    }


@pytest.mark.parametrize("service", [GOOGLE_TASKS, MICROSOFT_TODO])
def test_toolkit_operations_through_real_provider(service):
    google = service == GOOGLE_TASKS
    list_title = "title" if google else "displayName"
    collection = "items" if google else "value"
    changes = []

    def handler(request):
        assert request.headers["Authorization"] == "Bearer synthetic-token"
        if request.method == "DELETE":
            return httpx.Response(204)
        if request.content:
            changes.append(json.loads(request.content))
            return httpx.Response(200, json=task_response(google))
        if request.url.path.endswith("/lists"):
            return httpx.Response(200, json={collection: [{"id": "list", list_title: "Work"}]})
        return httpx.Response(200, json={collection: [task_response(google)]})

    with make_mock_client(handler) as http:
        toolkit = connected_toolkit(service, http)
        assert {
            name for name, function in toolkit.get_async_functions().items() if function.requires_confirmation
        } == set(TASKS_WRITE_TOOLS)
        assert toolkit.list_task_lists() == {"lists": [{"id": "list", "title": "Work"}]}
        [task] = toolkit.list_tasks("list")["tasks"]
        assert task["due"] == "2026-11-20"
        assert task["completed_at"] == "2026-11-19T09:15:00+00:00"
        assert task["status"] == "completed"
        assert toolkit.create_task("list", {"title": "null"})["id"] == "task"
        assert toolkit.update_task("list", "task", {"title": "true"})["id"] == "task"
        assert toolkit.update_task("list", "task", {"notes": None, "due": None})["id"] == "task"
        assert toolkit.complete_task("list", "task")["status"] == "completed"
        assert toolkit.delete_task("list", "task") == {"deleted": True, "list_id": "list", "task_id": "task"}
    assert changes[:2] == [{"title": "null"}, {"title": "true"}]
    expected_clear = (
        {"notes": None, "due": None}
        if google
        else {"body": {"contentType": "html", "content": ""}, "dueDateTime": None}
    )
    assert changes[2] == expected_clear


def test_update_schema_exposes_an_optional_nonempty_title():
    toolkit = toolkits.TasksToolkit("alice", GOOGLE_TASKS)
    changes = toolkit.get_async_functions()["update_task"].parameters["properties"]["changes"]
    title = changes["properties"]["title"]
    assert title["type"] == "string"
    assert title["minLength"] == 1
    assert "default" not in title
    assert "title" not in changes.get("required", [])


def test_invalid_tool_input_stops_before_io():
    def unexpected_fetch(*args):
        pytest.fail("Invalid input reached token lookup")

    toolkit = toolkits.TasksToolkit("alice", GOOGLE_TASKS, fetch_token_func=unexpected_fetch)
    update = toolkit.get_async_functions()["update_task"]
    assert update.entrypoint is not None
    for changes in (
        {},
        {"due": "20261120"},
        {"due": "2026-02-31"},
        {"notes": ""},
        {"title": None},
        {"title": ""},
        {"notes": 12},
    ):
        assert update.entrypoint(list_id="list", task_id="task", changes=changes)["card"] == "error"
    assert update.entrypoint(list_id="list", changes={"notes": None})["card"] == "error"
    assert update.entrypoint(list_id="list", task_id="task", changes={"notes": None}, extra="unused")["card"] == "error"
    assert update.entrypoint(list_id="list", task_id=".", changes={"notes": None})["card"] == "error"
    assert toolkit.create_task("list", {"title": "", "notes": "private-canary"})["card"] == "error"
    assert toolkit.list_tasks("")["card"] == "error"
    assert toolkit.complete_task("list", "..")["card"] == "error"
    assert toolkit.delete_task(".", "task")["card"] == "error"
    disconnected = toolkits.TasksToolkit("alice", "tasks", auth=False, fetch_token_func=unexpected_fetch)
    assert list(disconnected.get_async_functions()) == ["tasks_auth_required"]
    assert disconnected.create_task("list", {"title": "Draft"})["card"] == "tasks-auth-required"


def test_rejected_token_evicts_only_the_matching_cache_entry(caplog):
    grants = {
        ("alice", GOOGLE_TASKS): "rejected-alice",
        ("bob", GOOGLE_TASKS): "cached-bob",
    }
    requests = []

    def fetch(user, service):
        return grants[user, service]

    def handler(request):
        bearer = request.headers["Authorization"]
        requests.append(bearer)
        if bearer == "Bearer rejected-alice":
            return httpx.Response(401, text="private-vendor-body")
        return httpx.Response(200, json={})

    with make_mock_client(handler) as http:
        alice = connected_toolkit(GOOGLE_TASKS, http, user="alice", fetch=fetch)
        bob = connected_toolkit(GOOGLE_TASKS, http, user="bob", fetch=fetch)
        assert bob.list_task_lists() == {"lists": []}

        reconnect = alice.list_task_lists()
        assert reconnect["card"] == "tasks-auth-required"
        assert reconnect["context"] == {
            "token_valid": False,
            "service_name": GOOGLE_TASKS,
            "available_services": [GOOGLE_TASKS, MICROSOFT_TODO],
        }

        grants["alice", GOOGLE_TASKS] = "replacement-alice"
        grants["bob", GOOGLE_TASKS] = "uncached-bob"
        assert alice.list_task_lists() == {"lists": []}
        assert bob.list_task_lists() == {"lists": []}

    assert requests == [
        "Bearer cached-bob",
        "Bearer rejected-alice",
        "Bearer replacement-alice",
        "Bearer cached-bob",
    ]
    assert "private-vendor-body" not in str(reconnect)
    assert "private-vendor-body" not in caplog.text


@pytest.mark.parametrize("failure", [403, 500, "timeout", "malformed"])
def test_task_failure_is_sanitized(failure, caplog):
    def handler(request):
        if failure == "timeout":
            raise httpx.ReadTimeout("private-canary", request=request)
        if failure == "malformed":
            return httpx.Response(200, text="private-canary")
        return httpx.Response(failure, text="private-canary")

    with caplog.at_level(logging.DEBUG, logger="toolkits.tasks"), make_mock_client(handler) as http:
        reply = connected_toolkit(GOOGLE_TASKS, http).create_task("list", {"title": "private-canary"})
    assert reply["card"] == "error"
    assert "private-canary" not in str(reply)
    assert "private-canary" not in caplog.text
    if failure == 403:
        assert "Permission denied" in reply["message"]
    if failure == "timeout":
        assert "Task operation failed with ReadTimeout" in caplog.messages
