from __future__ import annotations

"""PR head 新鲜度与推送前置校验。"""

from pathlib import Path

from codepilot.auto_pr.git_push import get_remote_branch_sha
from codepilot.pr_feedback.github_client import PRFeedbackGitHubClientProtocol
from codepilot.pr_feedback.models import FeedbackFreshness, PRFeedbackStaleHeadError, PRRef


def resolve_current_pr_head(
    client: PRFeedbackGitHubClientProtocol,
    pr: PRRef,
) -> tuple[str | None, str | None]:
    """读取当前 PR head sha，并确认 head branch 没有被切换到别的分支。"""

    data = client.get_pull_request(pr)
    head = data.get("head") or {}
    if head.get("ref") != pr.head_branch:
        raise PRFeedbackStaleHeadError("PR head branch does not match the manifest head branch")
    return head.get("sha"), data.get("updated_at")


def build_feedback_freshness(
    *,
    observed_head_sha: str | None,
    current_head_sha: str | None,
    observed_at: str | None,
) -> FeedbackFreshness:
    """把观察到的 PR head 状态压缩成一个可序列化对象。"""

    is_stale = observed_head_sha is not None and current_head_sha is not None and observed_head_sha != current_head_sha
    stale_reason = None
    if is_stale:
        stale_reason = "PR head sha changed after the source manifest was written"
    return FeedbackFreshness(
        observed_head_sha=observed_head_sha,
        current_head_sha=current_head_sha,
        observed_at=observed_at,
        is_stale=is_stale,
        stale_reason=stale_reason,
    )


def assert_fresh_head_for_execute(freshness: FeedbackFreshness) -> None:
    """执行模式下必须先确认 head 没有过期。"""

    if freshness.is_stale:
        raise PRFeedbackStaleHeadError(freshness.stale_reason or "PR head sha is stale")


def assert_controlled_head_branch(pr: PRRef) -> None:
    """只允许在受控的 codepilot/ 分支上做 follow-up。"""

    if not pr.head_branch.startswith("codepilot/"):
        raise PRFeedbackStaleHeadError("head_branch must start with codepilot/")
    if pr.head_branch in {"main", "master", pr.base_branch}:
        raise PRFeedbackStaleHeadError("head_branch must not equal main, master, or base branch")


def assert_remote_branch_sha_for_push(
    repo_path: str | Path,
    *,
    remote_name: str,
    branch: str,
    expected_sha: str,
) -> None:
    """push 前先确认远端分支仍然指向预期提交。"""

    remote_sha = get_remote_branch_sha(repo_path, remote_name=remote_name, remote_branch=branch)
    if remote_sha != expected_sha:
        raise PRFeedbackStaleHeadError("remote branch sha does not match expected commit")
