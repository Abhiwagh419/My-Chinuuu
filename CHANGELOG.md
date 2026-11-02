# Changelog

## v4.0.0 — Durable intelligence and recovery
- Added local-model conversation summarization with SQLite persistence; source messages remain untouched and summaries are treated as untrusted historical context.
- Added deterministic relevance ranking for approved memories to keep prompt context bounded and focused.
- Added stale agent-run recovery: only runs older than ten minutes are marked interrupted, and interrupted actions are never replayed automatically.
- Added read-only `/health` diagnostics and `/context` statistics to CLI and GUI, plus a GUI Health Check shortcut.
- Added additive database migration and regression tests for summaries, source preservation, memory ranking, recovery and GUI routing.
- Validation in build environment: 243 tests passed; target Windows hardware acceptance remains outstanding.


## 2.0.2
- Fixed the desktop GUI `/system`, `/time`, and `/processes` commands so they execute their deterministic local diagnostics instead of being sent to the language model.
- Fixed HP/Windows WMI providers that apply brightness changes but return no method status value.
- Accepts a missing method return code only when all WMI brightness controllers read back the requested level.
- Explicit non-zero status codes, incomplete output, controller-count changes, and read-back mismatches still fail.
- Reports the distinction between a changed brightness level and a level that was already set.


## v2.0.1 — Desktop keyboard workflow and release polish

### Changed
- GUI composer now sends on Enter or keypad Enter; Shift+Enter inserts a newline and Ctrl+Enter remains a send shortcut.
- Added an inline keyboard-shortcut hint beneath the composer.
- Sending is ignored safely while a request is running, preserving unsent text instead of silently clearing it.
- Empty messages are ignored without changing the transcript.
- Added focused regression tests for GUI keyboard behaviour.

### Verification
- 226 automated tests pass in the release build environment.
- The v2.0.0 Windows test result supplied by the user was 217 passed and 4 skipped; the skipped tests require symbolic-link creation support. The new GUI tests increase the expected suite count to 226, subject to rerun on the target PC.

## v2.0.0 — Integrated personal assistant foundation

### Added
- Windows Core Audio volume and mute controls using `pycaw` with read-after-write checks.
- Windows media-key dispatch for play/pause, next and previous.
- Wi-Fi and Bluetooth state inspection and WinRT radio control with mandatory approval and state verification.
- Semantic Windows UI Automation for window listing, control inspection, uniquely matched clicks and approved text entry.
- Approval-gated primary-display screenshots stored in the configured workspace.
- Local document indexing for TXT, Markdown, Python, JSON, CSV, HTML, PDF and DOCX using SQLite FTS5 with a LIKE fallback.
- Optional local Ollama embeddings with persisted vectors and cosine-similarity semantic retrieval; CPU inference protects discrete GPU VRAM.
- Confirmed public HTML page extraction when public network tools are enabled.
- Local reminders, exact-name contact resolution, SQLite calendar events and `.ics` export.
- Email and WhatsApp draft preparation without automatic sending.
- Optional local faster-whisper speech recognition, pyttsx3 speech output and explicitly opt-in ONNX wake-word detection.
- Tkinter desktop interface with local chat, tool approvals, push-to-talk, optional speech output and reminders.
- Durable agent-run/step history, local tool audit, recursive sensitive-value redaction and integrity-checked SQLite backups.
- Workspace-local Markdown/TXT/HTML/DOCX and PowerPoint generation.
- PowerShell launchers, Windows PyInstaller build script and Windows/Linux CI workflow.

### Security and reliability
- Added confirmation-denial short-circuiting: a denied operation is not passed back to the language model for reinterpretation.
- Strengthened symlink/junction and output-path checks for document indexing, document/presentation creation, screenshot capture and calendar export.
- Added collision-safe artifact and calendar output creation; existing files are not silently overwritten.
- Added redaction of common contact identifiers in durable agent objectives/summaries and recursive tool argument logging.
- Kept arbitrary shell execution unavailable. Workspace Python execution remains explicitly approval-gated and is not an OS sandbox.
- Kept external web access opt-in and individually confirmed; email and WhatsApp remain drafts only.

### Known limitations
- Windows hardware and UI Automation behaviour requires acceptance testing on the actual target PC.
- Voice requires optional dependencies and user-supplied local model files; no model is bundled or automatically downloaded.
- Wake-word detection is disabled by default and requires a user-supplied local ONNX model with an appropriate licence.
- Semantic document retrieval requires a locally installed Ollama embedding model; without one, search uses SQLite FTS. Scanned PDFs are not OCR-processed.
- Reminders notify only while Chinuuu is running. Calendar/contact data has no cloud sync.
- GUI/executable build scripts are included, but a signed installer is not prebuilt in this source release.

## v1.3.2
- Corrected brightness false-success handling by requiring a numeric WMI success return code and an explicit brightness read-back.
- Added additional regression coverage for Windows brightness control.

## v1.3.1
- Changed the brightness WMI invocation path for compatibility with Windows display controllers.

## v1.3.0
- Added deterministic natural-language routing for screen brightness and Night Light.
- Added phone-number-based WhatsApp click-to-chat draft preparation; no automatic sending.

## v1.2.0
- Added the existing application, workspace and browser tool foundation, persistent local memory and bounded tool loop.
