from __future__ import annotations

"""第十四步 PR feedback / PR review loop 主 workflow。"""

import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic, sleep
from typing import Any

from codepilot.agent.runner import run_agent_task
from codepilot.github.patch_exporter import export_patch_with_metadata
from codepilot.pr_assist.commit import render_commit_message
from codepilot.pr_feedback.branch_update import prepare_followup_commit, push_pr_branch_update_if_allowed
from codepilot.pr_feedback.checks import collect_pr_checks, has_blocking_ci_failure, has_pending_checks, summarize_check_state
from codepilot.pr_feedback.freshness import assert_controlled_head_branch, assert_fresh_head_for_execute, build_feedback_freshness, resolve_current_pr_head
from codepilot.pr_feedback.followup_attempt import copy_followup_task_to_attempt, create_followup_attempt, write_followup_attempt_manifest
from codepilot.pr_feedback.github_action import write_pr_feedback_workflow_template
from codepilot.pr_feedback.github_client import PRFeedbackGitHubClientProtocol, PRFeedbackGitHubError, RestPRFeedbackGitHubClient, assert_github_token_available, redact_feedback_text
from codepilot.pr_feedback.logs import collect_failed_ci_logs
from codepilot.pr_feedback.manifest_loader import load_auto_pr_manifest, load_source_manifests_for_feedback, resolve_feedback_artifact_paths, resolve_pr_ref, validate_auto_pr_manifest_for_feedback
from codepilot.pr_feedback.models import FollowupAttemptRef, PRFeedbackManifestInvalidError, PRFeedbackResult, PRFeedbackSafetyError, PRFeedbackStatus, to_pr_feedback_jsonable
from codepilot.pr_feedback.normalizer import normalize_feedback
from codepilot.pr_feedback.report import write_ci_feedback_report
from codepilot.pr_feedback.reviews import collect_pr_reviews
from codepilot.pr_feedback.task_builder import build_followup_task, write_followup_task
from codepilot.pr_feedback.update_plan import render_pr_update_plan, write_pr_update_plan
from codepilot.auto_pr.models import AutoPRManifestInvalidError
from codepilot.repo.git_utils import sha256_file
from codepilot.repo.worktree import create_issue_worktree


PR_FEEDBACK_ARTIFACT_NAMES = [
    "ci_status.json",
    "review_feedback.json",
    "ci_feedback_report.md",
    "followup_task.md",
    "pr_update_plan.md",
    "ci_feedback_manifest.json",
    "pr_feedback_workflow.yml",
]


def _ensure_can_write(run_dir: Path, *, overwrite: bool) -> None:
    """只管理第十四步自己的根目录产物，不碰 follow-up attempt 目录。"""

    existing = [run_dir / name for name in PR_FEEDBACK_ARTIFACT_NAMES if (run_dir / name).exists()]
    if existing and not overwrite:
        raise FileExistsError("PR feedback artifacts already exist: " + ", ".join(str(path) for path in existing))
    if overwrite:
        for path in existing:
            path.unlink()


