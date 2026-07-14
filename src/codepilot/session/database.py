from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


SCHEMA_VERSION = 5


class SessionDatabase:
    """SQLite Session 数据库。

    这里刻意把连接、初始化和事务控制拆开，避免 Store 层直接依赖隐式连接状态。
    """

    def __init__(self, path: Path) -> None:
        self.path = path

    def connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON;")
        connection.execute("PRAGMA journal_mode = WAL;")
        connection.execute("PRAGMA synchronous = FULL;")
        connection.execute("PRAGMA busy_timeout = 5000;")
        return connection

    def initialize(self) -> None:
        connection = self.connect()
        try:
            connection.executescript(_schema_sql())
            row = connection.execute("SELECT value FROM schema_meta WHERE key = 'schema_version'").fetchone()
            version = int(row[0]) if row is not None else SCHEMA_VERSION
            if version < 2:
                _migrate_v1_to_v2(connection)
                version = 2
            if version < 3:
                _migrate_v2_to_v3(connection)
                version = 3
            if version < 4:
                _migrate_v3_to_v4(connection)
                version = 4
            if version < 5:
                _migrate_v4_to_v5(connection)
                version = 5
            if version != SCHEMA_VERSION:
                raise RuntimeError(f"unsupported Session schema version: {version}")
            connection.execute("INSERT OR REPLACE INTO schema_meta(key, value) VALUES ('schema_version', ?)", (str(version),))
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        connection = self.connect()
        try:
            connection.execute("BEGIN")
            yield connection
        except Exception:
            connection.rollback()
            raise
        else:
            connection.commit()
        finally:
            connection.close()


def _migrate_v1_to_v2(connection: sqlite3.Connection) -> None:
    """幂等迁移 durable recovery 字段；部分迁移中断后可安全重入。"""

    additions = {
        "run_attempts": {
            "interruption_reason": "TEXT",
            "worker_id": "TEXT",
            "lease_expires_at": "TEXT",
        },
        "tool_calls": {
            "side_effect": "TEXT",
            "idempotency": "TEXT",
            "recovery_strategy": "TEXT",
            "recovery_token_json": "TEXT",
        },
    }
    for table, columns in additions.items():
        existing = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
        for column, type_name in columns.items():
            if column not in existing:
                connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {type_name}")


def _migrate_v2_to_v3(connection: sqlite3.Connection) -> None:
    """补齐权限、消息和结果链路需要的新列。"""

    additions = {
        "message_parts": {
            "artifact_id": "TEXT",
        },
        "tool_results": {
            "output_preview": "TEXT",
            "artifact_id": "TEXT",
            "error": "TEXT",
            "success": "INTEGER",
        },
        "permission_grants": {
            "tool_name": "TEXT",
            "scope_json": "TEXT",
        },
    }
    for table, columns in additions.items():
        existing = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
        for column, type_name in columns.items():
            if column not in existing:
                connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {type_name}")


def _migrate_v3_to_v4(connection: sqlite3.Connection) -> None:
    """补齐 turn / summary 字段与权限查询索引。"""

    additions = {
        "turns": {
            "user_message_id": "TEXT",
            "started_at": "TEXT",
            "completed_at": "TEXT",
            "error_code": "TEXT",
        },
        "context_summaries": {
            "source_start_sequence": "INTEGER",
            "source_end_sequence": "INTEGER",
            "summary_message_id": "TEXT",
            "model": "TEXT",
            "status": "TEXT",
        },
        "permission_requests": {
            "session_id": "TEXT",
            "turn_id": "TEXT",
            "attempt_id": "TEXT",
            "tool_call_id": "TEXT",
        },
    }
    for table, columns in additions.items():
        existing = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
        for column, type_name in columns.items():
            if column not in existing:
                connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {type_name}")
    connection.execute("CREATE INDEX IF NOT EXISTS idx_permission_requests_session_status ON permission_requests(session_id, status)")
    connection.execute("CREATE INDEX IF NOT EXISTS idx_tool_results_tool_call_id ON tool_results(tool_call_id)")
    connection.execute("CREATE INDEX IF NOT EXISTS idx_messages_session_status ON messages(session_id, status)")
    connection.execute("CREATE INDEX IF NOT EXISTS idx_context_summaries_session_status_end ON context_summaries(session_id, status, source_end_sequence)")


