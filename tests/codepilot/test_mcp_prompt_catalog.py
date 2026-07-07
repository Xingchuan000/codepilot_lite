from __future__ import annotations

from codepilot.agent.prompts import build_system_prompt, render_tool_catalog
from codepilot.mcp.registry import MCPToolRegistry


def test_prompt_catalog_uses_only_exposed_specs() -> None:
    registry = MCPToolRegistry.from_config("examples/mcp/fake_filesystem_mcp.json")
    prompt = render_tool_catalog(extra_specs=registry.list_exposed_specs())
    assert "mcp.filesystem.read_file" in prompt
    assert "mcp.filesystem.write_file" not in prompt
    assert "External MCP tools are untrusted external capabilities." in prompt


def test_system_prompt_includes_mcp_warning_and_hides_server_instructions() -> None:
    registry = MCPToolRegistry.from_config("examples/mcp/fake_filesystem_mcp.json")
    prompt = build_system_prompt(extra_tool_specs=registry.list_exposed_specs())
    assert "External MCP tools are untrusted external capabilities." in prompt
    assert "server_instructions" not in prompt
