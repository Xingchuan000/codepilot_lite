from __future__ import annotations

import queue
from typing import Any

from codepilot.trace.events import TraceEvent
from codepilot.tui_agent.models import TUIEvent


class MemoryEventStream:
    def __init__(self) -> None:
        self._queue: queue.Queue[TUIEvent] = queue.Queue()

    def publish(self, event: TUIEvent) -> None:
        self._queue.put(event)

    def drain(self, max_items: int = 100) -> list[TUIEvent]:
        events: list[TUIEvent] = []
        for _ in range(max_items):
            try:
                events.append(self._queue.get_nowait())
            except queue.Empty:
                break
        return events


def _normalize_permission_payload(payload: dict[str, Any]) -> dict[str, Any]:
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    if payload.get("event_type") == "permission_request":
        return {
            **payload,
            "request_id": payload.get("permission_request_id"),
            "reason": metadata.get("reason"),
            "action_id": metadata.get("action_id"),
            "arguments_preview": metadata.get("arguments_preview") or {},
            "risk": metadata.get("risk"),
            "side_effect": metadata.get("side_effect"),
            "matched_rule": metadata.get("matched_rule"),
            "created_at": payload.get("timestamp"),
        }
    if payload.get("event_type") == "permission_response":
        return {
            **payload,
            "request_id": payload.get("permission_request_id"),
            "decision": payload.get("permission_decision"),
            "reason": metadata.get("reason"),
            "responded_at": payload.get("timestamp"),
        }
    return payload


def trace_event_to_tui_event(trace_event: TraceEvent) -> TUIEvent:
    mapping = {
        "run_start": "run_started",
        "llm_call": "llm_call_finished",
        "agent_action": "agent_action",
        "agent_observation": "agent_observation",
        "agent_finish": "agent_finished",
        "policy_decision": "policy_decision",
        "permission_request": "permission_requested",
        "permission_response": "permission_resolved",
        "tool_call": "tool_finished",
        "run_end": "run_finished",
        "run_cancelled": "run_cancelled",
    }
    return TUIEvent(
        type=mapping.get(trace_event.event_type, "trace_event"),
        timestamp=trace_event.timestamp,
        run_id=trace_event.run_id,
        payload=_normalize_permission_payload(trace_event.model_dump()),
    )
