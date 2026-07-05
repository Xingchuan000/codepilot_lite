from __future__ import annotations

import re
import tempfile
from pathlib import Path

from codepilot.repo.git_utils import GitCommandError, get_git_root, get_head_sha, run_git
from codepilot.repo.models import CleanupResult, WorktreeInfo


def sanitize_run_id_for_ref(value: str, *, max_length: int = 64) -> str:
    """把 run_id / branch prefix 压缩成安全 ref 片段，避免创建非法分支名。"""

    sanitized = re.sub(r"[^A-Za-z0-9._-]+", "-", value)
    sanitized = re.sub(r"-{2,}", "-", sanitized).strip(".-/")
    return (sanitized or "run")[:max_length]


def ensure_worktree_path_is_outside_repo(worktree_path: Path, git_root: Path) -> None:
    """禁止把 worktree 建在原仓库内部，避免污染原工作区。"""

    resolved_worktree = worktree_path.resolve()
    resolved_root = git_root.resolve()
    if resolved_worktree == resolved_root:
        raise ValueError("Worktree path must be outside the original git root.")
    try:
        resolved_worktree.relative_to(resolved_root)
    except ValueError:
        return
    raise ValueError("Worktree path must be outside the original git root.")


def branch_exists(repo: str | Path, branch_name: str) -> bool:
    """只检查本地 refs/heads，避免把远端状态带入这个最小实现。"""

    try:
        run_git(repo, ["show-ref", "--verify", "--quiet", f"refs/heads/{branch_name}"])
    except GitCommandError:
        return False
    return True


def create_issue_worktree(
    repo: str | Path,
    *,
    run_id: str,
    base_dir: str | Path | None = None,
    branch_prefix: str = "codepilot",
) -> WorktreeInfo:
    """基于当前 HEAD 创建隔离 worktree，不覆盖已有 branch 或目录。"""

    repo_path = Path(repo).expanduser().resolve()
    git_root = get_git_root(repo_path)
    base_head_sha = get_head_sha(repo_path)
    safe_run_id = sanitize_run_id_for_ref(run_id)
    safe_prefix = sanitize_run_id_for_ref(branch_prefix)
    branch_name = f"{safe_prefix}/{safe_run_id}"
    if branch_exists(repo_path, branch_name):
        raise ValueError(f"Worktree branch already exists: {branch_name}")
    target_base_dir = (
        Path(base_dir).expanduser().resolve()
        if base_dir is not None
        else (Path(tempfile.gettempdir()) / "codepilot-worktrees").resolve()
    )
    worktree_path = target_base_dir / safe_run_id
    if worktree_path.exists():
        raise FileExistsError(f"Worktree path already exists: {worktree_path}")
    ensure_worktree_path_is_outside_repo(worktree_path, git_root)
    worktree_path.parent.mkdir(parents=True, exist_ok=True)
    run_git(repo_path, ["worktree", "add", "-b", branch_name, str(worktree_path), "HEAD"], timeout=60)
    worktree_head_sha = get_head_sha(worktree_path)
    if worktree_head_sha != base_head_sha:
        raise RuntimeError("Worktree HEAD does not match original HEAD.")
    return WorktreeInfo(
        original_repo_path=repo_path,
        worktree_path=worktree_path,
        branch_name=branch_name,
        base_head_sha=base_head_sha,
        worktree_head_sha=worktree_head_sha,
    )


def remove_issue_worktree(
    worktree_path: str | Path,
    *,
    original_repo: str | Path,
    branch_name: str | None = None,
    force: bool = False,
) -> CleanupResult:
    """尝试移除 worktree，但失败只转成结果对象，不向上抛异常覆盖主流程。"""

    resolved_worktree_path = Path(worktree_path).expanduser().resolve()
    if not resolved_worktree_path.exists():
        return CleanupResult(
            attempted=False,
            success=None,
            reason="worktree path does not exist",
            branch_name=branch_name,
            branch_left_in_place=branch_name is not None,
        )
    args = ["worktree", "remove", str(resolved_worktree_path)]
    if force:
        args.append("--force")
    try:
        run_git(original_repo, args, timeout=60)
    except GitCommandError as exc:
        return CleanupResult(
            requested=True,
            attempted=True,
            success=False,
            reason=exc.stderr_summary,
            branch_name=branch_name,
            branch_left_in_place=branch_name is not None,
        )
    return CleanupResult(
        requested=True,
        attempted=True,
        success=True,
        branch_name=branch_name,
        branch_left_in_place=branch_name is not None,
    )
