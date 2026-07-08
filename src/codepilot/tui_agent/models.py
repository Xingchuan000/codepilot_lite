from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
from pathlib import Path
from typing import Any, Literal


PermissionMode = Literal["manual", "read_only", "accept_edits", "unsafe_auto"]
RunStatus = Literal[
    "idle",
    "running",
    "waiting_permission",
    "success",
    "failed",
    "cancelled",
    "interrupted",
    "max_steps_exceeded",
    "llm_error",
    "llm_exhausted",
    "unknown",
]

TUIEventType = Literal[
    "session_started",
    "run_started",
    "trace_event",
    "llm_call_started",
    "llm_call_finished",
    "agent_action",
    "tool_started",
    "tool_finished",
    "policy_decision",
    "permission_requested",
    "permission_resolved",
    "test_status_changed",
    "file_changed",
    "run_finished",
    "run_cancelled",
    "error",
]


@dataclass(frozen=True)
class ProjectContext:
    schema_version: str
    project_path: Path
    resolved_project: Path
    git_root: Path | None
    is_git_repo: bool
    git_dirty_status: str
    workspace_root: Path
    effective_repo_path: Path
    default_runs_dir: Path
    project_config_path: Path | None = None
    mcp_config_path: Path | None = None
    instructions_files: tuple[Path, ...] = ()
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class TUISessionRunRef:
    run_id: str
    task_preview: str
    status: str
    trace_path: str | None = None
    report_path: str | None = None
    report_json_path: str | None = None
    started_at: str | None = None
    ended_at: str | None = None
    changed_files: tuple[str, ...] = ()
    tests: str | None = None


@dataclass(frozen=True)
class TUISession:
    schema_version: str
    session_id: str
    project_path: Path
    git_root: Path | None
    workspace_root: Path
    created_at: str
    updated_at: str
    title: str
    model: str | None
    permission_mode: PermissionMode
    runs_dir: Path
    session_dir: Path
    messages_path: Path
    runs_index_path: Path
    runs: tuple[TUISessionRunRef, ...] = ()
    last_run_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PermissionRequest:
    request_id: str
    run_id: str
    action_id: str | None
    tool_name: str
    arguments_preview: dict[str, Any]
    reason: str
    risk: str | None
    side_effect: str | None
    matched_rule: str | None
    created_at: str
    status: Literal["pending", "approved", "denied", "expired"] = "pending"


@dataclass(frozen=True)
class PermissionResponse:
    request_id: str
    decision: Literal["approve_once", "deny"]
    reason: str | None
    responded_at: str


@dataclass(frozen=True)
class TUIEvent:
    type: TUIEventType
    timestamp: str
    run_id: str | None = None
    session_id: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TimelineItem:
    step: int | None
    title: str
    category: str
    status: str | None = None
    tool_name: str | None = None
    policy_decision: str | None = None
    executed: bool | None = None
    output_summary: str | None = None


@dataclass(frozen=True)
class AgentRunView:
    run_id: str | None = None
    task: str = ""
    status: RunStatus = "idle"
    current_step: int | None = None
    current_tool: str | None = None
    timeline: tuple[TimelineItem, ...] = ()
    changed_files: tuple[str, ...] = ()
    test_status: str | None = None
    permission_requests: tuple[PermissionRequest, ...] = ()
    report_path: str | None = None
    report_json_path: str | None = None
    trace_path: str | None = None
    warnings: tuple[str, ...] = ()


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
