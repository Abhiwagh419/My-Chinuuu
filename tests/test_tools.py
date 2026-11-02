from pathlib import Path
import pytest

from chinuuu.tools import ToolRegistry, ToolError, safe_calculate


def test_workspace_list_and_read_are_confined(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "notes.txt").write_text("hello Chinuuu", encoding="utf-8")
    tools = ToolRegistry(workspace)
    assert "notes.txt" in tools.workspace.list_files(".")
    assert "hello Chinuuu" in tools.workspace.read_file("notes.txt")


def test_path_traversal_is_blocked(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "secret.txt"
    outside.write_text("private", encoding="utf-8")
    tools = ToolRegistry(workspace)
    with pytest.raises(ToolError):
        tools.workspace.read_file("../secret.txt")


def test_absolute_paths_are_blocked(tmp_path):
    tools = ToolRegistry(tmp_path / "workspace")
    with pytest.raises(ToolError):
        tools.workspace.read_file(str(tmp_path / "secret.txt"))


def test_new_file_creation_does_not_require_confirmation(tmp_path):
    workspace = tmp_path / "workspace"
    tools = ToolRegistry(workspace)
    result = tools.execute("write_workspace_file", {"path": "new.txt", "content": "safe"})
    assert "Created file:" in result
    assert (workspace / "new.txt").read_text(encoding="utf-8") == "safe"


def test_new_file_creation_does_not_prompt_even_if_model_sets_overwrite_true(tmp_path):
    workspace = tmp_path / "workspace"
    tools = ToolRegistry(workspace)
    result = tools.execute(
        "write_workspace_file",
        {"path": "new.txt", "content": "safe", "overwrite": True},
    )
    assert "Created file:" in result
    assert (workspace / "new.txt").read_text(encoding="utf-8") == "safe"


def test_existing_file_overwrite_requires_confirmation_and_backup(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "new.txt").write_text("first", encoding="utf-8")
    tools = ToolRegistry(workspace)
    asked = []
    approve = lambda name, args: asked.append((name, args)) or True
    result = tools.execute(
        "write_workspace_file",
        {"path": "new.txt", "content": "second", "overwrite": True},
        approve,
    )
    assert "Replaced file:" in result
    assert asked and asked[0][0] == "write_workspace_file"
    assert (workspace / "new.txt").read_text(encoding="utf-8") == "second"
    assert (workspace / ".chinuuu_backups" / "new.txt.backup").read_text(encoding="utf-8") == "first"


def test_existing_file_is_not_overwritten_without_explicit_overwrite_flag(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "keep.txt").write_text("original", encoding="utf-8")
    tools = ToolRegistry(workspace)
    with pytest.raises(ToolError, match="already exists"):
        tools.execute("write_workspace_file", {"path": "keep.txt", "content": "replacement"})
    assert (workspace / "keep.txt").read_text(encoding="utf-8") == "original"


def test_directory_creation_does_not_require_confirmation(tmp_path):
    workspace = tmp_path / "workspace"
    tools = ToolRegistry(workspace)
    result = tools.execute("create_workspace_directory", {"path": "projects/demo"})
    assert "Created directory:" in result
    assert (workspace / "projects" / "demo").is_dir()


def test_file_deletion_requires_confirmation(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "remove.txt"
    target.write_text("delete me", encoding="utf-8")
    tools = ToolRegistry(workspace)
    result = tools.execute("delete_workspace_file", {"path": "remove.txt"})
    assert "Not executed" in result
    assert target.exists()
    result = tools.execute(
        "delete_workspace_file", {"path": "remove.txt"}, lambda *_: True
    )
    assert "Deleted file" in result
    assert not target.exists()


def test_calculator_supports_arithmetic_and_rejects_code():
    assert safe_calculate("(12 + 8) * 3") == "60"
    with pytest.raises(ToolError):
        safe_calculate("__import__('os').system('whoami')")


def test_tool_registry_does_not_expose_shell(tmp_path):
    tools = ToolRegistry(tmp_path / "workspace")
    names = {name for name, _, _ in tools.descriptions()}
    assert "run_shell" not in names
    assert "execute_powershell" not in names


def test_tool_permission_descriptions_are_accurate(tmp_path):
    tools = ToolRegistry(tmp_path / "workspace")
    permissions = {name: permission for name, permission, _ in tools.descriptions()}
    assert permissions["write_workspace_file"] == "Approval only when replacing an existing file"
    assert permissions["create_workspace_directory"] == "No confirmation required"
    assert permissions["delete_workspace_file"] == "Confirmation required"
    assert permissions["delete_workspace_directory"] == "Confirmation required"
    assert permissions["move_workspace_path"] == "Confirmation required"
    assert permissions["inspect_workspace"] == "No confirmation required"
    assert permissions["read_workspace_file"] == "No confirmation required"


def test_sensitive_files_are_blocked(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / ".env").write_text("SECRET=value", encoding="utf-8")
    tools = ToolRegistry(workspace)
    with pytest.raises(ToolError):
        tools.workspace.read_file(".env")
    assert ".env" not in tools.workspace.list_files(".")


def test_parent_directory_segments_are_blocked_even_when_normalized_inside(tmp_path):
    tools = ToolRegistry(tmp_path / "workspace")
    with pytest.raises(ToolError):
        tools.workspace.read_file("subdir/../notes.txt")


def test_tool_argument_types_are_validated_before_execution(tmp_path):
    tools = ToolRegistry(tmp_path / "workspace")
    with pytest.raises(ToolError):
        tools.execute("write_workspace_file", {"path": "a.txt", "content": "x", "overwrite": "true"}, lambda *_: True)


def test_directory_creation_returns_verified_absolute_path(tmp_path):
    workspace = tmp_path / "Chinuuu" / "workspace"
    tools = ToolRegistry(workspace)
    result = tools.execute("create_workspace_directory", {"path": "test_folder"})
    assert str(workspace.resolve() / "test_folder") in result
    assert (workspace / "test_folder").is_dir()


def test_workspace_listing_reports_actual_absolute_root(tmp_path):
    workspace = tmp_path / "Chinuuu" / "workspace"
    tools = ToolRegistry(workspace)
    result = tools.workspace.list_files(".")
    assert f"Workspace: {workspace.resolve()}" in result
    assert f"Directory: {workspace.resolve()}" in result
