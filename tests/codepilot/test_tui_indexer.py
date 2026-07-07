from __future__ import annotations

from pathlib import Path

import pytest

from codepilot.report.models import RunReport
from codepilot.tui.indexer import build_run_entry, build_run_index, load_report_json, load_trace_events, list_run_dirs
from tests.codepilot.tui_helpers import make_broken_run, make_mcp_run, make_policy_denied_run, make_success_run


def test_empty_runs_dir_returns_empty_list(tmp_path: Path) -> None:
    assert build_run_index(tmp_path) == []


def test_missing_runs_dir_raises_file_not_found() -> None:
    with pytest.raises(FileNotFoundError):
        build_run_index(Path("/does/not/exist"))


def test_build_run_entry_reads_success_run(tmp_path: Path) -> None:
    run_dir = make_success_run(tmp_path)

    entry = build_run_entry(run_dir)

    assert entry.status == "success"
    assert entry.run_type == "agent_run"
    assert entry.tool_call_count == 2
    assert entry.policy_denied_count == 0
    assert entry.test_status == "passed"
    assert entry.changed_files == ("src/calc.py",)
    assert entry.source_provenance["summary"] == "report_json"


def test_report_json_bad_falls_back_to_trace(tmp_path: Path) -> None:
    run_dir = make_success_run(tmp_path)
    (run_dir / "report.json").write_text("{bad json", encoding="utf-8")

    entry = build_run_entry(run_dir)

    assert entry.status == "success"
    assert any(item in {"bad_report_json", "bad_report_json_schema"} for item in entry.warnings)


def test_missing_trace_marks_unknown(tmp_path: Path) -> None:
    run_dir = tmp_path / "run-missing-trace"
    run_dir.mkdir()

    entry = build_run_entry(run_dir)

    assert entry.status == "unknown"
    assert "missing_trace" in entry.warnings


def test_bad_trace_json_does_not_crash(tmp_path: Path) -> None:
    run_dir = make_broken_run(tmp_path)

    entry = build_run_entry(run_dir)

    assert entry.status == "unknown"
    assert any(warning.startswith("bad_trace_json:") for warning in entry.warnings)


def test_build_run_index_orders_by_recent_update(tmp_path: Path) -> None:
    old_run = make_success_run(tmp_path, "run-old")
    new_run = make_success_run(tmp_path, "run-new")
    (new_run / "report.md").write_text("new", encoding="utf-8")

    entries = build_run_index(tmp_path)

    assert entries[0].run_id == "run-new"
    assert entries[1].run_id == "run-old"


def test_build_run_index_limit_and_filters(tmp_path: Path) -> None:
    make_success_run(tmp_path, "run-success")
    make_policy_denied_run(tmp_path, "run-policy-denied")
    make_mcp_run(tmp_path, "mcp-dashboard-demo")

    assert len(build_run_index(tmp_path, limit=2)) == 2
    assert [item.run_id for item in build_run_index(tmp_path, status="success")] == ["mcp-dashboard-demo", "run-success"]
    assert [item.run_id for item in build_run_index(tmp_path, run_type="mcp_demo")] == ["mcp-dashboard-demo"]


def test_build_run_entry_detects_mcp_and_issue_and_pr_artifacts(tmp_path: Path) -> None:
    mcp_run = make_mcp_run(tmp_path)
    issue_run = tmp_path / "issue-demo"
    issue_run.mkdir()
    (issue_run / "trace.jsonl").write_text('{"schema_version":"trace.v1","run_id":"issue-demo","step":1,"event_type":"run_start","timestamp":"2026-01-01T00:00:00+00:00","metadata":{"task":"Issue"}}\n', encoding="utf-8")
    (issue_run / "issue.json").write_text("{}", encoding="utf-8")

    mcp_entry = build_run_entry(mcp_run)
    issue_entry = build_run_entry(issue_run)

    assert mcp_entry.has_mcp is True
    assert mcp_entry.run_type == "mcp_demo"
    assert issue_entry.has_issue_artifacts is True
    assert issue_entry.run_type == "issue_workflow"


def test_source_provenance_uses_trace_extraction_when_report_json_missing(tmp_path: Path) -> None:
    run_dir = make_success_run(tmp_path)
    (run_dir / "report.json").unlink()

    entry = build_run_entry(run_dir)

    assert entry.source_provenance["summary"] == "trace_extraction"
    assert entry.source_provenance["tests"] in {"trace_extraction", "trace.run_tests"}
    assert entry.source_provenance["diff"] in {"trace_extraction", "trace.git_diff", "unknown"}


def test_load_report_and_trace_helpers(tmp_path: Path) -> None:
    run_dir = make_success_run(tmp_path)

    report, warnings = load_report_json(run_dir)
    events, trace_warnings = load_trace_events(run_dir)

    assert isinstance(report, RunReport)
    assert warnings == []
    assert len(events) >= 1
    assert trace_warnings == []


def test_list_run_dirs_rejects_non_directory(tmp_path: Path) -> None:
    file_path = tmp_path / "runs"
    file_path.write_text("x", encoding="utf-8")

    with pytest.raises(NotADirectoryError):
        list_run_dirs(file_path)
