from __future__ import annotations

from codepilot.trace.events import TraceEvent
from codepilot.tui_agent.event_stream import trace_event_to_tui_event


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
            "matched_rule": "tool.default_permission.ask",
        },
    )

    tui_event = trace_event_to_tui_event(event)

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

    assert tui_event.type == "permission_resolved"
    assert tui_event.payload["request_id"] == "perm-1"
    assert tui_event.payload["decision"] == "approve_once"
    assert tui_event.payload["reason"] == "approved"


def test_llm_call_trace_event_maps_to_finished_event() -> None:
    event = TraceEvent(run_id="run-1", step=3, event_type="llm_call", output_preview='{"short_rationale":"inspect"}')

    tui_event = trace_event_to_tui_event(event)

    assert tui_event.type == "llm_call_finished"


def test_agent_finish_trace_event_maps_to_finished_event() -> None:
    event = TraceEvent(run_id="run-1", step=4, event_type="agent_finish", output_summary="done")

    tui_event = trace_event_to_tui_event(event)

    assert tui_event.type == "agent_finished"


def test_agent_observation_trace_event_maps_to_observation_event() -> None:
    event = TraceEvent(run_id="run-1", step=5, event_type="agent_observation", output_summary="observed")

    tui_event = trace_event_to_tui_event(event)

    assert tui_event.type == "agent_observation"


def test_tool_call_trace_event_maps_to_tool_finished_event() -> None:
    event = TraceEvent(run_id="run-1", step=6, event_type="tool_call", tool_name="list_files")

    tui_event = trace_event_to_tui_event(event)

    assert tui_event.type == "tool_finished"


def test_unknown_trace_event_stays_trace_event() -> None:
    event = TraceEvent.model_construct(run_id="run-1", step=7, event_type="something_else")

    tui_event = trace_event_to_tui_event(event)

    assert tui_event.type == "trace_event"
