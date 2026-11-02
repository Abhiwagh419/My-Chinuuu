from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .errors import ToolError


def create_database_backup(db_path: Path, backup_dir: Path) -> str:
    source = db_path.resolve()
    if not source.is_file():
        raise ToolError(f"SQLite database does not exist: {source}")
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    destination = backup_dir / f"chinuuu_{stamp}.sqlite3"
    if destination.exists():
        raise ToolError("A backup with this timestamp already exists; retry shortly.")
    try:
        with sqlite3.connect(source) as src, sqlite3.connect(destination) as dst:
            src.backup(dst)
            result = dst.execute("PRAGMA integrity_check").fetchone()
            if not result or result[0] != "ok":
                raise ToolError(f"Backup integrity check failed: {result[0] if result else 'no result'}")
    except ToolError:
        destination.unlink(missing_ok=True)
        raise
    except sqlite3.Error as exc:
        destination.unlink(missing_ok=True)
        raise ToolError(f"SQLite backup failed: {exc}") from exc
    if not destination.is_file() or destination.stat().st_size == 0:
        destination.unlink(missing_ok=True)
        raise ToolError("Backup file could not be verified.")
    return f"SQLite backup created and integrity-checked: {destination} ({destination.stat().st_size:,} bytes)."
