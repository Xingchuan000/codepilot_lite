from __future__ import annotations

from codepilot.agent.runner import run_agent_task


def test_agent_run_with_fake_mcp_config() -> None:
    result = run_agent_task(
        task="Use MCP to read README and summarize it",
        repo=".",
        fake_actions="examples/mcp/fake_actions_mcp_read_file.jsonl",
        mcp_config="examples/mcp/fake_filesystem_mcp.json",
        approve=True,
        runs_dir="runs",
        run_id="mcp-agent-loop-test",
    )
    assert result.success is True
    assert result.trace_path is not None
