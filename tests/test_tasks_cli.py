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


def _client(monkeypatch, decisions, stream):
    """Load the CLI with InquirerPy stubbed: text prompts keep their defaults, actions come from `decisions`."""
    directory = Path(__file__).resolve().parents[1] / "cli"
    monkeypatch.syspath_prepend(str(directory))
    monkeypatch.setattr("logging.handlers.RotatingFileHandler", lambda *args, **kwargs: logging.NullHandler())
    logger = logging.getLogger("agent_cli")
    monkeypatch.setattr(logger, "handlers", [])
    monkeypatch.setattr(logger, "level", logger.level)
    select = Mock(side_effect=[SimpleNamespace(execute=lambda action=action: action) for action in decisions])
    keep_default = lambda message, default: SimpleNamespace(execute=lambda: default)  # noqa: E731
    inquirer = SimpleNamespace(select=select, text=keep_default, confirm=keep_default, number=keep_default)
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


@pytest.mark.parametrize("decisions", [("skip", "skip"), ("confirm", "cancel")])
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
