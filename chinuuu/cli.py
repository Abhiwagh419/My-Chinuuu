from __future__ import annotations

from pathlib import Path
import re
import subprocess
import sys
import atexit
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.prompt import Confirm
from rich.text import Text

from .assistant import Assistant, _extract_python_run_path
from .config import load_settings, project_root, resolve_workspace_path
from .database import LocalStore
from .learning import (
    category_for_explicit_preference,
    detect_behavior_signals,
    explicit_preference_candidate,
)
from .ollama_client import OllamaClient, OllamaError
from .system_info import local_time_report, process_report, system_report
from .presentation import assistant_response_panel
from .tools import ToolError, ToolRegistry
from .desktop import administrator_launch_rejection, is_administrator_launch_request
from .diagnostics import run_diagnostics
from .productivity import ReminderScheduler
from .voice import OfflineVoiceEngine

console = Console()


def print_help() -> None:
    table = Table(title="Chinuuu commands", header_style="bold cyan")
    table.add_column("Command", style="green")
    table.add_column("Description")
    rows = [
        ("/help", "Show commands"),
        ("/time", "Show local date and time"),
        ("/system", "Read-only CPU, RAM, disk, battery and uptime telemetry"),
        ("/health", "Diagnose SQLite, Ollama model, workspace and local tool readiness"),
        ("/context", "Show conversation-summary and memory-context statistics"),
        ("/processes", "List process names and memory usage (read-only)"),
        ("/apps", "Discover installed Start Menu applications and configured aliases"),
        ("/open <app name>", "Launch an installed app without a Chinuuu confirmation (e.g. /open cmd)"),
        ("/settings [page]", "Open a Windows Settings page without changing its values"),
        ("/computer", "List common computer locations and local drives (read-only)"),
        ("/browse <absolute path>", "Browse a local computer directory (read-only)"),
        ("/thispc", "Open This PC in File Explorer"),
        ("/search <query>", "Search public web after enabling external research and confirming the query"),
        ("/todo add <task>", "Save a task in the local SQLite database"),
        ("/todo list", "List open and completed local tasks"),
        ("/todo done <id>", "Mark a task complete"),
        ("/todo delete <id>", "Delete a local task after confirmation"),
        ("/check <file.py>", "Check Python syntax without executing the file"),
        ("/workspace", "Show the configured file workspace"),
        ("/tools", "List available local tools and permission requirements"),
        ("/plan <task>", "Ask Chinuuu to analyse a task and identify missing requirements; does not execute"),
        ("/remember <text>", "Propose a preference for long-term memory"),
        ("/memory list", "List approved memories"),
        ("/memory pending", "Review possible memories detected by Chinuuu"),
        ("/memory approve <id>", "Approve a pending memory candidate"),
        ("/memory reject <id>", "Reject a pending memory candidate"),
        ("/memory show <id>", "Inspect one approved memory"),
        ("/memory edit <id> <text>", "Edit an approved memory"),
        ("/memory delete <id>", "Delete an approved memory"),
        ("/index <path>", "Extract and index a local workspace document (TXT, Markdown, PDF, DOCX, HTML, CSV, JSON, Python)"),
        ("/docs <query>", "Search previously indexed local documents (SQLite FTS full-text retrieval)"),
        ("/indexed", "List locally indexed documents"),
        ("/reminders", "List local reminders; create with: remind me to <task> at YYYY-MM-DD HH:MM"),
        ("/calendar", "List local calendar events; create with: create calendar event <title> at YYYY-MM-DD HH:MM"),
        ("/contacts", "List local contacts; add with: save contact <name> with phone +countrycode"),
        ("/backup", "Create and integrity-check a local SQLite backup"),
        ("/runs", "Show recent durable agent workflow runs"),
        ("/audit", "Show recent local tool audit events (sensitive arguments redacted)"),
        ("/voice", "Record a six-second push-to-talk request using an installed local Whisper model"),
        ("/gui", "Launch Chinuuu's Tkinter desktop interface"),
        ("/clear", "Clear saved conversation history"),
        ("/exit", "Exit Chinuuu"),
    ]
    for command, description in rows:
        table.add_row(command, description)
    console.print(table)


