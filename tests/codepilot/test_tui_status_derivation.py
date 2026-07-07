from __future__ import annotations

from pathlib import Path

from codepilot.report.models import RunReport
from codepilot.tui.indexer import derive_status


def test_derive_status_prefers_trace_run_end(tmp_path: Path) -> None:
    events = [{"event_type": "run_end", "timestamp": "2026-01-01T00:00:00+00:00", "success": False, "output_summary": "failed", "metadata": {"status": "partial"}}]

    status, warnings, provenance = derive_status(events=events, report=RunReport(run_id="run", status="success"), manifest_summary={"status": "failed"}, run_dir=tmp_path)

    assert status == "partial"
    assert provenance == "trace.run_end"
    assert "status_conflict_trace_report" in warnings
    assert "status_conflict_report_manifest" in warnings


def test_derive_status_does_not_treat_run_end_output_summary_as_status(tmp_path: Path) -> None:
    status, _, provenance = derive_status(
        events=[{"event_type": "run_end", "timestamp": "2026-01-01T00:00:00+00:00", "output_summary": "done", "metadata": {}}],
        report=None,
        manifest_summary={},
        run_dir=tmp_path,
    )

    assert status == "unknown"
    assert provenance == "trace.run_end"


def test_derive_status_uses_report_when_trace_missing(tmp_path: Path) -> None:
    status, warnings, provenance = derive_status(events=[], report=RunReport(run_id="run", status="success"), manifest_summary={}, run_dir=tmp_path)

    assert status == "success"
    assert provenance == "report_json"
    assert warnings == []


def test_derive_status_returns_running_for_recent_trace(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    status, _, provenance = derive_status(events=[{"event_type": "tool_call"}], report=None, manifest_summary={}, run_dir=run_dir)

    assert status == "running"
    assert provenance == "filesystem_mtime"
