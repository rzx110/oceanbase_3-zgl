"""Task-board listing service, before the status-filter development task."""

from dataclasses import dataclass
from collections.abc import Sequence


@dataclass(frozen=True)
class Task:
    task_id: str
    title: str
    status: str


def list_tasks(tasks: Sequence[Task], *, status: str | None = None) -> list[Task]:
    """Return tasks in input order. Status filtering is not implemented yet."""
    return list(tasks)
