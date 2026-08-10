from __future__ import annotations

"""第十四步 PR feedback / PR review loop 主 workflow。"""

import json
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic, sleep
from typing import Any

from codepilot.agent.runner import run_agent_task
from codepilot.auto_pr.models import AutoPRManifestInvalidError
from codepilot.github.patch_exporter import export_patch_with_metadata
from codepilot.pr_feedback.branch_update import prepare_followup_commit, push_pr_branch_update_if_allowed
from codepilot.pr_feedback.checks import collect_pr_checks_degraded, has_pending_checks, summarize_check_state
from codepilot.pr_feedback.freshness import assert_controlled_head_branch, assert_fresh_head_for_execute, build_feedback_freshness, resolve_current_pr_head
from codepilot.pr_feedback.followup_attempt import copy_followup_task_to_attempt, create_followup_attempt, write_followup_attempt_manifest
from codepilot.pr_feedback.github_action import write_pr_feedback_workflow_template
from codepilot.pr_feedback.github_client import PRFeedbackGitHubClientProtocol, PRFeedbackGitHubError, RestPRFeedbackGitHubClient, is_github_token_available, redact_feedback_text
from codepilot.pr_feedback.logs import collect_failed_ci_logs
from codepilot.pr_feedback.manifest_loader import load_auto_pr_manifest, load_source_manifests_for_feedback, resolve_feedback_artifact_paths, resolve_pr_ref, validate_auto_pr_manifest_for_feedback
from codepilot.pr_feedback.models import FeedbackFreshness, FollowupAttemptRef, PRFeedbackManifestInvalidError, PRFeedbackResult, PRFeedbackSafetyError, PRFeedbackStatus, to_pr_feedback_jsonable
from codepilot.pr_feedback.normalizer import normalize_feedback
from codepilot.pr_feedback.report import write_ci_feedback_report
from codepilot.pr_feedback.reviews import collect_pr_reviews
from codepilot.pr_feedback.task_builder import build_followup_task, write_followup_task
from codepilot.pr_feedback.update_plan import render_pr_update_plan, write_pr_update_plan
from codepilot.report.generator import generate_report
from codepilot.repo.git_utils import sha256_file
from codepilot.repo.models import RepoSafetyConfig
from codepilot.repo.safety import check_repo_safety
from codepilot.repo.worktree import create_issue_worktree
from codepilot.workflow_artifacts import build_artifact_record, prepare_owned_artifacts


PR_FEEDBACK_ARTIFACT_NAMES = [
    "ci_status.json",
    "review_feedback.json",
    "ci_feedback_report.md",
    "followup_task.md",
    "pr_update_plan.md",
    "ci_feedback_manifest.json",
    "pr_feedback_workflow.yml",
]