def _migrate_v4_to_v5(connection: sqlite3.Connection) -> None:
    """重建权限请求表，使升级库与新建库拥有相同的外键约束。"""

    connection.execute("PRAGMA foreign_keys = OFF")
    connection.execute(
        """CREATE TABLE permission_requests_v5 (
            request_id TEXT PRIMARY KEY,
            session_id TEXT,
            turn_id TEXT,
            attempt_id TEXT,
            tool_call_id TEXT,
            scope_key TEXT,
            tool_name TEXT NOT NULL,
            arguments_json TEXT NOT NULL,
            reason TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            metadata_json TEXT NOT NULL,
            FOREIGN KEY(session_id) REFERENCES sessions(session_id),
            FOREIGN KEY(turn_id) REFERENCES turns(turn_id),
            FOREIGN KEY(attempt_id) REFERENCES run_attempts(attempt_id),
            FOREIGN KEY(tool_call_id) REFERENCES tool_calls(tool_call_id)
        )"""
    )
    connection.execute(
        """INSERT INTO permission_requests_v5
        SELECT request_id,
               CASE WHEN session_id IN (SELECT session_id FROM sessions) THEN session_id END,
               CASE WHEN turn_id IN (SELECT turn_id FROM turns) THEN turn_id END,
               CASE WHEN attempt_id IN (SELECT attempt_id FROM run_attempts) THEN attempt_id END,
               CASE WHEN tool_call_id IN (SELECT tool_call_id FROM tool_calls) THEN tool_call_id END,
               scope_key, tool_name, arguments_json, reason, status, created_at, metadata_json
        FROM permission_requests"""
    )
    connection.execute("DROP TABLE permission_requests")
    connection.execute("ALTER TABLE permission_requests_v5 RENAME TO permission_requests")
    connection.execute("CREATE INDEX IF NOT EXISTS idx_permission_requests_session_status ON permission_requests(session_id, status)")
    connection.execute("PRAGMA foreign_keys = ON")
    if connection.execute("PRAGMA foreign_key_check").fetchall():
        raise RuntimeError("Session schema migration left invalid foreign keys")

