from __future__ import annotations

import json
from pathlib import Path

from codepilot.llm.types import ChatMessage
from codepilot.session.models import MessagePartRecord
from codepilot.session.store import SessionStore
from codepilot.session.database import SessionDatabase
from codepilot.agent.prompts import build_system_event_text, build_system_prompt


class ContextAssembler:
    """从 SQLite 记录恢复模型上下文，不读取 TUI Transcript。"""

    def __init__(self, database: SessionDatabase, store: SessionStore | None = None) -> None:
        self.store = store or SessionStore(database)

    def build(self, session_id: str, current_turn_id: str, provider: str, model: str) -> list[ChatMessage]:
        session = self.store.get_session(session_id)
        with self.store.database.transaction() as connection:
            project_path = Path(connection.execute("SELECT path FROM projects WHERE project_id = ?", (session.project_id,)).fetchone()[0])
        messages: list[ChatMessage] = [ChatMessage("system", build_system_prompt())]
        covered_ids: set[str] = set()
        for summary in self.store.list_context_summaries(session_id):
            summary_metadata = summary.metadata
            covered_ids.update(str(item) for item in summary_metadata.get("covered_message_ids", []))
            messages.append(ChatMessage("system", f"Persisted context summary:\n{summary.content}"))
        for event in self.store.list_events(session_id):
            if event.event_type == "branch_changed":
                messages.append(ChatMessage("system", build_system_event_text(event.event_type, event.payload)))
        for message, parts in self.store.list_messages_with_parts(session_id):
            if message.message_id in covered_ids:
                continue
            if message.status == "failed":
                continue
            content = _message_content(message.content, parts)
            if message.status == "interrupted":
                content += "\n[恢复指令] 上一条 assistant 消息被中断，请从已持久化事实继续。"
            if message.turn_id == current_turn_id and message.role == "user":
                content += f"\nRepository: {project_path}"
            messages.append(ChatMessage(message.role, content))
        return messages


def _message_content(content: object, parts: list[MessagePartRecord]) -> str:
    if parts:
        values = [part.content if isinstance(part.content, str) else json.dumps(part.content, ensure_ascii=False) for part in parts if part.replayable]
        return "\n".join(values)
    return content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
