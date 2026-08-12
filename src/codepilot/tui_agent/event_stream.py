from __future__ import annotations

import queue
from typing import Any

from codepilot.trace.events import TraceEvent
from codepilot.tui_agent.models import TUIEvent
from codepilot.tui_agent.preview import safe_dict_preview


TRACE_METADATA_KEYS = (
    "action_type",
    "finish_blocked_by_evidence",
    "requested_status",
    "effective_status",
    "status_normalized",
    "status",
    "summary",
    "completion_kind",
    "assistant_stop_reason",
    "delivery_kind",
    "requires_evidence",
    "evidence_reasons",
    "write_attempted",
    "write_executed",
    "written_files",
    "observed_changed_files",
    "claimed_changed_files",
    "changed_files",
    "tests_required",
    "diff_required",
    "diff_checked",
    "missing_evidence",
    "last_test_status",
    "executed",
    "side_effect",
    "approved",
    "requires_approval",
    "action_id",
    "arguments_preview",
    "risk",
    "external_impact",
    "reversibility",
    "matched_rule",
    "created_at",
    "responded_at",
)


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


def _flatten_metadata(payload: dict[str, Any], keys: tuple[str, ...]) -> dict[str, Any]:
    metadata = payload.get("trace_metadata")
    if not isinstance(metadata, dict):
        return payload
    flattened = dict(payload)
    for key in keys:
        if key in flattened or key not in metadata:
            continue
        flattened[key] = metadata[key]
    return flattened


def _trace_payload(trace_event: TraceEvent) -> dict[str, Any]:
    payload = trace_event.model_dump()
    metadata = payload.pop("metadata")
    normalized = {
        **payload,
        "trace_metadata": metadata if isinstance(metadata, dict) else {},
    }
    # 先把 trace 的常用元数据抬到顶层，Reducer 之后只认这一层的字段。
    normalized = _flatten_metadata(normalized, TRACE_METADATA_KEYS)
    if trace_event.event_type == "permission_request":
        # 权限事件在适配层统一改成 request_id / created_at 这套固定命名。
        normalized.pop("permission_request_id", None)
        normalized["request_id"] = trace_event.permission_request_id
        normalized["arguments_preview"] = normalized["arguments_preview"] if isinstance(normalized.get("arguments_preview"), dict) else {}
        normalized["reason"] = str(normalized.get("reason") or normalized["trace_metadata"].get("reason") or "")
        normalized["created_at"] = trace_event.timestamp
    elif trace_event.event_type == "permission_response":
        # 响应事件同样只保留标准字段，避免 TUI 再兼容旧别名。
        normalized.pop("permission_request_id", None)
        normalized.pop("permission_decision", None)
        normalized["request_id"] = trace_event.permission_request_id
        normalized["decision"] = trace_event.permission_decision
        normalized["reason"] = str(normalized.get("reason") or normalized["trace_metadata"].get("reason") or "")
        normalized["responded_at"] = trace_event.timestamp
    elif trace_event.event_type == "agent_action":
        # The internal Native finish action only exposes a safe input preview.
        input_value = trace_event.input if isinstance(trace_event.input, dict) else {}
        arguments = input_value.get("arguments") if isinstance(input_value, dict) else None
        if isinstance(arguments, dict):
            normalized["input_preview"] = safe_dict_preview(arguments) or {}
        elif isinstance(input_value, dict):
            normalized["input_preview"] = safe_dict_preview(input_value) or {}
        else:
            normalized["input_preview"] = {}
    return normalized


def trace_event_to_tui_event(trace_event: TraceEvent) -> TUIEvent | None:
    mapping = {
        "llm_call": "llm_call_finished",
        "agent_action": "agent_action",
        "agent_observation": "agent_observation",
        "agent_finish": "agent_finished",
        "policy_decision": "policy_decision",
        "permission_request": "permission_requested",
        "permission_response": "permission_resolved",
        "tool_call": "tool_finished",
        "run_cancelled": "run_cancelled",
    }
    if trace_event.event_type in {"run_start", "run_end"}:
        return None
    return TUIEvent(
        type=mapping.get(trace_event.event_type, "trace_event"),
        timestamp=trace_event.timestamp,
        run_id=trace_event.run_id,
        payload=_trace_payload(trace_event),
    )
