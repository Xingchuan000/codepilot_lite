from __future__ import annotations

from codepilot.mcp.registry import MCPToolRegistry
from codepilot.policy import PolicyChecker, PolicyContext
from codepilot.router.actions import ToolAction


def test_policy_checker_accepts_mcp_extra_specs() -> None:
    registry = MCPToolRegistry.from_config("examples/mcp/fake_filesystem_mcp.json")
    checker = PolicyChecker.default(extra_tool_specs={spec.name: spec for spec in registry.list_specs()})

    read = checker.check(ToolAction(tool_name="mcp.filesystem.read_file", arguments={"path": "README.md"}), context=PolicyContext(mode="build"))
    write = checker.check(ToolAction(tool_name="mcp.filesystem.write_file", arguments={"path": "demo.txt"}), context=PolicyContext(mode="build"))
    write_read_only = checker.check(ToolAction(tool_name="mcp.filesystem.write_file", arguments={"path": "demo.txt"}), context=PolicyContext(mode="read_only"))
    deny = checker.check(ToolAction(tool_name="mcp.filesystem.publish_release", arguments={"version": "1.0.0"}), context=PolicyContext(mode="build", approved=True))
    env_deny = checker.check(ToolAction(tool_name="mcp.filesystem.read_file", arguments={"path": ".env"}), context=PolicyContext(mode="build"))
    cmd_deny = checker.check(ToolAction(tool_name="mcp.filesystem.run_command", arguments={"command": "rm -rf ."}), context=PolicyContext(mode="build"))

    assert read.decision == "allow"
    assert write.decision == "ask"
    assert write_read_only.decision == "deny"
    assert deny.decision == "deny"
    assert env_deny.decision == "deny"
    assert cmd_deny.decision == "deny"
    assert read.metadata["server_name"] == "filesystem"
    assert "descriptor_hash" in read.metadata
