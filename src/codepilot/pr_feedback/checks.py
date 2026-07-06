from __future__ import annotations

"""收集并统一 GitHub Checks / Status / Workflow 结果。"""

from collections import Counter
from typing import Any

from codepilot.pr_feedback.github_client import PRFeedbackGitHubClientProtocol, PRFeedbackGitHubError, redact_feedback_text
from codepilot.pr_feedback.models import CheckConclusion, CheckCorrelation, CheckRunSummary, PRRef


_PENDING_STATUSES = {"queued", "in_progress", "waiting", "requested"}
_ACTIONABLE_CONCLUSIONS = {"failure", "timed_out", "action_required", "cancelled"}


def _normalize_conclusion(status: str | None, conclusion: str | None) -> CheckConclusion:
    """把 GitHub 原始状态字段压缩成统一结论。"""

    if status in _PENDING_STATUSES:
        return "pending"
    if conclusion in {"success", "failure", "cancelled", "timed_out", "skipped", "neutral", "action_required"}:
        return conclusion  # type: ignore[return-value]
    if conclusion is None and status != "completed":
        return "pending"
    return "unknown"


def _truncate_text(value: str | None, *, limit: int = 600) -> str | None:
    """把 GitHub output / summary 压成适合放进 JSON 的短文本。"""

    if value is None:
        return None
    text = value.strip()
    if len(text) <= limit:
        return text
    return text[:limit] + "\n[truncated]"


def normalize_check_run(raw: dict[str, Any], *, pr: PRRef) -> CheckRunSummary:
    """把 check_run payload 转成统一摘要。"""

    output = raw.get("output") or {}
    return CheckRunSummary(
        name=str(raw.get("name") or raw.get("check_suite") or "check_run"),
        status=str(raw.get("status") or "unknown"),
        conclusion=_normalize_conclusion(str(raw.get("status") or ""), raw.get("conclusion")),
        html_url=raw.get("html_url"),
        workflow_run_id=raw.get("workflow_run_id"),
        job_id=raw.get("job_id"),
        started_at=raw.get("started_at"),
        completed_at=raw.get("completed_at"),
        output_title=_truncate_text(output.get("title")),
        output_summary=_truncate_text(output.get("summary")),
        head_sha=raw.get("head_sha") or pr.head_sha,
        correlation=CheckCorrelation(
            source="check_run",
            head_sha=raw.get("head_sha") or pr.head_sha,
            workflow_run_id=raw.get("workflow_run_id"),
            job_id=raw.get("job_id"),
            confidence="high" if raw.get("head_sha") == pr.head_sha else "medium",
            correlation_reason="check_run head_sha matches PR head_sha"
            if raw.get("head_sha") == pr.head_sha
            else "check_run head_sha is missing or different",
        ),
        is_required=raw.get("is_required"),
        required_unknown=raw.get("is_required") is None,
    )


def normalize_commit_status(raw: dict[str, Any], *, pr: PRRef) -> CheckRunSummary:
    """把 commit status payload 变成统一摘要。"""

    state = str(raw.get("state") or "unknown")
    conclusion = {"success": "success", "failure": "failure", "error": "failure", "pending": "pending"}.get(
        state,
        "unknown",
    )
    return CheckRunSummary(
        name=str(raw.get("context") or raw.get("description") or "commit_status"),
        status=state,
        conclusion=conclusion,  # type: ignore[arg-type]
        html_url=raw.get("target_url"),
        started_at=raw.get("created_at"),
        completed_at=raw.get("updated_at"),
        output_title=_truncate_text(raw.get("description")),
        output_summary=_truncate_text(raw.get("description")),
        head_sha=pr.head_sha,
        correlation=CheckCorrelation(
            source="commit_status",
            head_sha=pr.head_sha,
            confidence="high",
            correlation_reason="commit status is requested for the PR head sha",
        ),
        is_required=raw.get("required"),
        required_unknown=raw.get("required") is None,
    )


def normalize_workflow_run(raw: dict[str, Any], *, pr: PRRef) -> CheckRunSummary:
    """把 workflow run payload 变成统一摘要。"""

    status = str(raw.get("status") or "unknown")
    conclusion = _normalize_conclusion(status, raw.get("conclusion"))
    head_sha = raw.get("head_sha")
    confidence = "high" if head_sha == pr.head_sha else "medium" if raw.get("head_branch") == pr.head_branch else "low"
    return CheckRunSummary(
        name=str(raw.get("name") or raw.get("workflow_name") or "workflow_run"),
        status=status,
        conclusion=conclusion,
        html_url=raw.get("html_url"),
        workflow_run_id=raw.get("id"),
        started_at=raw.get("run_started_at") or raw.get("created_at"),
        completed_at=raw.get("updated_at") or raw.get("completed_at"),
        output_title=_truncate_text(raw.get("display_title")),
        output_summary=_truncate_text(raw.get("path")),
        head_sha=head_sha,
        correlation=CheckCorrelation(
            source="workflow_run",
            head_sha=head_sha,
            workflow_run_id=raw.get("id"),
            confidence=confidence,
            correlation_reason="workflow run head_sha matches PR head_sha"
            if head_sha == pr.head_sha
            else "workflow run matched by branch or PR context",
        ),
        is_required=None,
        required_unknown=True,
    )


