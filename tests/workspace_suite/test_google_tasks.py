import json
from datetime import date, datetime, timezone
from typing import Iterator
from unittest.mock import patch

import httpx
import pytest

from tests.workspace_suite.helpers import make_mock_client
from workspace_suite.config import ProviderConfig
from workspace_suite.models import Task, TaskCreateRequest, TaskList, TaskUpdateRequest
from workspace_suite.providers.google_tasks import GoogleTasksProvider


def test_list_task_lists_follows_page_token() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/tasks/v1/users/@me/lists"
        assert request.headers["Authorization"] == "Bearer read-token"
        assert request.extensions["timeout"] == dict.fromkeys(("connect", "read", "write", "pool"), 15.0)
        if not request.url.params:
            return httpx.Response(200, json={"items": [{"id": "1", "title": "Home"}], "nextPageToken": "a+/?="})
        assert dict(request.url.params) == {"pageToken": "a+/?="}
        return httpx.Response(200, json={"items": [{"id": "2", "title": "Work"}]})

    with make_mock_client(handler) as http:
        provider = GoogleTasksProvider(ProviderConfig(), http=http)
        assert provider.list_task_lists("read-token") == [TaskList("1", "Home"), TaskList("2", "Work")]


def test_list_tasks_keeps_visibility_and_timeout_across_pages() -> None:
    page_tokens = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.raw_path.split(b"?")[0] == b"/tasks/v1/lists/list%2F%2B%25/tasks"
        assert request.headers["Authorization"] == "Bearer read-token"
        assert request.extensions["timeout"] == {"connect": 2.0, "read": 7.0, "write": 7.0, "pool": 7.0}
        params = dict(request.url.params)
        page_token = params.pop("pageToken", None)
        page_tokens.append(page_token)
        assert params == {"maxResults": "100", "showCompleted": "true", "showHidden": "true"}
        if page_token is None:
            return httpx.Response(200, json={"nextPageToken": "empty-page"})
        if page_token == "empty-page":
            return httpx.Response(200, json={"items": [], "nextPageToken": "last-page"})
        assert page_token == "last-page"
        return httpx.Response(
            200,
            json={
                "items": [
                    {"id": "1", "status": "needsAction", "notes": ""},
                    {
                        "id": "2",
                        "title": "Water plants",
                        "status": "completed",
                        "hidden": True,
                        "due": "2026-11-20T00:30:00+05:30",
                        "completed": "2026-11-19T09:15:00Z",
                    },
                ]
            },
        )

    with make_mock_client(handler) as http:
        provider = GoogleTasksProvider(ProviderConfig(), http=http)
        assert provider.list_tasks("read-token", "list/+%", (2.0, 7.0)) == [
            Task(id="1", title="", list_id="list/+%", status="open"),
            Task(
                id="2",
                title="Water plants",
                list_id="list/+%",
                status="completed",
                due=date(2026, 11, 20),
                completed_at=datetime(2026, 11, 19, 9, 15, tzinfo=timezone.utc),
            ),
        ]
        assert page_tokens == [None, "empty-page", "last-page"]


