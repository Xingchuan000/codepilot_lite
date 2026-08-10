from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from codepilot.session.artifacts import ArtifactStore
from codepilot.session.database import SessionDatabase
from codepilot.session.paths import SessionPaths, resolve_session_paths

Snapshot = tuple[object, ...]


class SessionExporter:
    """把 SQLite Session 快照导出为可校验的目录。

    导出 Parent Session 时会递归包含所有 Child/Descendant Session。当前 Session
    仍保留原有 v2 文件布局；子 Session 放在 ``child_sessions/<session_id>/`` 下，
    因此已有只读取根目录文件的诊断工具不需要改变。
    """

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
        snapshots, children_by_parent = self._snapshot_tree(session_id)
        try:
            staging_dir.mkdir(parents=True, exist_ok=False)
            self._write_session_bundle(
                session_id=session_id,
                target_dir=staging_dir,
                snapshots=snapshots,
                children_by_parent=children_by_parent,
                timestamp=timestamp,
            )
            staging_dir.replace(final_dir)
            return final_dir
        except Exception:
            shutil.rmtree(staging_dir, ignore_errors=True)
            shutil.rmtree(final_dir, ignore_errors=True)
            raise

    def _write_session_bundle(
        self,
        *,
        session_id: str,
        target_dir: Path,
        snapshots: Mapping[str, Snapshot],
        children_by_parent: Mapping[str, Sequence[str]],
        timestamp: str,
    ) -> None:
        """写一个 Session 以及其整个 Child Session 子树。"""

        snapshot = snapshots[session_id]
        (
            session,
            project,
            turns,
            attempts,
            messages,
            parts,
            events,
            tool_calls,
            tool_results,
            requests,
            responses,
            grants,
            summaries,
            context_snapshots,
            artifacts,
        ) = snapshot
        child_session_ids = list(children_by_parent.get(session_id, ()))
        descendant_session_ids = self._descendant_session_ids(session_id, children_by_parent)

        target_dir.mkdir(parents=True, exist_ok=True)
        (target_dir / "artifacts").mkdir(exist_ok=True)
        self._write_json(
            target_dir / "session.json",
            {
                "schema_version": "codepilot.session.export.v2",
                "project": self._row_dict(project),
                "session": self._row_dict(session),
            },
        )
        self._write_jsonl(
            target_dir / "turns.jsonl",
            [self._record("turn", row) for row in turns]
            + [self._record("attempt", row) for row in attempts],
        )
        self._write_jsonl(
            target_dir / "messages.jsonl",
            [self._record("message", row) for row in messages]
            + [self._record("message_part", row) for row in parts],
        )
        self._write_jsonl(
            target_dir / "events.jsonl",
            [self._record("event", row) for row in events]
            + [self._record("context_summary", row) for row in summaries]
            + [self._record("context_compaction_snapshot", row) for row in context_snapshots],
        )
        self._write_jsonl(
            target_dir / "trace.jsonl",
            [self._record_trace(session_id, row) for row in events],
        )
        self._write_json(
            target_dir / "report.json",
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
                "context_compaction_snapshots": [self._row_dict(row) for row in context_snapshots],
                "compact_count": sum(row["event_type"] == "context_compacted" for row in events),
                "artifact_count": len(artifacts),
                "artifact_size_bytes": sum(row["size_bytes"] for row in artifacts),
                "recoveries": [
                    self._row_dict(row)
                    for row in events
                    if row["event_type"]
                    in {"tool_reconciled", "recovery_required", "permission_recovery_resumed"}
                ],
                "child_session_ids": child_session_ids,
                "descendant_session_ids": descendant_session_ids,
                "descendant_session_count": len(descendant_session_ids),
            },
        )
        for artifact in artifacts:
            self.artifacts.copy_to_export(artifact["artifact_id"], target_dir / "artifacts")

        # 先完整写入 Child 子树，再生成当前 Session manifest。这样当前 manifest
        # 会递归覆盖所有 Child trace/artifact/manifest，可作为整个子树的完整性校验入口。
        for child_session_id in child_session_ids:
            self._write_session_bundle(
                session_id=child_session_id,
                target_dir=target_dir / "child_sessions" / child_session_id,
                snapshots=snapshots,
                children_by_parent=children_by_parent,
                timestamp=timestamp,
            )

        current_manifest = target_dir / "manifest.json"
        files = [
            path
            for path in target_dir.rglob("*")
            if path.is_file() and path != current_manifest
        ]
        self._write_json(
            target_dir / "manifest.json",
            {
                "schema_version": "codepilot.session.export.v2",
                "session_id": session_id,
                "exported_at": timestamp,
                "session_status_at_snapshot": session["status"],
                "active_turn_ids": [
                    row["turn_id"]
                    for row in turns
                    if row["status"]
                    in {"queued", "running", "waiting_permission", "recovery_required"}
                ],
                "snapshot_schema_version": "codepilot.session.export.v2",
                "artifact_verification_status": "verified",
                "child_session_ids": child_session_ids,
                "descendant_session_count": len(descendant_session_ids),
                "files": [
                    {
                        "relative_path": path.relative_to(target_dir).as_posix(),
                        "size_bytes": path.stat().st_size,
                        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                    }
                    for path in sorted(files)
                ],
            },
        )

    def _snapshot_tree(
        self, session_id: str
    ) -> tuple[dict[str, Snapshot], dict[str, list[str]]]:
        """在同一个 SQLite read transaction 中冻结 Parent + 全部 descendants。"""

        with self.database.transaction() as connection:
            rows = connection.execute(
                """
                WITH RECURSIVE session_tree(
                    session_id, parent_session_id, created_at, depth, path
                ) AS (
                    SELECT session_id,
                           parent_session_id,
                           created_at,
                           0,
                           '|' || session_id || '|'
                    FROM sessions
                    WHERE session_id = ?

                    UNION ALL

                    SELECT child.session_id,
                           child.parent_session_id,
                           child.created_at,
                           parent.depth + 1,
                           parent.path || child.session_id || '|'
                    FROM sessions AS child
                    JOIN session_tree AS parent
                      ON child.parent_session_id = parent.session_id
                    WHERE instr(parent.path, '|' || child.session_id || '|') = 0
                )
                SELECT session_id, parent_session_id, created_at, depth
                FROM session_tree
                ORDER BY depth ASC, created_at ASC, session_id ASC
                """,
                (session_id,),
            ).fetchall()
            if not rows:
                raise LookupError(session_id)

            snapshots: dict[str, Snapshot] = {}
            children_by_parent: dict[str, list[str]] = {}
            for row in rows:
                current_session_id = str(row["session_id"])
                snapshots[current_session_id] = self._snapshot_with_connection(
                    connection, current_session_id
                )
                parent_session_id = row["parent_session_id"]
                if parent_session_id is not None:
                    children_by_parent.setdefault(str(parent_session_id), []).append(
                        current_session_id
                    )
        return snapshots, children_by_parent

    def _snapshot_with_connection(self, connection: sqlite3.Connection, session_id: str) -> Snapshot:
        session = connection.execute(
            "SELECT * FROM sessions WHERE session_id = ?", (session_id,)
        ).fetchone()
        if session is None:
            raise LookupError(session_id)
        project = connection.execute(
            "SELECT * FROM projects WHERE project_id = ?", (session["project_id"],)
        ).fetchone()
        turns = connection.execute(
            "SELECT * FROM turns WHERE session_id = ? ORDER BY sequence", (session_id,)
        ).fetchall()
        attempts = connection.execute(
            "SELECT * FROM run_attempts WHERE turn_id IN (SELECT turn_id FROM turns WHERE session_id = ?) ORDER BY turn_id, attempt_number",
            (session_id,),
        ).fetchall()
        messages = connection.execute(
            "SELECT * FROM messages WHERE session_id = ? ORDER BY created_at, message_id",
            (session_id,),
        ).fetchall()
        parts = connection.execute(
            "SELECT * FROM message_parts WHERE message_id IN (SELECT message_id FROM messages WHERE session_id = ?) ORDER BY message_id, sequence",
            (session_id,),
        ).fetchall()
        events = connection.execute(
            "SELECT * FROM session_events WHERE session_id = ? ORDER BY sequence", (session_id,)
        ).fetchall()
        tool_calls = connection.execute(
            "SELECT * FROM tool_calls WHERE turn_id IN (SELECT turn_id FROM turns WHERE session_id = ?) ORDER BY created_at, tool_call_id",
            (session_id,),
        ).fetchall()
        tool_results = connection.execute(
            "SELECT tr.* FROM tool_results tr JOIN tool_calls tc ON tc.tool_call_id = tr.tool_call_id JOIN turns t ON t.turn_id = tc.turn_id WHERE t.session_id = ? ORDER BY tr.created_at, tr.tool_result_id",
            (session_id,),
        ).fetchall()
        requests = connection.execute(
            "SELECT * FROM permission_requests WHERE session_id = ? ORDER BY created_at, request_id",
            (session_id,),
        ).fetchall()
        responses = connection.execute(
            "SELECT * FROM permission_responses WHERE request_id IN (SELECT request_id FROM permission_requests WHERE session_id = ?) ORDER BY responded_at, response_id",
            (session_id,),
        ).fetchall()
        grants = connection.execute(
            "SELECT * FROM permission_grants WHERE session_id = ? ORDER BY created_at, grant_id",
            (session_id,),
        ).fetchall()
        summaries = connection.execute(
            "SELECT * FROM context_summaries WHERE session_id = ? ORDER BY created_at, summary_id",
            (session_id,),
        ).fetchall()
        context_snapshots = connection.execute(
            "SELECT * FROM context_compaction_snapshots WHERE session_id = ? ORDER BY created_at, snapshot_id",
            (session_id,),
        ).fetchall()
        artifacts = connection.execute(
            "SELECT * FROM artifacts WHERE session_id = ? ORDER BY created_at, artifact_id",
            (session_id,),
        ).fetchall()
        return (
            session,
            project,
            turns,
            attempts,
            messages,
            parts,
            events,
            tool_calls,
            tool_results,
            requests,
            responses,
            grants,
            summaries,
            context_snapshots,
            artifacts,
        )

    @staticmethod
    def _descendant_session_ids(
        session_id: str, children_by_parent: Mapping[str, Sequence[str]]
    ) -> list[str]:
        descendants: list[str] = []
        pending = list(children_by_parent.get(session_id, ()))
        while pending:
            current = pending.pop(0)
            descendants.append(current)
            pending[0:0] = list(children_by_parent.get(current, ()))
        return descendants

    @staticmethod
    def _record(record_type: str, row: object) -> dict[str, object]:
        return {"record_type": record_type, **dict(row)}

    @staticmethod
    def _record_trace(session_id: str, row: object) -> dict[str, object]:
        return {
            "sequence": row["sequence"],
            "timestamp": row["created_at"],
            "event_type": row["event_type"],
            "session_id": session_id,
            "turn_id": row["turn_id"],
            "attempt_id": row["attempt_id"],
            "payload": json.loads(row["payload_json"]),
        }

    @staticmethod
    def _row_dict(row: object) -> dict[str, object]:
        return dict(row)

    @staticmethod
    def _write_json(path: Path, value: object) -> None:
        path.write_text(
            json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n",
            encoding="utf-8",
        )

    @staticmethod
    def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
        path.write_text(
            "".join(json.dumps(row, ensure_ascii=False, default=str) + "\n" for row in rows),
            encoding="utf-8",
        )
