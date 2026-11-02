from __future__ import annotations

import ast
import json
import math
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .system_info import local_time_report, process_report, system_report
from .desktop import ApplicationLauncher
from .computer import ComputerExplorer
from .errors import ToolError
from .web_research import WebResearch
from .system_controls import WindowsSystemControls
from .windows_controls import WindowsControls
from .desktop_automation import DesktopAutomation
from .knowledge import DocumentIndexer
from .embeddings import OllamaEmbedder
from .productivity import ProductivityService
from .backups import create_database_backup
from .artifacts import ArtifactGenerator
from .privacy import redact_data, redact_text


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]
    requires_confirmation: bool
    handler: Callable[..., str]
    confirmation_condition: Callable[[dict[str, Any]], bool] | None = None

    def as_ollama_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class WorkspaceTools:
    """Tools restricted to one configured workspace directory."""

    BLOCKED_PARTS = {
        ".git", ".venv", "venv", "node_modules", "__pycache__",
        "data", ".chinuuu_backups", ".pytest_cache",
    }
    BLOCKED_NAMES = {".env", ".npmrc", "id_rsa", "id_ed25519", "credentials.json"}
    TEXT_LIMIT = 150_000
    SEARCH_FILE_LIMIT = 2_000
    DELETE_ENTRY_LIMIT = 5_000
    TREE_DEPTH_LIMIT = 6
    TREE_ENTRY_LIMIT = 300

    def __init__(self, workspace_root: Path) -> None:
        self.root = workspace_root.expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.backup_root = self.root / ".chinuuu_backups"

    @staticmethod
    def _is_reparse_point(path: Path) -> bool:
        """Detect symlinks and Windows junctions without following them."""
        try:
            if path.is_symlink():
                return True
            is_junction = getattr(path, "is_junction", None)
            return bool(is_junction()) if callable(is_junction) else False
        except OSError as exc:
            raise ToolError(f"Could not safely inspect path component {path}: {exc}") from exc

    def _resolve(self, relative_path: str, *, allow_root: bool = False) -> Path:
        if not isinstance(relative_path, str) or not relative_path.strip():
            raise ToolError("A non-empty workspace-relative path is required.")
        raw = Path(relative_path)
        if raw.is_absolute() or raw.drive:
            raise ToolError("Use a relative path inside the configured Chinuuu workspace.")
        if ".." in raw.parts:
            raise ToolError("Parent-directory traversal is blocked, even when the resolved path stays inside the workspace.")
        blocked_parts = {name.casefold() for name in self.BLOCKED_PARTS}
        blocked_names = {name.casefold() for name in self.BLOCKED_NAMES}
        if any(part.casefold() in blocked_parts for part in raw.parts):
            raise ToolError("That path is reserved or excluded from workspace access.")
        if any(part.casefold() in blocked_names or part.casefold().startswith(".env.") or Path(part).suffix.lower() in {".pem", ".key"} for part in raw.parts):
            raise ToolError("This filename is treated as sensitive and is not accessible through workspace tools.")

        # Inspect each existing component before resolving. Resolving first would
        # silently follow an in-workspace symlink or junction to another target.
        candidate = self.root
        for part in raw.parts:
            if part in {"", "."}:
                continue
            candidate = candidate / part
            if self._is_reparse_point(candidate):
                raise ToolError("Symbolic links and directory junctions are not accessible through workspace tools.")

        resolved = candidate.resolve(strict=False)
        try:
            resolved.relative_to(self.root)
        except ValueError as exc:
            raise ToolError("Path escapes the configured workspace; operation blocked.") from exc
        if resolved == self.root and not allow_root:
            raise ToolError("A specific file or subdirectory path is required.")
        return resolved

    def list_files(self, path: str = ".") -> str:
        target = self.root if path == "." else self._resolve(path, allow_root=True)
        if not target.exists():
            raise ToolError(f"Directory does not exist: {path}")
        if not target.is_dir():
            raise ToolError(f"Not a directory: {path}")
        entries = []
        for item in sorted(target.iterdir(), key=lambda p: p.name.casefold()):
            if (item.name.casefold() in {name.casefold() for name in self.BLOCKED_PARTS}
                or item.name.casefold() in {name.casefold() for name in self.BLOCKED_NAMES}
                or item.name.casefold().startswith(".env.") or item.suffix.lower() in {".pem", ".key"}
                or self._is_reparse_point(item)):
                continue
            kind = "DIR " if item.is_dir() else "FILE"
            size = "" if item.is_dir() else f" ({item.stat().st_size:,} bytes)"
            entries.append(f"{kind}  {item.name}{size}")
            if len(entries) >= 100:
                entries.append("... output capped at 100 entries")
                break
        heading = f"Workspace: {self.root}\nDirectory: {target}"
        listing = "\n".join(entries) if entries else "Directory is empty."
        return f"{heading}\n{listing}"

    def inspect_workspace(self, path: str = ".") -> str:
        """Return a bounded recursive inventory generated from the live filesystem."""
        base = self.root if path == "." else self._resolve(path, allow_root=True)
        if not base.exists() or not base.is_dir():
            raise ToolError(f"Directory does not exist: {path}")

        lines = [f"Workspace: {self.root}", f"Inventory root: {base}"]
        file_count = 0
        directory_count = 0
        total_bytes = 0
        emitted = 0

        def walk(directory: Path, prefix: str, depth: int) -> None:
            nonlocal file_count, directory_count, total_bytes, emitted
            if depth > self.TREE_DEPTH_LIMIT or emitted >= self.TREE_ENTRY_LIMIT:
                return
            try:
                children = sorted(directory.iterdir(), key=lambda item: item.name.casefold())
            except OSError as exc:
                lines.append(f"{prefix}[cannot inspect: {exc}]")
                return
            visible = []
            for item in children:
                if (item.name.casefold() in {name.casefold() for name in self.BLOCKED_PARTS}
                    or item.name.casefold() in {name.casefold() for name in self.BLOCKED_NAMES}
                    or item.name.casefold().startswith(".env.") or item.suffix.lower() in {".pem", ".key"}
                    or self._is_reparse_point(item)):
                    continue
                visible.append(item)
            for index, item in enumerate(visible):
                if emitted >= self.TREE_ENTRY_LIMIT:
                    lines.append(f"{prefix}... inventory capped at {self.TREE_ENTRY_LIMIT} entries")
                    return
                emitted += 1
                is_last = index == len(visible) - 1
                branch = "└── " if is_last else "├── "
                if item.is_dir():
                    directory_count += 1
                    lines.append(f"{prefix}{branch}{item.name}/")
                    if depth < self.TREE_DEPTH_LIMIT:
                        walk(item, prefix + ("    " if is_last else "│   "), depth + 1)
                elif item.is_file():
                    file_count += 1
                    try:
                        size = item.stat().st_size
                    except OSError:
                        size = 0
                    total_bytes += size
                    lines.append(f"{prefix}{branch}{item.name} ({size:,} bytes)")

        walk(base, "", 0)
        if emitted == 0:
            lines.append("(empty)")
        lines.extend([
            "",
            f"Summary: {directory_count} directories, {file_count} files, {total_bytes:,} bytes",
        ])
        return "\n".join(lines)

    def resolve_existing_target(self, operator_phrase: str) -> str:
        """Resolve a simple spoken target name to one existing workspace path.

        Exact paths take precedence. A phrase such as 'test folder' may map to
        the literal directory 'test_folder', but ambiguous matches are rejected.
        """
        if not isinstance(operator_phrase, str) or not operator_phrase.strip():
            raise ToolError("Specify the exact workspace file or folder to locate.")
        phrase = operator_phrase.strip().strip('"\'')
        candidates = [phrase]
        normalized = "_".join(phrase.split())
        if normalized.casefold() != phrase.casefold():
            candidates.append(normalized)
        # Also accept a hyphenated convention, but never guess between matches.
        hyphenated = "-".join(phrase.split())
        if all(hyphenated.casefold() != item.casefold() for item in candidates):
            candidates.append(hyphenated)

        # Exact spelling has priority. Only normalized variants can be ambiguous.
        try:
            exact = self._resolve(candidates[0])
        except ToolError:
            exact = None
        if exact is not None and exact.exists():
            return exact.relative_to(self.root).as_posix()

        found: list[Path] = []
        for candidate_text in candidates[1:]:
            try:
                candidate = self._resolve(candidate_text)
            except ToolError:
                continue
            if candidate.exists() and all(candidate != existing for existing in found):
                found.append(candidate)

        if not found:
            raise ToolError(f"No file or folder matching '{operator_phrase}' exists in the configured workspace.")
        if len(found) > 1:
            choices = ", ".join(item.relative_to(self.root).as_posix() for item in found)
            raise ToolError(f"'{operator_phrase}' is ambiguous. Matching workspace paths: {choices}. Specify the exact path.")
        return found[0].relative_to(self.root).as_posix()

    def preview_delete_directory(self, path: str) -> dict[str, int]:
        """Count descendants without following links; reject oversized or unsafe trees."""
        target = self._resolve(path)
        if not target.exists() or not target.is_dir():
            raise ToolError(f"Directory does not exist: {path}")
        files = directories = total_bytes = entries = 0
        def raise_walk_error(exc: OSError) -> None:
            raise ToolError(f"Could not fully inspect the directory before deletion: {exc}") from exc

        for current, dirnames, filenames in os.walk(
            target, topdown=True, followlinks=False, onerror=raise_walk_error
        ):
            current_path = Path(current)
            safe_dirs = []
            for dirname in list(dirnames):
                child = current_path / dirname
                if dirname.casefold() in {name.casefold() for name in self.BLOCKED_PARTS | self.BLOCKED_NAMES}:
                    raise ToolError(f"Deletion blocked: protected directory '{dirname}' is present inside the target.")
                if self._is_reparse_point(child):
                    raise ToolError("Deletion blocked: the directory contains a symbolic link or junction.")
                safe_dirs.append(dirname)
            dirnames[:] = safe_dirs
            for filename in filenames:
                child = current_path / filename
                if (filename.casefold() in {name.casefold() for name in self.BLOCKED_NAMES}
                    or filename.casefold().startswith(".env.")
                    or child.suffix.lower() in {".pem", ".key"}):
                    raise ToolError(f"Deletion blocked: protected file '{filename}' is present inside the target.")
                if self._is_reparse_point(child):
                    raise ToolError("Deletion blocked: the directory contains a symbolic link or junction.")
                if not child.is_file():
                    continue
                try:
                    total_bytes += child.stat().st_size
                except OSError as exc:
                    raise ToolError(f"Cannot inspect '{child}': {exc}") from exc
                files += 1
                entries += 1
            directories += len(dirnames)
            entries += len(dirnames)
            if entries > self.DELETE_ENTRY_LIMIT:
                raise ToolError(
                    f"Deletion blocked: the target contains more than {self.DELETE_ENTRY_LIMIT:,} entries. "
                    "Use File Explorer for this unusually large directory."
                )
        return {"files": files, "directories": directories, "bytes": total_bytes}

    def read_file(self, path: str) -> str:
        target = self._resolve(path)
        if not target.exists() or not target.is_file():
            raise ToolError(f"File not found: {path}")
        if target.is_symlink():
            raise ToolError("Symbolic links are not read by this tool.")
        size = target.stat().st_size
        if size > self.TEXT_LIMIT:
            raise ToolError(f"File is {size:,} bytes; maximum permitted read is {self.TEXT_LIMIT:,} bytes.")
        try:
            return target.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise ToolError("File is not valid UTF-8 text; binary files are not supported.") from exc

    def search_files(self, query: str, path: str = ".") -> str:
        if not isinstance(query, str) or not query.strip():
            raise ToolError("Search query cannot be empty.")
        base = self.root if path == "." else self._resolve(path, allow_root=True)
        if not base.exists() or not base.is_dir():
            raise ToolError(f"Directory not found: {path}")
        matches: list[str] = []
        inspected = 0
        for item in base.rglob("*"):
            rel_parts = item.relative_to(self.root).parts
            blocked_parts = {name.casefold() for name in self.BLOCKED_PARTS}
            if self._is_reparse_point(item) or any(part.casefold() in blocked_parts for part in rel_parts):
                continue
            if item.name.casefold() in {name.casefold() for name in self.BLOCKED_NAMES} or item.name.casefold().startswith(".env.") or item.suffix.lower() in {".pem", ".key"}:
                continue
            if not item.is_file():
                continue
            inspected += 1
            if inspected > self.SEARCH_FILE_LIMIT:
                matches.append("Search capped after 2,000 files.")
                break
            rel = item.relative_to(self.root).as_posix()
            if query.casefold() in item.name.casefold():
                matches.append(f"Filename match: {rel}")
            if item.stat().st_size <= self.TEXT_LIMIT:
                try:
                    content = item.read_text(encoding="utf-8", errors="strict")
                except (UnicodeDecodeError, OSError):
                    continue
                for line_no, line in enumerate(content.splitlines(), start=1):
                    if query.casefold() in line.casefold():
                        matches.append(f"{rel}:{line_no}: {line.strip()[:240]}")
                        if len(matches) >= 40:
                            matches.append("Results capped at 40 matches.")
                            return "\n".join(matches)
        return "\n".join(matches) if matches else "No matching filenames or text found in the workspace."

    def write_file(self, path: str, content: str, overwrite: bool = False) -> str:
        target = self._resolve(path)
        if not isinstance(content, str):
            raise ToolError("File content must be text.")
        expected_bytes = content.encode("utf-8")
        if len(expected_bytes) > self.TEXT_LIMIT:
            raise ToolError(f"Content exceeds the {self.TEXT_LIMIT:,}-byte write limit.")
        existed_before = target.exists()
        if existed_before and target.is_dir():
            raise ToolError("The target path is a directory.")
        if existed_before and not overwrite:
            raise ToolError("The file already exists. Replacement requires explicit approval.")

        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.parent.is_dir():
            raise ToolError(f"Parent directory could not be verified: {target.parent}")

        backup = None
        if existed_before:
            backup_dir = self.backup_root / target.parent.relative_to(self.root)
            backup_dir.mkdir(parents=True, exist_ok=True)
            backup = backup_dir / f"{target.name}.backup"
            shutil.copy2(target, backup)

        temp = target.with_name(f".{target.name}.{os.getpid()}.chinuuu-tmp")
        try:
            temp.write_bytes(expected_bytes)
            os.replace(temp, target)
        except OSError as exc:
            raise ToolError(f"Could not write '{target}': {exc}") from exc
        finally:
            temp.unlink(missing_ok=True)

        try:
            if not target.is_file() or target.read_bytes() != expected_bytes:
                raise ToolError(f"File write could not be verified at {target}; on-disk content differs.")
        except OSError as exc:
            raise ToolError(f"File was written but read-back verification failed: {exc}") from exc

        size = len(expected_bytes)
        if existed_before:
            return f"Replaced file: {target} ({size:,} bytes). Backup: {backup}"
        return f"Created file: {target} ({size:,} bytes)."

    def create_directory(self, path: str) -> str:
        target = self._resolve(path)
        if target.exists() and not target.is_dir():
            raise ToolError(f"A file already exists at this path: {path}")
        existed_before = target.is_dir()
        target.mkdir(parents=True, exist_ok=True)
        if not target.is_dir():
            raise ToolError(f"Directory creation could not be verified at {target}.")
        if existed_before:
            return f"Directory already exists: {target}"
        return f"Created directory: {target}"

    def delete_file(self, path: str) -> str:
        target = self._resolve(path)
        if not target.exists():
            raise ToolError(f"File does not exist: {path}")
        if not target.is_file():
            raise ToolError("This operation deletes files only; use the directory-deletion operation for folders.")
        target.unlink()
        if target.exists():
            raise ToolError(f"File deletion could not be verified: {target} still exists.")
        return f"Deleted file: {target.relative_to(self.root).as_posix()}\nVerification: target no longer exists."

    def delete_directory(self, path: str) -> str:
        target = self._resolve(path)
        if not target.exists() or not target.is_dir():
            raise ToolError(f"Directory does not exist: {path}")
        summary = self.preview_delete_directory(path)
        shutil.rmtree(target)
        if target.exists():
            raise ToolError(f"Directory deletion could not be verified: {target} still exists.")
        return (
            f"Deleted directory: {target.relative_to(self.root).as_posix()}\n"
            f"Removed: {summary['directories']} subdirectories, {summary['files']} files, "
            f"{summary['bytes']:,} bytes.\nVerification: target no longer exists."
        )

    def move_path(self, source: str, destination: str) -> str:
        source_path = self._resolve(source)
        destination_path = self._resolve(destination)
        if not source_path.exists():
            raise ToolError(f"Source does not exist: {source}")
        if source_path.is_dir():
            # A directory may not be moved into itself or any descendant.
            try:
                destination_path.relative_to(source_path)
            except ValueError:
                pass
            else:
                raise ToolError("A directory cannot be moved into itself or one of its descendants.")
            self.preview_delete_directory(source)
        if destination_path.exists():
            raise ToolError("Destination already exists. Move operations never overwrite existing content.")
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source_path), str(destination_path))
        if source_path.exists() or not destination_path.exists():
            raise ToolError("Move operation could not be verified; source/destination state is unexpected.")
        return (
            f"Moved: {source_path.relative_to(self.root).as_posix()} -> "
            f"{destination_path.relative_to(self.root).as_posix()}\n"
            "Verification: source is absent and destination exists."
        )

    def file_exists(self, path: str) -> bool:
        # Only replacing an existing regular file is a confirmation-gated action.
        target = self._resolve(path)
        return target.exists() and target.is_file()

    def python_source(self, path: str) -> tuple[Path, str]:
        """Validate and read an existing UTF-8 Python source file for preview/execution."""
        target = self._resolve(path)
        if not target.exists() or not target.is_file():
            raise ToolError(f"Python script does not exist: {path}")
        if target.suffix.casefold() != ".py":
            raise ToolError("Only files with a .py extension can be executed by this tool.")
        try:
            size = target.stat().st_size
        except OSError as exc:
            raise ToolError(f"Could not inspect Python script: {exc}") from exc
        if size > 100_000:
            raise ToolError("Python script exceeds the 100 KB execution limit.")
        try:
            source = target.read_text(encoding="utf-8", errors="strict")
        except (UnicodeDecodeError, OSError) as exc:
            raise ToolError(f"Python script must be a readable UTF-8 text file: {exc}") from exc
        return target, source

    def validate_python_file(self, path: str) -> bool:
        self.python_source(path)
        return True

    def check_python_syntax(self, path: str) -> str:
        """Compile source to bytecode in memory without executing it or writing pyc files."""
        target, source = self.python_source(path)
        try:
            compile(source, str(target), "exec", dont_inherit=True)
        except SyntaxError as exc:
            location = f"line {exc.lineno or '?'}"
            if exc.offset:
                location += f", column {exc.offset}"
            return f"Python syntax check failed for {target.relative_to(self.root).as_posix()}: {location}: {exc.msg}"
        return f"Python syntax check passed: {target.relative_to(self.root).as_posix()} (source compiled in memory; not executed)."

    def run_python_file(self, path: str, timeout_seconds: int = 15) -> str:
        """Run one existing workspace Python script after an application approval.

        This is a trusted-script runner, not an OS sandbox: the child process runs
        with the current user's Windows permissions. The CLI therefore shows the
        source preview and requires explicit approval for every invocation.
        """
        target, _source = self.python_source(path)
        if type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 30:
            raise ToolError("Execution timeout must be an integer from 1 to 30 seconds.")

        command = [sys.executable, "-I", str(target)]
        started = time.monotonic()
        process: subprocess.Popen[bytes] | None = None
        creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        try:
            process = subprocess.Popen(
                command,
                cwd=str(self.root),
                shell=False,
                stdin=None,   # Interactive input() remains available in the terminal.
                stdout=None,  # Program output is displayed directly in the terminal.
                stderr=None,
                creationflags=creationflags,
            )
            return_code = process.wait(timeout=timeout_seconds)
        except subprocess.TimeoutExpired as exc:
            if process is not None:
                process.kill()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    pass
            raise ToolError(
                f"Python script exceeded the {timeout_seconds}-second time limit and was terminated."
            ) from exc
        except KeyboardInterrupt:
            if process is not None:
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=2)
            raise
        except OSError as exc:
            raise ToolError(f"Python execution could not start: {exc}") from exc

        elapsed = time.monotonic() - started
        relative = target.relative_to(self.root).as_posix()
        status = "completed successfully" if return_code == 0 else f"exited with code {return_code}"
        return (
            f"Python script {status}: {relative}\n"
            f"Exit code: {return_code}\n"
            f"Elapsed time: {elapsed:.2f} seconds\n"
            "Program stdout/stderr was displayed directly in the terminal."
        )

    def git_status(self) -> str:
        if not (self.root / ".git").exists():
            return "The configured workspace is not a Git repository."
        try:
            result = subprocess.run(
                ["git", "-C", str(self.root), "status", "--short", "--branch"],
                capture_output=True, text=True, timeout=10, check=False,
                shell=False, cwd=str(self.root),
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ToolError(f"Git status could not be completed: {exc}") from exc
        if result.returncode != 0:
            raise ToolError(result.stderr.strip() or "Git status failed.")
        return result.stdout.strip() or "Git working tree is clean."


class ToolRegistry:
    """Allowlisted local tools; Python scripts require explicit approval and are not sandboxed."""

    def __init__(
        self,
        workspace_root: Path,
        app_allowlist: dict[str, str] | None = None,
        allow_external_network: bool = False,
        task_store: Any | None = None,
        embedding_model: str = "",
        ollama_url: str = "http://127.0.0.1:11434",
    ) -> None:
        self.workspace = WorkspaceTools(workspace_root)
        self.app_launcher = ApplicationLauncher(app_allowlist)
        self.system_controls = WindowsSystemControls()
        self.windows_controls = WindowsControls()
        self.desktop_automation = DesktopAutomation()
        self.computer = ComputerExplorer(workspace_root)
        self.web_research = WebResearch(enabled=allow_external_network)
        self.allow_external_network = allow_external_network
        self.task_store = task_store
        self.active_run_id: str | None = None
        self.productivity = ProductivityService(task_store, workspace_root) if task_store is not None else None
        self.embedder = OllamaEmbedder(ollama_url, embedding_model) if embedding_model else None
        self.documents = DocumentIndexer(task_store, workspace_root, self.embedder) if task_store is not None else None
        self.artifacts = ArtifactGenerator(workspace_root)
        self._tools: dict[str, ToolSpec] = {}
        self._register_defaults()

    @property
    def workspace_root(self) -> Path:
        return self.workspace.root

    def _register(self, spec: ToolSpec) -> None:
        self._tools[spec.name] = spec

    def _register_defaults(self) -> None:
        object_schema = {"type": "object", "properties": {}, "required": []}
        self._register(ToolSpec(
            "get_local_time", "Get the current Windows local date, time and UTC offset.",
            object_schema, False, lambda: local_time_report(),
        ))
        self._register(ToolSpec(
            "get_system_report", "Get read-only operating-system, CPU, memory, disk, battery and uptime telemetry.",
            object_schema, False, lambda: system_report(),
        ))
        self._register(ToolSpec(
            "get_process_report", "List process names, PIDs and memory consumption only. Read-only; no command lines or process control.",
            object_schema, False, lambda: process_report(),
        ))
        self._register(ToolSpec(
            "list_allowed_applications", "List configured aliases, Start Menu shortcuts, packaged Windows Start apps, and classic applications registered under App Paths.",
            object_schema, False, self.app_launcher.list_apps,
        ))
        self._register(ToolSpec(
            "set_screen_brightness", "Set Windows laptop/display brightness to an explicit integer percentage from 0 to 100 and verify the WMI read-back. Does not run arbitrary commands.",
            {"type": "object", "properties": {"level": {"type": "integer", "description": "Brightness percentage from 0 (lowest) to 100 (highest)"}}, "required": ["level"]},
            False, self.system_controls.set_brightness,
        ))
        self._register(ToolSpec(
            "set_night_light", "Enable or disable Windows Night Light using the uniquely identified UI Automation toggle and verify its resulting state. Never clicks by screen coordinates.",
            {"type": "object", "properties": {"enabled": {"type": "boolean", "description": "True to enable Night Light; false to disable"}}, "required": ["enabled"]},
            False, self.system_controls.set_night_light,
        ))
        self._register(ToolSpec(
            "prepare_whatsapp_message", "Open a prefilled WhatsApp click-to-chat draft for an explicit phone number and message. Does NOT send; the operator reviews and presses Send manually.",
            {"type": "object", "properties": {"phone_number": {"type": "string", "description": "Recipient phone number including country code"}, "message": {"type": "string", "description": "Message text"}}, "required": ["phone_number", "message"]},
            False, self.system_controls.prepare_whatsapp_message,
        ))
        self._register(ToolSpec(
            "open_windows_setting", "Open a supported Windows Settings page. This only navigates to the page; it does not change settings and does not require confirmation.",
            {"type": "object", "properties": {"page": {"type": "string", "description": "Known page such as display, sound, wifi, bluetooth, apps, update, privacy, power, or about"}}, "required": []},
            False, self.app_launcher.open_setting,
        ))
        self._register(ToolSpec(
            "open_this_pc", "Open This PC in Windows File Explorer. Navigation only; no files are changed.",
            object_schema, False, self.app_launcher.open_this_pc,
        ))
        self._register(ToolSpec(
            "list_computer_locations", "List the current user's common folders and local drive roots. Read-only; does not expose file contents.",
            object_schema, False, self.computer.locations,
        ))
        self._register(ToolSpec(
            "list_computer_directory", "Browse a real absolute local directory anywhere on a local drive, excluding system, credential and reparse-point locations. Read-only.",
            {"type": "object", "properties": {"path": {"type": "string", "description": "Absolute local directory path"}}, "required": ["path"]},
            False, self.computer.list_directory,
        ))
        self._register(ToolSpec(
            "read_computer_text_file", "Read a small UTF-8 text/code file under the current user's profile. Credential-like paths and files are excluded. Read-only.",
            {"type": "object", "properties": {"path": {"type": "string", "description": "Absolute path to a supported text file under the user profile"}}, "required": ["path"]},
            False, self.computer.read_text_file,
        ))
        self._register(ToolSpec(
            "check_workspace_python", "Check syntax of a workspace .py file by compiling source in memory. Does not execute it.",
            {"type": "object", "properties": {"path": {"type": "string", "description": "Workspace-relative Python file"}}, "required": ["path"]},
            False, self.workspace.check_python_syntax,
        ))
        self._register(ToolSpec(
            "list_workspace_files", "List files and directories inside the configured workspace. Use '.' for its root.",
            {"type": "object", "properties": {"path": {"type": "string", "description": "Relative directory path, or '.'"}}, "required": []},
            False, self.workspace.list_files,
        ))
        self._register(ToolSpec(
            "inspect_workspace", "Recursively inspect the actual configured workspace contents with bounded depth and output. Use this for questions about everything in the workspace.",
            {"type": "object", "properties": {"path": {"type": "string", "description": "Relative directory path, or '.'"}}, "required": []},
            False, self.workspace.inspect_workspace,
        ))
        self._register(ToolSpec(
            "read_workspace_file", "Read a UTF-8 text file inside the configured workspace (max 150 KB).",
            {"type": "object", "properties": {"path": {"type": "string", "description": "Workspace-relative file path"}}, "required": ["path"]},
            False, self.workspace.read_file,
        ))
        self._register(ToolSpec(
            "search_workspace", "Search filenames and UTF-8 text contents inside the configured workspace.",
            {"type": "object", "properties": {
                "query": {"type": "string", "description": "Text to search for"},
                "path": {"type": "string", "description": "Relative directory path, or '.'", "default": "."},
            }, "required": ["query"]},
            False, self.workspace.search_files,
        ))
        self._register(ToolSpec(
            "write_workspace_file", "Create a NEW UTF-8 text file inside the configured workspace without an approval prompt. If the target already exists, do not replace it silently: set overwrite=true only when Abhi explicitly requested replacement; replacement requires operator confirmation and creates a backup.",
            {"type": "object", "properties": {
                "path": {"type": "string", "description": "Workspace-relative target path"},
                "content": {"type": "string", "description": "Complete file content"},
                "overwrite": {"type": "boolean", "description": "Set true only when Abhi explicitly requested replacing an existing file", "default": False},
            }, "required": ["path", "content"]},
            False, self.workspace.write_file,
            confirmation_condition=lambda args: self.workspace.file_exists(str(args.get("path", ""))) and args.get("overwrite") is True,
        ))
        self._register(ToolSpec(
            "create_workspace_directory", "Create a new directory inside the configured workspace without an approval prompt. This operation does not delete or replace existing content.",
            {"type": "object", "properties": {"path": {"type": "string", "description": "Workspace-relative directory path"}}, "required": ["path"]},
            False, self.workspace.create_directory,
        ))
        self._register(ToolSpec(
            "delete_workspace_file", "Permanently delete one regular file inside the configured workspace. ALWAYS requires application-level operator confirmation; verify the target is absent afterward.",
            {"type": "object", "properties": {"path": {"type": "string", "description": "Workspace-relative file path to delete"}}, "required": ["path"]},
            True, self.workspace.delete_file,
        ))
        self._register(ToolSpec(
            "delete_workspace_directory", "Permanently delete one non-root directory and its contents inside the configured workspace. ALWAYS requires application-level operator confirmation. Rejects symlinks/junctions and directories with more than 5,000 entries.",
            {"type": "object", "properties": {"path": {"type": "string", "description": "Workspace-relative directory path to delete"}}, "required": ["path"]},
            True, self.workspace.delete_directory,
        ))
        self._register(ToolSpec(
            "move_workspace_path", "Move or rename a file or directory within the configured workspace. Never overwrites an existing destination. ALWAYS requires operator confirmation.",
            {"type": "object", "properties": {
                "source": {"type": "string", "description": "Existing workspace-relative source path"},
                "destination": {"type": "string", "description": "New workspace-relative destination path"},
            }, "required": ["source", "destination"]},
            True, self.workspace.move_path,
        ))
        self._register(ToolSpec(
            "run_workspace_python",
            "Run one existing UTF-8 .py script inside the configured workspace. ALWAYS requires operator approval. The script runs with the current Windows user's permissions (not an OS sandbox), receives interactive terminal stdin, displays stdout/stderr in the terminal, and is limited to 1-30 seconds. Only run code the operator trusts.",
            {"type": "object", "properties": {
                "path": {"type": "string", "description": "Existing workspace-relative .py file"},
                "timeout_seconds": {"type": "integer", "description": "Runtime limit from 1 to 30 seconds", "default": 15},
            }, "required": ["path"]},
            True, self.workspace.run_python_file,
            confirmation_condition=lambda args: self.workspace.validate_python_file(str(args.get("path", ""))),
        ))
        self._register(ToolSpec(
            "open_application",
            "Launch an installed Windows Start Menu application by its exact display name, or a configured application alias. No confirmation is required for launching an app. Never accepts command-line arguments or shell commands.",
            {"type": "object", "properties": {"alias": {"type": "string", "description": "Installed application display name or configured alias"}}, "required": ["alias"]},
            False, self.app_launcher.launch,
        ))
        self._register(ToolSpec(
            "search_microsoft_store",
            "Open the Microsoft Store app to search for a product or application. Uses the documented ms-windows-store URI scheme; no shell command is executed and no confirmation is required for this navigation.",
            {"type": "object", "properties": {"query": {"type": "string", "description": "Store search query, 1-200 characters"}}, "required": ["query"]},
            False, self.app_launcher.search_microsoft_store,
        ))
        self._register(ToolSpec(
            "open_url",
            "Open an explicit public HTTP(S) URL in the selected installed browser or the Windows default browser. Navigation only: does not click, submit forms, send messages, download files, or interact with authenticated content. No confirmation is required for an operator-requested URL.",
            {"type": "object", "properties": {
                "url": {"type": "string", "description": "Public HTTP(S) URL or supported site name"},
                "browser": {"type": "string", "description": "Optional installed browser name or alias, e.g. Chrome or Edge"},
            }, "required": ["url"]},
            False, self.app_launcher.open_url,
        ))
        if self.allow_external_network:
            self._register(ToolSpec(
                "search_public_web",
                "Search public web pages using DuckDuckGo. Sends only the explicit search query; do not include private data. ALWAYS requires operator confirmation.",
                {"type": "object", "properties": {
                    "query": {"type": "string", "description": "Public search query, 1-300 characters"},
                    "limit": {"type": "integer", "description": "Maximum results, 1-8", "default": 5},
                }, "required": ["query"]},
                True, self.web_research.search,
            ))
        if self.task_store is not None:
            self._register(ToolSpec(
                "add_local_task", "Save a task in Chinuuu's local SQLite task list and verify the stored record.",
                {"type": "object", "properties": {"title": {"type": "string", "description": "Task description, 1-300 characters"}}, "required": ["title"]},
                False, self._add_local_task,
            ))
            self._register(ToolSpec(
                "list_local_tasks", "List tasks stored in the local SQLite task list.",
                object_schema, False, self._list_local_tasks,
            ))
            self._register(ToolSpec(
                "complete_local_task", "Mark one existing local task as complete and verify the status change.",
                {"type": "object", "properties": {"task_id": {"type": "integer", "description": "Existing task ID"}}, "required": ["task_id"]},
                False, self._complete_local_task,
            ))
            self._register(ToolSpec(
                "delete_local_task", "Permanently delete one local task. ALWAYS requires application-level operator confirmation.",
                {"type": "object", "properties": {"task_id": {"type": "integer", "description": "Existing task ID"}}, "required": ["task_id"]},
                True, self._delete_local_task,
            ))
        # System audio and media controls use Windows Core Audio and documented media keys.
        self._register(ToolSpec(
            "get_system_volume", "Read the Windows default audio endpoint volume and mute state.",
            object_schema, False, self.windows_controls.get_volume,
        ))
        self._register(ToolSpec(
            "set_system_volume", "Set the Windows default audio endpoint volume to an integer percentage and verify the read-back.",
            {"type": "object", "properties": {"level": {"type": "integer", "description": "Volume from 0 to 100"}}, "required": ["level"]},
            False, self.windows_controls.set_volume,
        ))
        self._register(ToolSpec(
            "set_audio_mute", "Mute or unmute the Windows default audio endpoint and verify the resulting state.",
            {"type": "object", "properties": {"muted": {"type": "boolean"}}, "required": ["muted"]},
            False, self.windows_controls.set_mute,
        ))
        self._register(ToolSpec(
            "media_control", "Send one standard Windows media key: play_pause, next or previous. Does not inspect or control a particular app session.",
            {"type": "object", "properties": {"action": {"type": "string", "enum": ["play_pause", "next", "previous"]}}, "required": ["action"]},
            False, self.windows_controls.media_key,
        ))
        self._register(ToolSpec(
            "get_wifi_status", "Read Windows Wi-Fi radio state and WLAN interface details; does not change connectivity.",
            object_schema, False, self.windows_controls.wifi_status,
        ))
        self._register(ToolSpec(
            "set_wifi_radio", "Enable or disable the Windows Wi-Fi radio through the WinRT Radio API; verifies the resulting state. Always requires operator confirmation.",
            {"type": "object", "properties": {"enabled": {"type": "boolean"}}, "required": ["enabled"]},
            True, self.windows_controls.set_wifi,
        ))
        self._register(ToolSpec(
            "get_bluetooth_status", "Read the Windows Bluetooth radio state without changing it.",
            object_schema, False, self.windows_controls.bluetooth_status,
        ))
        self._register(ToolSpec(
            "set_bluetooth_radio", "Enable or disable the Windows Bluetooth radio through the WinRT Radio API; verifies the resulting state. Always requires operator confirmation.",
            {"type": "object", "properties": {"enabled": {"type": "boolean"}}, "required": ["enabled"]},
            True, self.windows_controls.set_bluetooth,
        ))
        self._register(ToolSpec(
            "open_wifi_settings", "Open Windows Wi-Fi Settings without changing adapter state.",
            object_schema, False, self.windows_controls.open_wifi_settings,
        ))
        self._register(ToolSpec(
            "open_bluetooth_settings", "Open Windows Bluetooth Settings. Actual adapter pairing and state remain under Windows control.",
            object_schema, False, self.windows_controls.open_bluetooth_settings,
        ))
        self._register(ToolSpec(
            "capture_desktop_screenshot", "Capture the primary display into the configured workspace. Privacy-sensitive; always requires operator confirmation.",
            object_schema, True, lambda: self.desktop_automation.capture_screenshot(self.workspace_root),
        ))
        self._register(ToolSpec(
            "list_visible_windows", "List visible, titled desktop application windows using Windows UI Automation.",
            object_schema, False, self.desktop_automation.list_windows,
        ))
        self._register(ToolSpec(
            "inspect_window_controls", "Inspect semantic UI controls in exactly one visible application window; sensitive control labels are redacted.",
            {"type": "object", "properties": {"window_title": {"type": "string"}}, "required": ["window_title"]},
            False, self.desktop_automation.inspect_controls,
        ))
        self._register(ToolSpec(
            "click_desktop_control", "Click one uniquely identified visible, enabled control in one unambiguous application window. Always requires operator approval.",
            {"type": "object", "properties": {"window_title": {"type": "string"}, "control_name": {"type": "string"}}, "required": ["window_title", "control_name"]},
            True, self.desktop_automation.click_control,
        ))
        self._register(ToolSpec(
            "set_desktop_text", "Enter text into one uniquely identified visible edit control. Does not submit forms. Always requires operator approval; sensitive auth fields are blocked.",
            {"type": "object", "properties": {"window_title": {"type": "string"}, "control_name": {"type": "string"}, "text": {"type": "string"}}, "required": ["window_title", "control_name", "text"]},
            True, self.desktop_automation.set_text,
        ))
        self._register(ToolSpec(
            "lock_windows_session", "Lock the current Windows session. This is a consequential desktop action and always requires confirmation.",
            object_schema, True, self.windows_controls.lock,
        ))
        self._register(ToolSpec(
            "sleep_windows_session", "Request Windows sleep. This is a consequential power action and always requires confirmation.",
            object_schema, True, self.windows_controls.sleep,
        ))

        if self.documents is not None:
            self._register(ToolSpec(
                "index_workspace_document", "Extract and index text from one supported document inside the workspace for local SQLite FTS search. No document contents leave the device.",
                {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
                False, self.documents.index_file,
            ))
            self._register(ToolSpec(
                "search_local_documents", "Search previously indexed local workspace documents and return source paths and excerpts.",
                {"type": "object", "properties": {"query": {"type": "string"}, "limit": {"type": "integer", "default": 6}}, "required": ["query"]},
                False, self.documents.search,
            ))
            self._register(ToolSpec(
                "list_indexed_documents", "List documents indexed in the local SQLite knowledge store.",
                object_schema, False, self.documents.list_indexed,
            ))

        if self.productivity is not None:
            self._register(ToolSpec(
                "create_local_reminder", "Create a local reminder with an explicit future ISO date/time. Reminders only notify; they do not run arbitrary actions.",
                {"type": "object", "properties": {"title": {"type": "string"}, "due_at": {"type": "string"}}, "required": ["title", "due_at"]},
                False, self.productivity.add_reminder,
            ))
            self._register(ToolSpec(
                "list_local_reminders", "List local reminders and their states.", object_schema, False, self.productivity.list_reminders,
            ))
            self._register(ToolSpec(
                "dismiss_local_reminder", "Dismiss one pending local reminder.",
                {"type": "object", "properties": {"reminder_id": {"type": "integer"}}, "required": ["reminder_id"]},
                False, self.productivity.dismiss_reminder,
            ))
            self._register(ToolSpec(
                "add_local_contact", "Save an explicitly supplied contact name and international phone number on this device.",
                {"type": "object", "properties": {"name": {"type": "string"}, "phone": {"type": "string"}, "email": {"type": "string"}}, "required": ["name", "phone"]},
                False, self.productivity.add_contact,
            ))
            self._register(ToolSpec(
                "list_local_contacts", "List the operator's locally stored contacts; no cloud synchronization.", object_schema, False, self.productivity.list_contacts,
            ))
            self._register(ToolSpec(
                "create_calendar_event", "Save a local calendar event and export a standard .ics file into the workspace.",
                {"type": "object", "properties": {"title": {"type": "string"}, "starts_at": {"type": "string"}, "ends_at": {"type": "string"}, "location": {"type": "string"}, "description": {"type": "string"}}, "required": ["title", "starts_at"]},
                False, self.productivity.create_calendar_event,
            ))
            self._register(ToolSpec(
                "list_calendar_events", "List events saved in Chinuuu's local calendar store.", object_schema, False, self.productivity.list_events,
            ))
            self._register(ToolSpec(
                "prepare_email_draft", "Open an email draft in the default mail client using explicit recipient, subject and body. Never sends automatically.",
                {"type": "object", "properties": {"recipient": {"type": "string"}, "subject": {"type": "string"}, "body": {"type": "string"}}, "required": ["recipient", "subject", "body"]},
                False, self.windows_controls.open_email_draft,
            ))

        self._register(ToolSpec(
            "create_workspace_document", "Create a new Markdown, TXT, HTML or DOCX document inside the workspace. Existing files are never overwritten.",
            {"type": "object", "properties": {"path": {"type": "string"}, "title": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "title", "content"]},
            False, self.artifacts.create_document,
        ))
        self._register(ToolSpec(
            "create_workspace_presentation", "Create a new editable PPTX presentation inside the workspace from slide sections separated by ---; first line in each section is the slide title.",
            {"type": "object", "properties": {"path": {"type": "string"}, "title": {"type": "string"}, "outline": {"type": "string"}}, "required": ["path", "title", "outline"]},
            False, self.artifacts.create_presentation,
        ))

        if self.task_store is not None:
            self._register(ToolSpec(
                "create_database_backup", "Create and integrity-check a consistent SQLite backup of Chinuuu's local database.",
                object_schema, False, lambda: create_database_backup(self.task_store.db_path, self.task_store.db_path.parent / "backups"),
            ))
            self._register(ToolSpec(
                "list_agent_runs", "List recent durable agent workflow runs and their final states.",
                object_schema, False, self._list_agent_runs,
            ))
            self._register(ToolSpec(
                "list_tool_audit", "List recent local tool audit events. Arguments may contain personal text, so output is bounded.",
                object_schema, False, self._list_tool_audit,
            ))

        if self.allow_external_network:
            self._register(ToolSpec(
                "fetch_public_webpage", "Fetch and extract up to 1 MB of text from an explicitly requested public HTTP(S) HTML page. Rejects local/private targets and revalidates redirects. ALWAYS requires operator confirmation.",
                {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]},
                True, self.web_research.fetch_public_page,
            ))

        self._register(ToolSpec(
            "get_git_status", "Read the Git working-tree status of the configured workspace; does not change Git state.",
            object_schema, False, self.workspace.git_status,
        ))
        self._register(ToolSpec(
            "calculate", "Evaluate a basic arithmetic expression using numeric literals and +, -, *, /, //, %, and parentheses. No names, functions, imports or attributes.",
            {"type": "object", "properties": {"expression": {"type": "string", "description": "Arithmetic expression"}}, "required": ["expression"]},
            False, safe_calculate,
        ))

    def _list_agent_runs(self) -> str:
        rows = self._require_task_store().recent_agent_runs(20)
        if not rows:
            return "No agent workflow runs have been recorded."
        return "Recent agent workflow runs:\n" + "\n".join(
            f"- {row['id']} [{row['status']}] {redact_text(str(row['objective']))[:160]} — {row['started_at']}" for row in rows
        )

    def _list_tool_audit(self) -> str:
        rows = self._require_task_store().recent_tool_audit(30)
        if not rows:
            return "No tool audit events have been recorded."
        return "Recent local tool audit (arguments omitted):\n" + "\n".join(
            f"- #{row['id']} {row['tool_name']} [{row['outcome']}] {row['started_at']}" + (f" — {row['error']}" if row['error'] else "")
            for row in rows
        )

    def _require_task_store(self):
        if self.task_store is None:
            raise ToolError("Local task storage is not connected to this tool registry.")
        return self.task_store

    def _add_local_task(self, title: str) -> str:
        store = self._require_task_store()
        task_id = store.add_task(title)
        item = store.get_task(task_id)
        if not item or item["title"] != " ".join(title.strip().split()):
            raise ToolError("Task creation could not be verified in SQLite.")
        return f"Task saved: #{task_id} — {item['title']} (verified in local SQLite)."

    def _list_local_tasks(self) -> str:
        items = self._require_task_store().list_tasks(include_done=True)
        if not items:
            return "No local tasks are stored."
        return "Local tasks (SQLite):\n" + "\n".join(
            f"#{item['id']} [{item['status']}] {item['title']}" for item in items
        )

    def _complete_local_task(self, task_id: int) -> str:
        store = self._require_task_store()
        if not store.complete_task(task_id):
            raise ToolError(f"No open local task exists with ID {task_id}.")
        item = store.get_task(task_id)
        if not item or item["status"] != "done":
            raise ToolError(f"Task completion could not be verified for ID {task_id}.")
        return f"Task #{task_id} marked complete and verified in SQLite: {item['title']}"

    def _delete_local_task(self, task_id: int) -> str:
        store = self._require_task_store()
        existing = store.get_task(task_id)
        if existing is None:
            raise ToolError(f"Local task #{task_id} does not exist.")
        if not store.delete_task(task_id) or store.get_task(task_id) is not None:
            raise ToolError(f"Task deletion could not be verified for ID {task_id}.")
        return f"Deleted local task #{task_id}; SQLite verification confirms it is absent."

    def verify_mutation(self, name: str, arguments: dict[str, Any]) -> str:
        """Independently verify a mutating tool result against the live filesystem."""
        try:
            if name == "write_workspace_file":
                target = self.workspace._resolve(arguments.get("path", ""))
                expected = arguments.get("content")
                if not isinstance(expected, str):
                    raise ToolError("File verification failed: expected content is missing or invalid.")
                if not target.is_file() or target.read_bytes() != expected.encode("utf-8"):
                    raise ToolError(f"File write was not verified: {target} is absent or its contents differ.")
                return f"Filesystem verification: file exists and content matches ({target})."

            if name == "create_workspace_directory":
                target = self.workspace._resolve(arguments.get("path", ""))
                if not target.is_dir():
                    raise ToolError(f"Directory creation was not verified: {target} is not a directory.")
                return f"Filesystem verification: directory exists. Path: {target}"

            if name == "delete_workspace_file":
                target = self.workspace._resolve(arguments.get("path", ""))
                if target.exists():
                    raise ToolError(f"File deletion was not verified: {target} still exists.")
                return f"Filesystem verification: file is absent ({target})."

            if name == "delete_workspace_directory":
                target = self.workspace._resolve(arguments.get("path", ""))
                if target.exists():
                    raise ToolError(f"Directory deletion was not verified: {target} still exists.")
                return f"Filesystem verification: directory is absent ({target})."

            if name == "move_workspace_path":
                source = self.workspace._resolve(arguments.get("source", ""))
                destination = self.workspace._resolve(arguments.get("destination", ""))
                if source.exists() or not destination.exists():
                    raise ToolError("Move verification failed: source still exists or destination is absent.")
                return f"Filesystem verification: source is absent and destination exists ({destination})."
        except OSError as exc:
            raise ToolError(f"Filesystem verification failed: {exc}") from exc

        raise ToolError(f"Tool '{name}' is not a supported filesystem mutation.")

    def schemas(self) -> list[dict[str, Any]]:
        return [item.as_ollama_schema() for item in self._tools.values()]

    def descriptions(self) -> list[tuple[str, str, str]]:
        descriptions: list[tuple[str, str, str]] = []
        for spec in self._tools.values():
            if spec.name == "write_workspace_file":
                permission = "Approval only when replacing an existing file"
            elif spec.requires_confirmation:
                permission = "Confirmation required"
            else:
                permission = "No confirmation required"
            descriptions.append((spec.name, permission, spec.description))
        return descriptions

    def execute(
        self,
        name: str,
        arguments: dict[str, Any],
        confirm: Callable[[str, dict[str, Any]], bool] | None = None,
    ) -> str:
        spec = self._tools.get(name)
        if spec is None:
            raise ToolError(f"Tool '{name}' is not registered.")
        if not isinstance(arguments, dict):
            raise ToolError("Tool arguments must be an object.")
        allowed = set(spec.parameters.get("properties", {}))
        unexpected = set(arguments) - allowed
        if unexpected:
            raise ToolError(f"Unexpected tool arguments: {', '.join(sorted(unexpected))}")
        required = set(spec.parameters.get("required", []))
        missing = required - set(arguments)
        if missing:
            raise ToolError(f"Missing required arguments: {', '.join(sorted(missing))}")
        for key, value in arguments.items():
            expected = spec.parameters.get("properties", {}).get(key, {}).get("type")
            valid = {
                "string": lambda v: isinstance(v, str),
                "boolean": lambda v: type(v) is bool,
                "integer": lambda v: type(v) is int,
                "number": lambda v: type(v) in (int, float),
                "object": lambda v: isinstance(v, dict),
                "array": lambda v: isinstance(v, list),
            }.get(expected)
            if valid is not None and not valid(value):
                raise ToolError(f"Argument '{key}' must have JSON type '{expected}'.")
        needs_confirmation = spec.requires_confirmation
        if spec.confirmation_condition is not None:
            try:
                condition_requires_confirmation = spec.confirmation_condition(arguments)
                needs_confirmation = needs_confirmation or condition_requires_confirmation
            except ToolError:
                raise
            except (OSError, ValueError, TypeError) as exc:
                raise ToolError(f"Could not evaluate the operation's permission requirement: {exc}") from exc
        redacted = redact_data(arguments)
        arguments_json = json.dumps(redacted, ensure_ascii=False, default=str)
        if needs_confirmation:
            if confirm is None or not confirm(name, arguments):
                if self.task_store is not None:
                    self.task_store.record_tool_audit(self.active_run_id, name, arguments_json, "approval_denied")
                return "Not executed: the operator did not approve this operation."
        try:
            result = str(spec.handler(**arguments))
            if self.task_store is not None:
                outcome = "approval_cancelled" if result.startswith("Not executed:") else "completed"
                self.task_store.record_tool_audit(self.active_run_id, name, arguments_json, outcome)
            return result
        except TypeError as exc:
            if self.task_store is not None:
                self.task_store.record_tool_audit(self.active_run_id, name, arguments_json, "failed", redact_text(str(exc)))
            raise ToolError(f"Invalid arguments for {name}: {exc}") from exc
        except (OSError, ValueError) as exc:
            if self.task_store is not None:
                self.task_store.record_tool_audit(self.active_run_id, name, arguments_json, "failed", redact_text(str(exc)))
            raise ToolError(str(exc)) from exc
        except ToolError as exc:
            if self.task_store is not None:
                self.task_store.record_tool_audit(self.active_run_id, name, arguments_json, "failed", redact_text(str(exc)))
            raise


def safe_calculate(expression: str) -> str:
    if not isinstance(expression, str) or not expression.strip() or len(expression) > 120:
        raise ToolError("Provide an arithmetic expression of at most 120 characters.")
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        raise ToolError("Invalid arithmetic expression.") from exc

    allowed_binops = (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod, ast.Pow)
    allowed_unary = (ast.UAdd, ast.USub)

    def evaluate(node: ast.AST) -> float | int:
        if isinstance(node, ast.Expression):
            return evaluate(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
            return node.value
        if isinstance(node, ast.BinOp) and isinstance(node.op, allowed_binops):
            left, right = evaluate(node.left), evaluate(node.right)
            if isinstance(node.op, ast.Pow) and abs(float(right)) > 8:
                raise ToolError("Exponent magnitude is limited to 8.")
            result = {
                ast.Add: lambda: left + right,
                ast.Sub: lambda: left - right,
                ast.Mult: lambda: left * right,
                ast.Div: lambda: left / right,
                ast.FloorDiv: lambda: left // right,
                ast.Mod: lambda: left % right,
                ast.Pow: lambda: left ** right,
            }[type(node.op)]()
            if isinstance(result, complex) or not math.isfinite(float(result)) or abs(float(result)) > 1e100:
                raise ToolError("Result is outside the supported numeric range.")
            return result
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, allowed_unary):
            value = evaluate(node.operand)
            return value if isinstance(node.op, ast.UAdd) else -value
        raise ToolError("Only basic arithmetic is supported; function calls, names and attributes are blocked.")

    try:
        result = evaluate(tree)
    except ZeroDivisionError as exc:
        raise ToolError("Division by zero is not allowed.") from exc
    except OverflowError as exc:
        raise ToolError("Result is outside the supported numeric range.") from exc
    return str(result)
