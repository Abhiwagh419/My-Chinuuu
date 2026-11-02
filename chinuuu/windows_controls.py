from __future__ import annotations

import ctypes
import os
import re
import subprocess
import time
import webbrowser
from dataclasses import dataclass
from typing import Any

from .errors import ToolError


@dataclass(frozen=True)
class AudioState:
    volume_percent: int
    muted: bool


class WindowsControls:
    """Explicit, narrowly-scoped Windows controls with read-after-write checks."""

    VK_MEDIA_PLAY_PAUSE = 0xB3
    VK_MEDIA_NEXT_TRACK = 0xB0
    VK_MEDIA_PREV_TRACK = 0xB1

    def __init__(self, *, platform_name: str | None = None) -> None:
        self.platform_name = os.name if platform_name is None else platform_name

    def _require_windows(self) -> None:
        if self.platform_name != "nt":
            raise ToolError("This operation is supported only on Windows 10/11.")

    @staticmethod
    def _audio_endpoint() -> Any:
        try:
            from pycaw.pycaw import AudioUtilities  # type: ignore[import-not-found]
        except ImportError as exc:
            raise ToolError(
                "System volume control requires pycaw. Install Windows extras with: "
                "python -m pip install -e '.[windows]'."
            ) from exc
        try:
            device = AudioUtilities.GetSpeakers()
            endpoint = getattr(device, "EndpointVolume", None)
            if endpoint is None:
                # Newer pycaw versions expose Activate through the device.
                from pycaw.pycaw import IAudioEndpointVolume  # type: ignore[import-not-found]
                interface = device.Activate(IAudioEndpointVolume._iid_, 23, None)
                endpoint = interface.QueryInterface(IAudioEndpointVolume)
            return endpoint
        except Exception as exc:
            raise ToolError(f"Could not access the default Windows audio endpoint: {exc}") from exc

    def get_volume(self) -> str:
        self._require_windows()
        endpoint = self._audio_endpoint()
        try:
            state = AudioState(
                volume_percent=round(float(endpoint.GetMasterVolumeLevelScalar()) * 100),
                muted=bool(endpoint.GetMute()),
            )
        except Exception as exc:
            raise ToolError(f"Could not read the Windows master-volume state: {exc}") from exc
        return f"System volume: {state.volume_percent}%; muted: {'yes' if state.muted else 'no'}."

    def set_volume(self, level: int) -> str:
        self._require_windows()
        if type(level) is not int or not 0 <= level <= 100:
            raise ToolError("Volume must be an integer percentage from 0 to 100.")
        endpoint = self._audio_endpoint()
        try:
            endpoint.SetMasterVolumeLevelScalar(level / 100.0, None)
            actual = round(float(endpoint.GetMasterVolumeLevelScalar()) * 100)
        except Exception as exc:
            raise ToolError(f"Windows volume change failed: {exc}") from exc
        if abs(actual - level) > 1:
            raise ToolError(f"Volume verification failed: requested {level}%, endpoint reports {actual}%.")
        return f"System volume set to {actual}% and verified through the audio endpoint."

    def set_mute(self, muted: bool) -> str:
        self._require_windows()
        if type(muted) is not bool:
            raise ToolError("Mute state must be true or false.")
        endpoint = self._audio_endpoint()
        try:
            endpoint.SetMute(1 if muted else 0, None)
            actual = bool(endpoint.GetMute())
        except Exception as exc:
            raise ToolError(f"Windows mute operation failed: {exc}") from exc
        if actual is not muted:
            raise ToolError("Windows audio endpoint did not report the requested mute state.")
        return f"System audio {'muted' if muted else 'unmuted'} and verified."

    def media_key(self, action: str) -> str:
        self._require_windows()
        key_map = {
            "play_pause": self.VK_MEDIA_PLAY_PAUSE,
            "next": self.VK_MEDIA_NEXT_TRACK,
            "previous": self.VK_MEDIA_PREV_TRACK,
        }
        if action not in key_map:
            raise ToolError("Media action must be play_pause, next or previous.")
        try:
            user32 = ctypes.windll.user32  # type: ignore[attr-defined]
            user32.keybd_event(key_map[action], 0, 0, 0)
            user32.keybd_event(key_map[action], 0, 0x0002, 0)
        except Exception as exc:
            raise ToolError(f"Could not send the media-control key: {exc}") from exc
        return f"Sent the Windows media {action.replace('_', '/')} command. Playback state depends on the active media application."

    @staticmethod
    def _format_wifi_interface(values: dict[str, str]) -> str:
        name = values.get("name", "Unknown interface")
        state = values.get("state", "Unknown")
        ssid = values.get("ssid", "Not connected")
        return f"- {name}: {state}; network: {ssid}"

    def _radio_operation(self, radio_kind: str, enabled: bool | None = None) -> str:
        self._require_windows()
        if radio_kind not in {"wifi", "bluetooth"}:
            raise ToolError("Radio type must be wifi or bluetooth.")
        try:
            from winrt.windows.devices.radios import Radio, RadioKind, RadioState  # type: ignore[import-not-found]
        except ImportError as exc:
            raise ToolError(
                "Wi-Fi/Bluetooth radio control requires the Windows WinRT radio package. "
                "Install Windows extras with: python -m pip install -e '.[windows]'."
            ) from exc
        import asyncio

        async def operate() -> str:
            try:
                radios = list(await Radio.get_radios_async())
            except Exception as exc:
                raise ToolError(f"Windows radio enumeration failed: {exc}") from exc
            def enum_label(value: Any) -> str:
                name = getattr(value, "name", None)
                if isinstance(name, str) and name:
                    return name.casefold().replace("_", "")
                rendered = str(value).casefold().rsplit(".", 1)[-1]
                return rendered.strip(" <>_").replace("_", "")

            def kind_matches(radio: Any) -> bool:
                rendered = enum_label(radio.kind).replace("-", "").replace(" ", "")
                if radio_kind == "wifi":
                    return rendered in {"wifi", "wif"} or "wifi" in rendered
                return "bluetooth" in rendered

            candidates = [radio for radio in radios if kind_matches(radio)]
            if not candidates:
                raise ToolError(f"Windows did not expose a {radio_kind} radio through the WinRT Radio API.")
            if len(candidates) != 1:
                details = ", ".join(f"{getattr(item, 'name', 'radio')}: {enum_label(item.state)}" for item in candidates[:5])
                raise ToolError(f"Multiple {radio_kind} radios were found ({details}); no state change was attempted.")
            radio = candidates[0]
            current = enum_label(radio.state)
            label = "Wi-Fi" if radio_kind == "wifi" else "Bluetooth"
            if enabled is None:
                return f"{label} radio state: {current}."
            desired_name = "on" if enabled else "off"
            on_state = getattr(RadioState, "on", getattr(RadioState, "ON", None))
            off_state = getattr(RadioState, "off", getattr(RadioState, "OFF", None))
            desired_state = on_state if enabled else off_state
            if desired_state is None:
                raise ToolError("The installed WinRT projection does not expose the expected radio state enum.")
            if current == desired_name:
                return f"{label} is already {desired_name} according to Windows radio state."
            try:
                access = await Radio.request_access_async()
            except Exception as exc:
                raise ToolError(f"Windows radio permission request failed: {exc}") from exc
            allowed = "allowed" in str(access).casefold()
            if not allowed:
                raise ToolError(f"Windows denied permission to change {label}: {access}.")
            try:
                result = await radio.set_state_async(desired_state)
            except Exception as exc:
                raise ToolError(f"Windows could not change {label} state: {exc}") from exc
            if "allowed" not in str(result).casefold():
                raise ToolError(f"Windows did not accept the {label} state request: {result}.")
            deadline = time.monotonic() + 4.0
            while time.monotonic() < deadline:
                await asyncio.sleep(0.2)
                actual_state = radio.state
                if enum_label(actual_state) == desired_name:
                    return f"{label} turned {'on' if enabled else 'off'} and verified through the Windows radio state API."
            raise ToolError(f"Windows accepted the {label} request but the radio did not reach '{desired_name}' within four seconds. Current state: {enum_label(radio.state)}.")

        try:
            return asyncio.run(operate())
        except RuntimeError as exc:
            if "running event loop" in str(exc).casefold():
                raise ToolError("Radio control could not start its WinRT event loop in this context.") from exc
            raise

    def wifi_status(self) -> str:
        self._require_windows()
        radio_state = self._radio_operation("wifi")
        try:
            result = subprocess.run(
                ["netsh.exe", "wlan", "show", "interfaces"],
                shell=False, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=8,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return f"{radio_state} WLAN interface details unavailable: {exc}"
        if result.returncode != 0 or not result.stdout.strip():
            return radio_state
        # Keep output small and redact BSSID (hardware MAC address).
        lines = [line.strip() for line in result.stdout.splitlines() if re.match(r"\s*(Name|State|SSID|Profile)\s*:", line, re.I)]
        return radio_state + ("\n" + "\n".join(lines[:16]) if lines else "")

    def set_wifi(self, enabled: bool) -> str:
        if type(enabled) is not bool:
            raise ToolError("Wi-Fi state must be true or false.")
        return self._radio_operation("wifi", enabled)

    def bluetooth_status(self) -> str:
        return self._radio_operation("bluetooth")

    def set_bluetooth(self, enabled: bool) -> str:
        if type(enabled) is not bool:
            raise ToolError("Bluetooth state must be true or false.")
        return self._radio_operation("bluetooth", enabled)

    def open_bluetooth_settings(self) -> str:
        self._require_windows()
        try:
            os.startfile("ms-settings:bluetooth")  # type: ignore[attr-defined]
        except (AttributeError, OSError) as exc:
            raise ToolError(f"Could not open Bluetooth Settings: {exc}") from exc
        return "Opened Windows Bluetooth Settings. Bluetooth state changes remain under Windows control."

    def open_wifi_settings(self) -> str:
        self._require_windows()
        try:
            os.startfile("ms-settings:network-wifi")  # type: ignore[attr-defined]
        except (AttributeError, OSError) as exc:
            raise ToolError(f"Could not open Wi-Fi Settings: {exc}") from exc
        return "Opened Windows Wi-Fi Settings."

    def sleep(self) -> str:
        self._require_windows()
        try:
            accepted = ctypes.windll.powrprof.SetSuspendState(False, True, False)  # type: ignore[attr-defined]
            if not accepted:
                raise OSError("SetSuspendState returned false")
        except Exception as exc:
            raise ToolError(f"Windows did not accept the sleep request: {exc}") from exc
        return "Windows accepted the sleep request."

    def lock(self) -> str:
        self._require_windows()
        try:
            if not ctypes.windll.user32.LockWorkStation():  # type: ignore[attr-defined]
                raise OSError("LockWorkStation returned false")
        except Exception as exc:
            raise ToolError(f"Could not lock the Windows session: {exc}") from exc
        return "Windows lock request accepted."

    @staticmethod
    def open_email_draft(recipient: str, subject: str, body: str) -> str:
        from urllib.parse import quote
        if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", recipient.strip()):
            raise ToolError("Provide one valid email address.")
        if len(subject) > 180 or len(body) > 5000:
            raise ToolError("Email subject or body exceeds the supported length.")
        url = f"mailto:{recipient.strip()}?subject={quote(subject)}&body={quote(body)}"
        if not webbrowser.open(url, new=1, autoraise=True):
            raise ToolError("The default email application did not confirm opening the draft.")
        return "Opened an email draft in the default mail application. It has NOT been sent; review and send it yourself."
