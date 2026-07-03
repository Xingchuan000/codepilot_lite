from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

SENSITIVE_KEY_PARTS = ("api_key", "token", "password", "secret")
TRACE_TEXT_MAX_CHARS = 1000


class AgentActionParseError(ValueError):
    """模型输出无法解析为 AgentAction 时抛出的结构化异常。"""

    def __init__(self, message: str, *, raw_text: str | None = None) -> None:
        super().__init__(message)
        self.raw_text = raw_text


class AgentToolCallAction(BaseModel):
    """模型请求调用一个结构化工具。"""

    model_config = ConfigDict(extra="forbid")

    type: Literal["tool_call"]
    tool_name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    short_rationale: str | None = None

    @field_validator("tool_name")
    @classmethod
    def validate_tool_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("tool_name must not be empty")
        return value


class AgentFinishAction(BaseModel):
    """模型声明本轮任务已结束。"""

    model_config = ConfigDict(extra="forbid")

    type: Literal["finish"]
    status: Literal["success", "failed", "partial"]
    summary: str
    tests: str | None = None
    changed_files: list[str] = Field(default_factory=list)

    @field_validator("summary")
    @classmethod
    def validate_summary(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("summary must not be empty")
        return value


AgentAction = AgentToolCallAction | AgentFinishAction


def _truncate_text(text: str, max_chars: int = TRACE_TEXT_MAX_CHARS) -> str:
    """对大文本字段做截断，避免 trace 输入过大。"""

    if len(text) <= max_chars:
        return text
    suffix = "... truncated"
    return f"{text[: max(0, max_chars - len(suffix))]}{suffix}"


def _is_sensitive_key(key: str) -> bool:
    """对明显敏感字段名做轻量识别。"""

    lowered = key.lower()
    return any(part in lowered for part in SENSITIVE_KEY_PARTS)


def _sanitize_trace_value(value: Any, *, parent_key: str | None = None) -> Any:
    """把动作输入整理成适合 trace 的脱敏结构。"""

    if parent_key is not None and _is_sensitive_key(parent_key):
        return "[REDACTED]"
    if isinstance(value, dict):
        return {str(key): _sanitize_trace_value(item, parent_key=str(key)) for key, item in value.items()}
    if isinstance(value, list):
        return [_sanitize_trace_value(item, parent_key=parent_key) for item in value]
    if isinstance(value, str):
        if parent_key in {"patch", "replacement"}:
            return _truncate_text(value)
        return value
    return value


def parse_agent_action(text: str) -> AgentAction:
    """把模型文本解析为严格的单个 JSON AgentAction。"""

    if not isinstance(text, str) or not text.strip():
        raise AgentActionParseError("Response must be a non-empty JSON object.", raw_text=text)
    stripped = text.strip()
    if stripped.startswith("```"):
        raise AgentActionParseError("Markdown fenced JSON is not allowed. Return raw JSON only.", raw_text=text)
    try:
        data = json.loads(stripped)
    except json.JSONDecodeError as exc:
        raise AgentActionParseError(f"Invalid JSON object: {exc}", raw_text=text) from exc
    if not isinstance(data, dict):
        raise AgentActionParseError("Response must be a JSON object, not an array or primitive.", raw_text=text)
    action_type = data.get("type")
    try:
        if action_type == "tool_call":
            return AgentToolCallAction.model_validate(data)
        if action_type == "finish":
            return AgentFinishAction.model_validate(data)
        if action_type is None:
            raise AgentActionParseError("Missing required field: type.", raw_text=text)
        raise AgentActionParseError(f"Unknown action type: {action_type}", raw_text=text)
    except ValidationError as exc:
        message = exc.errors()[0]["msg"] if exc.errors() else str(exc)
        raise AgentActionParseError(message, raw_text=text) from exc


def agent_action_to_trace_input(action: AgentAction) -> dict[str, Any]:
    """把动作转成适合 trace 的输入结构。"""

    return _sanitize_trace_value(action.model_dump())
