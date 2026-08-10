from __future__ import annotations

import re
from pathlib import Path
from typing import Any

SENSITIVE_KEYS = {
    "token",
    "access_token",
    "refresh_token",
    "password",
    "secret",
    "api_key",
    "apikey",
    "authorization",
    "cookie",
    "set-cookie",
    "client_secret",
    "private_key",
}

_TEXT_REDACTIONS = (
    (re.compile(r"(?i)\b(token|access_token|refresh_token)\s*=\s*([^\s,;]+)"), r"\1=[REDACTED]"),
    (re.compile(r"(?i)\bpassword\s*=\s*([^\s,;]+)"), "password=[REDACTED]"),
    (re.compile(r"(?i)\b(secret|api_key|apikey|private_key|client_secret)\s*=\s*([^\s,;]+)"), "[REDACTED]"),
    (re.compile(r"(?i)\bauthorization:\s*bearer\s+[^\s,;]+"), "Authorization: [REDACTED]"),
    (re.compile(r"(?i)\b(set-cookie|cookie):\s*[^\r\n]+"), r"\1: [REDACTED]"),
)


def redact_text(value: str, *, max_chars: int = 4000) -> str:
    redacted = value
    for pattern, replacement in _TEXT_REDACTIONS:
        redacted = pattern.sub(replacement, redacted)
    if len(redacted) <= max_chars:
        return redacted
    suffix = "... truncated"
    return f"{redacted[: max(0, max_chars - len(suffix))]}{suffix}"


def redact_mapping(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if any(token in key_text.lower() for token in SENSITIVE_KEYS):
                redacted[key_text] = "[REDACTED]"
            else:
                redacted[key_text] = redact_mapping(item)
        return redacted
    if isinstance(value, list):
        return [redact_mapping(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_mapping(item) for item in value)
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, str):
        return redact_text(value)
    return value
