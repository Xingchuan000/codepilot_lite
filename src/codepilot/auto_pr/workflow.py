from __future__ import annotations

"""Controlled Auto PR 主 workflow。"""

import json
from datetime import UTC, datetime
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from codepilot.auto_pr.commenter import build_issue_comment_body, extract_issue_number, post_issue_comment_if_allowed
from codepilot.auto_pr.git_push import push_branch
from codepilot.auto_pr.git_remote import build_push_plan, get_default_base_branch, resolve_repo_ref
from codepilot.auto_pr.github_action import write_controlled_auto_pr_workflow_template
from codepilot.auto_pr.github_client import (
    GitHubClientProtocol,
    RestGitHubClient,
    assert_github_token_available,
    redact_github_error,
)
from codepilot.auto_pr.manifest_loader import (
    load_pr_assist_manifest,
    resolve_required_auto_pr_artifacts,
    validate_pr_assist_manifest,
)
from codepilot.auto_pr.models import (
    AutoPRError,
    AutoPRManifestInvalidError,
    AutoPRResult,
    AutoPRSafetyGate,
    BranchPushPlan,
    GitHubRepoRef,
    PRCreateRequest,
    PRCreateResult,
    RemoteActionMode,
    to_auto_pr_jsonable,
)
from codepilot.auto_pr.pr_creator import build_pr_create_request, create_pr_if_allowed, extract_pr_title
from codepilot.auto_pr.safety import assert_remote_side_effect_allowed, build_auto_pr_safety_gate, summarize_safety_gate
from codepilot.auto_pr.workflow_inputs import validate_head_branch, validate_repo_slug, validate_run_id
from codepilot.repo.git_utils import sha256_file
from codepilot.security.secrets import scan_token_like_strings
from codepilot.workflow_artifacts import build_artifact_record, prepare_owned_artifacts
from codepilot.auto_pr.manifest_loader import load_source_artifact_manifest


AUTO_PR_ARTIFACT_NAMES = [
    "auto_pr_plan.md",
    "auto_pr_manifest.json",
    "controlled_auto_pr_workflow.yml",
]


def _status_from_gate(gate: AutoPRSafetyGate, *, execute: bool, blockers: list[str], validation_errors: list[str]) -> str:
    """根据校验结果与安全门状态推导 workflow 状态。"""

    if validation_errors:
        return "manifest_invalid"
    if gate.status != "pass":
        return "blocked_by_safety" if execute else "planned_with_blockers"
    if blockers:
        return "planned_with_blockers"
    return "planned"


def render_auto_pr_plan(
    *,
    run_id: str,
    mode: RemoteActionMode,
    safety_gate: AutoPRSafetyGate,
    source_artifact_manifest: dict[str, Any],
    pr_assist_manifest: dict[str, Any],
    branch_push_plan: BranchPushPlan | None,
    pr_request: PRCreateRequest | None,
    blockers: list[str],
    warnings: list[str],
) -> str:
    """渲染 dry-run / execute 共用的计划说明。"""

    status = _status_from_gate(safety_gate, execute=mode == "execute", blockers=blockers, validation_errors=[])
    source_artifacts = [
        "pr_assist_manifest.json",
        "artifact_manifest.json",
        "pr_body.md",
        "changes.patch",
        "pr_summary.md",
        "restore_plan.md",
    ]
    lines = [
        "# Controlled Auto PR Plan",
        "",
        "## Run",
        "",
        f"- Run ID: {run_id}",
        f"- Mode: {mode}",
        f"- Status: {status}",
        "",
        "## Safety Gate",
        "",
        f"- status: {safety_gate.status}",
        f"- reasons: {', '.join(safety_gate.reasons) if safety_gate.reasons else 'none'}",
        f"- warnings: {', '.join(safety_gate.warnings) if safety_gate.warnings else 'none'}",
        "",
        "## Source Artifacts",
        "",
    ]
    lines.extend(f"- {name}" for name in source_artifacts)
    lines.extend(["", "## Branch / Push Plan", ""])
    if branch_push_plan is None:
        lines.append("- unavailable")
    else:
        lines.extend(
            [
                f"- local_branch: {branch_push_plan.local_branch}",
                f"- remote_branch: {branch_push_plan.remote_branch}",
                f"- base_branch: {branch_push_plan.base_branch}",
                f"- commit_sha: {branch_push_plan.commit_sha}",
                f"- push_refspec: {branch_push_plan.push_refspec}",
                f"- will_push: {'yes' if branch_push_plan.will_push else 'no'}",
                f"- branch_collision: {'yes' if branch_push_plan.branch_collision else 'no'}",
                f"- remote_ref_verified: {'yes' if branch_push_plan.remote_ref_verified else 'no'}",
            ]
        )
    lines.extend(["", "## PR Plan", ""])
    if pr_request is None:
        lines.append("- unavailable")
    else:
        lines.extend(
            [
                f"- title: {pr_request.title}",
                f"- body_path: {pr_request.body_path.name}",
                f"- draft: {'yes' if pr_request.draft else 'no'}",
                f"- create_pr allowed: {'yes' if mode == 'execute' else 'no'}",
            ]
        )
    lines.extend(
        [
            "",
            "## Side Effects",
            "",
            "- push_executed: no",
            "- pr_created: no",
            "- github_api_called: no",
            "- comment_posted: no",
            "",
            "## Blockers",
            "",
        ]
    )
    lines.extend(f"- {item}" for item in blockers or ["none"])
    lines.extend(["", "## Warnings", ""])
    lines.extend(f"- {item}" for item in warnings or ["none"])
    return "\n".join(lines).rstrip() + "\n"


