# Task-board status filtering — effective specification v1

## REQ-STATUS-01: exact status filter
`taskboard.service.list_tasks(tasks, *, status=None)` accepts `todo`, `doing`, or
`done`. When status is supplied, return only Task objects whose status exactly
matches it. Do not convert case or trim input.

## REQ-STATUS-02: invalid status
Any other non-None status, including the empty string, upper-case `TODO`, leading
or trailing whitespace, or a value of a different type, must raise ValueError.
Validation applies even when the task sequence is empty. Do not accept substrings.

## REQ-COMPAT-01: default and ordering
When status is None, return all tasks in the original input order. Always return
a new list. Preserve duplicates and Task object identity.

## REQ-COMPAT-02: input immutability
Do not mutate the input sequence or its Task objects. Accept tuple inputs and
empty sequences. Keep the Task dataclass and public function signature compatible.

## Verification sources
Feature: `demo/tests/test_status_filter.py`.
Compatibility: `demo/tests/test_regression.py`.
Implementation: `demo/before/taskboard/service.py`, symbol `list_tasks`.
The same test files must be used against the before and after snapshots.
