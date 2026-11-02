from __future__ import annotations

from chinuuu.control_center import ControlCenterWindow
from chinuuu.database import LocalStore


class FakeTree:
    def __init__(self):
        self.rows = {}
    def delete(self, *items):
        for item in items:
            self.rows.pop(item, None)
    def get_children(self):
        return tuple(self.rows)
    def insert(self, _parent, _index, iid=None, values=()):
        key = str(iid) if iid is not None else str(len(self.rows) + 1)
        self.rows[key] = values


def test_control_center_fill_replaces_stale_rows():
    tree = FakeTree()
    tree.insert("", "end", iid="old", values=("stale",))
    ControlCenterWindow._fill(tree, [{"id": 7, "title": "Review project"}], ("id", "title"))
    assert tree.rows == {"7": ("7", "Review project")}


def test_control_center_reads_tasks_memories_and_activity(tmp_path):
    store = LocalStore(tmp_path / "chinuuu.sqlite3")
    task_id = store.add_task("Review Chinuuu release")
    store.add_memory("Explain technical concepts", category="preference")
    run_id = "test-run-control-center"
    store.begin_agent_run(run_id, "Inspect local project")
    store.finish_agent_run(run_id, "completed", "Inspection completed")
    store.record_tool_audit(run_id, "system_report", "{}", "success")

    assert store.list_tasks()[0]["id"] == task_id
    assert store.list_memories()[0]["content"] == "Explain technical concepts"
    assert store.recent_agent_runs(1)[0]["status"] == "completed"
    assert store.recent_tool_audit(1)[0]["tool_name"] == "system_report"
