from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any


class OllamaError(RuntimeError):
    pass


class OllamaClient:
    def __init__(self, base_url: str, model: str, context_window: int, temperature: float) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.context_window = context_window
        self.temperature = temperature

    def check_connection(self) -> None:
        request = urllib.request.Request(f"{self.base_url}/api/tags", method="GET")
        try:
            with urllib.request.urlopen(request, timeout=4) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise OllamaError("Cannot reach Ollama. Start the Ollama application.") from exc
        models = {item.get("name") for item in payload.get("models", [])}
        if self.model not in models:
            raise OllamaError(f"Model '{self.model}' is not installed. Run: ollama pull {self.model}")

    def _post_chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "options": {"num_ctx": self.context_window, "temperature": self.temperature},
        }
        if tools:
            payload["tools"] = tools
        request = urllib.request.Request(
            f"{self.base_url}/api/chat",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=300) as response:
                result = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise OllamaError(f"Ollama HTTP {exc.code}: {detail[:500]}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise OllamaError("Could not complete the local model request.") from exc
        message = result.get("message")
        if not isinstance(message, dict):
            raise OllamaError("Ollama returned an unexpected response format.")
        return message

    def chat(self, messages: list[dict[str, Any]]) -> str:
        message = self._post_chat(messages)
        content = message.get("content")
        if not isinstance(content, str):
            raise OllamaError("Ollama returned an unexpected text response.")
        return content.strip()

    def chat_message(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None
    ) -> dict[str, Any]:
        return self._post_chat(messages, tools)
