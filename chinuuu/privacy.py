from __future__ import annotations

import re
from typing import Any

SENSITIVE_KEYS = frozenset({
    "phone", "phone_number", "email", "recipient", "body", "message",
    "text", "content", "password", "passcode", "pin", "otp", "token",
    "authorization", "secret", "api_key", "access_token", "refresh_token",
})

_EMAIL_PATTERN = re.compile(r"(?<![\w.+-])[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}(?![\w.-])", re.IGNORECASE)
_PHONE_PATTERN = re.compile(r"(?<!\w)\+\d(?:[\d\s().-]{6,}\d)(?!\w)")


def redact_text(value: str) -> str:
    """Redact common contact identifiers before writing operational audit records."""
    cleaned = _EMAIL_PATTERN.sub("[EMAIL REDACTED]", value)
    return _PHONE_PATTERN.sub("[PHONE REDACTED]", cleaned)


def redact_data(value: Any, *, parent_key: str = "") -> Any:
    """Recursively redact sensitive values from JSON-compatible tool arguments."""
    if parent_key.casefold() in SENSITIVE_KEYS:
        return "[REDACTED]"
    if isinstance(value, dict):
        return {
            str(key): redact_data(item, parent_key=str(key))
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact_data(item) for item in value]
    if isinstance(value, str):
        return redact_text(value)
    return value
