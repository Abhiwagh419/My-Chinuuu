from __future__ import annotations

import os
import re
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from .errors import ToolError


def _parse_datetime(value: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ToolError("Provide a date and time in ISO format, for example 2026-11-01T18:30.")
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError as exc:
        raise ToolError("Date/time must use ISO format: YYYY-MM-DDTHH:MM, optionally with a UTC offset.") from exc
    if parsed.tzinfo is None:
        parsed = parsed.astimezone()
    return parsed.astimezone(timezone.utc)


def _ics_escape(value: str) -> str:
    normalized = value.replace("\r\n", "\n").replace("\r", "\n")
    return normalized.replace("\\", "\\\\").replace(";", r"\;").replace(",", r"\,").replace("\n", r"\n")


class ProductivityService:
    def __init__(self, store, workspace_root: Path) -> None:
        self.store = store
        self.workspace_root = workspace_root.resolve()

    def add_reminder(self, title: str, due_at: str) -> str:
        cleaned = " ".join(title.split())
        if not cleaned or len(cleaned) > 240:
            raise ToolError("Reminder title must contain 1–240 characters.")
        due = _parse_datetime(due_at)
        if due <= datetime.now(timezone.utc):
            raise ToolError("Reminder time must be in the future.")
        with self.store._connect() as con:
            cursor = con.execute(
                "INSERT INTO reminders(title,due_at,status,created_at) VALUES(?,?,'pending',?)",
                (cleaned, due.isoformat(timespec="seconds"), self.store._now()),
            )
            reminder_id = int(cursor.lastrowid)
            row = con.execute("SELECT title,due_at,status FROM reminders WHERE id=?", (reminder_id,)).fetchone()
        if not row or row["status"] != "pending":
            raise ToolError("Reminder could not be verified in SQLite.")
        return f"Reminder #{reminder_id} saved: {cleaned}; due {row['due_at']} (stored locally)."

    def list_reminders(self) -> str:
        with self.store._connect() as con:
            rows = con.execute(
                "SELECT id,title,due_at,status FROM reminders ORDER BY CASE status WHEN 'pending' THEN 0 ELSE 1 END,due_at LIMIT 100"
            ).fetchall()
        if not rows:
            return "No reminders are stored."
        return "Local reminders:\n" + "\n".join(
            f"#{r['id']} [{r['status']}] {r['title']} — {r['due_at']}" for r in rows
        )

    def dismiss_reminder(self, reminder_id: int) -> str:
        with self.store._connect() as con:
            cursor = con.execute("UPDATE reminders SET status='dismissed' WHERE id=? AND status='pending'", (reminder_id,))
        if cursor.rowcount != 1:
            raise ToolError(f"Pending reminder #{reminder_id} was not found.")
        return f"Reminder #{reminder_id} dismissed and verified."

    def add_contact(self, name: str, phone: str, email: str = "") -> str:
        cleaned_name = " ".join(name.split())
        digits = re.sub(r"[\s().-]", "", phone)
        if not cleaned_name or len(cleaned_name) > 100:
            raise ToolError("Contact name must contain 1–100 characters.")
        if digits.startswith("00"):
            digits = "+" + digits[2:]
        if digits.startswith("+"):
            normalized_phone = "+" + re.sub(r"\D", "", digits)
        else:
            normalized_phone = "+" + re.sub(r"\D", "", digits)
        if not re.fullmatch(r"\+[1-9][0-9]{7,14}", normalized_phone):
            raise ToolError("Provide an international phone number with country code (8–15 digits).")
        if email and not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
            raise ToolError("Contact email is not valid.")
        with self.store._connect() as con:
            con.execute(
                "INSERT INTO contacts(name,normalized_name,phone_e164,email,created_at) VALUES(?,?,?,?,?) "
                "ON CONFLICT(normalized_name,phone_e164) DO UPDATE SET name=excluded.name,email=excluded.email",
                (cleaned_name, cleaned_name.casefold(), normalized_phone, email.strip(), self.store._now()),
            )
            rows = con.execute("SELECT id FROM contacts WHERE normalized_name=? AND phone_e164=?", (cleaned_name.casefold(), normalized_phone)).fetchall()
        if len(rows) != 1:
            raise ToolError("Contact save could not be uniquely verified.")
        return f"Contact saved locally: {cleaned_name} (verified in SQLite). The phone number is stored privately in the local contacts database."

    def resolve_contact(self, name: str) -> str:
        cleaned = " ".join(name.split()).casefold()
        with self.store._connect() as con:
            rows = con.execute("SELECT name,phone_e164 FROM contacts WHERE normalized_name=?", (cleaned,)).fetchall()
        if not rows:
            raise ToolError(f"No local contact exactly matches '{name}'. Add it using /contact add first.")
        phones = {str(row["phone_e164"]) for row in rows}
        if len(phones) != 1:
            raise ToolError(f"Contact name '{name}' matches multiple phone numbers. Use the exact phone number.")
        return next(iter(phones))

    def list_contacts(self) -> str:
        with self.store._connect() as con:
            rows = con.execute("SELECT id,name,phone_e164,email FROM contacts ORDER BY normalized_name").fetchall()
        if not rows:
            return "No local contacts are stored."
        return "Local contacts (stored only on this device):\n" + "\n".join(
            f"#{r['id']} {r['name']} — {r['phone_e164']}" + (f"; {r['email']}" if r['email'] else "") for r in rows
        )

    def create_calendar_event(
        self, title: str, starts_at: str, ends_at: str = "", location: str = "", description: str = ""
    ) -> str:
        cleaned_title = " ".join(title.split())
        if not cleaned_title or len(cleaned_title) > 200:
            raise ToolError("Event title must contain 1–200 characters.")
        start = _parse_datetime(starts_at)
        end = _parse_datetime(ends_at) if ends_at.strip() else start + timedelta(hours=1)
        if end <= start:
            raise ToolError("Calendar event end must be after its start.")
        if len(location) > 200 or len(description) > 2000:
            raise ToolError("Event location or description exceeds its length limit.")
        with self.store._connect() as con:
            cursor = con.execute(
                "INSERT INTO calendar_events(title,starts_at,ends_at,location,description,created_at) VALUES(?,?,?,?,?,?)",
                (cleaned_title, start.isoformat(timespec="seconds"), end.isoformat(timespec="seconds"), location.strip(), description.strip(), self.store._now()),
            )
            event_id = int(cursor.lastrowid)
            row = con.execute("SELECT title,starts_at,ends_at FROM calendar_events WHERE id=?", (event_id,)).fetchone()
        if not row:
            raise ToolError("Calendar event could not be verified in SQLite.")
        uid = str(uuid.uuid4())
        dtstamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        dtstart = start.strftime("%Y%m%dT%H%M%SZ")
        dtend = end.strftime("%Y%m%dT%H%M%SZ")
        lines = [
            "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Chinuuu//Personal Calendar//EN", "CALSCALE:GREGORIAN",
            "BEGIN:VEVENT", f"UID:{uid}", f"DTSTAMP:{dtstamp}", f"DTSTART:{dtstart}", f"DTEND:{dtend}",
            f"SUMMARY:{_ics_escape(cleaned_title)}", f"LOCATION:{_ics_escape(location)}", f"DESCRIPTION:{_ics_escape(description)}",
            "END:VEVENT", "END:VCALENDAR", "",
        ]
        exports = self.workspace_root / "calendar_exports"
        if exports.is_symlink() or (callable(getattr(exports, "is_junction", None)) and exports.is_junction()):
            raise ToolError("Calendar exports cannot be written through a symbolic link or directory junction.")
        exports.mkdir(parents=True, exist_ok=True)
        if not exports.resolve().is_relative_to(self.workspace_root):
            raise ToolError("Calendar export path escaped the configured workspace.")
        safe_name = re.sub(r"[^A-Za-z0-9_-]+", "_", cleaned_title).strip("_")[:48] or "event"
        target = exports / f"event_{event_id}_{safe_name}.ics"
        is_junction = getattr(target, "is_junction", None)
        if target.is_symlink() or (callable(is_junction) and is_junction()):
            raise ToolError("Calendar export target cannot be a symbolic link or junction.")
        payload = "\r\n".join(lines)
        descriptor: int | None = None
        try:
            descriptor = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as output:
                descriptor = None  # fdopen owns and closes the descriptor.
                output.write(payload)
        except OSError as exc:
            if descriptor is not None:
                os.close(descriptor)
                target.unlink(missing_ok=True)
            raise ToolError(f"Calendar event was saved locally, but the .ics export failed: {exc}") from exc
        if not target.is_file() or target.stat().st_size == 0:
            target.unlink(missing_ok=True)
            raise ToolError("Event is in local SQLite, but the calendar export could not be verified.")
        return f"Calendar event #{event_id} saved locally and exported as an iCalendar file: {target}. Import the .ics file into your calendar app."

    def list_events(self) -> str:
        with self.store._connect() as con:
            rows = con.execute("SELECT id,title,starts_at,ends_at,location FROM calendar_events ORDER BY starts_at LIMIT 100").fetchall()
        if not rows:
            return "No calendar events are stored."
        return "Local calendar events:\n" + "\n".join(
            f"#{r['id']} {r['title']} — {r['starts_at']} to {r['ends_at']}" + (f"; {r['location']}" if r['location'] else "") for r in rows
        )


class ReminderScheduler:
    """A bounded reminder poller. It emits local notifications; it never runs arbitrary actions."""

    def __init__(self, store, notify: Callable[[str], None], interval_seconds: float = 10.0) -> None:
        self.store = store
        self.notify = notify
        self.interval_seconds = max(2.0, min(interval_seconds, 60.0))
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="ChinuuuReminderScheduler", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)

    def _run(self) -> None:
        while not self._stop.is_set():
            now = datetime.now(timezone.utc).isoformat(timespec="seconds")
            due: list[tuple[int, str]] = []
            try:
                with self.store._connect() as con:
                    con.execute("BEGIN IMMEDIATE")
                    rows = con.execute("SELECT id,title FROM reminders WHERE status='pending' AND due_at<=? ORDER BY due_at LIMIT 20", (now,)).fetchall()
                    for row in rows:
                        cursor = con.execute("UPDATE reminders SET status='fired',fired_at=? WHERE id=? AND status='pending'", (now, int(row["id"])))
                        if cursor.rowcount == 1:
                            due.append((int(row["id"]), str(row["title"])))
            except Exception:
                due = []  # A transient DB lock must not terminate the scheduler thread.
            for reminder_id, title in due:
                try:
                    self.notify(f"Reminder #{reminder_id}: {title}")
                except Exception:
                    pass
            self._stop.wait(self.interval_seconds)
