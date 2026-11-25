import json
import logging
import runpy
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import httpx
import pytest
import requests

UPDATE = {"list_id": "opaque", "task_id": "one", "changes": {"notes": None, "title": "null"}}
DELETE = {"list_id": "opaque", "task_id": "two"}
COMPLETED_REPLY = {"status": "completed", "content": "Updated."}


def _pending(index, name, arguments):
    return {"tool_name": name, "tool_call_id": f"call-{index}", "tool_args": arguments, "requires_confirmation": True}


# A completed read and two pending Tasks writes, as the gateway reports a paused run.
COMPLETED = {"tool_name": "list_tasks", "tool_call_id": "done", "requires_confirmation": False, "result": {"ok": 1}}
PAUSED = {
    "event": "RunPaused",
    "run_id": "run",
    "status": "paused",
    "tools": [COMPLETED, _pending(0, "update_task", UPDATE), _pending(1, "delete_task", DELETE)],
}


def _client(monkeypatch, decisions, stream, reason=""):
    """Load the CLI with InquirerPy stubbed: actions come from `decisions`, the reason for declining a call is
    `reason`, and other prompts keep their defaults."""
    directory = Path(__file__).resolve().parents[1] / "cli"
    monkeypatch.syspath_prepend(str(directory))
    monkeypatch.setattr("logging.handlers.RotatingFileHandler", lambda *args, **kwargs: logging.NullHandler())
    logger = logging.getLogger("agent_cli")
    monkeypatch.setattr(logger, "handlers", [])
    monkeypatch.setattr(logger, "level", logger.level)
    select = Mock(side_effect=[SimpleNamespace(execute=lambda action=action: action) for action in decisions])
    keep_default = lambda message, default: SimpleNamespace(execute=lambda: default)  # noqa: E731

    def text(message, default):
        return (
            SimpleNamespace(execute=lambda: reason) if message.startswith("Reason") else keep_default(message, default)
        )

    inquirer = SimpleNamespace(select=select, text=text, confirm=keep_default, number=keep_default)
    terminal_stubs = {
        "InquirerPy": SimpleNamespace(inquirer=inquirer),
        "InquirerPy.base.control": SimpleNamespace(Choice=SimpleNamespace),
        "InquirerPy.separator": SimpleNamespace(Separator=SimpleNamespace),
    }
    with patch.dict(sys.modules, terminal_stubs):
        for name in ("client_ui.dialogs", "client_ui.formatters", "client_ui"):
            sys.modules.pop(name, None)
        cli = runpy.run_path(str(directory / "agent_cli.py"))
    client_class = cli["ConsoleChatClient"]
    client = client_class.__new__(client_class)
    client.result_formatter = Mock()
    client.dialog_builder = cli["DialogBuilder"](client.result_formatter)
    client.agent_id, client.user_id, client.session_id = "agent", "alice", "session"
    client.model, client.stream_flag = "gemini-2.5-pro", stream
    client.format_timestamp = lambda: "test"
    client._last_tools_payload, client._last_run_id = [], None
    return client, select


def _sse(*events):
    return "".join(f"event: message\ndata: {json.dumps(event)}\n\n" for event in events)


def _run(monkeypatch, client, stream, commit_reply, paused=PAUSED):
    """Deliver `paused` to the client and return the commit payloads it sends."""
    submitted = []
    if stream:
        transport_client = httpx.Client

        def transport(request):
            if request.url.path.endswith("/commit"):
                submitted.append(json.loads(request.content))
                return httpx.Response(200, text=_sse({"event": "RunCompleted", **commit_reply}))
            return httpx.Response(200, text=_sse({"event": "RunStarted", "run_id": "run"}, paused))

        monkeypatch.setattr(
            httpx, "Client", lambda **kwargs: transport_client(transport=httpx.MockTransport(transport))
        )
        client.handle_sse_stream("Update task")
    else:

        def post(url, *, json, timeout):
            submitted.append(json)
            return SimpleNamespace(status_code=200, json=lambda: commit_reply)

        # The CLI's own module: imported at the top, so _client's patch.dict keeps it in sys.modules
        monkeypatch.setattr(requests, "post", post)
        client.handle_single_response(paused)
    return submitted


