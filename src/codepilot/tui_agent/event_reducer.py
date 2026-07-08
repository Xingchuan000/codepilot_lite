from __future__ import annotations

from dataclasses import replace
from typing import Any

from codepilot.tui_agent.models import AgentRunView, PermissionRequest, TUIEvent, TimelineItem


VALID_RUN_STATUSES = {"idle", "running", "waiting_permission", "success", "failed", "cancelled", "interrupted", "max_steps_exceeded", "llm_error", "llm_exhausted", "unknown"}


def _metadata(payload: dict[str, Any]) -> dict[str, Any]:
    metadata = payload.get("metadata")
    return metadata if isinstance(metadata, dict) else {}


def _payload_value(payload: dict[str, Any], key: str, fallback: Any) -> Any:
    return payload[key] if key in payload else fallback


def trace_payload_to_timeline_item(payload: dict[str, Any]) -> TimelineItem:
    metadata = _metadata(payload)
    return TimelineItem(
        step=payload.get("step") if isinstance(payload.get("step"), int) else None,
        title=str(payload.get("type") or payload.get("event_type") or payload.get("tool_name") or "event"),
        category=str(payload.get("event_type") or "event"),
        status=str(payload.get("status")) if isinstance(payload.get("status"), str) else None,
        tool_name=str(payload.get("tool_name")) if isinstance(payload.get("tool_name"), str) else None,
        policy_decision=str(payload.get("policy_decision") or metadata.get("policy_decision"))
        if (payload.get("policy_decision") or metadata.get("policy_decision")) is not None
        else None,
        executed=metadata.get("executed") if isinstance(metadata.get("executed"), bool) else None,
        output_summary=str(payload.get("output_summary")) if isinstance(payload.get("output_summary"), str) else None,
    )


def reduce_event(view: AgentRunView, event: TUIEvent) -> AgentRunView:
    payload = event.payload
    if event.type == "run_started":
        return replace(
            view,
            run_id=event.run_id or view.run_id,
            task=str((_metadata(payload).get("task")) or view.task),
            status="running",
            trace_path=payload.get("trace_path") or view.trace_path,
        )
    if event.type == "agent_action":
        item = trace_payload_to_timeline_item(payload)
        return replace(view, current_step=item.step, current_tool=item.tool_name, timeline=view.timeline + (item,))
    if event.type == "policy_decision":
        item = trace_payload_to_timeline_item(payload)
        metadata = _metadata(payload)
        status = "waiting_permission" if item.policy_decision == "ask" and metadata.get("approved") is False else view.status
        return replace(view, status=status, timeline=view.timeline + (item,))
    if event.type == "permission_requested":
        request_id = payload.get("request_id") or payload.get("permission_request_id")
        metadata = _metadata(payload)
        reason = payload.get("reason") or metadata.get("reason") or ""
        arguments_preview = payload.get("arguments_preview") or metadata.get("arguments_preview") or {}
        if not request_id:
            return replace(view, warnings=view.warnings + ("permission_request_missing_id",))
        request = PermissionRequest(
            request_id=str(request_id),
            run_id=str(payload.get("run_id") or view.run_id or ""),
            action_id=payload.get("action_id"),
            tool_name=str(payload.get("tool_name") or ""),
            arguments_preview=arguments_preview if isinstance(arguments_preview, dict) else {},
            reason=str(reason),
            risk=payload.get("risk"),
            side_effect=payload.get("side_effect"),
            matched_rule=payload.get("matched_rule"),
            created_at=str(payload.get("created_at") or event.timestamp),
            status="pending",
        )
        return replace(view, status="waiting_permission", permission_requests=view.permission_requests + (request,))
    if event.type == "permission_resolved":
        request_id = payload.get("request_id") or payload.get("permission_request_id")
        if not request_id:
            return replace(view, warnings=view.warnings + ("permission_response_missing_id",))
        updated_requests = []
        for request in view.permission_requests:
            if request.request_id != request_id:
                updated_requests.append(request)
                continue
            updated_requests.append(
                replace(
                    request,
                    status="approved" if (payload.get("decision") or payload.get("permission_decision")) == "approve_once" else "denied",
                )
            )
        return replace(
            view,
            status="running" if (payload.get("decision") or payload.get("permission_decision")) == "approve_once" else view.status,
            permission_requests=tuple(updated_requests),
        )
    if event.type == "tool_finished":
        item = trace_payload_to_timeline_item(payload)
        changed_files = list(view.changed_files)
        metadata = _metadata(payload)
        for key in ("changed_files", "touched_paths"):
            value = metadata.get(key)
            if isinstance(value, list):
                for path in value:
                    if isinstance(path, str) and path not in changed_files:
                        changed_files.append(path)
        test_status = view.test_status
        if payload.get("tool_name") == "run_tests":
            status = metadata.get("status")
            if isinstance(status, str):
                test_status = status
        return replace(view, changed_files=tuple(changed_files), test_status=test_status, timeline=view.timeline + (item,))
    if event.type == "run_finished":
        metadata = _metadata(payload)
        status = payload.get("status") or metadata.get("status")
        if not status:
            if payload.get("success") is True:
                status = "success"
            elif payload.get("success") is False:
                status = metadata.get("summary") or payload.get("output_summary") or "failed"
        status_text = str(status or "unknown")
        if status_text not in VALID_RUN_STATUSES:
            status_text = "failed" if payload.get("success") is False else "unknown"
        return replace(
            view,
            status=status_text,
            trace_path=_payload_value(payload, "trace_path", view.trace_path),
            report_path=_payload_value(payload, "report_path", view.report_path),
            report_json_path=_payload_value(payload, "report_json_path", view.report_json_path),
            changed_files=tuple(_payload_value(payload, "changed_files", view.changed_files) or ()),
            test_status=_payload_value(payload, "test_status", view.test_status),
        )
    if event.type == "run_cancelled":
        return replace(view, status="cancelled")
    if event.type == "error":
        warnings = view.warnings + (str(payload.get("error") or "unknown error"),)
        return replace(view, status="failed", warnings=warnings)
    if event.type == "file_changed":
        changed_files = list(view.changed_files)
        path = payload.get("path")
        if isinstance(path, str) and path not in changed_files:
            changed_files.append(path)
        return replace(view, changed_files=tuple(changed_files))
    if event.type == "test_status_changed":
        status = payload.get("status")
        return replace(view, test_status=str(status) if isinstance(status, str) else view.test_status)
    return view


class EventReducer:
    def __init__(self) -> None:
        self.view = AgentRunView()

    def reduce(self, event: TUIEvent) -> AgentRunView:
        self.view = reduce_event(self.view, event)
        return self.view