def _write_json(path: Path, payload: dict[str, Any], *, overwrite: bool) -> Path:
    """把 payload 统一写成 UTF-8 JSON。"""

    if path.exists() and not overwrite:
        raise FileExistsError(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def _sha256_or_none(path: Path) -> str | None:
    """只在文件存在时计算 sha256，便于写 blocked / degraded artifact。"""

    return sha256_file(path) if path.exists() else None


def _write_manifest(
    *,
    output_path: Path,
    result: PRFeedbackResult,
    source_auto_pr_manifest_path: Path,
    artifacts: dict[str, Path | None],
    latest_attempt_id: str | None = None,
    overwrite: bool = False,
) -> Path:
    """把 workflow 结果压缩成第十四步 manifest。"""

    payload = {
        "schema_version": "codepilot.ci_feedback_manifest.v1",
        "created_at": datetime.now(UTC).isoformat(),
        "run_id": result.run_id,
        "status": result.status,
        "source_auto_pr_manifest": source_auto_pr_manifest_path.name,
        "source_auto_pr_manifest_sha256": _sha256_or_none(source_auto_pr_manifest_path),
        "inputs": {
            "dry_run": result.dry_run,
            "execute": result.execute,
            "allow_run_agent_input": result.allow_run_agent_input,
            "allow_push_update_input": result.allow_push_update_input,
            "allow_comment_input": result.allow_comment_input,
            "feedback_sources_degraded": result.feedback_sources_degraded,
        },
        "pr": to_pr_feedback_jsonable(result.pr),
        "feedback_freshness": to_pr_feedback_jsonable(result.feedback_freshness),
        "summary": {
            "checks_total": len(result.checks),
            "log_summaries_total": len(result.log_summaries),
            "review_comments_total": len(result.review_comments),
            "feedback_items_total": len(result.feedback_items),
        },
        "safe_summary": {
            "checks": [to_pr_feedback_jsonable(item) for item in result.checks],
            "logs": [to_pr_feedback_jsonable(item) for item in result.log_summaries],
            "reviews": [to_pr_feedback_jsonable(item) for item in result.review_comments],
            "feedback_items": [to_pr_feedback_jsonable(item) for item in result.feedback_items],
        },
        "side_effects": {
            "github_api_called": result.github_api_called,
            "agent_ran": result.agent_ran,
            "patch_generated": result.patch_generated,
            "commit_created": result.commit_created,
            "push_update_executed": result.push_update_executed,
            "comment_posted": result.comment_posted,
        },
        "latest_attempt_id": result.followup_attempt.attempt_id if isinstance(result.followup_attempt, FollowupAttemptRef) else latest_attempt_id,
        "new_commit_sha": result.new_commit_sha,
        "blockers": result.blockers,
        "warnings": result.warnings,
        "generated_artifacts": [
            build_artifact_record(name, path, run_dir=result.run_dir)
            for name, path in artifacts.items()
            if path is not None
        ],
    }
    return _write_json(output_path, payload, overwrite=overwrite)


def _comment_body(result: PRFeedbackResult, *, marker: str | None = None) -> str:
    """构造简短 PR 评论，只放状态，不放完整日志。"""

    lines = [
        "CodePilot PR feedback follow-up completed.",
        "",
        f"- Run ID: {result.run_id}",
        f"- Status: {result.status}",
        f"- Agent ran: {'yes' if result.agent_ran else 'no'}",
        f"- Patch generated: {'yes' if result.patch_generated else 'no'}",
        f"- Commit created: {'yes' if result.commit_created else 'no'}",
        f"- PR branch updated: {'yes' if result.push_update_executed else 'no'}",
    ]
    if marker:
        lines.append(f"<!-- codepilot:post-pr:{marker} -->")
    if result.followup_attempt:
        lines.append(f"- Attempt: {result.followup_attempt.attempt_id}")
    return "\n".join(lines)


def _resolve_repo_path(source_artifact_manifest: dict[str, Any]) -> Path:
    """从第十一步 manifest 恢复可执行的 repo 路径。"""

    repo_path = source_artifact_manifest.get("effective_repo_path") or source_artifact_manifest.get("repo_path")
    if not isinstance(repo_path, str) or not repo_path:
        raise PRFeedbackManifestInvalidError("missing repo_path or effective_repo_path in source artifact manifest")
    if repo_path.startswith("[REDACTED"):
        raise PRFeedbackSafetyError("source artifact manifest redacted the repository path")
    return Path(repo_path).expanduser().resolve()


def _prepare_followup_repo(
    *,
    source_artifact_manifest: dict[str, Any],
    run_id: str,
    attempt_id: str,
) -> Path:
    """优先复用原有效 repo；若不在 worktree 中则创建新 worktree。"""

    repo_path = _resolve_repo_path(source_artifact_manifest)
    if source_artifact_manifest.get("used_worktree") is True and repo_path.exists():
        return repo_path
    if repo_path.exists():
        return create_issue_worktree(repo_path, run_id=f"{run_id}-{attempt_id}").worktree_path
    raise PRFeedbackSafetyError("repository path does not exist for follow-up execution")


def _collect_feedback(
    *,
    client: PRFeedbackGitHubClientProtocol,
    pr,
    include_logs: bool,
    include_success_logs: bool,
    max_log_bytes: int,
    output_dir: Path,
) -> tuple[list[Any], list[Any], list[Any], bool, list[str], list[str]]:
    """把 checks / logs / reviews 的收集收束到一个 helper。"""

    checks, warnings, degraded_sources = collect_pr_checks_degraded(client=client, pr=pr)
    github_api_called = True
    log_summaries: list[Any] = []
    if include_logs:
        try:
            log_summaries = collect_failed_ci_logs(
                client=client,
                pr=pr,
                checks=checks,
                output_dir=output_dir,
                max_log_bytes=max_log_bytes,
                include_success_logs=include_success_logs,
            )
        except PRFeedbackGitHubError as exc:
            warnings.append(redact_feedback_text(str(exc)))
            degraded_sources.append("logs")
    try:
        review_comments = collect_pr_reviews(client=client, pr=pr)
    except PRFeedbackGitHubError as exc:
        review_comments = []
        warnings.append(redact_feedback_text(str(exc)))
        degraded_sources.append("reviews")
    return checks, log_summaries, review_comments, github_api_called, warnings, degraded_sources


def _write_base_artifacts(
    *,
    result: PRFeedbackResult,
    artifact_paths: dict[str, Path],
    source_auto_pr_manifest_path: Path,
    feedback_action_template: bool,
    overwrite: bool,
    dry_run: bool,
    execute: bool,
    allow_run_agent: bool,
    allow_push_update: bool,
    allow_comment: bool,
) -> tuple[Path, Path, Path, Path, Path | None]:
    """写出 CI status、review feedback、report、update plan 和 workflow 模板。"""

    ci_status_path = _write_json(
        artifact_paths["ci_status"],
        {
            "schema_version": "codepilot.ci_status.v1",
            "run_id": result.run_id,
            "pr": to_pr_feedback_jsonable(result.pr),
            "summary": summarize_check_state(result.checks),
            "checks": [to_pr_feedback_jsonable(check) for check in result.checks],
        },
        overwrite=overwrite,
    )
    review_feedback_path = _write_json(
        artifact_paths["review_feedback"],
        {
            "schema_version": "codepilot.review_feedback.v1",
            "run_id": result.run_id,
            "pr": to_pr_feedback_jsonable(result.pr),
            "comments": [to_pr_feedback_jsonable(comment) for comment in result.review_comments],
        },
        overwrite=overwrite,
    )
    report_result = replace(
        result,
        ci_status_path=ci_status_path,
        review_feedback_path=review_feedback_path,
        ci_feedback_report_path=artifact_paths["ci_feedback_report"],
    )
    report_path = write_ci_feedback_report(result=report_result, output_path=artifact_paths["ci_feedback_report"], overwrite=overwrite)
    plan_result = replace(report_result, ci_feedback_report_path=report_path)
    update_plan_path = write_pr_update_plan(
        render_pr_update_plan(
            result=plan_result,
            dry_run=dry_run,
            execute=execute,
            allow_run_agent=allow_run_agent,
            allow_push_update=allow_push_update,
            allow_comment=allow_comment,
        ),
        artifact_paths["pr_update_plan"],
        overwrite=overwrite,
    )
    workflow_path = None
    if feedback_action_template:
        workflow_path = write_pr_feedback_workflow_template(artifact_paths["feedback_workflow"], overwrite=overwrite)
    return ci_status_path, review_feedback_path, report_path, update_plan_path, workflow_path


def _write_terminal_artifacts(
    *,
    run_id: str,
    run_dir: Path,
    status: PRFeedbackStatus,
    pr,
    feedback_freshness: FeedbackFreshness | None,
    artifact_paths: dict[str, Path],
    source_auto_pr_manifest_path: Path,
    feedback_action_template: bool,
    overwrite: bool,
    dry_run: bool,
    execute: bool,
    allow_run_agent: bool,
    allow_push_update: bool,
    allow_comment: bool,
    warnings: list[str] | None = None,
    blockers: list[str] | None = None,
    feedback_sources_degraded: list[str] | None = None,
    github_api_called: bool = False,
    remote_head_checked: bool = False,
    execute_blocked_by_stale_head: bool = False,
    checks: list[Any] | None = None,
    log_summaries: list[Any] | None = None,
    review_comments: list[Any] | None = None,
    feedback_items: list[Any] | None = None,
) -> PRFeedbackResult:
    """把 blocked / degraded 的终止路径统一写成完整 artifact。"""

    result = PRFeedbackResult(
        run_id=run_id,
        run_dir=run_dir,
        status=status,
        dry_run=dry_run,
        execute=execute,
        allow_run_agent_input=allow_run_agent,
        allow_push_update_input=allow_push_update,
        allow_comment_input=allow_comment,
        feedback_sources_degraded=list(feedback_sources_degraded or []),
        pr=pr,
        feedback_freshness=feedback_freshness,
        checks=list(checks or []),
        log_summaries=list(log_summaries or []),
        review_comments=list(review_comments or []),
        feedback_items=list(feedback_items or []),
        github_api_called=github_api_called,
        remote_head_checked=remote_head_checked,
        execute_blocked_by_stale_head=execute_blocked_by_stale_head,
        api_degraded=bool(feedback_sources_degraded) or status in {"partial_feedback", "api_degraded", "feedback_unavailable"},
        warnings=list(warnings or []),
        blockers=list(blockers or []),
    )
    followup_task_text = build_followup_task(pr=pr, feedback_items=result.feedback_items, source_run_id=run_id) if pr else "No actionable feedback could be generated.\n"
    followup_task_path = write_followup_task(followup_task_text, artifact_paths["followup_task"], overwrite=overwrite)
    result = replace(result, followup_task_path=followup_task_path)
    ci_status_path, review_feedback_path, report_path, update_plan_path, workflow_path = _write_base_artifacts(
        result=result,
        artifact_paths=artifact_paths,
        source_auto_pr_manifest_path=source_auto_pr_manifest_path,
        feedback_action_template=feedback_action_template,
        overwrite=overwrite,
        dry_run=dry_run,
        execute=execute,
        allow_run_agent=allow_run_agent,
        allow_push_update=allow_push_update,
        allow_comment=allow_comment,
    )
    result = replace(
        result,
        ci_status_path=ci_status_path,
        review_feedback_path=review_feedback_path,
        ci_feedback_report_path=report_path,
        pr_update_plan_path=update_plan_path,
        feedback_workflow_path=workflow_path,
    )
    manifest_written = _write_manifest(
        output_path=artifact_paths["ci_feedback_manifest"],
        result=result,
        source_auto_pr_manifest_path=source_auto_pr_manifest_path,
        artifacts={
            "ci_status": ci_status_path,
            "review_feedback": review_feedback_path,
            "ci_feedback_report": report_path,
            "followup_task": followup_task_path,
            "pr_update_plan": update_plan_path,
            "feedback_workflow": workflow_path,
        },
        overwrite=True,
    )
    return replace(result, ci_feedback_manifest_path=manifest_written)



@dataclass
class PRFeedbackWorkflowContext:
    run_dir: Path
    artifact_paths: dict[str, Path]
    manifest_path: Path
    auto_pr_manifest: dict[str, Any]
    source_artifact_manifest: dict[str, Any]
    pr: Any
    run_id: str
    dry_run: bool
    execute: bool
    wait_ci: bool
    include_logs: bool
    include_success_logs: bool
    allow_run_agent: bool
    allow_push_update: bool
    allow_comment: bool
    max_feedback_items: int
    max_log_bytes: int
    poll_interval_seconds: int
    timeout_seconds: int
    feedback_action_template: bool
    comment_marker: str | None
    client: PRFeedbackGitHubClientProtocol
    current_head_sha: str | None
    observed_at: str | None
    freshness: FeedbackFreshness
    remote_head_checked: bool
    github_api_called: bool
    warnings: list[str] = field(default_factory=list)
    attempt_artifacts: dict[str, Path] = field(default_factory=dict)


@dataclass(frozen=True)
class FeedbackCollectionPhase:
    checks: list[Any]
    log_summaries: list[Any]
    review_comments: list[Any]
    feedback_items: list[Any]
    status: PRFeedbackStatus
    blockers: list[str]
    warnings: list[str]
    degraded_sources: list[str]
    github_api_called: bool


def _prepare_feedback_phase(
    *,
    run_dir: str | Path,
    auto_pr_manifest_path: str | Path | None,
    dry_run: bool,
    execute: bool,
    wait_ci: bool,
    include_logs: bool,
    include_success_logs: bool,
    allow_run_agent: bool,
    allow_push_update: bool,
    allow_comment: bool,
    max_feedback_items: int,
    max_log_bytes: int,
    max_followup_rounds: int,
    poll_interval_seconds: int,
    timeout_seconds: int,
    token_env: str,
    repo_slug: str | None,
    pull_number: int | None,
    head_branch: str | None,
    feedback_action_template: bool,
    comment_marker: str | None,
    overwrite: bool,
    github_client: PRFeedbackGitHubClientProtocol | None,
) -> PRFeedbackWorkflowContext | PRFeedbackResult:
    if max_followup_rounds != 1:
        raise PRFeedbackSafetyError("max_followup_rounds v1 only supports 1")
    run_dir_path = Path(run_dir).expanduser().resolve()
    if not run_dir_path.exists() or not run_dir_path.is_dir():
        raise FileNotFoundError(run_dir_path)
    prepare_owned_artifacts(
        run_dir_path,
        PR_FEEDBACK_ARTIFACT_NAMES,
        overwrite=overwrite,
        label="PR feedback",
    )
    artifact_paths = resolve_feedback_artifact_paths(run_dir_path)
    manifest_path = Path(auto_pr_manifest_path).expanduser().resolve() if auto_pr_manifest_path else run_dir_path / "auto_pr_manifest.json"
    try:
        auto_pr_manifest = load_auto_pr_manifest(manifest_path)
        validation_errors = validate_auto_pr_manifest_for_feedback(auto_pr_manifest, run_dir_path)
        if validation_errors:
            raise PRFeedbackManifestInvalidError("; ".join(validation_errors))
        _, source_artifact_manifest = load_source_manifests_for_feedback(run_dir_path, auto_pr_manifest)
        pr = resolve_pr_ref(auto_pr_manifest, repo_slug=repo_slug, pull_number=pull_number, head_branch=head_branch)
        assert_controlled_head_branch(pr)
    except (FileNotFoundError, PRFeedbackManifestInvalidError, AutoPRManifestInvalidError, PRFeedbackSafetyError) as exc:
        return _write_terminal_artifacts(
            run_id=run_dir_path.name,
            run_dir=run_dir_path,
            status="blocked",
            pr=None,
            feedback_freshness=None,
            artifact_paths=artifact_paths,
            source_auto_pr_manifest_path=manifest_path,
            feedback_action_template=feedback_action_template,
            overwrite=True,
            dry_run=dry_run,
            execute=execute,
            allow_run_agent=allow_run_agent,
            allow_push_update=allow_push_update,
            allow_comment=allow_comment,
            warnings=[str(exc)],
            blockers=[str(exc)],
        )

    run_id = str(auto_pr_manifest.get("run_id") or run_dir_path.name)
    if github_client is None and not is_github_token_available(token_env):
        return _write_terminal_artifacts(
            run_id=run_id,
            run_dir=run_dir_path,
            status="blocked" if execute else "feedback_unavailable",
            pr=pr,
            feedback_freshness=None,
            artifact_paths=artifact_paths,
            source_auto_pr_manifest_path=manifest_path,
            feedback_action_template=feedback_action_template,
            overwrite=True,
            dry_run=dry_run,
            execute=execute,
            allow_run_agent=allow_run_agent,
            allow_push_update=allow_push_update,
            allow_comment=allow_comment,
            warnings=["missing required GitHub credential"],
            blockers=["missing required GitHub credential"] if execute else [],
            feedback_sources_degraded=["github"],
        )
    client = github_client or RestPRFeedbackGitHubClient(token_env=token_env)
    try:
        current_head_sha, observed_at = resolve_current_pr_head(client, pr)
        remote_head_checked = True
        github_api_called = True
        head_warning = None
    except PRFeedbackGitHubError as exc:
        warning = redact_feedback_text(str(exc))
        if execute:
            return _write_terminal_artifacts(
                run_id=run_id,
                run_dir=run_dir_path,
                status="blocked",
                pr=pr,
                feedback_freshness=None,
                artifact_paths=artifact_paths,
                source_auto_pr_manifest_path=manifest_path,
                feedback_action_template=feedback_action_template,
                overwrite=True,
                dry_run=dry_run,
                execute=execute,
                allow_run_agent=allow_run_agent,
                allow_push_update=allow_push_update,
                allow_comment=allow_comment,
                warnings=[warning],
                blockers=[warning],
                feedback_sources_degraded=["head"],
                github_api_called=True,
            )
        current_head_sha = None
        observed_at = None
        remote_head_checked = False
        github_api_called = True
        head_warning = warning
    except PRFeedbackSafetyError as exc:
        return _write_terminal_artifacts(
            run_id=run_id,
            run_dir=run_dir_path,
            status="blocked",
            pr=pr,
            feedback_freshness=None,
            artifact_paths=artifact_paths,
            source_auto_pr_manifest_path=manifest_path,
            feedback_action_template=feedback_action_template,
            overwrite=True,
            dry_run=dry_run,
            execute=execute,
            allow_run_agent=allow_run_agent,
            allow_push_update=allow_push_update,
            allow_comment=allow_comment,
            warnings=[str(exc)],
            blockers=[str(exc)],
            feedback_sources_degraded=["head"],
            github_api_called=True,
            remote_head_checked=True,
            execute_blocked_by_stale_head=True,
        )
    freshness = build_feedback_freshness(
        observed_head_sha=pr.head_sha,
        current_head_sha=current_head_sha,
        observed_at=observed_at,
    )
    if execute:
        try:
            assert_fresh_head_for_execute(freshness)
        except PRFeedbackSafetyError as exc:
            return _write_terminal_artifacts(
                run_id=run_id,
                run_dir=run_dir_path,
                status="blocked",
                pr=pr,
                feedback_freshness=freshness,
                artifact_paths=artifact_paths,
                source_auto_pr_manifest_path=manifest_path,
                feedback_action_template=feedback_action_template,
                overwrite=True,
                dry_run=dry_run,
                execute=execute,
                allow_run_agent=allow_run_agent,
                allow_push_update=allow_push_update,
                allow_comment=allow_comment,
                warnings=[str(exc)],
                blockers=[str(exc)],
                feedback_sources_degraded=["head"],
                github_api_called=github_api_called,
                remote_head_checked=True,
                execute_blocked_by_stale_head=True,
            )
    warnings = [head_warning] if head_warning is not None else []
    return PRFeedbackWorkflowContext(
        run_dir=run_dir_path,
        artifact_paths=artifact_paths,
        manifest_path=manifest_path,
        auto_pr_manifest=auto_pr_manifest,
        source_artifact_manifest=source_artifact_manifest,
        pr=pr,
        run_id=run_id,
        dry_run=dry_run,
        execute=execute,
        wait_ci=wait_ci,
        include_logs=include_logs,
        include_success_logs=include_success_logs,
        allow_run_agent=allow_run_agent,
        allow_push_update=allow_push_update,
        allow_comment=allow_comment,
        max_feedback_items=max_feedback_items,
        max_log_bytes=max_log_bytes,
        poll_interval_seconds=poll_interval_seconds,
        timeout_seconds=timeout_seconds,
        feedback_action_template=feedback_action_template,
        comment_marker=comment_marker,
        client=client,
        current_head_sha=current_head_sha,
        observed_at=observed_at,
        freshness=freshness,
        remote_head_checked=remote_head_checked,
        github_api_called=github_api_called,
        warnings=warnings,
    )


def _collect_feedback_phase(ctx: PRFeedbackWorkflowContext) -> FeedbackCollectionPhase:
    checks, log_summaries, review_comments, github_api_called, warnings, degraded_sources = _collect_feedback(
        client=ctx.client,
        pr=ctx.pr,
        include_logs=ctx.include_logs,
        include_success_logs=ctx.include_success_logs,
        max_log_bytes=ctx.max_log_bytes,
        output_dir=ctx.artifact_paths["ci_logs_dir"],
    )
    warnings = [*ctx.warnings, *warnings]
    if ctx.wait_ci and has_pending_checks(checks):
        deadline = monotonic() + ctx.timeout_seconds
        while has_pending_checks(checks) and monotonic() < deadline:
            sleep(ctx.poll_interval_seconds)
            checks, log_summaries, review_comments, github_api_called, more_warnings, more_degraded_sources = _collect_feedback(
                client=ctx.client,
                pr=ctx.pr,
                include_logs=ctx.include_logs,
                include_success_logs=ctx.include_success_logs,
                max_log_bytes=ctx.max_log_bytes,
                output_dir=ctx.artifact_paths["ci_logs_dir"],
            )
            warnings.extend(more_warnings)
            degraded_sources.extend(more_degraded_sources)
        if has_pending_checks(checks):
            warnings.append("CI checks did not finish before timeout")
    if ctx.current_head_sha is None and "head" not in degraded_sources:
        degraded_sources.append("head")
    feedback_items = normalize_feedback(
        checks=checks,
        log_summaries=log_summaries,
        review_comments=review_comments,
        max_items=ctx.max_feedback_items,
        observed_at=ctx.observed_at,
        head_sha=ctx.pr.head_sha,
    )
    only_low_confidence = bool(feedback_items) and all(item.confidence == "low" for item in feedback_items)
    blockers: list[str] = []
    if ctx.execute and has_pending_checks(checks) and not ctx.wait_ci:
        status: PRFeedbackStatus = "blocked"
        blockers.append("pending checks require --wait-ci")
    elif ctx.execute and ctx.freshness.is_stale:
        status = "blocked"
        blockers.append(ctx.freshness.stale_reason or "PR head is stale")
    elif ctx.execute and feedback_items and not ctx.allow_run_agent:
        status = "blocked"
        blockers.append("--allow-run-agent is required in execute mode when feedback exists")
    elif ctx.execute and only_low_confidence:
        status = "blocked"
        blockers.append("unreliable feedback correlation requires fresh CI data")
    elif degraded_sources:
        status = "partial_feedback" if feedback_items else "api_degraded"
    elif not feedback_items:
        status = "no_feedback"
    else:
        status = "feedback_found"
    if status == "blocked":
        warnings.extend(blockers)
    return FeedbackCollectionPhase(
        checks=checks,
        log_summaries=log_summaries,
        review_comments=review_comments,
        feedback_items=feedback_items,
        status=status,
        blockers=blockers,
        warnings=warnings,
        degraded_sources=degraded_sources,
        github_api_called=github_api_called or ctx.github_api_called,
    )


def _persist_feedback_phase(ctx: PRFeedbackWorkflowContext, collection: FeedbackCollectionPhase) -> PRFeedbackResult:
    result = PRFeedbackResult(
        run_id=ctx.run_id,
        run_dir=ctx.run_dir,
        status=collection.status,
        dry_run=ctx.dry_run,
        execute=ctx.execute,
        allow_run_agent_input=ctx.allow_run_agent,
        allow_push_update_input=ctx.allow_push_update,
        allow_comment_input=ctx.allow_comment,
        feedback_sources_degraded=collection.degraded_sources,
        pr=ctx.pr,
        feedback_freshness=ctx.freshness,
        checks=collection.checks,
        log_summaries=collection.log_summaries,
        review_comments=collection.review_comments,
        feedback_items=collection.feedback_items,
        github_api_called=collection.github_api_called,
        remote_head_checked=ctx.remote_head_checked,
        warnings=collection.warnings,
        blockers=collection.blockers,
        api_degraded=bool(collection.degraded_sources) or collection.status in {"partial_feedback", "api_degraded", "feedback_unavailable"},
    )
    followup_task_path = write_followup_task(
        build_followup_task(pr=ctx.pr, feedback_items=collection.feedback_items, source_run_id=ctx.run_id),
        ctx.artifact_paths["followup_task"],
        overwrite=True,
    )
    result = replace(result, followup_task_path=followup_task_path)
    ci_status_path, review_feedback_path, report_path, update_plan_path, workflow_path = _write_base_artifacts(
        result=result,
        artifact_paths=ctx.artifact_paths,
        source_auto_pr_manifest_path=ctx.manifest_path,
        feedback_action_template=ctx.feedback_action_template,
        overwrite=True,
        dry_run=ctx.dry_run,
        execute=ctx.execute,
        allow_run_agent=ctx.allow_run_agent,
        allow_push_update=ctx.allow_push_update,
        allow_comment=ctx.allow_comment,
    )
    result = replace(
        result,
        ci_status_path=ci_status_path,
        review_feedback_path=review_feedback_path,
        ci_feedback_report_path=report_path,
        pr_update_plan_path=update_plan_path,
        feedback_workflow_path=workflow_path,
    )
    return result


def _execute_feedback_phase(ctx: PRFeedbackWorkflowContext, result: PRFeedbackResult) -> PRFeedbackResult:
    preliminary_manifest_path = _write_manifest(
        output_path=ctx.artifact_paths["ci_feedback_manifest"],
        result=result,
        source_auto_pr_manifest_path=ctx.manifest_path,
        artifacts={
            "ci_status": result.ci_status_path,
            "review_feedback": result.review_feedback_path,
            "ci_feedback_report": result.ci_feedback_report_path,
            "followup_task": result.followup_task_path,
            "pr_update_plan": result.pr_update_plan_path,
            "feedback_workflow": result.feedback_workflow_path,
        },
        overwrite=True,
    )
    attempt = create_followup_attempt(
        ctx.run_dir,
        source_feedback_manifest_path=preliminary_manifest_path,
        followup_task_path=result.followup_task_path,
        overwrite_attempt=False,
    )
    copy_followup_task_to_attempt(attempt)
    attempt_repo_path = _prepare_followup_repo(
        source_artifact_manifest=ctx.source_artifact_manifest,
        run_id=ctx.run_id,
        attempt_id=attempt.attempt_id,
    )
    repo_safety = check_repo_safety(attempt_repo_path, config=RepoSafetyConfig(dirty_policy="warn", worktree_mode="off"))
    if repo_safety.decision == "deny":
        raise PRFeedbackSafetyError(repo_safety.reason or "follow-up repo safety denied")
    warnings = [*result.warnings, *repo_safety.warnings]
    followup_result = run_agent_task(
        task=result.followup_task_path.read_text(encoding="utf-8"),
        repo=attempt_repo_path,
        runs_dir=attempt.attempt_dir.parent,
        run_id=attempt.attempt_id,
    )
    trace_path = Path(followup_result.trace_path or attempt.attempt_dir / "trace.jsonl")
    attempt_report_path = trace_path.with_name("report.md")
    generate_report(trace_path, attempt_report_path, overwrite=True)
    patch_path, patch_metadata = export_patch_with_metadata(attempt_repo_path, attempt.attempt_dir / "changes.patch")
    new_commit_sha = None
    commit_created = False
    push_update_executed = False
    push_update_blocked_reason: str | None = None
    if patch_metadata.changed_files:
        new_commit_sha = prepare_followup_commit(
            attempt_repo_path,
            attempt_manifest_path=attempt.attempt_dir / "followup_attempt_manifest.json",
            patch_metadata=patch_metadata,
            issue_title=str((ctx.auto_pr_manifest.get("pr_request") or {}).get("title") or ctx.pr.url),
            tests_summary=followup_result.outcome.last_test_status,
            run_id=ctx.run_id,
        )
        commit_created = True
        if ctx.allow_push_update:
            expected_current_head_sha = ctx.freshness.current_head_sha or ctx.pr.head_sha
            if not expected_current_head_sha:
                push_update_blocked_reason = "cannot push update without verified current PR head sha"
                warnings.append(push_update_blocked_reason)
            else:
                push_result = push_pr_branch_update_if_allowed(
                    repo_path=attempt_repo_path,
                    pr=ctx.pr,
                    new_commit_sha=new_commit_sha,
                    expected_current_head_sha=expected_current_head_sha,
                    execute=ctx.execute,
                    allow_push_update=ctx.allow_push_update,
                )
                push_update_executed = bool(push_result.get("pushed"))
    final_attempt = replace(
        attempt,
        agent_ran=True,
        patch_generated=bool(patch_metadata.changed_files),
        commit_created=commit_created,
        push_update_executed=push_update_executed,
    )
    write_followup_attempt_manifest(
        final_attempt,
        {
            "trace_path": str(trace_path),
            "report_path": str(attempt_report_path),
            "patch_path": str(patch_path),
            "agent_ran": followup_result.success,
            "patch_generated": bool(patch_metadata.changed_files),
            "commit_created": commit_created,
            "push_update_executed": push_update_executed,
        },
        overwrite=True,
    )
    ctx.attempt_artifacts.update({"attempt_report": attempt_report_path, "attempt_patch": patch_path})
    final_status: PRFeedbackStatus = (
        "blocked"
        if push_update_blocked_reason
        else "branch_updated"
        if push_update_executed
        else "commit_created"
        if commit_created
        else "agent_ran"
    )
    final_result = replace(
        result,
        status=final_status,
        followup_attempt=final_attempt,
        agent_ran=True,
        patch_generated=bool(patch_metadata.changed_files),
        commit_created=commit_created,
        new_commit_sha=new_commit_sha,
        push_update_executed=push_update_executed,
        github_api_called=True,
        warnings=warnings,
        blockers=[*result.blockers, *([push_update_blocked_reason] if push_update_blocked_reason else [])],
    )
    comment_posted = False
    if ctx.allow_comment and final_status != "blocked":
        try:
            ctx.client.post_pr_comment(ctx.pr, _comment_body(final_result, marker=ctx.comment_marker))
            comment_posted = True
        except PRFeedbackGitHubError as exc:
            warnings.append(redact_feedback_text(str(exc)))
    return replace(final_result, comment_posted=comment_posted, warnings=warnings)


def _finalize_feedback_phase(ctx: PRFeedbackWorkflowContext, result: PRFeedbackResult) -> PRFeedbackResult:
    manifest_written = _write_manifest(
        output_path=ctx.artifact_paths["ci_feedback_manifest"],
        result=result,
        source_auto_pr_manifest_path=ctx.manifest_path,
        artifacts={
            "ci_status": result.ci_status_path,
            "review_feedback": result.review_feedback_path,
            "ci_feedback_report": result.ci_feedback_report_path,
            "followup_task": result.followup_task_path,
            "pr_update_plan": result.pr_update_plan_path,
            "feedback_workflow": result.feedback_workflow_path,
            **ctx.attempt_artifacts,
        },
        latest_attempt_id=result.followup_attempt.attempt_id if isinstance(result.followup_attempt, FollowupAttemptRef) else None,
        overwrite=True,
    )
    return replace(result, ci_feedback_manifest_path=manifest_written)


def run_pr_feedback_loop(
    *,
    run_dir: str | Path,
    auto_pr_manifest_path: str | Path | None = None,
    dry_run: bool = True,
    execute: bool = False,
    wait_ci: bool = False,
    include_logs: bool = True,
    include_success_logs: bool = False,
    allow_run_agent: bool = False,
    allow_push_update: bool = False,
    allow_comment: bool = False,
    max_feedback_items: int = 20,
    max_log_bytes: int = 200_000,
    max_followup_rounds: int = 1,
    poll_interval_seconds: int = 30,
    timeout_seconds: int = 900,
    token_env: str = "GITHUB_TOKEN",
    repo_slug: str | None = None,
    pull_number: int | None = None,
    head_branch: str | None = None,
    feedback_action_template: bool = True,
    comment_marker: str | None = None,
    overwrite: bool = False,
    github_client: PRFeedbackGitHubClientProtocol | None = None,
) -> PRFeedbackResult:
    ctx = _prepare_feedback_phase(
        run_dir=run_dir,
        auto_pr_manifest_path=auto_pr_manifest_path,
        dry_run=dry_run,
        execute=execute,
        wait_ci=wait_ci,
        include_logs=include_logs,
        include_success_logs=include_success_logs,
        allow_run_agent=allow_run_agent,
        allow_push_update=allow_push_update,
        allow_comment=allow_comment,
        max_feedback_items=max_feedback_items,
        max_log_bytes=max_log_bytes,
        max_followup_rounds=max_followup_rounds,
        poll_interval_seconds=poll_interval_seconds,
        timeout_seconds=timeout_seconds,
        token_env=token_env,
        repo_slug=repo_slug,
        pull_number=pull_number,
        head_branch=head_branch,
        feedback_action_template=feedback_action_template,
        comment_marker=comment_marker,
        overwrite=overwrite,
        github_client=github_client,
    )
    if isinstance(ctx, PRFeedbackResult):
        return ctx
    collection = _collect_feedback_phase(ctx)
    result = _persist_feedback_phase(ctx, collection)
    if not execute or result.status in {"no_feedback", "blocked"}:
        return _finalize_feedback_phase(ctx, result)
    return _finalize_feedback_phase(ctx, _execute_feedback_phase(ctx, result))
