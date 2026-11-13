import json
from datetime import date, datetime, timezone

import httpx
import pytest

from tests.workspace_suite.helpers import make_mock_client
from workspace_suite.config import ProviderConfig
from workspace_suite.models import Task, TaskCreateRequest, TaskList, TaskUpdateRequest
from workspace_suite.providers.microsoft_todo import MicrosoftTodoProvider

LISTS_PATH = b"/v1.0/me/todo/lists"
TASKS_PATH = LISTS_PATH + b"/list%2F%3F%3D/tasks"


def test_list_task_lists_keeps_opaque_next_link_and_per_call_auth() -> None:
    query = b"?$top=2&$skiptoken=a%2B%2f%3D&repeat=x&repeat=y"
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.extensions["timeout"] == {"connect": 2.0, "read": 7.0, "write": 7.0, "pool": 7.0}
        seen.append((request.url.raw_path, request.headers["Authorization"]))
        if request.url.raw_path == LISTS_PATH:
            return httpx.Response(
                200,
                json={
                    "value": [{"id": "one", "displayName": "Home"}],
                    "@odata.nextLink": f"https://GRAPH.microsoft.com:443{LISTS_PATH.decode()}{query.decode()}",
                },
            )
        assert request.url.raw_path == LISTS_PATH + query
        return httpx.Response(200, json={"value": [{"id": "two", "displayName": "Work"}]})

    with httpx.Client(
        transport=httpx.MockTransport(handler), auth=("client", "default"), params={"$top": "override"}
    ) as http:
        provider = MicrosoftTodoProvider(ProviderConfig(), http=http)
        for token in ("first-token", "second-token"):
            assert provider.list_task_lists(token, (2.0, 7.0)) == [TaskList("one", "Home"), TaskList("two", "Work")]
        assert seen == [
            (LISTS_PATH, "Bearer first-token"),
            (LISTS_PATH + query, "Bearer first-token"),
            (LISTS_PATH, "Bearer second-token"),
            (LISTS_PATH + query, "Bearer second-token"),
        ]
        assert not http.is_closed


def test_list_tasks_reads_html_and_completed_status_after_an_empty_page() -> None:
    paths = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        paths.append(request.url.raw_path)
        if request.url.raw_path == TASKS_PATH:
            return httpx.Response(
                200, json={"value": [], "@odata.nextLink": f"https://example.test:8443{TASKS_PATH.decode()}?$skip=1"}
            )
        assert request.url.raw_path == TASKS_PATH + b"?$skip=1"
        return httpx.Response(
            200,
            json={
                "value": [
                    {"id": "1", "title": "Waiting", "status": "waitingOnOthers", "body": None},
                    {
                        "id": "2",
                        "title": "Water plants",
                        "status": "completed",
                        "body": {
                            "contentType": "html",
                            "content": "<html><head><title>Metadata</title></head><body>"
                            "<div>Kitchen &amp; patio<div>Use rain<b>water</b><br><br>Check later</div>Done</div>"
                            "<div><br></div><div>&lt;done&gt;</div><script>private()</script></body></html>",
                        },
                        "dueDateTime": {"dateTime": "2026-11-20T00:30:00.0000000", "timeZone": "India Standard Time"},
                        "completedDateTime": {"dateTime": "2026-11-19T09:15:00.0000000", "timeZone": "UTC"},
                    },
                ],
            },
        )

    with make_mock_client(handler) as http:
        provider = MicrosoftTodoProvider(ProviderConfig(base_url="https://example.test:8443/v1.0/"), http=http)
        assert provider.list_tasks("read-token", "list/?=") == [
            Task("1", "Waiting", "list/?=", "open"),
            Task(
                "2",
                "Water plants",
                "list/?=",
                "completed",
                notes="Kitchen & patio\nUse rainwater\n\nCheck later\nDone\n\n<done>",
                due=date(2026, 11, 20),
                completed_at=datetime(2026, 11, 19, 9, 15, tzinfo=timezone.utc),
            ),
        ]
        assert paths == [TASKS_PATH, TASKS_PATH + b"?$skip=1"]


