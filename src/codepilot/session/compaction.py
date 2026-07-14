from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from codepilot.session.database import SessionDatabase
from codepilot.session.model_capabilities import ModelContextProfile, resolve_model_context_profile
from codepilot.session.models import ContextSummaryRecord
from codepilot.session.store import SessionStore


@dataclass(frozen=True)
class CompactionResult:
    summary: ContextSummaryRecord
    covered_message_ids: tuple[str, ...]
    retained_message_ids: tuple[str, ...]


@dataclass(frozen=True)
class CompactionSelection:
    covered_message_ids: tuple[str, ...]
    retained_message_ids: tuple[str, ...]
    retained_tool_call_ids: tuple[str, ...]
    source_start_sequence: int
    source_end_sequence: int


class MustRetainPolicy:
    """集中定义 Compact 不能覆盖的业务事实。"""

    def __init__(self, store: SessionStore, recent_turn_count: int = 4) -> None:
        self.store = store
        self.recent_turn_count = recent_turn_count

    def select(self, session_id: str, current_turn_id: str | None) -> tuple[set[str], set[str]]:
        turns = self.store.list_turns(session_id)
        recent_ids = {turn.turn_id for turn in turns[-self.recent_turn_count:]}
        if current_turn_id is not None:
            recent_ids.add(current_turn_id)
        messages = self.store.list_messages_with_parts(session_id)
        retained_messages = {message.message_id for message, _ in messages if message.turn_id in recent_ids}
        retained_calls = {call.tool_call_id for call in self.store.list_tool_calls(session_id) if call.status in {"approval_pending", "execution_started", "execution_uncertain", "recovery_required"}}
        retained_messages.update(
            message.message_id
            for message, parts in messages
            if any(part.metadata.get("key_decision") is True for part in parts)
        )
        return retained_messages, retained_calls


class CompactionService:
    """把旧历史压缩成摘要，但不删除原始事实。"""

    def __init__(self, database: SessionDatabase, summarizer: Callable[[list[dict[str, Any]]], str] | None = None, threshold: float = 0.8) -> None:
        if not 0 < threshold <= 1:
            raise ValueError("threshold must be between 0 and 1")
        self.store = SessionStore(database)
        self.summarizer = summarizer or _default_summary
        self.threshold = threshold

    def compact(
        self,
        session_id: str,
        *,
        force: bool = False,
        current_turn_id: str | None = None,
        profile: ModelContextProfile | None = None,
    ) -> CompactionResult:
        session = self.store.get_session(session_id)
        profile = profile or resolve_model_context_profile(session.provider, session.current_model)
        messages = self.store.list_messages_with_parts(session_id)
        payload = [
            {
                "message_id": message.message_id,
                "turn_id": message.turn_id,
                "role": message.role,
                "status": message.status,
                "content": message.content,
                "parts": [part.content for part in parts if part.replayable],
            }
            for message, parts in messages
            if message.metadata.get("summary_id") is None
        ]
        if not payload:
            raise ValueError("cannot compact an empty session")
        if not force and _estimate_tokens(payload) < profile.max_input_tokens * self.threshold:
            raise ValueError("context is below the compaction threshold")

        retained_message_ids, retained_tool_call_ids = MustRetainPolicy(self.store).select(session_id, current_turn_id)
        latest_summary = self.store.get_latest_context_summary(session_id)
        covered_before = set(latest_summary.metadata.get("covered_message_ids", [])) if latest_summary is not None and latest_summary.status == "completed" else set()
        covered_message_ids = [message.message_id for message, _ in messages if message.message_id not in retained_message_ids and message.message_id not in covered_before]
        summary_payload = [item for item in payload if item["message_id"] in covered_message_ids]
        if not summary_payload:
            raise ValueError("no new history is available for compaction")
        try:
            summary_text = self.summarizer(summary_payload)
            _validate_summary(summary_text)
        except Exception as exc:
            self.store.append_event(
                session_id=session_id,
                event_type="context_compaction_failed",
                payload={"error": str(exc), "message_count": len(summary_payload)},
                turn_id=current_turn_id,
                metadata={"source": "compaction_service"},
            )
            raise
        source_sequences = [self.store.get_turn(message.turn_id).sequence for message, _ in messages if message.message_id in covered_message_ids]
        selection = CompactionSelection(tuple(covered_message_ids), tuple(sorted(retained_message_ids)), tuple(sorted(retained_tool_call_ids)), min(source_sequences), max(source_sequences))
        summary_record = self.store.create_context_summary_with_message(
            session_id=session_id,
            turn_id=current_turn_id,
            content=summary_text,
            source_start_sequence=selection.source_start_sequence,
            source_end_sequence=selection.source_end_sequence,
            model=profile.model,
            metadata={
                "covered_message_ids": list(selection.covered_message_ids),
                "retained_message_ids": list(selection.retained_message_ids),
                "retained_tool_call_ids": list(selection.retained_tool_call_ids),
                "provider": profile.provider,
                "model": profile.model,
            },
        )
        self.store.append_event(
            session_id=session_id,
            event_type="context_compacted",
            payload={
                "summary_id": summary_record.summary_id,
                "covered_message_count": len(covered_message_ids),
                "retained_message_count": len(retained_message_ids),
                "retained_tool_call_count": len(retained_tool_call_ids),
                "force": force,
            },
            turn_id=current_turn_id,
            metadata={"source": "compaction_service"},
        )
        return CompactionResult(summary_record, selection.covered_message_ids, selection.retained_message_ids)

    def ensure_context_budget(self, session_id: str, current_turn_id: str, profile: ModelContextProfile) -> None:
        """运行前先尝试压缩上下文；成功后上下文再重建一次。"""

        if _estimate_tokens(self.store.list_messages_with_parts(session_id)) >= profile.max_input_tokens * self.threshold:
            try:
                self.compact(session_id, force=False, current_turn_id=current_turn_id, profile=profile)
            except Exception as exc:
                self.store.append_event(
                    session_id=session_id,
                    event_type="context_compaction_failed",
                    payload={"turn_id": current_turn_id, "error": str(exc)},
                    turn_id=current_turn_id,
                    metadata={"source": "compaction_service"},
                )
                raise


def _estimate_tokens(value: Any) -> int:
    return max(1, len(str(value)) // 4)


def _default_summary(messages: list[dict[str, Any]]) -> str:
    lines = ["Session summary:"]
    for message in messages:
        lines.append(f"- {message['role']}: {str(message['content'])[:800]}")
    lines.extend(["Key decisions: preserved in the summarized messages.", "Files/tests/diff: see the listed tool results.", "Unfinished work: continue from the latest message."])
    return "\n".join(lines)


def _validate_summary(summary: str) -> None:
    if not summary.strip():
        raise ValueError("compaction summary is empty")
    required = ("Key decisions", "Files/tests/diff", "Unfinished work")
    if any(item not in summary for item in required):
        raise ValueError("compaction summary does not cover required fields")
