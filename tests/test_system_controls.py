from __future__ import annotations

import sys
import webbrowser
from dataclasses import replace
from types import ModuleType, SimpleNamespace

import pytest

from chinuuu.assistant import _handle_deterministic_workspace_request
from chinuuu.errors import ToolError
from chinuuu.system_controls import WindowsSystemControls
from chinuuu.tools import ToolRegistry


def test_brightness_command_routes_to_verified_system_tool(tmp_path, monkeypatch):
    registry = ToolRegistry(tmp_path / "workspace")
    calls = []
    spec = registry._tools["set_screen_brightness"]
    registry._tools["set_screen_brightness"] = replace(spec, handler=lambda level: calls.append(level) or f"brightness={level}")
    result = _handle_deterministic_workspace_request("change the brightness to lowest", registry, None)
    assert result == "brightness=0"
    assert calls == [0]


def test_numeric_brightness_command_routes_exact_value(tmp_path, monkeypatch):
    registry = ToolRegistry(tmp_path / "workspace")
    calls = []
    spec = registry._tools["set_screen_brightness"]
    registry._tools["set_screen_brightness"] = replace(spec, handler=lambda level: calls.append(level) or f"brightness={level}")
    result = _handle_deterministic_workspace_request("set screen brightness to 37%", registry, None)
    assert result == "brightness=37"
    assert calls == [37]


def test_brightness_rejects_out_of_range_and_boolean_values():
    controls = WindowsSystemControls(platform_name="nt")
    for value in (-1, 101, True, 10.5):
        with pytest.raises(ToolError, match="integer from 0 to 100"):
            controls.set_brightness(value)


def test_brightness_uses_fixed_powershell_and_verifies_readback(monkeypatch):
    controls = WindowsSystemControls(platform_name="nt")
    seen = {}

    def fake_run(command, **kwargs):
        seen["command"] = command
        seen["kwargs"] = kwargs
        return SimpleNamespace(returncode=0, stdout='{"statuses":["0","0"],"before":[50,60],"current":[0,0]}\n', stderr="")

    monkeypatch.setattr("chinuuu.system_controls.subprocess.run", fake_run)
    result = controls.set_brightness(0)
    assert "target 0% confirmed by WMI" in result
    assert "status 0" in result
    assert "not independently measurable" in result
    assert seen["kwargs"]["shell"] is False
    assert "WmiSetBrightness" in seen["command"][-1]
    assert "Get-WmiObject" in seen["command"][-1]
    assert "[byte]0" in seen["command"][-1]
    assert "ConvertTo-Json" in seen["command"][-1]


def test_brightness_requires_wmi_status_zero_and_matching_readback(monkeypatch):
    controls = WindowsSystemControls(platform_name="nt")
    seen = {}

    def fake_run(command, **kwargs):
        seen["script"] = command[-1]
        return SimpleNamespace(returncode=0, stdout='{"statuses":["0"],"before":[50],"current":[0]}\n', stderr="")

    monkeypatch.setattr("chinuuu.system_controls.subprocess.run", fake_run)
    assert "status 0" in controls.set_brightness(0)
    assert "WmiSetBrightness" in seen["script"]
    assert "[uint32]5" in seen["script"]
    assert "statuses=@($statuses)" in seen["script"]


def test_brightness_rejects_nonzero_wmi_status(monkeypatch):
    controls = WindowsSystemControls(platform_name="nt")
    monkeypatch.setattr(
        "chinuuu.system_controls.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(returncode=1, stdout="", stderr="WmiSetBrightness returned non-zero status: 5"),
    )
    with pytest.raises(ToolError, match="Windows could not change brightness"):
        controls.set_brightness(100)


def test_brightness_never_skips_setter_when_readback_may_already_match(monkeypatch):
    controls = WindowsSystemControls(platform_name="nt")
    seen = {}
    def fake_run(command, **kwargs):
        seen["script"] = command[-1]
        return SimpleNamespace(returncode=0, stdout='{"statuses":["0"],"before":[50],"current":[100]}\n', stderr="")
    monkeypatch.setattr("chinuuu.system_controls.subprocess.run", fake_run)
    controls.set_brightness(100)
    script = seen["script"]
    assert "WmiSetBrightness" in script
    assert "if(@($beforeLevels" not in script


def test_brightness_accepts_driver_null_status_when_readback_matches(monkeypatch):
    controls = WindowsSystemControls(platform_name="nt")
    monkeypatch.setattr(
        "chinuuu.system_controls.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0, stdout='{"statuses":["NULL"],"before":[100],"current":[50]}\n', stderr=""
        ),
    )
    result = controls.set_brightness(50)
    assert "target 50% confirmed by WMI" in result
    assert "driver omitted its method return code" in result


def test_brightness_accepts_null_status_when_already_at_requested_level(monkeypatch):
    controls = WindowsSystemControls(platform_name="nt")
    monkeypatch.setattr(
        "chinuuu.system_controls.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0, stdout='{"statuses":["NULL"],"before":[50],"current":[50]}\n', stderr=""
        ),
    )
    result = controls.set_brightness(50)
    assert "already reported the requested level" in result


