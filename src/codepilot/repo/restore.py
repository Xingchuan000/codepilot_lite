from __future__ import annotations

from pathlib import Path

from codepilot.repo.models import CleanupResult, PatchMetadata


def render_restore_plan(
    *,
    run_id: str,
    repo_path: str | Path,
    effective_repo_path: str | Path,
    used_worktree: bool,
    worktree_path: str | Path | None,
    baseline_dirty: bool,
    patch_metadata: PatchMetadata | None,
    cleanup_result: CleanupResult | None = None,
    redact_absolute_paths: bool = False,
) -> str:
    """生成只包含人工恢复提示的 Markdown，避免输出危险回滚命令。"""

    changed_files = [] if patch_metadata is None else patch_metadata.changed_files
    original_repo_text = "[REDACTED_PATH]" if redact_absolute_paths else str(Path(repo_path).expanduser().resolve())
    effective_repo_text = "[REDACTED_PATH]" if redact_absolute_paths else str(Path(effective_repo_path).expanduser().resolve())
    worktree_path_text = (
        "[REDACTED_PATH]" if redact_absolute_paths and worktree_path is not None else str(worktree_path)
    )
    patch_path_text = (
        Path(patch_metadata.patch_path).name
        if redact_absolute_paths and patch_metadata is not None
        else str(patch_metadata.patch_path) if patch_metadata is not None else "none"
    )
    lines = [
        "# Restore Plan",
        "",
        "## Run",
        f"- Run ID: `{run_id}`",
        f"- Original repo: `{original_repo_text}`",
        f"- Effective repo: `{effective_repo_text}`",
        f"- Worktree used: {'yes' if used_worktree else 'no'}",
        f"- Baseline dirty: {'yes' if baseline_dirty else 'no'}",
        "",
        "## Patch",
        f"- Patch path: `{patch_path_text}`",
        f"- Empty patch: {'yes' if patch_metadata is not None and patch_metadata.is_empty else 'no' if patch_metadata is not None else 'unknown'}",
        f"- Patch SHA256: `{patch_metadata.sha256 if patch_metadata is not None and patch_metadata.sha256 is not None else 'none'}`",
        "- Changed files:",
    ]
    lines.extend(f"  - {path}" for path in changed_files) if changed_files else lines.append("  - none")
    lines.extend(["", "## Worktree"])
    if used_worktree:
        lines.append(f"- Worktree path: `{worktree_path_text}`")
        if cleanup_result is not None and cleanup_result.branch_name is not None:
            lines.append(f"- Worktree branch: `{cleanup_result.branch_name}`")
            lines.append("- Branch cleanup: branch is left in place by v1 for safety.")
            lines.append("- If you want to reuse the same run_id, inspect and remove the branch manually.")
        if cleanup_result is not None:
            lines.append(f"- Cleanup requested: {'yes' if cleanup_result.requested else 'no'}")
            lines.append(
                "- Cleanup status: "
                + (
                    "success"
                    if cleanup_result.success is True
                    else "failed"
                    if cleanup_result.success is False
                    else "not attempted"
                )
            )
            lines.append(f"- Reason: {cleanup_result.reason or 'none'}")
        if cleanup_result is None or cleanup_result.success is not True:
            lines.append("- Please manually inspect the worktree before removing it.")
            lines.append(f"- Suggested command: `git worktree remove {worktree_path_text}`")
    else:
        lines.append("- Worktree not used.")
    lines.extend(["", "## Non-worktree recovery"])
    if not used_worktree:
        lines.append("- Review `changes.patch` first before touching the repository.")
    if baseline_dirty:
        lines.append("- Do not use commands that overwrite pre-existing user changes.")
    if patch_metadata is not None and patch_metadata.protected_after_files:
        lines.append("- Protected dirty files were detected after the run.")
        lines.append("- Do not blindly reset or clean the repository.")
        lines.append("- Review these files manually before any recovery action.")
    lines.append("- Manually review the changed files listed above before applying any recovery action.")
    return "\n".join(lines).rstrip() + "\n"


def write_restore_plan(
    *,
    run_id: str,
    repo_path: str | Path,
    effective_repo_path: str | Path,
    used_worktree: bool,
    worktree_path: str | Path | None,
    baseline_dirty: bool,
    patch_metadata: PatchMetadata | None,
    cleanup_result: CleanupResult | None = None,
    output_path: str | Path,
    redact_absolute_paths: bool = False,
) -> Path:
    """把恢复说明写成文件，供人工审查和后续自动化读取。"""

    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        render_restore_plan(
            run_id=run_id,
            repo_path=repo_path,
            effective_repo_path=effective_repo_path,
            used_worktree=used_worktree,
            worktree_path=worktree_path,
            baseline_dirty=baseline_dirty,
            patch_metadata=patch_metadata,
            cleanup_result=cleanup_result,
            redact_absolute_paths=redact_absolute_paths,
        ),
        encoding="utf-8",
    )
    return path
