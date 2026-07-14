from __future__ import annotations

from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from codepilot.session.artifacts import ArtifactStore
from codepilot.session.database import SessionDatabase
from codepilot.session.store import SessionStore
from codepilot.trace.events import TraceEvent


class SessionTraceRecorder:
    """把 Loop/Router 事件先写入 SQLite，再通知可选的 UI hook。"""

    def __init__(self, database: SessionDatabase, session_id: str, turn_id: str | None = None, attempt_id: str | None = None, record_hook: Callable[[TraceEvent], None] | None = None) -> None:
        self.store = SessionStore(database)
        self.artifacts = ArtifactStore(database)
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
        message = self.store.create_message(session_id=self.session_id, turn_id=self.turn_id or "", attempt_id=self.attempt_id, role="assistant", status="in_progress", content="")
        self._last_message_id = message.message_id

    def assistant_message_completed(self, *, content: str, **_: Any) -> None:
        if self._last_message_id is None:
            self.assistant_message_started()
        persisted = self.artifacts.persist_content(self.session_id, "assistant_message", content)
        if not self._streaming_message:
            self.store.append_message_part(
                self._last_message_id,
                type="text",
                content=persisted.inline_content if persisted.inline_content is not None else persisted.preview,
                artifact_id=persisted.artifact_id,
            )
        self.store.update_message_status(self._last_message_id, "completed")
        self._streaming_message = False

    def assistant_text_delta(self, **_: Any) -> None:
        if self._last_message_id is None:
            self.assistant_message_started()
        content = str(_.get("content", ""))
        persisted = self.artifacts.persist_content(self.session_id, "assistant_message_delta", content)
        self.store.append_message_part(
            self._last_message_id,
            type=str(_.get("type", "text")),
            content=persisted.inline_content if persisted.inline_content is not None else persisted.preview,
            provider_format=_.get("provider_format"),
            replayable=bool(_.get("replayable", True)),
            artifact_id=persisted.artifact_id,
        )

    def assistant_message_interrupted(self, **_: Any) -> None:
        if self._last_message_id is not None:
            self.store.update_message_status(self._last_message_id, "interrupted")

    def tool_call_created(self, *, tool_name: str, arguments: dict[str, Any], **_: Any) -> None:
        # ToolCall 业务表只允许 Router Lifecycle 写入；此入口保留给 Loop/UI 事件协议。
        return None

    def tool_result_created(self, *, tool_name: str, success: bool, content: Any, **_: Any) -> None:
        persisted = self.artifacts.persist_content(self.session_id, "tool_result", content)
        message = self.store.create_message(
            session_id=self.session_id,
            turn_id=self.turn_id or "",
            attempt_id=self.attempt_id,
            role="tool",
            status="completed",
            content=persisted.inline_content if persisted.inline_content is not None else persisted.preview,
        )
        self.store.append_message_part(
            message.message_id,
            type="tool_result",
            content=persisted.inline_content if persisted.inline_content is not None else persisted.preview,
            artifact_id=persisted.artifact_id,
            metadata={"tool_call_id": _.get("tool_call_id")},
        )

    def agent_finished(self, **_: Any) -> None:
        return None

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
