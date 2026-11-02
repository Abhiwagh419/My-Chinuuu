from chinuuu.assistant import Assistant
from chinuuu.planner import ToolExecutionPolicy
from chinuuu.tools import ToolRegistry


def test_direct_open_app_route_is_application_owned(tmp_path, monkeypatch):
    from chinuuu.assistant import _handle_deterministic_workspace_request
    executable = tmp_path / "editor.exe"
    executable.write_bytes(b"test")
    monkeypatch.setattr("chinuuu.desktop.shutil.which", lambda _: str(executable))

    class FakeProcess:
        pid = 777
        returncode = None
        def poll(self):
            return None

    monkeypatch.setattr("chinuuu.desktop.subprocess.Popen", lambda *a, **k: FakeProcess())
    registry = ToolRegistry(tmp_path / "workspace", app_allowlist={"editor": "editor.exe"})
    prompts = []
    answer = _handle_deterministic_workspace_request(
        "open app editor", registry,
        lambda name, args: prompts.append((name, args)) or False,
    )
    assert "Launch request submitted for" in answer
    assert prompts == []


def test_direct_web_search_denied_cleanly_when_disabled(tmp_path):
    from chinuuu.assistant import _handle_deterministic_workspace_request
    registry = ToolRegistry(tmp_path / "workspace", allow_external_network=False)
    result = _handle_deterministic_workspace_request("search web for public Python docs", registry, None)
    assert "Web research unavailable" in result
    assert "disabled" in result


def test_no_fake_web_search_success_if_model_skips_tool(tmp_path):
    class NoToolClient:
        def chat_message(self, messages, tools=None):
            return {"role": "assistant", "content": "I searched the web and found results."}

    assistant = Assistant.__new__(Assistant)
    assistant.policy = ToolExecutionPolicy(max_tool_rounds=2, max_tool_calls=4)
    assistant.client = NoToolClient()
    registry = ToolRegistry(tmp_path / "workspace", allow_external_network=True)
    result = assistant._agent_loop(
        [{"role": "user", "content": "search web for public Python docs"}],
        registry, confirm=lambda *_: True,
        user_text="search web for public Python docs",
    )
    assert "No web search was performed" in result


def test_natural_system_report_request_is_deterministic(tmp_path):
    from chinuuu.assistant import _handle_deterministic_workspace_request
    registry = ToolRegistry(tmp_path / "workspace")
    result = _handle_deterministic_workspace_request("show system report", registry, None)
    assert "CPU utilization:" in result
    assert "RAM:" in result


def test_natural_process_request_is_deterministic(tmp_path):
    from chinuuu.assistant import _handle_deterministic_workspace_request
    registry = ToolRegistry(tmp_path / "workspace")
    result = _handle_deterministic_workspace_request("list running processes", registry, None)
    assert "Top processes by resident memory" in result


def test_natural_app_alias_request_is_deterministic(tmp_path, monkeypatch):
    from chinuuu.assistant import _handle_deterministic_workspace_request
    executable = tmp_path / "editor.exe"
    executable.write_bytes(b"test")
    monkeypatch.setattr("chinuuu.desktop.shutil.which", lambda _: str(executable))

    class FakeProcess:
        pid = 778
        returncode = None
        def poll(self):
            return None

    monkeypatch.setattr("chinuuu.desktop.subprocess.Popen", lambda *a, **k: FakeProcess())
    registry = ToolRegistry(tmp_path / "workspace", app_allowlist={"editor": "editor.exe"})
    prompts = []
    result = _handle_deterministic_workspace_request("open editor", registry, lambda *args: prompts.append(args) or False)
    assert "Launch request submitted" in result
    assert prompts == []


def test_app_launch_success_is_never_claimed_without_tool_call(tmp_path):
    class NoToolClient:
        def chat_message(self, messages, tools=None):
            return {"role": "assistant", "content": "I opened Notepad."}

    assistant = Assistant.__new__(Assistant)
    assistant.policy = ToolExecutionPolicy(max_tool_rounds=2, max_tool_calls=4)
    assistant.client = NoToolClient()
    registry = ToolRegistry(tmp_path / "workspace", app_allowlist={"notepad": "notepad.exe"})
    result = assistant._agent_loop(
        [{"role": "user", "content": "open notepad"}], registry,
        confirm=lambda *_: True, user_text="open notepad",
    )
    assert "No application was launched" in result


