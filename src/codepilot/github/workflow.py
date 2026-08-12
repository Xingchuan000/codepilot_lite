from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from codepilot.agent.loop import AgentRunResult
from codepilot.agent.runner import run_agent_task
from codepilot.github.issue_loader import load_issue_from_file, load_issue_from_github
from codepilot.github.issue_models import IssueTask
from codepilot.github.patch_exporter import export_patch_with_metadata, remove_protected_patch_content
from codepilot.github.pr_summary import write_pr_summary
from codepilot.github.task_builder import build_agent_task_from_issue
from codepilot.repo.git_utils import get_head_sha
from codepilot.repo.manifest import build_artifact_manifest, write_artifact_manifest_two_phase
from codepilot.repo.patch_metadata import compute_patch_metadata
from codepilot.repo.models import CleanupResult, DirtyPolicy, PatchMetadata, RepoSafetyConfig, RepoSafetyResult, RepoStateSnapshot, WorktreeInfo
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



class IssueWorkflowPhaseError(RuntimeError):
    def __init__(self, *, phase: str, status: str, reason: str) -> None:
        super().__init__(reason)
        self.phase = phase
        self.status = status
        self.reason = reason


@dataclass
class IssueWorkflowContext:
    issue: IssueTask
    repo_path: Path
    runs_root: Path
    run_dir: Path
    resolved_run_id: str
    issue_json_path: Path
    safety_config: RepoSafetyConfig
    dirty_policy: DirtyPolicy
    policy_mode: Literal["read_only", "build", "danger"]
    fake_responses: str | Path | None
    max_steps: int | None
    generate_report_markdown: bool
    export_json_report: bool
    github_token: str | None
    worktree: bool
    worktree_base_dir: str | Path | None
    keep_worktree: bool
    cleanup_worktree: bool
    write_manifest: bool
    write_restore_plan: bool
    redact_absolute_paths: bool
    effective_repo_path: Path
    warnings: list[str] = field(default_factory=list)
    safety_result: RepoSafetyResult | None = None
    worktree_info: WorktreeInfo | None = None
    trace_path: Path | None = None
    report_path: Path | None = None
    report_json_path: Path | None = None
    report: dict[str, object] | None = None
    patch_path: Path | None = None
    patch_metadata: PatchMetadata | None = None
    after: RepoStateSnapshot | None = None
    original_after: RepoStateSnapshot | None = None
    cleanup_result: CleanupResult | None = None
    restore_plan_path: Path | None = None
    manifest_path: Path | None = None
    status: str = "agent_run_failed"
    success: bool = False


@dataclass(frozen=True)
class AgentPhaseResult:
    result: AgentRunResult
    report: dict[str, object]


@dataclass(frozen=True)
class PatchPhaseResult:
    path: Path
    metadata: PatchMetadata


@dataclass(frozen=True)
class SafetyPhaseResult:
    after: RepoStateSnapshot | None
    original_after: RepoStateSnapshot | None


def _build_issue_context(
    *,
    issue_file: str | Path | None,
    issue_url: str | None,
    repo: str | Path,
    run_id: str | None,
    runs_dir: str | Path,
    policy_mode: Literal["read_only", "build", "danger"],
    fake_responses: str | Path | None,
    max_steps: int | None,
    generate_report_markdown: bool,
    export_json_report: bool,
    github_token: str | None,
    dirty_policy: DirtyPolicy,
    worktree: bool,
    worktree_base_dir: str | Path | None,
    keep_worktree: bool,
    cleanup_worktree: bool,
    write_manifest: bool,
    write_restore_plan: bool,
    require_clean_source_for_worktree: bool,
    worktree_branch_prefix: str,
    redact_absolute_paths: bool,
    overwrite: bool,
) -> IssueWorkflowContext:
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

    issue = load_issue_from_file(issue_file) if issue_file is not None else load_issue_from_github(issue_url, token=github_token)
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
    return IssueWorkflowContext(
        issue=issue,
        repo_path=repo_path,
        runs_root=runs_root,
        run_dir=run_dir,
        resolved_run_id=resolved_run_id,
        issue_json_path=issue_json_path,
        safety_config=safety_config,
        dirty_policy=dirty_policy,
        policy_mode=policy_mode,
        fake_responses=fake_responses,
        max_steps=max_steps,
        generate_report_markdown=generate_report_markdown,
        export_json_report=export_json_report,
        github_token=github_token,
        worktree=worktree,
        worktree_base_dir=worktree_base_dir,
        keep_worktree=keep_worktree,
        cleanup_worktree=cleanup_worktree,
        write_manifest=write_manifest,
        write_restore_plan=write_restore_plan,
        redact_absolute_paths=redact_absolute_paths,
        effective_repo_path=repo_path,
    )


