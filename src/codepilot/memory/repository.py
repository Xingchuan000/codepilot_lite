from __future__ import annotations

import json
import re
from typing import Any, Literal
from uuid import uuid4

from codepilot.memory.models import (
    CandidateStatus,
    MemoryCandidateRecord,
    MemoryKind,
    MemoryQuery,
    MemorySearchResult,
    ProjectInstructionRecord,
    ProjectMemoryRecord,
    TurnMemoryCheckpoint,
)
from codepilot.session.database import SessionDatabase
from codepilot.session.ids import now_iso


class MemoryRepository:
    """SQLite CRUD for project memory; policy and consolidation live elsewhere."""

    def __init__(self, database: SessionDatabase) -> None:
        self.database = database

    def add(
        self,
        project_id: str,
        kind: MemoryKind,
        canonical_key: str,
        title: str,
        content: dict[str, Any],
        *,
        confidence: float = 1.0,
        importance: int = 5,
        branch_scope: str | None = None,
        source_commit: str | None = None,
        source_session_id: str | None = None,
        source_turn_id: str | None = None,
        source_message_ids: tuple[str, ...] = (),
        source_artifact_ids: tuple[str, ...] = (),
        metadata: dict[str, Any] | None = None,
    ) -> ProjectMemoryRecord:
        timestamp = now_iso()
        memory_id = f"mem-{uuid4().hex}"
        with self.database.transaction() as connection:
            row = connection.execute(
                "SELECT COALESCE(MAX(version), 0) FROM project_memories WHERE project_id = ? AND canonical_key = ?",
                (project_id, canonical_key),
            ).fetchone()
            version = int(row[0]) + 1
            connection.execute(
                "UPDATE project_memories SET status = 'superseded', updated_at = ? WHERE project_id = ? AND canonical_key = ? AND status = 'active'",
                (timestamp, project_id, canonical_key),
            )
            connection.execute(
                """INSERT INTO project_memories(
                    memory_id, project_id, kind, canonical_key, title, content_json, status,
                    confidence, importance, branch_scope, source_commit, last_verified_commit,
                    source_session_id, source_turn_id, source_message_ids_json,
                    source_artifact_ids_json, created_at, updated_at, last_verified_at,
                    expires_at, version, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)""",
                (
                    memory_id,
                    project_id,
                    kind,
                    canonical_key,
                    title,
                    _dump(content),
                    confidence,
                    importance,
                    branch_scope,
                    source_commit,
                    source_commit,
                    source_session_id,
                    source_turn_id,
                    _dump(source_message_ids),
                    _dump(source_artifact_ids),
                    timestamp,
                    timestamp,
                    timestamp if source_commit else None,
                    version,
                    _dump(metadata or {}),
                ),
            )
        return self.get(memory_id)

    def get(self, memory_id: str) -> ProjectMemoryRecord:
        with self.database.transaction() as connection:
            row = connection.execute("SELECT * FROM project_memories WHERE memory_id = ?", (memory_id,)).fetchone()
        if row is None:
            raise LookupError(memory_id)
        return _memory(row)

    def list(self, project_id: str, statuses: tuple[str, ...] = ("active",)) -> list[ProjectMemoryRecord]:
        placeholders = ", ".join("?" for _ in statuses)
        with self.database.transaction() as connection:
            rows = connection.execute(
                f"SELECT * FROM project_memories WHERE project_id = ? AND status IN ({placeholders}) ORDER BY importance DESC, updated_at DESC",
                (project_id, *statuses),
            ).fetchall()
        return [_memory(row) for row in rows]

    def forget(self, memory_id: str) -> ProjectMemoryRecord:
        with self.database.transaction() as connection:
            if connection.execute("SELECT 1 FROM project_memories WHERE memory_id = ?", (memory_id,)).fetchone() is None:
                raise LookupError(memory_id)
            connection.execute(
                "UPDATE project_memories SET status = 'forgotten', updated_at = ? WHERE memory_id = ?",
                (now_iso(), memory_id),
            )
        return self.get(memory_id)

    def search(self, project_id: str, query: MemoryQuery) -> list[MemorySearchResult]:
        terms = re.findall(r"[\w./-]+", query.text, re.UNICODE)
        if not terms:
            return [MemorySearchResult(memory, float(memory.importance)) for memory in self.list(project_id)[: query.limit]]
        match = " OR ".join(f'"{term.replace(chr(34), "")}"' for term in terms)
        clauses = ["memory.project_id = ?", "memory.status = 'active'", "project_memories_fts MATCH ?"]
        parameters: list[Any] = [project_id, match]
        if query.kinds:
            clauses.append(f"memory.kind IN ({', '.join('?' for _ in query.kinds)})")
            parameters.extend(query.kinds)
        if query.branch:
            clauses.append("(memory.branch_scope IS NULL OR memory.branch_scope = ?)")
            parameters.append(query.branch)
        with self.database.transaction() as connection:
            rows = connection.execute(
                f"""SELECT memory.*, bm25(project_memories_fts) AS rank
                    FROM project_memories_fts AS fts
                    JOIN project_memories AS memory ON memory.memory_id = fts.memory_id
                    WHERE {' AND '.join(clauses)}
                    ORDER BY rank, memory.importance DESC, memory.confidence DESC, memory.updated_at DESC
                    LIMIT ?""",
                (*parameters, query.limit),
            ).fetchall()
        return [
            MemorySearchResult(
                _memory(row),
                -float(row["rank"]) + float(row["importance"]) + float(row["confidence"]),
            )
            for row in rows
        ]


