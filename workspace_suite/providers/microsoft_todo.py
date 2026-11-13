from contextlib import AbstractContextManager, nullcontext
from datetime import date, datetime
from typing import Any
from urllib.parse import quote
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
from bs4 import BeautifulSoup, NavigableString, Tag

from ..config import ProviderConfig
from ..models import UNSET, Task, TaskCreateRequest, TaskList, TaskUpdateRequest
from ..utils import parse_dt
from .tasks_base import TasksProvider

_LISTS_PATH = "/me/todo/lists"


def _task_path(list_id: str, task_id: str | None = None) -> str:
    if list_id in ("", ".", "..") or task_id in ("", ".", ".."):
        raise ValueError("Expected a Microsoft To Do resource ID.")
    path = f"{_LISTS_PATH}/{quote(list_id, safe='')}/tasks"
    return f"{path}/{quote(task_id, safe='')}" if task_id is not None else path


def _format_due(due: date) -> dict[str, str]:
    # The shared model has no time or timezone. Encode the calendar date at midnight UTC.
    return {"dateTime": f"{due.isoformat()}T00:00:00", "timeZone": "UTC"}


def _notes_body(notes: str) -> dict[str, str]:
    return {"contentType": "text", "content": notes}


class _BlockBreak(NavigableString):
    """Mark a block boundary that can share a newline with adjacent blocks.

    An explicit <br> is a separate newline and can represent a blank line.
    """


def _read_notes(body: dict[str, str]) -> str | None:
    content = body.get("content")
    # Graph can return a whitespace-only text body for cleared notes.
    if not content or not content.strip():
        return None
    if body.get("contentType", "").lower() == "html":
        html = BeautifulSoup(content, "html.parser")
        for tag in html.select("head, script, style, template"):
            tag.decompose()
        for tag in html.select("p, div, li, pre"):
            tag.insert_before(_BlockBreak("\n"))
            tag.insert_after(_BlockBreak("\n"))
        lines: list[str] = []
        block_break = False
        for part in html.descendants:
            if isinstance(part, _BlockBreak):
                block_break = True
            elif isinstance(part, Tag) and part.name == "br":
                if block_break and lines and not lines[-1].endswith("\n"):
                    lines.append("\n")
                lines.append("\n")
                block_break = False
            elif type(part) is NavigableString:
                if block_break and lines and not lines[-1].endswith("\n") and not part.startswith("\n"):
                    lines.append("\n")
                lines.append(str(part))
                block_break = False
        content = "".join(lines)
        if not content.strip() and html.find("pre") is None:
            return None
    return content or None


def _read_completion(value: dict[str, str]) -> datetime | None:
    raw = value.get("dateTime")
    if not raw:
        return None
    completed = parse_dt(raw)
    zone = value.get("timeZone")
    if completed.tzinfo is None and zone:
        try:
            completed = completed.replace(tzinfo=ZoneInfo(zone))
        except ZoneInfoNotFoundError:
            # Keep the reported wall time for Windows zone names unavailable to zoneinfo.
            pass
    return completed


def _to_task(data: dict[str, Any], list_id: str) -> Task:
    due = (data.get("dueDateTime") or {}).get("dateTime")
    return Task(
        id=data["id"],
        title=data.get("title") or "",
        list_id=list_id,
        status="completed" if data.get("status") == "completed" else "open",
        notes=_read_notes(data.get("body") or {}),
        due=parse_dt(due).date() if due else None,
        completed_at=_read_completion(data.get("completedDateTime") or {}),
    )