def _prepare_issue(ctx: IssueWorkflowContext) -> str:
    ctx.safety_result = check_repo_safety(ctx.repo_path, config=ctx.safety_config)
    ctx.warnings.extend(ctx.safety_result.warnings)
    if ctx.safety_result.decision == "deny":
        if ctx.safety_result.reason is not None:
            ctx.warnings.insert(0, ctx.safety_result.reason)
        raise IssueWorkflowPhaseError(
            phase="prepare",
            status="repo_safety_denied",
            reason=ctx.safety_result.reason or "Repo safety denied before agent run.",
        )
    if ctx.worktree:
        try:
            ctx.worktree_info = create_issue_worktree(
                ctx.repo_path,
                run_id=ctx.resolved_run_id,
                base_dir=ctx.worktree_base_dir,
                branch_prefix=ctx.safety_config.worktree_branch_prefix,
            )
        except Exception as exc:
            raise IssueWorkflowPhaseError(
                phase="prepare",
                status="worktree_creation_failed",
                reason=f"worktree creation failed: {exc}",
            ) from exc
        ctx.effective_repo_path = ctx.worktree_info.worktree_path
        if ctx.safety_result.before is not None and ctx.safety_result.before.is_dirty:
            ctx.warnings.append("Original repo had uncommitted changes. Worktree was created from HEAD only.")
    return build_agent_task_from_issue(ctx.issue)


def _run_agent_phase(ctx: IssueWorkflowContext, task: str) -> AgentPhaseResult:
    try:
        result = run_agent_task(
            task=task,
            repo=ctx.effective_repo_path,
            max_steps=12 if ctx.max_steps is None else ctx.max_steps,
            policy_mode=ctx.policy_mode,
            fake_responses=ctx.fake_responses,
            runs_dir=ctx.runs_root,
            run_id=ctx.resolved_run_id,
        )
        ctx.trace_path = Path(result.trace_path) if result.trace_path is not None else ctx.run_dir / "trace.jsonl"
        if ctx.generate_report_markdown:
            ctx.report_path, report = generate_report(
                ctx.trace_path,
                ctx.run_dir / "report.md",
                write_json=ctx.export_json_report,
                overwrite=True,
            )
            ctx.report_json_path = ctx.report_path.with_suffix(".json") if ctx.export_json_report else None
        else:
            report = {
                "run_id": ctx.resolved_run_id,
                "status": result.status,
                "success": result.success,
                "final_summary": result.summary,
                "changed_files": list(result.outcome.changed_files),
                "tests": {"status": result.outcome.last_test_status},
            }
        ctx.report = report
        ctx.status = result.status
        ctx.success = result.success
        return AgentPhaseResult(result=result, report=report)
    except Exception as exc:
        if ctx.trace_path is None:
            candidate_trace = ctx.run_dir / "trace.jsonl"
            ctx.trace_path = candidate_trace if candidate_trace.exists() else None
        raise IssueWorkflowPhaseError(
            phase="execute",
            status="agent_run_failed",
            reason=f"agent run failed: {exc}",
        ) from exc


