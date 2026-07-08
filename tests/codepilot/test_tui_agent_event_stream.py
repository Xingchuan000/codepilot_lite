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

