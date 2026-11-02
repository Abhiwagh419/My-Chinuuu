from __future__ import annotations

import os
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    name: str = "Chinuuu"
    operator_name: str = "Abhi"
    model: str = "qwen3:4b-instruct"
    ollama_url: str = "http://127.0.0.1:11434"
    context_messages: int = 12
    context_summary_interval: int = 24
    context_window: int = 8192
    temperature: float = 0.5
    auto_suggest_preferences: bool = True
    behavior_threshold: int = 3
    behavior_window_days: int = 30
    workspace_root: str = "workspace"
    max_tool_rounds: int = 4
    max_tool_calls: int = 8
    allow_external_network_tools: bool = False
    app_allowlist: dict[str, str] | None = None
    voice_model_path: str = "models/faster-whisper-small"
    voice_response_enabled: bool = False
    reminder_poll_seconds: float = 10.0
    wakeword_enabled: bool = False
    wakeword_model_path: str = "models/wakeword.onnx"
    embedding_model: str = ""


def project_root() -> Path:
    """Return the writable application root in source and frozen deployments."""
    # In a PyInstaller one-directory build, package modules live under _internal;
    # user config/database/workspace must live beside the executable instead.
    if getattr(sys, "frozen", False):
        # Console and GUI bundles live in separate PyInstaller folders. Keep one
        # shared writable profile so both interfaces use the same DB/config.
        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            root = Path(local_app_data) / "Chinuuu"
        else:
            root = Path.home() / "AppData" / "Local" / "Chinuuu"
        root.mkdir(parents=True, exist_ok=True)
        return root.resolve()
    return Path(__file__).resolve().parent.parent


def resolve_workspace_path(workspace_setting: str, root: Path | None = None) -> Path:
    """Resolve configured relative workspaces against the application root."""
    base = (root or project_root()).expanduser().resolve()
    candidate = Path(workspace_setting).expanduser()
    if not candidate.is_absolute():
        candidate = base / candidate
    return candidate.resolve()


def load_settings(path: Path | None = None) -> Settings:
    config_path = path or (project_root() / "config.toml")
    if not config_path.exists():
        return Settings()
    with config_path.open("rb") as file:
        raw = tomllib.load(file)
    assistant = raw.get("assistant", {})
    memory = raw.get("memory", {})
    security = raw.get("security", {})
    apps = raw.get("apps", {})
    voice = raw.get("voice", {})
    productivity = raw.get("productivity", {})
    knowledge = raw.get("knowledge", {})
    return Settings(
        name=assistant.get("name", "Chinuuu"),
        operator_name=assistant.get("operator_name", "Abhi"),
        model=assistant.get("model", "qwen3:4b-instruct"),
        ollama_url=assistant.get("ollama_url", "http://127.0.0.1:11434").rstrip("/"),
        context_messages=max(4, min(40, int(assistant.get("context_messages", 12)))),
        context_summary_interval=max(12, min(200, int(assistant.get("context_summary_interval", 24)))),
        context_window=int(assistant.get("context_window", 8192)),
        temperature=float(assistant.get("temperature", 0.5)),
        auto_suggest_preferences=bool(memory.get("auto_suggest_preferences", True)),
        behavior_threshold=max(2, int(memory.get("behavior_threshold", 3))),
        behavior_window_days=max(1, int(memory.get("behavior_window_days", 30))),
        workspace_root=str(raw.get("tools", {}).get("workspace_root", "workspace")),
        max_tool_rounds=max(1, int(raw.get("tools", {}).get("max_tool_rounds", 4))),
        max_tool_calls=max(1, int(raw.get("tools", {}).get("max_tool_calls", 8))),
        allow_external_network_tools=bool(security.get("allow_external_network_tools", False)),
        app_allowlist={str(k): str(v) for k, v in apps.items() if isinstance(k, str) and isinstance(v, str)},
        voice_model_path=str(voice.get("model_path", "models/faster-whisper-small")),
        voice_response_enabled=bool(voice.get("speak_responses", False)),
        reminder_poll_seconds=max(2.0, min(60.0, float(productivity.get("reminder_poll_seconds", 10.0)))),
        wakeword_enabled=bool(voice.get("wakeword_enabled", False)),
        wakeword_model_path=str(voice.get("wakeword_model_path", "models/wakeword.onnx")),
        embedding_model=str(knowledge.get("embedding_model", "")).strip(),
    )
