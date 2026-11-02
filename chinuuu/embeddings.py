from __future__ import annotations

import json
import math
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from .errors import ToolError


class OllamaEmbedder:
    """Local-only Ollama embedding client with CPU inference and bounded batches."""

    def __init__(self, base_url: str, model: str, timeout: float = 90.0) -> None:
        parsed = urlsplit(base_url)
        if parsed.scheme not in {"http", "https"} or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise ToolError("The document embedding endpoint must be a loopback Ollama URL; remote embedding services are blocked.")
        if not isinstance(model, str) or not model.strip() or len(model) > 120:
            raise ToolError("Configure a valid local Ollama embedding model name.")
        self.base_url = base_url.rstrip("/")
        self.model = model.strip()
        self.timeout = max(5.0, min(float(timeout), 180.0))

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not isinstance(texts, list) or not texts or len(texts) > 32:
            raise ToolError("Embedding requests must contain 1–32 text chunks per batch.")
        if any(not isinstance(item, str) or not item.strip() or len(item) > 6000 for item in texts):
            raise ToolError("Each embedding input must contain 1–6,000 characters.")
        payload = json.dumps({
            "model": self.model,
            "input": texts,
            # Keep the generation model's VRAM available on a 6 GB GPU.
            "options": {"num_gpu": 0},
            "keep_alive": "2m",
        }).encode("utf-8")
        request = Request(
            f"{self.base_url}/api/embed",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                result = json.loads(response.read(8_000_000).decode("utf-8"))
        except HTTPError as exc:
            detail = exc.read(1000).decode("utf-8", errors="replace")
            raise ToolError(f"Local Ollama embedding request failed (HTTP {exc.code}): {detail}") from exc
        except (URLError, TimeoutError, OSError, ValueError) as exc:
            raise ToolError(f"Could not reach the local Ollama embedding endpoint: {exc}") from exc
        vectors = result.get("embeddings") if isinstance(result, dict) else None
        if not isinstance(vectors, list) or len(vectors) != len(texts):
            raise ToolError("Ollama returned an unexpected embedding response shape.")
        output: list[list[float]] = []
        dimension: int | None = None
        for vector in vectors:
            if not isinstance(vector, list) or not vector:
                raise ToolError("Ollama returned an empty or invalid embedding vector.")
            converted: list[float] = []
            for value in vector:
                if type(value) not in {int, float} or not math.isfinite(float(value)):
                    raise ToolError("Ollama returned a non-finite embedding value.")
                converted.append(float(value))
            if dimension is None:
                dimension = len(converted)
            elif len(converted) != dimension:
                raise ToolError("Ollama returned inconsistent embedding dimensions.")
            output.append(converted)
        return output