def _parse_id(command: str) -> int | None:
    try:
        return int(command.rsplit(" ", 1)[-1])
    except ValueError:
        return None


def _show_candidate(store: LocalStore, candidate_id: int) -> None:
    candidates = [c for c in store.list_candidates("pending") if c["id"] == candidate_id]
    if not candidates:
        return
    item = candidates[0]
    console.print(Panel(
        f"[bold]{item['content']}[/bold]\n\n"
        f"Category: {item['category']}\n"
        f"Origin: {item['source']}\n"
        f"Evidence count: {item['evidence_count']}\n"
        f"Confidence estimate: {float(item['confidence']):.0%}\n"
        f"Evidence: {item['evidence_summary']}",
        title=f"Learning candidate #{candidate_id}", border_style="yellow",
    ))
    if Confirm.ask("Approve and save this as a long-term memory?", default=False):
        approved = store.approve_candidate(candidate_id)
        console.print("[green]Approved. This memory will be used in future sessions.[/green]" if approved else "[yellow]Candidate is no longer pending.[/yellow]")
    else:
        console.print("[dim]Not saved. You can review it later with /memory pending.[/dim]")


def _handle_memory_command(user_text: str, store: LocalStore) -> bool:
    """Handle /memory and /remember commands. Return True when handled."""
    lower = user_text.lower()

    if lower.startswith("/remember "):
        content = user_text[len("/remember "):].strip()
        if not content:
            console.print("[yellow]Usage: /remember <preference>[/yellow]")
            return True
        candidate_id = store.add_candidate(
            content, "preference", "operator_submitted", 1.0,
            "Explicitly proposed by Abhi using /remember.", 1,
        )
        if candidate_id is None:
            console.print("[yellow]That preference already exists or is already under review.[/yellow]")
        else:
            _show_candidate(store, candidate_id)
        return True

    if lower == "/memory list":
        memories = store.list_memories()
        if not memories:
            console.print("[dim]No approved long-term memories saved yet.[/dim]")
            return True
        table = Table(title="Approved local memories")
        for column in ("ID", "Category", "Source", "Confidence", "Memory"):
            table.add_column(column, style="cyan" if column == "ID" else None)
        for item in memories:
            table.add_row(str(item["id"]), str(item["category"]), str(item["source"]),
                          f"{float(item['confidence']):.0%}", str(item["content"]))
        console.print(table)
        return True

    if lower == "/memory pending":
        candidates = store.list_candidates("pending")
        if not candidates:
            console.print("[dim]No pending learning suggestions. I will keep observing explicit corrections and repeated preferences.[/dim]")
            return True
        table = Table(title="Pending memory suggestions — approval required")
        table.add_column("ID", style="cyan")
        table.add_column("Category")
        table.add_column("Source")
        table.add_column("Evidence")
        table.add_column("Confidence")
        table.add_column("Suggestion")
        for item in candidates:
            table.add_row(str(item["id"]), str(item["category"]), str(item["source"]),
                          str(item["evidence_count"]), f"{float(item['confidence']):.0%}", str(item["content"]))
        console.print(table)
        return True

    if lower.startswith("/memory approve ") or lower.startswith("/memory reject "):
        parts = user_text.split()
        if len(parts) != 3 or not parts[2].isdigit():
            console.print("[yellow]Usage: /memory approve <candidate_id> OR /memory reject <candidate_id>[/yellow]")
            return True
        candidate_id = int(parts[2])
        if parts[1].lower() == "approve":
            success = store.approve_candidate(candidate_id)
            console.print("[green]Candidate approved and saved.[/green]" if success else "[yellow]Pending candidate not found.[/yellow]")
        else:
            success = store.reject_candidate(candidate_id)
            console.print("[green]Candidate rejected. It will not be used as memory.[/green]" if success else "[yellow]Pending candidate not found.[/yellow]")
        return True

    if lower.startswith("/memory show "):
        memory_id = _parse_id(user_text)
        item = store.get_memory(memory_id) if memory_id is not None else None
        if not item:
            console.print("[yellow]Memory not found. Usage: /memory show <id>[/yellow]")
        else:
            console.print(Panel(
                f"{item['content']}\n\nCategory: {item['category']}\nSource: {item['source']}\n"
                f"Confidence: {float(item['confidence']):.0%}\nCreated: {item['created_at']}\nUpdated: {item['updated_at']}",
                title=f"Memory #{memory_id}", border_style="cyan",
            ))
        return True

    if lower.startswith("/memory edit "):
        parts = user_text.split(maxsplit=3)
        if len(parts) < 4 or not parts[2].isdigit():
            console.print("[yellow]Usage: /memory edit <id> <replacement text>[/yellow]")
            return True
        memory_id, replacement = int(parts[2]), parts[3].strip()
        item = store.get_memory(memory_id)
        if not item:
            console.print("[yellow]Memory not found.[/yellow]")
        else:
            console.print(Panel(replacement, title=f"Replacement for memory #{memory_id}", border_style="yellow"))
            if Confirm.ask("Apply this memory update?", default=False):
                try:
                    updated = store.update_memory(memory_id, replacement)
                except Exception as exc:
                    console.print(f"[red]Update failed: {exc}[/red]")
                else:
                    console.print("[green]Memory updated.[/green]" if updated else "[yellow]Memory was not updated.[/yellow]")
        return True

    if lower.startswith("/memory delete "):
        memory_id = _parse_id(user_text)
        if memory_id is None:
            console.print("[yellow]Usage: /memory delete <id>[/yellow]")
        elif Confirm.ask(f"Delete approved memory #{memory_id}?", default=False):
            removed = store.delete_memory(memory_id)
            console.print("[green]Memory deleted.[/green]" if removed else "[yellow]Memory not found.[/yellow]")
        return True

    if lower.startswith("/memory"):
        console.print("[yellow]Unknown memory command. Use /help.[/yellow]")
        return True
    return False


