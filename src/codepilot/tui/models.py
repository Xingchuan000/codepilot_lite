from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
from pathlib import Path
from typing import Any, Literal

from codepilot.tui import DASHBOARD_SCHEMA_VERSION

RunStatus = Literal[
    "message_complete",
    "success",
    "failed",
    "task_incomplete",
    "running",
    "waiting_permission",
    "unknown",
    "partial",
    "max_steps_exceeded",
    "cancelled",
    "interrupted",
    "llm_error",
    "llm_exhausted",
    "repo_safety_denied",
]


@dataclass(frozen=True)
class RunArtifactRef:
    kind: str
    path: Path
    exists: bool
    size_bytes: int = 0
    sha256: str | None = None
    source: str | None = None
    verified: bool | None = None
    warnings: tuple[str, ...] = ()

    def to_json_dict(self) -> dict[str, Any]:
        return to_jsonable(self)


@dataclass(frozen=True)
class RunIndexEntry:
    schema_version: str = DASHBOARD_SCHEMA_VERSION
    run_id: str = ""
    run_dir: Path = Path("")
    run_type: str = "unknown"
    source_workflow: str | None = None
    status: str = "unknown"
    task: str = ""
    started_at: str | None = None
    ended_at: str | None = None
    updated_at: str | None = None
    tool_call_count: int = 0
    policy_decision_count: int = 0
    policy_denied_count: int = 0
    approval_required_count: int = 0
    unexecuted_action_count: int = 0
    test_status: str | None = None
    changed_files: tuple[str, ...] = ()
    has_mcp: bool = False
    has_issue_artifacts: bool = False
    has_pr_artifacts: bool = False
    artifacts: tuple[RunArtifactRef, ...] = ()
    source_provenance: dict[str, str] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()

    def to_json_dict(self) -> dict[str, Any]:
        return to_jsonable(self)


@dataclass(frozen=True)
class TimelineRow:
    step: int | None
    event_type: str
    title: str
    status: str | None = None
    category: str = "event"
    tool_name: str | None = None
    policy_decision: str | None = None
    executed: bool | None = None
    risk: str | None = None
    output_summary: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_json_dict(self) -> dict[str, Any]:
        return to_jsonable(self)


@dataclass(frozen=True)
class RunDashboardModel:
    schema_version: str
    entry: RunIndexEntry
    timeline: tuple[TimelineRow, ...] = ()
    policy_summary: dict[str, int] = field(default_factory=dict)
    tool_summary: dict[str, int] = field(default_factory=dict)
    mcp_summary: dict[str, Any] = field(default_factory=dict)
    test_summary: dict[str, Any] = field(default_factory=dict)
    diff_summary: dict[str, Any] = field(default_factory=dict)
    workflow_summary: dict[str, Any] = field(default_factory=dict)
    artifact_summary: tuple[RunArtifactRef, ...] = ()
    source_provenance: dict[str, str] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()

    def to_json_dict(self) -> dict[str, Any]:
        return to_jsonable(self)


def to_jsonable(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if is_dataclass(value):
        return {key: to_jsonable(item) for key, item in asdict(value).items()}
    if isinstance(value, dict):
        return {str(key): to_jsonable(item) for key, item in value.items()}
    if isinstance(value, tuple | list):
        return [to_jsonable(item) for item in value]
    if isinstance(value, set):
        return sorted(to_jsonable(item) for item in value)
    return value
