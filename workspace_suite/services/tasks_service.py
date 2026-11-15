from ..models import Task, TaskCreateRequest, TaskList, TaskUpdateRequest
from ..providers.tasks_base import TasksProvider


class TasksService:
    def __init__(self, provider: TasksProvider):
        self.provider = provider

    def list_task_lists(self, *, token: str) -> list[TaskList]:
        return self.provider.list_task_lists(token)

    def list_tasks(self, *, token: str, list_id: str) -> list[Task]:
        return self.provider.list_tasks(token, list_id)

    def create_task(self, *, token: str, list_id: str, request: TaskCreateRequest) -> Task:
        return self.provider.create_task(token, list_id, request)

    def update_task(self, *, token: str, list_id: str, task_id: str, request: TaskUpdateRequest) -> Task:
        return self.provider.update_task(token, list_id, task_id, request)

    def complete_task(self, *, token: str, list_id: str, task_id: str) -> Task:
        return self.provider.complete_task(token, list_id, task_id)

    def delete_task(self, *, token: str, list_id: str, task_id: str) -> None:
        self.provider.delete_task(token, list_id, task_id)
