from __future__ import annotations

import sys
import time

import pytest

from chinuuu.assistant import (
    Assistant,
    _extract_python_run_path,
    _handle_deterministic_workspace_request,
)
from chinuuu.cli import _confirm_tool_call
from chinuuu.planner import ToolExecutionPolicy
from chinuuu.tools import ToolError, ToolRegistry


def test_run_python_requires_application_confirmation(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "hello.py").write_text("print('hello')\n", encoding="utf-8")
    registry = ToolRegistry(workspace)

    result = registry.execute("run_workspace_python", {"path": "hello.py"}, confirm=None)

    assert result.startswith("Not executed:")


def test_approved_python_script_runs_and_returns_exit_code(tmp_path, capfd):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "hello.py").write_text("print('hello from Chinuuu')\n", encoding="utf-8")
    registry = ToolRegistry(workspace)

    result = registry.execute(
        "run_workspace_python",
        {"path": "hello.py"},
        confirm=lambda name, args: name == "run_workspace_python" and args["path"] == "hello.py",
    )
    captured = capfd.readouterr()

    assert "hello from Chinuuu" in captured.out
    assert "completed successfully" in result
    assert "Exit code: 0" in result


def test_python_runner_rejects_paths_outside_workspace(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside.py"
    outside.write_text("print('outside')", encoding="utf-8")
    registry = ToolRegistry(workspace)

    with pytest.raises(ToolError):
        registry.execute(
            "run_workspace_python", {"path": "../outside.py"}, confirm=lambda *_: True
        )


def test_python_runner_only_accepts_py_files(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "notes.txt").write_text("not python", encoding="utf-8")
    registry = ToolRegistry(workspace)

    with pytest.raises(ToolError):
        registry.execute(
            "run_workspace_python", {"path": "notes.txt"}, confirm=lambda *_: True
        )


def test_python_runner_enforces_runtime_timeout(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "slow.py").write_text(
        "import time\ntime.sleep(5)\n", encoding="utf-8"
    )
    registry = ToolRegistry(workspace)
    started = time.monotonic()

    with pytest.raises(ToolError, match="time limit"):
        registry.execute(
            "run_workspace_python",
            {"path": "slow.py", "timeout_seconds": 1},
            confirm=lambda *_: True,
        )

    assert time.monotonic() - started < 4


def test_python_runner_validates_timeout_bounds(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "hello.py").write_text("print('ok')", encoding="utf-8")
    registry = ToolRegistry(workspace)

    with pytest.raises(ToolError, match="1 to 30 seconds"):
        registry.execute(
            "run_workspace_python",
            {"path": "hello.py", "timeout_seconds": 60},
            confirm=lambda *_: True,
        )


def test_deterministic_run_command_routes_to_confirmed_runner(tmp_path, capfd):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    script = workspace / "sample.py"
    script.write_text("print('direct route')\n", encoding="utf-8")
    registry = ToolRegistry(workspace)
    confirmations = []

    result = _handle_deterministic_workspace_request(
        "run sample.py",
        registry,
        lambda name, args: confirmations.append((name, args)) or True,
    )
    captured = capfd.readouterr()

    assert "direct route" in captured.out
    assert "completed successfully" in result
    assert confirmations == [("run_workspace_python", {"path": "sample.py"})]


def test_run_path_parser_requires_explicit_python_file_path():
    assert _extract_python_run_path("run sample.py") == "sample.py"
    assert _extract_python_run_path("execute the file test_folder/sample.py") == "test_folder/sample.py"
    assert _extract_python_run_path("execute that file") is None


def test_confirmation_preview_shows_code_and_privilege_warning(tmp_path, monkeypatch):
    from chinuuu import cli

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "sample.py").write_text("print('review before run')\n", encoding="utf-8")
    registry = ToolRegistry(workspace)
    rendered = []
    questions = []
    monkeypatch.setattr(cli.console, "print", lambda panel: rendered.append(panel))
    monkeypatch.setattr(
        cli.Confirm,
        "ask",
        lambda question, default=False: questions.append((question, default)) or False,
    )

    approved = _confirm_tool_call(
        "run_workspace_python", {"path": "sample.py"}, registry
    )

    panel_text = str(rendered[0].renderable)
    assert approved is False
    assert "review before run" in panel_text
    assert "NOT an OS sandbox" in panel_text
    assert "sample.py" in panel_text
    assert questions == [("Run this Python script now?", False)]


def test_agent_does_not_claim_script_ran_without_tool_invocation(tmp_path):
    class NoToolClient:
        def chat_message(self, messages, tools=None):
            return {"role": "assistant", "content": "Done, I ran the script."}

    assistant = Assistant.__new__(Assistant)
    assistant.policy = ToolExecutionPolicy(max_tool_rounds=2, max_tool_calls=4)
    assistant.client = NoToolClient()
    registry = ToolRegistry(tmp_path / "workspace")

    result = assistant._agent_loop(
        [{"role": "user", "content": "execute that file in terminal"}],
        registry,
        confirm=lambda *_: True,
        user_text="execute that file in terminal",
    )

    assert "No Python script was run" in result
    assert "No program was executed" in result


def test_missing_script_does_not_trigger_confirmation(tmp_path):
    registry = ToolRegistry(tmp_path / "workspace")
    confirmation_calls = []

    with pytest.raises(ToolError):
        registry.execute(
            "run_workspace_python",
            {"path": "missing.py"},
            confirm=lambda *args: confirmation_calls.append(args) or True,
        )

    assert not confirmation_calls
