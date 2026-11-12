from typing import Protocol

from ..models import Task, TaskCreateRequest, TaskList, TaskUpdateRequest


class TasksProvider(Protocol):
    """Operate on tasks using the caller's token and opaque provider IDs.

    Notes in requests and results are plain text. List methods collect the
    provider's pages. The timeout applies to each HTTP request. HTTP error
    responses raise httpx.HTTPStatusError so callers can identify rejected
    credentials (401).
    """

    def list_task_lists(self, token: str, http_timeout: float | tuple[float, float] = 15.0) -> list[TaskList]:
        raise NotImplementedError

    def list_tasks(self, token: str, list_id: str, http_timeout: float | tuple[float, float] = 15.0) -> list[Task]:
        raise NotImplementedError

    def create_task(
        self, token: str, list_id: str, request: TaskCreateRequest, http_timeout: float | tuple[float, float] = 15.0
    ) -> Task:
        raise NotImplementedError

    def update_task(
        self,
        token: str,
        list_id: str,
        task_id: str,
        request: TaskUpdateRequest,
        http_timeout: float | tuple[float, float] = 15.0,
    ) -> Task:
        """Apply the supplied changes, including explicit clears.

        Raise ValueError if all fields are UNSET. Reject unsupported clears
        before writing any changes.
        """
        raise NotImplementedError

    def complete_task(
        self, token: str, list_id: str, task_id: str, http_timeout: float | tuple[float, float] = 15.0
    ) -> Task:
        raise NotImplementedError

    def delete_task(
        self, token: str, list_id: str, task_id: str, http_timeout: float | tuple[float, float] = 15.0
    ) -> None:
        raise NotImplementedError
