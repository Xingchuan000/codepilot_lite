from __future__ import annotations

"""收集 PR reviews / inline comments，并做安全化处理。"""

import re

from codepilot.pr_feedback.github_client import PRFeedbackGitHubClientProtocol, redact_feedback_text
from codepilot.pr_feedback.models import PRRef, ReviewCommentSummary


_TOKEN_RE = re.compile(r"ghp_[A-Za-z0-9_]+|github_pat_[A-Za-z0-9_]+|Bearer\s+[A-Za-z0-9._-]+|sk-[A-Za-z0-9_-]+")


def redact_review_body(body: str, *, limit: int = 1200) -> str:
    """把 review body 压缩到安全长度，并替换 token-like 字符串。"""

    redacted = _TOKEN_RE.sub("[REDACTED]", body.replace("\x00", ""))
    return redacted[:limit]


def quote_untrusted_feedback(text: str, *, limit: int = 1200) -> str:
    """把不可信反馈压成适合写入 Markdown 的安全文本。"""

    sanitized = redact_review_body(text, limit=limit)
    return sanitized.replace("```", "`\u200b``")


def normalize_review(raw: dict[str, object]) -> ReviewCommentSummary:
    """把 review payload 转成安全摘要。"""

    return ReviewCommentSummary(
        author=(raw.get("user") or {}).get("login") if isinstance(raw.get("user"), dict) else None,
        body=redact_review_body(str(raw.get("body") or "")),
        url=str(raw.get("html_url") or "") or None,
        state=str(raw.get("state") or "") or None,
        commit_id=str(raw.get("commit_id") or "") or None,
        resolved=None,
        resolution_unknown=True,
    )


def normalize_review_comment(raw: dict[str, object]) -> ReviewCommentSummary:
    """把 inline review comment payload 转成安全摘要。"""

    return ReviewCommentSummary(
        author=(raw.get("user") or {}).get("login") if isinstance(raw.get("user"), dict) else None,
        body=redact_review_body(str(raw.get("body") or "")),
        path=str(raw.get("path") or "") or None,
        line=raw.get("line") if isinstance(raw.get("line"), int) else None,
        side=str(raw.get("side") or "") or None,
        url=str(raw.get("html_url") or "") or None,
        original_line=raw.get("original_line") if isinstance(raw.get("original_line"), int) else None,
        commit_id=str(raw.get("commit_id") or "") or None,
        original_commit_id=str(raw.get("original_commit_id") or "") or None,
        outdated=bool(raw.get("outdated")) if raw.get("outdated") is not None else None,
        resolved=None,
        resolution_unknown=True,
    )


def filter_actionable_review_comments(comments: list[ReviewCommentSummary]) -> list[ReviewCommentSummary]:
    """只保留对任务有价值的 review 内容。"""

    return [comment for comment in comments if comment.body.strip()]


def sanitize_review_comment_for_task(comment: ReviewCommentSummary) -> ReviewCommentSummary:
    """在写入 follow-up task 前再做一次最小清洗。"""

    return ReviewCommentSummary(
        author=comment.author,
        body=redact_review_body(comment.body),
        path=comment.path,
        line=comment.line,
        side=comment.side,
        url=comment.url,
        state=comment.state,
        original_line=comment.original_line,
        commit_id=comment.commit_id,
        original_commit_id=comment.original_commit_id,
        outdated=comment.outdated,
        resolved=comment.resolved,
        resolution_unknown=comment.resolution_unknown,
    )


def collect_pr_reviews(*, client: PRFeedbackGitHubClientProtocol, pr: PRRef) -> list[ReviewCommentSummary]:
    """收集 review 与 inline comments，并转成统一摘要。"""

    reviews = [normalize_review(raw) for raw in client.list_pull_request_reviews(pr)]
    comments = [normalize_review_comment(raw) for raw in client.list_pull_request_review_comments(pr)]
    return filter_actionable_review_comments([*reviews, *comments])
