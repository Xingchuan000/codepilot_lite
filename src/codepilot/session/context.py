from __future__ import annotations

import json
from pathlib import Path

from codepilot.llm.types import ChatMessage, ChatMessagePart, RichChatMessage
from codepilot.session.artifacts import ArtifactStore
from codepilot.session.model_capabilities import resolve_model_context_profile
from codepilot.session.models import MessagePartRecord, MessageRecord
from codepilot.session.store import SessionStore
from codepilot.session.database import SessionDatabase
from codepilot.agent.prompts import build_system_event_text, build_system_prompt


class ContextAssembler:
    """从 SQLite 记录恢复模型上下文，不读取 TUI Transcript。"""

    def __init__(self, database: SessionDatabase, store: SessionStore | None = None) -> None:
        self.store = store or SessionStore(database)
        self.artifacts = ArtifactStore(database)

    def build(self, session_id: str, current_turn_id: str, provider: str, model: str) -> list[ChatMessage | RichChatMessage]:
        session = self.store.get_session(session_id)
        turn = self.store.get_turn(current_turn_id)
        profile = resolve_model_context_profile(provider, model)
        budget_chars = profile.max_input_tokens * 4
        with self.store.database.transaction() as connection:
            project_path = Path(connection.execute("SELECT path FROM projects WHERE project_id = ?", (session.project_id,)).fetchone()[0])
        messages: list[ChatMessage | RichChatMessage] = [ChatMessage("system", build_system_prompt())]
        covered_ids: set[str] = set()
        for summary in self.store.list_context_summaries(session_id):
            summary_metadata = summary.metadata
            covered_ids.update(str(item) for item in summary_metadata.get("covered_message_ids", []))
            messages.append(ChatMessage("system", f"Persisted context summary:\n{summary.content}"))
        turns = self.store.list_turns(session_id)
        turn_by_id = {item.turn_id: item for item in turns}
        timeline: list[tuple[str, int, int, str, MessageRecord | None, list[MessagePartRecord] | None, dict[str, object] | None]] = []
        # 只回放当前 Turn 及更早的消息，避免把未来 Turn 的事实提前喂给模型。
        for message, parts in self.store.list_messages_with_parts(session_id):
            if message.message_id in covered_ids:
                continue
            message_turn = turn_by_id.get(message.turn_id)
            if message_turn is None or message_turn.sequence > turn.sequence:
                continue
            timeline.append((message.created_at, message_turn.sequence, len(timeline), "message", message, parts, None))
        for event in self.store.list_events(session_id):
            event_turn = turn_by_id.get(event.turn_id) if event.turn_id is not None else None
            if event_turn is not None and event_turn.sequence > turn.sequence:
                continue
            if event.event_type != "branch_changed":
                continue
            timeline.append((event.created_at, event_turn.sequence if event_turn is not None else 0, len(timeline), "event", None, None, event.payload))
        for _, _, _, kind, message, parts, payload in sorted(timeline, key=lambda item: (item[0], item[1], item[2])):
            if kind == "event" and payload is not None:
                messages.append(ChatMessage("system", build_system_event_text("branch_changed", payload)))
                continue
            assert message is not None
            assert parts is not None
            content = _message_content(self.artifacts, message, parts, budget_chars)
            if message.status == "interrupted":
                # 中断消息必须强提醒模型重新完整生成，不能沿着上一次尾巴继续写。
                content += "\nThe previous assistant response was interrupted. Use the persisted content only as evidence and produce a complete response again. Do not continue from the last character."
            if message.turn_id == current_turn_id and message.role == "user":
                content += f"\nRepository: {project_path}"
            if profile.supports_reasoning_replay and parts:
                messages.append(_rich_message(message.role, parts, content))
            else:
                messages.append(ChatMessage(message.role, content))
        return messages


def _message_content(artifacts: ArtifactStore, message: MessageRecord, parts: list[MessagePartRecord], budget_chars: int) -> str:
    if message.status in {"failed", "in_progress"}:
        return ""
    if parts:
        values: list[str] = []
        remaining = budget_chars
        for part in parts:
            if not part.replayable:
                continue
            text = _part_content(artifacts, part, remaining)
            remaining = max(0, remaining - len(text))
            values.append(text)
        return "\n".join(values) if values else _message_text(message.content)
    return _message_text(message.content)


def _part_content(artifacts: ArtifactStore, part: MessagePartRecord, budget_chars: int) -> str:
    if part.artifact_id is not None and budget_chars > 0:
        text = artifacts.read_text(part.artifact_id)
        if len(text) <= budget_chars:
            return text
    return _message_text(part.content)


def _message_text(content: object) -> str:
    return content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)


def _rich_message(role: str, parts: list[MessagePartRecord], fallback: str) -> RichChatMessage:
    rich_parts = [
        ChatMessagePart(
            type=part.type,
            content=part.content if isinstance(part.content, str) else json.dumps(part.content, ensure_ascii=False),
            provider_format=part.provider_format,
            replayable=part.replayable,
        )
        for part in parts
        if part.replayable
    ]
    if fallback:
        rich_parts.append(ChatMessagePart(type="text", content=fallback))
    return RichChatMessage(role=role, parts=tuple(rich_parts))
