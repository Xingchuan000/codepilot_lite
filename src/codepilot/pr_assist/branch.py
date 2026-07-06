from __future__ import annotations

"""可选的本地分支准备逻辑。"""

from pathlib import Path

from codepilot.pr_assist.models import BranchPrepError
from codepilot.repo.git_utils import GitCommandError, is_git_repo, run_git
from codepilot.repo.safety import snapshot_repo_state
from codepilot.repo.worktree import branch_exists, sanitize_run_id_for_ref


def sanitize_branch_name(run_id: str, prefix: str = "codepilot") -> str:
    """把 prefix 和 run_id 变成稳定的本地分支名。"""

    safe_prefix = sanitize_run_id_for_ref(prefix)
    safe_run_id = sanitize_run_id_for_ref(run_id)
    return f"{safe_prefix}/{safe_run_id}"


def prepare_local_branch(
    repo_path: str | Path,
    *,
    branch_name: str,
    base_ref: str = "HEAD",
    fail_if_exists: bool = True,
    allow_dirty: bool = False,
) -> str:
    """只在本地创建分支，并明确拒绝 dirty repo 的默认情况。"""

    repo = Path(repo_path).expanduser().resolve()
    if not is_git_repo(repo):
        raise BranchPrepError(f"Not a git repository: {repo}")
    snapshot = snapshot_repo_state(repo)
    if snapshot.is_dirty and not allow_dirty:
        raise BranchPrepError("Repository is dirty. Refuse to create branch by default.")
    if branch_exists(repo, branch_name):
        if fail_if_exists:
            raise BranchPrepError(f"Branch already exists: {branch_name}")
        return branch_name
    try:
        run_git(repo, ["switch", "-c", branch_name, base_ref], timeout=30)
    except GitCommandError as exc:
        raise BranchPrepError(exc.stderr_summary) from exc
    return branch_name
