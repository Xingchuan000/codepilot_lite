from __future__ import annotations

from typer.testing import CliRunner

from codepilot.cli import app

runner = CliRunner()


def test_cli_mcp_tools_and_call() -> None:
    tools_result = runner.invoke(app, ["mcp-tools", "--mcp-config", "examples/mcp/fake_filesystem_mcp.json"])
    assert tools_result.exit_code == 0
    assert "mcp.filesystem.read_file" in tools_result.stdout

    call_result = runner.invoke(
        app,
        [
            "mcp-call",
            "mcp.filesystem.read_file",
            '{"path":"README.md"}',
            "--mcp-config",
            "examples/mcp/fake_filesystem_mcp.json",
            "--run-id",
            "test-cli-mcp",
        ],
    )
    assert call_result.exit_code == 0
    assert "Success: true" in call_result.stdout


def test_cli_mcp_call_ask_requires_approval() -> None:
    result = runner.invoke(
        app,
        [
            "mcp-call",
            "mcp.filesystem.write_file",
            '{"path":"demo.txt","content":"hello"}',
            "--mcp-config",
            "examples/mcp/fake_filesystem_mcp.json",
            "--run-id",
            "test-cli-mcp-ask",
        ],
    )
    assert result.exit_code == 1