def test_task_write_lifecycle_and_list_readback() -> None:
    steps: Iterator[tuple[str, dict[str, str | None] | None]] = iter(
        [
            (
                "POST",
                {"title": "Water plants", "notes": "Kitchen\nUse rainwater", "due": "2026-11-20T00:00:00Z"},
            ),
            ("PATCH", {"title": "Water balcony plants"}),
            ("PATCH", {"notes": "Balcony too", "due": "2026-11-21T00:00:00Z"}),
            ("PATCH", {"notes": None}),
            ("PATCH", {"notes": "Use rainwater", "due": None}),
            ("PATCH", {"status": "completed"}),
            ("GET", None),
            ("DELETE", None),
            ("GET", None),
        ]
    )
    state: dict[str, str] = {}
    list_path = b"/tasks/v1/lists/home/tasks"
    task_path = list_path + b"/task%2F%3F%3D"

    def handler(request: httpx.Request) -> httpx.Response:
        method, payload = next(steps)
        assert request.method == method
        assert request.url.raw_path.split(b"?")[0] == (list_path if method in ("GET", "POST") else task_path)
        assert request.headers["Authorization"] == ("Bearer read-token" if method == "GET" else "Bearer write-token")
        if method == "POST":
            assert request.extensions["timeout"] == dict.fromkeys(("connect", "read", "write", "pool"), 4.0)
            state.update(id="task/?=", status="needsAction")
        if payload is not None:
            assert json.loads(request.content) == payload
            for key, value in payload.items():
                if value is None:
                    del state[key]
                else:
                    state[key] = value
            if payload.get("status") == "completed":
                state["completed"] = "2026-11-19T09:15:00Z"
            return httpx.Response(200, json=state)

        assert request.content == b""
        if method == "DELETE":
            state.clear()
            return httpx.Response(204)
        return httpx.Response(200, json={"items": [state] if state else []})

    with make_mock_client(handler) as http:
        provider = GoogleTasksProvider(ProviderConfig(), http=http)
        task = provider.create_task(
            "write-token",
            "home",
            TaskCreateRequest("Water plants", notes="Kitchen\nUse rainwater", due=date(2026, 11, 20)),
            http_timeout=4.0,
        )
        assert task == Task("task/?=", "Water plants", "home", "open", "Kitchen\nUse rainwater", date(2026, 11, 20))
        task = provider.update_task("write-token", "home", task.id, TaskUpdateRequest(title="Water balcony plants"))
        assert task.title == "Water balcony plants"
        assert task.notes == "Kitchen\nUse rainwater"
        assert task.due == date(2026, 11, 20)
        task = provider.update_task(
            "write-token", "home", task.id, TaskUpdateRequest(notes="Balcony too", due=date(2026, 11, 21))
        )
        assert (task.notes, task.due) == ("Balcony too", date(2026, 11, 21))
        task = provider.update_task("write-token", "home", task.id, TaskUpdateRequest(notes=None))
        assert (task.notes, task.due) == (None, date(2026, 11, 21))
        task = provider.update_task("write-token", "home", task.id, TaskUpdateRequest(notes="Use rainwater", due=None))
        assert (task.notes, task.due) == ("Use rainwater", None)
        completed = provider.complete_task("write-token", "home", task.id)
        assert completed == Task(
            "task/?=",
            "Water balcony plants",
            "home",
            "completed",
            notes="Use rainwater",
            completed_at=datetime(2026, 11, 19, 9, 15, tzinfo=timezone.utc),
        )
        assert provider.list_tasks("read-token", "home") == [completed]
        provider.delete_task("write-token", "home", completed.id)
        assert provider.list_tasks("read-token", "home") == []
        assert next(steps, None) is None
        assert not http.is_closed


def test_create_omits_empty_notes_and_preserves_title() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://example.test/api/lists/home/tasks"
        assert json.loads(request.content) == {"title": "  "}
        return httpx.Response(200, json={"id": "1", "title": "  ", "status": "needsAction"})

    with make_mock_client(handler) as http:
        provider = GoogleTasksProvider(ProviderConfig(base_url="https://example.test/api/"), http=http)
        assert provider.create_task("token", "home", TaskCreateRequest("  ", notes="")) == Task(
            "1", "  ", "home", "open"
        )


def test_update_with_no_changes_does_not_open_a_client() -> None:
    provider = GoogleTasksProvider(ProviderConfig())
    with patch.object(provider, "_client") as client:
        with pytest.raises(ValueError, match="at least one change"):
            provider.update_task("token", "home", "1", TaskUpdateRequest())
        client.assert_not_called()


def test_update_due_only_clear_preserves_notes() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert json.loads(request.content) == {"due": None}
        return httpx.Response(
            200, json={"id": "1", "title": "Water plants", "notes": "Kitchen", "status": "needsAction", "due": None}
        )

    with make_mock_client(handler) as http:
        provider = GoogleTasksProvider(ProviderConfig(), http=http)
        assert provider.update_task("token", "home", "1", TaskUpdateRequest(due=None)) == Task(
            "1", "Water plants", "home", "open", notes="Kitchen"
        )


def test_401_on_later_page_raises_before_decoding() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "pageToken" not in request.url.params:
            return httpx.Response(200, json={"items": [{"id": "1", "title": "Home"}], "nextPageToken": "expired"})
        return httpx.Response(401, text="Expired")

    with make_mock_client(handler) as http:
        provider = GoogleTasksProvider(ProviderConfig(), http=http)
        with pytest.raises(httpx.HTTPStatusError) as exc:
            provider.list_task_lists("expired-token")
        assert exc.value.response.status_code == 401
        assert exc.value.request.url.params["pageToken"] == "expired"


def test_write_http_error_is_propagated_without_retry() -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.method)
        return httpx.Response(403)

    with make_mock_client(handler) as http:
        provider = GoogleTasksProvider(ProviderConfig(), http=http)
        with pytest.raises(httpx.HTTPStatusError) as exc:
            provider.complete_task("read-only-token", "home", "1")
        assert exc.value.response.status_code == 403
        assert calls == ["PATCH"]


def test_write_does_not_follow_redirect_with_injected_client() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(307, headers={"Location": "https://redirect.example.test/tasks"})

    with httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True) as http:
        provider = GoogleTasksProvider(ProviderConfig(), http=http)
        with pytest.raises(httpx.HTTPStatusError) as exc:
            provider.create_task("token", "home", TaskCreateRequest("Water plants", notes="Kitchen"))

    assert exc.value.response.status_code == 307
    assert len(requests) == 1
    assert requests[0].url.host == "tasks.googleapis.com"
    assert json.loads(requests[0].content) == {"title": "Water plants", "notes": "Kitchen"}
