from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from codepilot.session.database import SessionDatabase
from codepilot.session.ids import now_iso
from codepilot.session.store import SessionStore
from codepilot.trace.events import TraceEvent


class SessionTraceRecorder:
    """把 Loop/Router 事件先写入 SQLite，再通知可选的 UI hook。"""

    def __init__(self, database: SessionDatabase, session_id: str, turn_id: str | None = None, attempt_id: str | None = None, record_hook: Callable[[TraceEvent], None] | None = None) -> None:
        self.store = SessionStore(database)
        self.session_id = session_id
        self.turn_id = turn_id
        self.attempt_id = attempt_id
        self.run_id = f"session-{uuid4().hex[:12]}"
        self.trace_path: Path | None = None
        self._step = 0
        self.record_hook = record_hook
        self.last_record_hook_error: Exception | None = None
        self._last_message_id: str | None = None
        self._streaming_message = False

    @property
    def next_step(self) -> int:
        """兼容现有 traced tool helper 的步骤分配接口。"""

        self._step += 1
        return self._step

    def assistant_message_started(self, **_: Any) -> None:
        self._streaming_message = bool(_.get("streaming", True))
        message = self.store.create_message(session_id=self.session_id, turn_id=self.turn_id or "", role="assistant", status="in_progress", content="")
        self._last_message_id = message.message_id

    def assistant_message_completed(self, *, content: str, **_: Any) -> None:
        if self._last_message_id is None:
            self.assistant_message_started()
        if not self._streaming_message:
            self.store.append_message_part(self._last_message_id, type="text", content=content)
        self.store.update_message_status(self._last_message_id, "completed")
        self._streaming_message = False

    def assistant_text_delta(self, **_: Any) -> None:
        if self._last_message_id is None:
            self.assistant_message_started()
        self.store.append_message_part(self._last_message_id, type=str(_.get("type", "text")), content=str(_.get("content", "")), provider_format=_.get("provider_format"), replayable=bool(_.get("replayable", True)))

    def assistant_message_interrupted(self, **_: Any) -> None:
        if self._last_message_id is not None:
            self.store.update_message_status(self._last_message_id, "interrupted")

    def tool_call_created(self, *, tool_name: str, arguments: dict[str, Any], **_: Any) -> None:
        self.store.create_tool_call(
            turn_id=self.turn_id or "",
            attempt_id=self.attempt_id,
            message_id=self._last_message_id,
            tool_name=tool_name,
            arguments=arguments,
        )

    def tool_result_created(self, *, tool_name: str, success: bool, content: Any, **_: Any) -> None:
        message = self.store.create_message(
            session_id=self.session_id,
            turn_id=self.turn_id or "",
            role="tool",
            status="completed",
            content=content,
        )
        self.store.append_message_part(message.message_id, type="tool_result", content=content)

    def agent_finished(self, **_: Any) -> None:
        return None

    def on_tool_call_created(self, *, action: Any, **_: Any) -> None:
        with self.store.database.transaction() as connection:
            exists = connection.execute("SELECT 1 FROM tool_calls WHERE turn_id = ? AND tool_name = ? AND arguments_json = ?", (self.turn_id, action.tool_name, json.dumps(action.arguments, ensure_ascii=False, separators=(",", ":")))).fetchone()
        if exists is None:
            self.store.create_tool_call(turn_id=self.turn_id or "", attempt_id=self.attempt_id, tool_name=action.tool_name, arguments=action.arguments)

    def on_permission_pending(self, *, request: Any, **_: Any) -> None:
        self.store.append_event(session_id=self.session_id, event_type="permission_pending", payload={"request_id": request.request_id, "tool_name": request.tool_name}, turn_id=self.turn_id, attempt_id=self.attempt_id)

    def on_permission_resolved(self, *, request: Any, response: Any, **_: Any) -> None:
        self.store.append_event(session_id=self.session_id, event_type="permission_resolved", payload={"request_id": request.request_id, "decision": response.decision if response else None}, turn_id=self.turn_id, attempt_id=self.attempt_id)

    def on_execution_started(self, *, action: Any, **_: Any) -> None:
        with self.store.database.transaction() as connection:
            timestamp = now_iso()
            connection.execute("UPDATE tool_calls SET status = 'execution_started', started_at = ?, updated_at = ? WHERE turn_id = ? AND tool_name = ? AND status = 'created'", (timestamp, timestamp, self.turn_id, action.tool_name))

    def on_execution_finished(self, *, action: Any, result: Any, **_: Any) -> None:
        if result is None:
            return
        with self.store.database.transaction() as connection:
            row = connection.execute("SELECT tool_call_id FROM tool_calls WHERE turn_id = ? AND tool_name = ? ORDER BY created_at DESC LIMIT 1", (self.turn_id, action.tool_name)).fetchone()
        if row is not None:
            with self.store.database.transaction() as connection:
                exists = connection.execute("SELECT 1 FROM tool_results WHERE tool_call_id = ?", (row[0],)).fetchone()
                timestamp = now_iso()
                connection.execute("UPDATE tool_calls SET status = ?, completed_at = ?, updated_at = ? WHERE tool_call_id = ?", ("completed" if result.success else "failed", timestamp, timestamp, row[0]))
            if exists is None:
                self.store.create_tool_result(tool_call_id=row[0], status="success" if result.success else "failed", content=result.output, metadata=result.metadata)

    def _record(self, event_type: str, **data: Any) -> TraceEvent:
        self._step += 1
        event = TraceEvent(run_id=self.run_id, step=self._step, event_type=event_type, **_trace_fields(data))
        return self.record(event)

    def record(self, event: TraceEvent) -> TraceEvent:
        """兼容 traced tool helper，并保持先写 SQLite、后通知 Hook。"""

        self.store.append_event(
            session_id=self.session_id,
            event_type=event.event_type,
            payload=event.model_dump(mode="json"),
            turn_id=self.turn_id,
            attempt_id=self.attempt_id,
        )
        if self.record_hook is not None:
            try:
                self.record_hook(event)
            except Exception as exc:
                self.last_record_hook_error = exc
        return event

    def record_run_start(self, task: str | None = None, metadata: dict | None = None) -> TraceEvent:
        return self._record("run_start", task=task, metadata=metadata or {})

    def record_run_end(self, success: bool, summary: str, metadata: dict | None = None) -> TraceEvent:
        return self._record("run_end", success=success, summary=summary, metadata=metadata or {})

    def record_run_cancelled(self, metadata: dict | None = None) -> TraceEvent:
        return self._record("run_cancelled", success=False, metadata=metadata or {})

    def record_llm_call(self, **kwargs: Any) -> TraceEvent:
        return self._record("llm_call", **kwargs)

    def record_agent_action(self, **kwargs: Any) -> TraceEvent:
        return self._record("agent_action", **kwargs)

    def record_agent_observation(self, **kwargs: Any) -> TraceEvent:
        return self._record("agent_observation", **kwargs)

    def record_agent_finish(self, **kwargs: Any) -> TraceEvent:
        return self._record("agent_finish", **kwargs)

    def record_policy_decision(self, **kwargs: Any) -> TraceEvent:
        return self._record("policy_decision", **kwargs)

    def record_permission_request(self, **kwargs: Any) -> TraceEvent:
        return self._record("permission_request", **kwargs)

    def record_permission_response(self, **kwargs: Any) -> TraceEvent:
        return self._record("permission_response", **kwargs)


def _trace_fields(data: dict[str, Any]) -> dict[str, Any]:
    """只把 TraceEvent 已知字段传给 Pydantic，其他内容放入 metadata。"""

    known = {"success", "error", "tool_name", "input", "output_preview", "policy_decision", "policy_reason", "policy_rule", "policy_mode", "metadata"}
    fields = {key: value for key, value in data.items() if key in known}
    extras = {key: value for key, value in data.items() if key not in known and key != "metadata"}
    fields["metadata"] = {**(data.get("metadata") or {}), **extras}
    return fields
