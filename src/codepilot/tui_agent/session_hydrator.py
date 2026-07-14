from __future__ import annotations

import json
from dataclasses import dataclass

from codepilot.permissions import PermissionRequest
from codepilot.session.store import SessionStore
from codepilot.tui_agent.models import TimelineItem, TranscriptItem


@dataclass(frozen=True)
class HydratedSessionView:
    """把 SQLite 中的 Session 状态一次性整理成 TUI 可直接挂载的视图。"""

    transcript: tuple[TranscriptItem, ...]
    timeline: tuple[TimelineItem, ...]
    permission_requests: tuple[PermissionRequest, ...]
    recovery_state: str | None = None
    status: str = "idle"
    current_step: int | None = None
    current_tool: str | None = None
    active_tool: str | None = None
    last_assistant_message: str | None = None
    last_tool_output: str | None = None
    changed_files: tuple[str, ...] = ()
    test_status: str | None = None
    report_path: str | None = None
    report_json_path: str | None = None
    trace_path: str | None = None
    warnings: tuple[str, ...] = ()
    run_id: str | None = None
    task: str = ""


def hydrate_session_view(store: SessionStore, session_id: str) -> HydratedSessionView:
    """从 SQLite 重新构建 Session 画面。

    这里刻意只保留用户能理解的高层事实，Trace 噪声继续留在 timeline，不塞进主 transcript。
    """

    transcript: list[TranscriptItem] = []
    timeline: list[TimelineItem] = []
    permission_requests: list[PermissionRequest] = []
    last_user_text = ""
    last_assistant_text: str | None = None
    last_tool_output: str | None = None
    changed_files: list[str] = []
    test_status: str | None = None
    report_path: str | None = None
    report_json_path: str | None = None
    trace_path: str | None = None
    warnings: list[str] = []

    for event in store.list_events(session_id):
        timeline.append(TimelineItem(step=event.sequence, title=event.event_type, category="session_event", status=None))
        if event.event_type == "branch_changed":
            body = _dump_json(event.payload)
            transcript.append(_item(event.event_id, "system_status", event.created_at, "分支变化", body, copy_text=f"branch_changed: {body}"))
            continue
        if event.event_type in {"context_compacted", "tool_reconciled", "recovery_required"}:
            transcript.append(_item(event.event_id, "system_status", event.created_at, event.event_type, _dump_json(event.payload), copy_text=_dump_json(event.payload)))
            continue
        if event.event_type == "file_changed":
            path = event.payload.get("path")
            if isinstance(path, str):
                changed_files.append(path)
        if event.event_type == "test_status_changed":
            status = event.payload.get("status")
            if isinstance(status, str):
                test_status = status
        if event.event_type == "run_finished":
            trace_path = event.payload.get("trace_path") if isinstance(event.payload.get("trace_path"), str) else trace_path
            report_path = event.payload.get("report_path") if isinstance(event.payload.get("report_path"), str) else report_path
            report_json_path = event.payload.get("report_json_path") if isinstance(event.payload.get("report_json_path"), str) else report_json_path

    for request in store.list_permission_requests(session_id):
        if request.status != "pending":
            continue
        permission_requests.append(_permission_request_from_record(request))
        transcript.append(
            _item(
                request.request_id,
                "permission_request",
                request.created_at,
                f"权限请求: {request.tool_name}",
                request.reason,
                tool_name=request.tool_name,
                copy_text=f"? {request.tool_name}\n{request.reason}",
            )
        )

    for message, parts in store.list_messages_with_parts(session_id):
        body = _message_body(message.role, message.content, parts)
        if message.role == "user":
            last_user_text = body
            transcript.append(_item(message.message_id, "user_message", message.created_at, "用户", body, copy_text=f"You: {body}"))
        elif message.role == "assistant":
            last_assistant_text = body
            transcript.append(_item(message.message_id, "assistant_raw", message.created_at, "Assistant", body, copy_text=f"Assistant: {body}"))
        elif message.role == "tool":
            last_tool_output = body
            # Tool Message 的正文由下面的 ToolResult 业务记录统一渲染，避免 Message 和
            # tool_result Part 以及业务表三次显示同一结果。
            if not any(part.type == "tool_result" for part in parts):
                transcript.append(_item(message.message_id, "tool_result", message.created_at, "工具结果", body, tool_name=message.metadata.get("tool_name"), status=message.status, copy_text=body))
        elif message.role == "system":
            transcript.append(_item(message.message_id, "system_status", message.created_at, "系统", body, copy_text=body))

        if message.status == "interrupted" and message.role == "assistant":
            warnings.append("assistant_message_interrupted")
        for part in parts:
            if part.type == "tool_call":
                transcript.append(
                    _item(
                        part.part_id,
                        "assistant_action",
                        part.created_at,
                        "工具调用",
                        _dump_json(part.content),
                        tool_name=part.metadata.get("tool_name"),
                        copy_text=_dump_json(part.content),
                        metadata={"tool_call_id": part.metadata.get("tool_call_id"), **part.metadata},
                    )
                )
            elif part.type == "tool_result":
                continue

    tool_calls = {call.tool_call_id: call for call in store.list_tool_calls(session_id)}
    rendered_tool_call_ids = {
        str(part.metadata["tool_call_id"])
        for _, parts in store.list_messages_with_parts(session_id)
        for part in parts
        if part.type == "tool_call" and part.metadata.get("tool_call_id")
    }
    for call in tool_calls.values():
        if call.tool_call_id in rendered_tool_call_ids:
            continue
        transcript.append(
            _item(
                call.tool_call_id,
                "assistant_action",
                call.created_at,
                "工具调用",
                _dump_json({"tool_name": call.tool_name, "arguments": call.arguments}),
                tool_name=call.tool_name,
                status=call.status,
                copy_text=_dump_json(call.arguments),
                metadata={"tool_call_id": call.tool_call_id},
            )
        )
    for result in store.list_tool_results(session_id):
        call = tool_calls.get(result.tool_call_id)
        body = result.output_preview or _part_body(result.content)
        transcript.append(
            _item(
                result.tool_result_id,
                "tool_result",
                result.created_at,
                "工具结果",
                body,
                tool_name=call.tool_name if call is not None else None,
                status=result.status,
                copy_text=body,
                metadata={"tool_call_id": result.tool_call_id, "artifact_id": result.artifact_id},
            )
        )

    transcript.sort(key=lambda item: (item.timestamp, _transcript_type_order(item.kind), item.id))

    session = store.get_session(session_id)
    return HydratedSessionView(
        transcript=tuple(transcript),
        timeline=tuple(timeline),
        permission_requests=tuple(permission_requests),
        recovery_state=session.status,
        status="idle",
        last_assistant_message=last_assistant_text,
        last_tool_output=last_tool_output,
        changed_files=tuple(dict.fromkeys(changed_files)),
        test_status=test_status,
        report_path=report_path,
        report_json_path=report_json_path,
        trace_path=trace_path,
        warnings=tuple(warnings),
        task=last_user_text,
    )