def test_natural_python_syntax_check_compiles_without_running(tmp_path):
    from chinuuu.assistant import _handle_deterministic_workspace_request
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "sample.py").write_text(
        "from pathlib import Path\nPath('ran.flag').write_text('yes')\n", encoding="utf-8"
    )
    registry = ToolRegistry(workspace)
    result = _handle_deterministic_workspace_request(
        "check python syntax of sample.py", registry, None
    )
    assert "syntax check passed" in result
    assert not (workspace / "ran.flag").exists()


def test_natural_python_syntax_check_reports_syntax_error(tmp_path):
    from chinuuu.assistant import _handle_deterministic_workspace_request
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "broken.py").write_text("def main(:\n    pass\n", encoding="utf-8")
    registry = ToolRegistry(workspace)
    result = _handle_deterministic_workspace_request(
        "check syntax of broken.py", registry, None
    )
    assert "syntax check failed" in result
    assert "line 1" in result


def test_python_syntax_check_tool_result_is_returned_without_llm_reinterpretation(tmp_path):
    class TwoCallClient:
        def __init__(self):
            self.calls = 0
        def chat_message(self, messages, tools=None):
            self.calls += 1
            if self.calls == 1:
                return {
                    "role": "assistant", "content": "",
                    "tool_calls": [{"function": {"name": "check_workspace_python", "arguments": {"path": "good.py"}}}],
                }
            raise AssertionError("Deterministic syntax result should be returned directly")

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "good.py").write_text("x = 1\n", encoding="utf-8")
    assistant = Assistant.__new__(Assistant)
    assistant.policy = ToolExecutionPolicy(max_tool_rounds=2, max_tool_calls=4)
    assistant.client = TwoCallClient()
    registry = ToolRegistry(workspace)
    result = assistant._agent_loop(
        [{"role": "user", "content": "check syntax of good.py"}],
        registry, None, user_text="check syntax of good.py",
    )
    assert "syntax check passed" in result
    assert assistant.client.calls == 1


def test_open_application_is_no_confirmation_in_registry(tmp_path, monkeypatch):
    executable = tmp_path / "notepad.exe"
    executable.write_bytes(b"test")
    monkeypatch.setattr("chinuuu.desktop.shutil.which", lambda _: str(executable))

    class FakeProcess:
        pid = 1234

    monkeypatch.setattr("chinuuu.desktop.subprocess.Popen", lambda *a, **k: FakeProcess())
    registry = ToolRegistry(tmp_path / "workspace", app_allowlist={"editor": "notepad.exe"})
    approvals = []
    result = registry.execute("open_application", {"alias": "editor"}, lambda *a: approvals.append(a) or False)
    assert "Process ID: 1234" in result
    assert approvals == []


def test_natural_installed_apps_request_uses_real_app_inventory(tmp_path, monkeypatch):
    from chinuuu.assistant import _handle_deterministic_workspace_request
    registry = ToolRegistry(tmp_path / "workspace", app_allowlist={"editor": "notepad.exe"})
    result = _handle_deterministic_workspace_request("list installed apps", registry, None)
    assert "editor" in result


def test_natural_computer_request_uses_computer_explorer(tmp_path):
    from chinuuu.assistant import _handle_deterministic_workspace_request
    registry = ToolRegistry(tmp_path / "workspace")
    result = _handle_deterministic_workspace_request("show my computer", registry, None)
    assert "Computer locations:" in result
    assert "read-only" in result


def test_natural_windows_settings_request_routes_to_safe_page(tmp_path, monkeypatch):
    from chinuuu.assistant import _handle_deterministic_workspace_request
    opened = []
    registry = ToolRegistry(tmp_path / "workspace")
    monkeypatch.setattr("chinuuu.desktop.os.name", "nt")
    monkeypatch.setattr("chinuuu.desktop.os.startfile", lambda target: opened.append(target), raising=False)
    result = _handle_deterministic_workspace_request("open display settings", registry, None)
    assert "ms-settings:display" in result
    assert opened == ["ms-settings:display"]
