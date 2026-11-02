from __future__ import annotations

import os
import subprocess

import pytest

from chinuuu.desktop import ApplicationLauncher
from chinuuu.errors import ToolError
from chinuuu.tools import ToolRegistry


def test_launcher_uses_configured_alias_without_confirmation(monkeypatch, tmp_path):
    executable = tmp_path / "safe-app.exe"
    executable.write_bytes(b"test")
    launched = {}

    class FakeProcess:
        pid = 4321
        returncode = None

    def fake_popen(command, **kwargs):
        launched["command"] = command
        launched["kwargs"] = kwargs
        return FakeProcess()

    monkeypatch.setattr("chinuuu.desktop.shutil.which", lambda name: str(executable))
    monkeypatch.setattr("chinuuu.desktop.subprocess.Popen", fake_popen)
    registry = ToolRegistry(tmp_path / "workspace", app_allowlist={"editor": "safe-app.exe"})
    approvals = []
    result = registry.execute("open_application", {"alias": "editor"}, lambda *args: approvals.append(args) or False)

    assert "Process ID: 4321" in result
    assert launched["command"] == [str(executable)]
    assert launched["kwargs"]["shell"] is False
    assert approvals == []


def test_launcher_rejects_unknown_alias_and_shell_metacharacters(monkeypatch, tmp_path):
    launcher = ApplicationLauncher({"editor": "safe-app.exe"})
    # Discovery is deliberately stubbed: this test verifies rejection when an
    # alias is genuinely absent, regardless of the real Windows Start catalog.
    monkeypatch.setattr(ApplicationLauncher, "discover_apps", lambda self: {})
    monkeypatch.setattr(ApplicationLauncher, "discover_registered_apps", lambda self, force_refresh=False: {})
    monkeypatch.setattr(ApplicationLauncher, "discover_app_paths", lambda self: {})
    with pytest.raises(ToolError, match="not found|not allowlisted"):
        launcher.launch("app-that-is-not-installed-7f3c")

    malicious = ApplicationLauncher({"editor": "notepad.exe & whoami"})
    with pytest.raises(ToolError):
        malicious._resolve_target("editor")


def test_start_menu_shortcuts_are_discovered_and_launchable(monkeypatch, tmp_path):
    root = tmp_path / "Programs"
    root.mkdir()
    shortcut = root / "Visual Studio Code.lnk"
    shortcut.write_bytes(b"shortcut")
    monkeypatch.setattr(ApplicationLauncher, "_start_menu_roots", staticmethod(lambda: [root]))
    opened = []
    monkeypatch.setattr("chinuuu.desktop.os.name", "nt")
    monkeypatch.setattr("chinuuu.desktop.os.startfile", lambda target: opened.append(target), raising=False)

    launcher = ApplicationLauncher()
    assert "Visual Studio Code" in launcher.list_apps()
    result = launcher.launch("Visual Studio Code")
    assert "Windows accepted" in result
    assert opened == [str(shortcut.resolve())]


def test_windows_settings_page_opens_without_confirmation(monkeypatch):
    opened = []
    monkeypatch.setattr("chinuuu.desktop.os.name", "nt")
    monkeypatch.setattr("chinuuu.desktop.os.startfile", lambda target: opened.append(target), raising=False)
    launcher = ApplicationLauncher()
    result = launcher.open_setting("display")
    assert "ms-settings:display" in result
    assert opened == ["ms-settings:display"]


def test_unknown_settings_page_is_rejected():
    launcher = ApplicationLauncher()
    with pytest.raises(ToolError, match="Unknown Settings page"):
        launcher.open_setting("run arbitrary command")


def test_process_report_contains_read_only_process_metadata():
    from chinuuu.system_info import process_report
    report = process_report(3)
    assert "Top processes by resident memory" in report
    assert "Processes inspected:" in report


def test_system_report_contains_cpu_memory_and_disk():
    from chinuuu.system_info import system_report
    report = system_report()
    assert "CPU utilization:" in report
    assert "RAM:" in report
    assert "Disk (" in report


