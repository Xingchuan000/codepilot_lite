from __future__ import annotations

from typing import Any

from codepilot.session.database import SessionDatabase
from codepilot.session.ids import make_artifact_id, now_iso
from codepilot.session.models import ArtifactRecord
from codepilot.session.row_mappers import artifact_from_row
from codepilot.session.repositories._support import json_dumps


class ArtifactRepository:
    """Database metadata for artifacts; filesystem content stays in ArtifactStore."""

    def __init__(self, database: SessionDatabase) -> None:
        self.database = database

    def create_artifact(self, *, session_id: str, kind: str, mime_type: str, size_bytes: int, sha256: str, storage_path: str, artifact_id: str | None = None, content: Any | None = None, metadata: dict[str, Any] | None = None) -> ArtifactRecord:
        timestamp = now_iso()
        artifact_id = artifact_id or make_artifact_id()
        with self.database.transaction() as connection:
            connection.execute(
                """INSERT INTO artifacts(
                    artifact_id, session_id, kind, mime_type, size_bytes, sha256,
                    storage_path, created_at, content_json, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (artifact_id, session_id, kind, mime_type, size_bytes, sha256, storage_path, timestamp, json_dumps(content) if content is not None else None, json_dumps(metadata or {})),
            )
        return self.get_artifact(artifact_id)

    def get_artifact(self, artifact_id: str) -> ArtifactRecord:
        with self.database.transaction() as connection:
            row = connection.execute("SELECT * FROM artifacts WHERE artifact_id = ?", (artifact_id,)).fetchone()
        if row is None:
            raise LookupError(artifact_id)
        return artifact_from_row(row)

    def list_artifacts(self, session_id: str) -> list[ArtifactRecord]:
        with self.database.transaction() as connection:
            rows = connection.execute("SELECT * FROM artifacts WHERE session_id = ? ORDER BY created_at, artifact_id", (session_id,)).fetchall()
        return [artifact_from_row(row) for row in rows]
