from pathlib import Path

from chinuuu.assistant import Assistant
from chinuuu.config import Settings
from chinuuu.database import LocalStore
from chinuuu.tools import ToolRegistry


class NeverCallModel:
    def chat(self, *_args, **_kwargs):
        raise AssertionError("Deterministic workspace command must not call the LLM")

    def chat_message(self, *_args, **_kwargs):
        raise AssertionError("Deterministic workspace command must not call the LLM")


def make_assistant(tmp_path: Path):
    store = LocalStore(tmp_path / "data" / "chinuuu.sqlite3")
    assistant = Assistant(Settings(), store, NeverCallModel())
    workspace = tmp_path / "Projects" / "Chinuuu" / "workspace"
    registry = ToolRegistry(workspace)
    return assistant, store, registry, workspace


def test_create_bare_underscored_folder_targets_workspace_not_project_root(tmp_path):
    assistant, _store, registry, workspace = make_assistant(tmp_path)
    project_root = workspace.parent

    result = assistant.respond("Create test_folder", registry=registry)

    expected = workspace / "test_folder"
    assert expected.is_dir()
    assert str(expected.resolve()) in result
    assert not (project_root / "test_folder").exists()
    assert "Filesystem verification: directory exists." in result


def test_existing_workspace_folder_is_verified_from_disk_not_conversation(tmp_path):
    assistant, _store, registry, workspace = make_assistant(tmp_path)
    target = workspace / "test_folder"
    target.mkdir(parents=True)

    result = assistant.respond("Create test_folder", registry=registry)

    assert "Already exists:" in result
    assert str(target.resolve()) in result
    assert target.is_dir()


def test_workspace_listing_is_deterministic_and_uses_actual_root(tmp_path):
    assistant, _store, registry, workspace = make_assistant(tmp_path)
    (workspace / "real_file.txt").write_text("verified", encoding="utf-8")

    result = assistant.respond("List all files in my workspace", registry=registry)

    assert f"Workspace: {workspace.resolve()}" in result
    assert "real_file.txt" in result


def test_short_list_workspace_command_reads_live_filesystem_without_llm(tmp_path):
    assistant, _store, registry, workspace = make_assistant(tmp_path)
    (workspace / "actual_file.txt").write_text("real disk data", encoding="utf-8")

    result = assistant.respond("list workspace", registry=registry)

    assert f"Workspace: {workspace.resolve()}" in result
    assert "actual_file.txt" in result
    assert "real disk data" not in result  # inventory reports metadata, not file contents
    assert "Numbers/" not in result
    assert "todo.txt" not in result
    assert "config.json" not in result


def test_short_list_workspace_reports_empty_when_filesystem_is_empty(tmp_path):
    assistant, _store, registry, workspace = make_assistant(tmp_path)

    result = assistant.respond("list workspace", registry=registry)

    assert f"Workspace: {workspace.resolve()}" in result
    assert "(empty)" in result
    assert "Summary: 0 directories, 0 files, 0 bytes" in result
    assert "Numbers" not in result


def test_explicit_folder_word_supports_single_word_name(tmp_path):
    assistant, _store, registry, workspace = make_assistant(tmp_path)

    result = assistant.respond("Create folder notes", registry=registry)

    assert (workspace / "notes").is_dir()
    assert str((workspace / "notes").resolve()) in result


def test_ambiguous_generic_create_is_not_routed(tmp_path):
    from chinuuu.assistant import _extract_simple_directory_create

    assert _extract_simple_directory_create("Create a folder") is None
    assert _extract_simple_directory_create("Create a Python application") is None
    assert _extract_simple_directory_create("Create test_folder") == "test_folder"


def test_batch_create_request_parses_explicit_file_names_and_range():
    from chinuuu.assistant import _extract_batch_create_files

    parsed = _extract_batch_create_files(
        "create folder Numbers and create files named 1,2,3,4,5,6,7,8,9 and 10 in it"
    )
    assert parsed == ("Numbers", [str(number) for number in range(1, 11)])

    ranged = _extract_batch_create_files(
        "create folder Numbers and create files named 1 to 10 in it"
    )
    assert ranged == ("Numbers", [str(number) for number in range(1, 11)])


def test_batch_create_creates_and_verifies_all_requested_empty_files(tmp_path):
    assistant, _store, registry, workspace = make_assistant(tmp_path)

    result = assistant.respond(
        "create folder Numbers and create files named 1,2,3,4,5,6,7,8,9 and 10 in it",
        registry=registry,
    )

    target = workspace / "Numbers"
    assert target.is_dir()
    for number in range(1, 11):
        file_path = target / str(number)
        assert file_path.is_file()
        assert file_path.read_bytes() == b""
    assert "Verified files: 10 of 10 present." in result
    assert "File contents: empty files" in result


def test_batch_create_is_idempotent_and_never_overwrites_existing_files(tmp_path):
    assistant, _store, registry, workspace = make_assistant(tmp_path)
    target = workspace / "Numbers"
    target.mkdir(parents=True)
    (target / "1").write_text("keep this", encoding="utf-8")

    result = assistant.respond(
        "create folder Numbers and create files named 1,2,3 in it",
        registry=registry,
    )

    assert (target / "1").read_text(encoding="utf-8") == "keep this"
    assert (target / "2").is_file()
    assert (target / "3").is_file()
    assert "Preserved existing files: 1" in result
    assert "Verified files: 3 of 3 present." in result


def test_mutating_request_cannot_report_success_without_a_tool_call(tmp_path):
    class ProseOnlyModel:
        def chat(self, *_args, **_kwargs):
            return "All files and folders were created successfully."

        def chat_message(self, *_args, **_kwargs):
            return {"role": "assistant", "content": "All files and folders were created successfully."}

    store = LocalStore(tmp_path / "data" / "chinuuu.sqlite3")
    assistant = Assistant(Settings(), store, ProseOnlyModel())
    workspace = tmp_path / "Projects" / "Chinuuu" / "workspace"
    registry = ToolRegistry(workspace)

    result = assistant.respond(
        "create a file named sample.txt in workspace",
        registry=registry,
    )

    assert "No workspace operation was executed" in result
    assert not (workspace / "sample.txt").exists()
