from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from chinuuu.assistant import Assistant
from chinuuu.context_engine import rank_memories
from chinuuu.database import LocalStore


class SummaryClient:
    def __init__(self, summary: str = "Project context: local-first Chinuuu assistant."):
        self.summary = summary
        self.calls = []

    def chat(self, messages):
        self.calls.append(messages)
        return self.summary


def test_memory_ranking_prefers_relevant_approved_memories():
    memories = [
        {"id": 1, "category": "preference", "content": "Always explain technical concepts."},
        {"id": 2, "category": "project", "content": "Chinuuu uses local Ollama and SQLite."},
        {"id": 3, "category": "general", "content": "The user likes outdoor photography."},
    ]
    ranked = rank_memories("How does Chinuuu use SQLite locally?", memories, limit=2)
    assert ranked[0]["id"] == 2
    assert len(ranked) == 1


def test_memory_ranking_has_bounded_fallback_for_unrelated_query():
    memories = [{"id": i, "content": f"Memory item {i}", "category": "general"} for i in range(8)]
    ranked = rank_memories("Explain a queue", memories, limit=3)
    assert [item["id"] for item in ranked] == [5, 6, 7]


def test_conversation_summary_is_created_without_deleting_original_messages(tmp_path):
    store = LocalStore(tmp_path / "chinuuu.sqlite3")
    for index in range(8):
        store.add_message("user", f"User message {index}")
        store.add_message("assistant", f"Assistant response {index}")

    client = SummaryClient()
    assistant = Assistant.__new__(Assistant)
    assistant.store = store
    assistant.client = client
    assistant.settings = SimpleNamespace(context_messages=4, context_summary_interval=12)

    assistant._maybe_summarize_conversation()

    summary = store.latest_conversation_summary()
    assert summary is not None
    assert summary["through_message_id"] == 12
    assert "local-first" in summary["summary"]
    assert store.message_count() == 16
    assert len(client.calls) == 1


def test_conversation_summary_waits_until_threshold_is_met(tmp_path):
    store = LocalStore(tmp_path / "chinuuu.sqlite3")
    for index in range(7):
        store.add_message("user", f"User message {index}")
        store.add_message("assistant", f"Assistant response {index}")
    client = SummaryClient()
    assistant = Assistant.__new__(Assistant)
    assistant.store = store
    assistant.client = client
    assistant.settings = SimpleNamespace(context_messages=4, context_summary_interval=12)

    assistant._maybe_summarize_conversation()

    assert store.latest_conversation_summary() is None
    assert client.calls == []


def test_failed_summary_does_not_affect_original_chat(tmp_path):
    class BrokenSummaryClient:
        def chat(self, _messages):
            raise RuntimeError("Ollama unavailable")

    store = LocalStore(tmp_path / "chinuuu.sqlite3")
    for index in range(8):
        store.add_message("user", f"User message {index}")
        store.add_message("assistant", f"Assistant response {index}")
    assistant = Assistant.__new__(Assistant)
    assistant.store = store
    assistant.client = BrokenSummaryClient()
    assistant.settings = SimpleNamespace(context_messages=4, context_summary_interval=12)

    assistant._maybe_summarize_conversation()

    assert store.latest_conversation_summary() is None
    assert store.message_count() == 16


def test_stale_agent_runs_are_recovered_but_recent_runs_are_not(tmp_path):
    store = LocalStore(tmp_path / "chinuuu.sqlite3")
    store.begin_agent_run("stale-run", "Old task")
    store.begin_agent_run("live-run", "Current task")
    old = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat(timespec="seconds")
    with store._connect() as con:
        con.execute("UPDATE agent_runs SET started_at=? WHERE id='stale-run'", (old,))

    recovered = store.recover_stale_agent_runs(stale_after_minutes=10)

    assert recovered == 1
    statuses = {row["id"]: row["status"] for row in store.recent_agent_runs(10)}
    assert statuses["stale-run"] == "interrupted"
    assert statuses["live-run"] == "running"


def test_summary_migration_is_additive_and_keeps_existing_messages(tmp_path):
    db_path = tmp_path / "chinuuu.sqlite3"
    store = LocalStore(db_path)
    store.add_message("user", "Keep this original message")
    reopened = LocalStore(db_path)
    assert reopened.recent_messages(1)[0]["content"] == "Keep this original message"
    assert reopened.latest_conversation_summary() is None
    assert reopened.message_count() == 1


def test_health_diagnostics_reports_sqlite_and_configured_model(monkeypatch, tmp_path):
    import json
    from chinuuu import diagnostics
    from chinuuu.config import Settings

    store = LocalStore(tmp_path / "chinuuu.sqlite3")

    class FakeResponse:
        def __enter__(self):
            return self
        def __exit__(self, *_args):
            return False
        def read(self):
            return json.dumps({"models": [{"name": "qwen3:4b-instruct"}]}).encode()

    monkeypatch.setattr(diagnostics.urllib.request, "urlopen", lambda *_args, **_kwargs: FakeResponse())
    settings = Settings(workspace_root=str(tmp_path), model="qwen3:4b-instruct")
    report = diagnostics.run_diagnostics(settings, store, tool_count=47)

    assert "[PASS] SQLite integrity: ok" in report
    assert "[PASS] Ollama model: qwen3:4b-instruct is installed" in report
    assert "[INFO] Tool registry: 47 registered tools" in report
    assert "No system settings were changed" in report


def test_health_diagnostics_handles_unavailable_ollama_without_crashing(monkeypatch, tmp_path):
    from urllib.error import URLError
    from chinuuu import diagnostics
    from chinuuu.config import Settings

    store = LocalStore(tmp_path / "chinuuu.sqlite3")
    def unavailable(*_args, **_kwargs):
        raise URLError("offline")
    monkeypatch.setattr(diagnostics.urllib.request, "urlopen", unavailable)
    report = diagnostics.run_diagnostics(Settings(workspace_root=str(tmp_path)), store)

    assert "[PASS] SQLite integrity: ok" in report
    assert "[FAIL] Ollama model" in report
