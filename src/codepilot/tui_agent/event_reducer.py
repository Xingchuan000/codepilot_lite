from __future__ import annotations

import hashlib
import json
import re
from dataclasses import replace
from typing import Any

from codepilot.permissions import PermissionRequest
from codepilot.tui_agent.models import AgentRunView, TUIEvent, TimelineItem, TranscriptItem


VALID_RUN_STATUSES = {
    "idle",
    "running",
    "waiting_branch_confirmation",
    "waiting_permission",
    "message_complete",
    "success",
    "partial",
    "failed",
    "task_incomplete",
    "cancelled",
    "interrupted",
    "max_steps_exceeded",
    "llm_error",
    "llm_exhausted",
    "unknown",
}


def _truncate_text(text: str, limit: int = 1200) -> str:
    if len(text) <= limit:
        return text
    suffix = "... truncated"
    return f"{text[: max(0, limit - len(suffix))]}{suffix}"


def _safe_dict_preview(value: Any, limit: int = 800) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    preview: dict[str, Any] = {}
    for key, item in value.items():
        key_text = str(key)
        if isinstance(item, dict):
            preview_item: Any = _safe_dict_preview(item, max(80, limit // 4))
        elif isinstance(item, list):
            preview_item = [
                entry if isinstance(entry, (dict, list)) else str(entry)
                for entry in item[:5]
            ]
        elif isinstance(item, str):
            preview_item = _truncate_text(item, max(40, limit // 4))
        else:
            preview_item = item
        candidate = {**preview, key_text: preview_item}
        if len(json.dumps(candidate, ensure_ascii=False)) > limit:
            break
        preview[key_text] = preview_item
        if len(json.dumps(preview, ensure_ascii=False)) >= limit:
            break
    return preview


def _make_item_id(event: TUIEvent, suffix: str) -> str:
    run_id = event.run_id or event.payload.get("run_id") or "run"
    step = event.payload.get("step")
    step_text = str(step) if isinstance(step, int) else "none"
    return "-".join(
        _sanitize_identifier_segment(part)
        for part in (run_id, step_text, event.type, suffix)
    )


def _make_action_item_id(event: TUIEvent, signature: str) -> str:
    run_id = event.run_id or event.payload.get("run_id") or "run"
    digest = hashlib.sha256(signature.encode("utf-8")).hexdigest()[:16]
    return "-".join(
        _sanitize_identifier_segment(part)
        for part in (run_id, "assistant_action", digest)
    )


def _make_user_message_item_id(event: TUIEvent, text: str) -> str:
    run_id = event.run_id or event.payload.get("run_id") or "run"
    signature = f"{event.timestamp}\n{text}"
    digest = hashlib.sha256(signature.encode("utf-8")).hexdigest()[:16]
    return "-".join(
        _sanitize_identifier_segment(part)
        for part in (run_id, "user_message", digest)
    )


def _make_command_item_id(event: TUIEvent, command: str) -> str:
    run_id = event.run_id or event.payload.get("run_id") or "run"
    digest = hashlib.sha256(command.encode("utf-8")).hexdigest()[:16]
    return "-".join(
        _sanitize_identifier_segment(part)
        for part in (run_id, "command_output", digest)
    )


def _sanitize_identifier_segment(text: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "-", text).strip("-")
    return cleaned or "item"


def _string_tuple_or_current(value: Any, current: tuple[str, ...]) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return current
    return tuple(item for item in value if isinstance(item, str))


def _finish_status_text(payload: dict[str, Any]) -> str:
    status = payload.get("status")
    if isinstance(status, str) and status:
        return status
    if payload.get("success") is True:
        return "success"
    if payload.get("success") is False:
        return str(payload.get("summary") or payload.get("output_summary") or "failed")
    return "unknown"


def _has_transcript_body(view: AgentRunView, kind: str, body: str) -> bool:
    return any(item.kind == kind and item.body == body for item in view.transcript)


def _append_transcript(view: AgentRunView, item: TranscriptItem) -> AgentRunView:
    if any(existing.id == item.id for existing in view.transcript):
        return view
    return replace(view, transcript=view.transcript + (item,))


def trace_payload_to_timeline_item(payload: dict[str, Any]) -> TimelineItem:
    return TimelineItem(
        step=payload.get("step") if isinstance(payload.get("step"), int) else None,
        title=str(payload.get("type") or payload.get("event_type") or payload.get("tool_name") or "event"),
        category=str(payload.get("event_type") or "event"),
        status=str(payload.get("status")) if isinstance(payload.get("status"), str) else None,
        tool_name=str(payload.get("tool_name")) if isinstance(payload.get("tool_name"), str) else None,
        policy_decision=str(payload.get("policy_decision")) if isinstance(payload.get("policy_decision"), str) else None,
        executed=payload.get("executed") if isinstance(payload.get("executed"), bool) else None,
        output_summary=str(payload.get("output_summary")) if isinstance(payload.get("output_summary"), str) else None,
    )


def _reduce_run_started(view: AgentRunView, event: TUIEvent) -> AgentRunView:
    payload = event.payload
    return replace(
        view,
        run_id=event.run_id or view.run_id,
        task=str(payload.get("task") or view.task),
        status="running",
    )


def _reduce_user_message(view: AgentRunView, event: TUIEvent) -> AgentRunView:
    text = str(event.payload.get("text") or event.payload.get("message") or "")
    item = TranscriptItem(
        id=_make_user_message_item_id(event, text),
        kind="user_message",
        timestamp=event.timestamp,
        run_id=event.run_id or view.run_id,
        title="You",
        body=_truncate_text(text),
        copy_text=f"You: {text}",
    )
    return _append_transcript(replace(view, task=text, status="running"), item)


def _reduce_llm_call_finished(view: AgentRunView, event: TUIEvent) -> AgentRunView:
    text = str(event.payload.get("output_preview") or event.payload.get("output_summary") or "")
    parsed = _parse_json_output(text)
    if parsed is None:
        # 这里只保留模型原始预览，真正的自然回复正文要等 agent_finished 再落到 transcript。
        return replace(view, last_assistant_message=text or view.last_assistant_message)
    if parsed.get("type") == "finish" or "summary" in parsed:
        return replace(view, last_assistant_message=text or view.last_assistant_message)
    if isinstance(parsed.get("short_rationale"), str) and parsed["short_rationale"].strip():
        item = TranscriptItem(
            id=_make_item_id(event, "assistant_plan"),
            kind="assistant_plan",
            timestamp=event.timestamp,
            run_id=event.run_id or view.run_id,
            title="+ Plan",
            body=_truncate_text(parsed["short_rationale"]),
            copy_text=f"+ Plan: {parsed['short_rationale']}",
        )
        view = _append_transcript(replace(view, last_assistant_message=parsed["short_rationale"]), item)
    return replace(view, last_assistant_message=text or view.last_assistant_message)


def _reduce_agent_observation(view: AgentRunView, event: TUIEvent) -> AgentRunView:
    item = TranscriptItem(
        id=_make_item_id(event, "observation"),
        kind="observation",
        timestamp=event.timestamp,
        run_id=event.run_id or view.run_id,
        title="Observation",
        body=_truncate_text(str(event.payload.get("output_summary") or event.payload.get("output_preview") or "")),
        copy_text=f"Observation: {event.payload.get('output_summary') or event.payload.get('output_preview') or ''}",
    )
    return _append_transcript(view, item)


def _reduce_tool_finished(view: AgentRunView, event: TUIEvent) -> AgentRunView:
    payload = event.payload
    item = trace_payload_to_timeline_item(payload)
    changed_files = list(view.changed_files)
    changed_values = payload.get("changed_files")
    if isinstance(changed_values, (list, tuple)):
        for path in changed_values:
            if isinstance(path, str) and path not in changed_files:
                changed_files.append(path)
    test_status = view.test_status
    if payload.get("tool_name") == "run_tests":
        status = payload.get("status")
        if isinstance(status, str):
            test_status = status
    body = payload.get("output_summary") or payload.get("output_preview") or ""
    success = payload.get("success") is True
    title = f"{'✓' if success else '✗'} {item.tool_name or 'tool'}"
    transcript_item = TranscriptItem(
        id=_make_item_id(event, f"tool_result:{item.tool_name or 'tool'}"),
        kind="tool_result",
        timestamp=event.timestamp,
        run_id=event.run_id or view.run_id,
        step=item.step,
        title=title,
        body=_truncate_text(str(body)),
        tool_name=item.tool_name,
        status="success" if success else "failed",
        copy_text="\n".join(filter(None, [title, str(body)])),
    )
    return _append_transcript(
        replace(
            view,
            changed_files=tuple(changed_files),
            test_status=test_status,
            active_tool=None,
            last_tool_output=str(body),
            timeline=view.timeline + (item,),
        ),
        transcript_item,
    )


def _is_tool_call_action(payload: dict[str, Any]) -> bool:
    if payload.get("action_type") != "tool_call":
        return False
    if payload.get("parse_success") is False:
        return False
    if payload.get("finish_blocked_by_evidence") is True:
        return False
    return isinstance(payload.get("tool_name"), str) and bool(str(payload["tool_name"]).strip())


def _reduce_agent_action(view: AgentRunView, event: TUIEvent) -> AgentRunView:
    payload = event.payload
    if not _is_tool_call_action(payload):
        return view
    item = trace_payload_to_timeline_item(payload)
    preview = payload.get("input_preview")
    if not isinstance(preview, dict):
        preview = {}
    tool_name = str(payload.get("tool_name") or item.tool_name or "")
    signature = json.dumps({"tool_name": tool_name, "arguments": preview}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    transcript_item = TranscriptItem(
        id=_make_action_item_id(event, signature),
        kind="assistant_action",
        timestamp=event.timestamp,
        run_id=event.run_id or view.run_id,
        step=item.step,
        title="→",
        body=_truncate_text(f"{tool_name} {json.dumps(preview, ensure_ascii=False, sort_keys=True)}"),
        tool_name=tool_name,
        input_preview=preview,
        copy_text=f"→ {tool_name} {json.dumps(preview, ensure_ascii=False, sort_keys=True)}",
    )
    return _append_transcript(
        replace(
            view,
            current_step=item.step,
            current_tool=item.tool_name,
            active_tool=item.tool_name,
            timeline=view.timeline + (item,),
        ),
        transcript_item,
    )


def _reduce_policy_decision(view: AgentRunView, event: TUIEvent) -> AgentRunView:
    item = trace_payload_to_timeline_item(event.payload)
    status = "waiting_permission" if item.policy_decision == "ask" and event.payload.get("approved") is False else view.status
    return replace(view, status=status, timeline=view.timeline + (item,))


def _reduce_permission_requested(view: AgentRunView, event: TUIEvent) -> AgentRunView:
    request_id = event.payload.get("request_id")
    if not request_id:
        return replace(view, warnings=view.warnings + ("permission_request_missing_id",))
    if any(request.request_id == request_id for request in view.permission_requests):
        return replace(view, status="waiting_permission")
    arguments_preview = event.payload.get("arguments_preview")
    request = PermissionRequest(
        request_id=str(request_id),
        run_id=str(event.payload.get("run_id") or view.run_id or ""),
        action_id=event.payload.get("action_id"),
        tool_name=str(event.payload.get("tool_name") or ""),
        arguments_preview=arguments_preview if isinstance(arguments_preview, dict) else {},
        reason=str(event.payload.get("reason") or ""),
        risk=event.payload.get("risk") if isinstance(event.payload.get("risk"), str) else None,
        side_effect=event.payload.get("side_effect") if isinstance(event.payload.get("side_effect"), str) else None,
        matched_rule=event.payload.get("matched_rule") if isinstance(event.payload.get("matched_rule"), str) else None,
        created_at=str(event.payload.get("created_at") or event.timestamp),
        status="pending",
    )
    item = TranscriptItem(
        id=_make_item_id(event, f"permission_request:{request_id}"),
        kind="permission_request",
        timestamp=event.timestamp,
        run_id=event.run_id or view.run_id,
        title=f"Permission required: {request.tool_name}",
        body="\n".join(
            [
                f"Reason: {request.reason}",
                f"Arguments: {json.dumps(_safe_dict_preview(request.arguments_preview) or {}, ensure_ascii=False, sort_keys=True)}",
            ]
        ),
        tool_name=request.tool_name,
        input_preview=_safe_dict_preview(request.arguments_preview),
        copy_text="\n".join(
            [
                f"? Permission required: {request.tool_name}",
                f"Reason: {request.reason}",
                f"Arguments: {json.dumps(_safe_dict_preview(request.arguments_preview) or {}, ensure_ascii=False, sort_keys=True)}",
            ]
        ),
    )
    return _append_transcript(
        replace(view, status="waiting_permission", permission_requests=view.permission_requests + (request,)),
        item,
    )


def _reduce_permission_resolved(view: AgentRunView, event: TUIEvent) -> AgentRunView:
    request_id = event.payload.get("request_id")
    if not request_id:
        return replace(view, warnings=view.warnings + ("permission_response_missing_id",))
    decision = event.payload.get("decision")
    updated_requests = []
    for request in view.permission_requests:
        if request.request_id != request_id:
            updated_requests.append(request)
            continue
        updated_requests.append(replace(request, status="approved" if decision == "approve_once" else "denied"))
    title = "Permission approved once" if decision == "approve_once" else "Permission denied"
    item = TranscriptItem(
        id=_make_item_id(event, f"permission_response:{request_id}"),
        kind="permission_response",
        timestamp=event.timestamp,
        run_id=event.run_id or view.run_id,
        title=title,
        body=str(event.payload.get("reason") or ""),
        status="approved" if decision == "approve_once" else "denied",
        copy_text="✓ Approved once" if decision == "approve_once" else "✗ Denied",
    )
    return _append_transcript(
        replace(
            view,
            status="running" if decision == "approve_once" else view.status,
            permission_requests=tuple(updated_requests),
        ),
        item,
    )


def _finish_view_from_payload(view: AgentRunView, payload: dict[str, Any], *, validate_status: bool) -> AgentRunView:
    status_text = _finish_status_text(payload)
    if validate_status and status_text not in VALID_RUN_STATUSES:
        status_text = "failed" if payload.get("success") is False else "unknown"
    return replace(
        view,
        status=status_text,
        changed_files=_string_tuple_or_current(payload.get("changed_files"), view.changed_files),
        test_status=str(payload.get("test_status")) if isinstance(payload.get("test_status"), str) else view.test_status,
        completion_kind=str(payload.get("completion_kind")) if isinstance(payload.get("completion_kind"), str) else view.completion_kind,
        assistant_stop_reason=str(payload.get("assistant_stop_reason")) if isinstance(payload.get("assistant_stop_reason"), str) else view.assistant_stop_reason,
        delivery_kind=str(payload.get("delivery_kind")) if isinstance(payload.get("delivery_kind"), str) else view.delivery_kind,
        requires_evidence=payload.get("requires_evidence") if isinstance(payload.get("requires_evidence"), bool) else view.requires_evidence,
        evidence_reasons=_string_tuple_or_current(payload.get("evidence_reasons"), view.evidence_reasons),
        write_attempted=payload.get("write_attempted") if isinstance(payload.get("write_attempted"), bool) else view.write_attempted,
        write_executed=payload.get("write_executed") if isinstance(payload.get("write_executed"), bool) else view.write_executed,
        written_files=_string_tuple_or_current(payload.get("written_files"), view.written_files),
        observed_changed_files=_string_tuple_or_current(payload.get("observed_changed_files"), view.observed_changed_files),
        claimed_changed_files=_string_tuple_or_current(payload.get("claimed_changed_files"), view.claimed_changed_files),
        tests_required=payload.get("tests_required") if isinstance(payload.get("tests_required"), bool) else view.tests_required,
        diff_required=payload.get("diff_required") if isinstance(payload.get("diff_required"), bool) else view.diff_required,
        diff_checked=payload.get("diff_checked") if isinstance(payload.get("diff_checked"), bool) else view.diff_checked,
        missing_evidence=_string_tuple_or_current(payload.get("missing_evidence"), view.missing_evidence),
    )


def _reduce_agent_finished(view: AgentRunView, event: TUIEvent) -> AgentRunView:
    payload = event.payload
    updated_view = _finish_view_from_payload(view, payload, validate_status=False)
    summary = str(payload.get("output_summary") or payload.get("summary") or "")
    if updated_view.status == "message_complete":
        # message_complete 只应该生成一次完整的 Assistant 正文，避免把预览和最终正文重复展示。
        if not summary:
            return updated_view
        if _has_transcript_body(updated_view, "assistant_raw", summary):
            return replace(updated_view, last_assistant_message=summary)
        return _append_transcript(
            replace(updated_view, last_assistant_message=summary),
            TranscriptItem(
                id=_make_item_id(event, "assistant_raw"),
                kind="assistant_raw",
                timestamp=event.timestamp,
                run_id=event.run_id or view.run_id,
                title="Assistant",
                body=summary,
                copy_text=f"Assistant: {summary}",
            ),
        )
    if updated_view.status == "task_incomplete":
        status_body = "\n".join(
            filter(
                None,
                [
                    "Task incomplete.",
                    f"Missing evidence: {', '.join(updated_view.missing_evidence) if updated_view.missing_evidence else 'unknown'}",
                ],
            )
        )
        if _has_transcript_body(view, "system_status", status_body):
            return updated_view
        item = TranscriptItem(
            id=_make_item_id(event, "final_summary"),
            kind="system_status",
            timestamp=event.timestamp,
            run_id=event.run_id or view.run_id,
            title="Task incomplete",
            body=_truncate_text(status_body),
            copy_text=status_body,
        )
        return _append_transcript(updated_view, item)
    item = TranscriptItem(
        id=_make_item_id(event, "final_summary"),
        kind="final_summary",
        timestamp=event.timestamp,
        run_id=event.run_id or view.run_id,
        title="Final",
        body=summary,
        copy_text=f"Final: {summary}",
        status=updated_view.status,
    )
    return _append_transcript(updated_view, item)


def _reduce_run_finished(view: AgentRunView, event: TUIEvent) -> AgentRunView:
    payload = event.payload
    updated_view = _finish_view_from_payload(view, payload, validate_status=True)
    status_body = f"Run finished: {updated_view.status}"
    if _has_transcript_body(view, "system_status", status_body):
        return updated_view
    if any(item.kind == "final_summary" for item in view.transcript):
        return updated_view
    return _append_transcript(
        updated_view,
        TranscriptItem(
            id=_make_item_id(event, "run_finished"),
            kind="system_status",
            timestamp=event.timestamp,
            run_id=event.run_id or view.run_id,
            title="Run finished",
            body=_truncate_text(status_body),
            copy_text=status_body,
        ),
    )


def _reduce_command_output(view: AgentRunView, event: TUIEvent) -> AgentRunView:
    command = str(event.payload.get("command") or "")
    output = str(event.payload.get("output") or "")
    item = TranscriptItem(
        id=_make_command_item_id(event, command),
        kind="command_output",
        timestamp=event.timestamp,
        run_id=event.run_id or view.run_id,
        title=f"$ {command}" if command else "$ command",
        body=_truncate_text(output),
        copy_text="\n".join(filter(None, [f"$ {command}" if command else "$ command", output])),
    )
    return _append_transcript(view, item)


def _reduce_error(view: AgentRunView, event: TUIEvent) -> AgentRunView:
    error_message = str(event.payload.get("error") or "unknown error")
    fatal = event.payload.get("fatal") is not False
    source = str(event.payload.get("source") or "unknown")
    body = f"[{source}] {error_message}"
    title = "Error" if fatal else "Warning"
    item = TranscriptItem(
        id=_make_item_id(event, "error"),
        kind="error",
        timestamp=event.timestamp,
        run_id=event.run_id or view.run_id,
        title=title,
        body=_truncate_text(body),
        copy_text=f"! {body}",
    )
    return _append_transcript(
        replace(
            view,
            status="failed" if fatal else view.status,
            warnings=view.warnings + (body,),
        ),
        item,
    )


def _reduce_file_changed(view: AgentRunView, event: TUIEvent) -> AgentRunView:
    changed_files = list(view.changed_files)
    path = event.payload.get("path")
    if isinstance(path, str) and path not in changed_files:
        changed_files.append(path)
    return replace(view, changed_files=tuple(changed_files))


def _reduce_test_status_changed(view: AgentRunView, event: TUIEvent) -> AgentRunView:
    status = event.payload.get("status")
    return replace(view, test_status=str(status) if isinstance(status, str) else view.test_status)


def reduce_event(view: AgentRunView, event: TUIEvent) -> AgentRunView:
    if event.type == "run_started":
        return _reduce_run_started(view, event)
    if event.type == "user_message":
        return _reduce_user_message(view, event)
    if event.type == "llm_call_finished":
        return _reduce_llm_call_finished(view, event)
    if event.type == "agent_observation":
        return _reduce_agent_observation(view, event)
    if event.type == "tool_finished":
        return _reduce_tool_finished(view, event)
    if event.type == "agent_action":
        return _reduce_agent_action(view, event)
    if event.type == "policy_decision":
        return _reduce_policy_decision(view, event)
    if event.type == "permission_requested":
        return _reduce_permission_requested(view, event)
    if event.type == "permission_resolved":
        return _reduce_permission_resolved(view, event)
    if event.type == "branch_confirmation_required":
        # 分支变化是可恢复的等待状态，不应进入 fatal error 或污染错误 Transcript。
        return replace(view, status="waiting_branch_confirmation")
    if event.type == "agent_finished":
        return _reduce_agent_finished(view, event)
    if event.type == "run_finished":
        return _reduce_run_finished(view, event)
    if event.type == "tool_execution_uncertain":
        return replace(view, status="recovery_required", warnings=view.warnings + ("tool_execution_uncertain",))
    if event.type == "run_cancelled":
        return replace(view, status="cancelled")
    if event.type == "command_output":
        return _reduce_command_output(view, event)
    if event.type == "error":
        return _reduce_error(view, event)
    if event.type == "file_changed":
        return _reduce_file_changed(view, event)
    if event.type == "test_status_changed":
        return _reduce_test_status_changed(view, event)
    return view


def _parse_json_output(text: str) -> dict[str, Any] | None:
    stripped = text.strip()
    if not stripped.startswith("{") or not stripped.endswith("}"):
        return None
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


class EventReducer:
    def __init__(self) -> None:
        self.view = AgentRunView()

    def reduce(self, event: TUIEvent) -> AgentRunView:
        self.view = reduce_event(self.view, event)
        return self.view
