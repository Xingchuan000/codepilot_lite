from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from codepilot.session.database import SCHEMA_VERSION, SessionDatabase


def test_empty_database_is_created_as_latest_schema(tmp_path: Path) -> None:
    database = SessionDatabase(tmp_path / "session.sqlite3")
    database.initialize()

    with database.transaction() as connection:
        assert connection.execute("SELECT value FROM schema_meta WHERE key = 'schema_version'").fetchone()[0] == str(SCHEMA_VERSION)
        assert connection.execute("PRAGMA table_info(permission_requests)").fetchall()
        assert connection.execute("SELECT name FROM sqlite_master WHERE type = 'index' AND name = 'idx_permission_requests_session_status'").fetchone()


def test_old_permission_table_is_migrated_before_latest_index_is_created(tmp_path: Path) -> None:
    path = tmp_path / "v2.sqlite3"
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE schema_meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE projects(project_id TEXT PRIMARY KEY, path TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE sessions(session_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, title TEXT NOT NULL, provider TEXT NOT NULL, current_model TEXT NOT NULL, permission_mode TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, last_activity_at TEXT NOT NULL, metadata_json TEXT NOT NULL);
        CREATE TABLE turns(turn_id TEXT PRIMARY KEY, session_id TEXT NOT NULL, sequence INTEGER NOT NULL, title TEXT NOT NULL, status TEXT NOT NULL, provider_snapshot TEXT NOT NULL, model_snapshot TEXT NOT NULL, permission_mode_snapshot TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, last_activity_at TEXT NOT NULL, metadata_json TEXT NOT NULL);
        CREATE TABLE run_attempts(attempt_id TEXT PRIMARY KEY, turn_id TEXT NOT NULL, attempt_number INTEGER NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, metadata_json TEXT NOT NULL);
        CREATE TABLE tool_calls(tool_call_id TEXT PRIMARY KEY, turn_id TEXT NOT NULL, status TEXT NOT NULL, tool_name TEXT NOT NULL, arguments_json TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, metadata_json TEXT NOT NULL);
        CREATE TABLE messages(message_id TEXT PRIMARY KEY, session_id TEXT NOT NULL, turn_id TEXT NOT NULL, role TEXT NOT NULL, status TEXT NOT NULL, content_json TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, metadata_json TEXT NOT NULL);
        CREATE TABLE message_parts(part_id TEXT PRIMARY KEY, message_id TEXT NOT NULL, sequence INTEGER NOT NULL, type TEXT NOT NULL, content_json TEXT NOT NULL, replayable INTEGER NOT NULL, created_at TEXT NOT NULL, metadata_json TEXT NOT NULL);
        CREATE TABLE tool_results(tool_result_id TEXT PRIMARY KEY, tool_call_id TEXT NOT NULL, status TEXT NOT NULL, content_json TEXT NOT NULL, created_at TEXT NOT NULL, metadata_json TEXT NOT NULL);
        CREATE TABLE permission_requests(request_id TEXT PRIMARY KEY, scope_key TEXT, tool_name TEXT NOT NULL, arguments_json TEXT NOT NULL, reason TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL, metadata_json TEXT NOT NULL);
        INSERT INTO schema_meta VALUES ('schema_version', '2');
        """
    )
    connection.commit()
    connection.close()

    SessionDatabase(path).initialize()

    with SessionDatabase(path).transaction() as migrated:
        columns = {row[1] for row in migrated.execute("PRAGMA table_info(permission_requests)")}
        assert {"session_id", "turn_id", "attempt_id", "tool_call_id"} <= columns


def test_unknown_schema_version_is_rejected_without_overwriting_it(tmp_path: Path) -> None:
    database = SessionDatabase(tmp_path / "future.sqlite3")
    database.initialize()
    with database.transaction() as connection:
        connection.execute("UPDATE schema_meta SET value = '99' WHERE key = 'schema_version'")

    with pytest.raises(RuntimeError, match="unsupported"):
        database.initialize()

    with database.transaction() as connection:
        assert connection.execute("SELECT value FROM schema_meta WHERE key = 'schema_version'").fetchone()[0] == "99"
