from __future__ import annotations

import hashlib
import json
import re
from dataclasses import replace
from typing import Any

from codepilot.tui_agent.models import AgentRunView, PermissionRequest, TUIEvent, TimelineItem, TranscriptItem


VALID_RUN_STATUSES = {
    "idle",
    "running",
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


def _metadata(payload: dict[str, Any]) -> dict[str, Any]:
    metadata = payload.get("metadata")
    return metadata if isinstance(metadata, dict) else {}


def _payload_value(payload: dict[str, Any], key: str, fallback: Any) -> Any:
    return payload[key] if key in payload else fallback


def _event_value(payload: dict[str, Any], key: str, fallback: Any) -> Any:
    if key in payload:
        return payload[key]
    metadata = _metadata(payload)
    if key in metadata:
        return metadata[key]
    return fallback


def _truncate_text(text: str, limit: int = 1200) -> str:
    if len(text) <= limit:
        return text
    suffix = "... truncated"
    return f"{text[: max(0, limit - len(suffix))]}{suffix}"


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
            preview_item = [_truncate_text(str(entry), max(40, limit // 8)) if not isinstance(entry, (dict, list)) else entry for entry in item[:5]]
        elif isinstance(item, str):
            preview_item = _truncate_text(item, max(40, limit // 4))
        else:
            preview_item = item
        candidate = {**preview, key_text: preview_item}
        if len(json.dumps(candidate, ensure_ascii=False)) > limit:
            break
        preview[key_text] = preview_item
        current_length = len(json.dumps(preview, ensure_ascii=False))
        if current_length >= limit:
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
    return "-".join(_sanitize_identifier_segment(part) for part in (run_id, "assistant_action", digest))


def _make_user_message_item_id(event: TUIEvent, text: str) -> str:
    run_id = event.run_id or event.payload.get("run_id") or "run"
    signature = f"{event.timestamp}\n{text}"
    digest = hashlib.sha256(signature.encode("utf-8")).hexdigest()[:16]
    return "-".join(_sanitize_identifier_segment(part) for part in (run_id, "user_message", digest))


def _make_command_item_id(event: TUIEvent, command: str) -> str:
    run_id = event.run_id or event.payload.get("run_id") or "run"
    digest = hashlib.sha256(command.encode("utf-8")).hexdigest()[:16]
    return "-".join(_sanitize_identifier_segment(part) for part in (run_id, "command_output", digest))


def _sanitize_identifier_segment(text: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "-", text).strip("-")
    return cleaned or "item"


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


def _append_transcript(view: AgentRunView, item: TranscriptItem) -> AgentRunView:
    if any(existing.id == item.id for existing in view.transcript):
        return view
    return replace(view, transcript=view.transcript + (item,))


def _has_transcript_body(view: AgentRunView, kind: str, body: str) -> bool:
    return any(item.kind == kind and item.body == body for item in view.transcript)


def _canonical_action_input(payload: dict[str, Any]) -> dict[str, Any] | None:
    """提取工具动作里真正应该展示的 arguments，而不是整块 action 对象。"""

    action_input = payload.get("input")
    if not isinstance(action_input, dict):
        return None
    arguments = action_input.get("arguments")
    if isinstance(arguments, dict):
        return _safe_dict_preview(arguments)
    if {"type", "tool_name", "arguments", "short_rationale"} & action_input.keys():
        return None
    return _safe_dict_preview(action_input)


def _canonical_tool_name(payload: dict[str, Any]) -> str | None:
    """从真实 trace 结构和旧版简化结构里都拿到一致的工具名。"""

    tool_name = payload.get("tool_name")
    if isinstance(tool_name, str) and tool_name.strip():
        return tool_name.strip()
    action_input = payload.get("input")
    if isinstance(action_input, dict):
        nested = action_input.get("tool_name")
        if isinstance(nested, str) and nested.strip():
            return nested.strip()
    return None


def _tool_input_preview(payload: dict[str, Any]) -> dict[str, Any] | None:
    preview = _canonical_action_input(payload)
    if preview is not None:
        return preview
    metadata = _metadata(payload)
    if isinstance(metadata.get("normalized_action_preview"), dict):
        normalized_preview = _safe_dict_preview(metadata.get("normalized_action_preview"))
        if isinstance(normalized_preview, dict):
            return _canonical_action_input({"input": normalized_preview}) or normalized_preview
    if isinstance(metadata.get("raw_action_preview"), dict):
        raw_preview = _safe_dict_preview(metadata.get("raw_action_preview"))
        if isinstance(raw_preview, dict):
            return _canonical_action_input({"input": raw_preview}) or raw_preview
    return None


def _llm_output_text(payload: dict[str, Any]) -> str:
    metadata = _metadata(payload)
    for key in ("output_preview", "output_summary"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value
    for key in ("output_preview", "output_summary"):
        value = metadata.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return ""


def _parse_json_output(text: str) -> dict[str, Any] | None:
    stripped = text.strip()
    if not stripped.startswith("{") or not stripped.endswith("}"):
        return None
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _finish_status_text(payload: dict[str, Any]) -> str:
    metadata = _metadata(payload)
    status = payload.get("status") or metadata.get("status")
    if not status:
        if payload.get("success") is True:
            return "success"
        if payload.get("success") is False:
            return str(metadata.get("summary") or payload.get("output_summary") or "failed")
        return "unknown"
    return str(status)


def _is_tool_call_action(payload: dict[str, Any]) -> bool:
    metadata = _metadata(payload)
    if metadata.get("action_type") != "tool_call":
        return False
    if metadata.get("parse_success") is False:
        return False
    if metadata.get("finish_blocked_by_evidence") is True:
        return False
    return _canonical_tool_name(payload) is not None


def reduce_event(view: AgentRunView, event: TUIEvent) -> AgentRunView:
    payload = event.payload
    metadata = _metadata(payload)
    if event.type == "run_started":
        return replace(
            view,
            run_id=event.run_id or view.run_id,
            task=str(metadata.get("task") or view.task),
            status="running",
            trace_path=payload.get("trace_path") or view.trace_path,
        )
    if event.type == "user_message":
        text = str(payload.get("text") or payload.get("message") or "")
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
    if event.type == "llm_call_finished":
        text = _llm_output_text(payload)
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
    if event.type == "agent_observation":
        item = TranscriptItem(
            id=_make_item_id(event, "observation"),
            kind="observation",
            timestamp=event.timestamp,
            run_id=event.run_id or view.run_id,
            title="Observation",
            body=_truncate_text(str(payload.get("output_summary") or payload.get("output_preview") or "")),
            copy_text=f"Observation: {payload.get('output_summary') or payload.get('output_preview') or ''}",
        )
        return _append_transcript(view, item)
    if event.type == "agent_action":
        if not _is_tool_call_action(payload):
            return view
        item = trace_payload_to_timeline_item(payload)
        preview = _tool_input_preview(payload)
        tool_name = _canonical_tool_name(payload) or item.tool_name or ""
        signature = json.dumps({"tool_name": tool_name, "arguments": preview or {}}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        transcript_item = TranscriptItem(
            id=_make_action_item_id(event, signature),
            kind="assistant_action",
            timestamp=event.timestamp,
            run_id=event.run_id or view.run_id,
            step=item.step,
            title="→",
            body=_truncate_text(f"{tool_name} {json.dumps(preview or {}, ensure_ascii=False, sort_keys=True)}"),
            tool_name=tool_name,
            input_preview=preview,
            copy_text=f"→ {tool_name} {json.dumps(preview or {}, ensure_ascii=False, sort_keys=True)}",
        )
        return _append_transcript(
            replace(view, current_step=item.step, current_tool=item.tool_name, active_tool=item.tool_name, timeline=view.timeline + (item,)),
            transcript_item,
        )
    if event.type == "policy_decision":
        item = trace_payload_to_timeline_item(payload)
        status = "waiting_permission" if item.policy_decision == "ask" and metadata.get("approved") is False else view.status
        return replace(view, status=status, timeline=view.timeline + (item,))
    if event.type == "permission_requested":
        request_id = payload.get("request_id") or payload.get("permission_request_id")
        reason = payload.get("reason") or metadata.get("reason") or ""
        arguments_preview = payload.get("arguments_preview") or metadata.get("arguments_preview") or {}
        if not request_id:
            return replace(view, warnings=view.warnings + ("permission_request_missing_id",))
        if any(request.request_id == request_id for request in view.permission_requests):
            return replace(view, status="waiting_permission")
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
        return _append_transcript(replace(view, status="waiting_permission", permission_requests=view.permission_requests + (request,)), item)
    if event.type == "permission_resolved":
        request_id = payload.get("request_id") or payload.get("permission_request_id")
        if not request_id:
            return replace(view, warnings=view.warnings + ("permission_response_missing_id",))
        decision = payload.get("decision") or payload.get("permission_decision")
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
            body=str(payload.get("reason") or metadata.get("reason") or ""),
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
    if event.type == "tool_finished":
        item = trace_payload_to_timeline_item(payload)
        changed_files = list(view.changed_files)
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
        body = (
            payload.get("output_summary")
            if isinstance(payload.get("output_summary"), str)
            else payload.get("output_preview")
            if isinstance(payload.get("output_preview"), str)
            else metadata.get("output_summary")
            if isinstance(metadata.get("output_summary"), str)
            else metadata.get("output_preview")
            if isinstance(metadata.get("output_preview"), str)
            else ""
        )
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
    if event.type == "agent_finished":
        status_text = _finish_status_text(payload)
        summary = str(payload.get("output_summary") or metadata.get("summary") or payload.get("summary") or "")
        updated_view = replace(
            view,
            status=status_text,
            last_assistant_message=summary or view.last_assistant_message,
            completion_kind=_event_value(payload, "completion_kind", view.completion_kind) if isinstance(_event_value(payload, "completion_kind", view.completion_kind), str) else view.completion_kind,
            assistant_stop_reason=_event_value(payload, "assistant_stop_reason", view.assistant_stop_reason)
            if isinstance(_event_value(payload, "assistant_stop_reason", view.assistant_stop_reason), str)
            else view.assistant_stop_reason,
            delivery_kind=_event_value(payload, "delivery_kind", view.delivery_kind) if isinstance(_event_value(payload, "delivery_kind", view.delivery_kind), str) else view.delivery_kind,
            requires_evidence=_event_value(payload, "requires_evidence", view.requires_evidence) if isinstance(_event_value(payload, "requires_evidence", view.requires_evidence), bool) else view.requires_evidence,
            evidence_reasons=tuple(item for item in _event_value(payload, "evidence_reasons", view.evidence_reasons) if isinstance(item, str))
            if isinstance(_event_value(payload, "evidence_reasons", view.evidence_reasons), list)
            else view.evidence_reasons,
            write_attempted=_event_value(payload, "write_attempted", view.write_attempted) if isinstance(_event_value(payload, "write_attempted", view.write_attempted), bool) else view.write_attempted,
            write_executed=_event_value(payload, "write_executed", view.write_executed) if isinstance(_event_value(payload, "write_executed", view.write_executed), bool) else view.write_executed,
            written_files=tuple(item for item in _event_value(payload, "written_files", view.written_files) if isinstance(item, str))
            if isinstance(_event_value(payload, "written_files", view.written_files), list)
            else view.written_files,
            observed_changed_files=tuple(item for item in _event_value(payload, "observed_changed_files", view.observed_changed_files) if isinstance(item, str))
            if isinstance(_event_value(payload, "observed_changed_files", view.observed_changed_files), list)
            else view.observed_changed_files,
            claimed_changed_files=tuple(item for item in _event_value(payload, "claimed_changed_files", view.claimed_changed_files) if isinstance(item, str))
            if isinstance(_event_value(payload, "claimed_changed_files", view.claimed_changed_files), list)
            else view.claimed_changed_files,
            tests_required=_event_value(payload, "tests_required", view.tests_required) if isinstance(_event_value(payload, "tests_required", view.tests_required), bool) else view.tests_required,
            diff_required=_event_value(payload, "diff_required", view.diff_required) if isinstance(_event_value(payload, "diff_required", view.diff_required), bool) else view.diff_required,
            diff_checked=_event_value(payload, "diff_checked", view.diff_checked) if isinstance(_event_value(payload, "diff_checked", view.diff_checked), bool) else view.diff_checked,
            missing_evidence=tuple(item for item in _event_value(payload, "missing_evidence", view.missing_evidence) if isinstance(item, str))
            if isinstance(_event_value(payload, "missing_evidence", view.missing_evidence), list)
            else view.missing_evidence,
        )
        if status_text == "message_complete":
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
        if status_text == "task_incomplete":
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
            status=status_text,
        )
        return _append_transcript(updated_view, item)
    if event.type == "run_finished":
        status_text = _finish_status_text(payload)
        if status_text not in VALID_RUN_STATUSES:
            status_text = "failed" if payload.get("success") is False else "unknown"
        metadata_written_files = metadata.get("written_files")
        metadata_observed_changed_files = metadata.get("observed_changed_files")
        metadata_claimed_changed_files = metadata.get("claimed_changed_files")
        metadata_missing_evidence = metadata.get("missing_evidence")
        updated_view = replace(
            view,
            status=status_text,
            trace_path=_payload_value(payload, "trace_path", view.trace_path),
            report_path=_payload_value(payload, "report_path", view.report_path),
            report_json_path=_payload_value(payload, "report_json_path", view.report_json_path),
            changed_files=tuple(_payload_value(payload, "changed_files", view.changed_files) or ()),
            test_status=_payload_value(payload, "test_status", view.test_status),
            completion_kind=_event_value(payload, "completion_kind", view.completion_kind) if isinstance(_event_value(payload, "completion_kind", view.completion_kind), str) else view.completion_kind,
            assistant_stop_reason=_event_value(payload, "assistant_stop_reason", view.assistant_stop_reason)
            if isinstance(_event_value(payload, "assistant_stop_reason", view.assistant_stop_reason), str)
            else view.assistant_stop_reason,
            delivery_kind=_event_value(payload, "delivery_kind", view.delivery_kind) if isinstance(_event_value(payload, "delivery_kind", view.delivery_kind), str) else view.delivery_kind,
            requires_evidence=_event_value(payload, "requires_evidence", view.requires_evidence) if isinstance(_event_value(payload, "requires_evidence", view.requires_evidence), bool) else view.requires_evidence,
            evidence_reasons=tuple(item for item in _event_value(payload, "evidence_reasons", view.evidence_reasons) if isinstance(item, str))
            if isinstance(_event_value(payload, "evidence_reasons", view.evidence_reasons), list)
            else view.evidence_reasons,
            write_attempted=_event_value(payload, "write_attempted", view.write_attempted) if isinstance(_event_value(payload, "write_attempted", view.write_attempted), bool) else view.write_attempted,
            write_executed=_event_value(payload, "write_executed", view.write_executed) if isinstance(_event_value(payload, "write_executed", view.write_executed), bool) else view.write_executed,
            written_files=tuple(item for item in _event_value(payload, "written_files", view.written_files) if isinstance(item, str))
            if isinstance(_event_value(payload, "written_files", view.written_files), list)
            else view.written_files,
            observed_changed_files=tuple(item for item in _event_value(payload, "observed_changed_files", view.observed_changed_files) if isinstance(item, str))
            if isinstance(_event_value(payload, "observed_changed_files", view.observed_changed_files), list)
            else view.observed_changed_files,
            claimed_changed_files=tuple(item for item in _event_value(payload, "claimed_changed_files", view.claimed_changed_files) if isinstance(item, str))
            if isinstance(_event_value(payload, "claimed_changed_files", view.claimed_changed_files), list)
            else view.claimed_changed_files,
            tests_required=_event_value(payload, "tests_required", view.tests_required) if isinstance(_event_value(payload, "tests_required", view.tests_required), bool) else view.tests_required,
            diff_required=_event_value(payload, "diff_required", view.diff_required) if isinstance(_event_value(payload, "diff_required", view.diff_required), bool) else view.diff_required,
            diff_checked=_event_value(payload, "diff_checked", view.diff_checked) if isinstance(_event_value(payload, "diff_checked", view.diff_checked), bool) else view.diff_checked,
            missing_evidence=tuple(item for item in _event_value(payload, "missing_evidence", view.missing_evidence) if isinstance(item, str))
            if isinstance(_event_value(payload, "missing_evidence", view.missing_evidence), list)
            else view.missing_evidence,
        )
        status_body = f"Run finished: {status_text}"
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
    if event.type == "run_cancelled":
        return replace(view, status="cancelled")
    if event.type == "command_output":
        command = str(payload.get("command") or "")
        output = str(payload.get("output") or "")
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
    if event.type == "error":
        error_message = str(payload.get("error") or "unknown error")
        item = TranscriptItem(
            id=_make_item_id(event, "error"),
            kind="error",
            timestamp=event.timestamp,
            run_id=event.run_id or view.run_id,
            title="!",
            body=_truncate_text(error_message),
            copy_text=f"! {error_message}",
        )
        return _append_transcript(replace(view, status="failed", warnings=view.warnings + (error_message,)), item)
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
