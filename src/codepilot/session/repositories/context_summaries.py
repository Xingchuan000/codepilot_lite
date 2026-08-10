from __future__ import annotations

from typing import Any

from codepilot.session.database import SessionDatabase
from codepilot.session.ids import make_event_id, make_message_id, make_part_id, now_iso
from codepilot.session.models import ContextSummaryRecord
from codepilot.session.row_mappers import context_summary_from_row
from codepilot.session.repositories._support import json_dumps, local_id


class ContextSummaryRepository:
    """Persistence operations for context summary records."""

    def __init__(self, database: SessionDatabase) -> None:
        self.database = database

    def create_context_summary(self, *, session_id: str, content: Any, turn_id: str | None = None, source_start_sequence: int | None = None, source_end_sequence: int | None = None, summary_message_id: str | None = None, model: str | None = None, status: str = "completed", metadata: dict[str, Any] | None = None) -> ContextSummaryRecord:
        timestamp = now_iso()
        summary_id = local_id("summary")
        with self.database.transaction() as connection:
            connection.execute(
                """INSERT INTO context_summaries(
                    summary_id, session_id, turn_id, created_at, content_json,
                    source_start_sequence, source_end_sequence, summary_message_id,
                    model, status, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (summary_id, session_id, turn_id, timestamp, json_dumps(content), source_start_sequence, source_end_sequence, summary_message_id, model, status, json_dumps(metadata or {})),
            )
        return self.get_context_summary(summary_id)

    def create_context_summary_with_message(self, *, session_id: str, turn_id: str | None, content: str, source_start_sequence: int | None, source_end_sequence: int | None, model: str | None, metadata: dict[str, Any] | None = None) -> ContextSummaryRecord:
        timestamp = now_iso()
        message_id = make_message_id()
        part_id = make_part_id()
        summary_id = local_id("summary")
        with self.database.transaction() as connection:
            turn_id = turn_id or connection.execute("SELECT turn_id FROM turns WHERE session_id = ? ORDER BY sequence DESC LIMIT 1", (session_id,)).fetchone()[0]
            connection.execute(
                "INSERT INTO messages(message_id, session_id, turn_id, attempt_id, role, status, content_json, created_at, updated_at, interrupted_at, metadata_json) VALUES (?, ?, ?, NULL, 'system', 'completed', ?, ?, ?, NULL, ?)",
                (message_id, session_id, turn_id, json_dumps(content), timestamp, timestamp, json_dumps({"summary_id": summary_id})),
            )
            connection.execute(
                "INSERT INTO message_parts(part_id, message_id, sequence, type, content_json, provider_format, replayable, created_at, artifact_id, metadata_json) VALUES (?, ?, 1, 'summary', ?, NULL, 1, ?, NULL, ?)",
                (part_id, message_id, json_dumps(content), timestamp, json_dumps({"summary_id": summary_id})),
            )
            connection.execute(
                "INSERT INTO context_summaries(summary_id, session_id, turn_id, created_at, content_json, source_start_sequence, source_end_sequence, summary_message_id, model, status, metadata_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'completed', ?)",
                (summary_id, session_id, turn_id, timestamp, json_dumps(content), source_start_sequence, source_end_sequence, message_id, model, json_dumps(metadata or {})),
            )
        return self.get_context_summary(summary_id)

    def replace_context_summary(self, *, session_id: str, previous_summary_id: str | None, summary_content: Any, turn_id: str | None, source_start_sequence: int | None, source_end_sequence: int | None, model: str | None, metadata: dict[str, Any] | None, event_payload: dict[str, Any]) -> ContextSummaryRecord:
        timestamp = now_iso()
        message_id = make_message_id()
        part_id = make_part_id()
        summary_id = local_id("summary")
        with self.database.transaction() as connection:
            if previous_summary_id is not None:
                previous = connection.execute("SELECT status FROM context_summaries WHERE summary_id = ? AND session_id = ?", (previous_summary_id, session_id)).fetchone()
                if previous is None or previous["status"] not in {None, "completed"}:
                    raise RuntimeError("previous context summary is no longer completed")
            turn_id = turn_id or connection.execute("SELECT turn_id FROM turns WHERE session_id = ? ORDER BY sequence DESC LIMIT 1", (session_id,)).fetchone()[0]
            connection.execute(
                "INSERT INTO messages(message_id, session_id, turn_id, attempt_id, role, status, content_json, created_at, updated_at, interrupted_at, metadata_json) VALUES (?, ?, ?, NULL, 'system', 'completed', ?, ?, ?, NULL, ?)",
                (message_id, session_id, turn_id, json_dumps(summary_content), timestamp, timestamp, json_dumps({"summary_id": summary_id})),
            )
            connection.execute(
                "INSERT INTO message_parts(part_id, message_id, sequence, type, content_json, provider_format, replayable, created_at, artifact_id, metadata_json) VALUES (?, ?, 1, 'summary', ?, NULL, 1, ?, NULL, ?)",
                (part_id, message_id, json_dumps(summary_content), timestamp, json_dumps({"summary_id": summary_id})),
            )
            connection.execute(
                "INSERT INTO context_summaries(summary_id, session_id, turn_id, created_at, content_json, source_start_sequence, source_end_sequence, summary_message_id, model, status, metadata_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'completed', ?)",
                (summary_id, session_id, turn_id, timestamp, json_dumps(summary_content), source_start_sequence, source_end_sequence, message_id, model, json_dumps(metadata or {})),
            )
            connection.execute(
                "UPDATE context_summaries SET status = 'superseded' WHERE session_id = ? AND COALESCE(status, 'completed') = 'completed' AND summary_id != ?",
                (session_id, summary_id),
            )
            sequence = connection.execute("SELECT COALESCE(MAX(sequence), 0) + 1 FROM session_events WHERE session_id = ?", (session_id,)).fetchone()[0]
            connection.execute(
                "INSERT INTO session_events(event_id, session_id, sequence, event_type, created_at, turn_id, attempt_id, payload_json, metadata_json) VALUES (?, ?, ?, 'context_compacted', ?, ?, NULL, ?, ?)",
                (make_event_id(), session_id, sequence, timestamp, turn_id, json_dumps(event_payload | {"summary_id": summary_id}), json_dumps({"source": "compaction_service"})),
            )
            connection.execute("UPDATE sessions SET updated_at = ?, last_activity_at = ? WHERE session_id = ?", (timestamp, timestamp, session_id))
        return self.get_context_summary(summary_id)

    def get_context_summary(self, summary_id: str) -> ContextSummaryRecord:
        with self.database.transaction() as connection:
            row = connection.execute("SELECT * FROM context_summaries WHERE summary_id = ?", (summary_id,)).fetchone()
        if row is None:
            raise LookupError(summary_id)
        return context_summary_from_row(row)

    def list_context_summaries(self, session_id: str) -> list[ContextSummaryRecord]:
        with self.database.transaction() as connection:
            rows = connection.execute("SELECT * FROM context_summaries WHERE session_id = ? ORDER BY created_at, summary_id", (session_id,)).fetchall()
        return [context_summary_from_row(row) for row in rows]

    def get_latest_context_summary(self, session_id: str) -> ContextSummaryRecord | None:
        with self.database.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM context_summaries WHERE session_id = ? AND COALESCE(status, 'completed') = 'completed' ORDER BY COALESCE(source_end_sequence, -1) DESC, created_at DESC, summary_id DESC LIMIT 1",
                (session_id,),
            ).fetchone()
        return context_summary_from_row(row) if row is not None else None

    def update_context_summary_status(self, summary_id: str, status: str) -> ContextSummaryRecord:
        with self.database.transaction() as connection:
            connection.execute("UPDATE context_summaries SET status = ? WHERE summary_id = ?", (status, summary_id))
        return self.get_context_summary(summary_id)