def _collect_learning_suggestions(user_text: str, store: LocalStore, settings) -> list[int]:
    if not settings.auto_suggest_preferences:
        return []

    suggestions: list[int] = []
    explicit = explicit_preference_candidate(user_text)
    if explicit:
        candidate_id = store.add_candidate(
            explicit, category_for_explicit_preference(explicit), "explicit_instruction", 0.95,
            "Explicit preference or correction stated by Abhi in this message.", 1,
        )
        if candidate_id is not None:
            suggestions.append(candidate_id)

    else:
        for signal in detect_behavior_signals(user_text):
            count = store.record_behavior_observation(
                signal.key, signal.category, signal.evidence, settings.behavior_window_days
            )
            if count >= settings.behavior_threshold:
                confidence = min(0.95, 0.70 + 0.05 * (count - settings.behavior_threshold))
                evidence = (
                    f"Observed {count} matching interactions within {settings.behavior_window_days} days. "
                    f"Most recent example: {signal.evidence}"
                )
                candidate_id = store.add_candidate(
                    signal.proposed_memory, signal.category, "repeated_behavior",
                    confidence, evidence, count,
                )
                if candidate_id is not None:
                    suggestions.append(candidate_id)

    return list(dict.fromkeys(suggestions))



def _handle_todo_command(user_text: str, store: LocalStore) -> bool:
    lower = user_text.casefold()
    if lower == "/todo list":
        tasks = store.list_tasks()
        if not tasks:
            console.print("[dim]No local tasks yet. Add one with /todo add <task>.[/dim]")
            return True
        table = Table(title="Local tasks")
        table.add_column("ID", style="cyan", width=5)
        table.add_column("Status", width=8)
        table.add_column("Task")
        for item in tasks:
            table.add_row(str(item["id"]), str(item["status"]), str(item["title"]))
        console.print(table)
        return True
    if lower.startswith("/todo add "):
        title = user_text[len("/todo add "):].strip()
        try:
            task_id = store.add_task(title)
        except ValueError as exc:
            console.print(f"[yellow]{exc}[/yellow]")
        else:
            console.print(f"[green]Task #{task_id} saved locally and verified in SQLite.[/green]")
        return True
    if lower.startswith("/todo done "):
        raw_id = user_text[len("/todo done "):].strip()
        if not raw_id.isdigit():
            console.print("[yellow]Usage: /todo done <id>[/yellow]")
        else:
            updated = store.complete_task(int(raw_id))
            console.print("[green]Task marked complete.[/green]" if updated else "[yellow]Open task not found.[/yellow]")
        return True
    if lower.startswith("/todo delete "):
        raw_id = user_text[len("/todo delete "):].strip()
        if not raw_id.isdigit():
            console.print("[yellow]Usage: /todo delete <id>[/yellow]")
        elif Confirm.ask(f"Permanently delete local task #{raw_id}?", default=False):
            deleted = store.delete_task(int(raw_id))
            console.print("[green]Task deleted.[/green]" if deleted else "[yellow]Task not found.[/yellow]")
        else:
            console.print("[dim]No task was deleted.[/dim]")
        return True
    if lower == "/todo" or lower.startswith("/todo "):
        console.print("[yellow]Usage: /todo add <task> | /todo list | /todo done <id> | /todo delete <id>[/yellow]")
        return True
    return False


