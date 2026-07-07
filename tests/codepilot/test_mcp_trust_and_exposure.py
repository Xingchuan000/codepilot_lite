from __future__ import annotations

from codepilot.mcp.config import load_mcp_config
from codepilot.mcp.exposure import should_expose_mcp_tool
from codepilot.mcp.fake_client import FakeMCPClient
from codepilot.mcp.registry import MCPToolRegistry
from codepilot.mcp.risk import classify_mcp_tool
from codepilot.mcp.models import MCPToolInfo


def test_untrusted_server_exposes_only_read_only_without_allowlist() -> None:
    server = load_mcp_config("examples/mcp/fake_untrusted_annotations_mcp.json")[0]
    read_tool = MCPToolInfo(server_name=server.name, name="read_file", annotations={"readOnlyHint": True}, side_effect_hint="read_only")
    write_tool = MCPToolInfo(server_name=server.name, name="write_file", side_effect_hint="local_write")
    run_tool = MCPToolInfo(server_name=server.name, name="run_command", side_effect_hint="local_exec")
    fetch_tool = MCPToolInfo(server_name=server.name, name="fetch_url", side_effect_hint="network")
    read_spec = classify_mcp_tool(read_tool, server=server)
    write_spec = classify_mcp_tool(write_tool, server=server)
    run_spec = classify_mcp_tool(run_tool, server=server)
    fetch_spec = classify_mcp_tool(fetch_tool, server=server)
    assert should_expose_mcp_tool(server, read_tool, read_spec)[0] is True
    assert should_expose_mcp_tool(server, write_tool, write_spec)[0] is False
    assert should_expose_mcp_tool(server, run_tool, run_spec)[0] is False
    assert should_expose_mcp_tool(server, fetch_tool, fetch_spec)[0] is False


def test_trusted_server_allowlist_can_expose_more_tools() -> None:
    server = load_mcp_config("examples/mcp/fake_filesystem_mcp.json")[0]
    registry = MCPToolRegistry.from_config("examples/mcp/fake_filesystem_mcp.json", client=FakeMCPClient())
    assert registry.find_spec("mcp.filesystem.read_file") is not None
    assert registry.find_spec("mcp.filesystem.write_file") is not None
    assert registry.find_spec("mcp.filesystem.read_file").metadata["exposed_to_agent"] is True
    assert registry.find_spec("mcp.filesystem.write_file").metadata["exposed_to_agent"] is False
    assert server.trust_level == "fake"