@pytest.mark.parametrize("stream", [False, True])
def test_cli_prompts_for_every_pending_task_write_and_commits_once(monkeypatch, stream):
    client, select = _client(monkeypatch, ("confirm", "confirm"), stream)

    [payload] = _run(monkeypatch, client, stream, COMPLETED_REPLY)

    assert select.call_count == 2  # one prompt per pending call, none after the commit
    assert (payload["run_id"], payload["stream"]) == ("run", stream)
    completed, update, delete = payload["updated_tools"]
    assert "confirmed" not in completed
    # The generic editor round-trips nested arguments and nulls through JSON.
    assert (update["tool_args"], delete["tool_args"]) == (UPDATE, DELETE)
    assert (update["confirmed"], delete["confirmed"]) == (True, True)


@pytest.mark.parametrize("decisions", [("skip", "skip"), ("confirm", "cancel"), ("decline", "cancel")])
def test_cli_submits_nothing_without_a_decision_and_commit_reopens_the_batch(monkeypatch, decisions):
    client, _ = _client(monkeypatch, decisions, stream=False)

    assert _run(monkeypatch, client, False, COMPLETED_REPLY) == []

    # A cancelled or undecided batch is not committed, and /commit offers the same calls again.
    assert client._last_run_id == "run"
    assert [t.get("confirmed") for t in client._last_tools_payload] == [None, None, None]


def test_cli_submits_a_partly_skipped_batch_without_the_skipped_call(monkeypatch):
    client, _ = _client(monkeypatch, ("confirm", "skip"), stream=False)

    [payload] = _run(monkeypatch, client, False, COMPLETED_REPLY)

    _, update, delete = payload["updated_tools"]
    assert (update["confirmed"], delete.get("confirmed")) == (True, None)


def test_cli_prompts_for_other_approval_tools_and_keeps_custom_dialogs(monkeypatch):
    client, select = _client(monkeypatch, ("confirm",), stream=False)
    delete_contact = Mock(return_value={"action": "confirm", "edited_args": {"contact_id": "c1"}})
    monkeypatch.setattr(client.dialog_builder, "_build_delete_contact_dialog", delete_contact)
    paused = {
        "run_id": "run",
        "status": "paused",
        "tools": [_pending("code", "run_claude_code", {"prompt": "Fix it"}), _pending("c", "delete_contact", {})],
    }

    [payload] = _run(monkeypatch, client, False, COMPLETED_REPLY, paused=paused)

    delete_contact.assert_called_once()
    assert select.call_count == 1
    assert [t["confirmed"] for t in payload["updated_tools"]] == [True, True]


@pytest.mark.parametrize("stream", [False, True])
def test_cli_commits_a_batch_whose_only_call_is_declined_and_shows_the_reply(monkeypatch, capsys, stream):
    client, select = _client(monkeypatch, ("decline",), stream)
    pending = _pending(0, "delete_task", DELETE)
    paused = {**PAUSED, "tools": [pending]}
    # The reply lists the declined call again, as the gateway does
    reply = {
        "status": "completed",
        "content": "Understood, I left the task alone.",
        "tools": [{**pending, "confirmed": False}],
    }

    [payload] = _run(monkeypatch, client, stream, reply, paused=paused)

    # An empty reason falls back to a default note, which the gateway passes to the model.
    [declined] = payload["updated_tools"]
    assert (declined["confirmed"], declined["confirmation_note"]) == (False, "Declined by the user.")
    assert "Understood, I left the task alone." in capsys.readouterr().out
    assert select.call_count == 1  # the declined call is not asked about again


def test_cli_sends_the_typed_reason_for_a_declined_call_alongside_a_confirmed_one(monkeypatch):
    client, _ = _client(monkeypatch, ("confirm", "decline"), stream=False, reason="Keep that one")

    [payload] = _run(monkeypatch, client, False, COMPLETED_REPLY)

    _, update, delete = payload["updated_tools"]
    assert (update["confirmed"], update["tool_args"]) == (True, UPDATE)
    assert (delete["confirmed"], delete["confirmation_note"]) == (False, "Keep that one")


