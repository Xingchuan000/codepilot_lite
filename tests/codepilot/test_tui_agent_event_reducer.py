from __future__ import annotations

from codepilot.tui_agent.event_reducer import EventReducer
from codepilot.tui_agent.models import TUIEvent


def _transcript_kinds(view) -> tuple[str, ...]:
    return tuple(item.kind for item in view.transcript)


def test_user_message_updates_task_and_transcript() -> None:
    reducer = EventReducer()

    view = reducer.reduce(
        TUIEvent(
            type="user_message",
            timestamp="2024-01-01T00:00:00Z",
            payload={"text": "请列出项目结构"},
        )
    )

    assert view.task == "请列出项目结构"
    assert view.status == "running"
    assert _transcript_kinds(view) == ("user_message",)
    assert ":" not in view.transcript[0].id


def test_user_message_is_not_deduplicated_across_rounds() -> None:
    reducer = EventReducer()

    first = reducer.reduce(
        TUIEvent(
            type="user_message",
            timestamp="2024-01-01T00:00:00Z",
            run_id="run-1",
            payload={"text": "请列出项目结构"},
        )
    )
    view = reducer.reduce(
        TUIEvent(
            type="user_message",
            timestamp="2024-01-01T00:05:00Z",
            run_id="run-1",
            payload={"text": "请再列一次项目结构"},
        )
    )

    assert len(first.transcript) == 1
    assert len(view.transcript) == 2
    assert view.transcript[0].kind == "user_message"
    assert view.transcript[1].kind == "user_message"


def test_llm_call_finished_json_creates_plan_only_until_real_tool_action_arrives() -> None:
    reducer = EventReducer()

    planned = reducer.reduce(
        TUIEvent(
            type="llm_call_finished",
            timestamp="2024-01-01T00:00:01Z",
            run_id="run-1",
            payload={
                "output_preview": '{"short_rationale":"先检查结构","tool_name":"list_files","arguments":{"path":".","max_depth":2}}'
            },
        )
    )
    view = reducer.reduce(
        TUIEvent(
            type="agent_action",
            timestamp="2024-01-01T00:00:02Z",
            run_id="run-1",
            payload={
                "tool_name": "list_files",
                "input": {
                    "type": "tool_call",
                    "tool_name": "list_files",
                    "arguments": {"path": ".", "max_depth": 2},
                    "short_rationale": "先检查结构",
                },
                "metadata": {"action_type": "tool_call", "parse_success": True},
                "step": 2,
            },
        )
    )

    assert _transcript_kinds(planned) == ("assistant_plan",)
    assert _transcript_kinds(view) == ("assistant_plan", "assistant_action")
    assert view.transcript[1].body == 'list_files {"max_depth": 2, "path": "."}'
    assert len(view.timeline) == 1
    assert view.current_tool == "list_files"
    assert view.active_tool == "list_files"


def test_llm_call_finished_non_json_waits_for_agent_finish() -> None:
    reducer = EventReducer()

    view = reducer.reduce(
        TUIEvent(
            type="llm_call_finished",
            timestamp="2024-01-01T00:00:00Z",
            payload={"output_preview": "not json"},
        )
    )

    assert _transcript_kinds(view) == ()
    assert view.last_assistant_message == "not json"


def test_long_natural_reply_is_not_truncated_in_transcript_body() -> None:
    reducer = EventReducer()
    text = "解释" * 2000

    view = reducer.reduce(
        TUIEvent(
            type="llm_call_finished",
            timestamp="2024-01-01T00:00:00Z",
            payload={"output_preview": text},
        )
    )

    assert _transcript_kinds(view) == ()
    assert view.last_assistant_message == text


def test_llm_call_finished_finish_json_does_not_prematurely_create_final_summary() -> None:
    reducer = EventReducer()

    view = reducer.reduce(
        TUIEvent(
            type="llm_call_finished",
            timestamp="2024-01-01T00:00:00Z",
            payload={"output_preview": '{"type":"finish","status":"success","summary":"done"}'},
        )
    )

    assert _transcript_kinds(view) == ()


def test_llm_call_finished_json_without_short_rationale_skips_empty_plan() -> None:
    reducer = EventReducer()

    view = reducer.reduce(
        TUIEvent(
            type="llm_call_finished",
            timestamp="2024-01-01T00:00:00Z",
            payload={"output_preview": '{"tool_name":"list_files","arguments":{"path":".","max_depth":2}}'},
        )
    )

    assert _transcript_kinds(view) == ()


