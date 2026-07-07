from __future__ import annotations

from pathlib import Path

from codepilot.mcp.fake_client import FakeMCPClient
from codepilot.mcp.registry import MCPToolRegistry
from codepilot.policy import PolicyChecker, PolicyContext
from codepilot.router import ToolAction, ToolRouter


def test_router_executes_mcp_tools_and_respects_policy(tmp_path) -> None:
    client = FakeMCPClient()
    registry = MCPToolRegistry.from_config("examples/mcp/fake_filesystem_mcp.json", client=client)
    router = ToolRouter.from_runs_dir(
        runs_dir=tmp_path / "runs",
        run_id="mcp-router",
        policy_checker=PolicyChecker.default(extra_tool_specs={spec.name: spec for spec in registry.list_specs()}),
        policy_context=PolicyContext(mode="build", approved=False),
        external_tool_registry=registry,
    )

    allowed = router.route(ToolAction(tool_name="mcp.filesystem.read_file", arguments={"path": "README.md"}))
    denied = router.route(ToolAction(tool_name="mcp.filesystem.write_file", arguments={"path": "demo.txt", "content": "hello"}))
    external = router.route(ToolAction(tool_name="mcp.filesystem.publish_release", arguments={"version": "1.0.0"}))
    approved = ToolRouter.from_runs_dir(
        runs_dir=tmp_path / "runs2",
        run_id="mcp-router-approved",
        policy_checker=PolicyChecker.default(extra_tool_specs={spec.name: spec for spec in registry.list_specs()}),
        policy_context=PolicyContext(mode="build", approved=True),
        external_tool_registry=registry,
    ).route(ToolAction(tool_name="mcp.filesystem.write_file", arguments={"path": "demo.txt", "content": "hello"}))

    assert allowed.success is True
    assert denied.success is False
    assert external.success is False
    assert approved.success is True
    assert len(client.calls) == 2
    assert Path(allowed.trace_path).read_text(encoding="utf-8").count('"event_type":"tool_call"') >= 1
