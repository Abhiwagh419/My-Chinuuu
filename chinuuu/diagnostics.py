from __future__ import annotations

import importlib.util
import json
import sqlite3
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from .config import Settings, project_root, resolve_workspace_path
from .database import LocalStore


def run_diagnostics(settings: Settings, store: LocalStore, tool_count: int | None = None) -> str:
    """Read-only local health report. Network checks are restricted to configured Ollama URL."""
    checks: list[tuple[str, str, str]] = []
    try:
        with sqlite3.connect(store.db_path) as con:
            result = str(con.execute("PRAGMA quick_check").fetchone()[0])
        checks.append(("SQLite integrity", "PASS" if result == "ok" else "FAIL", result))
    except (sqlite3.Error, OSError) as exc:
        checks.append(("SQLite integrity", "FAIL", f"{type(exc).__name__}: {exc}"))

    workspace = resolve_workspace_path(settings.workspace_root, project_root())
    checks.append(("Workspace", "PASS" if workspace.is_dir() else "WARN", str(workspace)))

    request = urllib.request.Request(f"{settings.ollama_url.rstrip('/')}/api/tags", method="GET")
    try:
        with urllib.request.urlopen(request, timeout=2.5) as response:
            payload: dict[str, Any] = json.loads(response.read().decode("utf-8"))
        models = {str(item.get("name", "")) for item in payload.get("models", [])}
        if settings.model in models:
            checks.append(("Ollama model", "PASS", f"{settings.model} is installed"))
        else:
            checks.append(("Ollama model", "WARN", f"Ollama is reachable, but {settings.model} is not listed"))
    except (urllib.error.URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError) as exc:
        checks.append(("Ollama model", "FAIL", f"Local Ollama endpoint unavailable: {type(exc).__name__}"))

    checks.append(("Tool registry", "INFO", f"{tool_count} registered tools" if tool_count is not None else "Not inspected"))
    checks.append(("External web tools", "INFO", "Enabled (confirmation required)" if settings.allow_external_network_tools else "Disabled"))
    checks.append(("Conversation summaries", "INFO", "Local SQLite; generated only by the configured local model"))

    lines = ["CHINUUU LOCAL HEALTH REPORT", "=" * 31]
    for name, status, detail in checks:
        lines.append(f"[{status}] {name}: {detail}")
    lines.append("\nNo system settings were changed by this diagnostic.")
    return "\n".join(lines)
