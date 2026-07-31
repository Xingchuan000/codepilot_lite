from __future__ import annotations

from codepilot.memory.models import MemoryQuery, MemorySearchResult
from codepilot.memory.repository import MemoryRepository
from codepilot.session.database import SessionDatabase


class MemoryRetriever:
    def __init__(self, database: SessionDatabase) -> None:
        self.repository = MemoryRepository(database)

    def search(self, project_id: str, query: MemoryQuery) -> list[MemorySearchResult]:
        return self.repository.search(project_id, query)
