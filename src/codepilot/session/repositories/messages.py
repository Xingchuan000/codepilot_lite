from __future__ import annotations

from typing import Any

from codepilot.session.database import SessionDatabase
from codepilot.session.ids import make_message_id, make_part_id, now_iso
from codepilot.session.models import MessagePartRecord, MessageRecord
from codepilot.session.row_mappers import message_from_row, message_part_from_row
from codepilot.session.repositories._support import bool_to_int, json_dumps


class MessageRepository:
    """Persistence operations for messages and message parts."""

    def __init__(self, database: SessionDatabase) -> None:
        self.database = database

    def create_message(
        self,
        *,
        session_id: str,
        turn_id: str,
        role: str,
        status: str,
        content: Any,
        attempt_id: str | None = None,
        interrupted_at: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> MessageRecord:
        timestamp = now_iso()
        message_id = make_message_id()
        with self.database.transaction() as connection:
            connection.execute(
                """INSERT INTO messages(
                    message_id, session_id, turn_id, attempt_id, role, status, content_json,
                    created_at, updated_at, interrupted_at, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (message_id, session_id, turn_id, attempt_id, role, status, json_dumps(content), timestamp, timestamp, interrupted_at, json_dumps(metadata or {})),
            )
        return self.get_message(message_id)

    def create_message_with_part(
        self,
        *,
        session_id: str,
        turn_id: str,
        role: str,
        status: str,
        content: Any,
        part_type: str,
        part_content: Any,
        attempt_id: str | None = None,
        interrupted_at: str | None = None,
        metadata: dict[str, Any] | None = None,
        provider_format: str | None = None,
        replayable: bool = True,
        artifact_id: str | None = None,
        part_metadata: dict[str, Any] | None = None,
    ) -> tuple[MessageRecord, MessagePartRecord]:
        timestamp = now_iso()
        message_id = make_message_id()
        part_id = make_part_id()
        with self.database.transaction() as connection:
            connection.execute(
                """INSERT INTO messages(
                    message_id, session_id, turn_id, attempt_id, role, status, content_json,
                    created_at, updated_at, interrupted_at, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (message_id, session_id, turn_id, attempt_id, role, status, json_dumps(content), timestamp, timestamp, interrupted_at, json_dumps(metadata or {})),
            )
            connection.execute(
                """INSERT INTO message_parts(
                    part_id, message_id, sequence, type, content_json, provider_format,
                    replayable, created_at, artifact_id, metadata_json
                ) VALUES (?, ?, 1, ?, ?, ?, ?, ?, ?, ?)""",
                (part_id, message_id, part_type, json_dumps(part_content), provider_format, bool_to_int(replayable), timestamp, artifact_id, json_dumps(part_metadata or {})),
            )
            message_row = connection.execute("SELECT * FROM messages WHERE message_id = ?", (message_id,)).fetchone()
            part_row = connection.execute("SELECT * FROM message_parts WHERE part_id = ?", (part_id,)).fetchone()
        assert message_row is not None
        assert part_row is not None
        return message_from_row(message_row), message_part_from_row(part_row)

    def get_message(self, message_id: str) -> MessageRecord:
        with self.database.transaction() as connection:
            row = connection.execute("SELECT * FROM messages WHERE message_id = ?", (message_id,)).fetchone()
        if row is None:
            raise LookupError(message_id)
        return message_from_row(row)

    def append_message_part(
        self,
        message_id: str,
        *,
        type: str,
        content: Any,
        provider_format: str | None = None,
        replayable: bool = True,
        artifact_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> MessagePartRecord:
        timestamp = now_iso()
        part_id = make_part_id()
        with self.database.transaction() as connection:
            sequence = connection.execute(
                "SELECT COALESCE(MAX(sequence), 0) + 1 FROM message_parts WHERE message_id = ?", (message_id,)
            ).fetchone()[0]
            connection.execute(
                """INSERT INTO message_parts(
                    part_id, message_id, sequence, type, content_json, provider_format,
                    replayable, created_at, artifact_id, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (part_id, message_id, sequence, type, json_dumps(content), provider_format, bool_to_int(replayable), timestamp, artifact_id, json_dumps(metadata or {})),
            )
        return self.get_message_part(part_id)

    def get_message_part(self, part_id: str) -> MessagePartRecord:
        with self.database.transaction() as connection:
            row = connection.execute("SELECT * FROM message_parts WHERE part_id = ?", (part_id,)).fetchone()
        if row is None:
            raise LookupError(part_id)
        return message_part_from_row(row)

    def find_tool_call_part(self, message_id: str, provider_tool_call_id: str) -> MessagePartRecord | None:
        with self.database.transaction() as connection:
            rows = connection.execute(
                "SELECT * FROM message_parts WHERE message_id = ? AND type = 'tool_call' ORDER BY sequence",
                (message_id,),
            ).fetchall()
        for row in rows:
            part = message_part_from_row(row)
            if isinstance(part.content, dict) and part.content.get("provider_tool_call_id") == provider_tool_call_id:
                return part
        return None

    def update_message_status(self, message_id: str, status: str, interrupted_at: str | None = None) -> MessageRecord:
        timestamp = now_iso()
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE messages SET status = ?, updated_at = ?, interrupted_at = COALESCE(?, interrupted_at) WHERE message_id = ?",
                (status, timestamp, interrupted_at, message_id),
            )
        return self.get_message(message_id)

    def list_messages_with_parts(self, session_id: str, turn_id: str | None = None) -> list[tuple[MessageRecord, list[MessagePartRecord]]]:
        query = "SELECT * FROM messages WHERE session_id = ?"
        params: list[Any] = [session_id]
        if turn_id is not None:
            query += " AND turn_id = ?"
            params.append(turn_id)
        query += " ORDER BY created_at, message_id"
        with self.database.transaction() as connection:
            messages = connection.execute(query, params).fetchall()
            parts = (
                connection.execute(
                    "SELECT * FROM message_parts WHERE message_id IN (%s) ORDER BY message_id, sequence"
                    % ",".join("?" for _ in messages),
                    [row["message_id"] for row in messages],
                ).fetchall()
                if messages
                else []
            )
        parts_by_message: dict[str, list[MessagePartRecord]] = {}
        for row in parts:
            part = message_part_from_row(row)
            parts_by_message.setdefault(part.message_id, []).append(part)
        return [(message_from_row(row), parts_by_message.get(row["message_id"], [])) for row in messages]

    def get_user_message_for_turn(self, turn_id: str) -> MessageRecord | None:
        with self.database.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM messages WHERE turn_id = ? AND role = 'user' ORDER BY created_at, message_id LIMIT 1", (turn_id,)
            ).fetchone()
        return message_from_row(row) if row is not None else None
