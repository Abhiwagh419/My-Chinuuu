from __future__ import annotations

import pytest

from chinuuu.computer import ComputerExplorer
from chinuuu.errors import ToolError


def test_computer_explorer_lists_real_directory(tmp_path):
    folder = tmp_path / "Documents"
    folder.mkdir()
    (folder / "notes.txt").write_text("hello", encoding="utf-8")
    result = ComputerExplorer(tmp_path / "workspace").list_directory(str(folder))
    assert str(folder.resolve()) in result
    assert "notes.txt" in result


def test_computer_explorer_blocks_credential_and_system_paths(tmp_path):
    explorer = ComputerExplorer(tmp_path / "workspace")
    with pytest.raises(ToolError, match="excluded"):
        explorer.list_directory(str(tmp_path / ".ssh"))


def test_computer_explorer_rejects_relative_paths(tmp_path):
    explorer = ComputerExplorer(tmp_path / "workspace")
    with pytest.raises(ToolError, match="absolute local paths"):
        explorer.list_directory("Documents")


def test_computer_explorer_reads_only_small_supported_text_files_under_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    file_path = home / "notes.txt"
    file_path.write_text("private notes", encoding="utf-8")
    monkeypatch.setattr("chinuuu.computer.Path.home", lambda: home)
    explorer = ComputerExplorer(tmp_path / "workspace")
    assert "private notes" in explorer.read_text_file(str(file_path))


def test_computer_explorer_refuses_read_outside_user_profile(tmp_path, monkeypatch):
    # pytest's tmp_path is commonly inside the real Windows profile under
    # AppData\Local\Temp. Give the explorer an isolated synthetic home so
    # this test always exercises a path outside that profile on every OS.
    synthetic_home = tmp_path / "synthetic-user-profile"
    synthetic_home.mkdir()
    monkeypatch.setattr("chinuuu.computer.Path.home", lambda: synthetic_home)
    explorer = ComputerExplorer(tmp_path / "workspace")
    file_path = tmp_path / "outside.txt"
    file_path.write_text("not readable", encoding="utf-8")
    with pytest.raises(ToolError, match="limited to the current user's profile"):
        explorer.read_text_file(str(file_path))


def test_computer_explorer_can_read_supported_text_under_local_temp(tmp_path, monkeypatch):
    # Windows local temporary files are inside the user profile and are
    # intentionally allowed; Roaming application data remains excluded.
    home = tmp_path / "synthetic-user-profile"
    temp_dir = home / "AppData" / "Local" / "Temp"
    temp_dir.mkdir(parents=True)
    file_path = temp_dir / "notes.txt"
    file_path.write_text("temporary test note", encoding="utf-8")
    monkeypatch.setattr("chinuuu.computer.Path.home", lambda: home)
    explorer = ComputerExplorer(tmp_path / "workspace")
    assert "temporary test note" in explorer.read_text_file(str(file_path))


def test_computer_explorer_locations_report_is_read_only(tmp_path):
    result = ComputerExplorer(tmp_path / "workspace").locations()
    assert "Computer locations:" in result
    assert "read-only" in result


def test_computer_explorer_allows_windows_local_temp_directory(tmp_path):
    # Windows pytest commonly places tmp_path beneath %LOCALAPPDATA%\Temp.
    # The explorer should allow that benign temporary subtree, not all AppData.
    folder = tmp_path / "AppData" / "Local" / "Temp" / "pytest-sandbox"
    folder.mkdir(parents=True)
    (folder / "notes.txt").write_text("hello", encoding="utf-8")
    result = ComputerExplorer(tmp_path / "workspace").list_directory(str(folder))
    assert "notes.txt" in result


def test_computer_explorer_still_blocks_other_appdata_subtrees(tmp_path):
    folder = tmp_path / "AppData" / "Roaming" / "Microsoft" / "Credentials"
    folder.mkdir(parents=True)
    explorer = ComputerExplorer(tmp_path / "workspace")
    with pytest.raises(ToolError, match="excluded|Application data"):
        explorer.list_directory(str(folder))