def test_task_write_lifecycle_and_omitted_fields() -> None:
    notes = "\n  Kitchen & patio\nUse <rainwater>  \n"
    replacement = "Patio only\n  Bring water"
    steps = iter(
        [
            (
                "POST",
                {
                    "title": "  ",
                    "body": {"contentType": "text", "content": notes},
                    "dueDateTime": {"dateTime": "2026-11-20T00:00:00", "timeZone": "UTC"},
                },
            ),
            ("PATCH", {"title": "  Water plants  "}),
            (
                "PATCH",
                {
                    "body": {"contentType": "text", "content": replacement},
                    "dueDateTime": {"dateTime": "2026-11-21T00:00:00", "timeZone": "UTC"},
                },
            ),
            ("PATCH", {"status": "completed"}),
            ("DELETE", None),
        ]
    )
    state = {"id": "task/+%", "status": "notStarted"}

    def handler(request: httpx.Request) -> httpx.Response:
        method, payload = next(steps)
        assert request.method == method
        assert request.url.raw_path == (TASKS_PATH if method == "POST" else TASKS_PATH + b"/task%2F%2B%25")
        assert request.headers["Authorization"] == "Bearer write-token"
        assert request.extensions["timeout"] == dict.fromkeys(("connect", "read", "write", "pool"), 15.0)
        if payload is None:
            assert request.content == b""
            return httpx.Response(204)
        assert json.loads(request.content) == payload
        state.update(json.loads(request.content))
        return httpx.Response(201 if method == "POST" else 200, json=state)

    with make_mock_client(handler) as http:
        provider = MicrosoftTodoProvider(ProviderConfig(), http=http)
        created = provider.create_task("write-token", "list/?=", TaskCreateRequest("  ", notes, date(2026, 11, 20)))
        assert created == Task("task/+%", "  ", "list/?=", "open", notes, date(2026, 11, 20))
        updated = provider.update_task(
            "write-token", "list/?=", created.id, TaskUpdateRequest(title="  Water plants  ")
        )
        assert (updated.title, updated.notes, updated.due) == ("  Water plants  ", notes, date(2026, 11, 20))
        updated = provider.update_task(
            "write-token", "list/?=", created.id, TaskUpdateRequest(notes=replacement, due=date(2026, 11, 21))
        )
        assert (updated.title, updated.notes, updated.due) == ("  Water plants  ", replacement, date(2026, 11, 21))
        completed = provider.complete_task("write-token", "list/?=", created.id)
        assert completed.status == "completed"
        provider.delete_task("write-token", "list/?=", created.id)
        assert next(steps, None) is None


def test_create_omits_empty_notes_and_missing_due_date() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert json.loads(request.content) == {"title": "Task"}
        return httpx.Response(201, json={"id": "one", "title": "Task", "body": {"contentType": "text", "content": ""}})

    with make_mock_client(handler) as http:
        provider = MicrosoftTodoProvider(ProviderConfig(), http=http)
        assert provider.create_task("token", "list", TaskCreateRequest("Task", notes="")) == Task(
            "one", "Task", "list", "open"
        )


@pytest.mark.parametrize(
    ("update", "expected_fields"),
    [
        (TaskUpdateRequest(notes="New notes"), {"body"}),
        (TaskUpdateRequest(due=date(2026, 11, 21)), {"dueDateTime"}),
        (TaskUpdateRequest(title="New title", notes="New notes"), {"title", "body"}),
        (TaskUpdateRequest(title="New title", due=date(2026, 11, 21)), {"title", "dueDateTime"}),
        (
            TaskUpdateRequest(title="New title", notes="New notes", due=date(2026, 11, 21)),
            {"title", "body", "dueDateTime"},
        ),
    ],
)
def test_update_sends_only_supplied_fields(update: TaskUpdateRequest, expected_fields: set[str]) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "PATCH"
        assert set(json.loads(request.content)) == expected_fields
        return httpx.Response(200, json={"id": "task", "title": "Task"})

    with make_mock_client(handler) as http:
        provider = MicrosoftTodoProvider(ProviderConfig(), http=http)
        provider.update_task("token", "list", "task", update)


