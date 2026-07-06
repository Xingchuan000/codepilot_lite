from __future__ import annotations

"""第十五步 Post-PR automation 的核心数据模型。"""

import re
from dataclasses import asdict, dataclass, field, is_dataclass
from pathlib import Path
from typing import Any, Literal


PostPRAutomationStatus = Literal[
    "planned",
    "awaiting_approval",
    "feedback_found",
    "agent_ran",
    "patch_ready",
    "branch_updated",
    "no_feedback",
    "max_rounds_reached",
    "repeated_feedback",
    "blocked",
    "failed",
]

PostPRTerminalReason = Literal[
    "none",
    "no_feedback",
    "awaiting_approval",
    "approval_rejected",
    "approval_expired",
    "stale_approval",
    "max_rounds_reached",
    "repeated_feedback",
    "stale_head",
    "pending_checks",
    "ci_timeout",
    "checks_cancelled",
    "checks_skipped",
    "checks_unavailable",
    "unsafe_branch",
    "agent_failed",
    "push_failed",
    "comment_failed",
    "api_degraded",
    "manifest_invalid",
    "state_locked",
]

ApprovalAction = Literal["run_agent", "push_update", "post_comment"]
ApprovalDecisionStatus = Literal["pending", "approved", "rejected"]
PostPRRoundPhase = Literal["collect", "execute"]
SideEffectAction = Literal["run_agent", "commit", "push_update", "post_comment"]
SideEffectStatus = Literal["planned", "succeeded", "failed", "skipped"]


class PostPRError(RuntimeError):
    """Post-PR automation 的统一异常基类。"""


class PostPRStateLockedError(PostPRError):
    """state.lock 已被别的进程占用。"""


_TOKEN_KEY_RE = re.compile(r"(token|secret|password|authorization)", re.IGNORECASE)
_TOKEN_VALUE_RE = re.compile(r"ghp_[A-Za-z0-9_]+|github_pat_[A-Za-z0-9_]+|Bearer\s+[A-Za-z0-9._-]+|sk-[A-Za-z0-9_-]+")


@dataclass(frozen=True)
class PostPRAutomationInput:
    run_id: str
    run_dir: Path
    auto_pr_manifest_path: Path
    dry_run: bool = True
    execute: bool = False
    max_rounds: int = 2
    wait_ci: bool = False
    poll_interval_seconds: int = 30
    timeout_seconds: int = 900
    token_env: str = "GITHUB_TOKEN"
    include_logs: bool = True
    include_success_logs: bool = False
    max_log_bytes: int = 200_000
    max_feedback_items: int = 20
    stop_on_repeated_feedback: bool = True
    approve_run_agent: bool = False
    approve_push_update: bool = False
    approve_comment: bool = False
    approval_file: Path | None = None
    resume: bool = False
    overwrite: bool = False


@dataclass(frozen=True)
class ArtifactSnapshotEntry:
    name: str
    source_path: str
    snapshot_path: str
    exists: bool
    size_bytes: int | None = None
    sha256: str | None = None
    phase: PostPRRoundPhase = "collect"


@dataclass(frozen=True)
class PostPRRoundRef:
    round_id: str
    round_index: int
    round_dir: Path
    collect_manifest_path: Path | None = None
    execute_manifest_path: Path | None = None
    latest_pr_feedback_manifest_path: Path | None = None
    feedback_fingerprints: list[str] = field(default_factory=list)
    status: str = "planned"
    terminal_reason: str = "none"
    head_sha_before: str | None = None
    head_sha_after: str | None = None
    agent_ran: bool = False
    patch_generated: bool = False
    commit_created: bool = False
    new_commit_sha: str | None = None
    push_update_executed: bool = False
    comment_posted: bool = False
    blockers: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class FeedbackDelta:
    current_fingerprints: list[str]
    previous_fingerprints: list[str]
    new_fingerprints: list[str]
    repeated_fingerprints: list[str]
    resolved_fingerprints: list[str]
    is_repeated_failure: bool = False
    progressed: bool = False
    regressed: bool = False


@dataclass(frozen=True)
class ApprovalRequest:
    run_id: str
    round_id: str
    requested_actions: list[ApprovalAction]
    reason: str
    pr_url: str | None = None
    head_branch: str | None = None
    head_sha: str | None = None
    auto_pr_manifest_sha256: str | None = None
    pr_feedback_manifest_sha256: str | None = None
    approval_request_sha256: str | None = None
    expires_at: str | None = None
    feedback_manifest_path: Path | None = None
    followup_task_path: Path | None = None
    pr_update_plan_path: Path | None = None
    created_at: str | None = None


@dataclass(frozen=True)
class ApprovalDecision:
    run_id: str
    round_id: str
    status: ApprovalDecisionStatus
    approved_actions: list[ApprovalAction] = field(default_factory=list)
    reason: str | None = None
    decided_at: str | None = None
    approver: str | None = None
    head_sha: str | None = None
    auto_pr_manifest_sha256: str | None = None
    pr_feedback_manifest_sha256: str | None = None
    approval_request_sha256: str | None = None
    expires_at: str | None = None


@dataclass(frozen=True)
class SideEffectEntry:
    round_id: str
    action: SideEffectAction
    status: SideEffectStatus
    approval_decision_sha256: str | None = None
    head_sha_before: str | None = None
    head_sha_after: str | None = None
    commit_sha: str | None = None
    remote_ref: str | None = None
    comment_marker: str | None = None
    executed_at: str | None = None
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PostPRAutomationResult:
    run_id: str
    run_dir: Path
    post_pr_dir: Path
    status: PostPRAutomationStatus
    terminal_reason: PostPRTerminalReason = "none"
    rounds: list[PostPRRoundRef] = field(default_factory=list)
    latest_round_id: str | None = None
    approval_request_path: Path | None = None
    approval_decision_path: Path | None = None
    side_effects_path: Path | None = None
    state_path: Path | None = None
    manifest_path: Path | None = None
    report_path: Path | None = None
    workflow_path: Path | None = None
    warnings: list[str] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)


def validate_max_rounds(max_rounds: int) -> None:
    """严格限制 v1 只允许 1..3 轮。"""

    if max_rounds < 1 or max_rounds > 3:
        raise ValueError("max_rounds must be between 1 and 3")


def _redact_text(value: str) -> str:
    """把 token-like 串替换成安全占位符，同时去掉 NUL 字符。"""

    return _TOKEN_VALUE_RE.sub("[REDACTED]", value).replace("\x00", "")


def to_post_pr_jsonable(value: Any) -> Any:
    """把 Path / dataclass 递归转成可安全写入 JSON 的结构。"""

    if isinstance(value, Path):
        return str(value)
    if isinstance(value, str):
        return _redact_text(value)
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if _TOKEN_KEY_RE.search(str(key)):
                raise ValueError(f"token-like field is not allowed in post_pr jsonable payload: {key}")
            result[key] = to_post_pr_jsonable(item)
        return result
    if is_dataclass(value):
        return {key: to_post_pr_jsonable(item) for key, item in asdict(value).items()}
    if isinstance(value, (list, tuple)):
        return [to_post_pr_jsonable(item) for item in value]
    return value

