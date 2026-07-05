import json
from pathlib import Path

from typer.testing import CliRunner

from codepilot.cli import app


runner = CliRunner()


def _write_trace(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_cli_report_from_trace(tmp_path: Path) -> None:
    trace_path = tmp_path / "runs" / "run-test" / "trace.jsonl"
    _write_trace(trace_path, ['{"run_id":"run-test","step":1,"event_type":"run_end","success":true,"output_summary":"done"}'])

    result = runner.invoke(app, ["report", "--trace", str(trace_path), "--overwrite"])

    assert result.exit_code == 0
    assert "Report written:" in result.stdout
    assert "Status: success" in result.stdout
    assert trace_path.with_name("report.md").exists()


def test_cli_report_from_run_id(tmp_path: Path) -> None:
    trace_path = tmp_path / "runs" / "run-test" / "trace.jsonl"
    _write_trace(trace_path, ['{"run_id":"run-test","step":1,"event_type":"run_end","success":true,"output_summary":"done"}'])

    result = runner.invoke(app, ["report", "--run-id", "run-test", "--runs-dir", str(tmp_path / "runs"), "--overwrite"])

    assert result.exit_code == 0
    assert "Report written:" in result.stdout


def test_cli_report_supports_output_and_json(tmp_path: Path) -> None:
    trace_path = tmp_path / "trace.jsonl"
    _write_trace(trace_path, ['{"run_id":"run-test","step":1,"event_type":"run_end","success":true,"output_summary":"done"}'])

    output_path = tmp_path / "custom" / "report.md"
    result = runner.invoke(app, ["report", "--trace", str(trace_path), "--output", str(output_path), "--json", "--overwrite"])

    assert result.exit_code == 0
    assert output_path.exists()
    assert output_path.with_suffix(".json").exists()
    assert json.loads(output_path.with_suffix(".json").read_text(encoding="utf-8"))["run_id"] == "run-test"


def test_cli_report_rejects_duplicate_selector(tmp_path: Path) -> None:
    result = runner.invoke(app, ["report", "--trace", str(tmp_path / "trace.jsonl"), "--run-id", "run-test"])

    assert result.exit_code != 0
    assert "Provide exactly one of --trace or --run-id." in result.stderr


def test_cli_report_rejects_missing_selector() -> None:
    result = runner.invoke(app, ["report"])

    assert result.exit_code != 0
    assert "Provide exactly one of --trace or --run-id." in result.stderr


def test_cli_report_rejects_missing_trace(tmp_path: Path) -> None:
    result = runner.invoke(app, ["report", "--trace", str(tmp_path / "missing.jsonl")])

    assert result.exit_code != 0
    assert "Trace file does not exist" in result.stderr


def test_cli_report_rejects_existing_output_without_overwrite(tmp_path: Path) -> None:
    trace_path = tmp_path / "trace.jsonl"
    _write_trace(trace_path, ['{"run_id":"run-test","step":1,"event_type":"run_end","success":true}'])
    (trace_path.parent / "report.md").write_text("exists", encoding="utf-8")

    result = runner.invoke(app, ["report", "--trace", str(trace_path)])

    assert result.exit_code != 0
    assert "Use --overwrite to replace it." in result.stderr
