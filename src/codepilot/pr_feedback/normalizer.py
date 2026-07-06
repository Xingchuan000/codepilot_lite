from __future__ import annotations

"""把 checks / logs / reviews 归一化成最终反馈项。"""

from hashlib import sha256
import re

from codepilot.pr_feedback.github_client import redact_feedback_text
from codepilot.pr_feedback.logs import redact_ci_log
from codepilot.pr_feedback.models import (
    CILogSummary,
    CheckRunSummary,
    FeedbackItem,
    FeedbackKind,
    FeedbackSeverity,
    ReviewCommentSummary,
)
from codepilot.pr_feedback.reviews import redact_review_body


_SEVERITY_RANK = {"blocking": 3, "error": 2, "warning": 1, "info": 0}
_TEXT_PATTERNS: list[tuple[re.Pattern[str], FeedbackKind]] = [
    (re.compile(r"\b(pytest|failed tests?|tests? failed)\b", re.IGNORECASE), "test_failure"),
    (re.compile(r"\b(ruff|flake8|eslint)\b", re.IGNORECASE), "lint_failure"),
    (re.compile(r"\b(mypy|pyright|tsc)\b", re.IGNORECASE), "typecheck_failure"),
    (re.compile(r"\b(build|npm|webpack)\b", re.IGNORECASE), "build_failure"),
]
_SECURITY_PATTERNS = [re.compile(r"ghp_|github_pat_|Bearer\s+|sk-", re.IGNORECASE)]


def _compress_text(value: str) -> str:
    """标准化文本，便于生成稳定 fingerprint。"""

    return re.sub(r"\s+", " ", value.strip().lower())


def _classify_text(value: str, *, fallback: FeedbackKind = "ci_failure") -> FeedbackKind:
    """根据文本里出现的关键词粗略判断反馈类型。"""

    for pattern, kind in _TEXT_PATTERNS:
        if pattern.search(value):
            return kind
    if any(pattern.search(value) for pattern in _SECURITY_PATTERNS):
        return "security_warning"
    return fallback


def _severity_for_check(check: CheckRunSummary) -> FeedbackSeverity | None:
    """根据 check 结论推导严重度。"""

    if check.conclusion == "success":
        return None
    if check.conclusion == "pending":
        return "warning"
    if check.conclusion in {"failure", "timed_out", "action_required", "cancelled"}:
        return "blocking" if check.is_required is not False else "error"
    return "warning"


def _severity_for_review(comment: ReviewCommentSummary) -> FeedbackSeverity:
    """review comment 的严重度通常比 CI 低一档。"""

    if comment.state == "CHANGES_REQUESTED":
        return "blocking"
    return "warning" if comment.body.strip() else "info"


def _severity_for_log(log: CILogSummary) -> FeedbackSeverity | None:
    """日志摘要只对失败类内容生成反馈项。"""

    if not log.summary.strip():
        return None
    return "error"


def _fingerprint_text(item: FeedbackItem) -> str:
    """把反馈项转成稳定指纹。"""

    summary_hash = sha256(_compress_text(item.summary).encode("utf-8")).hexdigest()[:16]
    return "|".join(
        [
            item.kind,
            item.source,
            item.check_name or "",
            item.file_path or "",
            str(item.line or ""),
            summary_hash,
        ]
    )


def build_feedback_fingerprint(item: FeedbackItem) -> str:
    """对外暴露的 fingerprint 计算函数。"""

    return _fingerprint_text(item)


def feedback_from_check(check: CheckRunSummary, *, observed_at: str | None, head_sha: str | None) -> FeedbackItem | None:
    """把单个 check 变成反馈项。"""

    severity = _severity_for_check(check)
    if severity is None:
        return None
    if check.correlation and check.correlation.confidence == "low":
        severity = "warning"
    summary = redact_feedback_text(check.output_summary or check.output_title or check.name, limit=500)
    kind = _classify_text(f"{check.name} {summary}")
    return FeedbackItem(
        kind=kind,
        severity=severity,
        title=check.name,
        summary=summary,
        source=check.correlation.source if check.correlation else "check_run",
        url=check.html_url,
        raw_excerpt=redact_feedback_text(check.output_summary or check.output_title or check.name, limit=500),
        observed_at=observed_at,
        head_sha=head_sha or check.head_sha,
        workflow_run_id=check.workflow_run_id,
        job_id=check.job_id,
        evidence_path=None,
        redacted=True,
        truncated=(check.output_summary is not None and len(check.output_summary) > 500) or (check.output_title is not None and len(check.output_title) > 500),
        stale=False,
        confidence=check.correlation.confidence if check.correlation else "high",
    )


