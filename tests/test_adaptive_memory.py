from chinuuu.database import LocalStore


def test_old_style_database_is_migrated_without_losing_memory(tmp_path):
    import sqlite3
    db = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(db) as con:
        con.execute(
            "CREATE TABLE memories (id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "content TEXT NOT NULL UNIQUE, category TEXT NOT NULL DEFAULT 'preference', "
            "created_at TEXT NOT NULL)"
        )
        con.execute(
            "INSERT INTO memories(content, category, created_at) VALUES (?, ?, ?)",
            ("From now on, always explain technical concepts", "preference", "2026-10-30T10:00:00+00:00"),
        )
    store = LocalStore(db)
    memories = store.list_memories()
    assert len(memories) == 1
    assert memories[0]["content"] == "From now on, always explain technical concepts"
    assert memories[0]["source"] == "explicit"


def test_candidate_requires_approval_before_becoming_memory(tmp_path):
    store = LocalStore(tmp_path / "chinuuu.sqlite3")
    candidate_id = store.add_candidate(
        "Prefers practical examples.", "learning_style", "repeated_behavior", 0.8,
        "Observed three matching interactions.", 3,
    )
    assert candidate_id is not None
    assert store.list_memories() == []
    assert len(store.list_candidates("pending")) == 1
    assert store.approve_candidate(candidate_id) is True
    saved = store.list_memories()
    assert len(saved) == 1
    assert saved[0]["content"] == "Prefers practical examples."
    assert saved[0]["source"] == "repeated_behavior"
    assert store.list_candidates("pending") == []


def test_repeated_candidate_updates_evidence_without_duplicate_prompt(tmp_path):
    store = LocalStore(tmp_path / "chinuuu.sqlite3")
    first = store.add_candidate("Prefers concise answers.", "communication", "repeated_behavior", .7, "2 events", 2)
    second = store.add_candidate("Prefers concise answers.", "communication", "repeated_behavior", .8, "3 events", 3)
    assert first is not None
    assert second is None
    pending = store.list_candidates("pending")
    assert len(pending) == 1
    assert pending[0]["evidence_count"] == 3
    assert pending[0]["evidence_summary"] == "3 events"


def test_edit_and_delete_memory(tmp_path):
    store = LocalStore(tmp_path / "chinuuu.sqlite3")
    assert store.add_memory("Prefers examples", "learning_style")
    memory_id = int(store.list_memories()[0]["id"])
    assert store.update_memory(memory_id, "Prefers practical engineering examples")
    assert store.get_memory(memory_id)["content"] == "Prefers practical engineering examples"
    assert store.delete_memory(memory_id)
    assert store.get_memory(memory_id) is None


def test_repeated_observations_count_inside_window(tmp_path):
    store = LocalStore(tmp_path / "chinuuu.sqlite3")
    assert store.record_behavior_observation("step_by_step", "working_style", "First request") == 1
    assert store.record_behavior_observation("step_by_step", "working_style", "Second request") == 2
    assert store.record_behavior_observation("step_by_step", "working_style", "Third request") == 3


def test_rejected_inferred_pattern_is_not_repeatedly_suggested(tmp_path):
    store = LocalStore(tmp_path / "chinuuu.sqlite3")
    candidate_id = store.add_candidate(
        "Prefers concise answers.", "communication", "repeated_behavior", .7, "3 events", 3
    )
    assert candidate_id is not None
    assert store.reject_candidate(candidate_id)
    assert store.add_candidate(
        "Prefers concise answers.", "communication", "repeated_behavior", .8, "4 events", 4
    ) is None