def test_brightness_rejects_missing_status_field(monkeypatch):
    controls = WindowsSystemControls(platform_name="nt")
    monkeypatch.setattr(
        "chinuuu.system_controls.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout="BEFORE=100;CURRENT=50\n", stderr=""),
    )
    with pytest.raises(ToolError, match="incomplete brightness result"):
        controls.set_brightness(50)


def test_brightness_rejects_null_status_when_readback_does_not_match(monkeypatch):
    controls = WindowsSystemControls(platform_name="nt")
    monkeypatch.setattr(
        "chinuuu.system_controls.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0, stdout='{"statuses":["NULL"],"before":[100],"current":[80]}\n', stderr=""
        ),
    )
    with pytest.raises(ToolError, match="read-back mismatch"):
        controls.set_brightness(50)


def test_brightness_fails_if_readback_does_not_match(monkeypatch):
    controls = WindowsSystemControls(platform_name="nt")
    monkeypatch.setattr(
        "chinuuu.system_controls.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout='{"statuses":["0"],"before":[50],"current":[25]}\n', stderr=""),
    )
    with pytest.raises(ToolError, match="read-back mismatch"):
        controls.set_brightness(0)


def test_night_light_command_routes_to_ui_automation_tool(tmp_path, monkeypatch):
    registry = ToolRegistry(tmp_path / "workspace")
    calls = []
    spec = registry._tools["set_night_light"]
    registry._tools["set_night_light"] = replace(spec, handler=lambda enabled: calls.append(enabled) or "Night Light enabled")
    result = _handle_deterministic_workspace_request("apply night light", registry, None)
    assert result == "Night Light enabled"
    assert calls == [True]


def test_night_light_ui_automation_toggles_unique_control_and_verifies(monkeypatch):
    state = {"value": 0}

    class FakeToggle:
        def window_text(self):
            return "Night light"

        def get_toggle_state(self):
            return state["value"]

        def toggle(self):
            state["value"] = 1 - state["value"]

    class FakeWindow:
        def wait(self, *args, **kwargs):
            return None

        def descendants(self):
            return [FakeToggle()]

    class FakeDesktop:
        def __init__(self, backend):
            assert backend == "uia"

        def window(self, title_re):
            return FakeWindow()

    fake_module = ModuleType("pywinauto")
    fake_module.Desktop = FakeDesktop
    monkeypatch.setitem(sys.modules, "pywinauto", fake_module)
    monkeypatch.setattr("chinuuu.system_controls.os.startfile", lambda uri: None, raising=False)
    controls = WindowsSystemControls(platform_name="nt")
    assert "enabled and verified" in controls.set_night_light(True)
    assert state["value"] == 1


def test_night_light_refuses_ambiguous_controls(monkeypatch):
    class FakeToggle:
        def window_text(self):
            return "Night light"

        def get_toggle_state(self):
            return 0

        def toggle(self):
            raise AssertionError("Ambiguous controls must never be toggled")

    class FakeWindow:
        def wait(self, *args, **kwargs):
            return None

        def descendants(self):
            return [FakeToggle(), FakeToggle()]

    class FakeDesktop:
        def __init__(self, backend):
            pass

        def window(self, title_re):
            return FakeWindow()

    fake_module = ModuleType("pywinauto")
    fake_module.Desktop = FakeDesktop
    monkeypatch.setitem(sys.modules, "pywinauto", fake_module)
    monkeypatch.setattr("chinuuu.system_controls.os.startfile", lambda uri: None, raising=False)
    controls = WindowsSystemControls(platform_name="nt")
    with pytest.raises(ToolError, match="ambiguous"):
        controls.set_night_light(True)


def test_whatsapp_contact_name_requires_phone_number(tmp_path):
    registry = ToolRegistry(tmp_path / "workspace")
    result = _handle_deterministic_workspace_request(
        "open whatsapp and send message to Soham Gavande as hiii", registry, None
    )
    assert "phone number with country code" in result
    assert "must press Send yourself" in result


def test_whatsapp_phone_number_opens_prefilled_draft_but_does_not_send(tmp_path, monkeypatch):
    registry = ToolRegistry(tmp_path / "workspace")
    opened = []
    monkeypatch.setattr(webbrowser, "open", lambda url, **kwargs: opened.append(url) or True)
    result = _handle_deterministic_workspace_request(
        "send message to +919876543210 as hiii", registry, None
    )
    assert "NOT sent" in result
    assert opened and opened[0].startswith("https://wa.me/919876543210?")
    assert "text=hiii" in opened[0]


def test_whatsapp_message_parser_accepts_compound_open_phrase(tmp_path):
    registry = ToolRegistry(tmp_path / "workspace")
    result = _handle_deterministic_workspace_request(
        "open whatsapp and send message to Soham Gavande as hiii", registry, None
    )
    assert "Soham Gavande" in result
    assert "phone number" in result


def test_system_control_tools_are_registered(tmp_path):
    registry = ToolRegistry(tmp_path / "workspace")
    names = {item[0] for item in registry.descriptions()}
    assert {"set_screen_brightness", "set_night_light", "prepare_whatsapp_message"} <= names
