from __future__ import annotations

from codepilot.memory.models import MemoryKind, MemoryQuery, MemorySearchResult, ProjectMemoryRecord
from codepilot.memory.policy import validate_memory_content
from codepilot.memory.repository import CandidateRepository, MemoryRepository
from codepilot.session.database import SessionDatabase

MEMORY_KINDS = {"architecture", "convention", "command", "decision", "file_map", "known_issue", "project_preference"}


class MemoryService:
    def __init__(self, database: SessionDatabase) -> None:
        self.memories = MemoryRepository(database)
        self.candidates = CandidateRepository(database)

    def add(
        self,
        project_id: str,
        kind: MemoryKind,
        canonical_key: str,
        text: str,
        *,
        title: str | None = None,
        source_session_id: str | None = None,
        source_turn_id: str | None = None,
    ) -> ProjectMemoryRecord:
        if kind not in MEMORY_KINDS:
            raise ValueError(f"unknown memory kind: {kind}")
        content = {"text": text}
        validate_memory_content(content)
        return self.memories.add(
            project_id,
            kind,
            canonical_key,
            title or canonical_key,
            content,
            source_session_id=source_session_id,
            source_turn_id=source_turn_id,
            metadata={"source": "manual"},
        )

    def list(self, project_id: str) -> list[ProjectMemoryRecord]:
        return self.memories.list(project_id)

    def forget(self, memory_id: str) -> ProjectMemoryRecord:
        return self.memories.forget(memory_id)

    def search(self, project_id: str, text: str, limit: int = 8) -> list[MemorySearchResult]:
        return self.memories.search(project_id, MemoryQuery(text, limit=limit))

    def approve(self, candidate_id: str) -> ProjectMemoryRecord:
        return self.memories.approve_candidate(candidate_id)

    def reject(self, candidate_id: str) -> None:
        self.candidates.decide(candidate_id, "rejected")
