from __future__ import annotations

import fnmatch
from pathlib import Path

from codepilot.repo.git_utils import get_porcelain_status, get_repo_info, is_git_repo
from codepilot.repo.models import RepoSafetyConfig, RepoSafetyResult, RepoStateSnapshot


def normalize_repo_relative_path(path: str | Path) -> str:
    """把仓库内路径统一成 POSIX 相对路径，便于做 glob 匹配。"""

    normalized = str(path).replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized


def path_matches_any(path: str, patterns: list[str]) -> str | None:
    """返回首个命中的保护规则，便于上层构造可读错误信息。"""

    normalized = normalize_repo_relative_path(path)
    for pattern in patterns:
        if fnmatch.fnmatch(normalized, pattern):
            return pattern
        normalized_prefix = normalized.rstrip("/")
        if pattern.startswith(f"{normalized_prefix}/"):
            return pattern
    return None


def find_protected_paths(paths: list[str], protected_patterns: list[str]) -> list[str]:
    """按首次出现顺序找出命中保护规则的脏路径。"""

    matched: list[str] = []
    seen: set[str] = set()
    for path in paths:
        normalized = normalize_repo_relative_path(path)
        if normalized in seen or path_matches_any(normalized, protected_patterns) is None:
            continue
        seen.add(normalized)
        matched.append(normalized)
    return matched


def snapshot_repo_state(
    repo: str | Path,
    *,
    protected_paths: list[str] | None = None,
) -> RepoStateSnapshot:
    """采集当前仓库工作区快照，供安全决策与 artifact manifest 使用。"""

    repo_info = get_repo_info(repo)
    if not repo_info.is_git_repo:
        raise ValueError(f"Repository path is not a git repo: {repo_info.repo_path}")
    files = get_porcelain_status(repo_info.repo_path)
    dirty_paths = [item.path for item in files]
    return RepoStateSnapshot(
        repo_path=repo_info.repo_path,
        head_sha=repo_info.head_sha,
        branch=repo_info.current_branch,
        is_dirty=bool(files),
        files=files,
        untracked_count=sum(1 for item in files if item.untracked),
        protected_dirty_files=find_protected_paths(dirty_paths, protected_paths or []),
    )


def _dirty_warning(snapshot: RepoStateSnapshot) -> str:
    if not snapshot.files:
        return "Repository is clean."
    return "Dirty files: " + ", ".join(item.path for item in snapshot.files)


def check_repo_safety(
    repo: str | Path,
    *,
    config: RepoSafetyConfig | None = None,
) -> RepoSafetyResult:
    """按计划定义的固定顺序做 repo safety 判定，不引入额外兜底分支。"""

    config = config or RepoSafetyConfig()
    repo_path = Path(repo).expanduser().resolve()
    metadata = {
        "dirty_policy": config.dirty_policy,
        "worktree_mode": config.worktree_mode,
        "protected_paths_count": len(config.protected_paths),
        "dirty_files": [],
        "cleanup_worktree": config.cleanup_worktree,
    }
    if not repo_path.exists() or not repo_path.is_dir():
        return RepoSafetyResult(
            decision="deny",
            reason=f"Repo safety denied: repository path does not exist: {repo_path}",
            effective_repo_path=repo_path,
            used_worktree=config.worktree_mode == "create",
            metadata=metadata,
        )
    if not is_git_repo(repo_path):
        return RepoSafetyResult(
            decision="deny",
            reason=f"Repo safety denied: repository is not a git repo: {repo_path}",
            effective_repo_path=repo_path,
            used_worktree=config.worktree_mode == "create",
            metadata=metadata,
        )

    repo_info = get_repo_info(repo_path)
    before = snapshot_repo_state(repo_path, protected_paths=config.protected_paths)
    metadata["dirty_files"] = [item.path for item in before.files]
    warnings: list[str] = []
    if before.is_dirty:
        warnings.append(_dirty_warning(before))
    if before.protected_dirty_files:
        return RepoSafetyResult(
            decision="deny",
            reason="Repo safety denied: protected dirty path detected. "
            f"Protected dirty files: {', '.join(before.protected_dirty_files)}",
            repo_info=repo_info,
            before=before,
            effective_repo_path=repo_path,
            used_worktree=config.worktree_mode == "create",
            baseline_dirty=before.is_dirty,
            warnings=warnings,
            metadata=metadata,
        )
    if config.worktree_mode == "off" and before.is_dirty is False:
        return RepoSafetyResult(
            decision="allow",
            repo_info=repo_info,
            before=before,
            effective_repo_path=repo_path,
            used_worktree=False,
            baseline_dirty=False,
            contains_preexisting_changes=False,
            warnings=warnings,
            metadata=metadata,
        )
    if config.worktree_mode == "off" and before.is_dirty is True and config.dirty_policy == "fail":
        return RepoSafetyResult(
            decision="deny",
            reason=(
                "Repo safety denied: repository has uncommitted changes.\n"
                "Use --dirty-policy warn to continue, or --worktree to isolate the run."
            ),
            repo_info=repo_info,
            before=before,
            effective_repo_path=repo_path,
            used_worktree=False,
            baseline_dirty=True,
            contains_preexisting_changes=True,
            warnings=warnings,
            metadata=metadata,
        )
    if config.worktree_mode == "off" and before.is_dirty is True and config.dirty_policy == "warn":
        return RepoSafetyResult(
            decision="warn",
            reason="Repository has uncommitted changes.",
            repo_info=repo_info,
            before=before,
            effective_repo_path=repo_path,
            used_worktree=False,
            baseline_dirty=True,
            contains_preexisting_changes=True,
            warnings=warnings,
            metadata=metadata,
        )
    if config.worktree_mode == "off" and before.is_dirty is True and config.dirty_policy == "allow":
        return RepoSafetyResult(
            decision="allow",
            reason="Repository has uncommitted changes.",
            repo_info=repo_info,
            before=before,
            effective_repo_path=repo_path,
            used_worktree=False,
            baseline_dirty=True,
            contains_preexisting_changes=True,
            warnings=warnings,
            metadata=metadata,
        )
    if config.worktree_mode == "create" and before.is_dirty is False:
        return RepoSafetyResult(
            decision="allow",
            repo_info=repo_info,
            before=before,
            effective_repo_path=repo_path,
            used_worktree=True,
            baseline_dirty=False,
            contains_preexisting_changes=False,
            warnings=warnings,
            metadata=metadata,
        )
    if config.worktree_mode == "create" and before.is_dirty is True and config.require_clean_source_for_worktree:
        return RepoSafetyResult(
            decision="deny",
            reason="Repo safety denied: repository has uncommitted changes and worktree requires a clean source repo.",
            repo_info=repo_info,
            before=before,
            effective_repo_path=repo_path,
            used_worktree=True,
            baseline_dirty=True,
            contains_preexisting_changes=False,
            warnings=warnings,
            metadata=metadata,
        )
    return RepoSafetyResult(
        decision="warn",
        reason="Original repo has uncommitted changes. Worktree will be created from HEAD only.",
        repo_info=repo_info,
        before=before,
        effective_repo_path=repo_path,
        used_worktree=True,
        baseline_dirty=True,
        contains_preexisting_changes=False,
        warnings=warnings,
        metadata=metadata,
    )