def _write_json(path: Path, payload: dict[str, Any], *, overwrite: bool) -> Path:
    """把 payload 统一写成 UTF-8 JSON。"""

    if path.exists() and not overwrite:
        raise FileExistsError(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def _artifact_record(name: str, path: Path | None, *, run_dir: Path) -> dict[str, Any] | None:
    """把实际文件压缩成 manifest 里的稳定索引。"""

    if path is None:
        return None
    try:
        display_path = str(path.resolve().relative_to(run_dir.resolve()))
    except ValueError:
        display_path = path.name
    return {
        "name": name,
        "path": display_path,
        "exists": path.exists(),
        "size_bytes": path.stat().st_size if path.exists() else None,
        "sha256": sha256_file(path) if path.exists() else None,
    }


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
        "source_auto_pr_manifest_sha256": sha256_file(source_auto_pr_manifest_path),
        "pr": to_pr_feedback_jsonable(result.pr),
        "feedback_freshness": to_pr_feedback_jsonable(result.feedback_freshness),
        "summary": {
            "checks_total": len(result.checks),
            "feedback_items_total": len(result.feedback_items),
            "review_comments_total": len(result.review_comments),
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
        "generated_artifacts": [item for item in (_artifact_record(name, path, run_dir=result.run_dir) for name, path in artifacts.items()) if item is not None],
    }
    return _write_json(output_path, payload, overwrite=overwrite)


def _comment_body(result: PRFeedbackResult) -> str:
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
) -> tuple[list[Any], list[Any], list[Any], bool, list[str]]:
    """把 checks / logs / reviews 的收集收束到一个 helper。"""

    warnings: list[str] = []
    github_api_called = False
    try:
        checks = collect_pr_checks(client=client, pr=pr)
        github_api_called = True
    except PRFeedbackGitHubError as exc:
        return [], [], [], True, [redact_feedback_text(str(exc))]
    log_summaries = []
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
            github_api_called = True
        except PRFeedbackGitHubError as exc:
            warnings.append(redact_feedback_text(str(exc)))
    try:
        review_comments = collect_pr_reviews(client=client, pr=pr)
        github_api_called = True
    except PRFeedbackGitHubError as exc:
        review_comments = []
        warnings.append(redact_feedback_text(str(exc)))
    return checks, log_summaries, review_comments, github_api_called, warnings