def _confirm_tool_call(name: str, arguments: dict, registry: ToolRegistry | None = None) -> bool:
    """Application-owned approval gate; model text cannot approve an operation."""
    details = [f"Tool: {name}"]
    question = "Allow this operation inside the configured workspace?"

    if name == "delete_workspace_directory" and registry is not None:
        relative_path = str(arguments.get("path", ""))
        summary = registry.workspace.preview_delete_directory(relative_path)
        target = registry.workspace._resolve(relative_path)
        details.extend([
            f"Target: {target}",
            f"Contents: {summary['directories']} subdirectories, {summary['files']} files",
            f"Total file size: {summary['bytes']:,} bytes",
            "This permanently removes the directory and everything listed above.",
        ])
        question = "Permanently delete this directory and all its contents?"
    elif name == "delete_workspace_file" and registry is not None:
        target = registry.workspace._resolve(str(arguments.get("path", "")))
        details.append(f"Target file: {target}")
        question = "Permanently delete this workspace file?"
    elif name == "write_workspace_file" and registry is not None:
        target = registry.workspace._resolve(str(arguments.get("path", "")))
        details.append(f"Existing file: {target}")
        question = "Replace the existing file? A backup will be created first."
    elif name == "move_workspace_path" and registry is not None:
        source = registry.workspace._resolve(str(arguments.get("source", "")))
        destination = registry.workspace._resolve(str(arguments.get("destination", "")))
        details.extend([f"Source: {source}", f"Destination: {destination}"])
        question = "Move this workspace item? Existing destinations are never overwritten."
    elif name == "delete_local_task" and registry is not None and registry.task_store is not None:
        task_id = int(arguments.get("task_id", 0))
        item = registry.task_store.get_task(task_id)
        if item is None:
            raise ToolError(f"Local task #{task_id} does not exist.")
        details.extend([f"Task ID: {task_id}", f"Task: {item['title']}", f"Status: {item['status']}"])
        question = f"Permanently delete local task #{task_id}?"
    elif name == "search_public_web" and registry is not None:
        query = str(arguments.get("query", ""))
        details.extend([
            f"Search query: {query}",
            "Provider: DuckDuckGo public HTML search",
            "Only this query is sent. Conversation history, local files and memories are not uploaded.",
            "Do not search for passwords, private identifiers or sensitive personal data.",
        ])
        question = "Send this query to the public web search provider?"
    elif name == "run_workspace_python" and registry is not None:
        target, source = registry.workspace.python_source(str(arguments.get("path", "")))
        preview_lines = source.splitlines()[:50]
        preview = "\n".join(preview_lines) if preview_lines else "(empty Python file)"
        if len(source.splitlines()) > 50:
            preview += "\n... preview truncated after 50 lines"
        timeout = int(arguments.get("timeout_seconds", 15))
        details.extend([
            f"Script: {target}",
            f"Working directory: {registry.workspace_root}",
            f"Runtime limit: {timeout} seconds",
            "Python code preview:",
            preview,
            "WARNING: This runs as your current Windows user. It is NOT an OS sandbox and may access files/network permitted to your account. Only approve code you trust.",
        ])
        question = "Run this Python script now?"
    else:
        details.append(f"Arguments: {arguments}")

    console.print(Panel(
        Text("\n".join(details)),
        title="Operator approval required",
        border_style="yellow",
    ))
    return Confirm.ask(question, default=False)


