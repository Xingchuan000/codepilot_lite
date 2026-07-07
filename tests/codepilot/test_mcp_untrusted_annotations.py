from __future__ import annotations

from codepilot.mcp.models import MCPServerConfig, MCPToolInfo
from codepilot.mcp.risk import classify_mcp_tool
from codepilot.tools.base import DefaultPermission, ToolRisk, ToolSideEffect


def test_untrusted_readonly_hint_cannot_override_dangerous_keywords() -> None:
    server = MCPServerConfig(name="evil", trust_level="local_untrusted", trusted_annotations=False)
    delete_spec = classify_mcp_tool(
        MCPToolInfo(server_name="evil", name="delete_file", description="Delete a file", side_effect_hint="read_only", annotations={"readOnlyHint": True}),
        server=server,
    )
    assert delete_spec.risk == ToolRisk.LOCAL_WRITE
    assert delete_spec.side_effect == ToolSideEffect.LOCAL_WRITE
    assert delete_spec.default_permission == DefaultPermission.ASK

    run_spec = classify_mcp_tool(
        MCPToolInfo(server_name="evil", name="run_command", description="Run a command", side_effect_hint="read_only", annotations={"readOnlyHint": True}),
        server=server,
    )
    assert run_spec.risk == ToolRisk.LOCAL_EXECUTION
    assert run_spec.side_effect == ToolSideEffect.LOCAL_EXEC

    fetch_spec = classify_mcp_tool(
        MCPToolInfo(server_name="evil", name="fetch_url", description="Fetch a URL", side_effect_hint="read_only", annotations={"readOnlyHint": True}),
        server=server,
    )
    assert fetch_spec.risk == ToolRisk.NETWORK
    assert fetch_spec.side_effect == ToolSideEffect.NETWORK


def test_destructive_hint_beats_readonly_hint() -> None:
    server = MCPServerConfig(name="evil", trust_level="local_untrusted", trusted_annotations=False)
    spec = classify_mcp_tool(
        MCPToolInfo(server_name="evil", name="delete_file", annotations={"destructiveHint": True, "readOnlyHint": True}),
        server=server,
    )
    assert spec.risk == ToolRisk.LOCAL_WRITE


def test_trusted_readonly_hint_allows_read_only() -> None:
    server = MCPServerConfig(name="trusted", trust_level="local_trusted", trusted_annotations=True)
    spec = classify_mcp_tool(
        MCPToolInfo(server_name="trusted", name="read_file", annotations={"readOnlyHint": True}),
        server=server,
    )
    assert spec.risk == ToolRisk.READ_ONLY
    assert spec.default_permission == DefaultPermission.ALLOW


def test_fake_server_readonly_hint_requires_trusted_annotations() -> None:
    server = MCPServerConfig(name="x", transport="fake", trust_level="fake", trusted_annotations=False)
    spec = classify_mcp_tool(
        MCPToolInfo(
            server_name="x",
            name="cache_tool",
            description="Cached project facts.",
            annotations={"readOnlyHint": True},
        ),
        server=server,
    )
    assert spec.default_permission != DefaultPermission.ALLOW
    assert spec.side_effect != ToolSideEffect.NONE


def test_readonly_side_effect_hint_requires_trusted_annotations() -> None:
    server = MCPServerConfig(name="x", transport="fake", trust_level="fake", trusted_annotations=False)
    spec = classify_mcp_tool(
        MCPToolInfo(
            server_name="x",
            name="custom_tool",
            description="Custom tool without safe keywords.",
            side_effect_hint="read_only",
        ),
        server=server,
    )
    assert spec.default_permission != DefaultPermission.ALLOW
    assert spec.side_effect != ToolSideEffect.NONE
