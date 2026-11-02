# Chinuuu v4.1.0 Windows acceptance checklist

Run after the automated test suite passes. Record observed behaviour; do not mark a feature complete based only on an assistant success message.

## v4.0.0 intelligence and recovery
- [ ] `/health` reports SQLite integrity as PASS.
- [ ] `/health` resolves the configured workspace and reports the configured Ollama model accurately.
- [ ] `/health` reports the registered tool count and external-network setting; it does not change system settings.
- [ ] `/context` reports stored message and memory counts.
- [ ] After sufficient normal conversation history accumulates (default: 24 messages eligible for compaction while the most recent 12 are retained), a local conversation summary is created.
- [ ] The summary is visible in `/context`; original messages remain present in the database.
- [ ] Stop Ollama during a summary attempt: the user's chat request still completes and the original messages remain intact.
- [ ] Review agent activity: interrupted runs are labelled as interrupted, not completed.
- [ ] Confirm no interrupted operation is automatically replayed.

## GUI processing indicator
- [ ] The running cat is visibly compact and does not dominate the transcript/composer area.
- [ ] One animation runs during text requests; it starts immediately after sending.
- [ ] Voice capture shows "Listening locally" and changes to "Chinuuu is thinking" without speeding up or duplicating the animation.
- [ ] The animation disappears on both response and error.
- [ ] Closing the GUI cancels the pending animation callback without a Tcl error.

## GUI input and Control Center
- [ ] Enter and keypad Enter send a single-line message.
- [ ] Shift+Enter inserts a newline without sending.
- [ ] Ctrl+Enter sends.
- [ ] Empty input does nothing.
- [ ] While a request is running, unsent input is preserved.
- [ ] Control Center opens; task, memory review and agent activity tabs load.
- [ ] Health Check button runs local diagnostics without passing `/health` to the LLM.

## Windows controls
- [ ] Brightness 40% and 100% visibly change the built-in panel; read-back is consistent.
- [ ] Night Light changes state and reads back the new state.
- [ ] Volume/mute read and set the default playback endpoint.
- [ ] Media keys affect the active media application as expected.
- [ ] Wi-Fi/Bluetooth status is correct; radio on/off requires confirmation and is verified.
- [ ] Lock/sleep operations require approval and execute the selected action.

## Desktop automation
- [ ] Window enumeration works with Notepad and another application.
- [ ] UI Automation inspection returns semantic controls.
- [ ] UIA click/text entry require approval and do not submit forms.
- [ ] Screenshot confirmation appears and output is saved in the configured workspace.

## Knowledge and productivity
- [ ] TXT/Markdown/Python indexing and search work.
- [ ] PDF/DOCX indexing works with optional document dependencies installed.
- [ ] Optional Ollama embeddings work after the embedding model is installed; FTS fallback works without it.
- [ ] Reminder notification appears while Chinuuu is running and persists across restart.
- [ ] Calendar `.ics` export imports into the chosen calendar app.
- [ ] Contact lookup requires an unambiguous match.
- [ ] Email/WhatsApp actions prepare drafts only; no automatic sending.
- [ ] Database backup completes and can be integrity-checked.

## Voice and deployment
- [ ] Push-to-talk records and transcribes with a user-supplied local model.
- [ ] Speech output works with local TTS dependencies.
- [ ] Wake-word listener starts only when deliberately enabled and configured with a local model.
- [ ] CLI and GUI share the same database/config/workspace.
- [ ] Closing the GUI stops reminder and wake-word worker threads cleanly.
- [ ] PyInstaller console and GUI builds start on the target Windows installation.
- [ ] Upgrade and rollback preserve config, data and workspace.

## Symbolic-link tests

Four existing symlink tests may be skipped when Windows does not permit symlink creation. To exercise them, enable Windows Developer Mode or run the test shell with the Create symbolic links privilege, then rerun `python -m pytest -q`. Do not change the tests to silently pass when symlink creation fails.

## Important acceptance boundary

Automated tests validate application logic and mocked platform adapters. They do not prove that OEM display firmware, Bluetooth/Wi-Fi radios, installed applications, audio endpoints, microphone drivers or Windows UI Automation behave identically on every PC. Validate these on the actual target machine.
