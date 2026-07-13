from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from codepilot.agent.runner import run_agent_task
from codepilot.github.issue_loader import load_issue_from_file, load_issue_from_github
from codepilot.github.issue_models import IssueTask
from codepilot.github.patch_exporter import export_patch_with_metadata, remove_protected_patch_content
from codepilot.github.pr_summary import write_pr_summary
from codepilot.github.task_builder import build_agent_task_from_issue
from codepilot.repo.git_utils import get_head_sha
from codepilot.repo.manifest import build_artifact_manifest, write_artifact_manifest_two_phase
from codepilot.repo.patch_metadata import compute_patch_metadata
from codepilot.repo.models import CleanupResult, DirtyPolicy, PatchMetadata, RepoSafetyConfig, RepoSafetyResult, RepoStateSnapshot
from codepilot.repo.restore import write_restore_plan as write_restore_plan_file
from codepilot.repo.safety import check_repo_safety, snapshot_repo_state
from codepilot.repo.worktree import create_issue_worktree, remove_issue_worktree
from codepilot.report.generator import generate_report
from codepilot.trace.logger import make_run_id


@dataclass(frozen=True)
class IssueWorkflowResult:
    run_id: str
    run_dir: Path
    issue_json_path: Path
    trace_path: Path | None
    report_path: Path | None
    report_json_path: Path | None
    patch_path: Path | None
    pr_summary_path: Path | None
    manifest_path: Path | None = None
    restore_plan_path: Path | None = None
    repo_path: Path | None = None
    effective_repo_path: Path | None = None
    worktree_path: Path | None = None
    used_worktree: bool = False
    status: str | None = None
    success: bool | None = None
    warnings: list[str] = field(default_factory=list)


def _known_artifact_paths(run_dir: Path) -> list[Path]:
    """集中列出第十步会产生的固定产物，方便 overwrite 时精确删除。"""

    return [
        run_dir / "issue.json",
        run_dir / "trace.jsonl",
        run_dir / "report.md",
        run_dir / "report.json",
        run_dir / "changes.patch",
        run_dir / "pr_summary.md",
        run_dir / "artifact_manifest.json",
        run_dir / "restore_plan.md",
        run_dir / "pr_body.md",
        run_dir / "manual_pr_commands.md",
        run_dir / "review_checklist.md",
        run_dir / "github_action_template.yml",
        run_dir / "pr_assist_manifest.json",
    ]


def _build_failure_report(*, run_id: str, status: str, reason: str) -> dict[str, object]:
    """统一构造 failure report，避免异常路径缺少最小摘要字段。"""

    return {
        "run_id": run_id,
        "status": status,
        "success": False,
        "final_summary": reason,
        "changed_files": [],
        "tests": {"status": "skipped", "command": "not run", "summary": reason},
        "policy": {"violations": []},
    }


