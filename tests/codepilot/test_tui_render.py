from __future__ import annotations

from pathlib import Path

from rich.console import Console

from codepilot.tui.models import RunArtifactRef, RunDashboardModel, RunIndexEntry, TimelineRow
from codepilot.tui.render import render_run_detail, render_run_index


def test_render_run_index_outputs_core_columns() -> None:
    console = Console(record=True, color_system=None, width=200)
    render_run_index(console, [RunIndexEntry(run_id="run-1", status="success", tool_call_count=2, policy_denied_count=1)])

    output = console.export_text()

    assert "Run Dashboard" in output
    assert "run-1" in output
    assert "success" in output
    assert "Policy" in output


def test_render_run_index_empty_list_shows_message() -> None:
    console = Console(record=True, color_system=None, width=200)
    render_run_index(console, [])

    assert "No runs found" in console.export_text()


def test_render_run_detail_outputs_sections_and_masks_sensitive_text() -> None:
    console = Console(record=True, color_system=None, width=200)
    model = RunDashboardModel(
        schema_version="codepilot.dashboard.v1",
        entry=RunIndexEntry(
            run_id="run-1",
            status="success",
            task="a" * 200,
            changed_files=("src/a.py",),
            artifacts=(RunArtifactRef(kind="trace", path=Path("trace.jsonl"), exists=True, warnings=("artifact_missing",)),),
        ),
        timeline=(TimelineRow(step=1, event_type="run_start", title="Run started", category="lifecycle"),),
        policy_summary={"allow": 1, "ask": 0, "deny": 1},
        mcp_summary={"total_tool_calls": 1},
        test_summary={"status": "passed"},
        warnings=("artifact_missing",),
    )

    render_run_detail(console, model)
    output = console.export_text()

    assert "Timeline" in output
    assert "Policy" in output
    assert "Artifacts" in output
    assert "MCP" in output
    assert "artifact_missing" in output
    assert "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" in output
    assert "token" not in output
    assert "password" not in output
    assert "api_key" not in output


def test_render_run_detail_displays_descriptor_short_hash() -> None:
    console = Console(record=True, color_system=None, width=200)
    model = RunDashboardModel(
        schema_version="codepilot.dashboard.v1",
        entry=RunIndexEntry(run_id="run-1", status="success"),
        mcp_summary={"descriptor_hashes": ["1234567890ab"]},
    )

    render_run_detail(console, model)

    assert "1234567890ab" in console.export_text()
