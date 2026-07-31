from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from codepilot.session.models import (
    ArtifactRecord,
    ContextSummaryRecord,
    MessagePartRecord,
    MessageRecord,
    PermissionGrantRecord,
    PermissionRequestRecord,
    PermissionResponseRecord,
    ProjectRecord,
    RunAttemptRecord,
    SessionEventRecord,
    SessionRecord,
    SessionSummary,
    ToolCallRecord,
    ToolResultRecord,
    TurnRecord,
)


def _row_dict(row: Any) -> dict[str, Any]:
    return dict(row)


def _json_loads(value: str) -> Any:
    return json.loads(value)


def _int_to_bool(value: Any) -> bool:
    return bool(int(value))


def _content_preview(content: Any) -> str:
    if isinstance(content, str):
        return " ".join(content.split())[:120]
    return json.dumps(content, ensure_ascii=False, separators=(",", ":"))[:120]


def project_from_row(row: Any) -> ProjectRecord:
    data = _row_dict(row)
    return ProjectRecord(project_id=data["project_id"], path=Path(data["path"]), created_at=data["created_at"], updated_at=data["updated_at"])


def session_from_row(row: Any) -> SessionRecord:
    data = _row_dict(row)
    return SessionRecord(
        session_id=data["session_id"],
        project_id=data["project_id"],
        title=data["title"],
        provider=data["provider"],
        current_model=data["current_model"],
        permission_mode=data["permission_mode"],
        initial_branch=data["initial_branch"],
        current_branch=data["current_branch"],
        status=data["status"],
        parent_session_id=data["parent_session_id"],
        forked_from_turn_id=data["forked_from_turn_id"],
        created_at=data["created_at"],
        updated_at=data["updated_at"],
        last_activity_at=data["last_activity_at"],
        metadata=_json_loads(data["metadata_json"]),
    )


def session_summary_from_row(row: Any) -> SessionSummary:
    data = _row_dict(row)
    return SessionSummary(
        session_id=data["session_id"],
        project_id=data["project_id"],
        title=data["title"],
        provider=data["provider"],
        current_model=data["current_model"],
        permission_mode=data["permission_mode"],
        status=data["status"],
        current_branch=data["current_branch"],
        last_activity_at=data["last_activity_at"],
        created_at=data["created_at"],
        updated_at=data["updated_at"],
        project_path=Path(data["project_path"]),
        project_exists=Path(data["project_path"]).exists(),
        last_user_preview=_content_preview(_json_loads(data["last_user_content"])) if data.get("last_user_content") is not None else None,
    )


def turn_from_row(row: Any) -> TurnRecord:
    data = _row_dict(row)
    return TurnRecord(
        turn_id=data["turn_id"],
        session_id=data["session_id"],
        sequence=data["sequence"],
        title=data["title"],
        status=data["status"],
        provider_snapshot=data["provider_snapshot"],
        model_snapshot=data["model_snapshot"],
        permission_mode_snapshot=data["permission_mode_snapshot"],
        branch_snapshot=data["branch_snapshot"],
        created_at=data["created_at"],
        updated_at=data["updated_at"],
        last_activity_at=data["last_activity_at"],
        user_message_id=data.get("user_message_id"),
        started_at=data.get("started_at"),
        completed_at=data.get("completed_at"),
        error_code=data.get("error_code"),
        metadata=_json_loads(data["metadata_json"]),
    )


def attempt_from_row(row: Any) -> RunAttemptRecord:
    data = _row_dict(row)
    return RunAttemptRecord(
        attempt_id=data["attempt_id"],
        turn_id=data["turn_id"],
        attempt_number=data["attempt_number"],
        status=data["status"],
        created_at=data["created_at"],
        updated_at=data["updated_at"],
        started_at=data["started_at"],
        ended_at=data["ended_at"],
        interruption_reason=data["interruption_reason"],
        worker_id=data["worker_id"],
        lease_expires_at=data["lease_expires_at"],
        metadata=_json_loads(data["metadata_json"]),
    )


def message_from_row(row: Any) -> MessageRecord:
    data = _row_dict(row)
    return MessageRecord(
        message_id=data["message_id"],
        session_id=data["session_id"],
        turn_id=data["turn_id"],
        attempt_id=data["attempt_id"],
        role=data["role"],
        status=data["status"],
        content=_json_loads(data["content_json"]),
        created_at=data["created_at"],
        updated_at=data["updated_at"],
        interrupted_at=data["interrupted_at"],
        metadata=_json_loads(data["metadata_json"]),
    )


def message_part_from_row(row: Any) -> MessagePartRecord:
    data = _row_dict(row)
    return MessagePartRecord(
        part_id=data["part_id"],
        message_id=data["message_id"],
        sequence=data["sequence"],
        type=data["type"],
        content=_json_loads(data["content_json"]),
        provider_format=data["provider_format"],
        replayable=_int_to_bool(data["replayable"]),
        created_at=data["created_at"],
        artifact_id=data["artifact_id"],
        metadata=_json_loads(data["metadata_json"]),
    )


