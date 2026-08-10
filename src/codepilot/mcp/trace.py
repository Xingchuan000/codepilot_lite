from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from codepilot.mcp.models import MCPServerConfig, MCPToolInfo

import re

_SERVER_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]+$")
_TOOL_NAME_RE = re.compile(r"[^a-zA-Z0-9_.:-]+")


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