def test_extended_windows_settings_catalog_includes_common_pages():
    from chinuuu.desktop import _SETTINGS_PAGES
    for page in ("notifications", "vpn", "startup apps", "recovery", "privacy", "gaming", "accessibility"):
        assert page in _SETTINGS_PAGES


def test_windows_default_aliases_include_cmd_and_command_prompt():
    aliases = ApplicationLauncher.default_allowlist("nt")
    assert aliases["cmd"] == "cmd.exe"
    assert aliases["command prompt"] == "cmd.exe"


def test_command_prompt_launch_uses_visible_windows_shell_execute(monkeypatch, tmp_path):
    executable = tmp_path / "cmd.exe"
    executable.write_bytes(b"test")
    opened = []

    monkeypatch.setattr("chinuuu.desktop.os.name", "nt")
    monkeypatch.setattr("chinuuu.desktop.os.startfile", lambda target: opened.append(target), raising=False)
    monkeypatch.setattr(
        "chinuuu.desktop.shutil.which",
        lambda name: str(executable) if name.casefold() == "cmd.exe" else None,
    )
    monkeypatch.setattr(
        "chinuuu.desktop.subprocess.Popen",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("cmd.exe must open visibly via startfile")),
    )

    launcher = ApplicationLauncher.default_allowlist("nt")
    app = ApplicationLauncher(launcher)
    result = app.launch("cmd")

    assert "accepted the launch request" in result
    assert opened == [str(executable)]


def test_natural_command_prompt_request_launches_without_confirmation(monkeypatch, tmp_path):
    """Mock the platform's real console-launch mechanism on both Windows and POSIX."""
    from chinuuu.assistant import _handle_deterministic_workspace_request

    executable = tmp_path / "cmd.exe"
    executable.write_bytes(b"test")
    launched = {}

    class FakeProcess:
        pid = 9876

    if os.name == "nt":
        # Production uses ShellExecute via os.startfile for a visible console.
        # Mock it so pytest never attempts to execute the deliberately fake cmd.exe.
        monkeypatch.setattr(
            "chinuuu.desktop.os.startfile",
            lambda target: launched.update(target=target),
            raising=False,
        )
    else:
        # On POSIX, the Windows-specific branch is inactive and Popen is used.
        monkeypatch.setattr(
            "chinuuu.desktop.subprocess.Popen",
            lambda command, **kwargs: launched.update(command=command, kwargs=kwargs) or FakeProcess(),
        )

    registry = ToolRegistry(tmp_path / "workspace", app_allowlist={"command prompt": str(executable)})
    result = _handle_deterministic_workspace_request("open command prompt", registry, None)

    if os.name == "nt":
        assert "Windows accepted the launch request" in result
        assert launched["target"] == str(executable)
    else:
        assert "Process ID: 9876" in result
        assert launched["command"] == [str(executable)]
        assert launched["kwargs"]["shell"] is False


def test_elevated_command_prompt_request_is_rejected_without_launch(tmp_path):
    from chinuuu.assistant import _handle_deterministic_workspace_request

    registry = ToolRegistry(tmp_path / "workspace")
    result = _handle_deterministic_workspace_request(
        "open command prompt as administrator", registry, None
    )

    assert "unrestricted privileged shell execution" in result
    assert "No application was launched" in result


def test_default_cmd_alias_resolves_and_opens_visible_window_on_windows(monkeypatch, tmp_path):
    executable = tmp_path / "cmd.exe"
    executable.write_bytes(b"test")
    opened = []

    monkeypatch.setattr("chinuuu.desktop.os.name", "nt")
    monkeypatch.setattr("chinuuu.desktop.os.startfile", lambda target: opened.append(target), raising=False)
    monkeypatch.setattr(
        "chinuuu.desktop.shutil.which",
        lambda name: str(executable) if name.casefold() == "cmd.exe" else None,
    )

    result = ApplicationLauncher(platform_name="nt").launch("cmd")

    assert "accepted the launch request" in result
    assert opened == [str(executable)]


