from __future__ import annotations

import json
from pathlib import Path

from codepilot.llm.types import ChatMessage, RichChatMessage
from codepilot.memory.context_provider import MemoryContextProvider
from codepilot.memory.repository import TurnCheckpointRepository
from codepilot.memory.retrieval import MemoryQueryBuilder
from codepilot.memory.turn_window import render_turn_checkpoint
from codepilot.session.artifacts import ArtifactStore
from codepilot.session.context_adapters import PreparedContext, SessionHistory, TextActionContextAdapter
from codepilot.session.context_budget import ContextItem, estimate_tokens
from codepilot.session.context_fork import ForkContextPolicy
from codepilot.session.database import SessionDatabase
from codepilot.session.model_capabilities import resolve_model_context_profile
from codepilot.session.models import ContextSummaryRecord, MessagePartRecord, MessageRecord, SessionRecord, TurnRecord
from codepilot.session.store import SessionStore
from codepilot.tools.base import ToolSpec


class ContextAssembler:
    """从 SQLite 记录恢复模型上下文，不读取 TUI Transcript。"""

    def __init__(self, database: SessionDatabase, store: SessionStore | None = None) -> None:
        self.store = store or SessionStore(database)
        self.artifacts = ArtifactStore(database)
        self.adapter = TextActionContextAdapter(self.store, self.artifacts)
        self.memory = MemoryContextProvider(database)
        self.memory_query = MemoryQueryBuilder(self.store)
        self.checkpoints = TurnCheckpointRepository(database)

    def build(
        self,
        session_id: str,
        current_turn_id: str,
        provider: str,
        model: str,
        profile=None,
        *,
        tool_specs: tuple[ToolSpec, ...] | None = None,
        agent_instructions: str | None = None,
    ) -> list[ChatMessage | RichChatMessage]:
        return self.build_with_manifest(
            session_id,
            current_turn_id,
            provider,
            model,
            profile=profile,
            tool_specs=tool_specs,
            agent_instructions=agent_instructions,
        ).messages

    def build_with_manifest(
        self,
        session_id: str,
        current_turn_id: str,
        provider: str,
        model: str,
        profile=None,
        *,
        tool_specs: tuple[ToolSpec, ...] | None = None,
        agent_instructions: str | None = None,
    ) -> PreparedContext:
        profile = profile or resolve_model_context_profile(provider, model)
        return self.adapter.build_prepared_context(
            self.build_history(session_id, current_turn_id, profile),
            profile,
            tool_specs=tool_specs,
            agent_instructions=agent_instructions,
        )

    def build_plan(
        self,
        session_id: str,
        current_turn_id: str,
        provider: str,
        model: str,
        profile=None,
        *,
        tool_specs: tuple[ToolSpec, ...] | None = None,
        agent_instructions: str | None = None,
    ):
        """暴露预算规划，供 Compact 估算有效上下文而不重复统计已覆盖原文。"""

        profile = profile or resolve_model_context_profile(provider, model)
        return self.adapter.build_context_plan(
            self.build_history(session_id, current_turn_id, profile),
            profile,
            tool_specs=tool_specs,
            agent_instructions=agent_instructions,
        )

    def build_history(self, session_id: str, current_turn_id: str, profile=None) -> SessionHistory:
        session = self.store.get_session(session_id)
        turn = self.store.get_turn(current_turn_id)
        profile = profile or resolve_model_context_profile(turn.provider_snapshot, turn.model_snapshot)
        with self.store.database.transaction() as connection:
            project_path = Path(connection.execute("SELECT path FROM projects WHERE project_id = ?", (session.project_id,)).fetchone()[0])
        workspace_value = session.metadata.get("workspace_path")
        if isinstance(workspace_value, str) and workspace_value.strip():
            project_path = Path(workspace_value).expanduser().resolve()
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
            inherited_items=self._build_inherited_items(session, turn, profile),
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
                        source_kind="turn_checkpoint",
                        source_ids=(checkpoint.checkpoint_id,),
                    ),
                )
                if checkpoint is not None and checkpoint_message is not None
                else ()
            ),
            turn_checkpoint_covered_ids=checkpoint.covered_message_ids if checkpoint is not None else (),
        )

    def _build_inherited_items(
        self,
        session: SessionRecord,
        current_turn: TurnRecord,
        profile,
    ) -> tuple[ContextItem, ...]:
        if session.parent_session_id is None or session.forked_from_turn_id is None:
            return ()

        policy = ForkContextPolicy.from_session_metadata(session.metadata)
        if policy.mode == "none":
            return ()
        fork_turn = self.store.get_turn(session.forked_from_turn_id)
        if fork_turn.session_id != session.parent_session_id:
            raise ValueError("fork turn does not belong to parent session")
        parent_turns = [
            turn
            for turn in self.store.list_turns(session.parent_session_id)
            if turn.sequence <= fork_turn.sequence
        ]
        blocks: list[str] = []
        if policy.mode in {"summary", "summary_recent"}:
            summary = self._latest_parent_summary_before(
                session.parent_session_id,
                fork_turn.sequence,
            )
            if summary is not None:
                content = summary.content if isinstance(summary.content, str) else json.dumps(summary.content, ensure_ascii=False)
                blocks.append(f"Parent summary:\n{content}")

        if policy.mode in {"recent", "summary_recent", "full"}:
            selected_turns = parent_turns if policy.mode == "full" else parent_turns[-policy.recent_turns :]
            selected_ids = {turn.turn_id for turn in selected_turns}
            turn_by_id = {turn.turn_id: turn for turn in parent_turns}
            for message, parts in self.store.list_messages_with_parts(session.parent_session_id):
                if message.turn_id not in selected_ids or turn_by_id.get(message.turn_id) is None:
                    continue
                rendered = self.adapter.render_message_for_context(message, tuple(parts), profile)
                if rendered is not None:
                    blocks.append(f"{rendered.role}: {rendered.content}")

        if not blocks:
            return ()
        message = ChatMessage(
            "system",
            "Parent session background. This is context only; it is NOT your current task.\n\n"
            + "\n\n".join(blocks),
        )
        return (
            ContextItem(
                key=f"parent-context-{session.session_id}",
                messages=(message,),
                estimated_tokens=estimate_tokens(message),
                mandatory=False,
                priority=900,
                source_kind="parent_session_context",
                source_ids=(session.parent_session_id, session.forked_from_turn_id),
            ),
        )

    def _latest_parent_summary_before(self, parent_session_id: str, fork_sequence: int) -> ContextSummaryRecord | None:
        summaries = self.store.list_context_summaries(parent_session_id)
        for summary in reversed(summaries):
            if summary.status != "completed":
                continue
            if summary.source_end_sequence is not None and summary.source_end_sequence > fork_sequence:
                continue
            if summary.source_end_sequence is None and summary.turn_id is not None:
                if self.store.get_turn(summary.turn_id).sequence > fork_sequence:
                    continue
            return summary
        return None


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
