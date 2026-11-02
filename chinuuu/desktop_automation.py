from __future__ import annotations

import os
import re
import time
from typing import Any

from .errors import ToolError


class DesktopAutomation:
    """Conservative UI Automation using semantic controls, never guessed coordinates."""

    SENSITIVE_WORDS = ("password", "passcode", "pin", "security code", "otp", "one-time code")

    def __init__(self, *, platform_name: str | None = None) -> None:
        self.platform_name = os.name if platform_name is None else platform_name

    def _desktop(self):
        if self.platform_name != "nt":
            raise ToolError("Desktop UI Automation is supported only on Windows.")
        try:
            from pywinauto import Desktop
        except ImportError as exc:
            raise ToolError("Desktop automation requires pywinauto. Install the Windows dependencies.") from exc
        try:
            return Desktop(backend="uia")
        except Exception as exc:
            raise ToolError(f"Windows UI Automation could not initialize: {exc}") from exc

    def list_windows(self) -> str:
        desktop = self._desktop()
        try:
            windows = desktop.windows(visible_only=True, enabled_only=True)
        except Exception as exc:
            raise ToolError(f"Could not enumerate visible windows: {exc}") from exc
        lines: list[str] = []
        for window in windows[:60]:
            try:
                title = " ".join(window.window_text().split())
                if title:
                    lines.append(f"- {title[:160]}")
            except Exception:
                continue
        return "Visible application windows:\n" + ("\n".join(lines) if lines else "No titled windows were found.")

    @staticmethod
    def _find_window(desktop: Any, title: str):
        if not isinstance(title, str) or len(title.strip()) < 2 or len(title) > 160:
            raise ToolError("Provide an application-window title containing 2–160 characters.")
        try:
            matches = [
                item for item in desktop.windows(visible_only=True, enabled_only=True)
                if title.casefold() in " ".join(item.window_text().split()).casefold()
            ]
        except Exception as exc:
            raise ToolError(f"Could not inspect application windows: {exc}") from exc
        if not matches:
            raise ToolError(f"No visible application window matched '{title}'.")
        if len(matches) != 1:
            names = [" ".join(item.window_text().split()) for item in matches[:5]]
            raise ToolError(f"Window title '{title}' is ambiguous: {names}. Use a more specific title.")
        return matches[0]

    def inspect_controls(self, window_title: str) -> str:
        window = self._find_window(self._desktop(), window_title)
        try:
            descendants = window.descendants()
        except Exception as exc:
            raise ToolError(f"Could not inspect UI controls: {exc}") from exc
        lines: list[str] = []
        for control in descendants[:100]:
            try:
                info = control.element_info
                name = " ".join((info.name or "").split())
                control_type = str(getattr(info, "control_type", "Unknown"))
                automation_id = str(getattr(info, "automation_id", ""))
                if not name and not automation_id:
                    continue
                sensitive = bool(getattr(info, "is_password", False)) or any(
                    word in f"{name} {automation_id}".casefold() for word in self.SENSITIVE_WORDS
                )
                if sensitive:
                    name = "[sensitive control redacted]"
                    automation_id = "[redacted]"
                lines.append(f"- type={control_type}; name={name[:100] or '(unnamed)'}; automation_id={automation_id[:80]}")
            except Exception:
                continue
        return f"Controls in '{window.window_text()}':\n" + ("\n".join(lines) if lines else "No named controls were exposed.")

    def click_control(self, window_title: str, control_name: str) -> str:
        if not isinstance(control_name, str) or not control_name.strip() or len(control_name) > 120:
            raise ToolError("Specify an exact visible control name.")
        window = self._find_window(self._desktop(), window_title)
        try:
            matches = []
            for control in window.descendants():
                try:
                    name = " ".join(control.window_text().split())
                    if name.casefold() == control_name.strip().casefold() and control.is_visible() and control.is_enabled():
                        matches.append(control)
                except Exception:
                    continue
            if len(matches) != 1:
                raise ToolError(f"Expected one visible enabled control named '{control_name}', found {len(matches)}. No click was performed.")
            control = matches[0]
            if not callable(getattr(control, "click", None)):
                raise ToolError("The identified control does not expose a semantic click operation.")
            control.click()
            time.sleep(0.2)
            return f"Clicked the uniquely identified control '{control_name}' in '{window_title}'. Verify the application result."
        except ToolError:
            raise
        except Exception as exc:
            raise ToolError(f"UI Automation click failed: {exc}") from exc

    def capture_screenshot(self, workspace_root: Path) -> str:
        """Capture the primary display into the configured workspace after approval."""
        if self.platform_name != "nt":
            raise ToolError("Desktop screenshot capture is supported only on Windows in this release.")
        try:
            import mss  # type: ignore[import-not-found]
            from PIL import Image  # type: ignore[import-not-found]
        except ImportError as exc:
            raise ToolError("Screenshot capture requires mss and Pillow. Install with: python -m pip install -e '.[windows]'.") from exc
        from datetime import datetime
        root = workspace_root.resolve()
        target_dir = root / "screenshots"
        if target_dir.is_symlink() or (callable(getattr(target_dir, "is_junction", None)) and target_dir.is_junction()):
            raise ToolError("Screenshot output cannot use a symbolic link or directory junction.")
        target_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime('%Y%m%d_%H%M%S_%f')
        target = target_dir / f"desktop_{stamp}.png"
        try:
            descriptor = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(descriptor)
        except FileExistsError as exc:
            raise ToolError("A screenshot with this timestamp already exists; retry shortly.") from exc
        try:
            with mss.mss() as screen:
                monitor = screen.monitors[1]
                shot = screen.grab(monitor)
                image = Image.frombytes("RGB", shot.size, shot.rgb)
                image.save(target, format="PNG", optimize=True)
        except Exception as exc:
            target.unlink(missing_ok=True)
            raise ToolError(f"Screenshot capture failed: {exc}") from exc
        if not target.is_file() or target.stat().st_size < 128:
            target.unlink(missing_ok=True)
            raise ToolError("Screenshot file could not be verified.")
        return f"Screenshot captured and verified: {target}. The image is stored in the configured workspace."

    def set_text(self, window_title: str, control_name: str, text: str) -> str:
        if not isinstance(control_name, str) or not control_name.strip() or len(control_name) > 120:
            raise ToolError("Specify an exact visible edit-control name.")
        if not isinstance(text, str) or not text or len(text) > 4000:
            raise ToolError("Text input must contain 1–4,000 characters.")
        if any(word in control_name.casefold() for word in self.SENSITIVE_WORDS):
            raise ToolError("Typing into password, PIN, OTP or security-code controls is blocked.")
        window = self._find_window(self._desktop(), window_title)
        try:
            matches = []
            for control in window.descendants():
                try:
                    name = " ".join(control.window_text().split())
                    info = control.element_info
                    automation_id = str(getattr(info, "automation_id", ""))
                    is_sensitive = bool(getattr(info, "is_password", False)) or any(
                        word in f"{name} {automation_id}".casefold() for word in self.SENSITIVE_WORDS
                    )
                    if (name.casefold() == control_name.strip().casefold()
                        and control.is_visible() and control.is_enabled()
                        and not is_sensitive and str(getattr(info, "control_type", "")) == "Edit"):
                        matches.append(control)
                except Exception:
                    continue
            if len(matches) != 1:
                raise ToolError(f"Expected one visible edit control named '{control_name}', found {len(matches)}. No text was entered.")
            matches[0].set_edit_text(text)
            actual = matches[0].get_value() if callable(getattr(matches[0], "get_value", None)) else None
            if actual is not None and str(actual) != text:
                raise ToolError("The application edit control did not retain the exact requested text.")
            return f"Entered text into the uniquely identified edit control '{control_name}'. No form was submitted."
        except ToolError:
            raise
        except Exception as exc:
            raise ToolError(f"Could not enter text into the UI Automation edit control: {exc}") from exc
