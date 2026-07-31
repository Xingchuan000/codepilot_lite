from __future__ import annotations

from codepilot.memory.policy import is_memory_content_safe
from codepilot.memory.repository import CandidateRepository
from codepilot.session.database import SessionDatabase
from codepilot.session.store import SessionStore


class MemoryCandidateExtractor:
    """Creates reviewable candidates only from successful command evidence."""

    def __init__(self, database: SessionDatabase) -> None:
        self.store = SessionStore(database)
        self.repository = CandidateRepository(database)

    def extract(self, session_id: str, turn_id: str) -> list:
        session = self.store.get_session(session_id)
        candidates = []
        for call in self.store.list_tool_calls(session_id):
            if call.turn_id != turn_id or call.status != "completed" or call.tool_name not in {"run_tests", "run_shell"}:
                continue
            command = call.arguments.get("command")
            if not isinstance(command, str) or not command.strip():
                continue
            result = self.store.get_tool_result_by_call(call.tool_call_id)
            if result is None or result.success is not True:
                continue
            content = {"text": command.strip()}
            if not is_memory_content_safe(content):
                continue
            candidates.append(
                self.repository.create(
                    session.project_id,
                    session_id,
                    turn_id,
                    "command",
                    f"command:{' '.join(command.split())}",
                    content,
                    {"tool_call_id": call.tool_call_id, "tool_result_id": result.tool_result_id},
                    0.9,
                )
            )
        return candidates
