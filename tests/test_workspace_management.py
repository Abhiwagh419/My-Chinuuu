from pathlib import Path

import pytest

from chinuuu.assistant import Assistant
from chinuuu.config import Settings
from chinuuu.database import LocalStore
from chinuuu.tools import ToolError, ToolRegistry


class NeverCallModel:
    def chat(self, *_args, **_kwargs):
        raise AssertionError("A deterministic workspace command must not call the LLM")

    def chat_message(self, *_args, **_kwargs):
        raise AssertionError("A deterministic workspace command must not call the LLM")


def make_assistant(tmp_path: Path):
    store = LocalStore(tmp_path / "data" / "chinuuu.sqlite3")
    assistant = Assistant(Settings(), store, NeverCallModel())
    workspace = tmp_path / "workspace"
    registry = ToolRegistry(workspace)
    return assistant, registry, workspace


def test_recursive_inventory_reports_live_workspace_tree(tmp_path):
    workspace = tmp_path / "workspace"
    (workspace / "test_folder" / "nested").mkdir(parents=True)
    (workspace / "test_folder" / "nested" / "first_program.py").write_text(
        "print('hello')\n", encoding="utf-8"
    )
    registry = ToolRegistry(workspace)

    result = registry.execute("inspect_workspace", {"path": "."})

    assert f"Workspace: {workspace.resolve()}" in result
    assert "test_folder/" in result
    assert "nested/" in result
    assert "first_program.py" in result
    assert "1 files" in result


def test_human_spaced_folder_name_resolves_to_underscored_path(tmp_path):
    workspace = tmp_path / "workspace"
    (workspace / "test_folder").mkdir(parents=True)
    registry = ToolRegistry(workspace)

    assert registry.workspace.resolve_existing_target("test folder") == "test_folder"


def test_directory_delete_requires_confirmation_and_removes_nested_contents(tmp_path):
    workspace = tmp_path / "workspace"
    target = workspace / "test_folder" / "nested"
    target.mkdir(parents=True)
    (target / "first_program.py").write_text("print('hello')\n", encoding="utf-8")
    registry = ToolRegistry(workspace)

    denied = registry.execute("delete_workspace_directory", {"path": "test_folder"})
    assert denied.startswith("Not executed:")
    assert (workspace / "test_folder").is_dir()

    confirmations = []
    approved = registry.execute(
        "delete_workspace_directory",
        {"path": "test_folder"},
        lambda name, args: confirmations.append((name, args)) or True,
    )
    assert confirmations == [("delete_workspace_directory", {"path": "test_folder"})]
    assert "Deleted directory: test_folder" in approved
    assert "1 subdirectories, 1 files" in approved
    assert "Verification: target no longer exists." in approved
    assert not (workspace / "test_folder").exists()
    assert "directory is absent" in registry.verify_mutation(
        "delete_workspace_directory", {"path": "test_folder"}
    )


def test_directory_delete_cannot_delete_workspace_root(tmp_path):
    registry = ToolRegistry(tmp_path / "workspace")
    with pytest.raises(ToolError):
        registry.execute("delete_workspace_directory", {"path": "."}, lambda *_: True)


def test_directory_delete_rejects_symlink_inside_tree(tmp_path):
    workspace = tmp_path / "workspace"
    target = workspace / "test_folder"
    target.mkdir(parents=True)
    outside = tmp_path / "outside.txt"
    outside.write_text("preserve", encoding="utf-8")
    try:
        (target / "linked.txt").symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("Symlink creation is not available on this platform")

    registry = ToolRegistry(workspace)
    with pytest.raises(ToolError, match="symbolic link or junction"):
        registry.workspace.preview_delete_directory("test_folder")
    assert outside.read_text(encoding="utf-8") == "preserve"
    assert target.is_dir()


def test_direct_delete_test_folder_uses_real_filesystem_and_confirmation(tmp_path):
    assistant, registry, workspace = make_assistant(tmp_path)
    target = workspace / "test_folder"
    target.mkdir(parents=True)
    (target / "first_program.py").write_text("print(2 + 2)\n", encoding="utf-8")
    prompt_calls = []

    result = assistant.respond(
        "delete test folder",
        registry=registry,
        confirm=lambda name, args: prompt_calls.append((name, args)) or True,
    )

    assert prompt_calls == [("delete_workspace_directory", {"path": "test_folder"})]
    assert not target.exists()
    assert "Deleted directory: test_folder" in result
    assert "Filesystem verification: directory is absent" in result


def test_direct_delete_decline_leaves_folder_untouched(tmp_path):
    assistant, registry, workspace = make_assistant(tmp_path)
    target = workspace / "test_folder"
    target.mkdir(parents=True)
    (target / "keep.txt").write_text("keep", encoding="utf-8")

    result = assistant.respond(
        "delete test folder",
        registry=registry,
        confirm=lambda *_: False,
    )

    assert "Not executed" in result
    assert target.is_dir()
    assert (target / "keep.txt").read_text(encoding="utf-8") == "keep"


