from __future__ import annotations

import hashlib
import json
import shutil
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from codepilot.session.artifacts import ArtifactStore
from codepilot.session.database import SessionDatabase
from codepilot.session.paths import SessionPaths, resolve_session_paths


class SessionExporter:
    """把 SQLite Session 快照导出为可校验的目录。"""

    def __init__(self, database: SessionDatabase, paths: SessionPaths | None = None) -> None:
        self.database = database
        self.paths = paths or resolve_session_paths(database.path.parent)
        self.artifacts = ArtifactStore(database, self.paths)

    def export(self, session_id: str, target_root: Path | None = None) -> Path:
        root = (target_root or self.paths.exports_dir).expanduser().resolve()
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        final_dir = root / f"{session_id}-{timestamp}-{uuid4().hex[:6]}"
        staging_dir = root / f".{final_dir.name}.tmp"
        root.mkdir(parents=True, exist_ok=True)
        snapshot = self._snapshot(session_id)
        try:
            staging_dir.mkdir(parents=True, exist_ok=False)
            (staging_dir / "artifacts").mkdir()
            session, project, turns, attempts, messages, parts, events, tool_calls, tool_results, requests, responses, grants, summaries, artifacts = snapshot
            self._write_json(staging_dir / "session.json", {"schema_version": "codepilot.session.export.v2", "project": self._row_dict(project), "session": self._row_dict(session)})
            self._write_jsonl(staging_dir / "turns.jsonl", [self._record("turn", row) for row in turns] + [self._record("attempt", row) for row in attempts])
            self._write_jsonl(staging_dir / "messages.jsonl", [self._record("message", row) for row in messages] + [self._record("message_part", row) for row in parts])
            self._write_jsonl(staging_dir / "events.jsonl", [self._record("event", row) for row in events] + [self._record("context_summary", row) for row in summaries])
            self._write_jsonl(staging_dir / "trace.jsonl", [self._record_trace(session_id, row) for row in events])
            self._write_json(
                staging_dir / "report.json",
                {
                    "session_id": session_id,
                    "status": session["status"],
                    "turns": [self._row_dict(row) for row in turns],
                    "attempts": [self._row_dict(row) for row in attempts],
                    "tool_calls": [self._row_dict(row) for row in tool_calls],
                    "tool_results": [self._row_dict(row) for row in tool_results],
                    "permission_requests": [self._row_dict(row) for row in requests],
                    "permission_responses": [self._row_dict(row) for row in responses],
                    "grants": [self._row_dict(row) for row in grants],
                    "compact_count": sum(row["event_type"] == "context_compacted" for row in events),
                    "artifact_count": len(artifacts),
                    "artifact_size_bytes": sum(row["size_bytes"] for row in artifacts),
                    "recoveries": [self._row_dict(row) for row in events if row["event_type"] in {"tool_reconciled", "recovery_required", "permission_recovery_resumed"}],
                },
            )
            for artifact in artifacts:
                self.artifacts.copy_to_export(artifact["artifact_id"], staging_dir / "artifacts")
            files = [path for path in staging_dir.rglob("*") if path.is_file() and path.name != "manifest.json"]
            self._write_json(
                staging_dir / "manifest.json",
                {
                    "schema_version": "codepilot.session.export.v2",
                    "session_id": session_id,
                    "exported_at": timestamp,
                    "files": [{"relative_path": path.relative_to(staging_dir).as_posix(), "size_bytes": path.stat().st_size, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()} for path in sorted(files)],
                },
            )
            staging_dir.replace(final_dir)
            return final_dir
        except Exception:
            shutil.rmtree(staging_dir, ignore_errors=True)
            shutil.rmtree(final_dir, ignore_errors=True)
            raise

    def _snapshot(self, session_id: str):
        with self.database.transaction() as connection:
            session = connection.execute("SELECT * FROM sessions WHERE session_id = ?", (session_id,)).fetchone()
            if session is None:
                raise LookupError(session_id)
            project = connection.execute("SELECT * FROM projects WHERE project_id = ?", (session["project_id"],)).fetchone()
            turns = connection.execute("SELECT * FROM turns WHERE session_id = ? ORDER BY sequence", (session_id,)).fetchall()
            attempts = connection.execute("SELECT * FROM run_attempts WHERE turn_id IN (SELECT turn_id FROM turns WHERE session_id = ?) ORDER BY turn_id, attempt_number", (session_id,)).fetchall()
            messages = connection.execute("SELECT * FROM messages WHERE session_id = ? ORDER BY created_at, message_id", (session_id,)).fetchall()
            parts = connection.execute("SELECT * FROM message_parts WHERE message_id IN (SELECT message_id FROM messages WHERE session_id = ?) ORDER BY message_id, sequence", (session_id,)).fetchall()
            events = connection.execute("SELECT * FROM session_events WHERE session_id = ? ORDER BY sequence", (session_id,)).fetchall()
            tool_calls = connection.execute("SELECT * FROM tool_calls WHERE turn_id IN (SELECT turn_id FROM turns WHERE session_id = ?) ORDER BY created_at, tool_call_id", (session_id,)).fetchall()
            tool_results = connection.execute("SELECT tr.* FROM tool_results tr JOIN tool_calls tc ON tc.tool_call_id = tr.tool_call_id JOIN turns t ON t.turn_id = tc.turn_id WHERE t.session_id = ? ORDER BY tr.created_at, tr.tool_result_id", (session_id,)).fetchall()
            requests = connection.execute("SELECT * FROM permission_requests WHERE session_id = ? ORDER BY created_at, request_id", (session_id,)).fetchall()
            responses = connection.execute("SELECT * FROM permission_responses WHERE request_id IN (SELECT request_id FROM permission_requests WHERE session_id = ?) ORDER BY responded_at, response_id", (session_id,)).fetchall()
            grants = connection.execute("SELECT * FROM permission_grants WHERE session_id = ? ORDER BY created_at, grant_id", (session_id,)).fetchall()
            summaries = connection.execute("SELECT * FROM context_summaries WHERE session_id = ? ORDER BY created_at, summary_id", (session_id,)).fetchall()
            artifacts = connection.execute("SELECT * FROM artifacts WHERE session_id = ? ORDER BY created_at, artifact_id", (session_id,)).fetchall()
        return session, project, turns, attempts, messages, parts, events, tool_calls, tool_results, requests, responses, grants, summaries, artifacts

    @staticmethod
    def _record(record_type: str, row: object) -> dict[str, object]:
        return {"record_type": record_type, **dict(row)}

    @staticmethod
    def _record_trace(session_id: str, row: object) -> dict[str, object]:
        return {"sequence": row["sequence"], "timestamp": row["created_at"], "event_type": row["event_type"], "session_id": session_id, "turn_id": row["turn_id"], "attempt_id": row["attempt_id"], "payload": json.loads(row["payload_json"])}

    @staticmethod
    def _row_dict(row: object) -> dict[str, object]:
        return dict(row)

    @staticmethod
    def _write_json(path: Path, value: object) -> None:
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")

    @staticmethod
    def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
        path.write_text("".join(json.dumps(row, ensure_ascii=False, default=str) + "\n" for row in rows), encoding="utf-8")
