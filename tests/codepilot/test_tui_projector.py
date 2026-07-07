from __future__ import annotations

from pathlib import Path

from codepilot.report.models import RunReport
from codepilot.tui.projector import build_dashboard_model, build_diff_summary, build_mcp_summary, build_policy_summary, build_test_summary, build_tool_summary, event_to_timeline_row
from tests.codepilot.tui_helpers import make_mcp_run, make_success_run


def test_run_start_maps_to_lifecycle_row() -> None:
    row = event_to_timeline_row({"event_type": "run_start", "step": 1, "metadata": {}})

    assert row.title == "Run started"
    assert row.category == "lifecycle"


def test_agent_action_hides_raw_preview() -> None:
    row = event_to_timeline_row({"event_type": "agent_action", "step": 2, "metadata": {"action_type": "edit", "parse_success": True, "normalization_applied": True, "raw_action_preview": "secret"}})

    assert row.metadata["action_type"] == "edit"
    assert "raw_action_preview" not in row.metadata


def test_policy_decision_rows_set_executed_false_for_deny_and_unapproved_ask() -> None:
    deny_row = event_to_timeline_row({"event_type": "policy_decision", "step": 1, "policy_decision": "deny", "metadata": {"executed": True}})
    ask_row = event_to_timeline_row({"event_type": "policy_decision", "step": 2, "policy_decision": "ask", "metadata": {"approved": False, "executed": True}})

    assert deny_row.executed is False
    assert ask_row.executed is False


def test_tool_call_row_uses_status_and_summary() -> None:
    row = event_to_timeline_row({"event_type": "tool_call", "step": 3, "tool_name": "run_tests", "success": True, "output_summary": "Tests passed", "metadata": {"status": "passed"}})

    assert row.tool_name == "run_tests"
    assert row.status == "passed"
    assert row.output_summary == "Tests passed"


def test_mcp_tool_call_row_is_safely_summarized() -> None:
    row = event_to_timeline_row(
        {
            "event_type": "tool_call",
            "step": 4,
            "tool_name": "mcp.filesystem.read_file",
            "success": True,
            "output_summary": "read README",
            "metadata": {"mcp": True, "server_name": "filesystem", "mcp_tool_name": "read_file", "descriptor_hash": "1234567890abcdef", "exposed_to_agent": True, "structured_content": {"content": "secret token=abc"}},
        }
    )

    assert row.category == "mcp"
    assert row.metadata["descriptor_hash_short"] == "1234567890ab"
    assert "structured_content" not in row.metadata


def test_build_summaries_from_events() -> None:
    events = [
        {"event_type": "policy_decision", "tool_name": "read_file", "policy_decision": "deny", "metadata": {"approved": False, "executed": False}},
        {"event_type": "tool_call", "tool_name": "run_tests", "metadata": {"command": "python -m pytest", "status": "passed"}},
        {"event_type": "tool_call", "tool_name": "git_diff", "metadata": {"paths": ["src/a.py"], "truncated": False}},
        {"event_type": "tool_call", "tool_name": "mcp.filesystem.read_file", "metadata": {"mcp": True, "server_name": "filesystem", "descriptor_hash": "1234567890abcdef", "exposed_to_agent": True}},
    ]

    assert build_policy_summary(events)["deny"] == 1
    assert build_tool_summary(events)["run_tests"] == 1
    assert build_mcp_summary(events)["total_tool_calls"] == 1
    assert build_test_summary(events, None)["status"] == "passed"
    assert "preview" not in build_diff_summary(events, None)


def test_build_dashboard_model_redacts_sensitive_metadata_and_truncates_timeline(tmp_path: Path) -> None:
    run_dir = make_success_run(tmp_path)
    model = build_dashboard_model(run_dir, max_timeline_rows=2, max_text_chars=80)

    assert len(model.timeline) == 2
    assert "timeline_truncated" in model.warnings
    assert "token" not in str(model.to_json_dict())


def test_build_dashboard_model_from_mcp_run(tmp_path: Path) -> None:
    run_dir = make_mcp_run(tmp_path)
    model = build_dashboard_model(run_dir)

    assert model.mcp_summary["total_tool_calls"] == 1
    assert model.entry.has_mcp is True
    assert model.entry.run_type == "mcp_demo"


def test_build_dashboard_model_uses_unknown_report_provenance_for_empty_run(tmp_path: Path) -> None:
    run_dir = tmp_path / "empty-run"
    run_dir.mkdir()

    model = build_dashboard_model(run_dir)

    assert model.source_provenance["report"] == "unknown"