def main() -> None:
    app_root = project_root()
    settings = load_settings(app_root / "config.toml")
    store = LocalStore(app_root / "data" / "chinuuu.sqlite3")
    client = OllamaClient(
        settings.ollama_url, settings.model, settings.context_window, settings.temperature
    )
    assistant = Assistant(settings, store, client)
    workspace_setting = resolve_workspace_path(settings.workspace_root, app_root)
    registry = ToolRegistry(
        workspace_setting, app_allowlist=settings.app_allowlist,
        allow_external_network=settings.allow_external_network_tools, task_store=store,
        embedding_model=settings.embedding_model, ollama_url=settings.ollama_url,
    )

    console.print(Panel.fit(
        "[bold bright_cyan]CHINUUU[/bold bright_cyan]\n"
        "[white]Personal Local Intelligence System[/white]\n"
        f"[dim]Operator: {settings.operator_name} | Model: {settings.model}[/dim]",
        border_style="bright_blue",
    ))
    console.print("[cyan]Chinuuu:[/cyan] Good evening, Abhi. Local terminal interface initialized.")
    console.print("[dim]Type /help for commands. Local tools are confined to the configured workspace.[/dim]")
    console.print(f"[dim]Application: {app_root}[/dim]")
    console.print(f"[dim]Workspace: {registry.workspace_root}[/dim]")
    console.print(f"[dim]Public web research: {'enabled (confirmation required)' if settings.allow_external_network_tools else 'disabled'}[/dim]")

    try:
        client.check_connection()
    except OllamaError as exc:
        console.print(f"[bold red]Startup check failed:[/bold red] {exc}")
        console.print("[yellow]Start Ollama, pull the configured model, then relaunch Chinuuu.[/yellow]")
        return
    console.print("[green]Local model connection verified.[/green]")
    scheduler = ReminderScheduler(
        store,
        lambda message: console.print(f"\n[bold yellow]REMINDER[/bold yellow] {message}"),
        interval_seconds=settings.reminder_poll_seconds,
    )
    scheduler.start()
    atexit.register(scheduler.stop)

    while True:
        try:
            user_text = console.input("\n[bold green]Abhi[/bold green] [dim]›[/dim] ").strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\n[cyan]Chinuuu:[/cyan] Session ended. Goodbye, Abhi.")
            break
        if not user_text:
            continue

        lower = user_text.lower()
        if lower in {"/exit", "/quit"}:
            console.print("[cyan]Chinuuu:[/cyan] Understood, Abhi. Shutting down.")
            break
        if lower == "/help":
            print_help()
            continue
        if lower == "/time":
            console.print(Panel(local_time_report(), title="Local time", border_style="cyan"))
            continue
        if lower == "/system":
            console.print(Panel(system_report(), title="Read-only system report", border_style="cyan"))
            continue
        if lower == "/health":
            console.print(Panel(run_diagnostics(settings, store, len(registry.schemas())), title="Local health diagnostics", border_style="cyan"))
            continue
        if lower == "/context":
            summary = store.latest_conversation_summary()
            details = [f"Stored messages: {store.message_count()}", f"Approved memories: {len(store.list_memories())}",
                       f"Pending memory suggestions: {len(store.list_candidates('pending'))}"]
            if summary:
                details.append(f"Latest summary covers message ID: {summary['through_message_id']}")
                details.append(f"Summary created: {summary['created_at']}")
            else:
                details.append("Conversation summary: not generated yet (created after enough history accumulates)")
            console.print(Panel("\n".join(details), title="Conversation context", border_style="cyan"))
            continue
        if lower == "/processes":
            console.print(Panel(process_report(), title="Read-only process report", border_style="cyan"))
            continue
        if lower.startswith("/open "):
            alias = user_text[len("/open "):].strip()
            if is_administrator_launch_request(alias):
                base_alias = re.sub(r"\s+as\s+(?:an?\s+)?(?:administrator|admin)\s*[.!?]?\s*$", "", alias, flags=re.IGNORECASE).strip()
                console.print(f"[yellow]{administrator_launch_rejection(base_alias)}[/yellow]")
                continue
            try:
                result = registry.execute("open_application", {"alias": alias})
                console.print(assistant_response_panel(result))
            except ToolError as exc:
                console.print(f"[yellow]Application not launched:[/yellow] {exc}")
            continue
        if lower == "/apps":
            console.print(Panel(registry.app_launcher.list_apps(), title="Installed applications", border_style="cyan"))
            continue
        if lower == "/computer":
            console.print(Panel(registry.computer.locations(), title="Computer locations (read-only)", border_style="cyan"))
            continue
        if lower.startswith("/browse "):
            target = user_text[len("/browse "):].strip().strip('"')
            try:
                console.print(Panel(registry.computer.list_directory(target), title="Computer directory (read-only)", border_style="cyan"))
            except ToolError as exc:
                console.print(f"[yellow]Browse failed:[/yellow] {exc}")
            continue
        if lower == "/thispc":
            try:
                console.print(assistant_response_panel(registry.execute("open_this_pc", {})))
            except ToolError as exc:
                console.print(f"[yellow]Could not open This PC:[/yellow] {exc}")
            continue
        if lower == "/settings" or lower.startswith("/settings "):
            page = user_text[len("/settings"):].strip() or "home"
            try:
                console.print(assistant_response_panel(registry.execute("open_windows_setting", {"page": page})))
            except ToolError as exc:
                console.print(f"[yellow]Settings page unavailable:[/yellow] {exc}")
            continue
        if lower.startswith("/search "):
            query = user_text[len("/search "):].strip()
            try:
                if not settings.allow_external_network_tools:
                    result = registry.web_research.search(query)
                else:
                    result = registry.execute("search_public_web", {"query": query}, lambda name, args: _confirm_tool_call(name, args, registry))
                console.print(assistant_response_panel(result))
            except ToolError as exc:
                console.print(f"[yellow]Web research unavailable:[/yellow] {exc}")
            continue
        if lower.startswith("/check "):
            path = user_text[len("/check "):].strip()
            try:
                result = registry.execute("check_workspace_python", {"path": path})
                console.print(assistant_response_panel(result))
            except ToolError as exc:
                console.print(f"[yellow]Syntax check failed:[/yellow] {exc}")
            continue
        if lower == "/workspace":
            console.print(Panel(str(registry.workspace_root), title="Configured workspace", border_style="cyan"))
            continue
        if lower == "/gui":
            try:
                subprocess.Popen([sys.executable, "-m", "chinuuu.gui"], cwd=str(app_root), shell=False)
                console.print("[green]Chinuuu desktop interface launch requested.[/green]")
            except OSError as exc:
                console.print(f"[yellow]Could not launch desktop interface: {exc}[/yellow]")
            continue
        if lower == "/backup":
            try:
                console.print(assistant_response_panel(registry.execute("create_database_backup", {})))
            except ToolError as exc:
                console.print(f"[yellow]Backup failed:[/yellow] {exc}")
            continue
        if lower == "/reminders":
            try:
                console.print(assistant_response_panel(registry.execute("list_local_reminders", {})))
            except ToolError as exc:
                console.print(f"[yellow]Reminder list unavailable:[/yellow] {exc}")
            continue
        if lower == "/calendar":
            try:
                console.print(assistant_response_panel(registry.execute("list_calendar_events", {})))
            except ToolError as exc:
                console.print(f"[yellow]Calendar list unavailable:[/yellow] {exc}")
            continue
        if lower == "/contacts":
            try:
                console.print(assistant_response_panel(registry.execute("list_local_contacts", {})))
            except ToolError as exc:
                console.print(f"[yellow]Contacts unavailable:[/yellow] {exc}")
            continue
        if lower == "/runs":
            console.print(assistant_response_panel(registry.execute("list_agent_runs", {})))
            continue
        if lower == "/audit":
            console.print(assistant_response_panel(registry.execute("list_tool_audit", {})))
            continue
        if lower.startswith("/index "):
            path = user_text[len("/index "):].strip().strip('"')
            try:
                console.print(assistant_response_panel(registry.execute("index_workspace_document", {"path": path})))
            except ToolError as exc:
                console.print(f"[yellow]Indexing failed:[/yellow] {exc}")
            continue
        if lower.startswith("/docs "):
            query = user_text[len("/docs "):].strip()
            try:
                console.print(assistant_response_panel(registry.execute("search_local_documents", {"query": query})))
            except ToolError as exc:
                console.print(f"[yellow]Document search failed:[/yellow] {exc}")
            continue
        if lower == "/indexed":
            try:
                console.print(assistant_response_panel(registry.execute("list_indexed_documents", {})))
            except ToolError as exc:
                console.print(f"[yellow]Document index unavailable:[/yellow] {exc}")
            continue
        if lower == "/voice":
            model_path = Path(settings.voice_model_path)
            if not model_path.is_absolute():
                model_path = app_root / model_path
            try:
                with console.status("[bold cyan]Listening for six seconds; audio stays local...[/bold cyan]", spinner="dots"):
                    transcript = OfflineVoiceEngine(model_path).record_and_transcribe(seconds=6)
                console.print(f"[cyan]Voice transcription:[/cyan] {transcript}")
                response = assistant.respond(
                    transcript, registry,
                    confirm=lambda name, args: _confirm_tool_call(name, args, registry),
                )
                console.print(assistant_response_panel(response))
                if settings.voice_response_enabled:
                    OfflineVoiceEngine.speak(response)
            except ToolError as exc:
                console.print(f"[yellow]Voice unavailable:[/yellow] {exc}")
            except OllamaError as exc:
                console.print(f"[bold red]Local AI error:[/bold red] {exc}")
            continue
        if lower == "/tools":
            table = Table(title="Available local tools")
            table.add_column("Tool", style="cyan")
            table.add_column("Permission")
            table.add_column("Purpose")
            for name, permission, description in registry.descriptions():
                table.add_row(name, permission, description)
            console.print(table)
            continue
        if lower.startswith("/plan "):
            task = user_text[len("/plan "):].strip()
            if not task:
                console.print("[yellow]Usage: /plan <task>[/yellow]")
                continue
            try:
                with console.status("[bold cyan]Chinuuu is analysing the task...[/bold cyan]", spinner="dots"):
                    plan = assistant.plan_task(task, str(registry.workspace_root))
                console.print(assistant_response_panel(plan))
            except OllamaError as exc:
                console.print(f"[bold red]Planning error:[/bold red] {exc}")
            continue
        if lower == "/clear":
            if Confirm.ask("Clear locally saved conversation history? Preferences remain.", default=False):
                store.clear_messages()
                console.print("[green]Conversation history cleared.[/green]")
            continue
        if _handle_memory_command(user_text, store):
            continue
        if _handle_todo_command(user_text, store):
            continue

        try:
            response_kwargs = {
                "registry": registry,
                "confirm": lambda name, args: _confirm_tool_call(name, args, registry),
            }
            if _extract_python_run_path(user_text) is not None:
                # A running script may use input() and write directly to the
                # terminal. Do not keep Rich's live status spinner active.
                answer = assistant.respond(user_text, **response_kwargs)
            else:
                with console.status("[bold cyan]Chinuuu is thinking locally...[/bold cyan]", spinner="dots"):
                    answer = assistant.respond(user_text, **response_kwargs)
            console.print(assistant_response_panel(answer))
        except KeyboardInterrupt:
            console.print(
                "\n[yellow]Execution interrupted. A tool operation may have completed before the interruption. "
                "Check the workspace before retrying to avoid duplicate files or changes.[/yellow]"
            )
            continue
        except OllamaError as exc:
            console.print(f"[bold red]Local AI error:[/bold red] {exc}")
            continue

        for candidate_id in _collect_learning_suggestions(user_text, store, settings):
            _show_candidate(store, candidate_id)

    scheduler.stop()


if __name__ == "__main__":
    main()
