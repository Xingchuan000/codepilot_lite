from __future__ import annotations

from codepilot.mcp.models import MCPServerConfig, MCPToolInfo
from codepilot.tools.base import DefaultPermission, ToolSideEffect, ToolSpec


def should_expose_mcp_tool(
    server: MCPServerConfig,
    tool: MCPToolInfo,
    spec: ToolSpec,
    *,
    index: int = 0,
) -> tuple[bool, str | None]:
    if not server.enabled:
        return False, "server_disabled"
    if not server.expose_to_agent:
        return False, "server_expose_to_agent_false"
    if tool.name in server.tool_denylist:
        return False, "tool_denylisted"
    if spec.default_permission == DefaultPermission.DENY:
        return False, "default_permission_deny"
    if spec.side_effect == ToolSideEffect.EXTERNAL:
        return False, "external_side_effect_not_exposed"
    if server.trust_level in {"local_untrusted", "remote_untrusted"}:
        if spec.side_effect == ToolSideEffect.NONE:
            if server.tool_allowlist and tool.name not in server.tool_allowlist:
                return False, "not_in_tool_allowlist"
            return True, None
        if not server.tool_allowlist:
            return False, "untrusted_non_readonly_not_exposed"
        if tool.name not in server.tool_allowlist:
            return False, "untrusted_allowlist_required_for_non_readonly"
        return True, None
    if server.require_tool_allowlist and not server.tool_allowlist:
        return False, "allowlist_required_empty"
    if server.require_tool_allowlist and tool.name not in server.tool_allowlist:
        return False, "not_in_tool_allowlist"
    if index >= server.max_tools_to_expose:
        return False, "too_many_tools"
    return True, None


def mark_exposure_on_spec(spec: ToolSpec, *, exposed: bool, reason: str | None) -> ToolSpec:
    metadata = dict(spec.metadata)
    metadata.update({"exposed_to_agent": exposed, "exposure_reason": reason})
    return spec.model_copy(update={"metadata": metadata})