def test_tool_finished_updates_changed_files_and_test_status() -> None:
    reducer = EventReducer()

    view = reducer.reduce(
        TUIEvent(
            type="tool_finished",
            timestamp="2024-01-01T00:00:00Z",
            payload={
                "tool_name": "run_tests",
                "success": True,
                "output_summary": "tests passed",
                "metadata": {"changed_files": ["src/calc.py"], "status": "passed"},
            },
        )
    )

    assert view.changed_files == ("src/calc.py",)
    assert view.test_status == "passed"
    assert _transcript_kinds(view) == ("tool_result",)


def test_permission_requested_and_resolved_update_state_and_transcript() -> None:
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
            },
        )
    )
    resolved = reducer.reduce(
        TUIEvent(
            type="permission_resolved",
            timestamp="2024-01-01T00:00:01Z",
            payload={"request_id": "perm-1", "decision": "approve_once", "reason": "approved"},
        )
    )

    assert requested.permission_requests[0].status == "pending"
    assert resolved.permission_requests[0].status == "approved"
    assert _transcript_kinds(resolved) == ("permission_request", "permission_response")
    assert resolved.status == "running"


def test_agent_finished_message_complete_creates_raw_assistant_message() -> None:
    reducer = EventReducer()

    reducer.reduce(
        TUIEvent(
            type="llm_call_finished",
            timestamp="2024-01-01T00:00:00Z",
            payload={"output_preview": "Hello"},
        )
    )
    view = reducer.reduce(
        TUIEvent(
            type="agent_finished",
            timestamp="2024-01-01T00:00:01Z",
            payload={"output_summary": "Hello", "metadata": {"status": "message_complete", "assistant_stop_reason": "natural_reply"}},
        )
    )

    assert view.status == "message_complete"
    assert _transcript_kinds(view) == ("assistant_raw",)
    assert view.transcript[0].body == "Hello"


def test_long_final_summary_is_not_truncated_in_transcript_body() -> None:
    reducer = EventReducer()
    summary = "完成说明" * 2000

    view = reducer.reduce(
        TUIEvent(
            type="agent_finished",
            timestamp="2024-01-01T00:00:00Z",
            payload={"output_summary": summary, "metadata": {"status": "success"}},
        )
    )

    assert view.transcript[0].kind == "final_summary"
    assert view.transcript[0].body == summary
    assert "... truncated" not in view.transcript[0].body


def test_agent_finished_creates_final_summary() -> None:
    reducer = EventReducer()

    view = reducer.reduce(
        TUIEvent(
            type="agent_finished",
            timestamp="2024-01-01T00:00:00Z",
            payload={"output_summary": "已完成", "metadata": {"status": "success"}},
        )
    )

    assert view.status == "success"
    assert _transcript_kinds(view) == ("final_summary",)


def test_agent_finished_message_complete_does_not_duplicate_assistant_message() -> None:
    reducer = EventReducer()

    reducer.reduce(
        TUIEvent(
            type="llm_call_finished",
            timestamp="2024-01-01T00:00:00Z",
            payload={"output_preview": "Hello"},
        )
    )
    view = reducer.reduce(
        TUIEvent(
            type="agent_finished",
            timestamp="2024-01-01T00:00:01Z",
            payload={"output_summary": "Hello", "metadata": {"status": "message_complete", "assistant_stop_reason": "natural_reply"}},
        )
    )

    assert view.status == "message_complete"
    assert _transcript_kinds(view) == ("assistant_raw",)


def test_long_tool_result_is_still_truncated_in_transcript_body() -> None:
    reducer = EventReducer()
    output = "x" * 5000

    view = reducer.reduce(
        TUIEvent(
            type="tool_finished",
            timestamp="2024-01-01T00:00:00Z",
            payload={
                "tool_name": "run_tests",
                "success": True,
                "output_preview": output,
                "metadata": {"status": "passed"},
            },
        )
    )

    assert view.transcript[0].kind == "tool_result"
    assert view.transcript[0].body.endswith("... truncated")


def test_agent_finished_task_incomplete_uses_system_status() -> None:
    reducer = EventReducer()

    reducer.reduce(
        TUIEvent(
            type="llm_call_finished",
            timestamp="2024-01-01T00:00:00Z",
            payload={"output_preview": "Hello"},
        )
    )
    view = reducer.reduce(
        TUIEvent(
            type="agent_finished",
            timestamp="2024-01-01T00:00:01Z",
            payload={
                "output_summary": "Hello",
                "metadata": {"status": "task_incomplete", "assistant_stop_reason": "natural_reply", "missing_evidence": ["missing_changed_files"]},
            },
        )
    )

    assert view.status == "task_incomplete"
    assert _transcript_kinds(view) == ("system_status",)


