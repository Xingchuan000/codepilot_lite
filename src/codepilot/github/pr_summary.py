from __future__ import annotations

from pathlib import Path
from typing import Any

from codepilot.github.issue_models import IssueTask
from codepilot.repo.models import PatchMetadata
from codepilot.report.models import RunReport


def render_pr_summary(
    issue: IssueTask,
    report: RunReport | dict[str, Any],
    *,
    patch_path: str | Path | None = None,
    report_path: str | Path | None = None,
    manifest_path: str | Path | None = None,
    restore_plan_path: str | Path | None = None,
    repo_path: str | Path | None = None,
    effective_repo_path: str | Path | None = None,
    used_worktree: bool = False,
    worktree_path: str | Path | None = None,
    dirty_policy: str | None = None,
    baseline_dirty: bool | None = None,
    contains_preexisting_changes: bool | None = None,
    safety_decision: str | None = None,
    safety_reason: str | None = None,
    safety_warnings: list[str] | None = None,
    patch_metadata: PatchMetadata | None = None,
    redact_absolute_paths: bool = False,
) -> str:
    """把 issue 与 evidence report 摘要渲染成固定 PR 说明。"""

    normalized_report = report if isinstance(report, RunReport) else RunReport.model_validate(report)
    fixes_value = issue.ref.url or issue.title
    summary = normalized_report.final_summary or normalized_report.status or "No final summary available."
    changed_files = normalized_report.changed_files or []
    test_status = normalized_report.tests.status or "unknown"
    test_command = normalized_report.tests.command or "unknown"
    test_summary = normalized_report.tests.summary or "None."
    report_name = Path(report_path).name if report_path is not None else "report.md"
    patch_name = Path(patch_path).name if patch_path is not None else "changes.patch"
    manifest_name = Path(manifest_path).name if manifest_path is not None else "artifact_manifest.json"
    restore_plan_name = Path(restore_plan_path).name if restore_plan_path is not None else "restore_plan.md"

    original_repo_text = "[REDACTED_PATH]" if redact_absolute_paths and repo_path is not None else repo_path or "unknown"
    effective_repo_text = (
        "[REDACTED_PATH]" if redact_absolute_paths and effective_repo_path is not None else effective_repo_path or "unknown"
    )
    worktree_path_text = "[REDACTED_PATH]" if redact_absolute_paths and worktree_path is not None else worktree_path or "none"
    lines = [
        "# PR Summary",
        "",
        "## Issue",
        "",
        f"Fixes: {fixes_value}",
        "",
        "## Summary",
        "",
        summary,
        "",
        "## Changes",
        "",
    ]
    if changed_files:
        lines.extend(f"- {path}" for path in changed_files)
    else:
        lines.append("- No changed files reported.")
    lines.extend(
        [
            "",
            "## Tests",
            "",
            f"- Status: {test_status}",
            f"- Command: `{test_command}`",
            f"- Summary: {test_summary}",
            "",
            "## Evidence",
            "",
            f"- Report: `{report_name}`",
            f"- Patch: `{patch_name}`",
            f"- Manifest: `{manifest_name}`",
            f"- Restore plan: `{restore_plan_name}`",
            "",
            "## Safety",
            "",
            "- No commit was created automatically.",
            "- No push was performed.",
            "- No pull request was created automatically.",
            f"- Original repo: `{original_repo_text}`",
            f"- Effective repo: `{effective_repo_text}`",
            f"- Worktree used: {'yes' if used_worktree else 'no'}",
            f"- Worktree path: `{worktree_path_text}`",
            f"- Dirty policy: {dirty_policy or 'unknown'}",
            f"- Baseline dirty: {'yes' if baseline_dirty is True else 'no' if baseline_dirty is False else 'unknown'}",
            f"- Safety decision: {safety_decision or 'unknown'}",
            f"- Safety reason: {safety_reason or 'unknown'}",
        ]
    )
    if contains_preexisting_changes is True:
        lines.extend(["", "Warning: this patch may include changes that existed before CodePilot started."])
    if used_worktree:
        lines.extend(
            [
                "",
                "The agent ran in an isolated worktree. The original repo was not directly modified by the agent run.",
            ]
        )
    for warning in safety_warnings or []:
        lines.append(f"- Warning: {warning}")
    if patch_metadata is not None:
        generated_from_repo = (
            "[REDACTED_PATH]"
            if redact_absolute_paths and patch_metadata.generated_from_repo is not None
            else patch_metadata.generated_from_repo or "unknown"
        )
        lines.extend(
            [
                "",
                "## Patch Metadata",
                "",
                f"- Empty patch: {'yes' if patch_metadata.is_empty else 'no'}",
                f"- Patch SHA256: {patch_metadata.sha256 or 'none'}",
                f"- Patch size: {patch_metadata.size_bytes} bytes",
                f"- Generated from repo: {generated_from_repo}",
                f"- Base HEAD: {patch_metadata.base_head_sha or 'unknown'}",
                f"- Effective HEAD: {patch_metadata.effective_head_sha or 'unknown'}",
                "- Changed files:",
            ]
        )
        if patch_metadata.changed_files:
            lines.extend(f"  - {path}" for path in patch_metadata.changed_files)
        else:
            lines.append("  - no changes" if not patch_metadata.untracked_files and not patch_metadata.untracked_files_omitted else "  - none")
        lines.append("- Protected changed files:")
        if patch_metadata.protected_changed_files:
            lines.extend(f"  - {path}" for path in patch_metadata.protected_changed_files)
        else:
            lines.append("  - none")
        lines.append("- Protected dirty files after run:")
        if patch_metadata.protected_after_files:
            lines.extend(f"  - {path}" for path in patch_metadata.protected_after_files)
            lines.append("Warning: protected paths became dirty during this run. Review manually; this run is marked failed.")
        else:
            lines.append("  - none")
        lines.append("- Untracked files:")
        if patch_metadata.untracked_files:
            lines.extend(f"  - {path}" for path in patch_metadata.untracked_files)
        else:
            lines.append("  - none")
        lines.append("- Untracked files omitted from patch:")
        if patch_metadata.untracked_files_omitted:
            lines.extend(f"  - {path}" for path in patch_metadata.untracked_files_omitted)
        else:
            lines.append("  - none")
        lines.extend(
            [
                "- Diff stat:",
                patch_metadata.diff_stat
                or ("  no changes" if not patch_metadata.untracked_files and not patch_metadata.untracked_files_omitted else "  see untracked files"),
            ]
        )
    if normalized_report.policy.violations:
        lines.extend(
            [
                "",
                "## Notes",
                "",
                f"- Policy warnings or violations were recorded in the evidence report: {len(normalized_report.policy.violations)}",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def write_pr_summary(
    issue: IssueTask,
    report: RunReport | dict[str, Any],
    output_path: str | Path,
    *,
    patch_path: str | Path | None = None,
    report_path: str | Path | None = None,
    manifest_path: str | Path | None = None,
    restore_plan_path: str | Path | None = None,
    repo_path: str | Path | None = None,
    effective_repo_path: str | Path | None = None,
    used_worktree: bool = False,
    worktree_path: str | Path | None = None,
    dirty_policy: str | None = None,
    baseline_dirty: bool | None = None,
    contains_preexisting_changes: bool | None = None,
    safety_decision: str | None = None,
    safety_reason: str | None = None,
    safety_warnings: list[str] | None = None,
    patch_metadata: PatchMetadata | None = None,
    redact_absolute_paths: bool = False,
) -> Path:
    """把 PR Summary Markdown 写入目标文件。"""

    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        render_pr_summary(
            issue,
            report,
            patch_path=patch_path,
            report_path=report_path,
            manifest_path=manifest_path,
            restore_plan_path=restore_plan_path,
            repo_path=repo_path,
            effective_repo_path=effective_repo_path,
            used_worktree=used_worktree,
            worktree_path=worktree_path,
            dirty_policy=dirty_policy,
            baseline_dirty=baseline_dirty,
            contains_preexisting_changes=contains_preexisting_changes,
            safety_decision=safety_decision,
            safety_reason=safety_reason,
            safety_warnings=safety_warnings,
            patch_metadata=patch_metadata,
            redact_absolute_paths=redact_absolute_paths,
        ),
        encoding="utf-8",
    )
    return path
