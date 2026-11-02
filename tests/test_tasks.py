from chinuuu.database import LocalStore


def test_local_tasks_persist_and_transition_status(tmp_path):
    db_path = tmp_path / "chinuuu.sqlite3"
    store = LocalStore(db_path)
    task_id = store.add_task("Finish Chinuuu v1.0")
    assert store.list_tasks(include_done=False)[0]["title"] == "Finish Chinuuu v1.0"

    assert store.complete_task(task_id) is True
    assert store.complete_task(task_id) is False
    assert store.list_tasks(include_done=False) == []
    assert store.list_tasks(include_done=True)[0]["status"] == "done"

    reopened = LocalStore(db_path)
    assert reopened.list_tasks(include_done=True)[0]["id"] == task_id


def test_task_validation_and_deletion(tmp_path):
    store = LocalStore(tmp_path / "db.sqlite3")
    try:
        store.add_task("   ")
    except ValueError:
        pass
    else:
        raise AssertionError("Blank task title should be rejected")
    task_id = store.add_task("Review project")
    assert store.delete_task(task_id) is True
    assert store.delete_task(task_id) is False


def test_natural_language_task_tools_are_deterministic_and_verified(tmp_path):
    from chinuuu.assistant import _handle_deterministic_workspace_request
    from chinuuu.tools import ToolRegistry

    store = LocalStore(tmp_path / "db.sqlite3")
    registry = ToolRegistry(tmp_path / "workspace", task_store=store)

    added = _handle_deterministic_workspace_request(
        "add a task to finish the Chinuuu project", registry, None
    )
    assert "Task saved: #1" in added
    listed = _handle_deterministic_workspace_request("list my tasks", registry, None)
    assert "#1 [open] finish the Chinuuu project" in listed

    completed = _handle_deterministic_workspace_request("complete task 1", registry, None)
    assert "marked complete and verified" in completed
    assert store.get_task(1)["status"] == "done"


def test_natural_language_task_deletion_requires_confirmation(tmp_path):
    from chinuuu.assistant import _handle_deterministic_workspace_request
    from chinuuu.tools import ToolRegistry

    store = LocalStore(tmp_path / "db.sqlite3")
    task_id = store.add_task("Delete me only after approval")
    registry = ToolRegistry(tmp_path / "workspace", task_store=store)

    denied = _handle_deterministic_workspace_request(
        f"delete task {task_id}", registry, lambda *_: False
    )
    assert denied.startswith("Not executed:")
    assert store.get_task(task_id) is not None

    approved = _handle_deterministic_workspace_request(
        f"delete task {task_id}", registry, lambda name, args: name == "delete_local_task"
    )
    assert "SQLite verification confirms it is absent" in approved
    assert store.get_task(task_id) is None
