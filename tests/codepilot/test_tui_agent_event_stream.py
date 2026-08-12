from __future__ import annotations

from pathlib import Path

from codepilot.session.database import SessionDatabase
from codepilot.session.repositories import SessionRepositories
from codepilot.session.trace_recorder import SessionTraceRecorder
from codepilot.trace.events import TraceEvent
from codepilot.trace.logger import TraceLogger
from codepilot.tui_agent.event_reducer import EventReducer
from codepilot.tui_agent.event_stream import trace_event_to_tui_event


def _transcript_kinds(view) -> tuple[str, ...]:
    return tuple(item.kind for item in view.transcript)


def test_permission_request_trace_event_is_normalized() -> None:
    event = TraceEvent(
        run_id="run-1",
        step=1,
        event_type="permission_request",
        permission_request_id="perm-1",
        metadata={
            "reason": "need approval",
            "action_id": "act-1",
            "arguments_preview": {"path": "demo.py"},
            "risk": "local_write",
            "side_effect": "local_write",
            "external_impact": "none",
            "reversibility": "unknown",
            "matched_rule": "effect.approval.ask",
        },
    )

    tui_event = trace_event_to_tui_event(event)

    assert tui_event is not None
    assert tui_event.type == "permission_requested"
    assert tui_event.payload["request_id"] == "perm-1"
    assert tui_event.payload["arguments_preview"] == {"path": "demo.py"}
    assert tui_event.payload["reason"] == "need approval"


def test_permission_response_trace_event_is_normalized() -> None:
    event = TraceEvent(
        run_id="run-1",
        step=2,
        event_type="permission_response",
        permission_request_id="perm-1",
        permission_decision="approve_once",
        metadata={"reason": "approved"},
    )

    tui_event = trace_event_to_tui_event(event)

    assert tui_event is not None
    assert tui_event.type == "permission_resolved"
    assert tui_event.payload["request_id"] == "perm-1"
    assert tui_event.payload["decision"] == "approve_once"
    assert tui_event.payload["reason"] == "approved"


def test_session_trace_recorder_keeps_permission_ids_structured(tmp_path: Path) -> None:
    database = SessionDatabase(tmp_path / "sessions.sqlite3")
    database.initialize()
    store = SessionRepositories(database)
    session = store.sessions.create_session(project_path=tmp_path, provider="openai", current_model="fake", permission_mode="manual")
    turn = store.turns.create_turn(
        session_id=session.session_id,
        title="Turn 1",
        provider_snapshot="openai",
        model_snapshot="fake",
        permission_mode_snapshot="manual",
        branch_snapshot=None,
    )
    recorder = SessionTraceRecorder(database, session.session_id, turn.turn_id)

    event = recorder.record_permission_request(
        request_id="perm-1",
        tool_name="replace_range",
        reason="need approval",
    )

    assert event.permission_request_id == "perm-1"
    assert trace_event_to_tui_event(event).payload["request_id"] == "perm-1"


def test_llm_call_trace_event_maps_to_finished_event() -> None:
    event = TraceEvent(run_id="run-1", step=3, event_type="llm_call", output_preview='{"short_rationale":"inspect"}')

    tui_event = trace_event_to_tui_event(event)

    assert tui_event is not None
    assert tui_event.type == "llm_call_finished"


def test_agent_finish_trace_event_maps_to_finished_event() -> None:
    event = TraceEvent(run_id="run-1", step=4, event_type="agent_finish", output_summary="done")

    tui_event = trace_event_to_tui_event(event)

    assert tui_event is not None
    assert tui_event.type == "agent_finished"


def test_run_start_and_run_end_trace_events_are_not_published() -> None:
    start_event = TraceEvent(run_id="run-1", step=1, event_type="run_start")
    end_event = TraceEvent(run_id="run-1", step=2, event_type="run_end")

    assert trace_event_to_tui_event(start_event) is None
    assert trace_event_to_tui_event(end_event) is None


def test_agent_observation_trace_event_maps_to_observation_event() -> None:
    event = TraceEvent(run_id="run-1", step=5, event_type="agent_observation", output_summary="observed")

    tui_event = trace_event_to_tui_event(event)

    assert tui_event is not None
    assert tui_event.type == "agent_observation"


def test_tool_call_trace_event_maps_to_tool_finished_event() -> None:
    event = TraceEvent(run_id="run-1", step=6, event_type="tool_call", tool_name="list_files")

    tui_event = trace_event_to_tui_event(event)

    assert tui_event is not None
    assert tui_event.type == "tool_finished"


def test_native_finish_action_trace_event_uses_input_preview() -> None:
    event = TraceEvent(
        run_id="run-1",
        step=7,
        event_type="agent_action",
        input={
            "status": "success",
            "summary": "完成",
        },
        metadata={"action_type": "finish"},
    )

    tui_event = trace_event_to_tui_event(event)

    assert tui_event is not None
    assert tui_event.payload["input_preview"] == {"status": "success", "summary": "完成"}


def test_unknown_trace_event_stays_trace_event() -> None:
    event = TraceEvent.model_construct(run_id="run-1", step=7, event_type="something_else")

    tui_event = trace_event_to_tui_event(event)

    assert tui_event is not None
    assert tui_event.type == "trace_event"


def test_long_natural_reply_survives_trace_preview_pipeline(tmp_path: Path) -> None:
    logger = TraceLogger(runs_dir=tmp_path / "runs", run_id="run-1")
    text = "长文本" * 1000
    reducer = EventReducer()

    llm_event = trace_event_to_tui_event(
        logger.record_llm_call(
            message_count=2,
            response_text=text,
        )
    )
    finish_event = trace_event_to_tui_event(
        logger.record_agent_finish(
            status="message_complete",
            success=True,
            summary=text,
            metadata={"assistant_stop_reason": "natural_reply"},
        )
    )

    assert llm_event is not None
    assert finish_event is not None

    view = reducer.reduce(llm_event)
    view = reducer.reduce(finish_event)

    assert _transcript_kinds(view) == ("assistant_raw",)
    assert view.transcript[0].body == text
    assert "... truncated" not in view.transcript[0].body
    assert len(view.transcript[0].body) == len(text)
