"""Original task-board listing behaviors which the change must preserve."""
from taskboard.service import Task, list_tasks


def test_default_preserves_input_order():
    tasks = [Task("9", "later", "done"), Task("1", "first", "todo")]
    assert list_tasks(tasks) == tasks
    assert list_tasks(tasks, status=None) == tasks


def test_default_returns_fresh_list_without_mutation():
    item = Task("1", "spec", "todo")
    tasks = [item, item]
    result = list_tasks(tasks)
    assert result == tasks and result is not tasks
    assert result[0] is item and result[1] is item
    result.clear()
    assert tasks == [item, item]


def test_default_accepts_tuple_and_empty():
    task = Task("1", "spec", "todo")
    assert list_tasks((task,)) == [task]
    assert list_tasks([]) == []
    assert list_tasks(()) == []


def test_existing_unknown_task_status_is_preserved_without_filter():
    task = Task("1", "old imported item", "archived")
    assert list_tasks([task]) == [task]