def feedback_from_log(log: CILogSummary, *, observed_at: str | None, head_sha: str | None) -> FeedbackItem | None:
    """把 CI 日志摘要变成反馈项。"""

    severity = _severity_for_log(log)
    if severity is None:
        return None
    kind = _classify_text(log.summary)
    summary = redact_ci_log(log.summary)
    return FeedbackItem(
        kind=kind,
        severity=severity,
        title=log.name,
        summary=summary,
        source="ci_log",
        raw_excerpt=summary,
        observed_at=observed_at,
        head_sha=head_sha,
        workflow_run_id=log.workflow_run_id,
        job_id=log.job_id,
        evidence_path=log.evidence_path,
        redacted=True,
        truncated=log.truncated,
        stale=False,
        confidence="high",
    )


def feedback_from_review(
    comment: ReviewCommentSummary,
    *,
    observed_at: str | None,
    head_sha: str | None,
) -> FeedbackItem | None:
    """把 review / inline comment 变成反馈项。"""

    if not comment.body.strip():
        return None
    kind = "review_change_request" if comment.state == "CHANGES_REQUESTED" else "review_comment"
    if any(pattern.search(comment.body) for pattern in _SECURITY_PATTERNS):
        kind = "security_warning"
    summary = redact_review_body(comment.body)
    severity = _severity_for_review(comment)
    if comment.outdated:
        severity = "warning"
    return FeedbackItem(
        kind=kind,
        severity=severity,
        title=comment.path or (comment.state or "review"),
        summary=summary,
        source="review",
        file_path=comment.path,
        line=comment.line or comment.original_line,
        url=comment.url,
        raw_excerpt=summary,
        observed_at=observed_at,
        head_sha=head_sha,
        evidence_path=None,
        redacted=True,
        truncated=len(summary) >= 1200,
        stale=bool(comment.outdated),
        confidence="high" if not comment.outdated else "medium",
    )


def dedupe_feedback_items(items: list[FeedbackItem]) -> list[FeedbackItem]:
    """按 fingerprint 去重，只保留严重度更高的那条。"""

    chosen: dict[str, FeedbackItem] = {}
    for item in items:
        fingerprint = item.fingerprint or build_feedback_fingerprint(item)
        current = chosen.get(fingerprint)
        if current is None or _SEVERITY_RANK[item.severity] > _SEVERITY_RANK[current.severity]:
            chosen[fingerprint] = FeedbackItem(**{**item.__dict__, "fingerprint": fingerprint})
    return list(chosen.values())


def sort_feedback_items(items: list[FeedbackItem]) -> list[FeedbackItem]:
    """按 blocking > error > warning > info 排序。"""

    return sorted(items, key=lambda item: (-_SEVERITY_RANK[item.severity], item.kind, item.title))


def normalize_feedback(
    *,
    checks: list[CheckRunSummary],
    log_summaries: list[CILogSummary],
    review_comments: list[ReviewCommentSummary],
    max_items: int = 20,
    observed_at: str | None = None,
    head_sha: str | None = None,
) -> list[FeedbackItem]:
    """把所有反馈来源压缩成最终 follow-up 任务输入。"""

    items: list[FeedbackItem] = []
    items.extend(item for check in checks if (item := feedback_from_check(check, observed_at=observed_at, head_sha=head_sha)) is not None)
    items.extend(item for log in log_summaries if (item := feedback_from_log(log, observed_at=observed_at, head_sha=head_sha)) is not None)
    items.extend(
        item
        for comment in review_comments
        if (item := feedback_from_review(comment, observed_at=observed_at, head_sha=head_sha)) is not None
    )
    if not items:
        return []
    deduped = dedupe_feedback_items(items)
    ordered = sort_feedback_items(deduped)
    return ordered[:max_items]
