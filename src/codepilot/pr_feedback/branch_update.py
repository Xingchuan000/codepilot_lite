from __future__ import annotations

"""follow-up commit 与 PR 分支更新。"""

from pathlib import Path
from typing import Any

from codepilot.auto_pr.git_push import push_existing_pr_branch
from codepilot.pr_assist.commit import prepare_commit, render_commit_message
from codepilot.pr_feedback.freshness import assert_remote_branch_sha_for_push
from codepilot.pr_feedback.models import PRFeedbackStaleHeadError, PRRef
from codepilot.repo.models import PatchMetadata


def build_pr_branch_update_plan(
    *,
    pr: PRRef,
    repo_path: str | Path,
    expected_current_head_sha: str,
    new_commit_sha: str,
    remote_name: str = "origin",
) -> dict[str, Any]:
    """构造更新 PR 分支前的最小计划对象。"""

    return {
        "repo_path": str(Path(repo_path).expanduser().resolve()),
        "remote_name": remote_name,
        "remote_branch": pr.head_branch,
        "base_branch": pr.base_branch,
        "expected_current_head_sha": expected_current_head_sha,
        "new_commit_sha": new_commit_sha,
        "pull_number": pr.pull_number,
        "url": pr.url,
    }


def push_pr_branch_update_if_allowed(
    *,
    repo_path: str | Path,
    pr: PRRef,
    new_commit_sha: str,
    expected_current_head_sha: str,
    execute: bool,
    allow_push_update: bool,
    remote_name: str = "origin",
) -> dict[str, Any]:
    """在 execute 且显式允许 push_update 时才更新远端 PR 分支。"""

    if not execute:
        return {"pushed": False, "reason": "execute=false"}
    if not allow_push_update:
        return {"pushed": False, "reason": "allow_push_update=false"}
    if not pr.head_branch.startswith("codepilot/"):
        raise PRFeedbackStaleHeadError("head_branch must start with codepilot/")
    if pr.head_branch in {"main", "master", pr.base_branch}:
        raise PRFeedbackStaleHeadError("head_branch must not equal main, master, or base branch")
    assert_remote_branch_sha_for_push(repo_path, remote_name=remote_name, branch=pr.head_branch, expected_sha=expected_current_head_sha)
    return push_existing_pr_branch(
        repo_path,
        remote_name=remote_name,
        remote_branch=pr.head_branch,
        base_branch=pr.base_branch,
        expected_current_remote_sha=expected_current_head_sha,
        new_commit_sha=new_commit_sha,
        execute=execute,
        allow_push_update=allow_push_update,
    )


def prepare_followup_commit(
    repo_path: str | Path,
    *,
    attempt_manifest_path: str | Path,
    patch_metadata: PatchMetadata,
    issue_title: str,
    tests_summary: str | None,
    run_id: str,
    allow_empty: bool = False,
) -> str:
    """复用第十二步的提交准备逻辑，生成 follow-up commit。"""

    message = render_commit_message(
        issue_title=issue_title,
        changed_files=patch_metadata.changed_files,
        tests_summary=tests_summary,
        run_id=run_id,
    )
    if not patch_metadata.changed_files and not allow_empty:
        raise PRFeedbackStaleHeadError("follow-up commit requires changed files")
    return prepare_commit(
        repo_path,
        message=message,
        changed_files=patch_metadata.changed_files,
        run_id=run_id,
        allow_empty=allow_empty,
    )
