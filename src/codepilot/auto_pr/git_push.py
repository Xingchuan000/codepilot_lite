from __future__ import annotations

"""受控 git push，只允许最小 refspec。"""

from dataclasses import replace
from pathlib import Path
from typing import Any

from codepilot.auto_pr.git_remote import assert_branch_name_safe, get_current_branch
from codepilot.auto_pr.models import AutoPRGitError, BranchPushPlan
from codepilot.repo.git_utils import (
    GitCommandError,
    get_head_sha,
    get_remote_branch_sha as repo_get_remote_branch_sha,
    get_worktree_clean,
    run_git,
)


def remote_branch_exists(
    repo_path: str | Path,
    *,
    remote_name: str,
    remote_branch: str,
) -> bool:
    """检查远端分支是否已经存在。"""

    try:
        return get_remote_branch_sha(repo_path, remote_name=remote_name, remote_branch=remote_branch) is not None
    except GitCommandError as exc:
        raise AutoPRGitError(exc.stderr_summary) from exc


def assert_remote_branch_available(
    repo_path: str | Path,
    *,
    remote_name: str,
    remote_branch: str,
    allow_update_existing: bool = False,
) -> None:
    """默认拒绝覆盖已存在的远端分支。"""

    if remote_branch_exists(repo_path, remote_name=remote_name, remote_branch=remote_branch) and not allow_update_existing:
        raise AutoPRGitError(f"remote branch already exists: {remote_branch}")


def verify_local_push_preconditions(repo_path: str | Path, *, push_plan: BranchPushPlan) -> None:
    """执行 push 前再次确认本地状态和计划完全一致。"""

    if get_current_branch(repo_path) != push_plan.local_branch:
        raise AutoPRGitError("current branch does not match push plan")
    if get_head_sha(repo_path) != push_plan.commit_sha:
        raise AutoPRGitError("HEAD sha does not match push plan")
    if not get_worktree_clean(repo_path):
        raise AutoPRGitError("working tree must be clean before push")
    assert_branch_name_safe(remote_branch=push_plan.remote_branch, base_branch=push_plan.base_branch)
    if any(flag in push_plan.push_refspec for flag in ["--force", "--mirror", "--all"]):
        raise AutoPRGitError("push_refspec contains forbidden push flags")


def get_remote_branch_sha(
    repo_path: str | Path,
    *,
    remote_name: str,
    remote_branch: str,
) -> str | None:
    """代理 repo.git_utils 的远端分支 sha 查询。"""

    return repo_get_remote_branch_sha(repo_path, remote_name, remote_branch)


def verify_remote_branch_points_to_commit(
    repo_path: str | Path,
    *,
    remote_name: str,
    remote_branch: str,
    expected_commit_sha: str,
) -> None:
    """push 成功后再验证远端 ref 是否确实指向目标提交。"""

    remote_sha = get_remote_branch_sha(repo_path, remote_name=remote_name, remote_branch=remote_branch)
    if remote_sha is None:
        raise AutoPRGitError("remote branch sha is missing after push")
    if remote_sha != expected_commit_sha:
        raise AutoPRGitError("remote branch sha does not match expected commit")


def push_branch(
    repo_path: str | Path,
    *,
    push_plan: BranchPushPlan,
    execute: bool = False,
    allow_push: bool = False,
) -> BranchPushPlan:
    """只在 execute + allow_push 时执行真正的 push。"""

    if not execute or not allow_push:
        return replace(push_plan, will_push=False)
    verify_local_push_preconditions(repo_path, push_plan=push_plan)
    assert_remote_branch_available(repo_path, remote_name=push_plan.remote_name, remote_branch=push_plan.remote_branch)
    try:
        run_git(repo_path, ["push", push_plan.remote_name, push_plan.push_refspec], timeout=30)
    except GitCommandError as exc:
        raise AutoPRGitError(exc.stderr_summary) from exc
    verify_remote_branch_points_to_commit(
        repo_path,
        remote_name=push_plan.remote_name,
        remote_branch=push_plan.remote_branch,
        expected_commit_sha=push_plan.commit_sha,
    )
    return replace(
        push_plan,
        will_push=True,
        remote_ref_verified=True,
        remote_ref_sha=get_remote_branch_sha(
            repo_path,
            remote_name=push_plan.remote_name,
            remote_branch=push_plan.remote_branch,
        ),
    )


def push_existing_pr_branch(
    repo_path: str | Path,
    *,
    remote_name: str,
    remote_branch: str,
    base_branch: str,
    expected_current_remote_sha: str,
    new_commit_sha: str,
    execute: bool = False,
    allow_push_update: bool = False,
) -> dict[str, Any]:
    """显式更新一个已经存在的 PR 分支，只允许在受控的 execute 路径里调用。"""

    if not execute:
        return {"pushed": False, "reason": "execute=false"}
    if not allow_push_update:
        return {"pushed": False, "reason": "allow_push_update=false"}
    if not remote_branch.startswith("codepilot/"):
        raise AutoPRGitError("remote_branch must start with codepilot/")
    if remote_branch in {"main", "master", base_branch}:
        raise AutoPRGitError("remote_branch must not equal main, master, or base branch")
    if get_head_sha(repo_path) != new_commit_sha:
        raise AutoPRGitError("HEAD sha does not match expected new commit")
    if not get_worktree_clean(repo_path):
        raise AutoPRGitError("working tree must be clean before push")
    current_remote_sha = get_remote_branch_sha(repo_path, remote_name=remote_name, remote_branch=remote_branch)
    if current_remote_sha != expected_current_remote_sha:
        raise AutoPRGitError("remote branch sha does not match expected current sha")
    try:
        run_git(repo_path, ["push", remote_name, f"HEAD:refs/heads/{remote_branch}"], timeout=30)
    except GitCommandError as exc:
        raise AutoPRGitError(exc.stderr_summary) from exc
    remote_sha = get_remote_branch_sha(repo_path, remote_name=remote_name, remote_branch=remote_branch)
    if remote_sha != new_commit_sha:
        raise AutoPRGitError("remote branch sha does not match new commit after push")
    return {
        "pushed": True,
        "remote_name": remote_name,
        "remote_branch": remote_branch,
        "base_branch": base_branch,
        "expected_current_remote_sha": expected_current_remote_sha,
        "new_commit_sha": new_commit_sha,
        "remote_sha": remote_sha,
    }
