from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Literal

from codepilot.memory.policy import REDACTED_SECRET

ContentKind = Literal["code", "search_result", "log", "external", "generic"]

_HIGH_CONFIDENCE_PATTERNS = (
    re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{16,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"),
)
_ASSIGNMENT = re.compile(
    r"(?i)(\b(?:api[_-]?key|token|password|secret)\b\s*[:=]\s*)([\"'])(.*?)(\2)"
)
_PLAIN_ASSIGNMENT = re.compile(r"(?i)(\b(?:api[_-]?key|token|password|secret)\b\s*[:=]\s*)([^\s,;]+)")


@dataclass(frozen=True)
class ToolOutputRedactionResult:
    value: str
    redaction_count: int
    strategy: str


def redact_tool_output(
    text: str,
    *,
    tool_name: str,
    content_kind: ContentKind,
) -> ToolOutputRedactionResult:
    value = text
    count = 0
    for pattern in _HIGH_CONFIDENCE_PATTERNS:
        value, replaced = pattern.subn(REDACTED_SECRET, value)
        count += replaced
    value, replaced = _ASSIGNMENT.subn(_redact_quoted_assignment, value)
    count += replaced
    if content_kind in {"code", "search_result", "log", "external", "generic"}:
        value, replaced = _PLAIN_ASSIGNMENT.subn(_redact_plain_assignment, value)
        count += replaced
    return ToolOutputRedactionResult(value, count, f"{content_kind}:{tool_name}")


def _redact_quoted_assignment(match: re.Match[str]) -> str:
    value = match.group(3)
    return match.group(1) + match.group(2) + REDACTED_SECRET + match.group(2) if _looks_secret(value) else match.group(0)


def _redact_plain_assignment(match: re.Match[str]) -> str:
    value = match.group(2)
    if _looks_dynamic_expression(value):
        return match.group(0)
    return match.group(1) + REDACTED_SECRET if _looks_secret(value) else match.group(0)


def _looks_dynamic_expression(value: str) -> bool:
    if any(marker in value for marker in ("(", ")", "[", "]", "{", "}")):
        return True
    return re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]*", value) is not None


def _looks_secret(value: str) -> bool:
    if value in {"None", "null", "nil", "True", "False"} or len(value) < 16:
        return False
    classes = sum(bool(re.search(pattern, value)) for pattern in (r"[a-z]", r"[A-Z]", r"\d", r"[^A-Za-z0-9]"))
    entropy = -sum((value.count(char) / len(value)) * math.log2(value.count(char) / len(value)) for char in set(value))
    return classes >= 3 or entropy >= 3.2