def _write_failure_artifacts(
    *,
    issue: IssueTask,
    run_id: str,
    run_dir: Path,
    repo_path: Path,
    effective_repo_path: Path,
    used_worktree: bool,
    worktree_path: Path | None,
    safety_result: RepoSafetyResult,
    dirty_policy: DirtyPolicy,
    status: str,
    reason: str,
    trace_path: Path | None,
    report_path: Path | None,
    report_json_path: Path | None,
    patch_path: Path | None,
    patch_metadata: PatchMetadata | None,
    before: RepoStateSnapshot | None,
    after: RepoStateSnapshot | None,
    original_after: RepoStateSnapshot | None,
    restore_plan_path: Path | None,
    cleanup_result: CleanupResult | None,
    write_manifest: bool,
    redact_absolute_paths: bool,
    warnings: list[str],
) -> tuple[Path | None, Path | None, list[str]]:
    """统一写异常路径下的 pr_summary 和 manifest，尽量保留可审计产物。"""

    manifest_target_path = run_dir / "artifact_manifest.json"
    pr_summary_path = write_pr_summary(
        issue,
        _build_failure_report(run_id=run_id, status=status, reason=reason),
        run_dir / "pr_summary.md",
        patch_path=patch_path,
        report_path=report_path,
        manifest_path=manifest_target_path if write_manifest else None,
        restore_plan_path=restore_plan_path,
        repo_path=repo_path,
        effective_repo_path=effective_repo_path,
        used_worktree=used_worktree,
        worktree_path=worktree_path,
        dirty_policy=dirty_policy,
        baseline_dirty=safety_result.baseline_dirty,
        contains_preexisting_changes=safety_result.contains_preexisting_changes,
        safety_decision=safety_result.decision,
        safety_reason=reason,
        safety_warnings=warnings,
        patch_metadata=patch_metadata,
        redact_absolute_paths=redact_absolute_paths,
    )
    manifest_path: Path | None = None
    if write_manifest:
        try:
            manifest_path = write_artifact_manifest_two_phase(
                build_artifact_manifest(
                    run_id=run_id,
                    run_dir=run_dir,
                    status=status,
                    success=False,
                    repo_path=repo_path,
                    effective_repo_path=effective_repo_path,
                    used_worktree=used_worktree,
                    worktree_path=worktree_path,
                    safety_result=safety_result,
                    before=before,
                    after=after,
                    original_after=original_after,
                    patch_metadata=patch_metadata,
                    cleanup_result=cleanup_result,
                    artifact_paths={
                        "issue_json": run_dir / "issue.json",
                        "trace": trace_path,
                        "report_md": report_path,
                        "report_json": report_json_path,
                        "patch": patch_path,
                        "pr_summary": pr_summary_path,
                        "restore_plan": restore_plan_path,
                        "artifact_manifest": manifest_target_path,
                    },
                    redact_absolute_paths=redact_absolute_paths,
                ),
                manifest_target_path,
            )
        except Exception as exc:
            warnings = [*warnings, f"manifest generation failed: {exc}"]
    return pr_summary_path, manifest_path, warnings


