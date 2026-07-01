import json
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


def test_tool_rejects_direct_replace_range_without_unsafe_direct(tmp_path: Path) -> None:
    (tmp_path / "demo.py").write_text("a\nb\n", encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "tool",
            "replace_range",
            json.dumps(
                {
                    "repo": str(tmp_path),
                    "path": "demo.py",
                    "start_line": 2,
                    "end_line": 2,
                    "replacement": "x\n",
                }
            ),
        ],
    )

    assert result.exit_code != 0
    assert "Direct tool execution is disabled" in result.stderr
    assert (tmp_path / "demo.py").read_text(encoding="utf-8") == "a\nb\n"


def test_tool_allows_direct_replace_range_with_unsafe_direct(tmp_path: Path) -> None:
    (tmp_path / "demo.py").write_text("a\nb\n", encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "tool",
            "replace_range",
            json.dumps(
                {
                    "repo": str(tmp_path),
                    "path": "demo.py",
                    "start_line": 2,
                    "end_line": 2,
                    "replacement": "x\n",
                }
            ),
            "--unsafe-direct",
        ],
    )

    assert result.exit_code == 0
    assert (tmp_path / "demo.py").read_text(encoding="utf-8") == "a\nx\n"


def test_tool_rejects_direct_apply_patch_without_unsafe_direct(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        [
            "tool",
            "apply_patch",
            json.dumps(
                {
                    "repo": str(tmp_path),
                    "patch": "diff --git a/src/demo.py b/src/demo.py\n--- /dev/null\n+++ b/src/demo.py\n@@ -0,0 +1 @@\n+new\n",
                }
            ),
        ],
    )

    assert result.exit_code != 0
    assert "Direct tool execution is disabled" in result.stderr


def test_tool_allows_read_only_tools_without_unsafe_direct(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("hi\n", encoding="utf-8")

    result = runner.invoke(
        app,
        ["tool", "read_file", json.dumps({"repo": str(tmp_path), "path": "a.txt", "start_line": 1, "end_line": 1})],
    )

    assert result.exit_code == 0
    assert '"success": true' in result.stdout


def test_tool_trace_writes_trace_jsonl(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("hi\n", encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "tool",
            "list_files",
            f'{{"repo":"{tmp_path}","path":".","max_depth":1}}',
            "--trace",
            "--runs-dir",
            str(tmp_path / "runs"),
            "--run-id",
            "run-test",
        ],
    )

    assert result.exit_code == 0
    assert "Trace written to:" in result.stdout

    trace_path = tmp_path / "runs" / "run-test" / "trace.jsonl"
    assert trace_path.exists()
    assert json.loads(trace_path.read_text(encoding="utf-8").splitlines()[0])["event_type"] == "tool_call"


def test_route_writes_route_result_and_trace(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("hi\n", encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "route",
            f'{{"tool_name":"list_files","arguments":{{"repo":"{tmp_path}","path":".","max_depth":1}}}}',
            "--runs-dir",
            str(tmp_path / "runs"),
            "--run-id",
            "run-test",
        ],
    )

    assert result.exit_code == 0
    assert '"success": true' in result.stdout
    assert "Trace written to:" in result.stdout
    lines = (tmp_path / "runs" / "run-test" / "trace.jsonl").read_text(encoding="utf-8").splitlines()
    assert [json.loads(line)["event_type"] for line in lines] == ["policy_decision", "tool_call"]
    assert json.loads(lines[0])["policy_decision"] == "allow"


def test_route_default_policy_denies_sensitive_path(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("SECRET=demo\n", encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "route",
            f'{{"tool_name":"read_file","arguments":{{"repo":"{tmp_path}","path":".env"}}}}',
            "--runs-dir",
            str(tmp_path / "runs"),
            "--run-id",
            "run-test",
        ],
    )

    assert result.exit_code == 0
    assert '"policy_decision": "deny"' in result.stdout
    lines = (tmp_path / "runs" / "run-test" / "trace.jsonl").read_text(encoding="utf-8").splitlines()
    assert [json.loads(line)["event_type"] for line in lines] == ["policy_decision"]


def test_route_default_policy_requires_approval_without_approve(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        [
            "route",
            f'{{"tool_name":"run_shell","arguments":{{"repo":"{tmp_path}","command":"echo hi"}}}}',
            "--runs-dir",
            str(tmp_path / "runs"),
            "--run-id",
            "run-test",
        ],
    )

    assert result.exit_code == 0
    assert '"requires_approval": true' in result.stdout
    lines = (tmp_path / "runs" / "run-test" / "trace.jsonl").read_text(encoding="utf-8").splitlines()
    assert [json.loads(line)["event_type"] for line in lines] == ["policy_decision"]


def test_route_default_policy_approve_executes_shell(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        [
            "route",
            f'{{"tool_name":"run_shell","arguments":{{"repo":"{tmp_path}","command":"echo hi"}}}}',
            "--runs-dir",
            str(tmp_path / "runs"),
            "--run-id",
            "run-test",
            "--approve",
        ],
    )

    assert result.exit_code == 0
    assert '"policy_decision": "ask"' in result.stdout
    assert '"approved": true' in result.stdout
    lines = (tmp_path / "runs" / "run-test" / "trace.jsonl").read_text(encoding="utf-8").splitlines()
    assert [json.loads(line)["event_type"] for line in lines] == ["policy_decision", "tool_call"]


def test_route_default_policy_read_only_denies_shell(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        [
            "route",
            f'{{"tool_name":"run_shell","arguments":{{"repo":"{tmp_path}","command":"echo hi"}}}}',
            "--runs-dir",
            str(tmp_path / "runs"),
            "--run-id",
            "run-test",
            "--policy-mode",
            "read_only",
        ],
    )

    assert result.exit_code == 0
    assert '"policy_decision": "deny"' in result.stdout
    lines = (tmp_path / "runs" / "run-test" / "trace.jsonl").read_text(encoding="utf-8").splitlines()
    assert [json.loads(line)["event_type"] for line in lines] == ["policy_decision"]


def test_route_no_policy_preserves_original_behavior(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        [
            "route",
            f'{{"tool_name":"run_shell","arguments":{{"repo":"{tmp_path}","command":"echo hi"}}}}',
            "--runs-dir",
            str(tmp_path / "runs"),
            "--run-id",
            "run-test",
            "--no-policy",
        ],
    )

    assert result.exit_code == 0
    lines = (tmp_path / "runs" / "run-test" / "trace.jsonl").read_text(encoding="utf-8").splitlines()
    assert [json.loads(line)["event_type"] for line in lines] == ["tool_call"]


def test_route_invalid_json_returns_non_zero() -> None:
    result = runner.invoke(app, ["route", "{bad json}"])

    assert result.exit_code != 0
    assert "JSON 解析失败" in result.stderr


def test_route_invalid_schema_returns_non_zero() -> None:
    result = runner.invoke(app, ["route", '{"arguments": {}}'])

    assert result.exit_code != 0
    assert "ToolAction 校验失败" in result.stderr