def _write_base_artifacts(
    *,
    result: PRFeedbackResult,
    artifact_paths: dict[str, Path],
    source_auto_pr_manifest_path: Path,
    feedback_action_template: bool,
    overwrite: bool,
) -> tuple[Path, Path, Path, Path | None]:
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
    report_path = write_ci_feedback_report(
        result=PRFeedbackResult(**{**result.__dict__, "ci_status_path": ci_status_path, "review_feedback_path": review_feedback_path}),
        output_path=artifact_paths["ci_feedback_report"],
        overwrite=overwrite,
    )
    update_plan_path = write_pr_update_plan(
        render_pr_update_plan(
            result=PRFeedbackResult(**{**result.__dict__, "ci_status_path": ci_status_path, "review_feedback_path": review_feedback_path, "ci_feedback_report_path": report_path}),
            dry_run=not result.agent_ran,
            execute=result.status not in {"no_feedback", "blocked"},
            allow_run_agent=result.agent_ran,
            allow_push_update=result.push_update_executed,
            allow_comment=result.comment_posted,
        ),
        artifact_paths["pr_update_plan"],
        overwrite=overwrite,
    )
    workflow_path = None
    if feedback_action_template:
        workflow_path = write_pr_feedback_workflow_template(artifact_paths["feedback_workflow"], overwrite=overwrite)
    return ci_status_path, review_feedback_path, report_path, workflow_path


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
    overwrite: bool = False,
    github_client: PRFeedbackGitHubClientProtocol | None = None,
) -> PRFeedbackResult:
    """执行第十四步 PR feedback / PR review loop。"""

    if max_followup_rounds != 1:
        raise PRFeedbackSafetyError("max_followup_rounds v1 only supports 1")
    run_dir_path = Path(run_dir).expanduser().resolve()
    if not run_dir_path.exists() or not run_dir_path.is_dir():
        raise FileNotFoundError(run_dir_path)
    _ensure_can_write(run_dir_path, overwrite=overwrite)
    artifact_paths = resolve_feedback_artifact_paths(run_dir_path)
    manifest_path = Path(auto_pr_manifest_path).expanduser().resolve() if auto_pr_manifest_path else run_dir_path / "auto_pr_manifest.json"
    if github_client is None:
        if not dry_run or execute:
            assert_github_token_available(token_env)
        github_client = RestPRFeedbackGitHubClient(token_env=token_env)

    try:
        auto_pr_manifest = load_auto_pr_manifest(manifest_path)
        validation_errors = validate_auto_pr_manifest_for_feedback(auto_pr_manifest, run_dir_path)
        if validation_errors:
            raise PRFeedbackManifestInvalidError("; ".join(validation_errors))
        pr_assist_manifest, source_artifact_manifest = load_source_manifests_for_feedback(run_dir_path, auto_pr_manifest)
        pr = resolve_pr_ref(auto_pr_manifest, repo_slug=repo_slug, pull_number=pull_number, head_branch=head_branch)
        assert_controlled_head_branch(pr)
    except (PRFeedbackManifestInvalidError, AutoPRManifestInvalidError) as exc:
        result = PRFeedbackResult(
            run_id=run_dir_path.name,
            run_dir=run_dir_path,
            status="blocked",
            warnings=[str(exc)],
            blockers=[str(exc)],
        )
        ci_status_path, review_feedback_path, report_path, workflow_path = _write_base_artifacts(
            result=result,
            artifact_paths=artifact_paths,
            source_auto_pr_manifest_path=manifest_path,
            feedback_action_template=feedback_action_template,
            overwrite=True,
        )
        update_plan_path = write_pr_update_plan(
            render_pr_update_plan(
                result=PRFeedbackResult(**{**result.__dict__, "ci_status_path": ci_status_path, "review_feedback_path": review_feedback_path, "ci_feedback_report_path": report_path, "feedback_workflow_path": workflow_path}),
                dry_run=dry_run,
                execute=execute,
                allow_run_agent=allow_run_agent,
                allow_push_update=allow_push_update,
                allow_comment=allow_comment,
            ),
            artifact_paths["pr_update_plan"],
            overwrite=True,
        )
        manifest_written = _write_manifest(
            output_path=artifact_paths["ci_feedback_manifest"],
            result=PRFeedbackResult(**{**result.__dict__, "ci_status_path": ci_status_path, "review_feedback_path": review_feedback_path, "ci_feedback_report_path": report_path, "pr_update_plan_path": update_plan_path, "feedback_workflow_path": workflow_path}),
            source_auto_pr_manifest_path=manifest_path,
            artifacts={"ci_status": ci_status_path, "review_feedback": review_feedback_path, "ci_feedback_report": report_path, "pr_update_plan": update_plan_path, "feedback_workflow": workflow_path},
            overwrite=True,
        )
        return PRFeedbackResult(**{**result.__dict__, "ci_status_path": ci_status_path, "review_feedback_path": review_feedback_path, "ci_feedback_report_path": report_path, "ci_feedback_manifest_path": manifest_written, "pr_update_plan_path": update_plan_path, "feedback_workflow_path": workflow_path})

    run_id = str(auto_pr_manifest.get("run_id") or run_dir_path.name)
    current_head_sha, observed_at = resolve_current_pr_head(github_client, pr)
    freshness = build_feedback_freshness(observed_head_sha=pr.head_sha, current_head_sha=current_head_sha, observed_at=observed_at)
    if execute:
        try:
            assert_fresh_head_for_execute(freshness)
        except PRFeedbackSafetyError as exc:
            result = PRFeedbackResult(run_id=run_id, run_dir=run_dir_path, status="blocked", pr=pr, feedback_freshness=freshness, blockers=[str(exc)], warnings=[str(exc)], execute_blocked_by_stale_head=True, remote_head_checked=True)
            ci_status_path, review_feedback_path, report_path, workflow_path = _write_base_artifacts(result=result, artifact_paths=artifact_paths, source_auto_pr_manifest_path=manifest_path, feedback_action_template=feedback_action_template, overwrite=True)
            update_plan_path = write_pr_update_plan(render_pr_update_plan(result=PRFeedbackResult(**{**result.__dict__, "ci_status_path": ci_status_path, "review_feedback_path": review_feedback_path, "ci_feedback_report_path": report_path, "feedback_workflow_path": workflow_path}), dry_run=dry_run, execute=execute, allow_run_agent=allow_run_agent, allow_push_update=allow_push_update, allow_comment=allow_comment), artifact_paths["pr_update_plan"], overwrite=True)
            manifest_written = _write_manifest(output_path=artifact_paths["ci_feedback_manifest"], result=PRFeedbackResult(**{**result.__dict__, "ci_status_path": ci_status_path, "review_feedback_path": review_feedback_path, "ci_feedback_report_path": report_path, "pr_update_plan_path": update_plan_path, "feedback_workflow_path": workflow_path}), source_auto_pr_manifest_path=manifest_path, artifacts={"ci_status": ci_status_path, "review_feedback": review_feedback_path, "ci_feedback_report": report_path, "pr_update_plan": update_plan_path, "feedback_workflow": workflow_path}, overwrite=True)
            return PRFeedbackResult(**{**result.__dict__, "ci_status_path": ci_status_path, "review_feedback_path": review_feedback_path, "ci_feedback_report_path": report_path, "ci_feedback_manifest_path": manifest_written, "pr_update_plan_path": update_plan_path, "feedback_workflow_path": workflow_path})

    checks, log_summaries, review_comments, github_api_called, warnings = _collect_feedback(
        client=github_client,
        pr=pr,
        include_logs=include_logs,
        include_success_logs=include_success_logs,
        max_log_bytes=max_log_bytes,
        output_dir=artifact_paths["ci_logs_dir"],
    )
    if wait_ci and has_pending_checks(checks):
        deadline = monotonic() + timeout_seconds
        while has_pending_checks(checks) and monotonic() < deadline:
            sleep(poll_interval_seconds)
            checks, log_summaries, review_comments, github_api_called, more_warnings = _collect_feedback(
                client=github_client,
                pr=pr,
                include_logs=include_logs,
                include_success_logs=include_success_logs,
                max_log_bytes=max_log_bytes,
                output_dir=artifact_paths["ci_logs_dir"],
            )
            warnings.extend(more_warnings)
        if has_pending_checks(checks):
            warnings.append("CI checks did not finish before timeout")

    feedback_items = normalize_feedback(checks=checks, log_summaries=log_summaries, review_comments=review_comments, max_items=max_feedback_items, observed_at=observed_at, head_sha=pr.head_sha)
    status: PRFeedbackStatus
    if warnings:
        status = "blocked" if execute else ("partial_feedback" if feedback_items else "api_degraded")
    elif not feedback_items:
        status = "no_feedback"
    else:
        status = "feedback_found"
    if execute and has_pending_checks(checks) and not wait_ci:
        status = "blocked"
        warnings.append("pending checks require --wait-ci")
    if execute and status != "blocked" and not allow_run_agent and feedback_items:
        status = "blocked"
        warnings.append("allow_run_agent=false")

    result = PRFeedbackResult(
        run_id=run_id,
        run_dir=run_dir_path,
        status=status,
        pr=pr,
        feedback_freshness=freshness,
        checks=checks,
        log_summaries=log_summaries,
        review_comments=review_comments,
        feedback_items=feedback_items,
        github_api_called=github_api_called,
        remote_head_checked=True,
        api_degraded=bool(warnings),
        warnings=warnings,
    )
    followup_task_text = build_followup_task(pr=pr, feedback_items=feedback_items, source_run_id=run_id)
    followup_task_path = write_followup_task(followup_task_text, artifact_paths["followup_task"], overwrite=True)
    ci_status_path, review_feedback_path, report_path, workflow_path = _write_base_artifacts(result=PRFeedbackResult(**{**result.__dict__, "followup_task_path": followup_task_path}), artifact_paths=artifact_paths, source_auto_pr_manifest_path=manifest_path, feedback_action_template=feedback_action_template, overwrite=True)
    result = PRFeedbackResult(**{**result.__dict__, "ci_status_path": ci_status_path, "review_feedback_path": review_feedback_path, "ci_feedback_report_path": report_path, "followup_task_path": followup_task_path, "pr_update_plan_path": artifact_paths["pr_update_plan"], "feedback_workflow_path": workflow_path})

    if not execute or status == "no_feedback" or status == "blocked":
        manifest_written = _write_manifest(output_path=artifact_paths["ci_feedback_manifest"], result=result, source_auto_pr_manifest_path=manifest_path, artifacts={"ci_status": ci_status_path, "review_feedback": review_feedback_path, "ci_feedback_report": report_path, "followup_task": followup_task_path, "pr_update_plan": artifact_paths["pr_update_plan"], "feedback_workflow": workflow_path}, overwrite=True)
        return PRFeedbackResult(**{**result.__dict__, "ci_feedback_manifest_path": manifest_written})

    try:
        attempt = create_followup_attempt(run_dir_path, source_feedback_manifest_path=manifest_path, followup_task_path=followup_task_path, overwrite_attempt=False)
        copy_followup_task_to_attempt(attempt)
        attempt_repo_path = _prepare_followup_repo(source_artifact_manifest=source_artifact_manifest, run_id=run_id, attempt_id=attempt.attempt_id)
        followup_result = run_agent_task(task=followup_task_text, repo=attempt_repo_path, runs_dir=attempt.attempt_dir.parent, run_id=attempt.attempt_id)
        trace_path = Path(followup_result.trace_path or attempt.attempt_dir / "trace.jsonl")
        attempt_report_path = trace_path.with_name("report.md")
        from codepilot.report.generator import generate_report

        generate_report(trace_path, attempt_report_path, overwrite=True)
        patch_path, patch_metadata = export_patch_with_metadata(attempt_repo_path, attempt.attempt_dir / "changes.patch")
        new_commit_sha = None
        commit_created = False
        push_update_executed = False
        if patch_metadata.changed_files:
            new_commit_sha = prepare_followup_commit(
                attempt_repo_path,
                attempt_manifest_path=attempt.attempt_dir / "followup_attempt_manifest.json",
                patch_metadata=patch_metadata,
                issue_title=str((auto_pr_manifest.get("pr_request") or {}).get("title") or pr.url),
                tests_summary=followup_result.last_test_status,
                run_id=run_id,
            )
            commit_created = True
            if allow_push_update:
                push_result = push_pr_branch_update_if_allowed(
                    repo_path=attempt_repo_path,
                    pr=pr,
                    new_commit_sha=new_commit_sha,
                    expected_current_head_sha=pr.head_sha or new_commit_sha,
                    execute=execute,
                    allow_push_update=allow_push_update,
                )
                push_update_executed = bool(push_result.get("pushed"))
        comment_posted = False
        if allow_comment:
            try:
                github_client.post_pr_comment(pr, _comment_body(result))
                comment_posted = True
                github_api_called = True
            except PRFeedbackGitHubError as exc:
                warnings.append(redact_feedback_text(str(exc)))
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
        final_status: PRFeedbackStatus = "branch_updated" if push_update_executed else "commit_created" if commit_created else "agent_ran"
        final_result = PRFeedbackResult(
            **{
                **result.__dict__,
                "status": final_status,
                "followup_attempt": final_attempt,
                "agent_ran": True,
                "patch_generated": bool(patch_metadata.changed_files),
                "commit_created": commit_created,
                "new_commit_sha": new_commit_sha,
                "push_update_executed": push_update_executed,
                "comment_posted": comment_posted,
                "github_api_called": github_api_called or comment_posted,
                "warnings": warnings,
            }
        )
        manifest_written = _write_manifest(
            output_path=artifact_paths["ci_feedback_manifest"],
            result=final_result,
            source_auto_pr_manifest_path=manifest_path,
            artifacts={
                "ci_status": ci_status_path,
                "review_feedback": review_feedback_path,
                "ci_feedback_report": report_path,
                "followup_task": followup_task_path,
                "pr_update_plan": artifact_paths["pr_update_plan"],
                "feedback_workflow": workflow_path,
                "attempt_report": attempt_report_path,
                "attempt_patch": patch_path,
            },
            latest_attempt_id=attempt.attempt_id,
            overwrite=True,
        )
        return PRFeedbackResult(**{**final_result.__dict__, "ci_feedback_manifest_path": manifest_written})
    except PRFeedbackGitHubError as exc:
        warnings.append(redact_feedback_text(str(exc)))
        final_result = PRFeedbackResult(**{**result.__dict__, "status": "api_degraded", "warnings": warnings, "api_degraded": True})
        manifest_written = _write_manifest(output_path=artifact_paths["ci_feedback_manifest"], result=final_result, source_auto_pr_manifest_path=manifest_path, artifacts={"ci_status": ci_status_path, "review_feedback": review_feedback_path, "ci_feedback_report": report_path, "followup_task": followup_task_path, "pr_update_plan": artifact_paths["pr_update_plan"], "feedback_workflow": workflow_path}, overwrite=True)
        return PRFeedbackResult(**{**final_result.__dict__, "ci_feedback_manifest_path": manifest_written})
