from __future__ import annotations

"""第十四步 PR feedback / review loop 的核心数据模型。"""

from dataclasses import asdict, dataclass, field, is_dataclass
from pathlib import Path
from typing import Any, Literal
import re


PRFeedbackStatus = Literal[
    "planned",
    "no_feedback",
    "feedback_found",
    "partial_feedback",
    "api_degraded",
    "feedback_unavailable",
    "blocked",
    "followup_task_generated",
    "agent_ran",
    "patch_generated",
    "commit_created",
    "branch_updated",
    "comment_posted",
    "failed",
]

FeedbackKind = Literal[
    "ci_failure",
    "test_failure",
    "lint_failure",
    "typecheck_failure",
    "build_failure",
    "review_change_request",
    "review_comment",
    "security_warning",
    "pending_check",
    "unknown",
]

FeedbackSeverity = Literal["info", "warning", "error", "blocking"]
CheckConclusion = Literal[
    "success",
    "failure",
    "cancelled",
    "timed_out",
    "skipped",
    "neutral",
    "action_required",
    "pending",
    "unknown",
]
CorrelationConfidence = Literal["high", "medium", "low"]
CheckSource = Literal["check_run", "commit_status", "workflow_run", "workflow_job"]


class PRFeedbackError(RuntimeError):
    """PR feedback / review loop 的统一异常基类。"""


class PRFeedbackManifestInvalidError(PRFeedbackError):
    """auto_pr_manifest.json 或其依赖产物不合法。"""


class PRFeedbackGitHubError(PRFeedbackError):
    """GitHub API 调用失败。"""


class PRFeedbackSafetyError(PRFeedbackError):
    """安全门不允许继续执行。"""


class PRFeedbackWorkflowInputError(PRFeedbackError):
    """CLI 或 workflow 输入参数不合法。"""


class PRFeedbackStaleHeadError(PRFeedbackSafetyError):
    """PR head sha 或 head branch 已变化，当前输入已过期。"""


@dataclass(frozen=True)
class PRFeedbackInput:
    """描述一次 PR feedback workflow 的最小输入。"""

    run_id: str
    run_dir: Path
    auto_pr_manifest_path: Path
    dry_run: bool = True
    execute: bool = False
    wait_ci: bool = False
    include_logs: bool = True
    include_success_logs: bool = False
    allow_run_agent: bool = False
    allow_push_update: bool = False
    allow_comment: bool = False
    max_feedback_items: int = 20
    max_log_bytes: int = 200_000
    max_followup_rounds: int = 1
    poll_interval_seconds: int = 30
    timeout_seconds: int = 900
    token_env: str = "GITHUB_TOKEN"
    repo_slug: str | None = None
    pull_number: int | None = None
    head_branch: str | None = None
    overwrite: bool = False


@dataclass(frozen=True)
class PRRef:
    """描述目标 PR 的最小引用信息。"""

    owner: str
    repo: str
    pull_number: int
    url: str
    head_branch: str
    base_branch: str
    head_sha: str | None = None
    base_sha: str | None = None


@dataclass(frozen=True)
class FeedbackFreshness:
    """记录观察到的 PR head 是否过期。"""

    observed_head_sha: str | None
    current_head_sha: str | None
    observed_at: str | None
    is_stale: bool = False
    stale_reason: str | None = None


@dataclass(frozen=True)
class CheckCorrelation:
    """把 check / status / workflow 结果关联回 PR head。"""

    source: CheckSource
    head_sha: str | None
    workflow_run_id: int | None = None
    job_id: int | None = None
    confidence: CorrelationConfidence = "high"
    correlation_reason: str | None = None


@dataclass(frozen=True)
class CheckRunSummary:
    """统一描述 GitHub Checks / Status / Workflow 的摘要信息。"""

    name: str
    status: str
    conclusion: CheckConclusion
    html_url: str | None = None
    workflow_run_id: int | None = None
    job_id: int | None = None
    started_at: str | None = None
    completed_at: str | None = None
    output_title: str | None = None
    output_summary: str | None = None
    head_sha: str | None = None
    correlation: CheckCorrelation | None = None
    is_required: bool | None = None
    required_unknown: bool = False


@dataclass(frozen=True)
class CILogSummary:
    """保存单个 CI job 日志的摘要与证据路径。"""

    workflow_run_id: int | None
    job_id: int | None
    name: str
    path: Path | None = None
    summary: str = ""
    truncated: bool = False
    redacted: bool = False
    bytes_read: int = 0
    evidence_path: Path | None = None


