from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Mapping

from codepilot.mcp.models import MCPServerConfig, MCPToolInfo

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

_SERVER_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]+$")
_TOOL_NAME_RE = re.compile(r"[^a-zA-Z0-9_.:-]+")
_MCP_TEXT_REDACTIONS = (
    (re.compile(r"(?i)\b(token|access_token|refresh_token)\s*=\s*([^\s,;]+)"), r"\1=[REDACTED]"),
    (re.compile(r"(?i)\bpassword\s*=\s*([^\s,;]+)"), "password=[REDACTED]"),
    (re.compile(r"(?i)\b(secret|api_key|apikey|private_key|client_secret)\s*=\s*([^\s,;]+)"), "[REDACTED]"),
    (re.compile(r"(?i)\bauthorization:\s*bearer\s+[^\s,;]+"), "Authorization: [REDACTED]"),
    (re.compile(r"(?i)\b(set-cookie|cookie):\s*[^\r\n]+"), r"\1: [REDACTED]"),
)


def sanitize_mcp_server_name(name: str) -> str:
    name = name.strip()
    if not name or not _SERVER_NAME_RE.fullmatch(name):
        raise ValueError(f"Invalid MCP server name: {name!r}")
    return name


def sanitize_mcp_tool_name(name: str) -> str:
    name = name.strip()
    if not name:
        raise ValueError("Invalid MCP tool name: empty")
    return _TOOL_NAME_RE.sub("_", name)


def build_codepilot_mcp_tool_name(server_name: str, tool_name: str) -> str:
    return f"mcp.{sanitize_mcp_server_name(server_name)}.{sanitize_mcp_tool_name(tool_name)}"


def _redact_text(value: str, *, max_chars: int = 4000) -> str:
    if len(value) <= max_chars:
        return value
    suffix = "... truncated"
    return f"{value[: max(0, max_chars - len(suffix))]}{suffix}"


def redact_mcp_text(value: str, *, max_chars: int = 4000) -> str:
    redacted = value
    for pattern, replacement in _MCP_TEXT_REDACTIONS:
        redacted = pattern.sub(replacement, redacted)
    return _redact_text(redacted, max_chars=max_chars)


def redact_mcp_mapping(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if any(token in key_text.lower() for token in SENSITIVE_KEYS):
                redacted[key_text] = "[REDACTED]"
            else:
                redacted[key_text] = redact_mcp_mapping(item)
        return redacted
    if isinstance(value, list):
        return [redact_mcp_mapping(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_mcp_mapping(item) for item in value)
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, str):
        return redact_mcp_text(value)
    return value


def truncate_mcp_text(text: str, max_chars: int) -> tuple[str, bool]:
    if len(text) <= max_chars:
        return text, False
    suffix = "... truncated"
    return f"{text[: max(0, max_chars - len(suffix))]}{suffix}", True


def canonical_json_hash(payload: Mapping[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def build_mcp_descriptor_hash(tool: MCPToolInfo) -> str:
    return canonical_json_hash(
        {
            "server_name": tool.server_name,
            "tool_name": tool.name,
            "description": tool.description,
            "input_schema": tool.input_schema,
            "output_schema": tool.output_schema,
            "annotations": tool.annotations,
            "side_effect_hint": tool.side_effect_hint,
        }
    )


def build_mcp_config_hash(server: MCPServerConfig) -> str:
    return canonical_json_hash(
        {
            "name": server.name,
            "transport": server.transport,
            "enabled": server.enabled,
            "tool_allowlist": server.tool_allowlist,
            "tool_denylist": server.tool_denylist,
            "trust_level": server.trust_level,
            "expose_to_agent": server.expose_to_agent,
            "require_tool_allowlist": server.require_tool_allowlist,
            "trusted_annotations": server.trusted_annotations,
            "server_instructions_policy": server.server_instructions_policy,
            "startup_timeout_seconds": server.startup_timeout_seconds,
            "tool_timeout_seconds": server.tool_timeout_seconds,
            "required": server.required,
            "max_tools_to_expose": server.max_tools_to_expose,
            "max_description_chars": server.max_description_chars,
            "env_keys": sorted(server.env),
        }
    )


def summarize_mcp_command(command: list[str]) -> dict[str, Any]:
    if not command:
        return {}
    executable = Path(command[0]).name
    args = [truncate_mcp_text(arg, 80)[0] for arg in command[1:4]]
    return {
        "executable": executable,
        "arg_count": max(0, len(command) - 1),
        "args_preview": args,
        "args_preview_truncated": len(command) > 4,
    }
