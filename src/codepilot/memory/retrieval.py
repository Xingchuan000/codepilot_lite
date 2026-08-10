from __future__ import annotations

from codepilot.memory.models import MemoryQuery, MemorySearchResult, NormalizedMemoryQuery
from codepilot.memory.query import normalize_memory_query
from codepilot.memory.repository import MemoryRepository
from codepilot.session.database import SessionDatabase
from codepilot.session.models import ContextSummaryRecord
from codepilot.session.repositories import SessionRepositories

class MemoryQueryBuilder:
    def __init__(self, store: SessionRepositories) -> None:
        self.store = store

    def build(
        self,
        *,
        session_id: str,
        current_turn_id: str,
        current_user_text: str,
        latest_summary: ContextSummaryRecord | None,
        branch: str | None,
    ) -> MemoryQuery:
        summary = latest_summary.content if latest_summary is not None and isinstance(latest_summary.content, dict) else {}
        text = "\n".join(
            item
            for item in (
                current_user_text,
                str(summary.get("task_goal", "")),
                *[str(value) for value in summary.get("unresolved_work", [])],
            )
            if item
        )
        recent_turn_ids = {turn.turn_id for turn in self.store.turns.list_turns(session_id)[-3:]}
        recent_turn_ids.add(current_turn_id)
        paths = [
            value
            for call in self.store.tool_executions.list_tool_calls(session_id)
            if call.turn_id in recent_turn_ids
            for value in _argument_paths(call.arguments)
        ]
        return MemoryQuery(text=text, paths=tuple(dict.fromkeys(paths)), branch=branch)


def _argument_paths(arguments: dict) -> tuple[str, ...]:
    values = []
    for key in ("path", "file", "file_path", "target", "repo"):
        value = arguments.get(key)
        if isinstance(value, str) and value:
            values.append(value)
    return tuple(values)


class MemoryRetriever:
    def __init__(self, database: SessionDatabase) -> None:
        self.repository = MemoryRepository(database)

    def search(self, project_id: str, query: MemoryQuery) -> list[MemorySearchResult]:
        return self.repository.search(project_id, query)
