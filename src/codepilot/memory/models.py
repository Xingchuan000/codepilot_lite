from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

MemoryKind = Literal["architecture", "convention", "command", "decision", "file_map", "known_issue", "project_preference"]
MemoryStatus = Literal["active", "stale", "superseded", "forgotten"]
CandidateStatus = Literal["pending", "accepted", "rejected", "superseded"]


@dataclass(frozen=True)
class ProjectMemoryRecord:
    memory_id: str
    project_id: str
    kind: MemoryKind
    canonical_key: str
    title: str
    content: dict[str, Any]
    status: MemoryStatus
    confidence: float
    importance: int
    branch_scope: str | None
    source_commit: str | None
    last_verified_commit: str | None
    source_session_id: str | None
    source_turn_id: str | None
    source_message_ids: tuple[str, ...]
    source_artifact_ids: tuple[str, ...]
    created_at: str
    updated_at: str
    last_verified_at: str | None
    expires_at: str | None
    version: int
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class MemoryCandidateRecord:
    candidate_id: str
    project_id: str
    session_id: str
    turn_id: str
    kind: MemoryKind
    canonical_key: str
    content: dict[str, Any]
    evidence: dict[str, Any]
    confidence: float
    status: CandidateStatus
    created_at: str
    decided_at: str | None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ProjectInstructionRecord:
    instruction_id: str
    project_id: str
    path: str
    kind: Literal["instruction", "reference"]
    sha256: str
    content: dict[str, Any]
    status: str
    scanned_at: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class MemoryQuery:
    text: str
    kinds: tuple[MemoryKind, ...] = ()
    paths: tuple[str, ...] = ()
    branch: str | None = None
    limit: int = 8


@dataclass(frozen=True)
class MemorySearchResult:
    memory: ProjectMemoryRecord
    score: float


@dataclass(frozen=True)
class SessionMemorySnapshot:
    session_id: str
    memory_ids: tuple[str, ...]
    created_at: str


@dataclass(frozen=True)
class TurnMemoryCheckpoint:
    checkpoint_id: str
    session_id: str
    turn_id: str
    attempt_id: str | None
    step: int
    content: dict[str, Any]
    covered_message_ids: tuple[str, ...]
    status: str
    model: str | None
    created_at: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SessionSummaryContent:
    task_goal: str = ""
    user_constraints: tuple[str, ...] = ()
    confirmed_decisions: tuple[str, ...] = ()
    repository_facts: tuple[str, ...] = ()
    files_read: tuple[str, ...] = ()
    files_modified: tuple[str, ...] = ()
    commands_run: tuple[str, ...] = ()
    test_results: tuple[str, ...] = ()
    diff_status: dict[str, Any] = field(default_factory=dict)
    errors_and_failures: tuple[str, ...] = ()
    unresolved_work: tuple[str, ...] = ()
    next_actions: tuple[str, ...] = ()
    branch: str = ""
    commit: str = ""
    source_message_ids: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> SessionSummaryContent:
        required = {
            "task_goal",
            "user_constraints",
            "confirmed_decisions",
            "repository_facts",
            "files_read",
            "files_modified",
            "commands_run",
            "test_results",
            "diff_status",
            "errors_and_failures",
            "unresolved_work",
            "next_actions",
            "branch",
            "commit",
            "source_message_ids",
        }
        if missing := required - value.keys():
            raise ValueError(f"session summary is missing fields: {sorted(missing)}")
        return cls(
            task_goal=str(value["task_goal"]),
            user_constraints=_strings(value["user_constraints"]),
            confirmed_decisions=_strings(value["confirmed_decisions"]),
            repository_facts=_strings(value["repository_facts"]),
            files_read=_strings(value["files_read"]),
            files_modified=_strings(value["files_modified"]),
            commands_run=_strings(value["commands_run"]),
            test_results=_strings(value["test_results"]),
            diff_status=dict(value["diff_status"]),
            errors_and_failures=_strings(value["errors_and_failures"]),
            unresolved_work=_strings(value["unresolved_work"]),
            next_actions=_strings(value["next_actions"]),
            branch=str(value["branch"]),
            commit=str(value["commit"]),
            source_message_ids=_strings(value["source_message_ids"]),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_goal": self.task_goal,
            "user_constraints": list(self.user_constraints),
            "confirmed_decisions": list(self.confirmed_decisions),
            "repository_facts": list(self.repository_facts),
            "files_read": list(self.files_read),
            "files_modified": list(self.files_modified),
            "commands_run": list(self.commands_run),
            "test_results": list(self.test_results),
            "diff_status": self.diff_status,
            "errors_and_failures": list(self.errors_and_failures),
            "unresolved_work": list(self.unresolved_work),
            "next_actions": list(self.next_actions),
            "branch": self.branch,
            "commit": self.commit,
            "source_message_ids": list(self.source_message_ids),
        }


def _strings(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ValueError("session summary list field has invalid type")
    return tuple(str(item) for item in value)
