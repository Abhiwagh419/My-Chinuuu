# Chinuuu v4.1.0

**A local-first personal AI assistant for Windows 11.** Chinuuu combines a local Ollama model, SQLite-backed conversation and memory, a bounded tool-execution loop, Windows controls, desktop UI Automation, local document retrieval, offline voice adapters, reminders, local contacts/calendar, and a Tkinter desktop interface.

> **Release status:** v4.1.0 refines the running-cat processing indicator into a small, compact animation and prevents duplicate animation timers during voice-to-thinking transitions. It builds on v4.0.0 durable conversation continuity, relevance-ranked approved memory, stale-run recovery and local health diagnostics. Automated tests and patch replay are validated in the build environment. Hardware- and account-dependent acceptance tests must still be run on the target Windows PC. Do not interpret a passing mocked unit test as proof that a particular Windows driver or app workflow works on every device.

## 1. Design principles

- **Local by default:** Ollama, conversation history, approved memories, contacts, reminders, calendar events, document index and audit data stay on the PC.
- **Explicit network opt-in:** public search and page extraction are disabled unless `security.allow_external_network_tools = true`. Individual search/fetch actions still require confirmation. Only the explicit query or URL is transmitted; conversation history and local documents are not uploaded.
- **Bounded tool use:** agent tool rounds and total tool calls are configurable. No arbitrary shell or PowerShell command execution is exposed.
- **Least privilege:** workspace file access is confined to one configured workspace. UI automation uses semantic controls, not guessed coordinates. Symlinks and junctions are rejected by workspace/document/artifact paths.
- **Honest verification:** a tool reports only what its API can verify. Media keys report that a key was sent, not that a media player changed state. Brightness reports the WMI control/read-back result, not a photometric measurement of emitted light.
- **Human approval:** deleting files, replacing files, running Python, clicking desktop controls, entering desktop text, capturing screenshots, changing Wi-Fi/Bluetooth radio state, fetching pages and power actions require approval. Email and WhatsApp are drafts only; Chinuuu does not send them.

## 2. Requirements

