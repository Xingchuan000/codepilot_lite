from __future__ import annotations

from codepilot.mcp.models import MCPServerConfig, MCPToolInfo
from codepilot.mcp.risk import classify_mcp_tool
from codepilot.tools.base import DefaultPermission, ToolRisk, ToolSideEffect


def test_mcp_risk_classification_for_fake_read_only() -> None:
    server = MCPServerConfig(name="filesystem", trust_level="fake", trusted_annotations=True)
    spec = classify_mcp_tool(MCPToolInfo(server_name="filesystem", name="read_file", annotations={"readOnlyHint": True}), server=server)
    assert spec.risk == ToolRisk.READ_ONLY
    assert spec.side_effect == ToolSideEffect.NONE
    assert spec.default_permission == DefaultPermission.ALLOW


def test_mcp_risk_classification_for_common_tools() -> None:
    server = MCPServerConfig(name="filesystem", trust_level="fake", trusted_annotations=True)
    assert classify_mcp_tool(MCPToolInfo(server_name="filesystem", name="write_file"), server=server).risk == ToolRisk.LOCAL_WRITE
    assert classify_mcp_tool(MCPToolInfo(server_name="filesystem", name="run_command"), server=server).risk == ToolRisk.LOCAL_EXECUTION
    assert classify_mcp_tool(MCPToolInfo(server_name="filesystem", name="fetch_url"), server=server).risk == ToolRisk.NETWORK
    assert classify_mcp_tool(MCPToolInfo(server_name="filesystem", name="publish_release"), server=server).risk == ToolRisk.EXTERNAL_SIDE_EFFECT


def test_mcp_risk_unknown_fallback_is_not_allow() -> None:
    server = MCPServerConfig(name="filesystem", trust_level="local_untrusted")
    spec = classify_mcp_tool(MCPToolInfo(server_name="filesystem", name="mystery"), server=server)
    assert spec.default_permission != DefaultPermission.ALLOW