def test_direct_workspace_question_uses_recursive_inventory(tmp_path):
    assistant, registry, workspace = make_assistant(tmp_path)
    (workspace / "test_folder").mkdir(parents=True)
    (workspace / "test_folder" / "first_program.py").write_text("print(4)\n", encoding="utf-8")

    result = assistant.respond("whats all in my workspace?", registry=registry)

    assert str(workspace.resolve()) in result
    assert "test_folder/" in result
    assert "first_program.py" in result


def test_move_path_requires_confirmation_and_never_overwrites(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "old.txt").write_text("content", encoding="utf-8")
    registry = ToolRegistry(workspace)

    denied = registry.execute(
        "move_workspace_path",
        {"source": "old.txt", "destination": "new.txt"},
    )
    assert denied.startswith("Not executed:")
    assert (workspace / "old.txt").exists()

    result = registry.execute(
        "move_workspace_path",
        {"source": "old.txt", "destination": "new.txt"},
        lambda *_: True,
    )
    assert "Moved: old.txt -> new.txt" in result
    assert not (workspace / "old.txt").exists()
    assert (workspace / "new.txt").read_text(encoding="utf-8") == "content"
    assert "source is absent" in registry.verify_mutation(
        "move_workspace_path", {"source": "old.txt", "destination": "new.txt"}
    )


def test_move_path_rejects_existing_destination(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "source.txt").write_text("source", encoding="utf-8")
    (workspace / "destination.txt").write_text("keep", encoding="utf-8")
    registry = ToolRegistry(workspace)

    with pytest.raises(ToolError, match="never overwrite"):
        registry.execute(
            "move_workspace_path",
            {"source": "source.txt", "destination": "destination.txt"},
            lambda *_: True,
        )
    assert (workspace / "source.txt").read_text(encoding="utf-8") == "source"
    assert (workspace / "destination.txt").read_text(encoding="utf-8") == "keep"


def test_failed_delete_cannot_be_followed_by_model_success_claim(tmp_path):
    class DeleteClient:
        def __init__(self):
            self.calls = 0

        def chat_message(self, messages, tools=None):
            self.calls += 1
            return {
                "role": "assistant",
                "content": "",
                "tool_calls": [{
                    "function": {
                        "name": "delete_workspace_directory",
                        "arguments": {"path": "missing_folder"},
                    }
                }],
            }

    from chinuuu.planner import ToolExecutionPolicy

    assistant = Assistant.__new__(Assistant)
    assistant.policy = ToolExecutionPolicy(max_tool_rounds=2, max_tool_calls=4)
    assistant.client = DeleteClient()
    registry = ToolRegistry(tmp_path / "workspace")

    result = assistant._agent_loop(
        [{"role": "user", "content": "Delete missing_folder"}],
        registry,
        confirm=lambda *_: True,
        user_text="Delete missing_folder",
    )

    assert "Filesystem operation failed" in result
    assert "No success is being reported" in result
    assert assistant.client.calls == 1


def test_direct_file_delete_is_confirmed_and_verified(tmp_path):
    assistant, registry, workspace = make_assistant(tmp_path)
    target = workspace / "first_program.py"
    target.write_text("print(2 + 2)\n", encoding="utf-8")

    result = assistant.respond(
        "delete first_program.py",
        registry=registry,
        confirm=lambda name, args: name == "delete_workspace_file" and args == {"path": "first_program.py"},
    )

    assert not target.exists()
    assert "Deleted file: first_program.py" in result
    assert "Filesystem verification: file is absent" in result


def test_delete_target_must_exist_inside_workspace(tmp_path):
    assistant, registry, workspace = make_assistant(tmp_path)
    outside = workspace.parent / "test_folder"
    outside.mkdir()

    result = assistant.respond("delete test folder", registry=registry, confirm=lambda *_: True)

    assert "Deletion failed" in result
    assert outside.is_dir()


def test_spoken_target_is_ambiguous_when_underscore_and_hyphen_variants_exist(tmp_path):
    workspace = tmp_path / "workspace"
    (workspace / "test_folder").mkdir(parents=True)
    (workspace / "test-folder").mkdir(parents=True)
    registry = ToolRegistry(workspace)

    with pytest.raises(ToolError, match="ambiguous"):
        registry.workspace.resolve_existing_target("test folder")


def test_directory_confirmation_preview_uses_real_path_and_content_counts(tmp_path, monkeypatch):
    from chinuuu import cli

    workspace = tmp_path / "workspace"
    target = workspace / "test_folder"
    target.mkdir(parents=True)
    (target / "first_program.py").write_text("print(2 + 2)\n", encoding="utf-8")
    registry = ToolRegistry(workspace)
    rendered = []
    questions = []

    monkeypatch.setattr(cli.console, "print", lambda panel: rendered.append(panel))
    monkeypatch.setattr(
        cli.Confirm,
        "ask",
        lambda question, default=False: questions.append((question, default)) or True,
    )

    approved = cli._confirm_tool_call(
        "delete_workspace_directory", {"path": "test_folder"}, registry
    )

    assert approved is True
    assert questions == [("Permanently delete this directory and all its contents?", False)]
    panel_text = str(rendered[0].renderable)
    assert str(target.resolve()) in panel_text
    assert "1 files" in panel_text
    expected_size = (target / "first_program.py").stat().st_size
    assert f"{expected_size} bytes" in panel_text
