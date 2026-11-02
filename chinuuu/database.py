from __future__ import annotations

import sqlite3

from .privacy import redact_text
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


class LocalStore:
    """SQLite persistence for chat, approved memories and learning evidence."""

    def __init__(self, db_path: Path = Path("data/chinuuu.sqlite3")) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _initialize(self) -> None:
        with self._connect() as con:
            con.execute(
                "CREATE TABLE IF NOT EXISTS messages ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "role TEXT NOT NULL CHECK(role IN ('user','assistant')), "
                "content TEXT NOT NULL, created_at TEXT NOT NULL)"
            )
            con.execute(
                "CREATE TABLE IF NOT EXISTS memories ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "content TEXT NOT NULL UNIQUE, "
                "category TEXT NOT NULL DEFAULT 'preference', "
                "source TEXT NOT NULL DEFAULT 'explicit', "
                "confidence REAL NOT NULL DEFAULT 1.0, "
                "created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
            )
            # Safe migration for databases created by v0.1.0/v0.1.1.
            columns = {row["name"] for row in con.execute("PRAGMA table_info(memories)")}
            if "source" not in columns:
                con.execute("ALTER TABLE memories ADD COLUMN source TEXT NOT NULL DEFAULT 'explicit'")
            if "confidence" not in columns:
                con.execute("ALTER TABLE memories ADD COLUMN confidence REAL NOT NULL DEFAULT 1.0")
            if "updated_at" not in columns:
                con.execute("ALTER TABLE memories ADD COLUMN updated_at TEXT NOT NULL DEFAULT ''")
                con.execute("UPDATE memories SET updated_at = created_at WHERE updated_at = ''")

            con.execute(
                "CREATE TABLE IF NOT EXISTS memory_candidates ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "content TEXT NOT NULL, category TEXT NOT NULL, "
                "source TEXT NOT NULL, confidence REAL NOT NULL, "
                "evidence_count INTEGER NOT NULL DEFAULT 1, "
                "evidence_summary TEXT NOT NULL DEFAULT '', "
                "status TEXT NOT NULL DEFAULT 'pending' "
                "CHECK(status IN ('pending','approved','rejected')), "
                "created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
            )
            con.execute(
                "CREATE INDEX IF NOT EXISTS idx_candidates_status ON memory_candidates(status, id)"
            )
            con.execute(
                "CREATE TABLE IF NOT EXISTS behavior_observations ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "signal_key TEXT NOT NULL, category TEXT NOT NULL, "
                "evidence TEXT NOT NULL, created_at TEXT NOT NULL)"
            )
            con.execute(
                "CREATE INDEX IF NOT EXISTS idx_observations_signal_time "
                "ON behavior_observations(signal_key, created_at)"
            )
            con.execute(
                "CREATE TABLE IF NOT EXISTS tasks ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "title TEXT NOT NULL, "
                "status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','done')), "
                "created_at TEXT NOT NULL, completed_at TEXT)"
            )
            con.execute("CREATE INDEX IF NOT EXISTS idx_tasks_status_id ON tasks(status, id)")

            # Additive v2 persistence: events, reminders, contacts, research index,
            # agent execution audit and durable workflow checkpoints.
            con.execute(
                "CREATE TABLE IF NOT EXISTS reminders ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, "
                "due_at TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending' "
                "CHECK(status IN ('pending','fired','dismissed')), "
                "created_at TEXT NOT NULL, fired_at TEXT)"
            )
            con.execute("CREATE INDEX IF NOT EXISTS idx_reminders_due ON reminders(status, due_at)")
            con.execute(
                "CREATE TABLE IF NOT EXISTS calendar_events ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, "
                "starts_at TEXT NOT NULL, ends_at TEXT, location TEXT NOT NULL DEFAULT '', "
                "description TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL)"
            )
            con.execute("CREATE INDEX IF NOT EXISTS idx_calendar_starts ON calendar_events(starts_at)")
            con.execute(
                "CREATE TABLE IF NOT EXISTS contacts ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, "
                "normalized_name TEXT NOT NULL, phone_e164 TEXT NOT NULL, "
                "email TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, "
                "UNIQUE(normalized_name, phone_e164))"
            )
            con.execute("CREATE INDEX IF NOT EXISTS idx_contacts_name ON contacts(normalized_name)")
            con.execute(
                "CREATE TABLE IF NOT EXISTS documents ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, path TEXT NOT NULL UNIQUE, "
                "sha256 TEXT NOT NULL, title TEXT NOT NULL, indexed_at TEXT NOT NULL, "
                "chunk_count INTEGER NOT NULL DEFAULT 0)"
            )
            con.execute(
                "CREATE TABLE IF NOT EXISTS document_chunks ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, document_id INTEGER NOT NULL "
                "REFERENCES documents(id) ON DELETE CASCADE, chunk_index INTEGER NOT NULL, "
                "page_number INTEGER, content TEXT NOT NULL, "
                "UNIQUE(document_id, chunk_index))"
            )
            try:
                con.execute(
                    "CREATE VIRTUAL TABLE IF NOT EXISTS document_chunks_fts USING "
                    "fts5(content, path UNINDEXED, title UNINDEXED, chunk_id UNINDEXED, "
                    "tokenize='porter unicode61')"
                )
            except sqlite3.OperationalError:
                # SQLite builds without FTS5 still support the LIKE fallback in knowledge.py.
                pass
            con.execute(
                "CREATE TABLE IF NOT EXISTS document_embeddings ("
                "chunk_id INTEGER PRIMARY KEY REFERENCES document_chunks(id) ON DELETE CASCADE, "
                "model TEXT NOT NULL, vector_json TEXT NOT NULL)"
            )
            con.execute(
                "CREATE TABLE IF NOT EXISTS agent_runs ("
                "id TEXT PRIMARY KEY, objective TEXT NOT NULL, status TEXT NOT NULL, "
                "started_at TEXT NOT NULL, finished_at TEXT, summary TEXT NOT NULL DEFAULT '')"
            )
            con.execute(
                "CREATE TABLE IF NOT EXISTS agent_steps ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL "
                "REFERENCES agent_runs(id) ON DELETE CASCADE, step_index INTEGER NOT NULL, "
                "tool_name TEXT NOT NULL, arguments_json TEXT NOT NULL, status TEXT NOT NULL, "
                "started_at TEXT NOT NULL, finished_at TEXT, result TEXT NOT NULL DEFAULT '', "
                "UNIQUE(run_id, step_index))"
            )
            con.execute("CREATE INDEX IF NOT EXISTS idx_agent_steps_run ON agent_steps(run_id, step_index)")
            con.execute(
                "CREATE TABLE IF NOT EXISTS conversation_summaries ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "through_message_id INTEGER NOT NULL, summary TEXT NOT NULL, "
                "created_at TEXT NOT NULL, UNIQUE(through_message_id))"
            )
            con.execute("CREATE INDEX IF NOT EXISTS idx_conversation_summaries_through ON conversation_summaries(through_message_id)")
            con.execute(
                "CREATE TABLE IF NOT EXISTS tool_audit ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, tool_name TEXT NOT NULL, "
                "arguments_json TEXT NOT NULL, outcome TEXT NOT NULL, started_at TEXT NOT NULL, "
                "finished_at TEXT NOT NULL, error TEXT NOT NULL DEFAULT '')"
            )
            con.execute("CREATE INDEX IF NOT EXISTS idx_tool_audit_time ON tool_audit(started_at)")

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat(timespec="seconds")

    def add_message(self, role: str, content: str) -> None:
        with self._connect() as con:
            con.execute(
                "INSERT INTO messages(role, content, created_at) VALUES (?, ?, ?)",
                (role, content, self._now()),
            )

    def recent_messages(self, limit: int = 12) -> list[dict[str, str]]:
        with self._connect() as con:
            rows = con.execute(
                "SELECT role, content FROM messages ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [{"role": row["role"], "content": row["content"]} for row in reversed(rows)]

    def message_count(self) -> int:
        with self._connect() as con:
            return int(con.execute("SELECT COUNT(*) FROM messages").fetchone()[0])

    def latest_message_id(self) -> int:
        with self._connect() as con:
            row = con.execute("SELECT COALESCE(MAX(id), 0) AS value FROM messages").fetchone()
        return int(row["value"])

    def messages_after(self, message_id: int, limit: int = 500) -> list[dict[str, Any]]:
        with self._connect() as con:
            rows = con.execute(
                "SELECT id, role, content, created_at FROM messages WHERE id > ? ORDER BY id LIMIT ?",
                (max(0, int(message_id)), max(1, min(int(limit), 2000))),
            ).fetchall()
        return [dict(row) for row in rows]

    def latest_conversation_summary(self) -> dict[str, Any] | None:
        with self._connect() as con:
            row = con.execute(
                "SELECT id, through_message_id, summary, created_at "
                "FROM conversation_summaries ORDER BY through_message_id DESC LIMIT 1"
            ).fetchone()
        return dict(row) if row else None

    def add_conversation_summary(self, through_message_id: int, summary: str) -> bool:
        cleaned = summary.strip()
        if not cleaned or len(cleaned) > 12000:
            return False
        with self._connect() as con:
            cursor = con.execute(
                "INSERT OR IGNORE INTO conversation_summaries(through_message_id, summary, created_at) "
                "VALUES (?, ?, ?)",
                (int(through_message_id), redact_text(cleaned), self._now()),
            )
            return cursor.rowcount > 0

    def recover_stale_agent_runs(self, stale_after_minutes: int = 10) -> int:
        """Mark only old in-flight runs interrupted; never disturb another live session."""
        minutes = max(1, min(int(stale_after_minutes), 1440))
        cutoff = (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat(timespec="seconds")
        with self._connect() as con:
            cursor = con.execute(
                "UPDATE agent_runs SET status='interrupted', finished_at=?, "
                "summary='Process ended before this run was finalized; no automatic replay was attempted.' "
                "WHERE status='running' AND started_at < ?",
                (self._now(), cutoff),
            )
            return int(cursor.rowcount)

    def add_memory(
        self,
        content: str,
        category: str = "preference",
        source: str = "explicit",
        confidence: float = 1.0,
    ) -> bool:
        cleaned = " ".join(content.strip().split())
        if not cleaned:
            return False
        now = self._now()
        with self._connect() as con:
            cursor = con.execute(
                "INSERT OR IGNORE INTO memories"
                "(content, category, source, confidence, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (cleaned, category, source, max(0.0, min(1.0, confidence)), now, now),
            )
            return cursor.rowcount > 0

    def list_memories(self) -> list[dict[str, Any]]:
        with self._connect() as con:
            rows = con.execute(
                "SELECT id, content, category, source, confidence, created_at, updated_at "
                "FROM memories ORDER BY id"
            ).fetchall()
        return [dict(row) for row in rows]

    def memory_texts(self) -> list[str]:
        return [str(item["content"]) for item in self.list_memories()]

    def get_memory(self, memory_id: int) -> dict[str, Any] | None:
        with self._connect() as con:
            row = con.execute("SELECT * FROM memories WHERE id = ?", (memory_id,)).fetchone()
        return dict(row) if row else None

    def update_memory(self, memory_id: int, content: str, category: str | None = None) -> bool:
        cleaned = " ".join(content.strip().split())
        if not cleaned:
            return False
        with self._connect() as con:
            if category is None:
                cursor = con.execute(
                    "UPDATE memories SET content = ?, updated_at = ? WHERE id = ?",
                    (cleaned, self._now(), memory_id),
                )
            else:
                cursor = con.execute(
                    "UPDATE memories SET content = ?, category = ?, updated_at = ? WHERE id = ?",
                    (cleaned, category, self._now(), memory_id),
                )
            return cursor.rowcount > 0

    def delete_memory(self, memory_id: int) -> bool:
        with self._connect() as con:
            cursor = con.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
            return cursor.rowcount > 0

    def clear_messages(self) -> None:
        with self._connect() as con:
            con.execute("DELETE FROM messages")


    def add_task(self, title: str) -> int:
        cleaned = " ".join(title.strip().split())
        if not cleaned or len(cleaned) > 300:
            raise ValueError("Task title must contain 1–300 characters.")
        with self._connect() as con:
            cursor = con.execute(
                "INSERT INTO tasks(title, status, created_at) VALUES (?, 'open', ?)",
                (cleaned, self._now()),
            )
            return int(cursor.lastrowid)

    def get_task(self, task_id: int) -> dict[str, Any] | None:
        with self._connect() as con:
            row = con.execute(
                "SELECT id, title, status, created_at, completed_at FROM tasks WHERE id = ?",
                (task_id,),
            ).fetchone()
        return dict(row) if row else None

    def list_tasks(self, include_done: bool = True) -> list[dict[str, Any]]:
        query = "SELECT id, title, status, created_at, completed_at FROM tasks"
        if not include_done:
            query += " WHERE status = 'open'"
        query += " ORDER BY CASE status WHEN 'open' THEN 0 ELSE 1 END, id"
        with self._connect() as con:
            rows = con.execute(query).fetchall()
        return [dict(row) for row in rows]

    def complete_task(self, task_id: int) -> bool:
        with self._connect() as con:
            cursor = con.execute(
                "UPDATE tasks SET status = 'done', completed_at = ? WHERE id = ? AND status = 'open'",
                (self._now(), task_id),
            )
            return cursor.rowcount > 0

    def delete_task(self, task_id: int) -> bool:
        with self._connect() as con:
            cursor = con.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
            return cursor.rowcount > 0

    def add_candidate(
        self,
        content: str,
        category: str,
        source: str,
        confidence: float,
        evidence_summary: str,
        evidence_count: int = 1,
    ) -> int | None:
        cleaned = " ".join(content.strip().split())
        if not cleaned:
            return None
        now = self._now()
        with self._connect() as con:
            # A duplicate of an already-approved memory should not be proposed again.
            existing_memory = con.execute(
                "SELECT id FROM memories WHERE lower(content) = lower(?)", (cleaned,)
            ).fetchone()
            if existing_memory:
                return None
            # Do not nag Abhi with the same inferred behaviour after he has
            # rejected it. An explicit /remember can deliberately resubmit it.
            if source == "repeated_behavior":
                rejected = con.execute(
                    "SELECT id FROM memory_candidates WHERE lower(content) = lower(?) "
                    "AND source = 'repeated_behavior' AND status = 'rejected' LIMIT 1",
                    (cleaned,),
                ).fetchone()
                if rejected:
                    return None
            pending = con.execute(
                "SELECT id, evidence_count FROM memory_candidates "
                "WHERE lower(content) = lower(?) AND status = 'pending' ORDER BY id DESC LIMIT 1",
                (cleaned,),
            ).fetchone()
            if pending:
                con.execute(
                    "UPDATE memory_candidates SET evidence_count = ?, evidence_summary = ?, "
                    "confidence = ?, updated_at = ? WHERE id = ?",
                    (max(int(pending["evidence_count"]), evidence_count), evidence_summary,
                     max(0.0, min(1.0, confidence)), now, int(pending["id"])),
                )
                # Return None so the CLI does not repeatedly prompt about the
                # same already-pending candidate. Its evidence is still updated.
                return None
            cursor = con.execute(
                "INSERT INTO memory_candidates(content, category, source, confidence, "
                "evidence_count, evidence_summary, status, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?)",
                (cleaned, category, source, max(0.0, min(1.0, confidence)),
                 evidence_count, evidence_summary[:1000], now, now),
            )
            return int(cursor.lastrowid)

    def list_candidates(self, status: str = "pending") -> list[dict[str, Any]]:
        if status not in {"pending", "approved", "rejected", "all"}:
            raise ValueError("Invalid candidate status")
        query = "SELECT * FROM memory_candidates"
        params: tuple[()] | tuple[str, ...] = ()
        if status != "all":
            query += " WHERE status = ?"
            params = (status,)
        query += " ORDER BY id"
        with self._connect() as con:
            rows = con.execute(query, params).fetchall()
        return [dict(row) for row in rows]

    def approve_candidate(self, candidate_id: int) -> bool:
        now = self._now()
        with self._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute(
                "SELECT * FROM memory_candidates WHERE id = ? AND status = 'pending'",
                (candidate_id,),
            ).fetchone()
            if row is None:
                return False
            con.execute(
                "INSERT OR IGNORE INTO memories(content, category, source, confidence, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (row["content"], row["category"], row["source"], row["confidence"], now, now),
            )
            con.execute(
                "UPDATE memory_candidates SET status = 'approved', updated_at = ? WHERE id = ?",
                (now, candidate_id),
            )
            return True

    def reject_candidate(self, candidate_id: int) -> bool:
        with self._connect() as con:
            cursor = con.execute(
                "UPDATE memory_candidates SET status = 'rejected', updated_at = ? "
                "WHERE id = ? AND status = 'pending'",
                (self._now(), candidate_id),
            )
            return cursor.rowcount > 0

    def begin_agent_run(self, run_id: str, objective: str) -> None:
        with self._connect() as con:
            con.execute(
                "INSERT INTO agent_runs(id,objective,status,started_at) VALUES(?,?,'running',?)",
                (run_id, redact_text(objective)[:1000], self._now()),
            )

    def finish_agent_run(self, run_id: str, status: str, summary: str = "") -> None:
        if status not in {"completed", "failed", "cancelled", "approval_required", "interrupted"}:
            raise ValueError("Invalid agent-run status.")
        with self._connect() as con:
            con.execute(
                "UPDATE agent_runs SET status=?,finished_at=?,summary=? WHERE id=?",
                (status, self._now(), redact_text(summary)[:4000], run_id),
            )

    def record_agent_step(
        self, run_id: str, step_index: int, tool_name: str,
        arguments_json: str, status: str, result: str = "", error: str = "",
    ) -> None:
        now = self._now()
        with self._connect() as con:
            con.execute(
                "INSERT INTO agent_steps(run_id,step_index,tool_name,arguments_json,status,started_at,finished_at,result) "
                "VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(run_id,step_index) DO UPDATE SET "
                "status=excluded.status,finished_at=excluded.finished_at,result=excluded.result",
                (run_id, step_index, tool_name, arguments_json[:12000], status, now, now, (result or error)[:4000]),
            )

    def record_tool_audit(
        self, run_id: str | None, tool_name: str, arguments_json: str,
        outcome: str, error: str = "",
    ) -> None:
        now = self._now()
        with self._connect() as con:
            con.execute(
                "INSERT INTO tool_audit(run_id,tool_name,arguments_json,outcome,started_at,finished_at,error) "
                "VALUES(?,?,?,?,?,?,?)",
                (run_id, tool_name, arguments_json[:12000], outcome, now, now, error[:2000]),
            )

    def recent_agent_runs(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._connect() as con:
            rows = con.execute(
                "SELECT id,objective,status,started_at,finished_at,summary FROM agent_runs "
                "ORDER BY started_at DESC LIMIT ?", (max(1, min(int(limit), 100)),)
            ).fetchall()
        return [dict(row) for row in rows]

    def recent_tool_audit(self, limit: int = 30) -> list[dict[str, Any]]:
        with self._connect() as con:
            rows = con.execute(
                "SELECT id,run_id,tool_name,outcome,started_at,error FROM tool_audit ORDER BY id DESC LIMIT ?",
                (max(1, min(int(limit), 100)),)
            ).fetchall()
        return [dict(row) for row in rows]

    def record_behavior_observation(
        self, signal_key: str, category: str, evidence: str, days: int = 30
    ) -> int:
        now = datetime.now(timezone.utc)
        cutoff = (now - timedelta(days=days)).isoformat(timespec="seconds")
        with self._connect() as con:
            con.execute(
                "INSERT INTO behavior_observations(signal_key, category, evidence, created_at) "
                "VALUES (?, ?, ?, ?)",
                (signal_key, category, evidence[:300], now.isoformat(timespec="seconds")),
            )
            row = con.execute(
                "SELECT COUNT(*) AS n FROM behavior_observations "
                "WHERE signal_key = ? AND created_at >= ?",
                (signal_key, cutoff),
            ).fetchone()
            return int(row["n"])