class CandidateRepository:
    def __init__(self, database: SessionDatabase) -> None:
        self.database = database

    def create(
        self,
        project_id: str,
        session_id: str,
        turn_id: str,
        kind: MemoryKind,
        canonical_key: str,
        content: dict[str, Any],
        evidence: dict[str, Any],
        confidence: float,
        metadata: dict[str, Any] | None = None,
    ) -> MemoryCandidateRecord:
        with self.database.transaction() as connection:
            existing = connection.execute(
                """SELECT * FROM memory_candidates
                   WHERE project_id = ? AND canonical_key = ? AND status = 'pending'
                   ORDER BY created_at DESC LIMIT 1""",
                (project_id, canonical_key),
            ).fetchone()
            if existing is not None:
                return _candidate(existing)
            candidate_id = f"candidate-{uuid4().hex}"
            connection.execute(
                """INSERT INTO memory_candidates(
                    candidate_id, project_id, session_id, turn_id, kind, canonical_key,
                    content_json, evidence_json, confidence, status, created_at, decided_at,
                    metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, NULL, ?)""",
                (
                    candidate_id,
                    project_id,
                    session_id,
                    turn_id,
                    kind,
                    canonical_key,
                    _dump(content),
                    _dump(evidence),
                    confidence,
                    now_iso(),
                    _dump(metadata or {}),
                ),
            )
        return self.get(candidate_id)

    def get(self, candidate_id: str) -> MemoryCandidateRecord:
        with self.database.transaction() as connection:
            row = connection.execute("SELECT * FROM memory_candidates WHERE candidate_id = ?", (candidate_id,)).fetchone()
        if row is None:
            raise LookupError(candidate_id)
        return _candidate(row)

    def list(self, project_id: str, status: CandidateStatus = "pending") -> list[MemoryCandidateRecord]:
        with self.database.transaction() as connection:
            rows = connection.execute(
                "SELECT * FROM memory_candidates WHERE project_id = ? AND status = ? ORDER BY created_at DESC",
                (project_id, status),
            ).fetchall()
        return [_candidate(row) for row in rows]

    def decide(self, candidate_id: str, status: Literal["accepted", "rejected"]) -> MemoryCandidateRecord:
        with self.database.transaction() as connection:
            row = connection.execute("SELECT status FROM memory_candidates WHERE candidate_id = ?", (candidate_id,)).fetchone()
            if row is None:
                raise LookupError(candidate_id)
            if row[0] != "pending":
                raise ValueError("memory candidate has already been decided")
            connection.execute(
                "UPDATE memory_candidates SET status = ?, decided_at = ? WHERE candidate_id = ?",
                (status, now_iso(), candidate_id),
            )
        return self.get(candidate_id)


class InstructionRepository:
    def __init__(self, database: SessionDatabase) -> None:
        self.database = database

    def latest(self, project_id: str, path: str) -> ProjectInstructionRecord | None:
        with self.database.transaction() as connection:
            row = connection.execute(
                """SELECT * FROM project_instruction_snapshots
                   WHERE project_id = ? AND path = ? AND status = 'active'
                   ORDER BY scanned_at DESC LIMIT 1""",
                (project_id, path),
            ).fetchone()
        return _instruction(row) if row is not None else None

    def save(self, project_id: str, path: str, kind: str, sha256: str, content: dict[str, Any]) -> ProjectInstructionRecord:
        instruction_id = f"instruction-{uuid4().hex}"
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE project_instruction_snapshots SET status = 'superseded' WHERE project_id = ? AND path = ? AND status = 'active'",
                (project_id, path),
            )
            connection.execute(
                """INSERT INTO project_instruction_snapshots(
                    instruction_id, project_id, path, kind, sha256, content_json,
                    status, scanned_at, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, 'active', ?, '{}')""",
                (instruction_id, project_id, path, kind, sha256, _dump(content), now_iso()),
            )
        record = self.latest(project_id, path)
        if record is None:
            raise RuntimeError("instruction snapshot was not persisted")
        return record


