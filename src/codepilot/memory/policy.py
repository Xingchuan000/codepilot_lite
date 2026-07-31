from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

REDACTED_SECRET = "[REDACTED_SECRET]"
_SECRET_PATTERNS = (
    re.compile(r"(?i)\b(api[_-]?key|token|password|secret)\b(\s*[:=]\s*)(?!\[REDACTED_SECRET\])\S+"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
)


@dataclass(frozen=True)
class MemoryRedactionResult:
    value: Any
    redaction_count: int


def redact_memory_value(value: Any) -> MemoryRedactionResult:
    if isinstance(value, str):
        count = 0
        for index, pattern in enumerate(_SECRET_PATTERNS):
            replacement = _redact_assignment if index == 0 else REDACTED_SECRET
            value, replaced = pattern.subn(replacement, value)
            count += replaced
        return MemoryRedactionResult(value, count)
    if isinstance(value, dict):
        items = [(redact_memory_value(key), redact_memory_value(item)) for key, item in value.items()]
        return MemoryRedactionResult(
            {key.value: item.value for key, item in items},
            sum(key.redaction_count + item.redaction_count for key, item in items),
        )
    if isinstance(value, (list, tuple)):
        items = [redact_memory_value(item) for item in value]
        sanitized = [item.value for item in items]
        return MemoryRedactionResult(
            tuple(sanitized) if isinstance(value, tuple) else sanitized,
            sum(item.redaction_count for item in items),
        )
    return MemoryRedactionResult(value, 0)


def _redact_assignment(match: re.Match[str]) -> str:
    return f"{match.group(1)}{match.group(2)}{REDACTED_SECRET}"


def sanitize_memory_content(content: dict[str, Any]) -> tuple[dict[str, Any], int]:
    result = redact_memory_value(content)
    sanitized = dict(result.value)
    validate_memory_content(sanitized)
    return sanitized, result.redaction_count


def validate_memory_content(content: dict[str, Any]) -> None:
    text = str(content)
    if not is_memory_content_safe(content):
        raise ValueError("memory content appears to contain a secret")
    if len(text) > 20_000:
        raise ValueError("memory content is too large")


def is_memory_content_safe(content: dict[str, Any]) -> bool:
    return redact_memory_value(content).redaction_count == 0