def _export_patch_phase(ctx: IssueWorkflowContext) -> PatchPhaseResult:
    before = ctx.safety_result.before if ctx.safety_result is not None else None
    try:
        patch_path, metadata = export_patch_with_metadata(
            ctx.effective_repo_path,
            ctx.run_dir / "changes.patch",
            base_head_sha=before.head_sha if before else None,
            effective_head_sha=get_head_sha(ctx.effective_repo_path),
            baseline_dirty=ctx.safety_result.baseline_dirty if ctx.safety_result is not None else False,
            contains_preexisting_changes=ctx.safety_result.contains_preexisting_changes if ctx.safety_result is not None else None,
            protected_paths=ctx.safety_config.protected_paths,
            protected_after_files=[],
        )
    except Exception as exc:
        raise IssueWorkflowPhaseError(
            phase="persist",
            status="patch_export_failed",
            reason=f"patch export failed: {exc}",
        ) from exc
    ctx.patch_path = patch_path
    ctx.patch_metadata = metadata
    return PatchPhaseResult(path=patch_path, metadata=metadata)


def _verify_repo_safety_phase(ctx: IssueWorkflowContext, patch: PatchPhaseResult) -> SafetyPhaseResult:
    try:
        ctx.after = snapshot_repo_state(ctx.effective_repo_path, protected_paths=ctx.safety_config.protected_paths)
        if ctx.worktree:
            ctx.original_after = snapshot_repo_state(ctx.repo_path, protected_paths=ctx.safety_config.protected_paths)
    except Exception as exc:
        ctx.warnings.append(f"after snapshot failed: {exc}")

    if ctx.patch_metadata is not None and ctx.patch_metadata.protected_changed_files:
        ctx.status = "protected_patch_path_denied"
        ctx.success = False
        ctx.warnings.append("Protected patch path detected: " + ", ".join(ctx.patch_metadata.protected_changed_files))
    if ctx.after is not None and ctx.after.protected_dirty_files:
        ctx.status = "protected_after_path_denied"
        ctx.success = False
        ctx.warnings.append("Protected dirty path detected after agent run: " + ", ".join(ctx.after.protected_dirty_files))

    protected_paths_to_remove: list[str] = []
    if ctx.patch_metadata is not None:
        protected_paths_to_remove.extend(ctx.patch_metadata.protected_changed_files)
    if ctx.after is not None:
        protected_paths_to_remove.extend(path for path in ctx.after.protected_dirty_files if path not in protected_paths_to_remove)
    if ctx.patch_metadata is not None and protected_paths_to_remove:
        remove_protected_patch_content(ctx.patch_metadata.patch_path, excluded_paths=protected_paths_to_remove)
        before = ctx.safety_result.before if ctx.safety_result is not None else None
        sanitized_metadata = compute_patch_metadata(
            ctx.effective_repo_path,
            ctx.patch_metadata.patch_path,
            base_head_sha=before.head_sha if before else None,
            effective_head_sha=get_head_sha(ctx.effective_repo_path),
            baseline_dirty=ctx.safety_result.baseline_dirty if ctx.safety_result is not None else False,
            contains_preexisting_changes=ctx.safety_result.contains_preexisting_changes if ctx.safety_result is not None else None,
            protected_paths=ctx.safety_config.protected_paths,
            protected_after_files=[] if ctx.after is None else list(ctx.after.protected_dirty_files),
        )
        ctx.patch_metadata = PatchMetadata(
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
            protected_after_files=[] if ctx.after is None else list(ctx.after.protected_dirty_files),
        )
    return SafetyPhaseResult(after=ctx.after, original_after=ctx.original_after)


def _write_restore_plan(ctx: IssueWorkflowContext) -> None:
    if not ctx.write_restore_plan or ctx.safety_result is None:
        return
    try:
        ctx.restore_plan_path = write_restore_plan_file(
            run_id=ctx.resolved_run_id,
            repo_path=ctx.repo_path,
            effective_repo_path=ctx.effective_repo_path,
            used_worktree=ctx.worktree_info is not None,
            worktree_path=None if ctx.worktree_info is None else ctx.worktree_info.worktree_path,
            baseline_dirty=ctx.safety_result.baseline_dirty,
            patch_metadata=ctx.patch_metadata,
            cleanup_result=ctx.cleanup_result,
            output_path=ctx.run_dir / "restore_plan.md",
            redact_absolute_paths=ctx.redact_absolute_paths,
        )
    except Exception as exc:
        ctx.warnings.append(f"restore plan generation failed: {exc}")


