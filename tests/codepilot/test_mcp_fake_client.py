from __future__ import annotations

from codepilot.mcp.fake_client import FakeMCPClient
from codepilot.mcp.models import MCPCallRequest, MCPServerConfig


def test_fake_client_lists_and_calls_tools() -> None:
    client = FakeMCPClient()
    server = MCPServerConfig(name="filesystem", trust_level="fake", trusted_annotations=True)
    tools = client.list_tools(server)
    assert {tool.name for tool in tools} >= {"read_file", "search", "write_file", "run_command", "fetch_url", "publish_release"}

    result = client.call_tool(MCPCallRequest(server_name="filesystem", tool_name="read_file", arguments={"path": "README.md"}))
    assert result.success is True
    assert "fake content for README.md" in result.content
    assert len(client.calls) == 1


def test_fake_client_unknown_tool() -> None:
    client = FakeMCPClient()
    server = MCPServerConfig(name="filesystem")
    assert client.list_tools(server)
    result = client.call_tool(MCPCallRequest(server_name="filesystem", tool_name="nope"))
    assert result.success is False
    assert "Unknown fake MCP tool" in result.error
