from __future__ import annotations

from collections.abc import Callable
from typing import Any

from codepilot.session.database import SessionDatabase
from codepilot.session.model_capabilities import ModelContextProfile, resolve_model_context_profile
from codepilot.session.store import SessionStore


class CompactionService:
    """生成摘要而不删除原始 Session 历史。"""

    def __init__(self, database: SessionDatabase, summarizer: Callable[[list[dict[str, Any]]], str] | None = None, threshold: float = 0.8) -> None:
        if not 0 < threshold <= 1:
            raise ValueError("threshold must be between 0 and 1")
        self.store = SessionStore(database)
        self.summarizer = summarizer or _default_summary
        self.threshold = threshold

    def compact(self, session_id: str, profile: ModelContextProfile | None = None) -> Any:
        session = self.store.get_session(session_id)
        profile = profile or resolve_model_context_profile(session.provider, session.current_model)
        messages = self.store.list_messages_with_parts(session_id)
        payload = [{"message_id": message.message_id, "role": message.role, "content": message.content} for message, _ in messages]
        if not payload:
            raise ValueError("cannot compact an empty session")
        if _estimate_tokens(payload) < profile.max_input_tokens * self.threshold:
            raise ValueError("context is below the compaction threshold")
        try:
            summary = self.summarizer(payload)
            _validate_summary(summary)
        except Exception as exc:
            self.store.append_event(session_id=session_id, event_type="context_compaction_failed", payload={"error": str(exc), "message_count": len(payload)})
            raise
        covered_ids = [message.message_id for message, _ in messages[:-1]]
        record = self.store.create_context_summary(
            session_id=session_id,
            content=summary,
            metadata={"covered_message_ids": covered_ids, "provider": profile.provider, "model": profile.model},
        )
        self.store.append_event(session_id=session_id, event_type="context_compacted", payload={"summary_id": record.summary_id, "covered_message_count": len(covered_ids)})
        return record


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