def _write_success_manifest(ctx: IssueWorkflowContext, pr_summary_path: Path) -> None:
    if not ctx.write_manifest or ctx.safety_result is None:
        return
    manifest_target_path = ctx.run_dir / "artifact_manifest.json"
    try:
        ctx.manifest_path = write_artifact_manifest_two_phase(
            build_artifact_manifest(
                run_id=ctx.resolved_run_id,
                run_dir=ctx.run_dir,
                status=ctx.status,
                success=ctx.success,
                repo_path=ctx.repo_path,
                effective_repo_path=ctx.effective_repo_path,
                used_worktree=ctx.worktree_info is not None,
                worktree_path=None if ctx.worktree_info is None else ctx.worktree_info.worktree_path,
                safety_result=ctx.safety_result,
                before=ctx.safety_result.before,
                after=ctx.after,
                original_after=ctx.original_after,
                patch_metadata=ctx.patch_metadata,
                cleanup_result=ctx.cleanup_result,
                artifact_paths={
                    "issue_json": ctx.issue_json_path,
                    "trace": ctx.trace_path,
                    "report_md": ctx.report_path,
                    "report_json": ctx.report_json_path,
                    "patch": ctx.patch_path,
                    "pr_summary": pr_summary_path,
                    "restore_plan": ctx.restore_plan_path,
                    "artifact_manifest": manifest_target_path,
                },
                redact_absolute_paths=ctx.redact_absolute_paths,
            ),
            manifest_target_path,
        )
    except Exception as exc:
        ctx.warnings.append(f"manifest generation failed: {exc}")


def _result_from_context(ctx: IssueWorkflowContext, *, pr_summary_path: Path | None) -> IssueWorkflowResult:
    return IssueWorkflowResult(
        run_id=ctx.resolved_run_id,
        run_dir=ctx.run_dir,
        issue_json_path=ctx.issue_json_path,
        trace_path=ctx.trace_path,
        report_path=ctx.report_path,
        report_json_path=ctx.report_json_path,
        patch_path=ctx.patch_path,
        pr_summary_path=pr_summary_path,
        manifest_path=ctx.manifest_path,
        restore_plan_path=ctx.restore_plan_path,
        repo_path=ctx.repo_path,
        effective_repo_path=ctx.effective_repo_path,
        worktree_path=None if ctx.worktree_info is None else ctx.worktree_info.worktree_path,
        used_worktree=ctx.worktree_info is not None,
        status=ctx.status,
        success=ctx.success,
        warnings=ctx.warnings,
    )


def _finalize_failure(ctx: IssueWorkflowContext, error: IssueWorkflowPhaseError) -> IssueWorkflowResult:
    _write_restore_plan(ctx)
    safety_result = ctx.safety_result
    if safety_result is None:
        safety_result = RepoSafetyResult(decision="deny", reason=error.reason)
    pr_summary_path, ctx.manifest_path, ctx.warnings = _write_failure_artifacts(
        issue=ctx.issue,
        run_id=ctx.resolved_run_id,
        run_dir=ctx.run_dir,
        repo_path=ctx.repo_path,
        effective_repo_path=ctx.effective_repo_path,
        used_worktree=ctx.worktree_info is not None,
        worktree_path=None if ctx.worktree_info is None else ctx.worktree_info.worktree_path,
        safety_result=safety_result,
        dirty_policy=ctx.dirty_policy,
        status=error.status,
        reason=error.reason,
        trace_path=ctx.trace_path,
        report_path=ctx.report_path,
        report_json_path=ctx.report_json_path,
        patch_path=ctx.patch_path,
        patch_metadata=ctx.patch_metadata,
        before=safety_result.before,
        after=ctx.after,
        original_after=ctx.original_after,
        restore_plan_path=ctx.restore_plan_path,
        cleanup_result=ctx.cleanup_result,
        write_manifest=ctx.write_manifest,
        redact_absolute_paths=ctx.redact_absolute_paths,
        warnings=ctx.warnings,
    )
    ctx.status = error.status
    ctx.success = False
    return _result_from_context(ctx, pr_summary_path=pr_summary_path)


