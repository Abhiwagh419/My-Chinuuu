from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable

from .errors import ToolError


class ComputerExplorer:
    """Read-only computer browser. Writes and deletes remain confined to workspace tools."""

    MAX_ENTRIES = 150
    MAX_READ_BYTES = 100_000
    TEXT_SUFFIXES = {
        ".txt", ".md", ".py", ".js", ".jsx", ".ts", ".tsx", ".json", ".toml",
        ".yaml", ".yml", ".ini", ".cfg", ".log", ".csv", ".html", ".css", ".sql",
        ".xml", ".sh", ".bat", ".ps1", ".java", ".c", ".h", ".cpp", ".hpp",
    }
    BLOCKED_PARTS = {
        "windows", "program files", "program files (x86)", "programdata",
        "$recycle.bin", "system volume information", "recovery",
        ".ssh", ".aws", ".azure", ".gnupg", ".config", ".kube",
        "credentials", "credential", "credential manager", "vault",
        "identitycache", "webcache", "node_modules", ".git", ".venv",
        "venv", "__pycache__",
    }
    BLOCKED_NAMES = {
        ".env", ".npmrc", "credentials", "credentials.json", "secrets.json",
        "id_rsa", "id_ed25519", "known_hosts", "login data", "cookies",
    }

    def __init__(self, workspace_root: Path) -> None:
        self.workspace_root = workspace_root.expanduser().resolve()
        self.home = Path.home().resolve()

    @staticmethod
    def _is_reparse(path: Path) -> bool:
        try:
            junction = getattr(path, "is_junction", None)
            return path.is_symlink() or (callable(junction) and junction())
        except OSError:
            return True

    def _validate(self, raw_path: str, *, must_exist: bool = True) -> Path:
        if not isinstance(raw_path, str) or not raw_path.strip():
            raise ToolError("Provide an absolute local path on this computer.")
        candidate = Path(raw_path).expanduser()
        if not candidate.is_absolute() or (os.name == "nt" and candidate.drive == ""):
            raise ToolError("Computer paths must be absolute local paths, such as C:/Users/Abhi/Documents.")
        if raw_path.startswith(("\\\\", "//")):
            raise ToolError("Network and UNC paths are not accessible through Computer Explorer.")
        folded_parts = [part.casefold() for part in candidate.parts]
        if any(part in self.BLOCKED_PARTS for part in folded_parts):
            raise ToolError("This system, application, or credential directory is excluded from Computer Explorer.")

        # Windows pytest and other benign tools commonly place temporary test files
        # under %LOCALAPPDATA%\Temp. Do not block the entire AppData tree (which
        # caused all Windows temp-path tests to fail); allow only Local\Temp and
        # keep roaming/profile application data excluded.
        if "appdata" in folded_parts:
            appdata_index = folded_parts.index("appdata")
            appdata_tail = folded_parts[appdata_index + 1:appdata_index + 3]
            if appdata_tail != ["local", "temp"]:
                raise ToolError(
                    "Application data is excluded from Computer Explorer except the Local\\Temp directory."
                )
        if candidate.name.casefold() in self.BLOCKED_NAMES or candidate.name.casefold().startswith(".env."):
            raise ToolError("This filename is excluded because it may contain credentials or secrets.")
        current = Path(candidate.anchor)
        for part in candidate.parts[1:]:
            current = current / part
            if current.exists() and self._is_reparse(current):
                raise ToolError("Symbolic links and directory junctions are excluded from Computer Explorer.")
        resolved = candidate.resolve(strict=must_exist)
        if must_exist and not resolved.exists():
            raise ToolError(f"Path does not exist: {resolved}")
        return resolved

    def locations(self) -> str:
        rows: list[str] = [f"User profile: {self.home}"]
        for name in ("Desktop", "Documents", "Downloads", "Pictures", "Music", "Videos"):
            path = self.home / name
            if path.is_dir() and not self._is_reparse(path):
                rows.append(f"{name}: {path}")
        if os.name == "nt":
            import string
            import ctypes
            drives = []
            bitmask = ctypes.windll.kernel32.GetLogicalDrives()
            for index, letter in enumerate(string.ascii_uppercase):
                if bitmask & (1 << index):
                    drives.append(f"{letter}:\\")
            rows.append("Local drives: " + (", ".join(drives) if drives else "none detected"))
        else:
            rows.append("Filesystem root: /")
        rows.append("Access mode: read-only browsing; workspace write/delete tools remain separately scoped.")
        return "Computer locations:\n" + "\n".join(f"- {row}" for row in rows)

    def list_directory(self, path: str) -> str:
        target = self._validate(path)
        if not target.is_dir():
            raise ToolError(f"Not a directory: {target}")
        rows: list[str] = []
        try:
            children = sorted(target.iterdir(), key=lambda item: (not item.is_dir(), item.name.casefold()))
            for item in children:
                if len(rows) >= self.MAX_ENTRIES:
                    rows.append("... output capped at 150 entries")
                    break
                if item.name.casefold() in self.BLOCKED_NAMES or item.name.casefold() in self.BLOCKED_PARTS:
                    continue
                if item.name.casefold().startswith(".env.") or self._is_reparse(item):
                    continue
                try:
                    if item.is_dir():
                        rows.append(f"DIR   {item.name}/")
                    elif item.is_file():
                        rows.append(f"FILE  {item.name} ({item.stat().st_size:,} bytes)")
                except OSError:
                    rows.append(f"UNAVAILABLE  {item.name}")
        except OSError as exc:
            raise ToolError(f"Could not list {target}: {exc}") from exc
        return f"Computer directory: {target}\n" + ("\n".join(rows) if rows else "Directory is empty or contains only excluded entries.")

    def read_text_file(self, path: str) -> str:
        target = self._validate(path)
        try:
            target.relative_to(self.home)
        except ValueError as exc:
            raise ToolError("Reading file contents is limited to the current user's profile. Other locations are browse-only.") from exc
        if not target.is_file() or target.suffix.casefold() not in self.TEXT_SUFFIXES:
            raise ToolError("Only regular files with supported text/code extensions can be read.")
        size = target.stat().st_size
        if size > self.MAX_READ_BYTES:
            raise ToolError(f"File is {size:,} bytes; the read limit is {self.MAX_READ_BYTES:,} bytes.")
        try:
            content = target.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise ToolError(f"Could not read UTF-8 text file: {exc}") from exc
        return f"File: {target}\n--- BEGIN FILE ---\n{content}\n--- END FILE ---"
