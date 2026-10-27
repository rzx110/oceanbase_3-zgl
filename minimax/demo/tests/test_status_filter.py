"""Identical target tests for both code snapshots; a before failure is intentional."""
import pytest

from taskboard.service import Task, list_tasks


@pytest.fixture
def tasks():
    return [Task("3", "review", "done"), Task("1", "spec", "todo"),
            Task("4", "code", "doing"), Task("2", "test", "todo")]


@pytest.mark.parametrize("status,ids", [("todo", ["1", "2"]), ("doing", ["4"]), ("done", ["3"])])
def test_exact_status_filter(tasks, status, ids):
    assert [task.task_id for task in list_tasks(tasks, status=status)] == ids


@pytest.mark.parametrize("status", ["", "TODO", " todo", "todo ", "to", "cancelled", 0, [], {}])
def test_invalid_status_is_value_error_even_for_empty_input(status):
    with pytest.raises(ValueError):
        list_tasks([], status=status)


def test_no_match_is_empty():
    assert list_tasks([Task("1", "spec", "done")], status="todo") == []


def test_filter_keeps_order_duplicates_identity_and_input(tasks):
    original = tasks + [tasks[1]]
    snapshot = tuple(original)
    result = list_tasks(original, status="todo")
    assert result == [tasks[1], tasks[3], tasks[1]]
    assert result[0] is original[1] and result[2] is original[1]
    assert tuple(original) == snapshot


def test_filter_accepts_tuple(tasks):
    assert list_tasks(tuple(tasks), status="done") == [tasks[0]]
