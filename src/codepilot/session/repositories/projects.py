from __future__ import annotations

from pathlib import Path

from codepilot.session.database import SessionDatabase
from codepilot.session.ids import make_project_id, now_iso
from codepilot.session.models import ProjectRecord
from codepilot.session.row_mappers import project_from_row


class ProjectRepository:
    def __init__(self, database: SessionDatabase) -> None:
        self.database = database

    def create_project(self, path: Path) -> ProjectRecord:
        resolved = path.expanduser().resolve()
        created_at = now_iso()
        with self.database.transaction() as connection:
            row = connection.execute("SELECT * FROM projects WHERE path = ?", (str(resolved),)).fetchone()
            if row is None:
                connection.execute(
                    "INSERT INTO projects(project_id, path, created_at, updated_at) VALUES (?, ?, ?, ?)",
                    (make_project_id(), str(resolved), created_at, created_at),
                )
                row = connection.execute("SELECT * FROM projects WHERE path = ?", (str(resolved),)).fetchone()
        return project_from_row(row)

    def get_or_create_project(self, path: Path) -> ProjectRecord:
        resolved = path.expanduser().resolve()
        with self.database.transaction() as connection:
            row = connection.execute("SELECT * FROM projects WHERE path = ?", (str(resolved),)).fetchone()
            if row is not None:
                return project_from_row(row)
        return self.create_project(resolved)

    def get_project(self, project_id: str) -> ProjectRecord:
        with self.database.transaction() as connection:
            row = connection.execute("SELECT * FROM projects WHERE project_id = ?", (project_id,)).fetchone()
        if row is None:
            raise LookupError(project_id)
        return project_from_row(row)
