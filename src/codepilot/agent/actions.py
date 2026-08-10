from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

SENSITIVE_KEY_PARTS = ("api_key", "token", "password", "secret")
TRACE_TEXT_MAX_CHARS = 1000


class AgentFinishArgs(BaseModel):
    """Arguments accepted by the internal native finish tool."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["success", "failed", "partial"]
    summary: str
    delivery_kind: Literal["message", "analysis", "code_change"] | None = None
    tests: str | None = None
    changed_files: list[str] = Field(default_factory=list)

    @field_validator("summary")
    @classmethod
    def validate_summary(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("summary must not be empty")
        return value


class AgentFinishAction(AgentFinishArgs):
    """Internal domain action created from a native codepilot_finish call."""

    type: Literal["finish"] = "finish"


def _truncate_text(text: str, max_chars: int = TRACE_TEXT_MAX_CHARS) -> str:
    if len(text) <= max_chars:
        return text
    suffix = "... truncated"
    return f"{text[: max(0, max_chars - len(suffix))]}{suffix}"


def _is_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    return any(part in lowered for part in SENSITIVE_KEY_PARTS)


def _sanitize_trace_value(value: Any, *, parent_key: str | None = None) -> Any:
    if parent_key is not None and _is_sensitive_key(parent_key):
        return "[REDACTED]"
    if isinstance(value, dict):
        return {str(key): _sanitize_trace_value(item, parent_key=str(key)) for key, item in value.items()}
    if isinstance(value, list):
        return [_sanitize_trace_value(item, parent_key=parent_key) for item in value]
    if isinstance(value, str) and parent_key in {"patch", "replacement"}:
        return _truncate_text(value)
    return value


def agent_action_to_trace_input(action: AgentFinishAction) -> dict[str, Any]:
    return _sanitize_trace_value(action.model_dump())