@dataclass(frozen=True)
class ReviewCommentSummary:
    """保存 review / inline comment 的安全摘要。"""

    author: str | None
    body: str
    path: str | None = None
    line: int | None = None
    side: str | None = None
    url: str | None = None
    state: str | None = None
    original_line: int | None = None
    commit_id: str | None = None
    original_commit_id: str | None = None
    outdated: bool | None = None
    resolved: bool | None = None
    resolution_unknown: bool = False


@dataclass(frozen=True)
class FeedbackItem:
    """把 checks / logs / reviews 统一归一后的最终反馈项。"""

    kind: FeedbackKind
    severity: FeedbackSeverity
    title: str
    summary: str
    source: str
    file_path: str | None = None
    line: int | None = None
    check_name: str | None = None
    url: str | None = None
    raw_excerpt: str | None = None
    fingerprint: str | None = None
    observed_at: str | None = None
    head_sha: str | None = None
    workflow_run_id: int | None = None
    job_id: int | None = None
    evidence_path: Path | None = None
    redacted: bool = False
    truncated: bool = False
    stale: bool = False
    confidence: CorrelationConfidence = "high"


@dataclass(frozen=True)
class FollowupAttemptRef:
    """记录一次 follow-up agent run 的 attempt 目录。"""

    attempt_id: str
    attempt_index: int
    parent_run_id: str
    attempt_dir: Path
    source_feedback_manifest_path: Path
    followup_task_path: Path
    agent_ran: bool = False
    patch_generated: bool = False
    commit_created: bool = False
    push_update_executed: bool = False


@dataclass(frozen=True)
class PRFeedbackResult:
    """workflow 的最终返回对象。"""

    run_id: str
    run_dir: Path
    status: PRFeedbackStatus
    dry_run: bool = True
    execute: bool = False
    allow_run_agent_input: bool = False
    allow_push_update_input: bool = False
    allow_comment_input: bool = False
    feedback_sources_degraded: list[str] = field(default_factory=list)
    pr: PRRef | None = None
    feedback_freshness: FeedbackFreshness | None = None
    checks: list[CheckRunSummary] = field(default_factory=list)
    log_summaries: list[CILogSummary] = field(default_factory=list)
    review_comments: list[ReviewCommentSummary] = field(default_factory=list)
    feedback_items: list[FeedbackItem] = field(default_factory=list)
    ci_status_path: Path | None = None
    review_feedback_path: Path | None = None
    ci_feedback_report_path: Path | None = None
    ci_feedback_manifest_path: Path | None = None
    followup_task_path: Path | None = None
    pr_update_plan_path: Path | None = None
    feedback_workflow_path: Path | None = None
    followup_attempt: FollowupAttemptRef | None = None
    agent_ran: bool = False
    patch_generated: bool = False
    commit_created: bool = False
    new_commit_sha: str | None = None
    push_update_executed: bool = False
    comment_posted: bool = False
    github_api_called: bool = False
    remote_head_checked: bool = False
    execute_blocked_by_stale_head: bool = False
    api_degraded: bool = False
    warnings: list[str] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)


_TOKEN_KEY_RE = re.compile(r"(token|secret|password|authorization)", re.IGNORECASE)
_TOKEN_VALUE_RE = re.compile(
    r"ghp_[A-Za-z0-9_]+|github_pat_[A-Za-z0-9_]+|Bearer\s+[A-Za-z0-9._-]+|sk-[A-Za-z0-9_-]+"
)


def _redact_sensitive_text(value: str) -> str:
    """把明显的 token-like 字符串替换成占位符。"""

    redacted = _TOKEN_VALUE_RE.sub("[REDACTED]", value)
    return redacted.replace("\x00", "")


def to_pr_feedback_jsonable(value: Any) -> Any:
    """把 Path / dataclass 递归转换为可安全写入 JSON 的结构。"""

    if isinstance(value, Path):
        return str(value)
    if isinstance(value, str):
        return _redact_sensitive_text(value)
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if _TOKEN_KEY_RE.search(str(key)):
                raise ValueError(f"token-like field is not allowed in pr_feedback jsonable payload: {key}")
            result[key] = to_pr_feedback_jsonable(item)
        return result
    if is_dataclass(value):
        return {key: to_pr_feedback_jsonable(item) for key, item in asdict(value).items()}
    if isinstance(value, (list, tuple)):
        return [to_pr_feedback_jsonable(item) for item in value]
    return value
