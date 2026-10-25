"""What `todo_write` takes, and the one rule it keeps.

Its parameter is `todos`. A top-level argument named "items" shadows the `.items()` method of a map
in the minijinja chat templates some hosts use (Together GLM-5.2, 2026-07-21), and every request
that replays the call is a 400. "items" is still taken when a model writes it anyway, and never
appears in the schema again.
"""

from harness.tools.todo import TodoList, todo_tools
from harness.tools.todo.schema import TODO_SCHEMA


def _writer():
    todo = TodoList()
    (write,) = todo_tools(todo)
    return write, todo


def _task(content, status):
    return {"content": content, "status": status}


def test_the_schema_asks_for_todos_and_never_items():
    parameters = TODO_SCHEMA["function"]["parameters"]
    assert "todos" in parameters["properties"] and "items" not in parameters["properties"]
    assert parameters["required"] == ["todos"]


def test_todos_is_written_and_given_back():
    write, todo = _writer()
    written = write(todos=[_task("a", "in_progress")])
    assert todo.items == [_task("a", "in_progress")]
    assert written == {"count": 1, "todos": [_task("a", "in_progress")]}


def test_items_from_a_model_that_writes_it_anyway_still_works():
    write, todo = _writer()
    assert write(items=[_task("b", "done")])["count"] == 1
    assert todo.items == [_task("b", "done")]


def test_the_todo_list_keeps_one_item_in_progress():
    """One at a time (lecture 7): the description asked for exactly one item in progress and
    nothing held to it. Two are refused, and the list before stays."""
    write, todo = _writer()
    assert write(todos=[_task("a", "in_progress"), _task("b", "pending")])["count"] == 2
    refused = write(todos=[_task("a", "in_progress"), _task("b", "in_progress")])
    assert "exactly one item may be in_progress; 2 are" in refused["error"]
    assert [item["status"] for item in todo.items] == ["in_progress", "pending"]
    assert write(todos=[_task("a", "done"), _task("b", "pending")])["count"] == 2  # none in progress is fine
