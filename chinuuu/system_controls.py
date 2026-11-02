from __future__ import annotations

import os
import re
import subprocess
import time
from typing import Any

from .errors import ToolError


class WindowsSystemControls:
    """Narrow, validated Windows display controls; never runs user-supplied shell text."""

    def __init__(self, *, platform_name: str | None = None) -> None:
        self.platform_name = os.name if platform_name is None else platform_name

    def set_brightness(self, level: int) -> str:
        if type(level) is not int or not 0 <= level <= 100:
            raise ToolError("Brightness must be an integer from 0 to 100.")
        if self.platform_name != "nt":
            raise ToolError("Direct screen-brightness control is available only on Windows laptops/monitors exposing the WMI brightness interface.")

        # Some laptop WMI providers successfully apply the request but return
        # $null from WmiSetBrightness. A missing method status is acceptable
        # only when every readable controller reports the requested level.
        powershell = (
            "$ErrorActionPreference='Stop'; "
            "$before=@(Get-WmiObject -Namespace root\\wmi -Class WmiMonitorBrightness -ErrorAction Stop); "
            "if($before.Count -eq 0){throw 'No readable WMI brightness controller was found.'}; "
            "$beforeLevels=@($before | ForEach-Object {[int]$_.CurrentBrightness}); "
            "$methods=@(Get-WmiObject -Namespace root\\wmi -Class WmiMonitorBrightnessMethods -ErrorAction Stop); "
            "if($methods.Count -eq 0){throw 'No compatible WMI brightness method was found.'}; "
            "$statuses=@(); "
            f"foreach($m in $methods){{ $r=$m.WmiSetBrightness([uint32]5,[byte]{level}); "
            "if($null -eq $r){$statuses += 'NULL'} else {$statuses += [string][int]$r} }; "
            "$deadline=(Get-Date).AddSeconds(4); $current=@(); "
            "do { Start-Sleep -Milliseconds 250; "
            "$current=@(Get-WmiObject -Namespace root\\wmi -Class WmiMonitorBrightness -ErrorAction Stop | ForEach-Object {[int]$_.CurrentBrightness}); "
            f"if($current.Count -eq $beforeLevels.Count -and @($current | Where-Object {{$_ -ne {level}}}).Count -eq 0){{break}} "
            "} while((Get-Date) -lt $deadline); "
            "if($current.Count -eq 0){throw 'Brightness change could not be read back.'}; "
            "if($current.Count -ne $beforeLevels.Count){throw ('Brightness controller count changed; before='+($beforeLevels -join ',')+' after='+($current -join ','))}; "
            f"if(@($current | Where-Object {{$_ -ne {level}}}).Count -gt 0){{throw ('WMI brightness read-back mismatch; requested={level}; before='+($beforeLevels -join ',')+' after='+($current -join ','))}}; "
            "if(@($statuses | Where-Object {$_ -ne 'NULL' -and $_ -ne '0'}).Count -gt 0){throw ('WmiSetBrightness returned non-zero status: '+($statuses -join ','))}; "
            "$payload=[ordered]@{statuses=@($statuses);before=@($beforeLevels);current=@($current)}; "
            "ConvertTo-Json -InputObject $payload -Compress -Depth 4"
        )
        try:
            completed = subprocess.run(
                ["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", powershell],
                shell=False, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=12,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise ToolError(f"Could not communicate with the Windows brightness controller: {exc}") from exc
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "Unknown WMI error").strip()[-700:]
            raise ToolError(f"Windows could not change brightness: {detail}")

        # Parse a structured payload instead of fragile delimiter/regex output.
        import json
        payload = None
        for line in reversed((completed.stdout or "").splitlines()):
            candidate = line.strip()
            if candidate.startswith("{") and candidate.endswith("}"):
                try:
                    payload = json.loads(candidate)
                    break
                except json.JSONDecodeError:
                    continue
        if not isinstance(payload, dict):
            raise ToolError("Windows returned an incomplete brightness result; no success is being reported.")
        statuses = payload.get("statuses")
        before_levels = payload.get("before")
        levels = payload.get("current")
        if not all(isinstance(value, list) and value for value in (statuses, before_levels, levels)):
            raise ToolError("Windows returned incomplete brightness controller data; no success is being reported.")
        try:
            before_levels = [int(value) for value in before_levels]
            levels = [int(value) for value in levels]
            statuses = [str(value).upper() for value in statuses]
        except (TypeError, ValueError) as exc:
            raise ToolError("Windows returned malformed brightness controller data.") from exc
        if len(statuses) != len(levels) or len(before_levels) != len(levels):
            raise ToolError("Brightness controller counts did not match; no success is being reported.")
        if any(value not in {"0", "NULL"} for value in statuses):
            raise ToolError(f"WmiSetBrightness returned unsuccessful status codes: {statuses}.")
        if any(value != level for value in levels):
            raise ToolError(f"Brightness read-back mismatch. Requested {level}%; before {before_levels}; WMI now reports {levels}.")
        if "NULL" in statuses:
            if all(value == level for value in before_levels):
                status_note = "The driver omitted its method return code; the controllers already reported the requested level and still report it."
            else:
                status_note = "The driver omitted its method return code; every WMI brightness controller now reports the requested level."
        else:
            status_note = "Windows returned status 0 for the brightness request."
        return (
            f"Brightness target {level}% confirmed by WMI on {len(levels)} controller(s). "
            f"{status_note} Physical panel luminance is not independently measurable by this check."
        )

    def set_night_light(self, enabled: bool) -> str:
        if type(enabled) is not bool:
            raise ToolError("Night Light state must be true or false.")
        if self.platform_name != "nt":
            raise ToolError("Windows Night Light is available only on Windows.")
        try:
            from pywinauto import Desktop
        except ImportError as exc:
            raise ToolError(
                "Night Light automation requires the optional pywinauto package. Install it with: "
                "python -m pip install pywinauto"
            ) from exc

        try:
            os.startfile("ms-settings:nightlight")  # type: ignore[attr-defined]
            settings = Desktop(backend="uia").window(title_re=".*Settings.*")
            settings.wait("visible", timeout=12)
            deadline = time.monotonic() + 8
            candidates: list[Any] = []
            while time.monotonic() < deadline:
                try:
                    for control in settings.descendants():
                        try:
                            label = " ".join(control.window_text().casefold().split())
                            if label == "night light" and callable(getattr(control, "get_toggle_state", None)):
                                candidates.append(control)
                        except Exception:
                            continue
                except Exception:
                    pass
                if candidates:
                    break
                time.sleep(0.25)
            if not candidates:
                raise ToolError(
                    "Windows opened Night Light settings, but its toggle was not exposed through UI Automation. "
                    "No setting change was made."
                )
            # Require a unique, state-readable toggle. Never click a heading or guess coordinates.
            states = []
            for control in candidates:
                try:
                    states.append((control, int(control.get_toggle_state())))
                except Exception:
                    continue
            if len(states) != 1:
                raise ToolError("Night Light toggle was ambiguous in Windows UI Automation; no change was made.")
            control, current = states[0]
            desired = 1 if enabled else 0
            if current != desired:
                toggle = getattr(control, "toggle", None)
                if not callable(toggle):
                    raise ToolError("Windows exposed the Night Light state but not a safe toggle operation.")
                toggle()
                time.sleep(0.35)
            verified = int(control.get_toggle_state())
            if verified != desired:
                raise ToolError("Night Light state could not be verified after the toggle operation.")
            return f"Night Light {'enabled' if enabled else 'disabled'} and verified in Windows Settings."
        except ToolError:
            raise
        except Exception as exc:
            raise ToolError(f"Could not safely control Night Light through Windows UI Automation: {exc}") from exc

    @staticmethod
    def prepare_whatsapp_message(phone_number: str, message: str) -> str:
        """Open a WhatsApp click-to-chat draft; the operator must review and press Send."""
        import webbrowser
        from urllib.parse import quote

        digits = re.sub(r"[\s()+.-]", "", phone_number)
        if not digits.isdigit() or not 8 <= len(digits) <= 15:
            raise ToolError("Provide the recipient's phone number with country code, using 8–15 digits.")
        if not isinstance(message, str) or not message.strip() or len(message) > 2000:
            raise ToolError("Message must contain 1–2,000 characters.")
        url = f"https://wa.me/{digits}?text={quote(message.strip(), safe='')}"
        if not webbrowser.open(url, new=2, autoraise=True):
            raise ToolError("The browser did not confirm opening the WhatsApp message draft.")
        return (
            f"Opened a WhatsApp message draft for +{digits}. Message is prefilled but NOT sent. "
            "Review the recipient and message in WhatsApp, then press Send yourself."
        )
