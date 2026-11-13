from contextlib import AbstractContextManager, nullcontext
from datetime import date
from typing import Any
from urllib.parse import quote

import httpx

from ..config import ProviderConfig
from ..models import UNSET, Task, TaskCreateRequest, TaskList, TaskUpdateRequest
from ..utils import parse_dt
from .tasks_base import TasksProvider


def _task_path(list_id: str, task_id: str | None = None) -> str:
    if list_id in ("", ".", "..") or task_id in ("", ".", ".."):
        raise ValueError("Expected a Google Tasks resource ID.")
    path = f"/lists/{quote(list_id, safe='')}/tasks"
    return f"{path}/{quote(task_id, safe='')}" if task_id is not None else path


def _format_due(due: date) -> str:
    return f"{due.isoformat()}T00:00:00Z"


def _to_task(data: dict[str, Any], list_id: str) -> Task:
    due = data.get("due")
    completed = data.get("completed")
    return Task(
        id=data["id"],
        title=data.get("title") or "",
        list_id=list_id,
        status="completed" if data.get("status") == "completed" else "open",
        notes=data.get("notes") or None,
        # Keep the calendar date as written in the due timestamp, regardless of its offset.
        due=parse_dt(due).date() if due else None,
        completed_at=parse_dt(completed) if completed else None,
    )


class GoogleTasksProvider(TasksProvider):
    def __init__(self, config: ProviderConfig, http: httpx.Client | None = None):
        self.base_url = (config.base_url or "https://tasks.googleapis.com/tasks/v1").rstrip("/")
        self.http = http

    def _client(self) -> AbstractContextManager[httpx.Client]:
        return httpx.Client() if self.http is None else nullcontext(self.http)

    def _request(
        self,
        client: httpx.Client,
        method: str,
        path: str,
        token: str,
        http_timeout: float | tuple[float, float],
        *,
        params: dict[str, str | int | bool] | None = None,
        payload: dict[str, str | None] | None = None,
    ) -> httpx.Response:
        if isinstance(http_timeout, tuple):
            connect, read = http_timeout
            timeout = httpx.Timeout(read, connect=connect)
        else:
            timeout = httpx.Timeout(http_timeout)
        return client.request(
            method,
            self.base_url + path,
            headers={"Authorization": f"Bearer {token}"},
            auth=None,
            # A redirect can forward task contents even if HTTPX strips the bearer token.
            follow_redirects=False,
            timeout=timeout,
            params=params,
            json=payload,
        ).raise_for_status()

    def _list_items(
        self,
        path: str,
        token: str,
        http_timeout: float | tuple[float, float],
        **params: str | int | bool,
    ) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        with self._client() as client:
            while True:
                data = self._request(client, "GET", path, token, http_timeout, params=params).json()
                items.extend(data.get("items", []))
                page_token = data.get("nextPageToken")
                if not page_token:
                    return items
                params["pageToken"] = page_token

    def list_task_lists(self, token: str, http_timeout: float | tuple[float, float] = 15.0) -> list[TaskList]:
        items = self._list_items("/users/@me/lists", token, http_timeout)
        return [TaskList(id=item["id"], title=item.get("title") or "") for item in items]

    def list_tasks(self, token: str, list_id: str, http_timeout: float | tuple[float, float] = 15.0) -> list[Task]:
        """List tasks, including completed tasks that were cleared from the list.

        Google requires hidden tasks to be included to see completions made in
        its web and mobile apps. This also brings back cleared completed tasks.
        """
        items = self._list_items(
            _task_path(list_id), token, http_timeout, maxResults=100, showCompleted=True, showHidden=True
        )
        return [_to_task(item, list_id) for item in items]

    def create_task(
        self, token: str, list_id: str, request: TaskCreateRequest, http_timeout: float | tuple[float, float] = 15.0
    ) -> Task:
        payload: dict[str, str | None] = {"title": request.title}
        if request.notes is not None:
            payload["notes"] = request.notes
        if request.due is not None:
            payload["due"] = _format_due(request.due)
        path = _task_path(list_id)
        with self._client() as client:
            response = self._request(client, "POST", path, token, http_timeout, payload=payload)
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
        payload: dict[str, str | None] = {}
        if request.title is not UNSET:
            payload["title"] = request.title
        if request.notes is not UNSET:
            payload["notes"] = request.notes
        if request.due is not UNSET:
            payload["due"] = None if request.due is None else _format_due(request.due)
        return self._patch_task(token, list_id, task_id, http_timeout, payload)

    def complete_task(
        self, token: str, list_id: str, task_id: str, http_timeout: float | tuple[float, float] = 15.0
    ) -> Task:
        """Send the same completion PATCH even if the task is already completed."""
        return self._patch_task(token, list_id, task_id, http_timeout, {"status": "completed"})

    def _patch_task(
        self,
        token: str,
        list_id: str,
        task_id: str,
        http_timeout: float | tuple[float, float],
        payload: dict[str, str | None],
    ) -> Task:
        path = _task_path(list_id, task_id)
        with self._client() as client:
            response = self._request(client, "PATCH", path, token, http_timeout, payload=payload)
        return _to_task(response.json(), list_id)

    def delete_task(
        self, token: str, list_id: str, task_id: str, http_timeout: float | tuple[float, float] = 15.0
    ) -> None:
        path = _task_path(list_id, task_id)
        with self._client() as client:
            self._request(client, "DELETE", path, token, http_timeout)
