from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk
from typing import Any

from .database import LocalStore


class ControlCenterWindow:
    """Local, read-mostly dashboard for tasks, approved memory and agent activity."""

    def __init__(self, parent: tk.Misc, store: LocalStore) -> None:
        self.parent = parent
        self.store = store
        self.window = tk.Toplevel(parent)
        self.window.title("Chinuuu — Control Center")
        self.window.geometry("980x650")
        self.window.minsize(760, 480)
        self.window.configure(bg="#10151e")
        self._build()
        self.refresh_all()
        self.window.transient(parent)

    def _build(self) -> None:
        style = ttk.Style(self.window)
        style.configure("Control.TLabel", font=("Segoe UI", 10))
        header = ttk.Frame(self.window, padding=12)
        header.pack(fill="x")
        ttk.Label(header, text="CONTROL CENTER", font=("Segoe UI Semibold", 16)).pack(side="left")
        ttk.Button(header, text="Refresh all", command=self.refresh_all).pack(side="right")
        self.tabs = ttk.Notebook(self.window)
        self.tabs.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        self.tasks_tab = ttk.Frame(self.tabs, padding=12)
        self.memory_tab = ttk.Frame(self.tabs, padding=12)
        self.activity_tab = ttk.Frame(self.tabs, padding=12)
        self.tabs.add(self.tasks_tab, text="Tasks")
        self.tabs.add(self.memory_tab, text="Memory review")
        self.tabs.add(self.activity_tab, text="Agent activity")
        self._build_tasks()
        self._build_memory()
        self._build_activity()

    def _tree(self, parent: tk.Misc, columns: tuple[str, ...], headings: tuple[str, ...], widths: tuple[int, ...]) -> ttk.Treeview:
        tree = ttk.Treeview(parent, columns=columns, show="headings", height=6)
        for col, heading, width in zip(columns, headings, widths):
            tree.heading(col, text=heading)
            tree.column(col, width=width, anchor="w")
        scroll = ttk.Scrollbar(parent, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scroll.set)
        tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        return tree

    def _build_tasks(self) -> None:
        entry_row = ttk.Frame(self.tasks_tab)
        entry_row.pack(fill="x", pady=(0, 10))
        self.task_entry = ttk.Entry(entry_row)
        self.task_entry.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.task_entry.bind("<Return>", lambda _event: self.add_task())
        ttk.Button(entry_row, text="Add task", command=self.add_task).pack(side="left")
        actions = ttk.Frame(self.tasks_tab)
        actions.pack(fill="x", pady=(0, 8))
        ttk.Button(actions, text="Mark selected complete", command=self.complete_task).pack(side="left")
        ttk.Button(actions, text="Refresh", command=self.refresh_tasks).pack(side="left", padx=8)
        self.task_tree = self._tree(self.tasks_tab, ("id", "status", "title", "created"), ("ID", "Status", "Task", "Created"), (55, 90, 520, 170))

    def _build_memory(self) -> None:
        self.memory_tree = self._tree(self.memory_tab, ("id", "category", "confidence", "content"), ("ID", "Category", "Confidence", "Approved memory"), (55, 110, 95, 590))
        actions = ttk.Frame(self.memory_tab)
        actions.pack(fill="x", pady=(10, 0))
        ttk.Button(actions, text="Approve selected suggestion", command=self.approve_candidate).pack(side="left")
        ttk.Button(actions, text="Reject selected suggestion", command=self.reject_candidate).pack(side="left", padx=8)
        ttk.Button(actions, text="Refresh", command=self.refresh_memory).pack(side="left")
        self.candidate_label = ttk.Label(self.memory_tab, text="Pending suggestions appear below approved memories.")
        self.candidate_label.pack(anchor="w", pady=(8, 0))
        self.candidate_tree = self._tree(self.memory_tab, ("id", "category", "confidence", "content"), ("Suggestion ID", "Category", "Confidence", "Candidate memory"), (90, 110, 95, 555))

    def _build_activity(self) -> None:
        ttk.Label(self.activity_tab, text="Recent agent runs", font=("Segoe UI Semibold", 11)).pack(anchor="w")
        self.runs_tree = self._tree(self.activity_tab, ("status", "started", "objective", "summary"), ("Status", "Started", "Objective", "Summary"), (100, 160, 300, 360))
        ttk.Label(self.activity_tab, text="Recent tool audit", font=("Segoe UI Semibold", 11)).pack(anchor="w", pady=(12, 4))
        self.audit_tree = self._tree(self.activity_tab, ("tool", "outcome", "time", "error"), ("Tool", "Outcome", "Time", "Error"), (180, 100, 160, 480))

    @staticmethod
    def _fill(tree: ttk.Treeview, rows: list[dict[str, Any]], keys: tuple[str, ...]) -> None:
        tree.delete(*tree.get_children())
        for row in rows:
            values = tuple(str(row.get(key, "") or "") for key in keys)
            tree.insert("", "end", iid=str(row.get("id", "")) or None, values=values)

    def refresh_all(self) -> None:
        self.refresh_tasks()
        self.refresh_memory()
        self.refresh_activity()

    def refresh_tasks(self) -> None:
        self._fill(self.task_tree, self.store.list_tasks(), ("id", "status", "title", "created_at"))

    def add_task(self) -> None:
        title = self.task_entry.get().strip()
        if not title:
            return
        try:
            self.store.add_task(title)
        except ValueError as exc:
            messagebox.showwarning("Task not added", str(exc), parent=self.window)
            return
        self.task_entry.delete(0, "end")
        self.refresh_tasks()

    def complete_task(self) -> None:
        selected = self.task_tree.selection()
        if not selected:
            messagebox.showinfo("Select a task", "Choose a task first.", parent=self.window)
            return
        if self.store.complete_task(int(selected[0])):
            self.refresh_tasks()

    def refresh_memory(self) -> None:
        memories = self.store.list_memories()
        self._fill(self.memory_tree, memories, ("id", "category", "confidence", "content"))
        candidates = self.store.list_candidates("pending")
        self._fill(self.candidate_tree, candidates, ("id", "category", "confidence", "content"))
        self.candidate_label.configure(text=f"{len(candidates)} pending suggestion(s) — approve only facts/preferences you want retained.")

    def _candidate_action(self, approve: bool) -> None:
        selected = self.candidate_tree.selection()
        if not selected:
            messagebox.showinfo("Select a suggestion", "Choose a pending suggestion first.", parent=self.window)
            return
        candidate_id = int(selected[0])
        action = "approve" if approve else "reject"
        if not messagebox.askyesno("Confirm memory review", f"{action.title()} suggestion #{candidate_id}?", parent=self.window):
            return
        (self.store.approve_candidate if approve else self.store.reject_candidate)(candidate_id)
        self.refresh_memory()

    def approve_candidate(self) -> None:
        self._candidate_action(True)

    def reject_candidate(self) -> None:
        self._candidate_action(False)

    def refresh_activity(self) -> None:
        self._fill(self.runs_tree, self.store.recent_agent_runs(30), ("status", "started_at", "objective", "summary"))
        self._fill(self.audit_tree, self.store.recent_tool_audit(50), ("tool_name", "outcome", "started_at", "error"))
