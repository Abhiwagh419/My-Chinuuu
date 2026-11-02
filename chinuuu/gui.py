from __future__ import annotations

import queue
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, scrolledtext, ttk

from .assistant import Assistant
from .config import load_settings, project_root, resolve_workspace_path
from .database import LocalStore
from .ollama_client import OllamaClient, OllamaError
from .productivity import ReminderScheduler
from .tools import ToolError, ToolRegistry
from .voice import OfflineVoiceEngine
from .system_info import local_time_report, process_report, system_report
from .control_center import ControlCenterWindow
from .diagnostics import run_diagnostics


class ChinuuuDesktopApp:
    """Tkinter desktop shell around the same Assistant and ToolRegistry as the CLI."""

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("Chinuuu — Personal Local Intelligence")
        self.root.geometry("1120x760")
        self.root.minsize(840, 580)
        self.root.configure(bg="#10151e")
        self.events: queue.Queue = queue.Queue()
        self.pending_approvals: dict[int, dict] = {}
        self.approval_id = 0
        self.busy = False
        self._build_ui()

        app_root = project_root()
        self.settings = load_settings(app_root / "config.toml")
        self.speak_var.set(self.settings.voice_response_enabled)
        self.speak_responses = self.settings.voice_response_enabled
        self.store = LocalStore(app_root / "data" / "chinuuu.sqlite3")
        self.client = OllamaClient(
            self.settings.ollama_url, self.settings.model,
            self.settings.context_window, self.settings.temperature,
        )
        workspace = resolve_workspace_path(self.settings.workspace_root, app_root)
        self.registry = ToolRegistry(
            workspace, app_allowlist=self.settings.app_allowlist,
            allow_external_network=self.settings.allow_external_network_tools,
            task_store=self.store,
            embedding_model=self.settings.embedding_model,
            ollama_url=self.settings.ollama_url,
        )
        self.assistant = Assistant(self.settings, self.store, self.client)
        self.scheduler = ReminderScheduler(
            self.store, lambda message: self.events.put(("notification", message)),
            interval_seconds=self.settings.reminder_poll_seconds,
        )
        self.scheduler.start()
        self.wakeword_listener = None
        if self.settings.wakeword_enabled:
            try:
                from .wakeword import WakeWordListener
                wakeword_path = Path(self.settings.wakeword_model_path)
                if not wakeword_path.is_absolute():
                    wakeword_path = app_root / wakeword_path
                self.wakeword_listener = WakeWordListener(wakeword_path)
                self.wakeword_listener.start(lambda label, score: self.events.put(("wakeword", (label, score))))
            except ToolError as exc:
                # Do not keep a failed listener object: otherwise the GUI retries
                # an unavailable model or microphone after every assistant reply.
                self.wakeword_listener = None
                self.events.put(("notification", f"Wake-word listener not started: {exc}"))
        self._append("SYSTEM", f"Application root: {app_root}\nWorkspace: {workspace}\nModel: {self.settings.model}")
        try:
            self.client.check_connection()
        except OllamaError as exc:
            self._append("SYSTEM ERROR", str(exc))
            self._set_status("Ollama unavailable — start the local Ollama service")
        else:
            self._set_status("Local model connected · data stored on this device")
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.after(100, self._drain_events)

    def _build_ui(self) -> None:
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("TButton", padding=8, font=("Segoe UI", 10))
        style.configure("TLabel", background="#10151e", foreground="#e7edf5", font=("Segoe UI", 10))
        style.configure("Title.TLabel", background="#10151e", foreground="#62d4ff", font=("Segoe UI Semibold", 18))
        style.configure("Status.TLabel", background="#151d29", foreground="#aab7c8", padding=8)
        header = tk.Frame(self.root, bg="#10151e", padx=18, pady=14)
        header.pack(fill="x")
        ttk.Label(header, text="CHINUUU", style="Title.TLabel").pack(side="left")
        ttk.Label(header, text="PERSONAL LOCAL INTELLIGENCE", foreground="#92a3b7").pack(side="left", padx=16)
        ttk.Button(header, text="Control center", command=self._open_control_center).pack(side="right", padx=4)
        ttk.Button(header, text="Backup database", command=self._backup).pack(side="right", padx=4)
        ttk.Button(header, text="Health check", command=lambda: self._submit_text("/health")).pack(side="right", padx=4)
        ttk.Button(header, text="System status", command=lambda: self._submit_text("/system")).pack(side="right", padx=4)

        body = tk.Frame(self.root, bg="#10151e", padx=16, pady=4)
        body.pack(fill="both", expand=True)
        self.transcript = scrolledtext.ScrolledText(
            body, wrap="word", bg="#151d29", fg="#e7edf5", insertbackground="white",
            relief="flat", padx=16, pady=14, font=("Consolas", 10), state="disabled",
        )
        self.transcript.pack(fill="both", expand=True)
        self.transcript.tag_configure("user", foreground="#75d6a6", font=("Segoe UI Semibold", 10))
        self.transcript.tag_configure("assistant", foreground="#68cfff", font=("Segoe UI Semibold", 10))
        self.transcript.tag_configure("system", foreground="#e6bf78", font=("Segoe UI Semibold", 9))
        self.transcript.tag_configure("body", foreground="#e7edf5")

        # A compact, dependency-free animated running cat appears while the
        # assistant is working. It is separate from the transcript so it never
        # pollutes conversation history and can be hidden cleanly on completion.
        self.thinking_frame = tk.Frame(body, bg="#151d29", padx=8, pady=2)
        self.thinking_canvas = tk.Canvas(
            self.thinking_frame, width=120, height=30,
            bg="#151d29", highlightthickness=0, bd=0,
        )
        self.thinking_canvas.pack(side="left")
        self.thinking_label = tk.Label(
            self.thinking_frame, text="Chinuuu is thinking",
            bg="#151d29", fg="#9edfff", anchor="w",
            font=("Segoe UI Semibold", 10), padx=8,
        )
        self.thinking_label.pack(side="left", fill="x", expand=True)
        self._thinking_visible = False
        self._thinking_frame_index = 0
        self._thinking_after_id = None
        self._thinking_message = "Chinuuu is thinking"

        compose = tk.Frame(self.root, bg="#10151e", padx=16, pady=12)
        compose.pack(fill="x")
        input_column = tk.Frame(compose, bg="#10151e")
        input_column.pack(side="left", fill="both", expand=True)
        self.input = tk.Text(input_column, height=3, wrap="word", bg="#1b2634", fg="#f3f7fb", insertbackground="white", relief="flat", padx=12, pady=10, font=("Segoe UI", 11))
        self.input.pack(fill="both", expand=True)
        tk.Label(
            input_column,
            text="Enter to send  ·  Shift+Enter for a new line",
            bg="#10151e", fg="#8292a6", anchor="w",
            font=("Segoe UI", 8), padx=2, pady=4,
        ).pack(fill="x")
        controls = tk.Frame(compose, bg="#10151e")
        controls.pack(side="left", padx=(10, 0), fill="y")
        self.send_button = ttk.Button(controls, text="Send", command=self._send)
        self.send_button.pack(fill="x", pady=(0, 6))
        ttk.Button(controls, text="Push to talk (6s)", command=self._voice).pack(fill="x")
        self.speak_var = tk.BooleanVar(value=False)
        self.speak_responses = False
        ttk.Checkbutton(controls, text="Speak replies", variable=self.speak_var, command=self._toggle_speech).pack(anchor="w", pady=6)
        self.status = ttk.Label(self.root, text="Initializing…", style="Status.TLabel", anchor="w")
        self.status.pack(fill="x", side="bottom")
        # Enter sends; Shift+Enter inserts a newline. Control+Enter remains
        # supported as an explicit send shortcut for users who prefer it.
        self.input.bind("<Return>", self._on_enter)
        self.input.bind("<KP_Enter>", self._on_enter)
        self.input.bind("<Control-Return>", self._on_control_enter)
        self.input.bind("<Control-KP_Enter>", self._on_control_enter)

    def _set_status(self, value: str) -> None:
        self.status.configure(text=value)

    def _show_thinking(self, message: str = "Chinuuu is thinking") -> None:
        """Show the compact cat and keep exactly one animation loop scheduled."""
        self._thinking_message = message
        if not self._thinking_visible:
            self._thinking_visible = True
            self.thinking_frame.pack(fill="x", pady=(4, 0))
            self._thinking_frame_index = 0
        # Voice capture changes its label to "thinking" after transcription.
        # Do not start a second timer when the indicator is already animating.
        if self._thinking_after_id is None:
            self._animate_thinking()

    def _hide_thinking(self) -> None:
        """Stop scheduled animation and hide the indicator."""
        self._thinking_visible = False
        if self._thinking_after_id is not None:
            try:
                self.root.after_cancel(self._thinking_after_id)
            except tk.TclError:
                pass
            self._thinking_after_id = None
        self.thinking_frame.pack_forget()

    def _draw_running_cat(self, x: int, phase: int) -> None:
        """Draw a tiny, rounded chibi cat with a readable four-step run cycle."""
        canvas = self.thinking_canvas
        canvas.delete("all")
        track = "#294456"
        fur = "#a9e7ff"
        fur_light = "#d8f5ff"
        ear_inner = "#ff9fbd"
        ink = "#26384a"
        cheek = "#ff9fbd"

        # Fine running track; the character stays deliberately small.
        canvas.create_line(3, 26, 117, 26, fill=track, width=1)

        stride = phase % 4
        bounce = (0, -1, 0, -1)[stride]
        leg_sets = (
            ((-2, 3), (2, -1), (-1, 2), (1, -2)),
            ((-1, 1), (1, -2), (2, -1), (-2, 2)),
            ((2, -1), (-2, 3), (1, -2), (-1, 2)),
            ((1, -2), (-1, 2), (-2, 3), (2, -1)),
        )
        legs = leg_sets[stride]

        # Tail with a rounded curl.
        canvas.create_line(
            x + 7, 17 + bounce, x + 3, 13 + bounce,
            x, 14 + bounce, x - 1, 11 + bounce,
            fill=fur, width=2, capstyle="round", joinstyle="round", smooth=True,
        )

        # Four tiny paws, animated in alternating pairs.
        leg_origins = ((x + 10, 20), (x + 15, 20), (x + 20, 19), (x + 23, 18))
        for (lx, ly), (dx, dy) in zip(leg_origins, legs):
            canvas.create_line(
                lx, ly + bounce, lx + dx, 25 + dy,
                fill=fur, width=2, capstyle="round",
            )

        # Soft rounded body and oversized kitten head.
        canvas.create_oval(
            x + 5, 12 + bounce, x + 23, 22 + bounce,
            fill=fur, outline=fur,
        )
        canvas.create_oval(
            x + 17, 7 + bounce, x + 30, 20 + bounce,
            fill=fur_light, outline=fur_light,
        )

        # Pointed ears with pink inner details.
        canvas.create_polygon(
            x + 18, 11 + bounce, x + 19, 4 + bounce, x + 24, 9 + bounce,
            fill=fur_light, outline=fur_light,
        )
        canvas.create_polygon(
            x + 24, 9 + bounce, x + 29, 4 + bounce, x + 30, 13 + bounce,
            fill=fur_light, outline=fur_light,
        )
        canvas.create_polygon(
            x + 20, 9 + bounce, x + 20, 6 + bounce, x + 22, 9 + bounce,
            fill=ear_inner, outline=ear_inner,
        )
        canvas.create_polygon(
            x + 26, 9 + bounce, x + 28, 6 + bounce, x + 28, 10 + bounce,
            fill=ear_inner, outline=ear_inner,
        )

        # Eyes, nose and rosy cheeks make the face read as a kitten, not a blob.
        canvas.create_oval(
            x + 21, 13 + bounce, x + 23, 16 + bounce,
            fill=ink, outline=ink,
        )
        canvas.create_oval(
            x + 27, 13 + bounce, x + 29, 16 + bounce,
            fill=ink, outline=ink,
        )
        canvas.create_oval(
            x + 24, 16 + bounce, x + 26, 18 + bounce,
            fill="#ff8eaa", outline="#ff8eaa",
        )
        canvas.create_oval(
            x + 18, 17 + bounce, x + 21, 19 + bounce,
            fill=cheek, outline=cheek,
        )
        canvas.create_oval(
            x + 29, 17 + bounce, x + 31, 19 + bounce,
            fill=cheek, outline=cheek,
        )

    def _animate_thinking(self) -> None:
        # The callback currently being executed is no longer pending.
        self._thinking_after_id = None
        if not self._thinking_visible:
            return
        index = self._thinking_frame_index
        x = 10 + (index * 4) % 105
        self._draw_running_cat(x, index)
        dots = "." * (index % 4)
        self.thinking_label.configure(text=f"{self._thinking_message}{dots}")
        self._thinking_frame_index += 1
        self._thinking_after_id = self.root.after(85, self._animate_thinking)

    def _toggle_speech(self) -> None:
        self.speak_responses = bool(self.speak_var.get())

    def _append(self, speaker: str, text: str) -> None:
        self.transcript.configure(state="normal")
        tag = "user" if speaker == "ABHI" else "assistant" if speaker == "CHINUUU" else "system"
        self.transcript.insert("end", f"\n{speaker}\n", tag)
        self.transcript.insert("end", text.strip() + "\n", "body")
        self.transcript.see("end")
        self.transcript.configure(state="disabled")

    def _on_enter(self, event: tk.Event) -> str | None:
        """Send on Enter; preserve normal newline insertion on Shift+Enter."""
        if event.state & 0x0001:  # Tk Shift modifier bit
            return None
        self._send()
        return "break"

    def _on_control_enter(self, _event: tk.Event) -> str:
        self._send()
        return "break"

    def _send(self) -> None:
        # Do not discard text if a previous request is still running.
        if self.busy:
            return
        text = self.input.get("1.0", "end-1c").strip()
        if not text:
            return
        self._submit_text(text)
        # Clear only after submission has actually been accepted.
        if self.busy:
            self.input.delete("1.0", "end")

    def _submit_text(self, text: str) -> None:
        if not text or self.busy:
            return
        self.busy = True
        self.send_button.configure(state="disabled")
        self._append("ABHI", text)
        self._set_status("Chinuuu is processing locally…")
        self._show_thinking("Chinuuu is thinking")
        threading.Thread(target=self._respond_worker, args=(text,), daemon=True).start()

    def _confirm_from_worker(self, tool_name: str, arguments: dict) -> bool:
        self.approval_id += 1
        approval_id = self.approval_id
        event = threading.Event()
        state = {"approved": False}
        self.events.put(("approval", (approval_id, tool_name, arguments, event, state)))
        if not event.wait(timeout=180):
            return False
        return bool(state["approved"])

    def _respond_worker(self, text: str) -> None:
        try:
            command = text.strip().casefold()
            # Slash commands are application commands, not natural-language
            # prompts. Handle the read-only diagnostics directly in the GUI
            # just as the CLI does, instead of asking the LLM to interpret them.
            if command == "/system":
                response = system_report()
            elif command == "/health":
                response = run_diagnostics(self.settings, self.store, len(self.registry.schemas()))
            elif command == "/context":
                summary = self.store.latest_conversation_summary()
                details = [f"Stored messages: {self.store.message_count()}",
                           f"Approved memories: {len(self.store.list_memories())}",
                           f"Pending suggestions: {len(self.store.list_candidates('pending'))}"]
                if summary:
                    details.append(f"Latest summary covers message ID: {summary['through_message_id']}")
                else:
                    details.append("No conversation summary generated yet.")
                response = "\n".join(details)
            elif command == "/time":
                response = local_time_report()
            elif command == "/processes":
                response = process_report()
            else:
                response = self.assistant.respond(text, self.registry, self._confirm_from_worker)
            if self.speak_responses:
                try:
                    OfflineVoiceEngine.speak(response)
                except ToolError as exc:
                    self.events.put(("notification", f"Speech output unavailable: {exc}"))
            self.events.put(("response", response))
        except Exception as exc:
            self.events.put(("error", f"{type(exc).__name__}: {exc}"))

    def _pause_wakeword(self) -> None:
        if self.wakeword_listener is not None and self.wakeword_listener.is_running():
            self.wakeword_listener.stop()

    def _restart_wakeword(self) -> None:
        if self.settings.wakeword_enabled and self.wakeword_listener is not None and not self.wakeword_listener.is_running():
            try:
                self.wakeword_listener.start(
                    lambda label, score: self.events.put(("wakeword", (label, score)))
                )
            except ToolError as exc:
                self.wakeword_listener = None
                self.events.put(("notification", f"Wake-word listener could not restart: {exc}"))

    def _voice(self) -> None:
        if self.busy:
            return
        self._pause_wakeword()
        self.busy = True
        self.send_button.configure(state="disabled")
        self._set_status("Listening for up to six seconds. Audio is processed locally…")
        self._show_thinking("Listening locally")
        def worker():
            try:
                model_path = Path(self.settings.voice_model_path)
                if not model_path.is_absolute():
                    model_path = project_root() / model_path
                transcript = OfflineVoiceEngine(model_path).record_and_transcribe(seconds=6)
                self.events.put(("voice_text", transcript))
                response = self.assistant.respond(transcript, self.registry, self._confirm_from_worker)
                if self.speak_responses:
                    OfflineVoiceEngine.speak(response)
                self.events.put(("response", response))
            except Exception as exc:
                self.events.put(("error", f"Voice request failed: {exc}"))
        threading.Thread(target=worker, daemon=True).start()

    def _open_control_center(self) -> None:
        try:
            ControlCenterWindow(self.root, self.store)
        except Exception as exc:
            messagebox.showerror("Control Center unavailable", f"{type(exc).__name__}: {exc}", parent=self.root)

    def _backup(self) -> None:
        try:
            result = self.registry.execute("create_database_backup", {})
        except ToolError as exc:
            messagebox.showerror("Backup failed", str(exc), parent=self.root)
        else:
            self._append("SYSTEM", result)
            messagebox.showinfo("Backup complete", result, parent=self.root)

    def _drain_events(self) -> None:
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "response":
                    self._hide_thinking()
                    self._append("CHINUUU", payload)
                    self.busy = False
                    self.send_button.configure(state="normal")
                    self._set_status("Ready · local tools and memory available")
                    self._restart_wakeword()
                elif kind == "voice_text":
                    self._append("ABHI (VOICE TRANSCRIPTION)", payload)
                    self._show_thinking("Chinuuu is thinking")
                elif kind == "error":
                    self._append("SYSTEM ERROR", payload)
                    self._hide_thinking()
                    self.busy = False
                    self.send_button.configure(state="normal")
                    self._set_status("An error occurred · see transcript")
                    self._restart_wakeword()
                elif kind == "notification":
                    self._append("REMINDER / NOTIFICATION", payload)
                elif kind == "wakeword":
                    label, confidence = payload
                    self._append("WAKE WORD", f"Detected '{label}' ({confidence:.0%}). Starting a six-second voice capture.")
                    if not self.busy:
                        self._pause_wakeword()
                        self._voice()
                elif kind == "approval":
                    approval_id, tool_name, arguments, event, state = payload
                    detail = f"Tool: {tool_name}\n\nArguments:\n{arguments}\n\nApprove this operation?"
                    state["approved"] = messagebox.askyesno("Chinuuu approval required", detail, parent=self.root)
                    event.set()
        except queue.Empty:
            pass
        self.root.after(100, self._drain_events)

    def close(self) -> None:
        try:
            self._hide_thinking()
            if self.wakeword_listener is not None:
                self.wakeword_listener.stop()
            self.scheduler.stop()
        finally:
            self.root.destroy()


def main() -> None:
    root = tk.Tk()
    app = ChinuuuDesktopApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