def run_issue_workflow(
    *,
    issue_file: str | Path | None = None,
    issue_url: str | None = None,
    repo: str | Path,
    run_id: str | None = None,
    runs_dir: str | Path = "runs",
    policy_mode: Literal["read_only", "build", "danger"] = "build",
    approve: bool = False,
    fake_actions: str | Path | None = None,
    max_steps: int | None = None,
    generate_report_markdown: bool = True,
    export_json_report: bool = True,
    github_token: str | None = None,
    dirty_policy: DirtyPolicy = "fail",
    worktree: bool = False,
    worktree_base_dir: str | Path | None = None,
    keep_worktree: bool = True,
    cleanup_worktree: bool = False,
    write_manifest: bool = True,
    write_restore_plan: bool = True,
    require_clean_source_for_worktree: bool = False,
    worktree_branch_prefix: str = "codepilot",
    redact_absolute_paths: bool = False,
    overwrite: bool = False,
) -> IssueWorkflowResult:
    """执行 issue 输入到 agent 运行、报告导出、patch 导出和 PR 摘要生成的完整链路。"""

    if (issue_file is None) == (issue_url is None):
        raise ValueError("Provide exactly one of issue_file or issue_url.")

    repo_path = Path(repo).expanduser().resolve()
    if not repo_path.exists() or not repo_path.is_dir():
        raise ValueError(f"Repository path must be an existing directory: {repo_path}")
    if cleanup_worktree and not worktree:
        raise ValueError("cleanup_worktree requires worktree=True")

    resolved_run_id = run_id or make_run_id(prefix="issue")
    runs_root = Path(runs_dir).expanduser().resolve()
    run_dir = runs_root / resolved_run_id
    artifact_paths = _known_artifact_paths(run_dir)
    if run_dir.exists() and not overwrite and any(path.exists() for path in artifact_paths):
        raise FileExistsError(f"Run artifacts already exist: {run_dir}")
    if overwrite:
        for artifact_path in artifact_paths:
            if artifact_path.exists():
                artifact_path.unlink()

    issue = (
        load_issue_from_file(issue_file)
        if issue_file is not None
        else load_issue_from_github(issue_url, token=github_token)
    )
    run_dir.mkdir(parents=True, exist_ok=True)
    issue_json_path = run_dir / "issue.json"
    issue_json_path.write_text(issue.model_dump_json(indent=2), encoding="utf-8")

    safety_config = RepoSafetyConfig(
        dirty_policy=dirty_policy,
        worktree_mode="create" if worktree else "off",
        worktree_base_dir=Path(worktree_base_dir).expanduser().resolve() if worktree_base_dir else None,
        keep_worktree=keep_worktree,
        cleanup_worktree=cleanup_worktree,
        require_clean_source_for_worktree=require_clean_source_for_worktree,
        worktree_branch_prefix=worktree_branch_prefix,
        redact_absolute_paths=redact_absolute_paths,
    )
    safety_result = check_repo_safety(repo_path, config=safety_config)
    warnings = list(safety_result.warnings)
    effective_repo_path = repo_path
    worktree_info = None
    patch_metadata: PatchMetadata | None = None
    after: RepoStateSnapshot | None = None
    original_after: RepoStateSnapshot | None = None
    cleanup_result = None
    restore_plan_path: Path | None = None
    manifest_path: Path | None = None
    trace_path: Path | None = None
    manifest_target_path = run_dir / "artifact_manifest.json"

    if safety_result.decision == "deny":
        if safety_result.reason is not None:
            warnings = [safety_result.reason, *warnings]
        if write_restore_plan:
            restore_plan_path = write_restore_plan_file(
                run_id=resolved_run_id,
                repo_path=repo_path,
                effective_repo_path=effective_repo_path,
                used_worktree=False,
                worktree_path=None,
                baseline_dirty=safety_result.baseline_dirty,
                patch_metadata=None,
                output_path=run_dir / "restore_plan.md",
                redact_absolute_paths=redact_absolute_paths,
            )
        pr_summary_path, manifest_path, warnings = _write_failure_artifacts(
            issue=issue,
            run_id=resolved_run_id,
            run_dir=run_dir,
            repo_path=repo_path,
            effective_repo_path=effective_repo_path,
            used_worktree=False,
            worktree_path=None,
            safety_result=safety_result,
            dirty_policy=dirty_policy,
            status="repo_safety_denied",
            reason=safety_result.reason or "Repo safety denied before agent run.",
            trace_path=None,
            report_path=None,
            report_json_path=None,
            patch_path=None,
            patch_metadata=None,
            before=safety_result.before,
            after=None,
            original_after=None,
            restore_plan_path=restore_plan_path,
            cleanup_result=None,
            write_manifest=write_manifest,
            redact_absolute_paths=redact_absolute_paths,
            warnings=warnings,
        )
        return IssueWorkflowResult(
            run_id=resolved_run_id,
            run_dir=run_dir,
            issue_json_path=issue_json_path,
            trace_path=None,
            report_path=None,
            report_json_path=None,
            patch_path=None,
            pr_summary_path=pr_summary_path,
            manifest_path=manifest_path,
            restore_plan_path=restore_plan_path,
            repo_path=repo_path,
            effective_repo_path=effective_repo_path,
            used_worktree=False,
            status="repo_safety_denied",
            success=False,
            warnings=warnings,
        )

    if worktree:
        worktree_info = create_issue_worktree(
            repo_path,
            run_id=resolved_run_id,
            base_dir=worktree_base_dir,
            branch_prefix=worktree_branch_prefix,
        )
        effective_repo_path = worktree_info.worktree_path
        if safety_result.before is not None and safety_result.before.is_dirty:
            warnings.append("Original repo had uncommitted changes. Worktree was created from HEAD only.")

    task = build_agent_task_from_issue(issue)
    report_path: Path | None = None
    report_json_path: Path | None = None
    patch_path: Path | None = None
    status = "agent_run_failed"
    success = False
    try:
        agent_result = run_agent_task(
            task=task,
            repo=effective_repo_path,
            max_steps=12 if max_steps is None else max_steps,
            policy_mode=policy_mode,
            approve=approve,
            fake_actions=fake_actions,
            runs_dir=runs_root,
            run_id=resolved_run_id,
        )
        trace_path = Path(agent_result.trace_path) if agent_result.trace_path is not None else run_dir / "trace.jsonl"
        if generate_report_markdown:
            report_path, report = generate_report(
                trace_path,
                run_dir / "report.md",
                write_json=export_json_report,
                overwrite=True,
            )
            report_json_path = report_path.with_suffix(".json") if export_json_report else None
        else:
            report = {
                "run_id": resolved_run_id,
                "status": agent_result.status,
                "success": agent_result.success,
                "final_summary": agent_result.summary,
                "changed_files": list(agent_result.outcome.changed_files),
                "tests": {"status": agent_result.outcome.last_test_status},
            }
        status = agent_result.status
        success = agent_result.success
    except Exception as exc:
        warnings.append(f"agent run failed: {exc}")
        if trace_path is None:
            candidate_trace = run_dir / "trace.jsonl"
            trace_path = candidate_trace if candidate_trace.exists() else None
        if write_restore_plan:
            try:
                restore_plan_path = write_restore_plan_file(
                    run_id=resolved_run_id,
                    repo_path=repo_path,
                    effective_repo_path=effective_repo_path,
                    used_worktree=worktree_info is not None,
                    worktree_path=None if worktree_info is None else worktree_info.worktree_path,
                    baseline_dirty=safety_result.baseline_dirty,
                    patch_metadata=None,
                    cleanup_result=cleanup_result,
                    output_path=run_dir / "restore_plan.md",
                    redact_absolute_paths=redact_absolute_paths,
                )
            except Exception as restore_exc:
                warnings.append(f"restore plan generation failed: {restore_exc}")
        pr_summary_path, manifest_path, warnings = _write_failure_artifacts(
            issue=issue,
            run_id=resolved_run_id,
            run_dir=run_dir,
            repo_path=repo_path,
            effective_repo_path=effective_repo_path,
            used_worktree=worktree_info is not None,
            worktree_path=None if worktree_info is None else worktree_info.worktree_path,
            safety_result=safety_result,
            dirty_policy=dirty_policy,
            status="agent_run_failed",
            reason=f"agent run failed: {exc}",
            trace_path=trace_path,
            report_path=report_path,
            report_json_path=report_json_path,
            patch_path=None,
            patch_metadata=None,
            before=safety_result.before,
            after=None,
            original_after=None,
            restore_plan_path=restore_plan_path,
            cleanup_result=cleanup_result,
            write_manifest=write_manifest,
            redact_absolute_paths=redact_absolute_paths,
            warnings=warnings,
        )
        return IssueWorkflowResult(
            run_id=resolved_run_id,
            run_dir=run_dir,
            issue_json_path=issue_json_path,
            trace_path=trace_path,
            report_path=report_path,
            report_json_path=report_json_path,
            patch_path=None,
            pr_summary_path=pr_summary_path,
            manifest_path=manifest_path,
            restore_plan_path=restore_plan_path,
            repo_path=repo_path,
            effective_repo_path=effective_repo_path,
            worktree_path=None if worktree_info is None else worktree_info.worktree_path,
            used_worktree=worktree_info is not None,
            status="agent_run_failed",
            success=False,
            warnings=warnings,
        )
    try:
        patch_path, patch_metadata = export_patch_with_metadata(
            effective_repo_path,
            run_dir / "changes.patch",
            base_head_sha=safety_result.before.head_sha if safety_result.before else None,
            effective_head_sha=get_head_sha(effective_repo_path),
            baseline_dirty=safety_result.baseline_dirty,
            contains_preexisting_changes=safety_result.contains_preexisting_changes,
            protected_paths=safety_config.protected_paths,
            protected_after_files=[],
        )
    except Exception as exc:
        status = "patch_export_failed"
        success = False
        warnings.append(f"patch export failed: {exc}")
        if write_restore_plan:
            try:
                restore_plan_path = write_restore_plan_file(
                    run_id=resolved_run_id,
                    repo_path=repo_path,
                    effective_repo_path=effective_repo_path,
                    used_worktree=worktree_info is not None,
                    worktree_path=None if worktree_info is None else worktree_info.worktree_path,
                    baseline_dirty=safety_result.baseline_dirty,
                    patch_metadata=None,
                    cleanup_result=cleanup_result,
                    output_path=run_dir / "restore_plan.md",
                    redact_absolute_paths=redact_absolute_paths,
                )
            except Exception as restore_exc:
                warnings.append(f"restore plan generation failed: {restore_exc}")
        pr_summary_path, manifest_path, warnings = _write_failure_artifacts(
            issue=issue,
            run_id=resolved_run_id,
            run_dir=run_dir,
            repo_path=repo_path,
            effective_repo_path=effective_repo_path,
            used_worktree=worktree_info is not None,
            worktree_path=None if worktree_info is None else worktree_info.worktree_path,
            safety_result=safety_result,
            dirty_policy=dirty_policy,
            status="patch_export_failed",
            reason=f"patch export failed: {exc}",
            trace_path=trace_path,
            report_path=report_path,
            report_json_path=report_json_path,
            patch_path=None,
            patch_metadata=None,
            before=safety_result.before,
            after=None,
            original_after=None,
            restore_plan_path=restore_plan_path,
            cleanup_result=cleanup_result,
            write_manifest=write_manifest,
            redact_absolute_paths=redact_absolute_paths,
            warnings=warnings,
        )
        return IssueWorkflowResult(
            run_id=resolved_run_id,
            run_dir=run_dir,
            issue_json_path=issue_json_path,
            trace_path=trace_path,
            report_path=report_path,
            report_json_path=report_json_path,
            patch_path=None,
            pr_summary_path=pr_summary_path,
            manifest_path=manifest_path,
            restore_plan_path=restore_plan_path,
            repo_path=repo_path,
            effective_repo_path=effective_repo_path,
            worktree_path=None if worktree_info is None else worktree_info.worktree_path,
            used_worktree=worktree_info is not None,
            status="patch_export_failed",
            success=False,
            warnings=warnings,
        )
    if patch_metadata.protected_changed_files:
        status = "protected_patch_path_denied"
        success = False
        warnings.append(
            "Protected patch path detected: " + ", ".join(patch_metadata.protected_changed_files)
        )
    try:
        after = snapshot_repo_state(effective_repo_path, protected_paths=safety_config.protected_paths)
        if worktree:
            original_after = snapshot_repo_state(repo_path, protected_paths=safety_config.protected_paths)
    except Exception as exc:
        warnings.append(f"after snapshot failed: {exc}")
    if after is not None and after.protected_dirty_files:
        status = "protected_after_path_denied"
        success = False
        warnings.append("Protected dirty path detected after agent run: " + ", ".join(after.protected_dirty_files))
    protected_paths_to_remove: list[str] = []
    if patch_metadata is not None:
        protected_paths_to_remove.extend(patch_metadata.protected_changed_files)
    if after is not None:
        for path in after.protected_dirty_files:
            if path not in protected_paths_to_remove:
                protected_paths_to_remove.append(path)
    if patch_metadata is not None and protected_paths_to_remove:
        remove_protected_patch_content(patch_metadata.patch_path, excluded_paths=protected_paths_to_remove)
        sanitized_metadata = compute_patch_metadata(
            effective_repo_path,
            patch_metadata.patch_path,
            base_head_sha=safety_result.before.head_sha if safety_result.before else None,
            effective_head_sha=get_head_sha(effective_repo_path),
            baseline_dirty=safety_result.baseline_dirty,
            contains_preexisting_changes=safety_result.contains_preexisting_changes,
            protected_paths=safety_config.protected_paths,
            protected_after_files=[] if after is None else list(after.protected_dirty_files),
        )
        patch_metadata = PatchMetadata(
            patch_path=sanitized_metadata.patch_path,
            is_empty=sanitized_metadata.is_empty,
            size_bytes=sanitized_metadata.size_bytes,
            sha256=sanitized_metadata.sha256,
            changed_files=sanitized_metadata.changed_files,
            diff_stat=sanitized_metadata.diff_stat,
            base_head_sha=sanitized_metadata.base_head_sha,
            effective_head_sha=sanitized_metadata.effective_head_sha,
            baseline_dirty=sanitized_metadata.baseline_dirty,
            contains_preexisting_changes=sanitized_metadata.contains_preexisting_changes,
            generated_from_repo=sanitized_metadata.generated_from_repo,
            protected_changed_files=protected_paths_to_remove,
            untracked_files=sanitized_metadata.untracked_files,
            untracked_files_omitted=sanitized_metadata.untracked_files_omitted,
            protected_after_files=[] if after is None else list(after.protected_dirty_files),
        )
    if cleanup_worktree and worktree_info is not None:
        cleanup_result = remove_issue_worktree(
            worktree_info.worktree_path,
            original_repo=repo_path,
            branch_name=worktree_info.branch_name,
        )
        if cleanup_result.success is False:
            warnings.append(f"worktree cleanup failed: {cleanup_result.reason}")
    if write_restore_plan:
        try:
            restore_plan_path = write_restore_plan_file(
                run_id=resolved_run_id,
                repo_path=repo_path,
                effective_repo_path=effective_repo_path,
                used_worktree=worktree_info is not None,
                worktree_path=None if worktree_info is None else worktree_info.worktree_path,
                baseline_dirty=safety_result.baseline_dirty,
                patch_metadata=patch_metadata,
                cleanup_result=cleanup_result,
                output_path=run_dir / "restore_plan.md",
                redact_absolute_paths=redact_absolute_paths,
            )
        except Exception as exc:
            warnings.append(f"restore plan generation failed: {exc}")
    pr_summary_path = write_pr_summary(
        issue,
        report,
        run_dir / "pr_summary.md",
        patch_path=patch_path,
        report_path=report_path,
        manifest_path=manifest_target_path if write_manifest else None,
        restore_plan_path=restore_plan_path,
        repo_path=repo_path,
        effective_repo_path=effective_repo_path,
        used_worktree=worktree_info is not None,
        worktree_path=None if worktree_info is None else worktree_info.worktree_path,
        dirty_policy=dirty_policy,
        baseline_dirty=safety_result.baseline_dirty,
        contains_preexisting_changes=safety_result.contains_preexisting_changes,
        safety_decision=safety_result.decision,
        safety_reason=safety_result.reason,
        safety_warnings=warnings,
        patch_metadata=patch_metadata,
        redact_absolute_paths=redact_absolute_paths,
    )
    if write_manifest:
        try:
            manifest_path = write_artifact_manifest_two_phase(
                build_artifact_manifest(
                    run_id=resolved_run_id,
                    run_dir=run_dir,
                    status=status,
                    success=success,
                    repo_path=repo_path,
                    effective_repo_path=effective_repo_path,
                    used_worktree=worktree_info is not None,
                    worktree_path=None if worktree_info is None else worktree_info.worktree_path,
                    safety_result=safety_result,
                    before=safety_result.before,
                    after=after,
                    original_after=original_after,
                    patch_metadata=patch_metadata,
                    cleanup_result=cleanup_result,
                    artifact_paths={
                        "issue_json": issue_json_path,
                        "trace": trace_path,
                        "report_md": report_path,
                        "report_json": report_json_path,
                        "patch": patch_path,
                        "pr_summary": pr_summary_path,
                        "restore_plan": restore_plan_path,
                        "artifact_manifest": manifest_target_path,
                    },
                    redact_absolute_paths=redact_absolute_paths,
                ),
                manifest_target_path,
            )
        except Exception as exc:
            warnings.append(f"manifest generation failed: {exc}")
    return IssueWorkflowResult(
        run_id=resolved_run_id,
        run_dir=run_dir,
        issue_json_path=issue_json_path,
        trace_path=trace_path,
        report_path=report_path,
        report_json_path=report_json_path,
        patch_path=patch_path,
        pr_summary_path=pr_summary_path,
        manifest_path=manifest_path,
        restore_plan_path=restore_plan_path,
        repo_path=repo_path,
        effective_repo_path=effective_repo_path,
        worktree_path=None if worktree_info is None else worktree_info.worktree_path,
        used_worktree=worktree_info is not None,
        status=status,
        success=success,
        warnings=warnings,
    )
