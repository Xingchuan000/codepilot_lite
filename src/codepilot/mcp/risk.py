from __future__ import annotations

import re

from codepilot.mcp.models import MCPServerConfig, MCPToolInfo, MCPToolSideEffectHint
from codepilot.mcp.trace import build_codepilot_mcp_tool_name, build_mcp_config_hash, build_mcp_descriptor_hash
from codepilot.tools.base import ExternalImpact, Reversibility, ToolRisk, ToolSideEffect, ToolSpec


def _text(tool: MCPToolInfo) -> str:
    return f"{tool.name} {tool.description}".lower()


def _has_token(text: str, token: str) -> bool:
    return re.search(rf"(?i)(?<![a-z0-9]){re.escape(token)}(?![a-z0-9])", text) is not None


def _destructive_deny(text: str) -> bool:
    return any(_has_token(text, token) for token in ("publish", "deploy", "push", "release", "comment", "issue", "pr"))


def infer_side_effect_hint(tool: MCPToolInfo, *, server: MCPServerConfig | None = None) -> tuple[MCPToolSideEffectHint, str]:
    text = _text(tool)
    annotations = tool.annotations or {}
    trusted_annotations = bool(
        server and server.trusted_annotations and server.trust_level in {"fake", "local_trusted"}
    )
    trusted_non_readonly_hint = bool(server and server.trust_level in {"fake", "local_trusted"})

    if annotations.get("destructiveHint") is True or annotations.get("destructive") is True:
        if _destructive_deny(text):
            return "external", "annotation.destructive.deny"
        if any(token in text for token in ("write", "edit", "update", "create", "delete", "remove", "patch")):
            return "local_write", "annotation.destructive"
        return "local_write", "annotation.destructive"

    if annotations.get("openWorldHint") is True:
        return "network", "annotation.openWorldHint"

    if any(_has_token(text, token) for token in ("push", "publish", "deploy", "release", "comment", "issue", "pr")):
        return "external", "keyword.external"
    if any(_has_token(text, token) for token in ("write", "edit", "update", "create", "delete", "remove", "patch")):
        return "local_write", "keyword.local_write"
    if any(_has_token(text, token) for token in ("run", "exec", "shell", "test", "command")):
        return "local_exec", "keyword.local_exec"
    if any(_has_token(text, token) for token in ("fetch", "http", "web", "request", "download", "upload")):
        return "network", "keyword.network"

    if annotations.get("readOnlyHint") is True and trusted_annotations:
        return "read_only", "annotation.readOnlyHint.trusted"

    if tool.side_effect_hint != "unknown":
        if tool.side_effect_hint == "read_only":
            if trusted_annotations:
                return "read_only", "explicit_hint.trusted_read_only"
        elif trusted_non_readonly_hint:
            return tool.side_effect_hint, "explicit_hint"

    if any(_has_token(text, token) for token in ("read", "list", "get", "search", "find", "query", "inspect")):
        return "read_only", "keyword.read_only"

    return "unknown", "fallback.unknown"


def _effect(hint: MCPToolSideEffectHint) -> tuple[ToolRisk, ToolSideEffect, ExternalImpact, Reversibility]:
    mapping = {
        "read_only": (ToolRisk.READ_ONLY, ToolSideEffect.NONE, ExternalImpact.NONE, Reversibility.NOT_APPLICABLE),
        "local_write": (ToolRisk.LOCAL_WRITE, ToolSideEffect.LOCAL_WRITE, ExternalImpact.NONE, Reversibility.UNKNOWN),
        "local_exec": (ToolRisk.LOCAL_EXECUTION, ToolSideEffect.LOCAL_EXEC, ExternalImpact.NONE, Reversibility.UNKNOWN),
        "network": (ToolRisk.NETWORK, ToolSideEffect.NETWORK, ExternalImpact.READ, Reversibility.NOT_APPLICABLE),
        "external": (ToolRisk.EXTERNAL_SIDE_EFFECT, ToolSideEffect.EXTERNAL, ExternalImpact.WRITE, Reversibility.UNKNOWN),
        "unknown": (ToolRisk.NETWORK, ToolSideEffect.NETWORK, ExternalImpact.UNKNOWN, Reversibility.UNKNOWN),
    }
    return mapping[hint]


def classify_mcp_tool(tool: MCPToolInfo, *, server: MCPServerConfig) -> ToolSpec:
    descriptor_hash = tool.descriptor_hash or build_mcp_descriptor_hash(tool)
    config_hash = build_mcp_config_hash(server)
    hint, risk_source = infer_side_effect_hint(tool, server=server)
    risk, side_effect, external_impact, reversibility = _effect(hint)
    codepilot_tool_name = build_codepilot_mcp_tool_name(server.name, tool.name)
    description = tool.description[: server.max_description_chars]
    if tool.input_schema.get("type") != "object":
        raise ValueError(f"MCP tool {tool.name!r} input_schema must have type 'object'")
    return ToolSpec(
        name=codepilot_tool_name,
        description=description,
        risk=risk,
        side_effect=side_effect,
        external_impact=external_impact,
        reversibility=reversibility,
        input_schema=dict(tool.input_schema),
        parameters={},
        metadata={
            "source": "mcp",
            "mcp": True,
            "server_name": server.name,
            "mcp_tool_name": tool.name,
            "codepilot_tool_name": codepilot_tool_name,
            "transport": server.transport,
            "trust_level": server.trust_level,
            "descriptor_hash": descriptor_hash,
            "config_hash": config_hash,
            "risk_source": risk_source,
            "trusted_annotations": server.trusted_annotations,
        },
    )
