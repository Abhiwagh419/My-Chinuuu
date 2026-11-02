from __future__ import annotations

import asyncio
import json
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from chinuuu.artifacts import ArtifactGenerator
from chinuuu.backups import create_database_backup
from chinuuu.database import LocalStore
from chinuuu.desktop_automation import DesktopAutomation
from chinuuu.errors import ToolError
from chinuuu.knowledge import DocumentIndexer
from chinuuu.productivity import ProductivityService
from chinuuu.tools import ToolRegistry, ToolSpec
from chinuuu.windows_controls import WindowsControls
from chinuuu.wakeword import WakeWordListener
from chinuuu.voice import OfflineVoiceEngine


def _make_store(tmp_path: Path) -> tuple[LocalStore, Path]:
    store = LocalStore(tmp_path / "data" / "chinuuu.sqlite3")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    return store, workspace


def test_v2_database_migration_is_additive_and_preserves_existing_data(tmp_path):
    store = LocalStore(tmp_path / "chinuuu.sqlite3")
    store.add_memory("Keep technical explanations detailed.")
    task_id = store.add_task("Preserve pre-v2 task")
    reopened = LocalStore(store.db_path)
    assert reopened.get_task(task_id)["title"] == "Preserve pre-v2 task"
    assert reopened.list_memories()[0]["content"] == "Keep technical explanations detailed."
    with reopened._connect() as con:
        tables = {row["name"] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"reminders", "calendar_events", "contacts", "documents", "document_chunks", "document_embeddings", "agent_runs", "agent_steps", "tool_audit"} <= tables


def test_document_indexer_indexes_and_searches_local_markdown(tmp_path):
    store, workspace = _make_store(tmp_path)
    document = workspace / "networking.md"
    document.write_text("# Networking\n\nTCP provides reliable, ordered byte-stream delivery using sequence numbers.", encoding="utf-8")
    indexer = DocumentIndexer(store, workspace)
    result = indexer.index_file("networking.md")
    assert "SHA-256 verified" in result
    search = indexer.search("TCP sequence numbers")
    assert "networking.md" in search
    assert "reliable" in search
    assert "networking.md" in indexer.list_indexed()


def test_document_indexer_avoids_reindex_when_content_unchanged_and_updates_changed_content(tmp_path):
    store, workspace = _make_store(tmp_path)
    document = workspace / "notes.txt"
    document.write_text("first unique token", encoding="utf-8")
    indexer = DocumentIndexer(store, workspace)
    indexer.index_file("notes.txt")
    assert "already indexed" in indexer.index_file("notes.txt")
    document.write_text("second replacement token", encoding="utf-8")
    indexer.index_file("notes.txt")
    assert "second replacement" in indexer.search("replacement")
    assert "first unique" not in indexer.search("first")


def test_document_indexer_blocks_symlink_to_external_file(tmp_path):
    store, workspace = _make_store(tmp_path)
    outside = tmp_path / "secret.txt"
    outside.write_text("private", encoding="utf-8")
    link = workspace / "linked.txt"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("Symlink creation is unavailable on this host.")
    with pytest.raises(ToolError, match="Symbolic links"):
        DocumentIndexer(store, workspace).index_file("linked.txt")


