from dataclasses import FrozenInstanceError
from datetime import date, datetime, timezone

import pytest

from workspace_suite.models import UNSET, Task, TaskCreateRequest, TaskList, TaskUpdateRequest


def test_task_models() -> None:
    task_list = TaskList(id="list-1", title="Home")
    create = TaskCreateRequest(title="Water plants", notes="Kitchen", due=date(2026, 11, 16))
    completed_at = datetime(2026, 11, 15, 9, tzinfo=timezone.utc)
    task = Task(
        id="task-1",
        title=create.title,
        list_id=task_list.id,
        status="completed",
        notes=create.notes,
        due=create.due,
        completed_at=completed_at,
    )
    update = TaskUpdateRequest(notes="Balcony too")

    assert task_list.title == "Home"
    assert task.id == "task-1"
    assert task.list_id == task_list.id
    assert task.title == "Water plants"
    assert task.notes == "Kitchen"
    assert task.due == date(2026, 11, 16)
    assert task.status == "completed"
    assert task.completed_at == completed_at
    assert update.notes == "Balcony too"
    assert update.title is UNSET
    assert update.due is UNSET


def test_task_optional_fields_default_to_none() -> None:
    task = Task(id="task-1", title="Water plants", list_id="list-1", status="open")

    assert task.notes is None
    assert task.due is None
    assert task.completed_at is None


def test_task_is_frozen() -> None:
    task = Task(id="task-1", title="Water plants", list_id="list-1", status="open")

    with pytest.raises(FrozenInstanceError):
        setattr(task, "title", "Water balcony plants")


def test_task_update_distinguishes_omission_from_clearing() -> None:
    omitted = TaskUpdateRequest()
    clear = TaskUpdateRequest(notes=None, due=None)

    assert omitted.notes is UNSET
    assert omitted.due is UNSET
    assert clear.notes is None
    assert clear.due is None


def test_task_update_rejects_empty_notes() -> None:
    with pytest.raises(ValueError, match="Use None to clear notes"):
        TaskUpdateRequest(notes="")


@pytest.mark.parametrize("request_type", [TaskCreateRequest, TaskUpdateRequest])
@pytest.mark.parametrize("title", ["", None])
def test_task_write_rejects_invalid_title(
    request_type: type[TaskCreateRequest] | type[TaskUpdateRequest], title: str | None
) -> None:
    with pytest.raises(ValueError, match="Task title must be a nonempty string"):
        request_type(title=title)  # type: ignore[arg-type]