def hydrate_transcript(store: SessionStore, session_id: str) -> tuple[TranscriptItem, ...]:
    """只返回按业务表排序的 transcript。"""

    return hydrate_session_view(store, session_id).transcript


def hydrate_timeline(store: SessionStore, session_id: str) -> tuple[TimelineItem, ...]:
    """只返回 Session Event 时间线。"""

    return hydrate_session_view(store, session_id).timeline


def hydrate_pending_permissions(store: SessionStore, session_id: str) -> tuple[PermissionRequest, ...]:
    """当前 pending 直接来自权限业务表，不从历史 Event 推断。"""

    return hydrate_session_view(store, session_id).permission_requests


def hydrate_recovery_state(store: SessionStore, session_id: str) -> str | None:
    return hydrate_session_view(store, session_id).recovery_state


def _item(
    item_id: str,
    kind: str,
    timestamp: str,
    title: str,
    body: str,
    *,
    tool_name: str | None = None,
    status: str | None = None,
    copy_text: str | None = None,
    metadata: dict[str, object] | None = None,
) -> TranscriptItem:
    return TranscriptItem(
        id=item_id,
        kind=kind,  # 只保留用户可理解的高层类别，避免把 trace 噪声直接塞进 transcript。
        timestamp=timestamp,
        title=title,
        body=body,
        tool_name=tool_name,
        status=status,
        copy_text=copy_text or body,
        metadata=metadata or {},
    )


def _dump_json(value: object) -> str:
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)


def _message_body(role: str, content: object, parts) -> str:
    if parts:
        values = [_part_body(part.content) for part in parts if part.replayable]
        if values:
            return "\n".join(values)
    return _dump_json(content)


def _part_body(content: object) -> str:
    return content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)


def _permission_request_from_payload(event) -> PermissionRequest:
    return PermissionRequest(
        request_id=str(event.payload.get("request_id") or event.event_id),
        run_id=str(event.payload.get("run_id") or event.event_id),
        action_id=event.payload.get("action_id"),
        tool_name=str(event.payload.get("tool_name") or ""),
        arguments_preview=event.payload.get("arguments_preview") if isinstance(event.payload.get("arguments_preview"), dict) else {},
        reason=str(event.payload.get("reason") or ""),
        risk=event.payload.get("risk") if isinstance(event.payload.get("risk"), str) else None,
        side_effect=event.payload.get("side_effect") if isinstance(event.payload.get("side_effect"), str) else None,
        matched_rule=event.payload.get("matched_rule") if isinstance(event.payload.get("matched_rule"), str) else None,
        created_at=str(event.created_at),
        status="pending",
        session_id=event.session_id,
        turn_id=event.turn_id,
        attempt_id=event.attempt_id,
        tool_call_id=event.payload.get("tool_call_id") if isinstance(event.payload.get("tool_call_id"), str) else None,
        scope_key=event.payload.get("scope_key") if isinstance(event.payload.get("scope_key"), str) else None,
        scope_json=event.payload.get("scope_json") if isinstance(event.payload.get("scope_json"), dict) else None,
    )


def _permission_request_from_record(record) -> PermissionRequest:
    metadata = record.metadata
    return PermissionRequest(
        request_id=record.request_id,
        run_id=str(metadata.get("run_id") or record.request_id),
        action_id=metadata.get("action_id"),
        tool_name=record.tool_name,
        arguments_preview=record.arguments,
        reason=record.reason,
        risk=metadata.get("risk"),
        side_effect=metadata.get("side_effect"),
        matched_rule=metadata.get("matched_rule"),
        created_at=record.created_at,
        status=record.status,
        session_id=record.session_id,
        turn_id=record.turn_id,
        attempt_id=record.attempt_id,
        tool_call_id=record.tool_call_id,
        scope_key=record.scope_key,
        scope_json=metadata.get("scope_json"),
    )


def _transcript_type_order(kind: str) -> int:
    return {"user_message": 0, "assistant_raw": 1, "assistant_action": 2, "permission_request": 3, "permission_response": 4, "tool_result": 5}.get(kind, 9)
