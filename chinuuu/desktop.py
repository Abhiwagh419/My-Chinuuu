from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
import webbrowser
from pathlib import Path
from typing import Mapping
from urllib.parse import quote_plus, urlparse

from .errors import ToolError

_ALIAS_PATTERN = re.compile(r"^[\w .()+-]{1,100}$", re.UNICODE)
_APP_SYNONYMS = {
    "chrome": "google chrome",
    "google chrome browser": "google chrome",
    "edge": "microsoft edge",
    "ms edge": "microsoft edge",
    "firefox": "mozilla firefox",
    "vs code": "visual studio code",
    "vscode": "visual studio code",
    "store": "microsoft store",
    "ms store": "microsoft store",
    "microsoft store app": "microsoft store",
    "taskmgr": "task manager",
    "explorer": "file explorer",
    "windows explorer": "file explorer",
    "this pc": "this pc",
    "my computer": "this pc",
    "downloads folder": "downloads",
    "documents folder": "documents",
    "pictures folder": "pictures",
    "desktop folder": "desktop",
}
_SITE_DESTINATIONS = {
    "whatsapp": "https://web.whatsapp.com/",
    "whatsapp web": "https://web.whatsapp.com/",
    "youtube": "https://www.youtube.com/",
    "gmail": "https://mail.google.com/",
    "google": "https://www.google.com/",
    "google drive": "https://drive.google.com/",
    "wikipedia": "https://www.wikipedia.org/",
    "chatgpt": "https://chatgpt.com/",
}
_SETTINGS_PAGES = {
    "home": "",
    "display": "display",
    "advanced display": "display-advanced",
    "graphics": "display-advancedgraphics",
    "night light": "nightlight",
    "sound": "sound",
    "audio": "sound",
    "notifications": "notifications",
    "focus": "quiethours",
    "do not disturb": "quiethours",
    "network": "network-status",
    "network status": "network-status",
    "wifi": "network-wifi",
    "wi-fi": "network-wifi",
    "ethernet": "network-ethernet",
    "vpn": "network-vpn",
    "proxy": "network-proxy",
    "mobile hotspot": "network-mobilehotspot",
    "airplane mode": "network-airplanemode",
    "bluetooth": "bluetooth",
    "nearby sharing": "nearbysharing",
    "apps": "appsfeatures",
    "installed apps": "appsfeatures",
    "default apps": "defaultapps",
    "startup apps": "startupapps",
    "advanced app settings": "appsfeatures-app",
    "apps for websites": "appsforwebsites",
    "optional features": "optionalfeatures",
    "offline maps": "maps",
    "windows update": "windowsupdate",
    "update": "windowsupdate",
    "update history": "windowsupdate-history",
    "advanced update options": "windowsupdate-options",
    "windows security": "windowsdefender",
    "security": "windowsdefender",
    "activation": "activation",
    "troubleshoot": "troubleshoot",
    "troubleshooting": "troubleshoot",
    "recovery": "recovery",
    "backup": "backup",
    "find my device": "findmydevice",
    "device encryption": "deviceencryption",
    "for developers": "developers",
    "developer settings": "developers",
    "personalization": "personalization",
    "background": "personalization-background",
    "colors": "personalization-colors",
    "themes": "themes",
    "lock screen": "lockscreen",
    "fonts": "fonts",
    "start": "personalization-start",
    "taskbar": "taskbar",
    "accounts": "yourinfo",
    "your info": "yourinfo",
    "email and accounts": "emailandaccounts",
    "sign-in options": "signinoptions",
    "family": "otherusers",
    "other users": "otherusers",
    "work or school": "workplace",
    "sync": "sync",
    "time": "dateandtime",
    "date and time": "dateandtime",
    "language": "language",
    "region": "regionformatting",
    "typing": "typing",
    "speech": "speech",
    "privacy": "privacy",
    "camera privacy": "privacy-webcam",
    "microphone privacy": "privacy-microphone",
    "location privacy": "privacy-location",
    "activity history": "privacy-activityhistory",
    "diagnostics": "privacy-feedback",
    "search permissions": "search-permissions",
    "storage": "storagesense",
    "power": "powersleep",
    "battery": "batterysaver",
    "multitasking": "multitasking",
    "clipboard": "clipboard",
    "remote desktop": "remotedesktop",
    "projecting to this pc": "project",
    "printers": "printers",
    "keyboard": "typing",
    "mouse": "mousetouchpad",
    "touchpad": "mousetouchpad",
    "pen": "pen",
    "usb": "usb",
    "autoplay": "autoplay",
    "camera": "camera",
    "phone": "mobile-devices",
    "accessibility": "easeofaccess",
    "narrator": "easeofaccess-narrator",
    "magnifier": "easeofaccess-magnifier",
    "captions": "easeofaccess-closedcaptioning",
    "high contrast": "easeofaccess-highcontrast",
    "gaming": "gaming-gamebar",
    "game bar": "gaming-gamebar",
    "captures": "gaming-gamedvr",
    "game mode": "gaming-gamemode",
    "about": "about",
    "system": "about",
}