@pytest.mark.parametrize(
    ("update", "payload", "expected"),
    [
        (
            TaskUpdateRequest(notes=None),
            {"body": {"contentType": "html", "content": ""}},
            Task("task", "Old title", "list", "open", due=date(2026, 11, 20)),
        ),
        (
            TaskUpdateRequest(due=None),
            {"dueDateTime": None},
            Task("task", "Old title", "list", "open", notes="Old notes"),
        ),
        (
            TaskUpdateRequest(title="New title", notes=None, due=None),
            {"title": "New title", "body": {"contentType": "html", "content": ""}, "dueDateTime": None},
            Task("task", "New title", "list", "open"),
        ),
        (
            TaskUpdateRequest(notes="New notes", due=None),
            {"body": {"contentType": "text", "content": "New notes"}, "dueDateTime": None},
            Task("task", "Old title", "list", "open", notes="New notes"),
        ),
        (
            TaskUpdateRequest(notes=None, due=date(2026, 11, 21)),
            {
                "body": {"contentType": "html", "content": ""},
                "dueDateTime": {"dateTime": "2026-11-21T00:00:00", "timeZone": "UTC"},
            },
            Task("task", "Old title", "list", "open", due=date(2026, 11, 21)),
        ),
    ],
)
def test_update_applies_clears_in_one_request(
    update: TaskUpdateRequest, payload: dict[str, object], expected: Task
) -> None:
    calls = []
    stored: dict[str, object] = {
        "id": "task",
        "title": "Old title",
        "body": {"contentType": "text", "content": "Old notes"},
        "dueDateTime": {"dateTime": "2026-11-20T00:00:00", "timeZone": "UTC"},
    }

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.method)
        if request.method == "GET":
            return httpx.Response(200, json={"value": [stored]})
        assert request.method == "PATCH"
        assert json.loads(request.content) == payload
        stored.update(payload)
        if update.notes is None:
            # Live Graph returns a whitespace-only text body after a successful clear.
            stored["body"] = {"contentType": "text", "content": "\r\n"}
        return httpx.Response(200, json=stored)

    with make_mock_client(handler) as http:
        provider = MicrosoftTodoProvider(ProviderConfig(), http=http)
        assert provider.update_task("token", "list", "task", update) == expected
        assert provider.list_tasks("token", "list") == [expected]
    assert calls == ["PATCH", "GET"]


def test_empty_update_sends_no_request() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("The empty update reached the transport")

    with make_mock_client(handler) as http:
        provider = MicrosoftTodoProvider(ProviderConfig(), http=http)
        with pytest.raises(ValueError, match="at least one change"):
            provider.update_task("token", "list", "task", TaskUpdateRequest())


def test_create_surfaces_graph_rejection_of_whitespace_title() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert json.loads(request.content) == {"title": "   "}
        return httpx.Response(400, json={"error": {"code": "BadRequest"}})

    with make_mock_client(handler) as http:
        provider = MicrosoftTodoProvider(ProviderConfig(), http=http)
        with pytest.raises(httpx.HTTPStatusError) as exc:
            provider.create_task("token", "list", TaskCreateRequest("   "))
        assert exc.value.response.status_code == 400


@pytest.mark.parametrize(
    "link",
    [
        "https://example.test/page",
        "https://graph.microsoft.com:8443/page",
        "http://graph.microsoft.com/page",
        "//graph.microsoft.com/page",
        "/v1.0/me/todo/lists?$skip=1",
        "https://user:password@graph.microsoft.com/page",
    ],
)
def test_invalid_next_link_is_rejected_before_sending_a_second_request(link: str) -> None:
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.raw_path)
        return httpx.Response(200, json={"value": [], "@odata.nextLink": link})

    with make_mock_client(handler) as http:
        provider = MicrosoftTodoProvider(ProviderConfig(), http=http)
        with pytest.raises(ValueError, match="invalid Graph pagination URL"):
            provider.list_task_lists("token")
        assert seen == [LISTS_PATH]


def test_401_on_later_page_raises_before_decoding() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.raw_path == LISTS_PATH:
            return httpx.Response(
                200, json={"value": [], "@odata.nextLink": f"https://graph.microsoft.com{LISTS_PATH.decode()}?$skip=1"}
            )
        return httpx.Response(401, text="Expired")

    with make_mock_client(handler) as http:
        provider = MicrosoftTodoProvider(ProviderConfig(), http=http)
        with pytest.raises(httpx.HTTPStatusError) as exc:
            provider.list_task_lists("expired-token")
        assert exc.value.response.status_code == 401
        assert exc.value.response.text == "Expired"


@pytest.mark.parametrize("write", [False, True])
def test_no_redirect_even_with_redirect_enabled_client(write: bool) -> None:
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(307, headers={"Location": "https://example.test/tasks"})

    with httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True) as http:
        provider = MicrosoftTodoProvider(ProviderConfig(), http=http)
        with pytest.raises(httpx.HTTPStatusError) as exc:
            if write:
                provider.create_task("token", "list", TaskCreateRequest("Task", notes="Private note"))
            else:
                provider.list_task_lists("token")

    assert exc.value.response.status_code == 307
    assert [request.url.host for request in seen] == ["graph.microsoft.com"]
