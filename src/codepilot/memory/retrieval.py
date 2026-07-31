from __future__ import annotations

import re

from codepilot.memory.models import MemoryQuery, MemorySearchResult, NormalizedMemoryQuery
from codepilot.memory.repository import MemoryRepository
from codepilot.session.database import SessionDatabase
from codepilot.session.models import ContextSummaryRecord
from codepilot.session.store import SessionStore

_PATH = re.compile(r"(?:^|[\s\"'`(])((?:src|tests?)/[^\s\"'`,;)]+|[\w./-]+\.[A-Za-z0-9]{1,8})")
_CJK = re.compile(r"[\u3400-\u9fff]+")
_WORD = re.compile(r"[A-Za-z0-9_./-]{2,}")
_STOP_WORDS = {"a", "an", "the", "to", "of", "in", "on", "is", "and", "or"}


def normalize_memory_query(query: MemoryQuery) -> NormalizedMemoryQuery:
    paths = tuple(dict.fromkeys((*query.paths, *(match.group(1) for match in _PATH.finditer(query.text)))))
    words = tuple(dict.fromkeys(word.lower() for word in _WORD.findall(query.text) if word.lower() not in _STOP_WORDS))[:24]
    fragments: list[str] = []
    for chunk in _CJK.findall(query.text):
        fragments.extend([chunk, *(chunk[index : index + 2] for index in range(len(chunk) - 1))])
    return NormalizedMemoryQuery(query.text, words, tuple(dict.fromkeys(fragments))[:24], paths[:16])


class MemoryQueryBuilder:
    def __init__(self, store: SessionStore) -> None:
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
        recent_turn_ids = {turn.turn_id for turn in self.store.list_turns(session_id)[-3:]}
        recent_turn_ids.add(current_turn_id)
        paths = [
            value
            for call in self.store.list_tool_calls(session_id)
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