def test_run_finished_keeps_paths_out_of_transcript() -> None:
    reducer = EventReducer()

    view = reducer.reduce(
        TUIEvent(
            type="run_finished",
            timestamp="2024-01-01T00:00:00Z",
            payload={
                "status": "success",
                "trace_path": "runs/run-1/trace.jsonl",
                "report_path": "runs/run-1/report.md",
                "report_json_path": "runs/run-1/report.json",
                "changed_files": ["src/calc.py"],
                "test_status": "passed",
            },
        )
    )

    assert view.trace_path == "runs/run-1/trace.jsonl"
    assert view.report_path == "runs/run-1/report.md"
    assert view.report_json_path == "runs/run-1/report.json"
    assert _transcript_kinds(view) == ("system_status",)
    assert "trace.jsonl" not in view.transcript[0].body
    assert "report.md" not in view.transcript[0].body
    assert "report.json" not in view.transcript[0].body


def test_run_finished_reads_top_level_evidence_fields() -> None:
    reducer = EventReducer()

    view = reducer.reduce(
        TUIEvent(
            type="run_finished",
            timestamp="2024-01-01T00:00:00Z",
            payload={
                "status": "success",
                "completion_kind": "task_success",
                "assistant_stop_reason": "structured_finish",
                "delivery_kind": "code_change",
                "requires_evidence": True,
                "tests_required": True,
                "diff_required": True,
                "diff_checked": True,
                "missing_evidence": ["missing_diff_check"],
            },
        )
    )

    assert view.completion_kind == "task_success"
    assert view.assistant_stop_reason == "structured_finish"
    assert view.delivery_kind == "code_change"
    assert view.requires_evidence is True
    assert view.tests_required is True
    assert view.diff_required is True
    assert view.diff_checked is True
    assert view.missing_evidence == ("missing_diff_check",)


def test_duplicate_run_finished_does_not_duplicate_status_notice() -> None:
    reducer = EventReducer()

    first = reducer.reduce(
        TUIEvent(
            type="run_finished",
            timestamp="2024-01-01T00:00:00Z",
            payload={"status": "message_complete", "success": True, "metadata": {"status": "message_complete"}},
        )
    )
    view = reducer.reduce(
        TUIEvent(
            type="run_finished",
            timestamp="2024-01-01T00:00:01Z",
            payload={"status": "message_complete", "success": True, "metadata": {"status": "message_complete"}},
        )
    )

    assert _transcript_kinds(first) == ("system_status",)
    assert _transcript_kinds(view) == ("system_status",)
    assert len(view.transcript) == 1


def test_duplicate_permission_request_does_not_duplicate_transcript() -> None:
    reducer = EventReducer()

    first = reducer.reduce(
        TUIEvent(
            type="permission_requested",
            timestamp="2024-01-01T00:00:00Z",
            payload={"request_id": "perm-1", "run_id": "run-1", "tool_name": "edit", "reason": "need approval"},
        )
    )
    view = reducer.reduce(
        TUIEvent(
            type="permission_requested",
            timestamp="2024-01-01T00:00:01Z",
            payload={"request_id": "perm-1", "run_id": "run-1", "tool_name": "edit", "reason": "need approval"},
        )
    )

    assert len(first.transcript) == 1
    assert len(view.transcript) == 1
    assert len(view.permission_requests) == 1


def test_command_output_appends_transcript() -> None:
    reducer = EventReducer()

    view = reducer.reduce(
        TUIEvent(
            type="command_output",
            timestamp="2024-01-01T00:00:00Z",
            payload={"command": "/help", "output": "Run: running"},
        )
    )

    assert _transcript_kinds(view) == ("command_output",)
    assert view.transcript[0].copy_text == "$ /help\nRun: running"
    assert "/" not in view.transcript[0].id


def test_error_appends_transcript_and_warning() -> None:
    reducer = EventReducer()

    view = reducer.reduce(TUIEvent(type="error", timestamp="2024-01-01T00:00:00Z", payload={"error": "boom"}))

    assert view.status == "failed"
    assert view.warnings == ("boom",)
    assert _transcript_kinds(view) == ("error",)