class TurnCheckpointRepository:
    def __init__(self, database: SessionDatabase) -> None:
        self.database = database

    def latest(self, turn_id: str) -> TurnMemoryCheckpoint | None:
        with self.database.transaction() as connection:
            row = connection.execute(
                """SELECT * FROM turn_memory_checkpoints
                   WHERE turn_id = ? AND status = 'active'
                   ORDER BY step DESC, created_at DESC LIMIT 1""",
                (turn_id,),
            ).fetchone()
        return _checkpoint(row) if row is not None else None

    def replace(
        self,
        *,
        session_id: str,
        turn_id: str,
        attempt_id: str | None,
        step: int,
        content: dict[str, Any],
        covered_message_ids: tuple[str, ...],
        model: str | None,
        metadata: dict[str, Any] | None = None,
    ) -> TurnMemoryCheckpoint:
        checkpoint_id = f"checkpoint-{uuid4().hex}"
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE turn_memory_checkpoints SET status = 'superseded' WHERE turn_id = ? AND status = 'active'",
                (turn_id,),
            )
            connection.execute(
                """INSERT INTO turn_memory_checkpoints(
                    checkpoint_id, session_id, turn_id, attempt_id, step, content_json,
                    covered_message_ids_json, status, model, created_at, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?)""",
                (
                    checkpoint_id,
                    session_id,
                    turn_id,
                    attempt_id,
                    step,
                    _dump(content),
                    _dump(covered_message_ids),
                    model,
                    now_iso(),
                    _dump(metadata or {}),
                ),
            )
        checkpoint = self.latest(turn_id)
        if checkpoint is None:
            raise RuntimeError("turn memory checkpoint was not persisted")
        return checkpoint


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _memory(row: Any) -> ProjectMemoryRecord:
    return ProjectMemoryRecord(
        memory_id=row["memory_id"],
        project_id=row["project_id"],
        kind=row["kind"],
        canonical_key=row["canonical_key"],
        title=row["title"],
        content=json.loads(row["content_json"]),
        status=row["status"],
        confidence=float(row["confidence"]),
        importance=int(row["importance"]),
        branch_scope=row["branch_scope"],
        source_commit=row["source_commit"],
        last_verified_commit=row["last_verified_commit"],
        source_session_id=row["source_session_id"],
        source_turn_id=row["source_turn_id"],
        source_message_ids=tuple(json.loads(row["source_message_ids_json"])),
        source_artifact_ids=tuple(json.loads(row["source_artifact_ids_json"])),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        last_verified_at=row["last_verified_at"],
        expires_at=row["expires_at"],
        version=int(row["version"]),
        metadata=json.loads(row["metadata_json"]),
    )


def _candidate(row: Any) -> MemoryCandidateRecord:
    return MemoryCandidateRecord(
        candidate_id=row["candidate_id"],
        project_id=row["project_id"],
        session_id=row["session_id"],
        turn_id=row["turn_id"],
        kind=row["kind"],
        canonical_key=row["canonical_key"],
        content=json.loads(row["content_json"]),
        evidence=json.loads(row["evidence_json"]),
        confidence=float(row["confidence"]),
        status=row["status"],
        created_at=row["created_at"],
        decided_at=row["decided_at"],
        metadata=json.loads(row["metadata_json"]),
    )


def _instruction(row: Any) -> ProjectInstructionRecord:
    return ProjectInstructionRecord(
        instruction_id=row["instruction_id"],
        project_id=row["project_id"],
        path=row["path"],
        kind=row["kind"],
        sha256=row["sha256"],
        content=json.loads(row["content_json"]),
        status=row["status"],
        scanned_at=row["scanned_at"],
        metadata=json.loads(row["metadata_json"]),
    )


def _checkpoint(row: Any) -> TurnMemoryCheckpoint:
    return TurnMemoryCheckpoint(
        checkpoint_id=row["checkpoint_id"],
        session_id=row["session_id"],
        turn_id=row["turn_id"],
        attempt_id=row["attempt_id"],
        step=int(row["step"]),
        content=json.loads(row["content_json"]),
        covered_message_ids=tuple(json.loads(row["covered_message_ids_json"])),
        status=row["status"],
        model=row["model"],
        created_at=row["created_at"],
        metadata=json.loads(row["metadata_json"]),
    )
