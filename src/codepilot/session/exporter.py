from __future__ import annotations

import hashlib
import json
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from codepilot.session.database import SessionDatabase
from codepilot.session.paths import SessionPaths, resolve_session_paths


class SessionExporter:
    """把 SQLite 一致快照显式导出为 v2 文件结构。"""

    def __init__(self, database: SessionDatabase, paths: SessionPaths | None = None) -> None:
        self.database = database
        self.paths = paths or resolve_session_paths(database.path.parent)

    def export(self, session_id: str, target_root: Path | None = None) -> Path:
        root = (target_root or self.paths.exports_dir).expanduser().resolve()
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        final_dir = root / f"{session_id}-{timestamp}-{uuid4().hex[:6]}"
        temporary_dir = root / f".{final_dir.name}.tmp"
        root.mkdir(parents=True, exist_ok=True)
        try:
            with self.database.transaction() as connection:
                session = self._one(connection, "SELECT * FROM sessions WHERE session_id = ?", (session_id,))
                if session is None:
                    raise LookupError(session_id)
                tables = {
                    "turns": self._all(connection, "SELECT * FROM turns WHERE session_id = ? ORDER BY sequence", (session_id,)),
                    "messages": self._all(connection, "SELECT * FROM messages WHERE session_id = ? ORDER BY created_at, message_id", (session_id,)),
                    "events": self._all(connection, "SELECT * FROM session_events WHERE session_id = ? ORDER BY sequence", (session_id,)),
                    "artifacts": self._all(connection, "SELECT * FROM artifacts WHERE session_id = ? ORDER BY created_at, artifact_id", (session_id,)),
                }
                project = self._one(connection, "SELECT * FROM projects WHERE project_id = ?", (session["project_id"],))
                attempts = self._all(connection, "SELECT * FROM run_attempts WHERE turn_id IN (SELECT turn_id FROM turns WHERE session_id = ?) ORDER BY turn_id, attempt_number", (session_id,))
                parts = self._all(connection, "SELECT * FROM message_parts WHERE message_id IN (SELECT message_id FROM messages WHERE session_id = ?) ORDER BY message_id, sequence", (session_id,))
                tool_calls = self._all(connection, "SELECT * FROM tool_calls WHERE turn_id IN (SELECT turn_id FROM turns WHERE session_id = ?) ORDER BY created_at, tool_call_id", (session_id,))
                tool_results = self._all(connection, "SELECT * FROM tool_results WHERE tool_call_id IN (SELECT tool_call_id FROM tool_calls WHERE turn_id IN (SELECT turn_id FROM turns WHERE session_id = ?)) ORDER BY created_at, tool_result_id", (session_id,))
                permission_requests = self._all(connection, "SELECT * FROM permission_requests WHERE session_id = ? ORDER BY created_at, request_id", (session_id,))
                permission_responses = self._all(connection, "SELECT * FROM permission_responses WHERE request_id IN (SELECT request_id FROM permission_requests WHERE session_id = ?) ORDER BY responded_at, response_id", (session_id,))
                grants = self._all(connection, "SELECT * FROM permission_grants WHERE session_id = ? ORDER BY created_at, grant_id", (session_id,))
                summaries = self._all(connection, "SELECT * FROM context_summaries WHERE session_id = ? ORDER BY created_at, summary_id", (session_id,))
            temporary_dir.mkdir(parents=True, exist_ok=False)
            (temporary_dir / "artifacts").mkdir()
            self._write_json(temporary_dir / "session.json", {"project": dict(project), "session": dict(session)})
            self._write_jsonl(temporary_dir / "turns.jsonl", tables["turns"] + attempts)
            self._write_jsonl(temporary_dir / "messages.jsonl", tables["messages"] + parts)
            self._write_jsonl(temporary_dir / "events.jsonl", tables["events"] + summaries)
            self._write_jsonl(temporary_dir / "trace.jsonl", tables["events"])
            # 权限和工具记录并入 report，固定 v2 目录不额外增加文件类型。
            report = {"session_id": session_id, "turn_count": len(tables["turns"]), "event_count": len(tables["events"]), "tool_call_count": len(tool_calls), "tool_result_count": len(tool_results), "permission_request_count": len(permission_requests), "permission_response_count": len(permission_responses), "grant_count": len(grants)}
            self._write_json(temporary_dir / "report.json", report)
            for artifact in tables["artifacts"]:
                self._copy_artifact(artifact, temporary_dir / "artifacts")
            manifest = {"schema_version": "codepilot.session.export.v2", "session_id": session_id, "files": sorted(path.name for path in temporary_dir.iterdir() if path.is_file()), "artifact_count": len(tables["artifacts"])}
            self._write_json(temporary_dir / "manifest.json", manifest)
            temporary_dir.replace(final_dir)
            return final_dir
        except Exception:
            shutil.rmtree(temporary_dir, ignore_errors=True)
            raise

    @staticmethod
    def _one(connection: Any, query: str, params: tuple[Any, ...]) -> Any:
        return connection.execute(query, params).fetchone()

    @staticmethod
    def _all(connection: Any, query: str, params: tuple[Any, ...]) -> list[Any]:
        return connection.execute(query, params).fetchall()

    @staticmethod
    def _json_value(value: Any) -> Any:
        if isinstance(value, dict):
            return {key: SessionExporter._json_value(item) for key, item in value.items()}
        if isinstance(value, (bytes, bytearray)):
            return value.hex()
        return value

    @classmethod
    def _write_json(cls, path: Path, value: Any) -> None:
        path.write_text(json.dumps(cls._json_value(value), ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")

    @classmethod
    def _write_jsonl(cls, path: Path, rows: list[Any]) -> None:
        path.write_text("".join(json.dumps(cls._json_value(dict(row)), ensure_ascii=False, default=str) + "\n" for row in rows), encoding="utf-8")

    @staticmethod
    def _copy_artifact(artifact: Any, target_dir: Path) -> None:
        target = target_dir / artifact["artifact_id"]
        source = Path(artifact["storage_path"])
        if source.exists() and source.is_file():
            shutil.copy2(source, target)
        else:
            content = artifact["content_json"] or ""
            target.write_text(content, encoding="utf-8")
        digest = hashlib.sha256(target.read_bytes()).hexdigest()
        if digest != artifact["sha256"] and artifact["storage_path"] != "inline":
            raise ValueError(f"artifact hash mismatch: {artifact['artifact_id']}")
