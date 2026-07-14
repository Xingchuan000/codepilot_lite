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


def test_v1_database_migrates_recovery_fields_without_losing_rows(tmp_path: Path) -> None:
    path = tmp_path / "session.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE schema_meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
            INSERT INTO schema_meta VALUES ('schema_version', '1');
            CREATE TABLE run_attempts(
                attempt_id TEXT PRIMARY KEY, turn_id TEXT NOT NULL, attempt_number INTEGER NOT NULL,
                status TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                started_at TEXT, ended_at TEXT, metadata_json TEXT NOT NULL, interruption_reason TEXT
            );
            CREATE TABLE tool_calls(
                tool_call_id TEXT PRIMARY KEY, turn_id TEXT NOT NULL, attempt_id TEXT, message_id TEXT,
                status TEXT NOT NULL, tool_name TEXT NOT NULL, arguments_json TEXT NOT NULL,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL, started_at TEXT, completed_at TEXT,
                metadata_json TEXT NOT NULL
            );
            INSERT INTO run_attempts VALUES ('attempt-1', 'turn-1', 1, 'running', 't', 't', 't', NULL, '{}', NULL);
            INSERT INTO tool_calls VALUES ('call-1', 'turn-1', 'attempt-1', NULL, 'execution_started', 'replace_range', '{}', 't', 't', 't', NULL, '{}');
            """
        )

    database = SessionDatabase(path)
    database.initialize()

    with database.connect() as connection:
        assert connection.execute("SELECT value FROM schema_meta WHERE key = 'schema_version'").fetchone()[0] == str(SCHEMA_VERSION)
        assert connection.execute("SELECT interruption_reason FROM run_attempts WHERE attempt_id = 'attempt-1'").fetchone()[0] is None
        assert {row[1] for row in connection.execute("PRAGMA table_info(run_attempts)")} >= {"interruption_reason", "worker_id", "lease_expires_at"}
        assert {row[1] for row in connection.execute("PRAGMA table_info(turns)")} >= {"user_message_id", "started_at", "completed_at", "error_code"}
        row = connection.execute(
            "SELECT side_effect, idempotency, recovery_strategy, recovery_token_json FROM tool_calls WHERE tool_call_id = 'call-1'"
        ).fetchone()
        assert tuple(row) == (None, None, None, None)
