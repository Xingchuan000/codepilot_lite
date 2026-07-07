from __future__ import annotations

from codepilot.mcp.models import MCPServerConfig, MCPToolInfo
from codepilot.mcp.trace import build_mcp_descriptor_hash


def test_descriptor_hash_changes_with_descriptor_fields() -> None:
    server = MCPServerConfig(name="filesystem")
    tool = MCPToolInfo(server_name="filesystem", name="read_file", description="Read")
    base = build_mcp_descriptor_hash(tool)
    assert base == build_mcp_descriptor_hash(tool)
    assert build_mcp_descriptor_hash(tool.model_copy(update={"description": "Read more"})) != base
    assert build_mcp_descriptor_hash(tool.model_copy(update={"annotations": {"readOnlyHint": True}})) != base