def test_document_indexer_rejects_parent_traversal(tmp_path):
    store, workspace = _make_store(tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_text("private", encoding="utf-8")
    with pytest.raises(ToolError):
        DocumentIndexer(store, workspace).index_file("../outside.txt")


def test_productivity_contact_resolution_requires_exact_unique_match(tmp_path):
    store, workspace = _make_store(tmp_path)
    productivity = ProductivityService(store, workspace)
    productivity.add_contact("Soham Gavande", "+91 98765 43210")
    assert productivity.resolve_contact("Soham Gavande") == "+919876543210"
    productivity.add_contact("Soham Gavande", "+91 98765 43211")
    with pytest.raises(ToolError, match="multiple phone numbers"):
        productivity.resolve_contact("Soham Gavande")
    with pytest.raises(ToolError, match="exactly matches"):
        productivity.resolve_contact("Soham")


def test_productivity_rejects_invalid_contact_phone(tmp_path):
    store, workspace = _make_store(tmp_path)
    with pytest.raises(ToolError, match="international phone number"):
        ProductivityService(store, workspace).add_contact("Test Contact", "12345")


def test_calendar_event_is_persisted_and_exported_as_ics(tmp_path):
    store, workspace = _make_store(tmp_path)
    service = ProductivityService(store, workspace)
    start = (datetime.now().astimezone() + timedelta(days=2)).replace(second=0, microsecond=0)
    result = service.create_calendar_event(
        "Project Review, Final", start.isoformat(), location="Mumbai; Lab", description="Line one\r\nLine two"
    )
    assert "exported as an iCalendar file" in result
    exports = list((workspace / "calendar_exports").glob("*.ics"))
    assert len(exports) == 1
    payload = exports[0].read_text(encoding="utf-8")
    assert "BEGIN:VCALENDAR" in payload
    assert "SUMMARY:Project Review\\, Final" in payload
    assert "LOCATION:Mumbai\\; Lab" in payload
    assert "DESCRIPTION:Line one\\nLine two" in payload
    assert "Project Review" in service.list_events()


def test_calendar_rejects_end_before_start(tmp_path):
    store, workspace = _make_store(tmp_path)
    service = ProductivityService(store, workspace)
    start = (datetime.now().astimezone() + timedelta(days=1)).replace(second=0, microsecond=0)
    end = start - timedelta(minutes=1)
    with pytest.raises(ToolError, match="end must be after"):
        service.create_calendar_event("Invalid", start.isoformat(), end.isoformat())


def test_reminder_requires_future_time_and_can_be_dismissed(tmp_path):
    store, workspace = _make_store(tmp_path)
    service = ProductivityService(store, workspace)
    due = (datetime.now().astimezone() + timedelta(hours=1)).replace(second=0, microsecond=0)
    saved = service.add_reminder("Review Chinuuu", due.isoformat())
    assert "Reminder #1 saved" in saved
    assert "Review Chinuuu" in service.list_reminders()
    assert "dismissed and verified" in service.dismiss_reminder(1)
    assert "[dismissed]" in service.list_reminders()
    with pytest.raises(ToolError, match="must be in the future"):
        service.add_reminder("Past", (datetime.now().astimezone() - timedelta(minutes=1)).isoformat())


def test_database_backup_is_integrity_checked_and_preserves_data(tmp_path):
    store = LocalStore(tmp_path / "data" / "chinuuu.sqlite3")
    task_id = store.add_task("Backup this")
    result = create_database_backup(store.db_path, tmp_path / "backups")
    assert "integrity-checked" in result
    backup = next((tmp_path / "backups").glob("*.sqlite3"))
    with sqlite3.connect(backup) as con:
        assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert con.execute("SELECT title FROM tasks WHERE id=?", (task_id,)).fetchone()[0] == "Backup this"


def test_document_generator_creates_new_files_and_never_overwrites(tmp_path):
    workspace = tmp_path / "workspace"
    generator = ArtifactGenerator(workspace)
    result = generator.create_document("reports/summary.md", "Summary", "First paragraph.\n\n## Findings\nDetails.")
    target = workspace / "reports" / "summary.md"
    assert "created and verified" in result
    original = target.read_text(encoding="utf-8")
    assert "# Summary" in original and "## Findings" in original
    with pytest.raises(ToolError, match="already exists"):
        generator.create_document("reports/summary.md", "Replacement", "Do not overwrite")
    assert target.read_text(encoding="utf-8") == original


def test_document_generator_escapes_html_content(tmp_path):
    generator = ArtifactGenerator(tmp_path / "workspace")
    generator.create_document("safe.html", "A < B", "<script>alert(1)</script>")
    content = (tmp_path / "workspace" / "safe.html").read_text(encoding="utf-8")
    assert "&lt;script&gt;" in content
    assert "<script>alert" not in content


def test_artifact_generator_blocks_path_traversal(tmp_path):
    generator = ArtifactGenerator(tmp_path / "workspace")
    with pytest.raises(ToolError, match="relative path"):
        generator.create_document("../outside.md", "Title", "Content")


def test_artifact_generator_blocks_symlink_escape(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        (workspace / "external").symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("Symlink creation is unavailable on this host.")
    generator = ArtifactGenerator(workspace)
    with pytest.raises(ToolError, match="symbolic links"):
        generator.create_document("external/out.md", "Title", "Content")


def test_backup_reports_missing_database(tmp_path):
    with pytest.raises(ToolError, match="does not exist"):
        create_database_backup(tmp_path / "missing.sqlite3", tmp_path / "backups")


def test_desktop_automation_fails_closed_off_windows():
    with pytest.raises(ToolError, match="only on Windows"):
        DesktopAutomation(platform_name="posix").list_windows()


def test_voice_requires_local_model_without_downloading(tmp_path):
    engine = OfflineVoiceEngine(tmp_path / "missing-model")
    with pytest.raises(ToolError, match="will not download"):
        engine.record_and_transcribe()


def test_wakeword_requires_explicit_local_onnx_model(tmp_path):
    listener = WakeWordListener(tmp_path / "missing.onnx")
    with pytest.raises(ToolError, match="local ONNX"):
        listener.start(lambda *_: None)


def test_wifi_and_bluetooth_state_changes_are_confirmation_gated(tmp_path):
    registry = ToolRegistry(tmp_path / "workspace")
    for name in ("set_wifi_radio", "set_bluetooth_radio", "capture_desktop_screenshot", "click_desktop_control", "set_desktop_text", "lock_windows_session", "sleep_windows_session"):
        spec = registry._tools[name]
        assert spec.requires_confirmation is True
        result = registry.execute(name, {"enabled": False} if "radio" in name else ({"window_title": "Test", "control_name": "OK"} if name == "click_desktop_control" else ({"window_title": "Test", "control_name": "Input", "text": "hello"} if name == "set_desktop_text" else {})))
        assert result.startswith("Not executed:")


def test_radio_control_uses_winrt_and_verifies_state(monkeypatch):
    state = {"value": "on"}

    class EnumValue:
        def __init__(self, name):
            self.name = name
        def __str__(self):
            return self.name

    class FakeRadio:
        kind = EnumValue("wifi")
        name = "Wi-Fi"
        @property
        def state(self):
            return EnumValue(state["value"])
        async def set_state_async(self, requested):
            state["value"] = requested.name
            return "Allowed"

    class FakeRadioApi:
        @staticmethod
        async def get_radios_async():
            return [FakeRadio()]
        @staticmethod
        async def request_access_async():
            return "Allowed"

    fake_module = ModuleType("winrt.windows.devices.radios")
    fake_module.Radio = FakeRadioApi
    fake_module.RadioKind = SimpleNamespace()
    fake_module.RadioState = SimpleNamespace(on=EnumValue("on"), off=EnumValue("off"))
    modules = {
        "winrt": ModuleType("winrt"),
        "winrt.windows": ModuleType("winrt.windows"),
        "winrt.windows.devices": ModuleType("winrt.windows.devices"),
        "winrt.windows.devices.radios": fake_module,
    }
    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)
    controls = WindowsControls(platform_name="nt")
    assert "turned off and verified" in controls.set_wifi(False)
    assert state["value"] == "off"


def test_radio_status_reports_disabled_as_distinct_state(monkeypatch):
    class EnumValue:
        def __init__(self, name): self.name = name
        def __str__(self): return self.name
    class FakeRadio:
        kind = EnumValue("bluetooth")
        name = "Bluetooth"
        state = EnumValue("disabled")
    class FakeRadioApi:
        @staticmethod
        async def get_radios_async(): return [FakeRadio()]
    fake_module = ModuleType("winrt.windows.devices.radios")
    fake_module.Radio = FakeRadioApi
    fake_module.RadioKind = SimpleNamespace()
    fake_module.RadioState = SimpleNamespace(on=EnumValue("on"), off=EnumValue("off"))
    for name in ("winrt", "winrt.windows", "winrt.windows.devices"):
        monkeypatch.setitem(sys.modules, name, ModuleType(name))
    monkeypatch.setitem(sys.modules, "winrt.windows.devices.radios", fake_module)
    result = WindowsControls(platform_name="nt").bluetooth_status()
    assert "disabled" in result


def test_wifi_intent_requires_confirmation_and_routes(tmp_path, monkeypatch):
    from chinuuu.assistant import _handle_deterministic_workspace_request
    registry = ToolRegistry(tmp_path / "workspace")
    calls = []
    spec = registry._tools["set_wifi_radio"]
    from dataclasses import replace
    registry._tools["set_wifi_radio"] = replace(spec, handler=lambda enabled: calls.append(enabled) or "done")
    denied = _handle_deterministic_workspace_request("turn wifi off", registry, lambda *_: False)
    assert denied.startswith("Not executed:")
    assert calls == []
    accepted = _handle_deterministic_workspace_request("enable wi-fi", registry, lambda *_: True)
    assert accepted == "done"
    assert calls == [True]


def test_bluetooth_intent_requires_confirmation_and_routes(tmp_path):
    from chinuuu.assistant import _handle_deterministic_workspace_request
    from dataclasses import replace
    registry = ToolRegistry(tmp_path / "workspace")
    calls = []
    registry._tools["set_bluetooth_radio"] = replace(
        registry._tools["set_bluetooth_radio"], handler=lambda enabled: calls.append(enabled) or "done"
    )
    result = _handle_deterministic_workspace_request("disable bluetooth", registry, lambda *_: True)
    assert result == "done"
    assert calls == [False]


def test_desktop_text_entry_rejects_invalid_control_name_before_ui_init():
    automation = DesktopAutomation(platform_name="nt")
    with pytest.raises(ToolError, match="exact visible edit-control"):
        automation.set_text("Test", "", "hello")


def test_local_agent_and_tool_audit_tables_store_step_statuses(tmp_path):
    store = LocalStore(tmp_path / "db.sqlite3")
    store.begin_agent_run("run-1", "Test workflow")
    store.record_agent_step("run-1", 1, "calculate", json.dumps({"expression": "2+2"}), "completed", "4")
    store.record_tool_audit("run-1", "calculate", json.dumps({"expression": "2+2"}), "completed")
    store.finish_agent_run("run-1", "completed", "Finished")
    assert store.recent_agent_runs()[0]["status"] == "completed"
    assert store.recent_tool_audit()[0]["tool_name"] == "calculate"


def test_agent_step_argument_redaction_does_not_persist_contact_data(tmp_path):
    from chinuuu.assistant import Assistant
    from chinuuu.planner import ToolExecutionPolicy
    store, workspace = _make_store(tmp_path)
    registry = ToolRegistry(workspace, task_store=store)
    store.begin_agent_run("run-pii", "Save local contact")
    registry.active_run_id = "run-pii"
    spec = registry._tools["add_local_contact"]
    from dataclasses import replace
    registry._tools["add_local_contact"] = replace(spec, handler=lambda **_: "Contact saved")
    assistant = Assistant.__new__(Assistant)
    assistant.policy = ToolExecutionPolicy(max_tool_rounds=1, max_tool_calls=2)
    assistant.client = SimpleNamespace(chat_message=lambda *args, **kwargs: {
        "role": "assistant", "content": "", "tool_calls": [{"function": {
            "name": "add_local_contact", "arguments": {"name": "Test", "phone": "+911234567890", "email": "secret@example.com"}
        }}]
    })
    assistant.store = store
    # The tool isn't user-text deterministic; it is called through the bounded model tool loop.
    assistant._agent_loop([{"role": "user", "content": "save contact Test with phone +911234567890"}], registry, lambda *_: True, user_text="save contact Test with phone +911234567890")
    with store._connect() as con:
        row = con.execute("SELECT arguments_json FROM agent_steps WHERE run_id='run-pii'").fetchone()
    assert "+911234567890" not in row["arguments_json"]
    assert "secret@example.com" not in row["arguments_json"]


def test_artifact_generator_rejects_internal_symlink_parent(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "inside"
    target.mkdir()
    try:
        (workspace / "alias").symlink_to(target, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("Symlink creation is unavailable on this host.")
    generator = ArtifactGenerator(workspace)
    with pytest.raises(ToolError, match="symbolic links"):
        generator.create_document("alias/summary.md", "Title", "Body")
    assert not (target / "summary.md").exists()


def test_artifact_generator_rejects_empty_presentation_before_reserving_output(tmp_path):
    generator = ArtifactGenerator(tmp_path / "workspace")
    with pytest.raises(ToolError, match="at least one slide"):
        generator.create_presentation("empty.pptx", "Empty", "---\n---")
    assert not (tmp_path / "workspace" / "empty.pptx").exists()


def test_calendar_export_collision_never_deletes_existing_file(tmp_path):
    store, workspace = _make_store(tmp_path)
    service = ProductivityService(store, workspace)
    start = (datetime.now().astimezone() + timedelta(days=2)).replace(second=0, microsecond=0)
    result = service.create_calendar_event("Meeting", start.isoformat())
    export = next((workspace / "calendar_exports").glob("*.ics"))
    original = export.read_bytes()
    # Repeat the same target by simulating the existing event ID path.
    with store._connect() as con:
        con.execute("DELETE FROM calendar_events")
        con.execute("DELETE FROM sqlite_sequence WHERE name='calendar_events'")
    # Reusing ID 1 recreates the exact filename; O_EXCL must preserve the old export.
    with pytest.raises(ToolError, match="export failed"):
        service.create_calendar_event("Meeting", start.isoformat())
    assert export.read_bytes() == original


def test_privacy_redaction_is_recursive_and_redacts_contact_identifiers():
    from chinuuu.privacy import redact_data, redact_text
    safe = redact_data({"outer": [{"phone": "+91 98765 43210", "message": "Hello"}], "meta": "mail me at abhi@example.com"})
    serialized = json.dumps(safe)
    assert "+91 98765 43210" not in serialized
    assert "Hello" not in serialized
    assert "abhi@example.com" not in serialized
    assert "[EMAIL REDACTED]" in redact_text("abhi@example.com")


def test_agent_run_objective_and_summary_redact_contact_identifiers(tmp_path):
    store = LocalStore(tmp_path / "db.sqlite3")
    store.begin_agent_run("run-safe", "Save +91 98765 43210 for abhi@example.com")
    store.finish_agent_run("run-safe", "completed", "Contact +91 98765 43210 saved; email abhi@example.com")
    run = store.recent_agent_runs()[0]
    assert "+91 98765 43210" not in run["objective"]
    assert "abhi@example.com" not in run["objective"]
    assert "+91 98765 43210" not in run["summary"]
    assert "abhi@example.com" not in run["summary"]


def test_contact_save_result_does_not_echo_phone_number(tmp_path):
    store, workspace = _make_store(tmp_path)
    result = ProductivityService(store, workspace).add_contact("Soham", "+91 98765 43210")
    assert "Contact saved locally" in result
    assert "+91 98765 43210" not in result


def test_agent_loop_stops_immediately_when_confirmation_is_denied(tmp_path):
    from chinuuu.assistant import Assistant
    from chinuuu.planner import ToolExecutionPolicy
    from dataclasses import replace
    store, workspace = _make_store(tmp_path)
    registry = ToolRegistry(workspace, task_store=store)
    calls = []
    registry._tools["set_wifi_radio"] = replace(
        registry._tools["set_wifi_radio"], handler=lambda enabled: calls.append(enabled) or "changed"
    )
    run_id = "run-denied"
    store.begin_agent_run(run_id, "turn wifi off")
    registry.active_run_id = run_id
    assistant = Assistant.__new__(Assistant)
    assistant.policy = ToolExecutionPolicy(max_tool_rounds=2, max_tool_calls=4)
    assistant.client = SimpleNamespace(chat_message=lambda *args, **kwargs: {
        "role": "assistant", "content": "", "tool_calls": [{"function": {
            "name": "set_wifi_radio", "arguments": {"enabled": False}
        }}]
    })
    assistant.store = store
    result = assistant._agent_loop(
        [{"role": "user", "content": "turn wifi off"}], registry,
        lambda *_: False, user_text="turn wifi off",
    )
    assert result.startswith("Not executed:")
    assert calls == []
    with store._connect() as con:
        step = con.execute("SELECT status FROM agent_steps WHERE run_id=?", (run_id,)).fetchone()
    assert step["status"] == "approval_required"


def test_artifact_generator_allows_normal_workspace_filename(tmp_path):
    # Artifact outputs are workspace documents, not credential files.
    generator = ArtifactGenerator(tmp_path / "workspace")
    result = generator.create_document("normal.md", "Title", "Body")
    assert "created and verified" in result


def test_voice_push_to_talk_duration_is_bounded(tmp_path):
    engine = OfflineVoiceEngine(tmp_path / "model")
    for value in (1, 21, True):
        with pytest.raises(ToolError, match="2–20 seconds"):
            engine.record_and_transcribe(seconds=value)


def test_public_page_fetch_rejects_localhost_without_network_access():
    from chinuuu.web_research import fetch_public_page
    with pytest.raises(ToolError, match="Localhost"):
        fetch_public_page("http://localhost/admin")


def test_registry_confirmation_denial_redacts_nested_sensitive_audit_arguments(tmp_path):
    store = LocalStore(tmp_path / "db.sqlite3")
    registry = ToolRegistry(tmp_path / "workspace", task_store=store)
    registry._register(ToolSpec(
        "test_sensitive", "Test sensitive nested audit redaction",
        {"type": "object", "properties": {"payload": {"type": "object"}}, "required": ["payload"]},
        True, lambda **_: "must not run",
    ))
    arguments = {"payload": {"phone": "+919876543210", "message": "private text"}}
    result = registry.execute("test_sensitive", arguments, lambda *_: False)
    assert result.startswith("Not executed:")
    audit = store.recent_tool_audit()[0]
    assert audit["outcome"] == "approval_denied"
    with store._connect() as con:
        audit_arguments = con.execute("SELECT arguments_json FROM tool_audit WHERE id=?", (audit["id"],)).fetchone()["arguments_json"]
    assert "+919876543210" not in audit_arguments
    assert "private text" not in audit_arguments
    assert "[REDACTED]" in audit_arguments


def test_registry_redacts_sensitive_values_from_tool_error_audit(tmp_path):
    store = LocalStore(tmp_path / "db.sqlite3")
    registry = ToolRegistry(tmp_path / "workspace", task_store=store)
    registry._register(ToolSpec(
        "test_sensitive_error", "Test sensitive error redaction",
        {"type": "object", "properties": {}, "required": []},
        False, lambda: (_ for _ in ()).throw(ToolError("Failed for +919876543210; contact private@example.com")),
    ))
    with pytest.raises(ToolError):
        registry.execute("test_sensitive_error", {})
    audit = store.recent_tool_audit()[0]
    assert "+919876543210" not in audit["error"]
    assert "private@example.com" not in audit["error"]


def test_gui_and_cli_entrypoints_are_importable_without_starting_ui():
    import chinuuu.gui
    import chinuuu.cli
    assert callable(chinuuu.gui.main)
    assert callable(chinuuu.cli.main)


def test_calendar_event_ics_uses_utc_after_local_timezone_conversion(tmp_path):
    store, workspace = _make_store(tmp_path)
    service = ProductivityService(store, workspace)
    # An explicit offset must be normalized to UTC in DTSTART/DTEND.
    start = (datetime.now(timezone.utc) + timedelta(days=3)).replace(second=0, microsecond=0)
    local_zone = timezone(timedelta(hours=5, minutes=30))
    local_text = start.astimezone(local_zone).isoformat()
    service.create_calendar_event("Timezone Check", local_text)
    payload = next((workspace / "calendar_exports").glob("*.ics")).read_text(encoding="utf-8")
    assert f"DTSTART:{start.strftime('%Y%m%dT%H%M%SZ')}" in payload


def test_reminder_scheduler_fires_due_reminder_once(tmp_path):
    import threading
    from chinuuu.productivity import ReminderScheduler

    store, workspace = _make_store(tmp_path)
    with store._connect() as con:
        due = datetime.now(timezone.utc).replace(microsecond=0).isoformat(timespec="seconds")
        con.execute(
            "INSERT INTO reminders(title,due_at,status,created_at) VALUES(?,?,'pending',?)",
            ("Scheduler acceptance", due, store._now()),
        )
    received: list[str] = []
    notified = threading.Event()

    def notify(message: str) -> None:
        received.append(message)
        notified.set()

    scheduler = ReminderScheduler(store, notify, interval_seconds=2)
    scheduler.start()
    try:
        assert notified.wait(3.5), "scheduler did not emit the due reminder"
    finally:
        scheduler.stop()
    assert received == ["Reminder #1: Scheduler acceptance"]
    with store._connect() as con:
        status = con.execute("SELECT status FROM reminders WHERE id=1").fetchone()["status"]
    assert status == "fired"


def test_presentation_generator_creates_valid_pptx_when_dependency_installed(tmp_path):
    pytest.importorskip("pptx")
    from pptx import Presentation

    workspace = tmp_path / "workspace"
    generator = ArtifactGenerator(workspace)
    result = generator.create_presentation(
        "presentations/overview.pptx", "Project Overview",
        "## Problem\n- Existing process is manual\n- Data is fragmented\n---\n## Proposed Solution\n- Centralized workflow\n- Auditable status",
    )
    target = workspace / "presentations" / "overview.pptx"
    assert "created and verified" in result
    deck = Presentation(target)
    assert len(deck.slides) == 3
    assert deck.slides[1].shapes.title.text == "Problem"
    assert "Existing process is manual" in deck.slides[1].placeholders[1].text


def test_document_indexer_like_fallback_includes_page_metadata(tmp_path):
    store, workspace = _make_store(tmp_path)
    (workspace / "fallback.txt").write_text("Fallback engine stores reliable retrieval metadata.", encoding="utf-8")
    indexer = DocumentIndexer(store, workspace)
    indexer.index_file("fallback.txt")
    # Simulate a SQLite build without FTS5 after the regular schema migration.
    with store._connect() as con:
        con.execute("DROP TABLE document_chunks_fts")
    result = indexer.search("reliable retrieval")
    assert "fallback.txt" in result
    assert "reliable retrieval metadata" in result


def test_local_semantic_index_and_search_uses_cosine_similarity(tmp_path):
    class FakeEmbedder:
        model = "fake-local-embedder"

        def embed(self, texts):
            output = []
            for text in texts:
                folded = text.casefold()
                output.append([1.0, 0.0] if any(word in folded for word in ("database", "relational", "normalization")) else [0.0, 1.0])
            return output

    store, workspace = _make_store(tmp_path)
    (workspace / "db.md").write_text("Database normalization reduces relational redundancy.", encoding="utf-8")
    (workspace / "graphics.md").write_text("Graphics pipelines transform vertices and rasterize triangles.", encoding="utf-8")
    indexer = DocumentIndexer(store, workspace, FakeEmbedder())
    assert "semantic vectors stored" in indexer.index_file("db.md")
    assert "semantic vectors stored" in indexer.index_file("graphics.md")
    result = indexer.search("normalization and relational storage")
    assert "Semantic local document search results" in result
    assert "db.md" in result
    assert "graphics.md" not in result


def test_semantic_indexing_retries_embedding_for_unchanged_document(tmp_path):
    class ToggleEmbedder:
        model = "test-retry-model"
        available = False

        def embed(self, texts):
            if not self.available:
                raise ToolError("model is not installed")
            return [[0.5, 0.5] for _ in texts]

    store, workspace = _make_store(tmp_path)
    (workspace / "notes.md").write_text("Persistent local note about networks.", encoding="utf-8")
    embedder = ToggleEmbedder()
    indexer = DocumentIndexer(store, workspace, embedder)
    assert "Semantic indexing unavailable" in indexer.index_file("notes.md")
    embedder.available = True
    assert "semantic embeddings refreshed" in indexer.index_file("notes.md")
    result = indexer.search("network notes")
    assert "Semantic local document search results" in result


def test_ollama_embedder_is_loopback_only_and_validates_response(monkeypatch):
    import io
    from chinuuu.embeddings import OllamaEmbedder

    with pytest.raises(ToolError, match="loopback Ollama URL"):
        OllamaEmbedder("https://example.com", "nomic-embed-text")

    captured = {}
    class Response(io.BytesIO):
        pass

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["payload"] = json.loads(request.data.decode("utf-8"))
        captured["timeout"] = timeout
        return Response(json.dumps({"embeddings": [[0.25, 0.75]]}).encode("utf-8"))

    monkeypatch.setattr("chinuuu.embeddings.urlopen", fake_urlopen)
    embedder = OllamaEmbedder("http://127.0.0.1:11434", "nomic-embed-text")
    assert embedder.embed(["local document chunk"]) == [[0.25, 0.75]]
    assert captured["url"].endswith("/api/embed")
    assert captured["payload"]["options"]["num_gpu"] == 0
    assert captured["payload"]["model"] == "nomic-embed-text"


def test_ollama_embedder_rejects_invalid_vector_values(monkeypatch):
    import io
    from chinuuu.embeddings import OllamaEmbedder

    class Response(io.BytesIO):
        pass

    monkeypatch.setattr("chinuuu.embeddings.urlopen", lambda *_args, **_kwargs: Response(b'{"embeddings":[[NaN]]}'))
    with pytest.raises(ToolError, match="non-finite"):
        OllamaEmbedder("http://localhost:11434", "local-model").embed(["test input"])
