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
            content = _read_preview(path, self.max_bytes)
            digest = _sha256_file(path)
            size = path.stat().st_size
            cached = self.repository.latest(project_id, relative_path)
            if (
                cached is not None
                and cached.sha256 == digest
                and {"source_size_bytes", "loaded_bytes"} <= cached.content.keys()
            ):
                records.append(cached)
                continue
            records.append(
                self.repository.save(
                    project_id,
                    relative_path,
                    kind,
                    digest,
                    {
                        "text": content.decode("utf-8", errors="replace"),
                        "truncated": size > self.max_bytes,
                        "source_size_bytes": size,
                        "loaded_bytes": len(content),
                    },
                )
            )
        return records


def _sha256_file(path: Path, chunk_size: int = 64 * 1024) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def _read_preview(path: Path, max_bytes: int) -> bytes:
    with path.open("rb") as handle:
        return handle.read(max_bytes)
