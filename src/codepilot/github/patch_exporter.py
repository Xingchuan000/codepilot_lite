from __future__ import annotations

from pathlib import Path

from codepilot.repo.git_utils import GitCommandError, is_git_repo, run_git
from codepilot.repo.patch_metadata import build_untracked_file_patch, compute_patch_metadata, get_untracked_files
from codepilot.repo.models import PatchMetadata
from codepilot.tools.patch_utils import extract_paths_from_patch


def export_patch(repo_path: str | Path, output_path: str | Path) -> Path:
    """导出当前工作区的 binary-safe git diff。"""

    repo_dir = Path(repo_path).expanduser().resolve()
    if not repo_dir.exists() or not repo_dir.is_dir():
        raise ValueError(f"Repository path must be an existing directory: {repo_dir}")
    if not is_git_repo(repo_dir):
        raise ValueError(f"Repository path is not a git work tree: {repo_dir}")
    try:
        diff_output = run_git(repo_dir, ["diff", "--binary"], timeout=30)
    except GitCommandError as exc:
        raise RuntimeError(f"git diff --binary failed: {exc.stderr_summary}") from exc

    patch_path = Path(output_path)
    patch_path.parent.mkdir(parents=True, exist_ok=True)
    untracked_patches = [build_untracked_file_patch(repo_dir, path) for path in get_untracked_files(repo_dir)]
    patch_path.write_text(diff_output + "".join(untracked_patches), encoding="utf-8")
    return patch_path


def remove_protected_patch_content(
    patch_path: str | Path,
    *,
    excluded_paths: list[str],
) -> Path:
    """从 patch artifact 中移除受保护路径对应的 diff block，避免失败路径泄露内容。"""

    path = Path(patch_path)
    patch_text = path.read_text(encoding="utf-8")
    if not patch_text.strip() or not excluded_paths:
        return path
    lines = patch_text.splitlines(keepends=True)
    kept_blocks: list[str] = []
    current_block: list[str] = []
    for line in lines:
        if line.startswith("diff --git ") and current_block:
            block_text = "".join(current_block)
            if not any(item in extract_paths_from_patch(block_text) for item in excluded_paths):
                kept_blocks.append(block_text)
            current_block = [line]
            continue
        if line.startswith("diff --git "):
            current_block = [line]
            continue
        if current_block:
            current_block.append(line)
        else:
            kept_blocks.append(line)
    if current_block:
        block_text = "".join(current_block)
        if not any(item in extract_paths_from_patch(block_text) for item in excluded_paths):
            kept_blocks.append(block_text)
    path.write_text("".join(kept_blocks), encoding="utf-8")
    return path


def export_patch_with_metadata(
    repo_path: str | Path,
    output_path: str | Path,
    *,
    base_head_sha: str | None = None,
    effective_head_sha: str | None = None,
    baseline_dirty: bool = False,
    contains_preexisting_changes: bool | None = None,
    protected_paths: list[str] | None = None,
    protected_after_files: list[str] | None = None,
) -> tuple[Path, PatchMetadata]:
    """导出 patch 后立刻计算 metadata，避免 workflow 重复拼装参数。"""

    patch_path = export_patch(repo_path, output_path)
    return (
        patch_path,
        compute_patch_metadata(
            repo_path,
            patch_path,
            base_head_sha=base_head_sha,
            effective_head_sha=effective_head_sha,
            baseline_dirty=baseline_dirty,
            contains_preexisting_changes=contains_preexisting_changes,
            protected_paths=protected_paths,
            protected_after_files=protected_after_files,
        ),
    )


def patch_is_empty(path: str | Path) -> bool:
    """判断导出的 patch 是否为空内容。"""

    return not Path(path).read_text(encoding="utf-8").strip()
