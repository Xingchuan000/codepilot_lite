from __future__ import annotations

from pathlib import Path

from codepilot.agent.prompts import build_system_event_text
from codepilot.llm.types import ChatMessage, RichChatMessage
from codepilot.session.artifacts import ArtifactStore
from codepilot.session.context_adapters import SessionHistory, TextActionContextAdapter
from codepilot.session.database import SessionDatabase
from codepilot.session.model_capabilities import resolve_model_context_profile
from codepilot.session.models import MessagePartRecord, MessageRecord
from codepilot.session.store import SessionStore


class ContextAssembler:
    """从 SQLite 记录恢复模型上下文，不读取 TUI Transcript。"""

    def __init__(self, database: SessionDatabase, store: SessionStore | None = None) -> None:
        self.store = store or SessionStore(database)
        self.artifacts = ArtifactStore(database)
        self.adapter = TextActionContextAdapter(self.store, self.artifacts)

    def build(self, session_id: str, current_turn_id: str, provider: str, model: str) -> list[ChatMessage | RichChatMessage]:
        session = self.store.get_session(session_id)
        turn = self.store.get_turn(current_turn_id)
        profile = resolve_model_context_profile(provider, model)
        with self.store.database.transaction() as connection:
            project_path = Path(connection.execute("SELECT path FROM projects WHERE project_id = ?", (session.project_id,)).fetchone()[0])
        latest_summary = self.store.get_latest_context_summary(session_id)
        summaries = (latest_summary,) if latest_summary is not None else ()
        history = SessionHistory(
            session_id=session_id,
            current_turn_id=current_turn_id,
            project_path=project_path,
            summaries=summaries,
            messages=_messages_for_turn(self.store, session_id, turn.sequence),
        )
        messages = self.adapter.build_messages(history, profile)
        branch_messages = []
        for event in self.store.list_events(session_id):
            event_turn = _turn_sequence(self.store, event.turn_id) if event.turn_id is not None else None
            effective_sequence = event.payload.get("effective_turn_sequence")
            if event.event_type != "branch_changed":
                continue
            if event_turn is not None and event_turn > turn.sequence:
                continue
            if event_turn is None and isinstance(effective_sequence, int) and effective_sequence > turn.sequence:
                continue
            branch_messages.append(ChatMessage("system", build_system_event_text("branch_changed", event.payload)))
        # 一次性插入保持 Event sequence 的升序，避免反复 insert(1) 导致多次分支变化逆序。
        messages[1:1] = branch_messages
        return messages


def _messages_for_turn(store: SessionStore, session_id: str, current_turn_sequence: int) -> tuple[tuple[MessageRecord, tuple[MessagePartRecord, ...]], ...]:
    turn_by_id = {item.turn_id: item for item in store.list_turns(session_id)}
    messages = []
    for message, parts in store.list_messages_with_parts(session_id):
        message_turn = turn_by_id.get(message.turn_id)
        if message_turn is None or message_turn.sequence > current_turn_sequence:
            continue
        messages.append((message, tuple(parts)))
    return tuple(messages)


def _turn_sequence(store: SessionStore, turn_id: str) -> int | None:
    try:
        return store.get_turn(turn_id).sequence
    except LookupError:
        return None
