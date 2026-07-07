from __future__ import annotations

from codepilot.mcp.registry import MCPToolRegistry


def test_mcp_registry_loads_fake_tools() -> None:
    registry = MCPToolRegistry.from_config("examples/mcp/fake_filesystem_mcp.json")
    assert registry.has_tool("mcp.filesystem.read_file")
    assert registry.has_tool("mcp.filesystem.write_file")
    assert any(spec.name == "mcp.filesystem.read_file" for spec in registry.list_exposed_specs())
    assert all(spec.name != "mcp.filesystem.write_file" for spec in registry.list_exposed_specs())


def test_mcp_registry_fake_call_returns_tool_result() -> None:
    registry = MCPToolRegistry.from_config("examples/mcp/fake_filesystem_mcp.json")
    result = registry.call_tool("mcp.filesystem.read_file", {"path": "README.md"})
    assert result.success is True
    assert "fake content" in result.output
