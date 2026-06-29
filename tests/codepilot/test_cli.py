from pathlib import Path

from typer.testing import CliRunner

from codepilot.cli import app


runner = CliRunner()


def test_tool_list_files_returns_json(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("hi\n", encoding="utf-8")

    result = runner.invoke(app, ["tool", "list_files", f'{{"repo":"{tmp_path}","path":".","max_depth":1}}'])

    assert result.exit_code == 0
    assert '"success": true' in result.stdout
    assert '"entries_returned"' in result.stdout


def test_tool_output_is_indented_json(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("hi\n", encoding="utf-8")

    result = runner.invoke(app, ["tool", "list_files", f'{{"repo":"{tmp_path}","path":".","max_depth":1}}'])

    assert result.exit_code == 0
    # indent=2 输出应包含换行和前置空格，不是单行 JSON
    assert "\n" in result.stdout
    assert '  "' in result.stdout


def test_tool_invalid_json_returns_non_zero() -> None:
    result = runner.invoke(app, ["tool", "list_files", "{bad json}"])

    assert result.exit_code != 0
    assert "JSON 解析失败" in result.stderr


def test_tool_unknown_returns_failure_json() -> None:
    result = runner.invoke(app, ["tool", "unknown", '{"repo":"."}'])

    assert result.exit_code == 0
    assert '"success": false' in result.stdout
    assert "Unknown tool" in result.stdout
