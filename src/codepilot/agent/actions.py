from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

SENSITIVE_KEY_PARTS = ("api_key", "token", "password", "secret")
TRACE_TEXT_MAX_CHARS = 1000


def _registered_tool_names() -> set[str]:
    """延迟读取工具名，注册表故障直接暴露给调用方。

    这里没有可恢复的预期异常。若注册表初始化或工具规格本身损坏，应保留
    原始异常，而不是把编程错误伪装成模型提交了未知 action alias。
    """

    from codepilot.tools.registry import list_tool_specs

    return {spec.name for spec in list_tool_specs()}


@dataclass(frozen=True)
class ParsedAgentAction:
    """解析后的动作以及归一化过程中的辅助信息。"""

    action: AgentAction
    raw_action: dict[str, Any]
    normalized_action: dict[str, Any]
    normalization_metadata: dict[str, Any] = field(default_factory=dict)


class AgentActionParseError(ValueError):
    """模型输出无法解析为 AgentAction 时抛出的结构化异常。"""

    def __init__(
        self,
        message: str,
        *,
        code: str,
        raw_text: str | None = None,
        raw_action: dict[str, Any] | None = None,
        normalized_action: dict[str, Any] | None = None,
        normalization_metadata: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.raw_text = raw_text
        self.raw_action = raw_action
        self.normalized_action = normalized_action
        self.normalization_metadata = normalization_metadata or {}


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


AgentAction = AgentToolCallAction | AgentFinishAction
AgentTurnKind = Literal["natural_reply", "tool_call", "finish"]


@dataclass(frozen=True)
class ParsedAgentTurn:
    kind: AgentTurnKind
    text: str
    action: AgentAction | None = None
    parsed_action: ParsedAgentAction | None = None


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


def _mark_normalized_field(metadata: dict[str, Any], source: str, target: str) -> None:
    """记录一次字段归一化，避免散落在各个分支里。"""

    metadata["normalization_applied"] = True
    metadata["normalized_fields"][source] = target
    if source not in metadata["non_standard_fields"]:
        metadata["non_standard_fields"].append(source)


def _record_conflict(metadata: dict[str, Any], left: str, right: str) -> None:
    """记录字段冲突，方便 trace 和 observation 定位。"""

    conflict = f"{left}/{right}"
    if conflict not in metadata["conflicts"]:
        metadata["conflicts"].append(conflict)


def extract_single_json_object(text: str) -> dict[str, Any]:
    """从模型输出里提取单个 JSON object。

    只接受一个 JSON object，允许外层包裹少量解释性文字，也允许 fenced JSON。
    """

    if not isinstance(text, str) or not text.strip():
        raise AgentActionParseError("Response must be a non-empty JSON object.", code="empty_response", raw_text=text)
    stripped = text.strip()
    try:
        data = json.loads(stripped)
    except json.JSONDecodeError:
        data = None
    else:
        if isinstance(data, dict):
            return data
        raise AgentActionParseError(
            "Response must be a JSON object, not an array or primitive.",
            code="non_object_json",
            raw_text=text,
        )

    objects: list[dict[str, Any]] = []
    decoder = json.JSONDecoder()
    in_string = False
    escape = False
    brace_depth = 0
    bracket_depth = 0
    start_index: int | None = None
    for index, character in enumerate(text):
        if in_string:
            if escape:
                escape = False
            elif character == "\\":
                escape = True
            elif character == '"':
                in_string = False
            continue
        if character == '"':
            in_string = True
            continue
        if character == "{":
            if brace_depth == 0 and bracket_depth == 0:
                start_index = index
            brace_depth += 1
            continue
        if character == "}":
            if brace_depth == 0:
                continue
            brace_depth -= 1
            if brace_depth == 0 and bracket_depth == 0 and start_index is not None:
                candidate = text[start_index : index + 1]
                try:
                    candidate_data = decoder.decode(candidate)
                except json.JSONDecodeError:
                    start_index = None
                    continue
                if not isinstance(candidate_data, dict):
                    raise AgentActionParseError(
                        "Response must be a JSON object, not an array or primitive.",
                        code="non_object_json",
                        raw_text=text,
                    )
                objects.append(candidate_data)
                start_index = None
            continue
        if character == "[":
            bracket_depth += 1
            continue
        if character == "]" and bracket_depth > 0:
            bracket_depth -= 1

    if len(objects) > 1:
        raise AgentActionParseError("Return exactly one JSON object, not multiple objects.", code="multiple_json_objects", raw_text=text)
    if len(objects) == 1:
        return objects[0]
    raise AgentActionParseError(
        "Invalid JSON object: unable to locate a JSON object in the response.",
        code="no_json_object",
        raw_text=text,
    )


def normalize_agent_action(raw: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """把真实 LLM 常见的字段别名归一化成 CodePilot 的标准动作 schema。"""

    normalized = dict(raw)
    metadata: dict[str, Any] = {
        "normalization_applied": False,
        "normalized_fields": {},
        "non_standard_fields": [],
        "conflicts": [],
    }
    tool_name_aliases = ("tool_name", "tool", "name", "function_name", "function")
    argument_aliases = ("arguments", "parameters", "input", "args")
    if normalized.get("type") == "final":
        normalized["type"] = "finish"
        _mark_normalized_field(metadata, "type", "finish")
    if "type" in normalized and "action" in normalized:
        _mark_normalized_field(metadata, "action", "type")
        if normalized["type"] != normalized["action"]:
            _record_conflict(metadata, "type", "action")
        del normalized["action"]
    elif "action" in normalized:
        action_value = normalized["action"]
        if action_value in {"finish", "final"}:
            normalized["type"] = "finish"
            _mark_normalized_field(metadata, "action", "type")
        elif action_value in _registered_tool_names():
            normalized["type"] = "tool_call"
            normalized["tool_name"] = action_value
            _mark_normalized_field(metadata, "action", "tool_name")
        else:
            raise AgentActionParseError(
                f"Unknown action alias: {action_value}",
                code="unknown_action_type",
                raw_action=raw,
                normalized_action=normalized,
                normalization_metadata=metadata,
            )
        del normalized["action"]
    if normalized.get("type") != "finish":
        if "tool_name" not in normalized:
            for alias in tool_name_aliases[1:]:
                if alias not in normalized:
                    continue
                normalized["tool_name"] = normalized[alias]
                _mark_normalized_field(metadata, alias, "tool_name")
                del normalized[alias]
                break
        for alias in tool_name_aliases[1:]:
            if alias not in normalized:
                continue
            _mark_normalized_field(metadata, alias, "tool_name")
            if normalized[alias] != normalized["tool_name"]:
                _record_conflict(metadata, "tool_name", alias)
            del normalized[alias]
        if "arguments" not in normalized:
            for alias in argument_aliases[1:]:
                if alias not in normalized:
                    continue
                normalized["arguments"] = normalized[alias]
                _mark_normalized_field(metadata, alias, "arguments")
                del normalized[alias]
                break
        for alias in argument_aliases[1:]:
            if alias not in normalized:
                continue
            _mark_normalized_field(metadata, alias, "arguments")
            if normalized[alias] != normalized["arguments"]:
                _record_conflict(metadata, "arguments", alias)
            del normalized[alias]
    if "type" not in normalized and "tool_name" in normalized:
        normalized["type"] = "tool_call"
    return normalized, metadata


def _looks_like_structured_action_attempt(text: str) -> bool:
    """判断模型是否明显在尝试输出结构化动作。"""

    stripped = text.strip()
    if not stripped:
        return False
    if stripped.startswith("{") or stripped.startswith("["):
        return True
    if stripped.startswith("```"):
        return "{" in stripped
    lowered = stripped.lower()
    if re.search(r'"(?:type|tool_name|arguments|action|parameters)"\s*:\s*"(?:tool_call|finish|[^"]+)"', lowered):
        return True
    return False


def parse_agent_action_with_metadata(text: str) -> ParsedAgentAction:
    """解析模型输出，并保留原始字段与归一化过程信息。"""

    raw_action = extract_single_json_object(text)
    normalized_action, normalization_metadata = normalize_agent_action(raw_action)
    action_type = normalized_action.get("type")
    try:
        if action_type == "tool_call":
            action = AgentToolCallAction.model_validate(normalized_action)
        elif action_type == "finish":
            action = AgentFinishAction.model_validate(normalized_action)
        elif action_type is None:
            raise AgentActionParseError(
                "Missing required field after normalization: type.",
                code="schema_validation_error",
                raw_text=text,
                raw_action=raw_action,
                normalized_action=normalized_action,
                normalization_metadata=normalization_metadata,
            )
        else:
            raise AgentActionParseError(
                f"Unknown action type after normalization: {action_type}",
                code="unknown_action_type",
                raw_text=text,
                raw_action=raw_action,
                normalized_action=normalized_action,
                normalization_metadata=normalization_metadata,
            )
    except ValidationError as exc:
        error = exc.errors()[0] if exc.errors() else {}
        loc = ".".join(str(part) for part in error.get("loc", []))
        msg = error.get("msg", str(exc))
        message = f"Invalid field after normalization: {loc}: {msg}" if loc else msg
        raise AgentActionParseError(
            message,
            code="schema_validation_error",
            raw_text=text,
            raw_action=raw_action,
            normalized_action=normalized_action,
            normalization_metadata=normalization_metadata,
        ) from exc
    return ParsedAgentAction(
        action=action,
        raw_action=raw_action,
        normalized_action=normalized_action,
        normalization_metadata=normalization_metadata,
    )


def parse_agent_action(text: str) -> AgentAction:
    """保持向后兼容的简洁入口，只返回标准 AgentAction。"""

    return parse_agent_action_with_metadata(text).action


def parse_agent_turn(text: str) -> ParsedAgentTurn:
    """把模型输出分类成自然回复、工具调用或结构化 finish。"""

    stripped = text.strip()
    if not stripped:
        raise AgentActionParseError("Response must be a non-empty JSON object.", code="empty_response", raw_text=text)
    try:
        parsed_action = parse_agent_action_with_metadata(text)
    except AgentActionParseError as exc:
        if exc.code == "no_json_object" and not _looks_like_structured_action_attempt(text):
            return ParsedAgentTurn(kind="natural_reply", text=stripped)
        raise
    if isinstance(parsed_action.action, AgentToolCallAction):
        return ParsedAgentTurn(kind="tool_call", text=stripped, action=parsed_action.action, parsed_action=parsed_action)
    return ParsedAgentTurn(kind="finish", text=stripped, action=parsed_action.action, parsed_action=parsed_action)


def agent_action_dict_to_trace_preview(data: dict[str, Any], *, max_chars: int = TRACE_TEXT_MAX_CHARS) -> dict[str, Any] | str:
    """把原始动作字典压缩成适合 trace 的短预览。"""

    preview = _sanitize_trace_value(data)
    serialized = json.dumps(preview, ensure_ascii=False, sort_keys=True)
    if len(serialized) <= max_chars:
        return preview
    return _truncate_text(serialized, max_chars)


def agent_action_to_trace_input(action: AgentAction) -> dict[str, Any]:
    """把动作转成适合 trace 的输入结构。"""

    return _sanitize_trace_value(action.model_dump())
