from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from codepilot.mcp.models import MCPServerConfig

MCP_CONFIG_SCHEMA_VERSION = "codepilot.mcp.config.v1"
ALLOWED_TOP_LEVEL_KEYS = {"schema_version", "servers"}

_EXAMPLE_MCP_CONFIG = {
    "schema_version": MCP_CONFIG_SCHEMA_VERSION,
    "servers": [
        {
            "name": "filesystem",
            "transport": "fake",
            "enabled": True,
            "trust_level": "fake",
            "expose_to_agent": True,
            "require_tool_allowlist": True,
            "trusted_annotations": True,
            "server_instructions_policy": "record_summary",
            "tool_allowlist": ["read_file", "search"],
            "tool_denylist": [],
            "startup_timeout_seconds": 10,
            "tool_timeout_seconds": 30,
            "timeout_seconds": 30,
            "max_output_chars": 12000,
            "max_tools_to_expose": 20,
            "max_description_chars": 500,
        }
    ],
}


def _ensure_object(value: Any, message: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(message)
    return value


def load_mcp_config(path: str | Path) -> list[MCPServerConfig]:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"MCP config file does not exist: {path}")

    raw = json.loads(path.read_text(encoding="utf-8"))
    top = _ensure_object(raw, "MCP config top-level must be an object")
    unknown = sorted(set(top) - ALLOWED_TOP_LEVEL_KEYS)
    if unknown:
        raise ValueError(f"Unknown top-level MCP config key(s): {', '.join(unknown)}")
    if top.get("schema_version") != MCP_CONFIG_SCHEMA_VERSION:
        raise ValueError("Unsupported MCP config schema_version")
    servers = top.get("servers")
    if not isinstance(servers, list):
        raise ValueError("MCP config 'servers' must be a list")

    names: set[str] = set()
    parsed: list[MCPServerConfig] = []
    for item in servers:
        server_raw = _ensure_object(item, "Each MCP server config must be an object")
        if "name" in server_raw:
            server_raw["name"] = str(server_raw["name"]).strip()
        if server_raw.get("transport") == "stdio" and "trust_level" not in server_raw:
            server_raw["trust_level"] = "local_untrusted"
        if server_raw.get("transport") == "fake" and "trust_level" not in server_raw:
            server_raw["trust_level"] = "fake"
        if server_raw.get("transport") == "stdio" and not isinstance(server_raw.get("command", []), list):
            raise ValueError("stdio transport requires command to be a list[str]")

        server = MCPServerConfig.model_validate(server_raw)
        if server.name in names:
            raise ValueError(f"Duplicate MCP server name: {server.name}")
        if server.transport not in {"fake", "stdio"}:
            raise ValueError(f"Unsupported MCP transport: {server.transport}")
        if server.transport == "stdio" and "command" in server_raw and not isinstance(server_raw["command"], list):
            raise ValueError("stdio transport requires command to be a list[str]")
        if server.cwd is not None and not server.cwd.exists():
            raise ValueError(f"MCP cwd does not exist: {server.cwd}")
        names.add(server.name)
        parsed.append(server)
    return parsed


def write_example_mcp_config(path: str | Path, *, overwrite: bool = False) -> Path:
    path = Path(path)
    if path.exists() and not overwrite:
        raise FileExistsError(f"MCP config already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_EXAMPLE_MCP_CONFIG, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    return path
