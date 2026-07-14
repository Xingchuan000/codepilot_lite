from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
from pathlib import Path
from typing import Any, Literal

from codepilot.agent.outcome import RunOutcomeSnapshot
from codepilot.permissions import PermissionRequest


PermissionMode = Literal["manual", "read_only", "accept_edits", "unsafe_auto"]
RunStatus = Literal[
    "idle",
    "running",
    "waiting_branch_confirmation",
    "waiting_permission",
    "message_complete",
    "success",
    "partial",
    "failed",
    "task_incomplete",
    "cancelled",
    "interrupted",
    "max_steps_exceeded",
    "llm_error",
    "llm_exhausted",
    "unknown",
]

TranscriptItemKind = Literal[
    "user_message",
    "assistant_raw",
    "assistant_plan",
    "assistant_action",
    "tool_call",
    "tool_result",
    "observation",
    "permission_request",
    "permission_response",
    "final_summary",
    "command_output",
    "system_status",
    "error",
]

TUIEventType = Literal[
    "session_started",
    "run_started",
    "trace_event",
    "llm_call_started",
    "llm_call_finished",
    "agent_action",
    "agent_observation",
    "agent_finished",
    "tool_started",
    "tool_finished",
    "policy_decision",
    "permission_requested",
    "permission_resolved",
    "branch_confirmation_required",
    "test_status_changed",
    "file_changed",
    "run_finished",
    "run_cancelled",
    "command_output",
    "user_message",
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
    completion_kind: str | None = None
    assistant_stop_reason: str | None = None
    delivery_kind: str | None = None
    requires_evidence: bool | None = None
    evidence_reasons: tuple[str, ...] = ()
    write_attempted: bool | None = None
    write_executed: bool | None = None
    written_files: tuple[str, ...] = ()
    changed_files: tuple[str, ...] = ()
    tests_required: bool | None = None
    diff_required: bool | None = None
    diff_checked: bool | None = None
    missing_evidence: tuple[str, ...] = ()
    tests: str | None = None

    @classmethod
    def from_outcome(
        cls,
        *,
        run_id: str,
        task_preview: str,
        outcome: RunOutcomeSnapshot,
        trace_path: str | None,
        report_path: str | None,
        report_json_path: str | None,
        started_at: str,
        ended_at: str,
    ) -> "TUISessionRunRef":
        """从统一运行结果构造 Session 索引项。

        Session v1 的字段名和层级保持不变；这里只负责将不可变 Outcome 映射到已有
        持久化模型，避免 Runner 再逐项复制 Evidence 字段。
        """

        evidence = outcome.evidence
        return cls(
            run_id=run_id,
            task_preview=task_preview,
            status=outcome.status,
            trace_path=trace_path,
            report_path=report_path,
            report_json_path=report_json_path,
            started_at=started_at,
            ended_at=ended_at,
            completion_kind=outcome.completion_kind,
            assistant_stop_reason=outcome.assistant_stop_reason,
            delivery_kind=outcome.delivery_kind,
            requires_evidence=evidence.requires_evidence,
            evidence_reasons=evidence.reasons,
            write_attempted=evidence.write_attempted,
            write_executed=evidence.write_executed,
            written_files=evidence.written_files,
            changed_files=outcome.changed_files,
            tests_required=evidence.tests_required,
            diff_required=evidence.diff_required,
            diff_checked=evidence.diff_checked,
            missing_evidence=evidence.missing,
            tests=outcome.last_test_status,
        )


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
class TranscriptItem:
    id: str
    kind: TranscriptItemKind
    timestamp: str
    run_id: str | None = None
    step: int | None = None
    title: str = ""
    body: str = ""
    tool_name: str | None = None
    status: str | None = None
    input_preview: dict[str, Any] | None = None
    output_preview: str | None = None
    copy_text: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AgentRunView:
    run_id: str | None = None
    task: str = ""
    status: RunStatus = "idle"
    current_step: int | None = None
    current_tool: str | None = None
    active_tool: str | None = None
    last_assistant_message: str | None = None
    last_tool_output: str | None = None
    completion_kind: str | None = None
    assistant_stop_reason: str | None = None
    delivery_kind: str | None = None
    requires_evidence: bool | None = None
    evidence_reasons: tuple[str, ...] = ()
    write_attempted: bool | None = None
    write_executed: bool | None = None
    written_files: tuple[str, ...] = ()
    observed_changed_files: tuple[str, ...] = ()
    claimed_changed_files: tuple[str, ...] = ()
    transcript: tuple[TranscriptItem, ...] = ()
    timeline: tuple[TimelineItem, ...] = ()
    changed_files: tuple[str, ...] = ()
    test_status: str | None = None
    tests_required: bool | None = None
    diff_required: bool | None = None
    diff_checked: bool | None = None
    missing_evidence: tuple[str, ...] = ()
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