def test_microsoft_store_alias_opens_official_windows_store_uri(monkeypatch, tmp_path):
    from chinuuu.assistant import _handle_deterministic_workspace_request

    opened = []
    registry = ToolRegistry(tmp_path / "workspace")
    # Construct Path-based tools before simulating Windows; pathlib uses os.name.
    registry.app_launcher.platform_name = "nt"
    registry.app_launcher.allowlist["microsoft store"] = "uri:ms-windows-store://home"
    monkeypatch.setattr("chinuuu.desktop.os.name", "nt")
    monkeypatch.setattr(
        "chinuuu.desktop.os.startfile",
        lambda target: opened.append(target),
        raising=False,
    )
    result = _handle_deterministic_workspace_request("open microsoft store", registry, None)

    assert "ms-windows-store://home" in result
    assert opened == ["ms-windows-store://home"]


def test_store_synonyms_resolve_to_same_windows_uri(monkeypatch):
    monkeypatch.setattr("chinuuu.desktop.os.name", "nt")
    launcher = ApplicationLauncher(platform_name="nt")
    assert launcher._resolve_target("store")[0] == "uri:ms-windows-store://home"
    assert launcher._resolve_target("ms store")[0] == "uri:ms-windows-store://home"


def test_get_start_apps_discovers_packaged_apps_without_user_interpolated_powershell(monkeypatch):
    import json
    from types import SimpleNamespace

    captured = {}
    payload = [
        {"Name": "Microsoft Store", "AppID": "Microsoft.WindowsStore_8wekyb3d8bbwe!App"},
        {"Name": "Microsoft Photos", "AppID": "Microsoft.Windows.Photos_8wekyb3d8bbwe!App"},
    ]

    monkeypatch.setattr("chinuuu.desktop.shutil.which", lambda name: "powershell.exe")

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return SimpleNamespace(returncode=0, stdout=json.dumps(payload), stderr="")

    monkeypatch.setattr("chinuuu.desktop.subprocess.run", fake_run)
    launcher = ApplicationLauncher(platform_name="nt")
    apps = launcher.discover_registered_apps()

    assert apps["microsoft store"] == payload[0]["AppID"]
    assert apps["microsoft photos"] == payload[1]["AppID"]
    assert captured["kwargs"]["shell"] is False
    assert captured["kwargs"]["timeout"] == 8
    assert "Get-StartApps" in captured["command"][-1]
    assert "ConvertTo-Json" in captured["command"][-1]


def test_packaged_start_app_launches_via_explorer_appsfolder_without_shell(monkeypatch):
    import json
    from types import SimpleNamespace

    payload = [{"Name": "Microsoft Photos", "AppID": "Microsoft.Windows.Photos_8wekyb3d8bbwe!App"}]
    monkeypatch.setattr("chinuuu.desktop.shutil.which", lambda name: "powershell.exe")
    monkeypatch.setattr(
        "chinuuu.desktop.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout=json.dumps(payload), stderr=""),
    )
    launched = {}

    class FakeProcess:
        pid = 2468

    def fake_popen(command, **kwargs):
        launched["command"] = command
        launched["kwargs"] = kwargs
        return FakeProcess()

    monkeypatch.setattr("chinuuu.desktop.subprocess.Popen", fake_popen)
    launcher = ApplicationLauncher(platform_name="nt")
    result = launcher.launch("Microsoft Photos")

    assert "Process ID: 2468" in result
    assert launched["command"] == [
        "explorer.exe", "shell:AppsFolder\\Microsoft.Windows.Photos_8wekyb3d8bbwe!App"
    ]
    assert launched["kwargs"]["shell"] is False


def test_list_apps_includes_packaged_start_menu_apps(monkeypatch):
    import json
    from types import SimpleNamespace

    payload = [{"Name": "Microsoft Photos", "AppID": "Microsoft.Windows.Photos_8wekyb3d8bbwe!App"}]
    monkeypatch.setattr("chinuuu.desktop.shutil.which", lambda name: "powershell.exe")
    monkeypatch.setattr(
        "chinuuu.desktop.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout=json.dumps(payload), stderr=""),
    )
    launcher = ApplicationLauncher(platform_name="nt")
    listing = launcher.list_apps()
    assert "Microsoft Photos" in listing
    assert "Windows Start app" in listing


