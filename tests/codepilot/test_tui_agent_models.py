from __future__ import annotations

from typing import get_args

from codepilot.agent.evidence import EvidenceSnapshot
from codepilot.agent.outcome import RunOutcomeSnapshot
from codepilot.permissions import PermissionRequest
from codepilot.tui_agent.models import AgentRunView, TranscriptItem, TUIEventType, TUISessionRunRef, to_jsonable


def test_transcript_item_can_be_constructed() -> None:
    item = TranscriptItem(
        id="msg-1",
        kind="user_message",
        timestamp="2024-01-01T00:00:00Z",
        title="You",
        body="请列出项目结构",
        copy_text="You: 请列出项目结构",
    )

    assert item.kind == "user_message"
    assert item.copy_text == "You: 请列出项目结构"


def test_agent_run_view_defaults_include_empty_transcript() -> None:
    view = AgentRunView()

    assert view.transcript == ()
    assert view.diff_checked is None


def test_to_jsonable_serializes_transcript() -> None:
    view = AgentRunView(
        transcript=(
            TranscriptItem(
                id="msg-1",
                kind="system_status",
                timestamp="2024-01-01T00:00:00Z",
                title="Run finished",
                body="Run finished: success",
            ),
        )
    )

    assert to_jsonable(view)["transcript"][0]["kind"] == "system_status"


def test_tui_event_type_includes_chat_transcript_events() -> None:
    assert {"user_message", "command_output", "agent_finished", "agent_observation"} <= set(get_args(TUIEventType))


def test_permission_request_comes_from_codepilot_permissions() -> None:
    request = PermissionRequest(
        request_id="perm-1",
        run_id="run-1",
        action_id="act-1",
        tool_name="run_shell",
        arguments_preview={},
        reason="need approval",
        risk="shell_execution",
        side_effect="local_exec",
        matched_rule="tool.default_permission.ask",
        created_at="2024-01-01T00:00:00Z",
    )

    assert AgentRunView(permission_requests=(request,)).permission_requests[0].request_id == "perm-1"


def test_session_run_ref_from_outcome_keeps_session_v1_json_shape() -> None:
    outcome = RunOutcomeSnapshot(
        status="success",
        completion_kind="task_success",
        assistant_stop_reason="structured_finish",
        delivery_kind="code_change",
        evidence=EvidenceSnapshot(
            requires_evidence=True,
            reasons=("write_executed", "written_files"),
            write_attempted=True,
            write_executed=True,
            written_files=("src/calc.py",),
            observed_changed_files=("src/calc.py",),
            claimed_changed_files=("src/calc.py",),
            tests_required=True,
            diff_required=True,
            diff_checked=True,
            missing=(),
        ),
        changed_files=("src/calc.py",),
        last_test_status="passed",
    )

    run_ref = TUISessionRunRef.from_outcome(
        run_id="run-1",
        task_preview="fix add",
        outcome=outcome,
        trace_path="runs/run-1/trace.jsonl",
        report_path="runs/run-1/report.md",
        report_json_path="runs/run-1/report.json",
        started_at="2024-01-01T00:00:00Z",
        ended_at="2024-01-01T00:01:00Z",
    )

    assert to_jsonable(run_ref) == {
        "run_id": "run-1",
        "task_preview": "fix add",
        "status": "success",
        "trace_path": "runs/run-1/trace.jsonl",
        "report_path": "runs/run-1/report.md",
        "report_json_path": "runs/run-1/report.json",
        "started_at": "2024-01-01T00:00:00Z",
        "ended_at": "2024-01-01T00:01:00Z",
        "completion_kind": "task_success",
        "assistant_stop_reason": "structured_finish",
        "delivery_kind": "code_change",
        "requires_evidence": True,
        "evidence_reasons": ["write_executed", "written_files"],
        "write_attempted": True,
        "write_executed": True,
        "written_files": ["src/calc.py"],
        "changed_files": ["src/calc.py"],
        "tests_required": True,
        "diff_required": True,
        "diff_checked": True,
        "missing_evidence": [],
        "tests": "passed",
    }
