from __future__ import annotations

from pathlib import Path

from codepilot.llm.types import ChatMessage, RichChatMessage
from codepilot.memory.context_provider import MemoryContextProvider
from codepilot.memory.repository import TurnCheckpointRepository
from codepilot.memory.retrieval import MemoryQueryBuilder
from codepilot.memory.turn_window import render_turn_checkpoint
from codepilot.session.artifacts import ArtifactStore
from codepilot.session.context_adapters import SessionHistory, TextActionContextAdapter
from codepilot.session.context_budget import ContextItem, estimate_tokens
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
        self.memory = MemoryContextProvider(database)
        self.memory_query = MemoryQueryBuilder(self.store)
        self.checkpoints = TurnCheckpointRepository(database)

    def build(self, session_id: str, current_turn_id: str, provider: str, model: str, profile=None) -> list[ChatMessage | RichChatMessage]:
        profile = profile or resolve_model_context_profile(provider, model)
        return self.adapter.build_messages(self.build_history(session_id, current_turn_id, profile), profile)

    def build_plan(self, session_id: str, current_turn_id: str, provider: str, model: str, profile=None):
        """暴露预算规划，供 Compact 估算有效上下文而不重复统计已覆盖原文。"""

        profile = profile or resolve_model_context_profile(provider, model)
        return self.adapter.build_context_plan(self.build_history(session_id, current_turn_id, profile), profile)

    def build_history(self, session_id: str, current_turn_id: str, profile=None) -> SessionHistory:
        session = self.store.get_session(session_id)
        turn = self.store.get_turn(current_turn_id)
        profile = profile or resolve_model_context_profile(turn.provider_snapshot, turn.model_snapshot)
        with self.store.database.transaction() as connection:
            project_path = Path(connection.execute("SELECT path FROM projects WHERE project_id = ?", (session.project_id,)).fetchone()[0])
        latest_summary = self.store.get_latest_context_summary(session_id)
        summaries = (latest_summary,) if latest_summary is not None else ()
        checkpoint = self.checkpoints.latest(current_turn_id)
        checkpoint_message = (
            ChatMessage("system", render_turn_checkpoint(checkpoint.content))
            if checkpoint is not None
            else None
        )
        return SessionHistory(
            session_id=session_id,
            current_turn_id=current_turn_id,
            project_path=project_path,
            summaries=summaries,
            messages=_messages_for_turn(self.store, session_id, turn.sequence),
            branch_events=tuple(_branch_messages(self.store, session_id, turn.sequence)),
            instruction_items=self.memory.instruction_items(session.project_id, project_path, profile),
            memory_items=self.memory.memory_items(
                session.project_id,
                self.memory_query.build(
                    session_id=session_id,
                    current_turn_id=current_turn_id,
                    current_user_text=(
                        str(self.store.get_user_message_for_turn(current_turn_id).content)
                        if self.store.get_user_message_for_turn(current_turn_id) is not None
                        else ""
                    ),
                    latest_summary=latest_summary,
                    branch=turn.branch_snapshot,
                ),
                profile,
            ),
            turn_checkpoint_items=(
                (
                    ContextItem(
                        key=f"turn-checkpoint-{checkpoint.checkpoint_id}",
                        messages=(checkpoint_message,),
                        estimated_tokens=estimate_tokens(checkpoint_message),
                        mandatory=True,
                        priority=880,
                    ),
                )
                if checkpoint is not None and checkpoint_message is not None
                else ()
            ),
            turn_checkpoint_covered_ids=checkpoint.covered_message_ids if checkpoint is not None else (),
        )


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


def _branch_messages(store: SessionStore, session_id: str, current_turn_sequence: int) -> list[ChatMessage]:
    """把可见分支事件在预算规划前转换成必需的 system ContextItem。"""

    from codepilot.agent.prompts import build_system_event_text

    messages: list[ChatMessage] = []
    for event in store.list_events(session_id):
        if event.event_type != "branch_changed":
            continue
        event_turn = _turn_sequence(store, event.turn_id) if event.turn_id is not None else None
        effective_sequence = event.payload.get("effective_turn_sequence")
        if event_turn is not None and event_turn > current_turn_sequence:
            continue
        if event_turn is None and isinstance(effective_sequence, int) and effective_sequence > current_turn_sequence:
            continue
        messages.append(ChatMessage("system", build_system_event_text("branch_changed", event.payload)))
    return messages