def tool_call_from_row(row: Any) -> ToolCallRecord:
    data = _row_dict(row)
    return ToolCallRecord(
        tool_call_id=data["tool_call_id"],
        turn_id=data["turn_id"],
        attempt_id=data["attempt_id"],
        message_id=data["message_id"],
        status=data["status"],
        tool_name=data["tool_name"],
        arguments=_json_loads(data["arguments_json"]),
        created_at=data["created_at"],
        updated_at=data["updated_at"],
        started_at=data["started_at"],
        completed_at=data["completed_at"],
        side_effect=data["side_effect"],
        idempotency=data["idempotency"],
        recovery_strategy=data["recovery_strategy"],
        recovery_token=_json_loads(data["recovery_token_json"]) if data["recovery_token_json"] is not None else None,
        metadata=_json_loads(data["metadata_json"]),
    )


def tool_result_from_row(row: Any) -> ToolResultRecord:
    data = _row_dict(row)
    return ToolResultRecord(
        tool_result_id=data["tool_result_id"],
        tool_call_id=data["tool_call_id"],
        status=data["status"],
        content=_json_loads(data["content_json"]),
        created_at=data["created_at"],
        output_preview=data["output_preview"],
        artifact_id=data["artifact_id"],
        error=data["error"],
        success=_int_to_bool(data["success"]) if data["success"] is not None else None,
        metadata=_json_loads(data["metadata_json"]),
    )


def permission_request_from_row(row: Any) -> PermissionRequestRecord:
    data = _row_dict(row)
    return PermissionRequestRecord(
        request_id=data["request_id"],
        session_id=data["session_id"],
        turn_id=data["turn_id"],
        attempt_id=data["attempt_id"],
        tool_call_id=data["tool_call_id"],
        scope_key=data["scope_key"],
        tool_name=data["tool_name"],
        arguments=_json_loads(data["arguments_json"]),
        reason=data["reason"],
        status=data["status"],
        created_at=data["created_at"],
        metadata=_json_loads(data["metadata_json"]),
    )


def permission_response_from_row(row: Any) -> PermissionResponseRecord:
    data = _row_dict(row)
    return PermissionResponseRecord(
        response_id=data["response_id"],
        request_id=data["request_id"],
        decision=data["decision"],
        reason=data["reason"],
        responded_at=data["responded_at"],
        metadata=_json_loads(data["metadata_json"]),
    )


def permission_grant_from_row(row: Any) -> PermissionGrantRecord:
    data = _row_dict(row)
    return PermissionGrantRecord(
        grant_id=data["grant_id"],
        session_id=data["session_id"],
        scope_key=data["scope_key"],
        created_at=data["created_at"],
        revoked_at=data["revoked_at"],
        tool_name=data["tool_name"],
        scope_json=_json_loads(data["scope_json"]) if data["scope_json"] is not None else None,
        metadata=_json_loads(data["metadata_json"]),
    )


def event_from_row(row: Any) -> SessionEventRecord:
    data = _row_dict(row)
    return SessionEventRecord(
        event_id=data["event_id"],
        session_id=data["session_id"],
        sequence=data["sequence"],
        event_type=data["event_type"],
        created_at=data["created_at"],
        turn_id=data["turn_id"],
        attempt_id=data["attempt_id"],
        payload=_json_loads(data["payload_json"]),
        metadata=_json_loads(data["metadata_json"]),
    )


def context_summary_from_row(row: Any) -> ContextSummaryRecord:
    data = _row_dict(row)
    return ContextSummaryRecord(
        summary_id=data["summary_id"],
        session_id=data["session_id"],
        turn_id=data["turn_id"],
        created_at=data["created_at"],
        content=_json_loads(data["content_json"]),
        source_start_sequence=data.get("source_start_sequence"),
        source_end_sequence=data.get("source_end_sequence"),
        summary_message_id=data.get("summary_message_id"),
        model=data.get("model"),
        status=data.get("status") or "completed",
        metadata=_json_loads(data["metadata_json"]),
    )


def artifact_from_row(row: Any) -> ArtifactRecord:
    data = _row_dict(row)
    return ArtifactRecord(
        artifact_id=data["artifact_id"],
        session_id=data["session_id"],
        kind=data["kind"],
        mime_type=data["mime_type"],
        size_bytes=data["size_bytes"],
        sha256=data["sha256"],
        storage_path=data["storage_path"],
        created_at=data["created_at"],
        content=_json_loads(data["content_json"]) if data["content_json"] is not None else None,
        metadata=_json_loads(data["metadata_json"]),
    )