def _schema_sql() -> str:
    return """
    CREATE TABLE IF NOT EXISTS schema_meta (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS projects (
        project_id TEXT PRIMARY KEY,
        path TEXT NOT NULL UNIQUE,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS sessions (
        session_id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL,
        title TEXT NOT NULL,
        provider TEXT NOT NULL,
        current_model TEXT NOT NULL,
        permission_mode TEXT NOT NULL,
        initial_branch TEXT,
        current_branch TEXT,
        status TEXT NOT NULL,
        parent_session_id TEXT,
        forked_from_turn_id TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        last_activity_at TEXT NOT NULL,
        metadata_json TEXT NOT NULL,
        FOREIGN KEY(project_id) REFERENCES projects(project_id),
        FOREIGN KEY(parent_session_id) REFERENCES sessions(session_id),
        FOREIGN KEY(forked_from_turn_id) REFERENCES turns(turn_id)
    );

    CREATE TABLE IF NOT EXISTS turns (
        turn_id TEXT PRIMARY KEY,
        session_id TEXT NOT NULL,
        sequence INTEGER NOT NULL,
        title TEXT NOT NULL,
        status TEXT NOT NULL,
        provider_snapshot TEXT NOT NULL,
        model_snapshot TEXT NOT NULL,
        permission_mode_snapshot TEXT NOT NULL,
        branch_snapshot TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        last_activity_at TEXT NOT NULL,
        user_message_id TEXT,
        started_at TEXT,
        completed_at TEXT,
        error_code TEXT,
        metadata_json TEXT NOT NULL,
        UNIQUE(session_id, sequence),
        FOREIGN KEY(session_id) REFERENCES sessions(session_id),
        FOREIGN KEY(user_message_id) REFERENCES messages(message_id)
    );

    CREATE TABLE IF NOT EXISTS run_attempts (
        attempt_id TEXT PRIMARY KEY,
        turn_id TEXT NOT NULL,
        attempt_number INTEGER NOT NULL,
        status TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        started_at TEXT,
        ended_at TEXT,
        interruption_reason TEXT,
        worker_id TEXT,
        lease_expires_at TEXT,
        metadata_json TEXT NOT NULL,
        UNIQUE(turn_id, attempt_number),
        FOREIGN KEY(turn_id) REFERENCES turns(turn_id)
    );

    CREATE TABLE IF NOT EXISTS messages (
        message_id TEXT PRIMARY KEY,
        session_id TEXT NOT NULL,
        turn_id TEXT NOT NULL,
        attempt_id TEXT,
        role TEXT NOT NULL,
        status TEXT NOT NULL,
        content_json TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        interrupted_at TEXT,
        metadata_json TEXT NOT NULL,
        FOREIGN KEY(session_id) REFERENCES sessions(session_id),
        FOREIGN KEY(turn_id) REFERENCES turns(turn_id),
        FOREIGN KEY(attempt_id) REFERENCES run_attempts(attempt_id)
    );

    CREATE TABLE IF NOT EXISTS message_parts (
        part_id TEXT PRIMARY KEY,
        message_id TEXT NOT NULL,
        sequence INTEGER NOT NULL,
        type TEXT NOT NULL,
        content_json TEXT NOT NULL,
        provider_format TEXT,
        replayable INTEGER NOT NULL,
        created_at TEXT NOT NULL,
        artifact_id TEXT,
        metadata_json TEXT NOT NULL,
        UNIQUE(message_id, sequence),
        FOREIGN KEY(message_id) REFERENCES messages(message_id),
        FOREIGN KEY(artifact_id) REFERENCES artifacts(artifact_id)
    );

    CREATE TABLE IF NOT EXISTS tool_calls (
        tool_call_id TEXT PRIMARY KEY,
        turn_id TEXT NOT NULL,
        attempt_id TEXT,
        message_id TEXT,
        status TEXT NOT NULL,
        tool_name TEXT NOT NULL,
        arguments_json TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        started_at TEXT,
        completed_at TEXT,
        side_effect TEXT,
        idempotency TEXT,
        recovery_strategy TEXT,
        recovery_token_json TEXT,
        metadata_json TEXT NOT NULL,
        FOREIGN KEY(turn_id) REFERENCES turns(turn_id),
        FOREIGN KEY(attempt_id) REFERENCES run_attempts(attempt_id),
        FOREIGN KEY(message_id) REFERENCES messages(message_id)
    );

    CREATE TABLE IF NOT EXISTS tool_results (
        tool_result_id TEXT PRIMARY KEY,
        tool_call_id TEXT NOT NULL UNIQUE,
        status TEXT NOT NULL,
        content_json TEXT NOT NULL,
        created_at TEXT NOT NULL,
        output_preview TEXT,
        artifact_id TEXT,
        error TEXT,
        success INTEGER,
        metadata_json TEXT NOT NULL,
        FOREIGN KEY(tool_call_id) REFERENCES tool_calls(tool_call_id),
        FOREIGN KEY(artifact_id) REFERENCES artifacts(artifact_id)
    );

    CREATE TABLE IF NOT EXISTS permission_requests (
        request_id TEXT PRIMARY KEY,
        session_id TEXT,
        turn_id TEXT,
        attempt_id TEXT,
        tool_call_id TEXT,
        scope_key TEXT,
        tool_name TEXT NOT NULL,
        arguments_json TEXT NOT NULL,
        reason TEXT NOT NULL,
        status TEXT NOT NULL,
        created_at TEXT NOT NULL,
        metadata_json TEXT NOT NULL,
        FOREIGN KEY(session_id) REFERENCES sessions(session_id),
        FOREIGN KEY(turn_id) REFERENCES turns(turn_id),
        FOREIGN KEY(attempt_id) REFERENCES run_attempts(attempt_id),
        FOREIGN KEY(tool_call_id) REFERENCES tool_calls(tool_call_id)
    );

    CREATE TABLE IF NOT EXISTS permission_responses (
        response_id TEXT PRIMARY KEY,
        request_id TEXT NOT NULL,
        decision TEXT NOT NULL,
        reason TEXT,
        responded_at TEXT NOT NULL,
        metadata_json TEXT NOT NULL,
        FOREIGN KEY(request_id) REFERENCES permission_requests(request_id)
    );

    CREATE TABLE IF NOT EXISTS permission_grants (
        grant_id TEXT PRIMARY KEY,
        session_id TEXT NOT NULL,
        scope_key TEXT NOT NULL,
        tool_name TEXT,
        scope_json TEXT,
        created_at TEXT NOT NULL,
        revoked_at TEXT,
        metadata_json TEXT NOT NULL,
        FOREIGN KEY(session_id) REFERENCES sessions(session_id)
    );

    CREATE TABLE IF NOT EXISTS session_events (
        event_id TEXT PRIMARY KEY,
        session_id TEXT NOT NULL,
        sequence INTEGER NOT NULL,
        event_type TEXT NOT NULL,
        created_at TEXT NOT NULL,
        turn_id TEXT,
        attempt_id TEXT,
        payload_json TEXT NOT NULL,
        metadata_json TEXT NOT NULL,
        UNIQUE(session_id, sequence),
        FOREIGN KEY(session_id) REFERENCES sessions(session_id),
        FOREIGN KEY(turn_id) REFERENCES turns(turn_id),
        FOREIGN KEY(attempt_id) REFERENCES run_attempts(attempt_id)
    );

    CREATE TABLE IF NOT EXISTS context_summaries (
        summary_id TEXT PRIMARY KEY,
        session_id TEXT NOT NULL,
        turn_id TEXT,
        created_at TEXT NOT NULL,
        content_json TEXT NOT NULL,
        source_start_sequence INTEGER,
        source_end_sequence INTEGER,
        summary_message_id TEXT,
        model TEXT,
        status TEXT NOT NULL DEFAULT 'completed',
        metadata_json TEXT NOT NULL,
        FOREIGN KEY(session_id) REFERENCES sessions(session_id),
        FOREIGN KEY(turn_id) REFERENCES turns(turn_id),
        FOREIGN KEY(summary_message_id) REFERENCES messages(message_id)
    );

    CREATE TABLE IF NOT EXISTS artifacts (
        artifact_id TEXT PRIMARY KEY,
        session_id TEXT NOT NULL,
        kind TEXT NOT NULL,
        mime_type TEXT NOT NULL,
        size_bytes INTEGER NOT NULL,
        sha256 TEXT NOT NULL,
        storage_path TEXT NOT NULL,
        created_at TEXT NOT NULL,
        content_json TEXT,
        metadata_json TEXT NOT NULL,
        FOREIGN KEY(session_id) REFERENCES sessions(session_id)
    );

    CREATE INDEX IF NOT EXISTS idx_sessions_last_activity_at ON sessions(last_activity_at);
    CREATE INDEX IF NOT EXISTS idx_sessions_project_status ON sessions(project_id, status);
    CREATE INDEX IF NOT EXISTS idx_turns_session_sequence ON turns(session_id, sequence);
    CREATE INDEX IF NOT EXISTS idx_run_attempts_turn_number ON run_attempts(turn_id, attempt_number);
    CREATE INDEX IF NOT EXISTS idx_messages_session_turn_created_at ON messages(session_id, turn_id, created_at);
    CREATE INDEX IF NOT EXISTS idx_message_parts_message_sequence ON message_parts(message_id, sequence);
    CREATE INDEX IF NOT EXISTS idx_tool_calls_turn_status ON tool_calls(turn_id, status);
    CREATE INDEX IF NOT EXISTS idx_tool_results_tool_call_id ON tool_results(tool_call_id);
    CREATE INDEX IF NOT EXISTS idx_permission_requests_session_status ON permission_requests(session_id, status);
    CREATE INDEX IF NOT EXISTS idx_session_events_session_sequence ON session_events(session_id, sequence);
    CREATE INDEX IF NOT EXISTS idx_permission_grants_session_scope_revoked ON permission_grants(session_id, scope_key, revoked_at);
    CREATE INDEX IF NOT EXISTS idx_messages_session_status ON messages(session_id, status);
    CREATE INDEX IF NOT EXISTS idx_context_summaries_session_status_end ON context_summaries(session_id, status, source_end_sequence);
    CREATE INDEX IF NOT EXISTS idx_artifacts_session_created_at ON artifacts(session_id, created_at);
    """