- Windows 10/11 for Windows-specific controls; Windows 11 is the primary target.
- Python 3.12 or newer. Python 3.14 is supported by the project code and selected current dependencies, but confirm all optional Windows drivers/dependencies on the target machine.
- [Ollama](https://ollama.com/) running locally, with the configured model available. Default: `qwen3:4b-instruct`.
- 8 GB RAM is a practical minimum for a small local model; actual model memory requirements depend on quantization, context size and other applications. The application does not require a dedicated GPU.

## 3. Install from source

Open PowerShell in the extracted project directory:

```powershell
py -3.14 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip setuptools wheel
python -m pip install -e ".[dev,windows,documents,voice,wakeword,installer]"
Copy-Item config.example.toml config.toml
```

If `config.toml` already exists from an earlier version, **do not overwrite it**. The v4.0.0 database migration is additive and preserves existing messages, memories, tasks and configuration. Keep your existing `data`, `workspace`, `.venv` and `config.toml` directories/files during an update.

Pull the default local model if it is not already installed:

```powershell
ollama pull qwen3:4b-instruct
# Optional local semantic search model:
ollama pull nomic-embed-text
ollama list
```

Review `config.toml`. Set the workspace path to the directory you want Chinuuu to manage. For example:

```toml
[assistant]
name = "Chinuuu"
operator_name = "Abhi"
model = "qwen3:4b-instruct"
ollama_url = "http://127.0.0.1:11434"
context_messages = 12
context_summary_interval = 24
context_window = 8192
temperature = 0.5

[security]
allow_arbitrary_shell = false
allow_external_network_tools = false

[tools]
workspace_root = "D:/Projects/Chinuuu/workspace"
max_tool_rounds = 4
max_tool_calls = 8

[voice]
model_path = "models/faster-whisper-small"
speak_responses = false
wakeword_enabled = false
wakeword_model_path = "models/wakeword.onnx"

[productivity]
reminder_poll_seconds = 10

[knowledge]
embedding_model = "" # Optional: set to "nomic-embed-text" after running ollama pull nomic-embed-text
```

Start the local model service, then launch Chinuuu:

```powershell
python main.py
```

Or use the provided launchers:

```powershell
.\Launch_Chinuuu.ps1
.\Launch_Chinuuu_GUI.ps1
```

## 4. Included capabilities

### v4.1.0 — Compact running-cat indicator and GUI lifecycle hardening

- Reduced the running cat from a large 270×48 canvas with a roughly 64×35 drawing to a compact 150×30 canvas with a roughly 31×20 drawing.
- Kept the cyan cat, running-leg cycle, motion marks and processing label while reducing the indicator's vertical footprint.
- Fixed duplicate animation scheduling when voice capture changes from "Listening locally" to "Chinuuu is thinking"; the transition now updates the label without starting another timer loop.
- Added focused tests for compact indicator geometry, single-timer scheduling, timer rescheduling and clean cancellation.
- Added a source-based audit report and a final Windows acceptance checklist.

### v4.0.1 — Animated GUI processing feedback

The desktop GUI displays an animated running cat and a live processing label while text or voice requests are active. The indicator is removed when a response or error arrives and is cancelled on shutdown.

### v4.0.0 — Durable intelligence and operational recovery

- Conversation continuity: after enough history accumulates, Chinuuu asks the configured local Ollama model to create a concise factual summary of older messages. The summary is stored locally and supplied as explicitly untrusted historical context on later turns.
- Source preservation: original messages are never deleted or rewritten by summarization. If local summarization fails, the user's request and original conversation remain intact.
- Relevance-ranked memory: approved memories are ranked using deterministic lexical overlap; only a bounded set is supplied to the model. Inferred memory candidates still require explicit approval.
- Interrupted-run recovery: runs left in `running` state for more than ten minutes are marked `interrupted` on assistant initialization. Chinuuu never automatically replays interrupted actions. Recent runs are not changed, supporting concurrent CLI/GUI sessions.
- `/health`: read-only checks for SQLite integrity, workspace availability, the configured local Ollama model and tool registry.
- `/context`: reports message, memory and conversation-summary coverage.
- GUI Health Check button and direct `/health` and `/context` command routing.
- Additive SQLite migration creates the conversation-summary table while preserving existing chat, memories, tasks, documents, contacts, reminders and audit history.

Conversation summaries are generated only by the configured local model endpoint. The feature does not upload conversation history to a cloud service. A summary is a convenience layer, not a replacement for the original transcript or a guaranteed perfect record.


### Milestone 1 — Reliability and verification foundations

- Additive SQLite migrations; legacy chat, memory and task data are retained.
- Durable agent runs, tool steps and tool audit records.
- Redaction of common contact identifiers and message fields from tool-operation logs.
- Database backup uses SQLite's online backup API and runs `PRAGMA integrity_check`.
- Explicit failure paths, output verification, path traversal checks and symlink/junction rejection.
- Automated regression tests and a Windows/Linux CI workflow.

**Still required on a physical Windows PC:** test each hardware, UI Automation and optional-driver feature against the actual device and installed apps.

### Milestone 2 — Windows controls

- Read/set default system volume and mute state through Windows Core Audio (`pycaw`).
- Play/pause, next and previous via standard Windows media keys. This dispatches a key; playback state is controlled by the active app.
- Read Wi-Fi radio/interface state and request Wi-Fi radio on/off via WinRT, with confirmation.
- Read Bluetooth radio state and request on/off via WinRT, with confirmation.
- Open Wi-Fi/Bluetooth Settings pages.
- Existing WMI brightness and UI Automation Night Light controls remain available.
- Lock and sleep operations are confirmation-gated.

Bluetooth pairing, joining a Wi-Fi network, network credentials and OEM-specific controls remain in Windows Settings. Radio APIs can be blocked by device policy, airplane mode, drivers or hardware switches.

### Milestone 3 — Desktop automation

- List visible titled windows and inspect semantic UI Automation controls.
- Click one uniquely matched visible/enabled control after approval.
- Enter text into one uniquely matched, non-sensitive Edit control after approval. It does **not** submit forms.
- Password, PIN, OTP and security-code fields are blocked/redacted.
- Capture the primary display into `workspace/screenshots` after explicit approval.
- No coordinate guessing, unattended form submission, password entry or automatic purchase/message send.

UI Automation depends on the target app exposing accessible controls. Apps using custom-rendered controls may not expose a usable UIA tree.

### Milestone 4 — Research and local knowledge

- Public web search through DuckDuckGo HTML when explicitly enabled and individually confirmed.
- Bounded public HTML extraction for an explicitly supplied URL, with host/redirect validation and confirmation.
- Local document indexing: TXT, Markdown, Python, JSON, CSV, HTML, PDF and DOCX.
- SQLite FTS5 full-text retrieval with a LIKE fallback when SQLite was built without FTS5.
- Optional local semantic retrieval using Ollama embeddings, persisted vectors and cosine-similarity ranking. The embedding request is restricted to a loopback Ollama endpoint and requests CPU inference to preserve GPU VRAM for the chat model.
- Search results include source filename, path and page number when available.

To enable semantic retrieval, pull an embedding model locally and set `[knowledge] embedding_model = "nomic-embed-text"` in `config.toml`, then re-index your documents. Chinuuu never downloads models automatically. With no embedding model configured or available, it clearly falls back to lexical SQLite full-text search. Scanned PDFs without an embedded text layer require OCR outside this release. Treat web pages and indexed documents as untrusted source data and verify important claims.

### Milestone 5 — Offline voice

- Explicit six-second push-to-talk in CLI/GUI; recording duration is bounded to 2–20 seconds in the engine.
- Local faster-whisper speech-to-text using CPU/int8.
- Local `pyttsx3` text-to-speech.
- Optional ONNX wake-word listener, disabled by default and started only when explicitly enabled in configuration.

Voice is optional. Install `.[voice]`; provide a compatible faster-whisper model directory at `voice.model_path`. Wake-word mode additionally needs `.[wakeword]` and a local ONNX model at `voice.wakeword_model_path`. No speech or wake-word models are bundled or silently downloaded. Use a model whose licence permits your intended use. Always-listening microphone capture is opt-in and visibly configured; it is not active by default.

### Milestone 6 — Bounded agent execution

- Existing Ollama tool-call loop is bounded by `max_tool_rounds` and `max_tool_calls`.
- Runs and step outcomes are persisted in SQLite.
- Tool failures and approval denials stop the current operation; the model cannot paraphrase a denied operation into a success claim.
- Mutating file actions are independently verified.
- Sensitive operation details are redacted from durable operation logs where supported.

This is **supervised agent execution**, not unrestricted autonomy. It does not provide arbitrary shell, administrator access, unattended external communications or a distributed/background worker. A reviewed Python script still runs under the current Windows user's permissions; the runner is not an OS sandbox and a script may access anything that account can access. Only run code you trust.

### Milestone 7 — Local productivity

- Local reminders with notifications while Chinuuu is running.
- Local contact book with E.164 phone validation and exact-name matching; ambiguous names are rejected.
- Local calendar events stored in SQLite and exported as standard `.ics` files for manual import into another calendar app.
- `mailto:` email drafts and WhatsApp click-to-chat drafts. The user reviews and sends manually.
- Task list, audit history and local database backups.

Reminders are not delivered while the app is closed. Calendar/contact data is not synced to Google, Outlook or other cloud accounts. Email and WhatsApp are never sent automatically.

### Milestone 8 — Desktop app and deployment foundation

- Tkinter desktop chat with a responsive worker-thread model, local status, approval dialogs, push-to-talk and optional spoken replies. Enter or keypad Enter sends a message, Shift+Enter inserts a newline, and Ctrl+Enter also sends. The composer displays this shortcut hint.
- PowerShell launchers for CLI and GUI.
- Windows PyInstaller build script for console and windowed executables.
- CI workflow for Python 3.12–3.14 on Windows and Ubuntu.

The build script creates executables on a Windows host with the optional extras installed. **This source release does not include a prebuilt or signed installer.** A Windows packaging run and end-user acceptance test are still required before distributing an executable.

## 5. CLI command reference

Type `/help` inside Chinuuu. Common commands:

| Command | Purpose |
|---|---|
| `/system` | Read-only CPU, RAM, disk and battery report |
| `/processes` | Read-only process inventory |
| `/apps` | Discover installed apps |
| `/open <app>` | Launch a supported installed application |
| `/settings [page]` | Open a Windows Settings page |
| `/search <query>` | Public web search (must be enabled and approved) |
| `/todo add <task>` / `/todo list` | Local task list |
| `/index <path>` | Index one document inside the workspace |
| `/docs <query>` | Search the local document index |
| `/indexed` | List indexed documents |
| `/reminders` | List local reminders |
| `/calendar` | List local calendar events |
| `/contacts` | List local contacts |
| `/backup` | Create and verify a SQLite backup |
| `/runs` / `/audit` | Inspect recent local agent activity |
| `/voice` | Explicit push-to-talk recording |
| `/gui` | Open the desktop interface |
| `/plan <task>` | Analyse and clarify a task; does not execute it |
| `/memory list` | Show approved long-term memories |
| `/exit` | Close Chinuuu |

Natural-language commands include:

```text
set brightness to 65%
set volume to 40%
mute audio
play
turn Wi-Fi off
turn Bluetooth on
capture a screenshot
list open application windows
inspect controls in Notepad
index document project-notes.md
search my documents for database normalization
remind me to review the project at 2026-11-02 18:30
create calendar event Project Review at 2026-11-04 15:00
save contact Soham Gavande with phone +91 98765 43210
create database backup
```

Use explicit dates/times in the local system timezone. Chinuuu does not infer a missing date or time for reminders/calendar events.

## 6. GUI and Windows executable build

Install the installer extra:

```powershell
python -m pip install -e ".[installer,windows,documents,voice,wakeword]"
```

Run the build from PowerShell on Windows:

```powershell
.\build_windows.ps1
```

The script checks the test suite before invoking PyInstaller. It builds a console executable and a windowed GUI executable under `dist\Chinuuu` and `dist\ChinuuuDesktop`. Each output folder receives `config.example.toml`. For a packaged build, create `%LOCALAPPDATA%\Chinuuu` and copy either example file there as `config.toml` before customizing it. Both executable bundles use this shared profile, so they share one configuration, SQLite database, workspace and backups. The local Ollama models and user-supplied speech models are not embedded. Frozen builds store writable data in `%LOCALAPPDATA%\Chinuuu`, not inside PyInstaller's `_internal` directory. Test the resulting binaries on the target Windows build before use. Code signing and an actual setup installer are outside this source-release build script.

## 7. Updating from v1.3.2 or v2.0.0

1. Close all Chinuuu CLI/GUI instances.
2. Make a separate copy of the existing project folder as a backup.
3. Extract the applicable update patch over the existing project, allowing code files to be replaced. Use the v2.0.1 patch for an existing v2.0.0 installation; use the v2.0.0 patch for v1.3.2.
4. Do **not** replace `config.toml`, `data`, `workspace` or `.venv`.
5. Activate the existing virtual environment and install the updated package/extras.
6. Run the full test suite before launching.

```powershell
cd D:\Projects\Chinuuu
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev,windows,documents,voice,wakeword,installer]"
python -m pytest -q
if ($LASTEXITCODE -ne 0) { throw "Tests failed. Do not launch Chinuuu." }
python main.py
```

For a rollback, close Chinuuu and restore the backup project directory. The application database is migrated additively; keep a separate copy before first v2.0.0 launch.

## 8. Windows acceptance checklist

Run each test on the target PC and record the result before calling the release fully accepted:

- [ ] Brightness `40%` and `100%` visibly change the built-in panel.
- [ ] Night Light changes state and reads back the new state.
- [ ] Volume/mute read and set the default playback endpoint.
- [ ] Media keys affect the active media application as expected.
- [ ] Wi-Fi/Bluetooth status is correct; radio on/off requires confirmation and is verified.
- [ ] Window enumeration and UIA inspection work in Notepad and one other app.
- [ ] UIA click/text entry require approval; no form is submitted.
- [ ] Screenshot confirmation appears and output lands in workspace.
- [ ] PDF/DOCX indexing works with optional document dependencies installed.
- [ ] Push-to-talk transcription and offline speech output work with local models.
- [ ] Wake-word mode starts only when deliberately enabled, with a valid local model.
- [ ] Reminders notify while Chinuuu runs; dismissal and restart persistence work.
- [ ] Calendar `.ics` imports correctly into the calendar app selected by the user.
- [ ] Email/WhatsApp actions open drafts only and never auto-send.
- [ ] GUI, CLI, backups and upgrade/rollback are tested on the target Windows machine.

## 9. Data locations

- `config.toml` — private local configuration (not part of release archives).
- `data/chinuuu.sqlite3` — conversation, memories, tasks, contacts, reminders, calendar, document index and audit data.
- `data/backups/` — integrity-checked SQLite backups.
- `workspace/` — user-controlled working files and generated artifacts.
- `models/` — optional local speech and wake-word model directories.

The source release intentionally excludes the user's actual configuration, SQLite database, workspace, virtual environment and model files.

## 10. Development and tests

```powershell
python -m pip install -e ".[dev,documents]"
python -m compileall -q chinuuu
python -m pytest -q
```

The test suite includes migration compatibility, workspace and artifact path safety, local document indexing, contact resolution, iCalendar escaping, reminder persistence, backup integrity, privacy redaction, approval gates, radio API state checks, offline model requirements and CLI/GUI import checks. Windows acceptance tests that need actual hardware, microphones, radio devices, desktop controls or account apps are intentionally not mocked into a claim of physical-device success.


### Brightness driver compatibility (v2.0.2)
Some Windows laptop WMI providers apply the requested brightness but return no method status value. Chinuuu accepts this only when every WMI brightness controller reports the requested level after the operation. Explicit non-zero status codes and read-back mismatches remain failures. The result describes WMI verification, not direct measurement of physical panel luminance.


### GUI diagnostic commands (v2.0.2)
The desktop GUI now handles `/system`, `/time`, and `/processes` as deterministic local application commands. The System status button uses the same read-only system telemetry as the CLI; these commands are not sent to the language model.

### Brightness driver compatibility (v2.0.2)
Some laptop WMI providers apply the brightness setting but return no method status. Chinuuu accepts that driver behaviour only when every readable brightness controller reports the requested percentage after the operation. Explicit non-zero statuses, missing structured output, controller-count mismatches, and incorrect read-back values remain errors. The response identifies WMI read-back as software telemetry, not a direct measurement of panel luminance.

## 11. Control Center (v4.0.0)

Open the desktop GUI and select **Control center** in the header. The Control Center provides:

- **Tasks:** add tasks and mark selected tasks complete.
- **Memory review:** inspect approved memories and explicitly approve/reject pending adaptive-memory suggestions.
- **Agent activity:** inspect recent agent runs and tool audit records.

The dashboard reads the same local SQLite store used by the CLI and assistant. It does not create a second database or synchronize data to a cloud service.