def _normalise_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()


def is_administrator_launch_request(value: str) -> bool:
    """Identify a requested privilege elevation suffix without executing it."""
    return bool(re.search(r"\s+as\s+(?:an?\s+)?(?:administrator|admin)\s*[.!?]?\s*$", value, re.IGNORECASE))


def administrator_launch_rejection(alias: str) -> str:
    """Explain why unrestricted elevated interactive shells are not exposed."""
    label = alias.strip() or "application"
    return (
        f"Elevated launch of '{label}' is not available through Chinuuu. "
        "An administrator Command Prompt would expose unrestricted privileged shell execution. "
        "No application was launched. Use 'open command prompt' to open a standard, "
        "non-elevated Command Prompt. Windows UAC remains under Windows' control."
    )


class ApplicationLauncher:
    """Launch installed Start Menu shortcuts or configured executables without a shell."""

    @staticmethod
    def default_allowlist(platform_name: str | None = None) -> dict[str, str]:
        """Return safe, no-argument application aliases for the current platform."""
        platform_name = os.name if platform_name is None else platform_name
        if platform_name != "nt":
            return {}
        return {
            "notepad": "notepad.exe",
            "calculator": "calc.exe",
            "file explorer": "explorer.exe",
            "command prompt": "cmd.exe",
            "cmd": "cmd.exe",
            "microsoft store": "uri:ms-windows-store://home",
            "microsoft store downloads": "uri:ms-windows-store://downloadsandupdates",
            "microsoft store library": "uri:ms-windows-store://mylibrary",
            "task manager": "taskmgr.exe",
            "control panel": "control.exe",
            "windows terminal": "wt.exe",
            "resource monitor": "resmon.exe",
            "performance monitor": "perfmon.exe",
            "device manager": "assoc:devmgmt.msc",
            "disk management": "assoc:diskmgmt.msc",
            "services": "assoc:services.msc",
            "event viewer": "eventvwr.exe",
            "registry editor": "regedit.exe",
            "downloads": "shell:Downloads",
            "documents": "shell:Personal",
            "pictures": "shell:My Pictures",
            "music": "shell:My Music",
            "videos": "shell:My Video",
            "desktop": "shell:Desktop",
            "this pc": "shell:MyComputerFolder",
            "recycle bin": "shell:RecycleBinFolder",
            "network": "shell:NetworkPlacesFolder",
        }

    def __init__(
        self,
        allowlist: Mapping[str, str] | None = None,
        *,
        platform_name: str | None = None,
    ) -> None:
        defaults = self.default_allowlist(platform_name)
        merged = dict(defaults)
        if allowlist:
            merged.update({str(k): str(v) for k, v in allowlist.items()})
        self.allowlist = {str(key).casefold(): str(value) for key, value in merged.items()}
        self.platform_name = os.name if platform_name is None else platform_name
        self._registered_app_cache: dict[str, str] | None = None
        self._registered_app_cache_at = 0.0

    @staticmethod
    def _start_menu_roots() -> list[Path]:
        if os.name != "nt":
            return []
        roots = [
            Path(os.environ.get("APPDATA", Path.home() / "AppData/Roaming")) / "Microsoft/Windows/Start Menu/Programs",
            Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData")) / "Microsoft/Windows/Start Menu/Programs",
        ]
        return [root for root in roots if root.is_dir()]

    def discover_apps(self) -> dict[str, Path]:
        """Discover Start Menu .lnk entries; shortcut names are the public app names."""
        discovered: dict[str, Path] = {}
        for root in self._start_menu_roots():
            try:
                for shortcut in root.rglob("*.lnk"):
                    if len(discovered) >= 1000:
                        return discovered
                    if shortcut.is_file() and not shortcut.is_symlink():
                        name = shortcut.stem.strip()
                        if name and not name.startswith(("uninstall", "readme")):
                            discovered.setdefault(_normalise_name(name), shortcut)
            except OSError:
                continue
        return discovered

    def discover_registered_apps(self, *, force_refresh: bool = False) -> dict[str, str]:
        """Discover Start-menu apps, including packaged/UWP apps, using Get-StartApps.

        The PowerShell program is a fixed, read-only query. No user input is ever
        interpolated into its command string. Results are cached briefly because
        enumerating the Start catalog can take noticeable time on some PCs.
        """
        if self.platform_name != "nt":
            return {}
        now = time.monotonic()
        if (
            not force_refresh
            and self._registered_app_cache is not None
            and now - self._registered_app_cache_at < 30.0
        ):
            return dict(self._registered_app_cache)

        powershell = shutil.which("powershell.exe") or shutil.which("powershell")
        if not powershell:
            self._registered_app_cache = {}
            self._registered_app_cache_at = now
            return {}

        script = (
            "$ErrorActionPreference='Stop'; "
            "$apps = @(Get-StartApps | Where-Object { $_.Name -and $_.AppID } | "
            "ForEach-Object { [PSCustomObject]@{Name=[string]$_.Name; AppID=[string]$_.AppID} }); "
            "ConvertTo-Json -InputObject $apps -Compress -Depth 3"
        )
        discovered: dict[str, str] = {}
        try:
            completed = subprocess.run(
                [powershell, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", script],
                shell=False,
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=8,
            )
            if completed.returncode == 0 and completed.stdout.strip():
                payload = json.loads(completed.stdout.strip())
                rows = payload if isinstance(payload, list) else [payload]
                for row in rows:
                    if not isinstance(row, dict):
                        continue
                    name = str(row.get("Name", "")).strip()
                    app_id = str(row.get("AppID", "")).strip()
                    if name and app_id and len(name) <= 160 and len(app_id) <= 512:
                        discovered.setdefault(_normalise_name(name), app_id)
        except (OSError, subprocess.SubprocessError, json.JSONDecodeError, TypeError, ValueError):
            # Start Menu .lnk discovery remains available if the catalog query
            # is unavailable, times out, or returns malformed data.
            discovered = {}

        self._registered_app_cache = discovered
        self._registered_app_cache_at = now
        return dict(discovered)

    def discover_app_paths(self) -> dict[str, str]:
        """Read classic Win32 App Paths registrations (read-only registry access)."""
        if self.platform_name != "nt":
            return {}
        try:
            import winreg
        except ImportError:
            return {}

        discovered: dict[str, str] = {}
        registry_locations = (
            (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\App Paths"),
            (winreg.HKEY_LOCAL_MACHINE, r"Software\Microsoft\Windows\CurrentVersion\App Paths"),
            (winreg.HKEY_LOCAL_MACHINE, r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\App Paths"),
        )
        for hive, subkey in registry_locations:
            try:
                with winreg.OpenKey(hive, subkey) as parent:
                    index = 0
                    while index < 2000:
                        try:
                            executable_name = winreg.EnumKey(parent, index)
                        except OSError:
                            break
                        index += 1
                        if not executable_name.casefold().endswith(".exe"):
                            continue
                        try:
                            with winreg.OpenKey(parent, executable_name) as child:
                                raw_target, _ = winreg.QueryValueEx(child, None)
                        except OSError:
                            continue
                        if not isinstance(raw_target, str):
                            continue
                        candidate = Path(os.path.expandvars(raw_target.strip().strip('"')))
                        try:
                            resolved = candidate.resolve(strict=True)
                        except OSError:
                            continue
                        if resolved.is_file() and resolved.suffix.casefold() == ".exe":
                            discovered.setdefault(_normalise_name(Path(executable_name).stem), str(resolved))
            except OSError:
                continue
        return discovered

    def list_apps(self) -> str:
        rows: dict[str, str] = {}
        for alias, target in self.allowlist.items():
            rows[_normalise_name(alias)] = f"{alias}  [configured: {target}]"
        for normalised, shortcut in self.discover_apps().items():
            rows.setdefault(normalised, f"{shortcut.stem}  [Start Menu shortcut]")
        for normalised, app_id in self.discover_registered_apps(force_refresh=True).items():
            rows.setdefault(normalised, f"{normalised.title()}  [Windows Start app: {app_id}]")
        for normalised, executable in self.discover_app_paths().items():
            rows.setdefault(normalised, f"{normalised.title()}  [Windows App Paths: {executable}]")
        if not rows:
            return "No desktop applications were discovered on this platform."
        return "Applications available to launch (Start Menu discovery + configured aliases):\n" + "\n".join(
            f"- {rows[key]}" for key in sorted(rows)[:250]
        )

    def preview(self, alias: str) -> str:
        target, is_shortcut = self._resolve_target(alias)
        if target.startswith("uri:"):
            kind = "Windows application URI"
        elif target.startswith("shell:"):
            kind = "Windows shell location"
        elif target.startswith("aumid:"):
            kind = "packaged Windows app"
        elif target.startswith("assoc:"):
            kind = "Windows management console"
        else:
            kind = "Windows shortcut" if is_shortcut else "executable"
        return f"Application: {alias}\nResolved {kind}: {target}"

    def _resolve_target(self, alias: str) -> tuple[str, bool]:
        if not isinstance(alias, str) or not _ALIAS_PATTERN.fullmatch(alias.strip()):
            raise ToolError("Provide an application name, not a command or command-line arguments.")
        key = alias.strip().casefold()
        canonical_key = _APP_SYNONYMS.get(key, key)
        configured = self.allowlist.get(key) or self.allowlist.get(canonical_key)
        if configured:
            target = configured
            if target.startswith("uri:"):
                uri = target[4:].strip()
                scheme = uri.split(":", 1)[0].casefold() if ":" in uri else ""
                if scheme not in {"ms-windows-store", "ms-settings", "ms-photos", "ms-people", "msnweather"}:
                    raise ToolError("Configured application URI uses a protocol that is not approved for direct launching.")
                return target, True
            if target.startswith("shell:"):
                if self.platform_name != "nt":
                    raise ToolError("Windows shell locations are only available on Windows.")
                if any(char in target for char in ("\\", "/", "&", "|", "`", "$", "\n", "\r")):
                    raise ToolError("Configured shell location is invalid.")
                return target, True
            if target.startswith("assoc:"):
                association = target[6:].casefold()
                if self.platform_name != "nt" or association not in {"devmgmt.msc", "diskmgmt.msc", "services.msc"}:
                    raise ToolError("Only the built-in Microsoft Management Console tools are supported as document associations.")
                return target, True
            candidate = Path(target).expanduser()
            if candidate.is_absolute() or any(sep in target for sep in ("/", "\\")):
                if not candidate.is_absolute():
                    raise ToolError("Configured application paths must be absolute.")
                resolved = candidate.resolve(strict=False)
                if not resolved.exists() or not resolved.is_file():
                    raise ToolError(f"Configured application path does not exist: {resolved}")
                if resolved.suffix.casefold() not in {".exe", ".com", ".lnk"}:
                    raise ToolError("Configured application must be an executable or Windows shortcut.")
                return str(resolved), resolved.suffix.casefold() == ".lnk"
            if any(char in target for char in (";", "|", "&", "`", "$", "\n", "\r")):
                raise ToolError("Configured application contains shell metacharacters; launch blocked.")
            resolved = shutil.which(target)
            if not resolved:
                raise ToolError(f"Configured executable '{target}' was not found on PATH.")
            return resolved, False

        discovered = self.discover_apps()
        shortcut = discovered.get(_normalise_name(alias)) or discovered.get(_normalise_name(canonical_key))
        if shortcut is not None:
            return str(shortcut.resolve()), True

        registered = self.discover_registered_apps()
        app_id = registered.get(_normalise_name(alias)) or registered.get(_normalise_name(canonical_key))
        if app_id:
            # Get-StartApps returns AUMIDs for packaged apps. Some classic
            # Win32 entries expose their shortcut path as AppID instead.
            candidate = Path(app_id).expanduser()
            if candidate.suffix.casefold() == ".lnk" and candidate.exists():
                return str(candidate.resolve()), True
            if re.fullmatch(r"[A-Za-z0-9_.-]+![A-Za-z0-9_.-]+", app_id):
                return "aumid:" + app_id, True
            if candidate.is_absolute() and candidate.exists() and candidate.is_file():
                return str(candidate.resolve()), candidate.suffix.casefold() == ".lnk"

        app_paths = self.discover_app_paths()
        executable = app_paths.get(_normalise_name(alias)) or app_paths.get(_normalise_name(canonical_key))
        if executable:
            return executable, False

        available_names = sorted({
            path.stem for path in discovered.values()
        } | {
            name.title() for name in registered
        } | {
            name.title() for name in app_paths
        })
        available = ", ".join(available_names[:30]) or "none discovered"
        raise ToolError(
            f"Application '{alias}' was not found. Refresh /apps or check whether it is installed. "
            f"Examples: {available}"
        )

    @staticmethod
    def normalize_url(destination: str) -> str:
        """Resolve a common site name or validate an explicit HTTP(S) URL."""
        if not isinstance(destination, str) or not destination.strip():
            raise ToolError("Provide a website name or an HTTP(S) URL.")
        value = destination.strip().strip("\"'")
        mapped = _SITE_DESTINATIONS.get(value.casefold())
        if mapped:
            return mapped
        if len(value) > 2048:
            raise ToolError("Website URL is too long (maximum 2,048 characters).")
        # Do not reinterpret an explicit unsafe scheme as a hostname. Only
        # bare domain names are upgraded to HTTPS.
        if re.match(r"^[a-z][a-z0-9+.-]*:", value, re.IGNORECASE) and not re.match(
            r"^https?://", value, re.IGNORECASE
        ):
            raise ToolError("Only HTTP and HTTPS website URLs are supported.")
        candidate = value if re.match(r"^https?://", value, re.IGNORECASE) else f"https://{value}"
        parsed = urlparse(candidate)
        if (
            parsed.scheme.casefold() not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or any(ch.isspace() for ch in candidate)
        ):
            raise ToolError("Only valid public HTTP(S) website URLs can be opened.")
        return candidate

    @staticmethod
    def search_url(query: str) -> str:
        """Build a Google search URL from an explicit user query."""
        if not isinstance(query, str) or not query.strip() or len(query) > 300:
            raise ToolError("Search text must contain 1–300 characters.")
        return "https://www.google.com/search?q=" + quote_plus(" ".join(query.split()))

    @staticmethod
    def store_search_uri(query: str) -> str:
        """Build a Microsoft Store search URI from explicit user text."""
        if not isinstance(query, str) or not query.strip() or len(query) > 200:
            raise ToolError("Microsoft Store search text must contain 1–200 characters.")
        return "ms-windows-store://search/?query=" + quote_plus(" ".join(query.split()))

    def search_microsoft_store(self, query: str) -> str:
        """Open Microsoft Store search without running a shell command."""
        if self.platform_name != "nt":
            raise ToolError("Microsoft Store search is only available on Windows.")
        uri = self.store_search_uri(query)
        try:
            os.startfile(uri)  # type: ignore[attr-defined]
        except OSError as exc:
            raise ToolError(f"Could not open Microsoft Store search: {exc}") from exc
        return f"Windows accepted Microsoft Store search for: {query.strip()} ({uri})"

    def open_url(self, url: str, browser: str | None = None) -> str:
        """Open a validated public URL in a selected browser or the default browser.

        This navigates the browser only. It does not click page controls, submit
        forms, send messages, download files, or interact with authenticated content.
        """
        url = self.normalize_url(url)
        try:
            if browser:
                target, is_shortcut = self._resolve_target(browser)
                if is_shortcut:
                    if os.name != "nt":
                        raise ToolError("Windows Start Menu browser shortcuts can only be opened on Windows.")
                    # Windows ShellExecute passes a single URL argument to the
                    # selected shortcut; no shell command string is constructed.
                    os.startfile(target, "open", url)  # type: ignore[attr-defined]
                    return f"Windows accepted navigation in {browser}: {url}"
                process = subprocess.Popen(
                    [target, url], shell=False, stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    close_fds=(os.name != "nt"),
                )
                return f"Opened {url} in {browser}. Process ID: {process.pid}"

            opened = webbrowser.open(url, new=2, autoraise=True)
            if not opened:
                raise ToolError("Windows did not report that a default browser accepted the URL.")
            return f"Opened {url} in the default browser."
        except ToolError:
            raise
        except OSError as exc:
            raise ToolError(f"Could not open website '{url}': {exc}") from exc

    def launch(self, alias: str) -> str:
        try:
            target, is_shortcut = self._resolve_target(alias)
        except ToolError:
            # WhatsApp desktop is not installed on every Windows machine. When
            # no desktop shortcut exists, its official web client is a safe,
            # useful fallback rather than a false "app not found" dead-end.
            if _normalise_name(alias) == "whatsapp":
                return self.open_url("https://web.whatsapp.com/")
            raise
        try:
            if target.startswith("uri:"):
                if self.platform_name != "nt":
                    raise ToolError("Windows application URI handlers are only available on Windows.")
                uri = target[4:]
                os.startfile(uri)  # type: ignore[attr-defined]
                return f"Windows accepted the launch request for '{alias}'. URI: {uri}"
            if target.startswith("shell:"):
                if self.platform_name != "nt":
                    raise ToolError("Windows shell locations are only available on Windows.")
                process = subprocess.Popen(
                    ["explorer.exe", target], shell=False,
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL, close_fds=False,
                )
                return f"Opened '{alias}' in File Explorer. Process ID: {process.pid}"
            if target.startswith("assoc:"):
                if self.platform_name != "nt":
                    raise ToolError("Windows management console tools are only available on Windows.")
                association = target[6:]
                os.startfile(association)  # type: ignore[attr-defined]
                return f"Windows accepted the request to open '{alias}' ({association})."
            if target.startswith("aumid:"):
                if self.platform_name != "nt":
                    raise ToolError("Packaged Windows apps can only be opened on Windows.")
                app_id = target[6:]
                process = subprocess.Popen(
                    ["explorer.exe", f"shell:AppsFolder\\{app_id}"], shell=False,
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL, close_fds=False,
                )
                return f"Opened installed Windows app '{alias}'. Process ID: {process.pid}"
            if is_shortcut:
                if self.platform_name != "nt":
                    raise ToolError("Windows shortcuts can only be opened on Windows.")
                os.startfile(target)  # type: ignore[attr-defined]
                return f"Windows accepted the launch request for '{alias}'. Shortcut: {target}"
            # Console applications such as cmd.exe need ShellExecute/open so Windows
            # creates a visible console window instead of inheriting this CLI's
            # redirected standard handles. No command arguments are accepted.
            if os.name == "nt" and Path(target).name.casefold() == "cmd.exe":
                os.startfile(target)  # type: ignore[attr-defined]
                return f"Windows accepted the launch request for '{alias}'. Executable: {target}"
            process = subprocess.Popen(
                [target], shell=False, stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                close_fds=(os.name != "nt"),
            )
        except OSError as exc:
            raise ToolError(f"Could not launch '{alias}': {exc}") from exc
        return f"Launch request submitted for '{alias}'. Executable: {target}. Process ID: {process.pid}"

    def open_setting(self, page: str = "home") -> str:
        key = page.strip().casefold()
        if key not in _SETTINGS_PAGES:
            choices = ", ".join(sorted(_SETTINGS_PAGES))
            raise ToolError(f"Unknown Settings page '{page}'. Supported pages: {choices}")
        if os.name != "nt":
            raise ToolError("Windows Settings pages are only available on Windows.")
        uri = "ms-settings:" + _SETTINGS_PAGES[key]
        try:
            os.startfile(uri)  # type: ignore[attr-defined]
        except OSError as exc:
            raise ToolError(f"Could not open Windows Settings page '{page}': {exc}") from exc
        label = "Settings home" if not _SETTINGS_PAGES[key] else page
        return f"Windows accepted the request to open {label} ({uri}). No setting was changed."

    def open_this_pc(self) -> str:
        if os.name != "nt":
            raise ToolError("This PC is only available on Windows.")
        try:
            process = subprocess.Popen(["explorer.exe", "shell:MyComputerFolder"], shell=False)
        except OSError as exc:
            raise ToolError(f"Could not open This PC: {exc}") from exc
        return f"Opened This PC in File Explorer (process {process.pid})."
