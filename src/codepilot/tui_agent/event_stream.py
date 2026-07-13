from __future__ import annotations

import queue
from typing import Any

from codepilot.trace.events import TraceEvent
from codepilot.tui_agent.models import TUIEvent


TRACE_METADATA_KEYS = (
    "action_type",
    "parse_success",
    "normalization_applied",
    "normalized_fields",
    "non_standard_fields",
    "normalization_conflicts",
    "raw_action_preview",
    "normalized_action_preview",
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


def _safe_dict_preview(value: Any, limit: int = 800) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    preview: dict[str, Any] = {}
    current_length = 2
    for key, item in value.items():
        key_text = str(key)
        if isinstance(item, dict):
            preview_item: Any = _safe_dict_preview(item, max(80, limit // 4))
        elif isinstance(item, list):
            preview_item = [item_entry if isinstance(item_entry, (dict, list)) else str(item_entry) for item_entry in item[:5]]
        elif isinstance(item, str):
            preview_item = item if len(item) <= max(40, limit // 4) else f"{item[: max(0, max(40, limit // 4) - 13)]}... truncated"
        else:
            preview_item = item
        candidate = {**preview, key_text: preview_item}
        if len(str(candidate)) > limit:
            break
        preview[key_text] = preview_item
        current_length = len(str(preview))
        if current_length >= limit:
            break
    return preview


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
        # 工具动作只展示 arguments 的预览，避免把整块 action 结构塞进 transcript。
        input_value = trace_event.input if isinstance(trace_event.input, dict) else {}
        arguments = input_value.get("arguments") if isinstance(input_value, dict) else None
        if isinstance(arguments, dict):
            normalized["input_preview"] = _safe_dict_preview(arguments) or {}
        elif isinstance(normalized["trace_metadata"].get("normalized_action_preview"), dict):
            normalized["input_preview"] = _safe_dict_preview(normalized["trace_metadata"]["normalized_action_preview"]) or {}
        elif isinstance(normalized["trace_metadata"].get("raw_action_preview"), dict):
            normalized["input_preview"] = _safe_dict_preview(normalized["trace_metadata"]["raw_action_preview"]) or {}
        elif isinstance(input_value, dict):
            normalized["input_preview"] = _safe_dict_preview(input_value) or {}
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