def test_windows_known_folder_alias_opens_with_explorer_without_shell(monkeypatch):
    launched = {}

    class FakeProcess:
        pid = 1357

    def fake_popen(command, **kwargs):
        launched["command"] = command
        launched["kwargs"] = kwargs
        return FakeProcess()

    monkeypatch.setattr("chinuuu.desktop.subprocess.Popen", fake_popen)
    launcher = ApplicationLauncher(platform_name="nt")
    result = launcher.launch("downloads folder")

    assert "downloads folder" in result.casefold()
    assert launched["command"] == ["explorer.exe", "shell:Downloads"]
    assert launched["kwargs"]["shell"] is False


def test_windows_management_console_alias_uses_file_association(monkeypatch):
    opened = []
    monkeypatch.setattr(
        "chinuuu.desktop.os.startfile",
        lambda target: opened.append(target),
        raising=False,
    )
    launcher = ApplicationLauncher(platform_name="nt")
    result = launcher.launch("device manager")
    assert "device manager" in result
    assert opened == ["devmgmt.msc"]


def test_missing_powershell_catalog_falls_back_to_shortcut_discovery(monkeypatch):
    monkeypatch.setattr("chinuuu.desktop.shutil.which", lambda name: None)
    launcher = ApplicationLauncher(platform_name="nt")
    assert launcher.discover_registered_apps() == {}


def test_win32_app_paths_registry_fallback_discovers_installed_executables(monkeypatch, tmp_path):
    import sys
    from types import SimpleNamespace

    executable = tmp_path / "registered-editor.exe"
    executable.write_bytes(b"test")
    enum_calls = {}

    class FakeKey:
        def __init__(self, kind):
            self.kind = kind
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False

    def open_key(parent, subkey):
        if parent in (1, 2) and "App Paths" in subkey:
            return FakeKey("root")
        return FakeKey("child")

    def enum_key(parent, index):
        if index == 0:
            return "registered-editor.exe"
        raise OSError("end of key")

    def query_value(key, value_name):
        assert value_name is None
        return str(executable), 1

    fake_winreg = SimpleNamespace(
        HKEY_CURRENT_USER=1,
        HKEY_LOCAL_MACHINE=2,
        OpenKey=open_key,
        EnumKey=enum_key,
        QueryValueEx=query_value,
    )
    monkeypatch.setitem(sys.modules, "winreg", fake_winreg)
    launcher = ApplicationLauncher(platform_name="nt")
    apps = launcher.discover_app_paths()

    assert apps["registered editor"] == str(executable.resolve())


def test_app_paths_registered_application_launches_without_shell(monkeypatch, tmp_path):
    import sys
    from types import SimpleNamespace

    executable = tmp_path / "registered-editor.exe"
    executable.write_bytes(b"test")

    class FakeKey:
        def __init__(self, kind):
            self.kind = kind
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False

    def open_key(parent, subkey):
        if parent in (1, 2) and "App Paths" in subkey:
            return FakeKey("root")
        return FakeKey("child")

    def enum_key(parent, index):
        if index == 0:
            return "registered-editor.exe"
        raise OSError("end of key")

    fake_winreg = SimpleNamespace(
        HKEY_CURRENT_USER=1,
        HKEY_LOCAL_MACHINE=2,
        OpenKey=open_key,
        EnumKey=enum_key,
        QueryValueEx=lambda key, value_name: (str(executable), 1),
    )
    monkeypatch.setitem(sys.modules, "winreg", fake_winreg)
    monkeypatch.setattr("chinuuu.desktop.shutil.which", lambda name: None)
    launched = {}

    class FakeProcess:
        pid = 9753

    def fake_popen(command, **kwargs):
        launched["command"] = command
        launched["kwargs"] = kwargs
        return FakeProcess()

    monkeypatch.setattr("chinuuu.desktop.subprocess.Popen", fake_popen)
    launcher = ApplicationLauncher(platform_name="nt")
    result = launcher.launch("registered editor")

    assert "Process ID: 9753" in result
    assert launched["command"] == [str(executable.resolve())]
    assert launched["kwargs"]["shell"] is False