def _normalize_workflow_job(raw: dict[str, Any], *, pr: PRRef, workflow_run_id: int) -> CheckRunSummary:
    """把 workflow job 结果压成可下载日志的摘要。"""

    status = str(raw.get("status") or "unknown")
    conclusion = _normalize_conclusion(status, raw.get("conclusion"))
    head_sha = raw.get("head_sha") or pr.head_sha
    confidence = "high" if head_sha == pr.head_sha else "medium"
    return CheckRunSummary(
        name=str(raw.get("name") or raw.get("workflow_name") or "workflow_job"),
        status=status,
        conclusion=conclusion,
        html_url=raw.get("html_url"),
        workflow_run_id=workflow_run_id,
        job_id=raw.get("id"),
        started_at=raw.get("started_at"),
        completed_at=raw.get("completed_at"),
        output_title=_truncate_text(raw.get("name")),
        output_summary=_truncate_text(raw.get("steps") and str(raw.get("steps")) or raw.get("conclusion")),
        head_sha=head_sha,
        correlation=CheckCorrelation(
            source="workflow_job",
            head_sha=head_sha,
            workflow_run_id=workflow_run_id,
            job_id=raw.get("id"),
            confidence=confidence,
            correlation_reason="workflow job belongs to a workflow run for the PR",
        ),
        is_required=None,
        required_unknown=True,
    )


def collect_pr_checks(*, client: PRFeedbackGitHubClientProtocol, pr: PRRef) -> list[CheckRunSummary]:
    """收集 checks / statuses / workflow run / workflow job 的统一摘要。"""

    checks = [normalize_check_run(raw, pr=pr) for raw in client.list_check_runs_for_ref(pr)]
    checks.extend(normalize_commit_status(raw, pr=pr) for raw in client.list_commit_statuses(pr))
    for raw in client.list_workflow_runs_for_pr(pr):
        workflow_run = normalize_workflow_run(raw, pr=pr)
        checks.append(workflow_run)
        run_id = raw.get("id")
        if isinstance(run_id, int):
            checks.extend(
                _normalize_workflow_job(job, pr=pr, workflow_run_id=run_id)
                for job in client.list_workflow_jobs(pr.owner, pr.repo, run_id)
            )
    return checks


def collect_pr_checks_degraded(*, client: PRFeedbackGitHubClientProtocol, pr: PRRef) -> tuple[list[CheckRunSummary], list[str], list[str]]:
    """按固定顺序收集 checks，并允许局部 API 失败降级。"""

    warnings: list[str] = []
    degraded_sources: list[str] = []
    checks: list[CheckRunSummary] = []
    try:
        checks.extend(normalize_check_run(raw, pr=pr) for raw in client.list_check_runs_for_ref(pr))
    except PRFeedbackGitHubError as exc:
        warnings.append(redact_feedback_text(str(exc)))
        degraded_sources.append("checks")
    try:
        checks.extend(normalize_commit_status(raw, pr=pr) for raw in client.list_commit_statuses(pr))
    except PRFeedbackGitHubError as exc:
        warnings.append(redact_feedback_text(str(exc)))
        degraded_sources.append("checks")
    try:
        workflow_runs = client.list_workflow_runs_for_pr(pr)
    except PRFeedbackGitHubError as exc:
        warnings.append(redact_feedback_text(str(exc)))
        degraded_sources.append("checks")
        return checks, warnings, degraded_sources
    for raw in workflow_runs:
        workflow_run = normalize_workflow_run(raw, pr=pr)
        checks.append(workflow_run)
        run_id = raw.get("id")
        if not isinstance(run_id, int):
            continue
        try:
            jobs = client.list_workflow_jobs(pr.owner, pr.repo, run_id)
        except PRFeedbackGitHubError as exc:
            warnings.append(redact_feedback_text(str(exc)))
            degraded_sources.append("checks")
            continue
        checks.extend(_normalize_workflow_job(job, pr=pr, workflow_run_id=run_id) for job in jobs)
    return checks, warnings, degraded_sources


def summarize_check_state(checks: list[CheckRunSummary]) -> dict[str, int]:
    """把 checks 压缩成简单计数，供 report / manifest 使用。"""

    counter = Counter(check.conclusion for check in checks)
    return {
        "success": counter.get("success", 0),
        "failure": counter.get("failure", 0),
        "pending": counter.get("pending", 0),
    }


def check_is_actionable_failure(check: CheckRunSummary) -> bool:
    """判断一个 check 是否属于需要人工关注的失败项。"""

    return check.conclusion in _ACTIONABLE_CONCLUSIONS


def has_blocking_ci_failure(checks: list[CheckRunSummary]) -> bool:
    """判断是否存在会阻止继续 follow-up 的 CI 失败。"""

    return any(check_is_actionable_failure(check) for check in checks)


def has_pending_checks(checks: list[CheckRunSummary]) -> bool:
    """判断是否还有未完成的 CI 结果。"""

    return any(check.conclusion == "pending" for check in checks)
