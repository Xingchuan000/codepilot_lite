from __future__ import annotations

import os
import json
import subprocess
import sys
from pathlib import Path

from typer.testing import CliRunner

from codepilot.cli import app
from tests.codepilot.tui_helpers import make_broken_run, make_mcp_run, make_policy_denied_run, make_success_run


runner = CliRunner()


def test_dashboard_static_index_and_detail(tmp_path: Path) -> None:
    make_success_run(tmp_path)
    make_mcp_run(tmp_path)

    result = runner.invoke(app, ["dashboard", "--runs-dir", str(tmp_path), "--limit", "5", "--static"])

    assert result.exit_code == 0
    assert "Run Dashboard" in result.stdout


def test_dashboard_json_subprocess_stdout_is_clean_json(tmp_path: Path) -> None:
    make_success_run(tmp_path)

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "codepilot.cli",
            "dashboard",
            "--runs-dir",
            str(tmp_path),
            "--json",
        ],
        cwd=Path(__file__).resolve().parents[2],
        env={**os.environ, "PYTHONPATH": "src"},
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0
    assert result.stdout.lstrip().startswith("{")
    assert "mini-swe-agent version" not in result.stdout
    assert str(tmp_path) not in result.stdout
    assert json.loads(result.stdout)["runs"]


def test_dashboard_static_subprocess_does_not_duplicate_render(tmp_path: Path) -> None:
    make_success_run(tmp_path)

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "codepilot.cli",
            "dashboard",
            "--runs-dir",
            str(tmp_path),
            "--limit",
            "1",
            "--static",
        ],
        cwd=Path(__file__).resolve().parents[2],
        env={**os.environ, "PYTHONPATH": "src"},
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0
    assert result.stdout.count("Run Dashboard") == 1
    assert "mini-swe-agent version" not in result.stdout
    assert str(tmp_path) not in result.stdout


def test_dashboard_json_outputs_parseable_index_and_detail(tmp_path: Path) -> None:
    make_success_run(tmp_path)
    make_mcp_run(tmp_path)

    index_result = runner.invoke(app, ["dashboard", "--runs-dir", str(tmp_path), "--limit", "5", "--json"])
    detail_result = runner.invoke(app, ["dashboard", "--runs-dir", str(tmp_path), "--run-id", "run-success", "--json"])

    assert json.loads(index_result.stdout)["runs"]
    assert json.loads(detail_result.stdout)["run"]["entry"]["run_id"] == "run-success"


def test_dashboard_json_subprocess_hides_absolute_changed_files(tmp_path: Path) -> None:
    run_dir = make_success_run(tmp_path)
    absolute_changed_file = tmp_path / "repo" / "src" / "calc.py"
    absolute_changed_file.parent.mkdir(parents=True, exist_ok=True)
    report = json.loads((run_dir / "report.json").read_text(encoding="utf-8"))
    report["changed_files"] = [str(absolute_changed_file)]
    (run_dir / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "codepilot.cli",
            "dashboard",
            "--runs-dir",
            str(tmp_path),
            "--run-id",
            "run-success",
            "--json",
        ],
        cwd=Path(__file__).resolve().parents[2],
        env={**os.environ, "PYTHONPATH": "src"},
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0
    assert str(tmp_path) not in result.stdout
    assert str(absolute_changed_file) not in result.stdout


def test_dashboard_json_subprocess_hides_absolute_timeline_metadata(tmp_path: Path) -> None:
    run_dir = make_success_run(tmp_path)
    absolute_metadata_path = tmp_path / "trace-secret" / "nested" / "config.yaml"
    absolute_metadata_path.parent.mkdir(parents=True, exist_ok=True)
    trace_lines = (run_dir / "trace.jsonl").read_text(encoding="utf-8").splitlines()
    trace_event = json.loads(trace_lines[2])
    trace_event["metadata"] = {
        "path": str(absolute_metadata_path),
        "nested": {"paths": [str(absolute_metadata_path)]},
    }
    trace_lines[2] = json.dumps(trace_event)
    (run_dir / "trace.jsonl").write_text("\n".join(trace_lines) + "\n", encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "codepilot.cli",
            "dashboard",
            "--runs-dir",
            str(tmp_path),
            "--run-id",
            "run-success",
            "--json",
        ],
        cwd=Path(__file__).resolve().parents[2],
        env={**os.environ, "PYTHONPATH": "src"},
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0
    assert str(tmp_path) not in result.stdout
    assert str(absolute_metadata_path) not in result.stdout
    assert "config.yaml" in result.stdout


