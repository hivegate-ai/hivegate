import logging
from collections.abc import Callable
from dataclasses import asdict
from datetime import date
from inspect import signature
from typing import Any

import httpx
from agno.tools.function import Function
from pydantic import BaseModel, ConfigDict, Field, field_validator

from workspace_suite import ProviderConfig, TasksService
from workspace_suite.models import Task, TaskCreateRequest, TaskUpdateRequest
from workspace_suite.providers.google_tasks import GoogleTasksProvider
from workspace_suite.providers.microsoft_todo import MicrosoftTodoProvider
from workspace_suite.providers.tasks_base import TasksProvider

from .base import BaseToolkit

logger = logging.getLogger(__name__)

GOOGLE_TASKS = "google_tasks"
MICROSOFT_TODO = "microsoft_todo"
_TASKS_PROVIDERS: dict[str, Callable[[ProviderConfig], TasksProvider]] = {
    GOOGLE_TASKS: GoogleTasksProvider,
    MICROSOFT_TODO: MicrosoftTodoProvider,
}
TASKS_WRITE_TOOLS = ("create_task", "update_task", "complete_task", "delete_task")


class _TaskFields(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    notes: str | None = None
    due: str | None = Field(default=None, description="Calendar date in YYYY-MM-DD format")

    @field_validator("due")
    @classmethod
    def validate_due(cls, value: str | None) -> str | None:
        if value is not None and date.fromisoformat(value).isoformat() != value:
            raise ValueError("Use YYYY-MM-DD for a task due date.")
        return value


class _CreateFields(_TaskFields):
    title: str = Field(min_length=1)


class _UpdateFields(_TaskFields):
    # Keep the omission placeholder out of the tool schema.
    title: str = Field(default_factory=str, min_length=1)


def _task_result(task: Task) -> dict[str, Any]:
    result = asdict(task)
    result["due"] = task.due.isoformat() if task.due is not None else None
    result["completed_at"] = task.completed_at.isoformat() if task.completed_at is not None else None
    return result


def _valid_id(value: object) -> bool:
    return isinstance(value, str) and value not in ("", ".", "..")


class TasksToolkit(BaseToolkit):
    """List and manage tasks using the user's connected task account."""

    # Writes rely on the native chat approval pause, so the direct toolkit routes refuse Tasks.
    allow_direct_invocation = False

    @classmethod
    def provider_candidates(
        cls, user_id: str, fetch_token_func: Callable[[str, str], str | None]
    ) -> list["TasksToolkit"]:
        """Return the connected providers in selection order, then the connection prompt."""
        providers = [cls(user_id, key, fetch_token_func=fetch_token_func) for key in _TASKS_PROVIDERS]
        return [*providers, cls(user_id, "tasks", auth=False)]

    def __init__(
        self,
        user_id: str,
        service_name: str,
        auth: bool = True,
        fetch_token_func: Callable[[str, str], str | None] | None = None,
    ) -> None:
        super().__init__(user_id, service_name, auth, fetch_token_func)
        if not auth:
            self.stop_after_tool_call_tools = ["tasks_auth_required"]
            self.register(self.tasks_auth_required)
            return

        self.requires_confirmation_tools = list(TASKS_WRITE_TOOLS)
        schemas = {
            "list_id": {"type": "string", "description": "Opaque task list ID"},
            "task_id": {"type": "string", "description": "Opaque task ID"},
            "content": _CreateFields.model_json_schema(),
            "changes": _UpdateFields.model_json_schema(),
        }
        for method in (
            self.list_task_lists,
            self.list_tasks,
            self.create_task,
            self.update_task,
            self.complete_task,
            self.delete_task,
        ):
            self._register_task(method, schemas)

    def _initialize_service(self) -> None:
        self.tasks_service: TasksService | None = None
        if not self.auth:
            return
        factory = _TASKS_PROVIDERS.get(self.service_name)
        if factory is None:
            raise ValueError("Unsupported integration for an authenticated Tasks toolkit.")
        self.tasks_service = TasksService(factory(ProviderConfig()))

    def _register_task(self, method: Callable[..., dict[str, Any]], schemas: dict[str, Any]) -> None:
        properties = {name: schemas[name] for name in signature(method).parameters}

        def entrypoint(**arguments: Any) -> dict[str, Any]:
            if arguments.keys() != properties.keys():
                return self.error_card("Invalid task arguments.")
            return method(**arguments)

        # Keep schema validation inside the toolkit, where errors can omit raw arguments.
        self.functions[method.__name__] = Function(
            name=method.__name__,
            description=method.__doc__,
            parameters={
                "type": "object",
                "properties": properties,
                "required": list(properties),
                "additionalProperties": False,
            },
            entrypoint=entrypoint,
            skip_entrypoint_processing=True,
            requires_confirmation=method.__name__ in self.requires_confirmation_tools,
        )

    def _perform(
        self, action: str, operation: Callable[[TasksService, str], dict[str, Any]], *, write: bool = False
    ) -> dict[str, Any]:
        from . import token_cache

        if self.tasks_service is None:
            return self.tasks_auth_required()
        token = self._prepare_auth()
        if not token:
            return self.tasks_auth_required()
        try:
            return operation(self.tasks_service, token)
        except httpx.HTTPStatusError as error:
            status = error.response.status_code
            if status == 401:
                token_cache.invalidate(self.service_name, self.user_id)
                return self.tasks_auth_required()
            if status == 403:
                return self.error_card(f"Permission denied while attempting to {action}. Check the Tasks connection.")
            return self.error_card(f"Could not {action} (HTTP {status}).")
        except Exception as exc:
            logger.debug("Task operation failed with %s", type(exc).__name__)
            message = f"Could not {action}."
            if write:
                message += " Check the task list before trying again."
            return self.error_card(message)

    def tasks_auth_required(self) -> dict[str, Any]:
        """Show how to connect or reconnect a task account."""
        return {
            "card": "tasks-auth-required",
            "message": "Connect or reconnect your Tasks account." if self.auth else "Connect a Tasks account.",
            "context": {
                "token_valid": False,
                "service_name": self.service_name if self.auth else None,
                "available_services": list(_TASKS_PROVIDERS),
            },
        }

    def list_task_lists(self) -> dict[str, Any]:
        """List all task lists from the connected account."""
        return self._perform(
            "list task lists",
            lambda service, token: {"lists": [asdict(item) for item in service.list_task_lists(token=token)]},
        )

    def list_tasks(self, list_id: str) -> dict[str, Any]:
        """List all open and completed tasks in a list."""
        if not _valid_id(list_id):
            return self.error_card("Invalid task list ID.")
        return self._perform(
            "list tasks",
            lambda service, token: {
                "tasks": [_task_result(task) for task in service.list_tasks(token=token, list_id=list_id)]
            },
        )

    def create_task(self, list_id: str, content: dict[str, Any]) -> dict[str, Any]:
        """Create a task in a list. Supply a title and optionally notes and a due date."""
        invalid = "Invalid task creation fields. Supply a list ID, a title and an optional YYYY-MM-DD date."
        if not _valid_id(list_id):
            return self.error_card(invalid)
        try:
            fields = _CreateFields.model_validate(content)
            request = TaskCreateRequest(
                fields.title, fields.notes, date.fromisoformat(fields.due) if fields.due else None
            )
        except ValueError:
            return self.error_card(invalid)
        return self._perform(
            "create the task",
            lambda service, token: _task_result(service.create_task(token=token, list_id=list_id, request=request)),
            write=True,
        )

    def update_task(self, list_id: str, task_id: str, changes: dict[str, Any]) -> dict[str, Any]:
        """Update supplied fields only. Use null to clear notes or due. Omit fields to keep them."""
        invalid = "Invalid task update. Supply both IDs and at least one valid change."
        if not (_valid_id(list_id) and _valid_id(task_id)):
            return self.error_card(invalid)
        try:
            fields = _UpdateFields.model_validate(changes).model_dump(exclude_unset=True)
            if fields.get("due") is not None:
                fields["due"] = date.fromisoformat(fields["due"])
            request = TaskUpdateRequest(**fields)
            request.validate_changes()
        except ValueError:
            return self.error_card(invalid)
        return self._perform(
            "update the task",
            lambda service, token: _task_result(
                service.update_task(token=token, list_id=list_id, task_id=task_id, request=request)
            ),
            write=True,
        )

    def complete_task(self, list_id: str, task_id: str) -> dict[str, Any]:
        """Mark a task as complete and return the updated task."""
        if not (_valid_id(list_id) and _valid_id(task_id)):
            return self.error_card("Invalid task or list ID.")
        return self._perform(
            "complete the task",
            lambda service, token: _task_result(service.complete_task(token=token, list_id=list_id, task_id=task_id)),
            write=True,
        )

    def delete_task(self, list_id: str, task_id: str) -> dict[str, Any]:
        """Delete a task from a list."""
        if not (_valid_id(list_id) and _valid_id(task_id)):
            return self.error_card("Invalid task or list ID.")

        def delete(service: TasksService, token: str) -> dict[str, Any]:
            service.delete_task(token=token, list_id=list_id, task_id=task_id)
            return {"deleted": True, "list_id": list_id, "task_id": task_id}

        return self._perform("delete the task", delete, write=True)
