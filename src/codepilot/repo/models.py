from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
from pathlib import Path
from typing import Any, Literal

from codepilot.repo.protected_paths import DEFAULT_REPO_PROTECTED_PATHS


DirtyPolicy = Literal["fail", "warn", "allow"]
WorktreeMode = Literal["off", "create"]
SafetyDecision = Literal["allow", "warn", "deny"]


@dataclass(frozen=True)
class GitRepoInfo:
    """描述当前仓库的最小 Git 身份信息。"""

    repo_path: Path
    git_root: Path
    current_branch: str | None
    head_sha: str | None
    is_git_repo: bool


@dataclass(frozen=True)
class GitFileStatus:
    """描述一条 git status --porcelain 结果。"""

    path: str
    status: str
    staged: bool = False
    unstaged: bool = False
    untracked: bool = False


@dataclass(frozen=True)
class RepoStateSnapshot:
    """记录某个时刻仓库工作区状态，后续会写入 manifest。"""

    repo_path: Path
    head_sha: str | None
    branch: str | None
    is_dirty: bool
    files: list[GitFileStatus] = field(default_factory=list)
    untracked_count: int = 0
    protected_dirty_files: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class RepoSafetyConfig:
    """聚合第十一步需要的 repo safety 配置。"""

    dirty_policy: DirtyPolicy = "fail"
    worktree_mode: WorktreeMode = "off"
    worktree_base_dir: Path | None = None
    keep_worktree: bool = True
    cleanup_worktree: bool = False
    require_clean_source_for_worktree: bool = False
    worktree_branch_prefix: str = "codepilot"
    redact_absolute_paths: bool = False
    protected_paths: list[str] = field(default_factory=lambda: list(DEFAULT_REPO_PROTECTED_PATHS))


@dataclass(frozen=True)
class RepoSafetyResult:
    """保存 repo safety 判定结果，workflow 会直接消费它。"""

    decision: SafetyDecision
    reason: str | None = None
    repo_info: GitRepoInfo | None = None
    before: RepoStateSnapshot | None = None
    effective_repo_path: Path | None = None
    used_worktree: bool = False
    baseline_dirty: bool = False
    contains_preexisting_changes: bool | None = None
    warnings: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class WorktreeInfo:
    """描述创建出来的隔离 worktree。"""

    original_repo_path: Path
    worktree_path: Path
    branch_name: str
    base_head_sha: str | None
    worktree_head_sha: str | None


@dataclass(frozen=True)
class CleanupResult:
    """描述 worktree 清理尝试结果。"""

    requested: bool = False
    attempted: bool = False
    success: bool | None = None
    reason: str | None = None
    branch_name: str | None = None
    branch_left_in_place: bool | None = None


@dataclass(frozen=True)
class PatchMetadata:
    """保存 patch 文件的轻量元信息，避免在 artifact 中写完整 diff。"""

    patch_path: Path
    is_empty: bool
    size_bytes: int
    sha256: str | None
    changed_files: list[str]
    diff_stat: str | None = None
    base_head_sha: str | None = None
    effective_head_sha: str | None = None
    baseline_dirty: bool = False
    contains_preexisting_changes: bool | None = None
    generated_from_repo: Path | None = None
    protected_changed_files: list[str] = field(default_factory=list)
    untracked_files: list[str] = field(default_factory=list)
    untracked_files_omitted: list[str] = field(default_factory=list)
    protected_after_files: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ArtifactEntry:
    """描述一个产物文件在 manifest 里的最小索引信息。"""

    name: str
    path: str
    kind: str
    exists: bool
    size_bytes: int | None = None
    sha256: str | None = None


@dataclass(frozen=True)
class RunArtifactManifest:
    """第十一步定义的 artifact manifest 结构。"""

    schema_version: str
    created_at: str
    generator: str
    generator_version: str | None
    run_id: str
    status: str
    success: bool | None
    repo_path: str
    effective_repo_path: str
    used_worktree: bool
    worktree_path: str | None
    safety_decision: str
    safety_reason: str | None
    safety_warnings: list[str]
    safety_summary: dict[str, Any]
    before: dict[str, Any] | None
    after: dict[str, Any] | None
    original_after: dict[str, Any] | None
    patch: dict[str, Any] | None
    cleanup: dict[str, Any] | None
    artifacts: list[ArtifactEntry]
    redactions_applied: list[str] = field(default_factory=list)


def to_jsonable(value: Any) -> Any:
    """把 Path / dataclass 递归转换成可直接 json.dumps 的结构。"""

    if isinstance(value, Path):
        return str(value)
    if is_dataclass(value):
        return {key: to_jsonable(item) for key, item in asdict(value).items()}
    if isinstance(value, dict):
        return {key: to_jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_jsonable(item) for item in value]
    return value
