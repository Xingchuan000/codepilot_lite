from __future__ import annotations

import sqlite3
from pathlib import Path

from codepilot.session.database import SCHEMA_VERSION, SessionDatabase


def test_initialize_is_idempotent_and_enables_pragmas(tmp_path: Path) -> None:
    database = SessionDatabase(tmp_path / "session.sqlite3")

    database.initialize()
    database.initialize()

    with database.connect() as connection:
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert connection.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        assert connection.execute("PRAGMA synchronous").fetchone()[0] == 2
        assert connection.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
        assert connection.execute("SELECT value FROM schema_meta WHERE key = ?", ("schema_version",)).fetchone()[0] == str(SCHEMA_VERSION)


def test_foreign_keys_are_enforced(tmp_path: Path) -> None:
    database = SessionDatabase(tmp_path / "session.sqlite3")
    database.initialize()

    with database.transaction() as connection:
        try:
            connection.execute(
                "INSERT INTO sessions(session_id, project_id, title, provider, current_model, permission_mode, initial_branch, current_branch, status, parent_session_id, forked_from_turn_id, created_at, updated_at, last_activity_at, metadata_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "sess-1",
                    "missing-project",
                    "New session",
                    "openai",
                    "gpt-4.1",
                    "manual",
                    None,
                    None,
                    "active",
                    None,
                    None,
                    "2024-01-01T00:00:00+00:00",
                    "2024-01-01T00:00:00+00:00",
                    "2024-01-01T00:00:00+00:00",
                    "{}",
                ),
            )
        except sqlite3.IntegrityError:
            pass
        else:
            raise AssertionError("foreign key should be enforced")
