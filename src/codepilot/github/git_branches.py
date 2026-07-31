from __future__ import annotations

"""受控 Git 远端分支操作。"""

from pathlib import Path
from typing import Any

from codepilot.auto_pr.models import AutoPRGitError
from codepilot.repo.git_utils import GitCommandError, get_head_sha, get_remote_branch_sha as repo_get_remote_branch_sha, get_worktree_clean, run_git


def get_remote_branch_sha(
    repo_path: str | Path,
    *,
    remote_name: str,
    remote_branch: str,
) -> str | None:
    """代理 repo.git_utils 的远端分支 sha 查询。"""

    return repo_get_remote_branch_sha(repo_path, remote_name, remote_branch)


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
