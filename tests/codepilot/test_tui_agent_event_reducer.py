from __future__ import annotations

from codepilot.tui_agent.event_reducer import EventReducer
from codepilot.tui_agent.models import TUIEvent


def test_permission_requested_without_request_id_only_warns() -> None:
    reducer = EventReducer()

    view = reducer.reduce(TUIEvent(type="permission_requested", timestamp="2024-01-01T00:00:00Z", payload={}))

    assert view.warnings == ("permission_request_missing_id",)


def test_permission_request_and_response_update_request_state() -> None:
    reducer = EventReducer()
    requested = reducer.reduce(
        TUIEvent(
            type="permission_requested",
            timestamp="2024-01-01T00:00:00Z",
            payload={
                "request_id": "perm-1",
                "run_id": "run-1",
                "tool_name": "replace_range",
                "reason": "need approval",
                "arguments_preview": {"path": "src/calc.py"},
                "risk": "local_write",
                "side_effect": "local_write",
                "matched_rule": "tool.default_permission.ask",
                "created_at": "2024-01-01T00:00:00Z",
            },
        )
    )
    resolved = reducer.reduce(
        TUIEvent(
            type="permission_resolved",
            timestamp="2024-01-01T00:00:01Z",
            payload={"request_id": "perm-1", "decision": "approve_once", "responded_at": "2024-01-01T00:00:01Z"},
        )
    )

    assert requested.permission_requests[0].status == "pending"
    assert resolved.permission_requests[0].status == "approved"
    assert resolved.status == "running"


def test_permission_request_then_auto_response_is_not_pending() -> None:
    reducer = EventReducer()
    reducer.reduce(
        TUIEvent(
            type="permission_requested",
            timestamp="2024-01-01T00:00:00Z",
            payload={
                "request_id": "perm-2",
                "run_id": "run-1",
                "tool_name": "replace_range",
                "reason": "need approval",
                "arguments_preview": {"path": "src/calc.py"},
                "risk": "local_write",
                "side_effect": "local_write",
                "matched_rule": "tool.default_permission.ask",
                "created_at": "2024-01-01T00:00:00Z",
            },
        )
    )
    view = reducer.reduce(
        TUIEvent(
            type="permission_resolved",
            timestamp="2024-01-01T00:00:01Z",
            payload={"request_id": "perm-2", "decision": "approve_once", "responded_at": "2024-01-01T00:00:01Z"},
        )
    )

    assert view.permission_requests[0].status == "approved"


def test_run_finished_fallbacks_to_success_and_rehydrates_paths() -> None:
    reducer = EventReducer()

    view = reducer.reduce(
        TUIEvent(
            type="run_finished",
            timestamp="2024-01-01T00:00:00Z",
            payload={
                "success": True,
                "trace_path": "runs/run-1/trace.jsonl",
                "report_path": "runs/run-1/report.md",
                "report_json_path": "runs/run-1/report.json",
                "changed_files": ["src/calc.py"],
                "test_status": "passed",
            },
        )
    )

    assert view.status == "success"
    assert view.trace_path == "runs/run-1/trace.jsonl"
    assert view.report_path == "runs/run-1/report.md"
    assert view.report_json_path == "runs/run-1/report.json"
    assert view.changed_files == ("src/calc.py",)
    assert view.test_status == "passed"


def test_run_cancelled_sets_cancelled_status() -> None:
    reducer = EventReducer()

    view = reducer.reduce(TUIEvent(type="run_cancelled", timestamp="2024-01-01T00:00:00Z", payload={}))

    assert view.status == "cancelled"
