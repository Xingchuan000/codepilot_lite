from __future__ import annotations

import json
from codepilot.session.store import SessionStore
from codepilot.tui_agent.models import AgentRunView, TranscriptItem


def hydrate_session_view(store: SessionStore, session_id: str) -> AgentRunView:
    """从 SQLite Message/Event 构建全新的 View，切换 Session 时不会复用旧 ID。"""

    items: list[TranscriptItem] = []
    for message, parts in store.list_messages_with_parts(session_id):
        content = "\n".join(
            part.content if isinstance(part.content, str) else json.dumps(part.content, ensure_ascii=False)
            for part in parts
            if part.replayable
        )
        if not content:
            content = message.content if isinstance(message.content, str) else json.dumps(message.content, ensure_ascii=False)
        kind = {"user": "user_message", "assistant": "assistant_raw", "tool": "tool_result"}.get(message.role, "system_status")
        metadata = {"artifact_ids": [part.artifact_id for part in parts if part.artifact_id]}
        items.append(
            TranscriptItem(
                id=message.message_id,
                kind=kind,
                timestamp=message.created_at,
                title=message.role,
                body=content,
                status=message.status,
                copy_text=f"{message.role}: {content}",
                metadata=metadata,
            )
        )
    for event in store.list_events(session_id):
        items.append(TranscriptItem(id=event.event_id, kind="system_status", timestamp=event.created_at, title=event.event_type, body=json.dumps(event.payload, ensure_ascii=False), metadata=event.metadata))
    items.sort(key=lambda item: (item.timestamp, item.id))
    return AgentRunView(transcript=tuple(items), status="idle")