def _finalize_success(ctx: IssueWorkflowContext) -> IssueWorkflowResult:
    if ctx.cleanup_worktree and ctx.worktree_info is not None:
        ctx.cleanup_result = remove_issue_worktree(
            ctx.worktree_info.worktree_path,
            original_repo=ctx.repo_path,
            branch_name=ctx.worktree_info.branch_name,
        )
        if ctx.cleanup_result.success is False:
            ctx.warnings.append(f"worktree cleanup failed: {ctx.cleanup_result.reason}")
    _write_restore_plan(ctx)
    manifest_target_path = ctx.run_dir / "artifact_manifest.json"
    pr_summary_path = write_pr_summary(
        ctx.issue,
        ctx.report or {},
        ctx.run_dir / "pr_summary.md",
        patch_path=ctx.patch_path,
        report_path=ctx.report_path,
        manifest_path=manifest_target_path if ctx.write_manifest else None,
        restore_plan_path=ctx.restore_plan_path,
        repo_path=ctx.repo_path,
        effective_repo_path=ctx.effective_repo_path,
        used_worktree=ctx.worktree_info is not None,
        worktree_path=None if ctx.worktree_info is None else ctx.worktree_info.worktree_path,
        dirty_policy=ctx.dirty_policy,
        baseline_dirty=ctx.safety_result.baseline_dirty if ctx.safety_result is not None else False,
        contains_preexisting_changes=ctx.safety_result.contains_preexisting_changes if ctx.safety_result is not None else None,
        safety_decision=ctx.safety_result.decision if ctx.safety_result is not None else "deny",
        safety_reason=ctx.safety_result.reason if ctx.safety_result is not None else None,
        safety_warnings=ctx.warnings,
        patch_metadata=ctx.patch_metadata,
        redact_absolute_paths=ctx.redact_absolute_paths,
    )
    _write_success_manifest(ctx, pr_summary_path)
    return _result_from_context(ctx, pr_summary_path=pr_summary_path)


def run_issue_workflow(
    *,
    issue_file: str | Path | None = None,
    issue_url: str | None = None,
    repo: str | Path,
    run_id: str | None = None,
    runs_dir: str | Path = "runs",
    policy_mode: Literal["read_only", "build", "danger"] = "build",
    fake_responses: str | Path | None = None,
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
    ctx = _build_issue_context(
        issue_file=issue_file,
        issue_url=issue_url,
        repo=repo,
        run_id=run_id,
        runs_dir=runs_dir,
        policy_mode=policy_mode,
        fake_responses=fake_responses,
        max_steps=max_steps,
        generate_report_markdown=generate_report_markdown,
        export_json_report=export_json_report,
        github_token=github_token,
        dirty_policy=dirty_policy,
        worktree=worktree,
        worktree_base_dir=worktree_base_dir,
        keep_worktree=keep_worktree,
        cleanup_worktree=cleanup_worktree,
        write_manifest=write_manifest,
        write_restore_plan=write_restore_plan,
        require_clean_source_for_worktree=require_clean_source_for_worktree,
        worktree_branch_prefix=worktree_branch_prefix,
        redact_absolute_paths=redact_absolute_paths,
        overwrite=overwrite,
    )
    try:
        task = _prepare_issue(ctx)
        agent = _run_agent_phase(ctx, task)
        patch = _export_patch_phase(ctx)
        _verify_repo_safety_phase(ctx, patch)
    except IssueWorkflowPhaseError as exc:
        return _finalize_failure(ctx, exc)
    return _finalize_success(ctx)