def test_cli_shows_the_json_reply_to_a_streaming_commit(monkeypatch, capsys):
    # A run that is no longer cached: the gateway answers an all-declined streaming commit in JSON.
    client, _ = _client(monkeypatch, ("decline",), stream=True)
    paused = {**PAUSED, "tools": [_pending(0, "delete_task", DELETE)]}
    cancelled = {"status": "cancelled", "content": "Tool execution cancelled by user."}
    transport_client = httpx.Client

    def transport(request):
        if request.url.path.endswith("/commit"):
            return httpx.Response(200, json=cancelled)
        return httpx.Response(200, text=_sse({"event": "RunStarted", "run_id": "run"}, paused))

    monkeypatch.setattr(httpx, "Client", lambda **kwargs: transport_client(transport=httpx.MockTransport(transport)))
    client.handle_sse_stream("Delete task")

    assert "Tool execution cancelled by user." in capsys.readouterr().out


@pytest.mark.parametrize("stream_kind", ["chat", "commit"])
@pytest.mark.parametrize("status_code", [401, 410, 500])
def test_cli_shows_chunked_streaming_errors(monkeypatch, capsys, stream_kind, status_code):
    client, _ = _client(monkeypatch, (), stream=True)
    transport_client = httpx.Client
    responses = []

    def transport(request):
        expected_endpoint = client.chat_endpoint if stream_kind == "chat" else client.commit_endpoint
        assert str(request.url) == expected_endpoint
        assert request.method == "POST"
        # An iterator keeps the body unread, unlike bytes or json=, which hide ResponseNotRead.
        response = httpx.Response(status_code, content=iter([b'{"detail": "', b"Run expired", b'"}']))
        assert not response.is_stream_consumed
        responses.append(response)
        return response

    monkeypatch.setattr(httpx, "Client", lambda **kwargs: transport_client(transport=httpx.MockTransport(transport)))

    if stream_kind == "chat":
        client.handle_sse_stream("Hello")
    else:
        client._handle_commit_sse_stream({"run_id": "run", "stream": True, "updated_tools": []})

    assert capsys.readouterr().out.strip() == f'[error] {status_code}: {{"detail": "Run expired"}}'
    [response] = responses
    assert response.is_stream_consumed
    assert response.is_closed


@pytest.mark.parametrize("stream", [False, True])
def test_cli_commit_command_has_nothing_to_resend_after_a_committed_batch(monkeypatch, capsys, stream):
    client, select = _client(monkeypatch, ("confirm", "confirm"), stream)

    [_] = _run(monkeypatch, client, stream, COMPLETED_REPLY)
    capsys.readouterr()

    assert client._last_run_id is None and client._last_tools_payload == []
    client.handle_command("/commit")

    assert "nothing to commit" in capsys.readouterr().out
    assert select.call_count == 2  # no new prompts


@pytest.mark.parametrize("stream", [False, True])
def test_cli_commit_command_keeps_the_batch_when_the_commit_request_fails(monkeypatch, stream):
    client, _ = _client(monkeypatch, ("confirm", "confirm"), stream)
    transport_client = httpx.Client

    def transport(request):
        if request.url.path.endswith("/commit"):
            return httpx.Response(500, text="boom")
        return httpx.Response(200, text=_sse({"event": "RunStarted", "run_id": "run"}, PAUSED))

    monkeypatch.setattr(httpx, "Client", lambda **kwargs: transport_client(transport=httpx.MockTransport(transport)))
    monkeypatch.setattr(requests, "post", lambda *a, **k: SimpleNamespace(status_code=500, text="boom"))
    if stream:
        client.handle_sse_stream("Update task")
    else:
        client.handle_single_response(PAUSED)

    # A failed commit was never accepted, so /commit can still retry it.
    assert client._last_run_id == "run"
    assert len(client._last_tools_payload) == 3


def test_cli_commit_command_offers_a_later_paused_batch_after_a_commit(monkeypatch):
    client, _ = _client(monkeypatch, ("confirm", "confirm", "cancel"), stream=False)
    later = {**PAUSED, "run_id": "run-2", "tools": [_pending(2, "delete_task", DELETE)]}

    [_] = _run(monkeypatch, client, False, later)

    # The commit reply paused again; the user cancelled, so that new batch (not the old one) is pending.
    assert client._last_run_id == "run-2"
    assert [t["tool_call_id"] for t in client._last_tools_payload] == ["call-2"]
