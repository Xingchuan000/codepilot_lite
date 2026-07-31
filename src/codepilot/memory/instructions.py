from __future__ import annotations

from hashlib import sha256
from pathlib import Path

from codepilot.memory.models import ProjectInstructionRecord
from codepilot.memory.repository import InstructionRepository
from codepilot.session.database import SessionDatabase


class ProjectInstructionLoader:
    FILES = (("AGENTS.md", "instruction"), ("CLAUDE.md", "instruction"), ("README.md", "reference"))

    def __init__(self, database: SessionDatabase, max_bytes: int = 32_000) -> None:
        self.repository = InstructionRepository(database)
        self.max_bytes = max_bytes

    def load(self, project_id: str, root: Path) -> list[ProjectInstructionRecord]:
        records: list[ProjectInstructionRecord] = []
        for relative_path, kind in self.FILES:
            path = root / relative_path
            if not path.is_file():
                continue
            content = path.read_bytes()[: self.max_bytes]
            digest = sha256(content).hexdigest()
            cached = self.repository.latest(project_id, relative_path)
            if cached is not None and cached.sha256 == digest:
                records.append(cached)
                continue
            records.append(
                self.repository.save(
                    project_id,
                    relative_path,
                    kind,
                    digest,
                    {"text": content.decode("utf-8", errors="replace"), "truncated": path.stat().st_size > self.max_bytes},
                )
            )
        return records