def test_dashboard_detail_json_redacts_embedded_absolute_paths(tmp_path: Path) -> None:
    run_dir = make_success_run(tmp_path)
    inside_path = tmp_path / "repo" / "src" / "a.py"
    inside_path.parent.mkdir(parents=True, exist_ok=True)
    outside_path = Path("/etc/passwd")

    trace_lines = (run_dir / "trace.jsonl").read_text(encoding="utf-8").splitlines()
    trace_event = json.loads(trace_lines[4])
    trace_event["output_summary"] = f"changed {inside_path} and {outside_path}"
    trace_lines[4] = json.dumps(trace_event)
    (run_dir / "trace.jsonl").write_text("\n".join(trace_lines) + "\n", encoding="utf-8")

    report = json.loads((run_dir / "report.json").read_text(encoding="utf-8"))
    report["diff"]["summary"] = f"diff from {inside_path} to {outside_path}"
    (run_dir / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "codepilot.cli",
            "dashboard",
            "--runs-dir",
            str(tmp_path),
            "--run-id",
            "run-success",
            "--json",
        ],
        cwd=Path(__file__).resolve().parents[2],
        env={**os.environ, "PYTHONPATH": "src"},
        text=True,
        capture_output=True,
        check=False,
    )

    payload = json.loads(result.stdout)

    assert result.returncode == 0
    assert str(tmp_path) not in result.stdout
    assert str(inside_path) not in result.stdout
    assert "src/a.py" in result.stdout
    assert "passwd" in result.stdout
    assert payload["run"]["timeline"][4]["output_summary"] == "changed repo/src/a.py and passwd"
    assert payload["run"]["diff_summary"]["summary"] == "diff from repo/src/a.py to passwd"


def test_dashboard_detail_static_redacts_embedded_absolute_paths(tmp_path: Path) -> None:
    run_dir = make_success_run(tmp_path)
    inside_path = tmp_path / "repo" / "src" / "a.py"
    inside_path.parent.mkdir(parents=True, exist_ok=True)

    trace_lines = (run_dir / "trace.jsonl").read_text(encoding="utf-8").splitlines()
    trace_event = json.loads(trace_lines[4])
    trace_event["output_summary"] = f"changed {inside_path}"
    trace_lines[4] = json.dumps(trace_event)
    (run_dir / "trace.jsonl").write_text("\n".join(trace_lines) + "\n", encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "codepilot.cli",
            "dashboard",
            "--runs-dir",
            str(tmp_path),
            "--run-id",
            "run-success",
            "--static",
        ],
        cwd=Path(__file__).resolve().parents[2],
        env={**os.environ, "PYTHONPATH": "src"},
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0
    assert str(tmp_path) not in result.stdout
    assert str(inside_path) not in result.stdout
    assert "changed" in result.stdout


def test_dashboard_filters_by_status_and_run_type(tmp_path: Path) -> None:
    make_success_run(tmp_path)
    make_policy_denied_run(tmp_path)
    make_mcp_run(tmp_path)

    status_result = runner.invoke(app, ["dashboard", "--runs-dir", str(tmp_path), "--status", "success", "--json"])
    run_type_result = runner.invoke(app, ["dashboard", "--runs-dir", str(tmp_path), "--run-type", "mcp_demo", "--json"])

    assert [item["run_id"] for item in json.loads(status_result.stdout)["runs"]] == ["mcp-dashboard-demo", "run-success"]
    assert [item["run_id"] for item in json.loads(run_type_result.stdout)["runs"]] == ["mcp-dashboard-demo"]


def test_dashboard_reports_missing_runs_and_missing_run_id(tmp_path: Path) -> None:
    missing_runs = runner.invoke(app, ["dashboard", "--runs-dir", str(tmp_path / "missing"), "--json"])
    make_success_run(tmp_path)
    missing_run = runner.invoke(app, ["dashboard", "--runs-dir", str(tmp_path), "--run-id", "missing", "--json"])

    assert missing_runs.exit_code != 0
    assert "runs_dir does not exist" in missing_runs.stderr
    assert missing_run.exit_code != 0
    assert "Run not found" in missing_run.stderr


def test_dashboard_does_not_modify_artifacts_or_create_trace(tmp_path: Path) -> None:
    run_dir = make_success_run(tmp_path)
    mtimes_before = {path.name: path.stat().st_mtime for path in run_dir.iterdir() if path.is_file()}

    result = runner.invoke(app, ["dashboard", "--runs-dir", str(tmp_path), "--run-id", "run-success", "--static"])

    mtimes_after = {path.name: path.stat().st_mtime for path in run_dir.iterdir() if path.is_file()}

    assert result.exit_code == 0
    assert mtimes_before == mtimes_after
    assert not (run_dir / "dashboard.trace.jsonl").exists()


def test_dashboard_output_redacts_secret_like_content(tmp_path: Path) -> None:
    run_dir = make_mcp_run(tmp_path)
    result = runner.invoke(app, ["dashboard", "--runs-dir", str(tmp_path), "--run-id", run_dir.name, "--static"])

    assert result.exit_code == 0
    assert "secret token=abc" not in result.stdout
    assert "token=abc" not in result.stdout


def test_dashboard_rejects_low_watch_interval(tmp_path: Path) -> None:
    result = runner.invoke(app, ["dashboard", "--runs-dir", str(tmp_path), "--watch", "--watch-interval", "0.1"])

    assert result.exit_code != 0
    assert "watch-interval" in result.stderr


def test_dashboard_broken_run_does_not_break_index(tmp_path: Path) -> None:
    make_broken_run(tmp_path)
    make_success_run(tmp_path)

    result = runner.invoke(app, ["dashboard", "--runs-dir", str(tmp_path), "--json"])

    assert result.exit_code == 0
    assert "run-broken" in result.stdout
