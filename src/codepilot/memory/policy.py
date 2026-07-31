from __future__ import annotations

import re
from typing import Any

_SECRET_PATTERNS = (
    re.compile(r"(?i)\b(api[_-]?key|token|password|secret)\b\s*[:=]\s*\S+"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
)


def validate_memory_content(content: dict[str, Any]) -> None:
    text = str(content)
    if not is_memory_content_safe(content):
        raise ValueError("memory content appears to contain a secret")
    if len(text) > 20_000:
        raise ValueError("memory content is too large")


def is_memory_content_safe(content: dict[str, Any]) -> bool:
    return not any(pattern.search(str(content)) for pattern in _SECRET_PATTERNS)