class MicrosoftTodoProvider(TasksProvider):
    """Use delegated Graph tokens to read and write Microsoft To Do tasks.

    Updates omit UNSET fields. A clear sends an empty HTML body for notes or
    JSON null for the due date. Blank text bodies from Graph map to no notes.
    """

    def __init__(self, config: ProviderConfig, http: httpx.Client | None = None):
        self.base_url = (config.base_url or "https://graph.microsoft.com/v1.0").rstrip("/")
        base = httpx.URL(self.base_url)
        if base.scheme != "https" or not base.host or base.userinfo or base.query or base.fragment:
            raise ValueError("Expected an HTTPS Microsoft Graph base URL without credentials, query, or fragment.")
        self._origin = (base.scheme, base.host, base.port)
        self.http = http

    def _client(self) -> AbstractContextManager[httpx.Client]:
        return httpx.Client() if self.http is None else nullcontext(self.http)

    def _request(
        self,
        client: httpx.Client,
        method: str,
        url: httpx.URL,
        token: str,
        http_timeout: float | tuple[float, float],
        payload: dict[str, str | dict[str, str] | None] | None = None,
    ) -> httpx.Response:
        if isinstance(http_timeout, tuple):
            connect, read = http_timeout
            timeout = httpx.Timeout(read, connect=connect)
        else:
            timeout = httpx.Timeout(http_timeout)
        # Construct the request directly so client defaults cannot alter the continuation query.
        request = httpx.Request(
            method,
            url,
            headers={"Authorization": f"Bearer {token}"},
            json=payload,
            extensions={"timeout": timeout.as_dict()},
        )
        return client.send(request, auth=None, follow_redirects=False).raise_for_status()

    def _next_page(self, link: str) -> httpx.URL:
        url = httpx.URL(link)
        # Validate the same parsed URL that the transport will use, before adding the bearer.
        if (url.scheme, url.host, url.port) != self._origin or url.userinfo or url.fragment:
            raise ValueError("Microsoft To Do returned an invalid Graph pagination URL.")
        return url

    def _list_items(self, path: str, token: str, http_timeout: float | tuple[float, float]) -> list[dict[str, Any]]:
        url = httpx.URL(self.base_url + path)
        items: list[dict[str, Any]] = []
        with self._client() as client:
            while True:
                data = self._request(client, "GET", url, token, http_timeout).json()
                items.extend(data.get("value", []))
                link = data.get("@odata.nextLink")
                if not link:
                    return items
                url = self._next_page(link)

    def list_task_lists(self, token: str, http_timeout: float | tuple[float, float] = 15.0) -> list[TaskList]:
        items = self._list_items(_LISTS_PATH, token, http_timeout)
        return [TaskList(id=item["id"], title=item.get("displayName") or "") for item in items]

    def list_tasks(self, token: str, list_id: str, http_timeout: float | tuple[float, float] = 15.0) -> list[Task]:
        """Return all tasks Graph lists, including completed tasks, in shared status values."""
        return [_to_task(item, list_id) for item in self._list_items(_task_path(list_id), token, http_timeout)]

    def create_task(
        self, token: str, list_id: str, request: TaskCreateRequest, http_timeout: float | tuple[float, float] = 15.0
    ) -> Task:
        payload: dict[str, str | dict[str, str] | None] = {"title": request.title}
        if request.notes is not None:
            payload["body"] = _notes_body(request.notes)
        if request.due is not None:
            payload["dueDateTime"] = _format_due(request.due)
        url = httpx.URL(self.base_url + _task_path(list_id))
        with self._client() as client:
            response = self._request(client, "POST", url, token, http_timeout, payload)
        return _to_task(response.json(), list_id)

    def update_task(
        self,
        token: str,
        list_id: str,
        task_id: str,
        request: TaskUpdateRequest,
        http_timeout: float | tuple[float, float] = 15.0,
    ) -> Task:
        request.validate_changes()
        payload: dict[str, str | dict[str, str] | None] = {}
        if request.title is not UNSET:
            payload["title"] = request.title
        if request.notes is not UNSET:
            payload["body"] = (
                {"contentType": "html", "content": ""} if request.notes is None else _notes_body(request.notes)
            )
        if request.due is not UNSET:
            payload["dueDateTime"] = None if request.due is None else _format_due(request.due)
        return self._patch_task(token, list_id, task_id, http_timeout, payload)

    def complete_task(
        self, token: str, list_id: str, task_id: str, http_timeout: float | tuple[float, float] = 15.0
    ) -> Task:
        return self._patch_task(token, list_id, task_id, http_timeout, {"status": "completed"})

    def _patch_task(
        self,
        token: str,
        list_id: str,
        task_id: str,
        http_timeout: float | tuple[float, float],
        payload: dict[str, str | dict[str, str] | None],
    ) -> Task:
        url = httpx.URL(self.base_url + _task_path(list_id, task_id))
        with self._client() as client:
            response = self._request(client, "PATCH", url, token, http_timeout, payload)
        return _to_task(response.json(), list_id)

    def delete_task(
        self, token: str, list_id: str, task_id: str, http_timeout: float | tuple[float, float] = 15.0
    ) -> None:
        url = httpx.URL(self.base_url + _task_path(list_id, task_id))
        with self._client() as client:
            self._request(client, "DELETE", url, token, http_timeout)