def write_auto_pr_plan(text: str, output_path: str | Path, *, overwrite: bool = False) -> Path:
    """写 auto_pr_plan.md。"""

    path = Path(output_path)
    if path.exists() and not overwrite:
        raise FileExistsError(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def write_auto_pr_manifest(
    *,
    output_path: str | Path,
    result: AutoPRResult,
    pr_assist_manifest_path: Path,
    source_artifact_manifest_path: Path,
    artifacts: dict[str, Path | None],
    mode: RemoteActionMode,
    blockers: list[str],
    warnings: list[str],
    overwrite: bool = False,
) -> Path:
    """写 auto_pr_manifest.json，并再次扫描 token-like 内容。"""

    path = Path(output_path)
    if path.exists() and not overwrite:
        raise FileExistsError(path)
    payload = {
        "schema_version": "codepilot.auto_pr_manifest.v1",
        "created_at": datetime.now(UTC).isoformat(),
        "run_id": result.run_id,
        "status": result.status,
        "mode": mode,
        "source_pr_assist_manifest": "pr_assist_manifest.json",
        "source_pr_assist_manifest_sha256": sha256_file(pr_assist_manifest_path),
        "source_artifact_manifest": "artifact_manifest.json",
        "source_artifact_manifest_sha256": sha256_file(source_artifact_manifest_path),
        "safety_gate": to_auto_pr_jsonable(result.safety_gate),
        "branch_push_plan": to_auto_pr_jsonable(result.branch_push_plan),
        "pr_request": to_auto_pr_jsonable(result.pr_request),
        "pr_result": to_auto_pr_jsonable(result.pr_result),
        "side_effects": {
            "push_executed": result.push_executed,
            "pr_created": result.pr_created,
            "github_api_called": result.github_api_called,
            "comment_posted": result.comment_posted,
        },
        "blockers": blockers,
        "warnings": warnings,
        "generated_artifacts": [
            build_artifact_record(name, artifact_path, run_dir=result.run_dir)
            for name, artifact_path in artifacts.items()
            if artifact_path is not None
        ],
    }
    if scan_token_like_strings(payload):
        raise ValueError("token-like string detected in auto_pr_manifest payload")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    final_payload = json.loads(path.read_text(encoding="utf-8"))
    final_payload["generated_artifacts"].append(
        {
            "name": "auto_pr_manifest",
            "path": "auto_pr_manifest.json",
            "exists": True,
            "size_bytes": path.stat().st_size,
            "sha256": sha256_file(path),
        }
    )
    if scan_token_like_strings(final_payload):
        raise ValueError("token-like string detected in auto_pr_manifest payload")
    path.write_text(json.dumps(final_payload, indent=2, ensure_ascii=False), encoding="utf-8")
    if scan_token_like_strings(path.read_text(encoding="utf-8", errors="ignore")):
        raise ValueError("token-like string detected in auto_pr_manifest file")
    return path


def _write_manifest_invalid_result(
    *,
    run_dir_path: Path,
    run_id: str,
    mode: RemoteActionMode,
    pr_assist_manifest_path: Path,
    reason: str,
    overwrite: bool,
) -> AutoPRResult:
    """在 source manifest 缺失或损坏时仍写出最小可审计 artifact。"""

    safety_gate = AutoPRSafetyGate(status="fail", reasons=[reason])
    plan_path = write_auto_pr_plan(
        render_auto_pr_plan(
            run_id=run_id,
            mode=mode,
            safety_gate=safety_gate,
            source_artifact_manifest={},
            pr_assist_manifest={},
            branch_push_plan=None,
            pr_request=None,
            blockers=[reason],
            warnings=[],
        ),
        run_dir_path / "auto_pr_plan.md",
        overwrite=True,
    )
    result = AutoPRResult(
        run_id=run_id,
        run_dir=run_dir_path,
        status="manifest_invalid",
        safety_gate=safety_gate,
        auto_pr_plan_path=plan_path,
    )
    manifest_path = write_auto_pr_manifest(
        output_path=run_dir_path / "auto_pr_manifest.json",
        result=result,
        pr_assist_manifest_path=pr_assist_manifest_path,
        source_artifact_manifest_path=run_dir_path / "artifact_manifest.json",
        artifacts={"auto_pr_plan": plan_path},
        mode=mode,
        blockers=[reason],
        warnings=[],
        overwrite=overwrite,
    )
    return AutoPRResult(**{**result.__dict__, "auto_pr_manifest_path": manifest_path})



@dataclass
class AutoPRWorkflowContext:
    run_dir: Path
    mode: RemoteActionMode
    execute: bool
    allow_push: bool
    allow_create_pr: bool
    allow_comment: bool
    allow_empty_pr: bool
    remote_name: str
    base_branch: str | None
    head_branch: str | None
    repo_slug: str | None
    token_env: str
    draft: bool
    generate_workflow_template: bool
    github_client: GitHubClientProtocol | None
    pr_assist_manifest_path: Path
    pr_assist_manifest: dict[str, Any]
    source_manifest: dict[str, Any]
    artifacts: dict[str, Path]
    run_id: str
    safety_gate: AutoPRSafetyGate
    validation_errors: list[str]
    manifest_errors: list[str]
    blockers: list[str]
    warnings: list[str]
    repo_path: str | Path | None
    repo_ref: GitHubRepoRef | None
    branch_push_plan: BranchPushPlan | None
    pr_request: PRCreateRequest | None
    plan_path: Path | None = None
    controlled_workflow_path: Path | None = None


def _build_auto_pr_context(
    *,
    run_dir: str | Path,
    execute: bool,
    allow_push: bool,
    allow_create_pr: bool,
    allow_comment: bool,
    allow_empty_pr: bool,
    remote_name: str,
    base_branch: str | None,
    head_branch: str | None,
    repo_slug: str | None,
    token_env: str,
    draft: bool,
    generate_workflow_template: bool,
    github_client: GitHubClientProtocol | None,
    overwrite: bool,
) -> AutoPRWorkflowContext | AutoPRResult:
    mode: RemoteActionMode = "execute" if execute else "dry_run"
    run_dir_path = Path(run_dir).expanduser().resolve()
    if not run_dir_path.exists() or not run_dir_path.is_dir():
        raise FileNotFoundError(run_dir_path)
    prepare_owned_artifacts(
        run_dir_path,
        AUTO_PR_ARTIFACT_NAMES,
        overwrite=overwrite,
        label="Auto PR",
    )
    pr_assist_manifest_path = run_dir_path / "pr_assist_manifest.json"
    if not pr_assist_manifest_path.exists():
        raise FileNotFoundError(pr_assist_manifest_path)
    try:
        pr_assist_manifest = load_pr_assist_manifest(pr_assist_manifest_path)
    except AutoPRManifestInvalidError as exc:
        minimal_result = AutoPRResult(
            run_id=run_dir_path.name,
            run_dir=run_dir_path,
            status="manifest_invalid",
            safety_gate=AutoPRSafetyGate(status="fail", reasons=[str(exc)]),
        )
        plan_path = write_auto_pr_plan(
            render_auto_pr_plan(
                run_id=run_dir_path.name,
                mode=mode,
                safety_gate=minimal_result.safety_gate,
                source_artifact_manifest={},
                pr_assist_manifest={},
                branch_push_plan=None,
                pr_request=None,
                blockers=[str(exc)],
                warnings=[],
            ),
            run_dir_path / "auto_pr_plan.md",
            overwrite=True,
        )
        manifest_path = write_auto_pr_manifest(
            output_path=run_dir_path / "auto_pr_manifest.json",
            result=AutoPRResult(**{**minimal_result.__dict__, "auto_pr_plan_path": plan_path}),
            pr_assist_manifest_path=pr_assist_manifest_path,
            source_artifact_manifest_path=run_dir_path / "artifact_manifest.json",
            artifacts={"auto_pr_plan": plan_path},
            mode=mode,
            blockers=[str(exc)],
            warnings=[],
            overwrite=True,
        )
        return AutoPRResult(**{**minimal_result.__dict__, "auto_pr_plan_path": plan_path, "auto_pr_manifest_path": manifest_path})

    run_id = validate_run_id(str(pr_assist_manifest.get("run_id") or run_dir_path.name))
    validated_head_branch = validate_head_branch(head_branch, run_id=run_id)
    validated_repo_slug = validate_repo_slug(repo_slug)
    try:
        source_manifest = load_source_artifact_manifest(run_dir_path, pr_assist_manifest)
    except (FileNotFoundError, AutoPRManifestInvalidError) as exc:
        return _write_manifest_invalid_result(
            run_dir_path=run_dir_path,
            run_id=run_id,
            mode=mode,
            pr_assist_manifest_path=pr_assist_manifest_path,
            reason=redact_github_error(str(exc)),
            overwrite=True,
        )

    validation_errors = validate_pr_assist_manifest(pr_assist_manifest, run_dir_path, allow_empty_pr=allow_empty_pr)
    safety_gate = build_auto_pr_safety_gate(
        pr_assist_manifest=pr_assist_manifest,
        source_artifact_manifest=source_manifest,
        allow_empty_pr=allow_empty_pr,
    )
    warnings: list[str] = []
    manifest_errors = [
        item
        for item in validation_errors
        if item
        not in {
            "pr_assist safety_gate.status must be pass",
            "source artifact manifest safety_decision must not be deny",
        }
    ]
    blockers = [item for item in validation_errors if item not in manifest_errors]
    if safety_gate.status != "pass":
        blockers.extend(safety_gate.reasons or [f"safety_gate={safety_gate.status}"])
    artifacts = resolve_required_auto_pr_artifacts(run_dir_path, pr_assist_manifest, source_manifest)
    repo_path = source_manifest.get("effective_repo_path") or source_manifest.get("repo_path")
    if execute and repo_path == "[REDACTED_PATH]":
        blockers.append("execute requires non-redacted repo path")
    local_branch = str(pr_assist_manifest.get("branch_name") or f"codepilot/{run_id}")
    commit_sha = str(pr_assist_manifest.get("commit_sha") or "")
    resolved_base_branch = (
        base_branch
        or (None if repo_path == "[REDACTED_PATH]" else get_default_base_branch(repo_path, remote_name))
        or ((source_manifest.get("before") or {}).get("branch"))
        or "main"
    )
    resolved_head_branch = validated_head_branch or f"codepilot/{run_id}"
    branch_push_plan: BranchPushPlan | None = None
    pr_request: PRCreateRequest | None = None
    repo_ref: GitHubRepoRef | None = None
    if not validation_errors:
        try:
            repo_ref = resolve_repo_ref(repo_path, remote_name=remote_name, repo_slug=validated_repo_slug)
            branch_push_plan = build_push_plan(
                repo_path=repo_path,
                remote_name=remote_name,
                local_branch=local_branch,
                remote_branch=resolved_head_branch,
                base_branch=resolved_base_branch,
                commit_sha=commit_sha,
            )
            pr_request = build_pr_create_request(
                repo=repo_ref,
                pr_body_path=artifacts["pr_body"],
                title=extract_pr_title(
                    pr_body_path=artifacts["pr_body"],
                    issue_json_path=run_dir_path / "issue.json",
                    fallback=f"CodePilot changes for {run_id}",
                ),
                head_branch=resolved_head_branch,
                base_branch=resolved_base_branch,
                draft=draft,
            )
        except Exception as exc:
            blockers.append(redact_github_error(str(exc)))
    return AutoPRWorkflowContext(
        run_dir=run_dir_path,
        mode=mode,
        execute=execute,
        allow_push=allow_push,
        allow_create_pr=allow_create_pr,
        allow_comment=allow_comment,
        allow_empty_pr=allow_empty_pr,
        remote_name=remote_name,
        base_branch=base_branch,
        head_branch=validated_head_branch,
        repo_slug=validated_repo_slug,
        token_env=token_env,
        draft=draft,
        generate_workflow_template=generate_workflow_template,
        github_client=github_client,
        pr_assist_manifest_path=pr_assist_manifest_path,
        pr_assist_manifest=pr_assist_manifest,
        source_manifest=source_manifest,
        artifacts=artifacts,
        run_id=run_id,
        safety_gate=safety_gate,
        validation_errors=validation_errors,
        manifest_errors=manifest_errors,
        blockers=blockers,
        warnings=warnings,
        repo_path=repo_path,
        repo_ref=repo_ref,
        branch_push_plan=branch_push_plan,
        pr_request=pr_request,
    )


def _persist_auto_pr_plan(ctx: AutoPRWorkflowContext) -> AutoPRResult:
    status = _status_from_gate(
        ctx.safety_gate,
        execute=ctx.execute,
        blockers=ctx.blockers,
        validation_errors=ctx.manifest_errors,
    )
    ctx.plan_path = write_auto_pr_plan(
        render_auto_pr_plan(
            run_id=ctx.run_id,
            mode=ctx.mode,
            safety_gate=ctx.safety_gate,
            source_artifact_manifest=ctx.source_manifest,
            pr_assist_manifest=ctx.pr_assist_manifest,
            branch_push_plan=ctx.branch_push_plan,
            pr_request=ctx.pr_request,
            blockers=ctx.blockers,
            warnings=ctx.warnings,
        ),
        ctx.run_dir / "auto_pr_plan.md",
        overwrite=True,
    )
    if ctx.generate_workflow_template:
        ctx.controlled_workflow_path = write_controlled_auto_pr_workflow_template(
            ctx.run_dir / "controlled_auto_pr_workflow.yml",
            overwrite=True,
        )
    return AutoPRResult(
        run_id=ctx.run_id,
        run_dir=ctx.run_dir,
        status=status,
        safety_gate=ctx.safety_gate,
        auto_pr_plan_path=ctx.plan_path,
        controlled_workflow_path=ctx.controlled_workflow_path,
        branch_push_plan=ctx.branch_push_plan,
        pr_request=ctx.pr_request,
        warnings=ctx.warnings,
    )


def _execute_auto_pr_phase(ctx: AutoPRWorkflowContext, planned: AutoPRResult) -> AutoPRResult:
    pushed_plan = ctx.branch_push_plan
    push_executed = False
    pr_created = False
    github_api_called = False
    comment_posted = False
    pr_result: PRCreateResult | None = None
    try:
        assert_remote_side_effect_allowed(
            safety_gate=ctx.safety_gate,
            execute=ctx.execute,
            allow_push=ctx.allow_push,
            allow_create_pr=ctx.allow_create_pr,
        )
        if ctx.execute and ctx.allow_create_pr and ctx.github_client is None:
            assert_github_token_available(ctx.token_env)
        if ctx.branch_push_plan is None or ctx.pr_request is None:
            raise AutoPRError("push plan or pr request unavailable")
        pushed_plan = push_branch(
            repo_path=ctx.repo_path,
            push_plan=ctx.branch_push_plan,
            execute=ctx.execute,
            allow_push=ctx.allow_push,
        )
        push_executed = pushed_plan.will_push
        client = ctx.github_client or RestGitHubClient(token_env=ctx.token_env)
        try:
            pr_result = create_pr_if_allowed(
                client=client,
                request=ctx.pr_request,
                execute=ctx.execute,
                allow_create_pr=ctx.allow_create_pr,
                push_executed=push_executed,
                remote_ref_verified=pushed_plan.remote_ref_verified,
            )
            pr_created = pr_result.created
            github_api_called = pr_result.api_called
        except Exception as exc:
            ctx.warnings.append(redact_github_error(str(exc)))
            return AutoPRResult(
                run_id=ctx.run_id,
                run_dir=ctx.run_dir,
                status="failed",
                safety_gate=ctx.safety_gate,
                auto_pr_plan_path=ctx.plan_path,
                controlled_workflow_path=ctx.controlled_workflow_path,
                branch_push_plan=pushed_plan,
                pr_request=ctx.pr_request,
                pr_result=pr_result,
                push_executed=push_executed,
                pr_created=False,
                github_api_called=github_api_called,
                comment_posted=False,
                warnings=ctx.warnings,
            )
        if pr_created and ctx.repo_ref is not None:
            try:
                comment = post_issue_comment_if_allowed(
                    client=client,
                    repo=ctx.repo_ref,
                    issue_number=extract_issue_number(ctx.source_manifest, issue_json_path=ctx.run_dir / "issue.json"),
                    body=build_issue_comment_body(
                        pr_url=pr_result.url,
                        run_id=ctx.run_id,
                        safety_summary=summarize_safety_gate(ctx.safety_gate),
                        artifact_summary=["auto_pr_plan.md", "auto_pr_manifest.json", "pr_body.md"],
                        dry_run=False,
                    ),
                    execute=ctx.execute,
                    allow_comment=ctx.allow_comment,
                )
                comment_posted = comment is not None
            except Exception as exc:
                ctx.warnings.append(redact_github_error(str(exc)))
        final_status = "pr_created" if pr_created else ("pushed" if push_executed else "failed")
        return AutoPRResult(
            run_id=ctx.run_id,
            run_dir=ctx.run_dir,
            status=final_status,
            safety_gate=ctx.safety_gate,
            auto_pr_plan_path=ctx.plan_path,
            controlled_workflow_path=ctx.controlled_workflow_path,
            branch_push_plan=pushed_plan,
            pr_request=ctx.pr_request,
            pr_result=pr_result,
            push_executed=push_executed,
            pr_created=pr_created,
            github_api_called=github_api_called,
            comment_posted=comment_posted,
            warnings=ctx.warnings,
        )
    except Exception as exc:
        ctx.warnings.append(redact_github_error(str(exc)))
        return AutoPRResult(
            run_id=ctx.run_id,
            run_dir=ctx.run_dir,
            status="failed",
            safety_gate=ctx.safety_gate,
            auto_pr_plan_path=ctx.plan_path,
            controlled_workflow_path=ctx.controlled_workflow_path,
            branch_push_plan=pushed_plan,
            pr_request=ctx.pr_request,
            pr_result=pr_result,
            push_executed=push_executed,
            pr_created=pr_created,
            github_api_called=github_api_called,
            comment_posted=comment_posted,
            warnings=ctx.warnings,
        )


def _finalize_auto_pr(ctx: AutoPRWorkflowContext, result: AutoPRResult) -> AutoPRResult:
    manifest_path = write_auto_pr_manifest(
        output_path=ctx.run_dir / "auto_pr_manifest.json",
        result=result,
        pr_assist_manifest_path=ctx.pr_assist_manifest_path,
        source_artifact_manifest_path=ctx.run_dir / "artifact_manifest.json",
        artifacts={
            "auto_pr_plan": ctx.plan_path,
            "controlled_auto_pr_workflow": ctx.controlled_workflow_path,
        },
        mode=ctx.mode,
        blockers=ctx.blockers,
        warnings=ctx.warnings,
        overwrite=True,
    )
    return AutoPRResult(**{**result.__dict__, "auto_pr_manifest_path": manifest_path})


def run_auto_pr(
    *,
    run_dir: str | Path,
    dry_run: bool = True,
    execute: bool = False,
    allow_push: bool = False,
    allow_create_pr: bool = False,
    allow_comment: bool = False,
    allow_empty_pr: bool = False,
    remote_name: str = "origin",
    base_branch: str | None = None,
    head_branch: str | None = None,
    repo_slug: str | None = None,
    token_env: str = "GITHUB_TOKEN",
    draft: bool = True,
    generate_workflow_template: bool = True,
    overwrite: bool = False,
    github_client: GitHubClientProtocol | None = None,
) -> AutoPRResult:
    ctx = _build_auto_pr_context(
        run_dir=run_dir,
        execute=execute,
        allow_push=allow_push,
        allow_create_pr=allow_create_pr,
        allow_comment=allow_comment,
        allow_empty_pr=allow_empty_pr,
        remote_name=remote_name,
        base_branch=base_branch,
        head_branch=head_branch,
        repo_slug=repo_slug,
        token_env=token_env,
        draft=draft,
        generate_workflow_template=generate_workflow_template,
        github_client=github_client,
        overwrite=overwrite,
    )
    if isinstance(ctx, AutoPRResult):
        return ctx
    planned = _persist_auto_pr_plan(ctx)
    if ctx.manifest_errors or not execute or planned.status == "blocked_by_safety":
        return _finalize_auto_pr(ctx, planned)
    return _finalize_auto_pr(ctx, _execute_auto_pr_phase(ctx, planned))
