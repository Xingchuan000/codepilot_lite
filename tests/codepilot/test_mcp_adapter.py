from __future__ import annotations

from codepilot.mcp.adapter import mcp_result_to_tool_result
from codepilot.mcp.models import MCPCallResult, MCPServerConfig, MCPToolInfo
from codepilot.tools.base import DefaultPermission, ToolResult, ToolRisk, ToolSideEffect, ToolSpec
from codepilot.tools.registry import call_external_tool_traced
from codepilot.trace.logger import TraceLogger


class _FakeRegistry:
    def __init__(self) -> None:
        self.spec = ToolSpec(
            name="mcp.filesystem.read_file",
            description="Read fake files.",
            risk=ToolRisk.READ_ONLY,
            side_effect=ToolSideEffect.NONE,
            default_permission=DefaultPermission.ALLOW,
            metadata={"descriptor_hash": "abc", "source": "mcp"},
        )

    def find_spec(self, name: str):  # noqa: ANN201
        return self.spec

    def has_tool(self, name: str) -> bool:
        return True

    def call_tool(self, name: str, arguments: dict[str, str]) -> ToolResult:  # noqa: ANN201
        return ToolResult(
            success=True,
            output="token=[REDACTED] password=[REDACTED]",
            metadata={"mcp": False, "server_name": "spoofed", "descriptor_hash": "spoofed", "mcp_result_metadata": {"token": "[REDACTED]"}},
        )


def test_mcp_adapter_keeps_core_metadata_and_namespaces_server_metadata() -> None:
    server = MCPServerConfig(name="filesystem", trust_level="fake", trusted_annotations=True)
    tool = MCPToolInfo(server_name="filesystem", name="read_file", annotations={"readOnlyHint": True}, descriptor_hash="abc")
    result = mcp_result_to_tool_result(
        MCPCallResult(
            success=True,
            content="token=abc password=xyz",
            structured_content={"token": "abc"},
            metadata={"mcp": False, "server_name": "spoofed", "descriptor_hash": "spoofed"},
        ),
        server=server,
        tool=tool,
        codepilot_tool_name="mcp.filesystem.read_file",
        max_output_chars=200,
    )
    assert result.metadata["mcp"] is True
    assert result.metadata["server_name"] == "filesystem"
    assert result.metadata["descriptor_hash"] == "abc"
    assert result.metadata["mcp_result_metadata"]["mcp"] is False
    assert result.metadata["mcp_result_metadata"]["server_name"] == "spoofed"
    assert result.metadata["mcp_result_metadata"]["descriptor_hash"] == "spoofed"
    assert "abc" not in result.output
    assert "xyz" not in result.output


def test_external_trace_redacts_output_preview(tmp_path) -> None:
    logger = TraceLogger(runs_dir=tmp_path / "runs", run_id="mcp-trace")
    result = call_external_tool_traced(
        "mcp.filesystem.read_file",
        external_registry=_FakeRegistry(),
        trace_logger=logger,
        path="README.md",
    )
    trace = logger.trace_path.read_text(encoding="utf-8")
    assert result.success is True
    assert "token=abc" not in trace
    assert "password=xyz" not in trace
    assert "mcp_result_metadata" in trace
