from __future__ import annotations

"""第十五步 Post-PR automation 的控制器。"""

import json
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from codepilot.post_pr.approval import (
    ApprovalDecision,
    ApprovalRequest,
    approval_terminal_reason,
    build_approval_request,
    is_action_approved,
    load_approval_decision,
    load_approval_request,
    synthesize_cli_approval_decision,
    validate_approval_decision,
    validate_approval_request_integrity,
    write_approval_decision,
    write_approval_request,
)
from codepilot.post_pr.feedback_delta import (
    build_feedback_delta,
    classify_check_terminal_reason,
    extract_feedback_fingerprints,
    load_ci_feedback_manifest,
    should_stop_for_repeated_feedback,
)
from codepilot.post_pr.github_action import write_post_pr_automation_workflow_template
from codepilot.post_pr.models import (
    ApprovalAction,
    ArtifactSnapshotEntry,
    FeedbackDelta,
    PostPRAutomationInput,
    PostPRAutomationResult,
    PostPRAutomationState,
    PostPRAutomationStatus,
    PostPRRoundRef,
    PostPRTerminalReason,
    SideEffectAction,
    SideEffectEntry,
    SideEffectLedger,
    SideEffectStatus,
    validate_max_rounds,
)
from codepilot.post_pr.report import write_post_pr_automation_report
from codepilot.post_pr.round_runner import push_existing_followup_commit_if_approved, run_feedback_round, write_round_manifest
from codepilot.post_pr.state_store import (
    StateLock,
    acquire_state_lock,
    append_side_effect,
    atomic_write_json,
    clear_post_pr_dir,
    has_succeeded_effect,
    initial_post_pr_state,
    initial_side_effects,
    latest_successful_commit_for_round,
    load_post_pr_state,
    load_side_effects,
    mark_terminal,
    release_state_lock,
    resolve_post_pr_dir,
    resolve_side_effects_path,
    resolve_state_path,
    upsert_round,
    write_post_pr_state,
)
from codepilot.pr_feedback.github_client import PRFeedbackGitHubClientProtocol
from codepilot.repo.git_utils import sha256_file


@dataclass
class PostPRRuntime:
    """一次控制器调用共享的类型化运行时，集中保存状态和产物路径。"""

    automation_input: PostPRAutomationInput
    post_pr_dir: Path
    state_path: Path
    side_effects_path: Path
    state: PostPRAutomationState
    side_effects: SideEffectLedger
    approval_request_path: Path | None = None
    approval_decision_path: Path | None = None
    workflow_path: Path | None = None


@dataclass(frozen=True)
class CollectRoundResult:
    """采集阶段交给审批阶段的完整、已规范化结果。"""

    round_ref: PostPRRoundRef
    manifest: dict[str, Any]
    snapshots: list[ArtifactSnapshotEntry]
    delta: FeedbackDelta | None
    resumed: bool = False


@dataclass(frozen=True)
class ApprovalRoundResult:
    request: ApprovalRequest
    decision: ApprovalDecision | None


@dataclass(frozen=True)
class ExecutionRoundResult:
    round_ref: PostPRRoundRef
    manifest: dict[str, Any] | None
    snapshots: list[ArtifactSnapshotEntry]


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _load_json_object(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"JSON object required: {path}")
    return data


def _is_hard_terminal_state(state: PostPRAutomationState) -> bool:
    return state.status in {"blocked", "failed", "no_feedback", "max_rounds_reached", "repeated_feedback"} or state.terminal_reason in {
        "approval_rejected",
        "approval_expired",
        "stale_approval",
        "stale_head",
        "pending_checks",
        "ci_timeout",
        "checks_cancelled",
        "checks_skipped",
        "checks_unavailable",
        "unsafe_branch",
        "api_degraded",
        "manifest_invalid",
        "state_locked",
    }


def _find_pending_approval_round(state: PostPRAutomationState) -> PostPRRoundRef | None:
    if state.status not in {"awaiting_approval", "patch_ready", "agent_ran"} or state.terminal_reason != "awaiting_approval" or not state.rounds:
        return None
    latest = state.rounds[-1]
    if latest.collect_manifest_path is None and latest.latest_pr_feedback_manifest_path is None:
        return None
    return latest


def _write_round_delta(round_dir: Path, phase: str, delta: FeedbackDelta) -> Path:
    return atomic_write_json(delta, round_dir / phase / "feedback_delta.json", overwrite=True)


def _round_manifest_path(round_dir: Path, phase: str) -> Path:
    return round_dir / phase / "round_manifest.json"


def _result_from_state(
    *,
    runtime: PostPRRuntime,
    manifest_path: Path | None,
    report_path: Path | None,
) -> PostPRAutomationResult:
    """结果只投影类型状态，不再重复解析 state.json 的字典结构。"""

    state = runtime.state
    return PostPRAutomationResult(
        run_id=state.run_id,
        run_dir=state.run_dir,
        post_pr_dir=runtime.post_pr_dir,
        status=state.status,
        terminal_reason=state.terminal_reason,
        rounds=list(state.rounds),
        latest_round_id=state.latest_round_id,
        approval_request_path=runtime.approval_request_path,
        approval_decision_path=runtime.approval_decision_path,
        side_effects_path=runtime.side_effects_path,
        state_path=runtime.state_path,
        manifest_path=manifest_path,
        report_path=report_path,
        workflow_path=runtime.workflow_path,
        warnings=list(state.warnings),
        blockers=list(state.blockers),
    )


def _action_list(*, approve_comment: bool) -> list[ApprovalAction]:
    actions: list[ApprovalAction] = ["run_agent", "push_update"]
    if approve_comment:
        actions.append("post_comment")
    return actions


def _write_post_pr_automation_manifest(
    *,
    result: PostPRAutomationResult,
    auto_pr_manifest_path: Path,
    state: PostPRAutomationState,
    side_effects: SideEffectLedger,
) -> Path:
    def _display(path: Path | None) -> str | None:
        if path is None:
            return None
        try:
            return str(path.relative_to(result.run_dir))
        except ValueError:
            return path.name

    latest_manifest = state.rounds[-1].latest_pr_feedback_manifest_path if state.rounds else None
    payload = {
        "schema_version": "codepilot.post_pr_automation_manifest.v1",
        "created_at": _now_iso(),
        "run_id": result.run_id,
        "status": result.status,
        "terminal_reason": result.terminal_reason,
        "source_auto_pr_manifest": "auto_pr_manifest.json",
        "source_auto_pr_manifest_sha256": sha256_file(auto_pr_manifest_path),
        "state_path": "post_pr/state.json",
        "side_effects_path": "post_pr/side_effects.json",
        "approval_request_path": _display(result.approval_request_path),
        "approval_decision_path": _display(result.approval_decision_path),
        "rounds": list(state.rounds),
        "manifest_hash_chain": {
            "auto_pr_manifest_sha256": sha256_file(auto_pr_manifest_path),
            "latest_ci_feedback_manifest_sha256": sha256_file(latest_manifest) if latest_manifest else None,
            "approval_request_sha256": sha256_file(result.approval_request_path) if result.approval_request_path else None,
            "approval_decision_sha256": sha256_file(result.approval_decision_path) if result.approval_decision_path else None,
        },
        "side_effects_summary": {
            "effects_total": len(side_effects.effects),
            "last_action": side_effects.effects[-1].action if side_effects.effects else None,
        },
        "blockers": list(result.blockers),
        "warnings": list(result.warnings),
        "generated_artifacts": [
            {"name": "state.json", "path": "post_pr/state.json"},
            {"name": "side_effects.json", "path": "post_pr/side_effects.json"},
            {"name": "approval_request.md", "path": "post_pr/approval_request.md"} if result.approval_request_path else None,
            {"name": "approval_request.json", "path": "post_pr/approval_request.json"} if result.approval_request_path else None,
            {"name": "approval_decision.json", "path": "post_pr/approval_decision.json"} if result.approval_decision_path else None,
            {"name": "post_pr_automation_report.md", "path": "post_pr/post_pr_automation_report.md"},
        ],
    }
    payload["generated_artifacts"] = [item for item in payload["generated_artifacts"] if item is not None]
    return atomic_write_json(payload, result.manifest_path or result.post_pr_dir / "post_pr_automation_manifest.json", overwrite=True)


def _append_effect(
    ledger: SideEffectLedger,
    *,
    round_id: str,
    action: SideEffectAction,
    status: SideEffectStatus,
    approval_decision_sha256: str | None = None,
    head_sha_before: str | None = None,
    head_sha_after: str | None = None,
    commit_sha: str | None = None,
    remote_ref: str | None = None,
    comment_marker: str | None = None,
    error: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> SideEffectLedger:
    return append_side_effect(
        ledger,
        SideEffectEntry(
            round_id=round_id,
            action=action,
            status=status,
            approval_decision_sha256=approval_decision_sha256,
            head_sha_before=head_sha_before,
            head_sha_after=head_sha_after,
            commit_sha=commit_sha,
            remote_ref=remote_ref,
            comment_marker=comment_marker,
            executed_at=_now_iso(),
            error=error,
            metadata=metadata or {},
        ),
    )


def _load_or_initialize_runtime(runtime: PostPRRuntime) -> PostPRRuntime:
    """只在持久化边界加载类型对象，控制器后续不再接触状态字典。"""

    automation_input = runtime.automation_input
    state = load_post_pr_state(runtime.state_path) if automation_input.resume else None
    if state is not None and state.run_id != automation_input.run_id:
        raise ValueError("post_pr state run_id mismatch")
    return replace(
        runtime,
        state=state or initial_post_pr_state(
            run_id=automation_input.run_id,
            run_dir=automation_input.run_dir,
            max_rounds=automation_input.max_rounds,
        ),
        side_effects=load_side_effects(runtime.side_effects_path, run_id=automation_input.run_id)
        if automation_input.resume
        else initial_side_effects(automation_input.run_id),
    )


def _restore_existing_artifact_paths(runtime: PostPRRuntime) -> None:
    """只为不再进入审批执行的恢复分支展示既有产物。"""

    request_path = runtime.post_pr_dir / "approval_request.md"
    decision_path = runtime.post_pr_dir / "approval_decision.json"
    workflow_path = runtime.post_pr_dir / "post_pr_automation_workflow.yml"
    runtime.approval_request_path = request_path if request_path.exists() else None
    runtime.approval_decision_path = decision_path if decision_path.exists() else None
    runtime.workflow_path = workflow_path if workflow_path.exists() else None


def _collect_round(
    runtime: PostPRRuntime,
    *,
    round_index: int,
    pending_round: PostPRRoundRef | None,
    github_client: PRFeedbackGitHubClientProtocol | None,
) -> CollectRoundResult:
    config = runtime.automation_input
    round_id = f"round-{round_index:03d}"
    round_dir = runtime.post_pr_dir / round_id
    round_dir.mkdir(parents=True, exist_ok=True)
    if pending_round is not None and pending_round.round_index == round_index:
        collect_manifest_path = pending_round.collect_manifest_path or pending_round.latest_pr_feedback_manifest_path
        if collect_manifest_path is None:
            raise ValueError("missing collect manifest path")
        runtime.state = upsert_round(runtime.state, pending_round)
        return CollectRoundResult(
            round_ref=pending_round,
            manifest=load_ci_feedback_manifest(collect_manifest_path),
            snapshots=[],
            delta=None,
            resumed=True,
        )

    collect_ref, collect_manifest, snapshots = run_feedback_round(
        run_dir=config.run_dir,
        auto_pr_manifest_path=config.auto_pr_manifest_path,
        round_dir=round_dir,
        phase="collect",
        dry_run=True,
        execute=False,
        allow_run_agent=False,
        allow_push_update=False,
        allow_comment=False,
        wait_ci=config.wait_ci,
        poll_interval_seconds=config.poll_interval_seconds,
        timeout_seconds=config.timeout_seconds,
        token_env=config.token_env,
        include_logs=config.include_logs,
        include_success_logs=config.include_success_logs,
        max_log_bytes=config.max_log_bytes,
        max_feedback_items=config.max_feedback_items,
        overwrite=config.overwrite,
        github_client=github_client,
    )
    previous_fingerprints = list(runtime.state.rounds[-1].feedback_fingerprints) if runtime.state.rounds else []
    delta = build_feedback_delta(previous_fingerprints=previous_fingerprints, current_fingerprints=collect_ref.feedback_fingerprints)
    _write_round_delta(round_dir, "collect", delta)
    collect_manifest_path = collect_ref.latest_pr_feedback_manifest_path or config.run_dir / "ci_feedback_manifest.json"
    collect_round = replace(
        collect_ref,
        collect_manifest_path=collect_manifest_path,
        latest_pr_feedback_manifest_path=collect_manifest_path,
    )
    write_round_manifest(
        round_ref=collect_round,
        collect_manifest=collect_manifest,
        execute_manifest=None,
        feedback_delta=delta.__dict__,
        snapshots=snapshots,
        output_path=_round_manifest_path(round_dir, "collect"),
        overwrite=True,
    )
    runtime.state = upsert_round(runtime.state, collect_round)
    return CollectRoundResult(round_ref=collect_round, manifest=collect_manifest, snapshots=snapshots, delta=delta)


def _collect_terminal_reason(
    result: CollectRoundResult,
    *,
    stop_on_repeated_feedback: bool,
) -> tuple[PostPRAutomationStatus, PostPRTerminalReason, list[str]] | None:
    """按原有顺序执行采集安全门，首个命中的门决定终止原因。"""

    if result.resumed:
        return None
    if result.round_ref.status == "blocked":
        reason: PostPRTerminalReason = "manifest_invalid" if any("manifest" in item for item in result.round_ref.blockers) else "unsafe_branch"
        return "blocked", reason, list(result.round_ref.blockers)
    freshness = result.manifest.get("feedback_freshness") or {}
    if isinstance(freshness, dict) and freshness.get("is_stale"):
        return "blocked", "stale_head", ["stale_head"]
    check_reason = classify_check_terminal_reason(result.manifest)
    if check_reason is not None:
        return "blocked", check_reason, [check_reason]
    if not extract_feedback_fingerprints(result.manifest) and not result.round_ref.feedback_fingerprints:
        return "no_feedback", "no_feedback", []
    if result.delta is not None and should_stop_for_repeated_feedback(result.delta, stop_on_repeated_feedback=stop_on_repeated_feedback):
        return "repeated_feedback", "repeated_feedback", ["repeated_feedback"]
    return None


def _prepare_approval(runtime: PostPRRuntime, collected: CollectRoundResult) -> ApprovalRoundResult | None:
    """创建或恢复审批，并在读取 decision 前验证 request 的完整性。"""

    config = runtime.automation_input
    if collected.resumed:
        request = load_approval_request(runtime.post_pr_dir / "approval_request.json")
        errors = validate_approval_request_integrity(request)
        if errors:
            runtime.state = mark_terminal(
                runtime.state,
                status="blocked",
                terminal_reason="stale_approval",
                blockers=list(runtime.state.blockers) + errors,
            )
            return None
        runtime.approval_request_path = runtime.post_pr_dir / "approval_request.md"
    else:
        request = build_approval_request(
            run_id=config.run_id,
            round_id=collected.round_ref.round_id,
            pr_feedback_manifest=collected.manifest,
            auto_pr_manifest_path=config.auto_pr_manifest_path,
            pr_feedback_manifest_path=collected.round_ref.collect_manifest_path,
            requested_actions=_action_list(approve_comment=config.approve_comment),
            reason="Actionable feedback found.",
        )
        runtime.approval_request_path, _, request = write_approval_request(
            request,
            output_md=runtime.post_pr_dir / "approval_request.md",
            output_json=runtime.post_pr_dir / "approval_request.json",
            overwrite=True,
        )

    # 审批请求先持久化为 awaiting_approval，崩溃后 resume 才能安全复用同一轮。
    runtime.state = mark_terminal(
        runtime.state,
        status="awaiting_approval",
        terminal_reason="awaiting_approval",
        blockers=list(runtime.state.blockers),
    )
    write_post_pr_state(runtime.state, runtime.state_path, overwrite=True)
    atomic_write_json(runtime.side_effects, runtime.side_effects_path, overwrite=True)
    if not (config.execute and not config.dry_run):
        return ApprovalRoundResult(request=request, decision=None)

    if config.approval_file is not None:
        decision = load_approval_decision(config.approval_file)
        runtime.approval_decision_path = config.approval_file
    else:
        decision = synthesize_cli_approval_decision(
            request=request,
            approve_run_agent=config.approve_run_agent,
            approve_push_update=config.approve_push_update,
            approve_comment=config.approve_comment,
        )
        if decision is not None:
            runtime.approval_decision_path = write_approval_decision(
                decision,
                runtime.post_pr_dir / "approval_decision.json",
                overwrite=True,
            )
    if decision is None or decision.status == "pending":
        return ApprovalRoundResult(request=request, decision=decision)
    if decision.status == "rejected":
        runtime.state = mark_terminal(
            runtime.state,
            status="blocked",
            terminal_reason="approval_rejected",
            blockers=list(runtime.state.blockers) + ["approval_rejected"],
        )
        return None
    errors = validate_approval_decision(
        decision,
        request=request,
        existing_commit_sha=latest_successful_commit_for_round(runtime.side_effects, round_id=collected.round_ref.round_id),
    )
    if errors:
        runtime.state = mark_terminal(
            runtime.state,
            status="blocked",
            terminal_reason=approval_terminal_reason(errors),
            blockers=list(runtime.state.blockers) + errors,
        )
        return None
    return ApprovalRoundResult(request=request, decision=decision)


def _build_execute_round(
    runtime: PostPRRuntime,
    *,
    collected: CollectRoundResult,
    github_client: PRFeedbackGitHubClientProtocol | None,
    run_agent_approved: bool,
    push_approved: bool,
    comment_approved: bool,
    existing_commit_sha: str | None,
    comment_marker: str,
    run_agent_succeeded: bool,
    push_succeeded: bool,
    comment_succeeded: bool,
) -> tuple[PostPRRoundRef, dict[str, Any] | None, list[ArtifactSnapshotEntry]] | None:
    """把“这轮到底执行什么”收拢成一个选择阶段。"""

    config = runtime.automation_input
    round_ref = collected.round_ref
    if run_agent_approved:
        if not run_agent_succeeded:
            return run_feedback_round(
                run_dir=config.run_dir,
                auto_pr_manifest_path=config.auto_pr_manifest_path,
                round_dir=round_ref.round_dir,
                phase="execute",
                dry_run=False,
                execute=True,
                allow_run_agent=True,
                allow_push_update=push_approved and not push_succeeded,
                allow_comment=comment_approved and not comment_succeeded,
                wait_ci=config.wait_ci,
                poll_interval_seconds=config.poll_interval_seconds,
                timeout_seconds=config.timeout_seconds,
                token_env=config.token_env,
                include_logs=config.include_logs,
                include_success_logs=config.include_success_logs,
                max_log_bytes=config.max_log_bytes,
                max_feedback_items=config.max_feedback_items,
                overwrite=config.overwrite,
                github_client=github_client,
                comment_marker=comment_marker if comment_approved else None,
            )
        return (
            replace(
                round_ref,
                agent_ran=True,
                commit_created=bool(existing_commit_sha or round_ref.commit_created),
                new_commit_sha=existing_commit_sha or round_ref.new_commit_sha,
                push_update_executed=push_succeeded or round_ref.push_update_executed,
                comment_posted=comment_succeeded or round_ref.comment_posted,
                execute_manifest_path=round_ref.collect_manifest_path,
            ),
            collected.manifest,
            [],
        )
    if existing_commit_sha and push_approved and not push_succeeded:
        push_result = push_existing_followup_commit_if_approved(
            run_dir=config.run_dir,
            pr_feedback_manifest=collected.manifest,
            commit_sha=existing_commit_sha,
            execute=True,
            allow_push_update=True,
        )
        return (
            replace(
                round_ref,
                status="branch_updated" if push_result.get("pushed") else "patch_ready",
                commit_created=True,
                new_commit_sha=existing_commit_sha,
                push_update_executed=bool(push_result.get("pushed")),
                execute_manifest_path=round_ref.collect_manifest_path,
            ),
            collected.manifest,
            [],
        )
    if existing_commit_sha and push_approved:
        return (
            replace(
                round_ref,
                status="branch_updated",
                commit_created=True,
                new_commit_sha=existing_commit_sha,
                push_update_executed=True,
                execute_manifest_path=round_ref.collect_manifest_path,
            ),
            collected.manifest,
            [],
        )
    return None


def _classify_execute_blocked_reason(execute_ref: PostPRRoundRef) -> PostPRTerminalReason:
    """按 blocker 内容判断这轮应该落成什么终止原因。"""

    if any("stale" in item for item in execute_ref.blockers):
        return "stale_head"
    if any("push" in item.lower() for item in execute_ref.blockers):
        return "push_failed"
    return "api_degraded" if any("api" in item.lower() for item in execute_ref.blockers) else "unsafe_branch"


def _record_blocked_execute_effects(
    runtime: PostPRRuntime,
    *,
    collected: CollectRoundResult,
    execute_ref: PostPRRoundRef,
    decision_sha256: str | None,
    run_agent_approved: bool,
    push_approved: bool,
    comment_approved: bool,
    comment_marker: str,
) -> None:
    """执行阶段被 blocker 拦住时，仍把未发生的副作用明确记到账本里。"""

    reason = _classify_execute_blocked_reason(execute_ref)
    blockers = list(runtime.state.blockers) + execute_ref.blockers
    runtime.state = upsert_round(runtime.state, execute_ref)
    runtime.state = mark_terminal(runtime.state, status="blocked", terminal_reason=reason, blockers=blockers)
    if run_agent_approved and not execute_ref.agent_ran:
        runtime.side_effects = _append_effect(
            runtime.side_effects,
            round_id=execute_ref.round_id,
            action="run_agent",
            status="failed",
            approval_decision_sha256=decision_sha256,
            head_sha_before=execute_ref.head_sha_before,
            head_sha_after=execute_ref.head_sha_after,
            commit_sha=execute_ref.new_commit_sha,
            error="run_agent not executed",
            metadata={"phase": "execute", "terminal_reason": reason},
        )
    if push_approved and not execute_ref.push_update_executed:
        runtime.side_effects = _append_effect(
            runtime.side_effects,
            round_id=execute_ref.round_id,
            action="push_update",
            status="failed" if execute_ref.commit_created else "skipped",
            approval_decision_sha256=decision_sha256,
            commit_sha=execute_ref.new_commit_sha,
            remote_ref=str((collected.manifest.get("pr") or {}).get("head_branch") or ""),
            error="push_update not executed",
            metadata={"phase": "execute", "terminal_reason": reason},
        )
    if comment_approved and not execute_ref.comment_posted:
        runtime.side_effects = _append_effect(
            runtime.side_effects,
            round_id=execute_ref.round_id,
            action="post_comment",
            status="failed" if execute_ref.commit_created else "skipped",
            approval_decision_sha256=decision_sha256,
            comment_marker=comment_marker,
            error="post_comment not executed",
            metadata={"phase": "execute", "terminal_reason": reason},
        )


def _record_execute_round_effects(
    runtime: PostPRRuntime,
    *,
    collected: CollectRoundResult,
    execute_ref: PostPRRoundRef,
    execute_manifest: dict[str, Any],
    execute_snapshots: list[ArtifactSnapshotEntry],
    decision_sha256: str | None,
    run_agent_approved: bool,
    push_approved: bool,
    comment_approved: bool,
    run_agent_succeeded: bool,
    push_succeeded: bool,
    comment_succeeded: bool,
    comment_marker: str,
) -> None:
    """把执行成功后的 ledger 更新集中到一起，避免 _execute_round 继续膨胀。"""

    round_id = execute_ref.round_id
    if execute_ref.agent_ran and not run_agent_succeeded:
        runtime.side_effects = _append_effect(
            runtime.side_effects,
            round_id=round_id,
            action="run_agent",
            status="succeeded",
            approval_decision_sha256=decision_sha256,
            head_sha_before=execute_ref.head_sha_before,
            head_sha_after=execute_ref.head_sha_after,
            commit_sha=execute_ref.new_commit_sha,
            metadata={"phase": "execute"},
        )
    if execute_ref.commit_created and not has_succeeded_effect(
        runtime.side_effects,
        round_id=round_id,
        action="commit",
        commit_sha=execute_ref.new_commit_sha,
    ):
        runtime.side_effects = _append_effect(
            runtime.side_effects,
            round_id=round_id,
            action="commit",
            status="succeeded",
            approval_decision_sha256=decision_sha256,
            commit_sha=execute_ref.new_commit_sha,
            metadata={"phase": "execute"},
        )
    if execute_ref.push_update_executed and not push_succeeded:
        runtime.side_effects = _append_effect(
            runtime.side_effects,
            round_id=round_id,
            action="push_update",
            status="succeeded",
            approval_decision_sha256=decision_sha256,
            commit_sha=execute_ref.new_commit_sha,
            remote_ref=str((collected.manifest.get("pr") or {}).get("head_branch") or ""),
            metadata={"phase": "execute"},
        )
    if execute_ref.comment_posted and not comment_succeeded:
        runtime.side_effects = _append_effect(
            runtime.side_effects,
            round_id=round_id,
            action="post_comment",
            status="succeeded",
            approval_decision_sha256=decision_sha256,
            comment_marker=comment_marker,
            metadata={"phase": "execute"},
        )
    if run_agent_approved and not execute_ref.agent_ran:
        runtime.side_effects = _append_effect(
            runtime.side_effects,
            round_id=round_id,
            action="run_agent",
            status="failed",
            approval_decision_sha256=decision_sha256,
            head_sha_before=execute_ref.head_sha_before,
            head_sha_after=execute_ref.head_sha_after,
            commit_sha=execute_ref.new_commit_sha,
            error="run_agent not executed",
            metadata={"phase": "execute"},
        )
    if push_approved and not execute_ref.push_update_executed and execute_ref.commit_created:
        runtime.side_effects = _append_effect(
            runtime.side_effects,
            round_id=round_id,
            action="push_update",
            status="failed",
            approval_decision_sha256=decision_sha256,
            commit_sha=execute_ref.new_commit_sha,
            remote_ref=str((collected.manifest.get("pr") or {}).get("head_branch") or ""),
            error="push_update not executed",
            metadata={"phase": "execute"},
        )
    if comment_approved and not execute_ref.comment_posted:
        runtime.side_effects = _append_effect(
            runtime.side_effects,
            round_id=round_id,
            action="post_comment",
            status="failed",
            approval_decision_sha256=decision_sha256,
            comment_marker=comment_marker,
            error="post_comment not executed",
            metadata={"phase": "execute"},
        )
    execute_delta = build_feedback_delta(
        previous_fingerprints=collected.round_ref.feedback_fingerprints,
        current_fingerprints=extract_feedback_fingerprints(execute_manifest),
    )
    _write_round_delta(execute_ref.round_dir, "execute", execute_delta)
    write_round_manifest(
        round_ref=execute_ref,
        collect_manifest=collected.manifest,
        execute_manifest=execute_manifest,
        feedback_delta=execute_delta.__dict__,
        snapshots=execute_snapshots,
        output_path=_round_manifest_path(execute_ref.round_dir, "execute"),
        overwrite=True,
    )


def _execute_round(
    runtime: PostPRRuntime,
    *,
    collected: CollectRoundResult,
    approval: ApprovalRoundResult,
    github_client: PRFeedbackGitHubClientProtocol | None,
) -> ExecutionRoundResult | None:
    """执行已审批动作，并用 ledger 查询保证 resume 时不重复产生副作用。"""

    config = runtime.automation_input
    decision = approval.decision
    if decision is None:
        return None
    round_ref = collected.round_ref
    round_id = round_ref.round_id
    run_agent_approved = is_action_approved(decision, "run_agent")
    push_approved = is_action_approved(decision, "push_update")
    comment_approved = is_action_approved(decision, "post_comment")
    existing_commit_sha = latest_successful_commit_for_round(runtime.side_effects, round_id=round_id)
    decision_sha256 = sha256_file(runtime.approval_decision_path) if runtime.approval_decision_path else None
    comment_marker = f"{config.run_id}:{round_id}:{approval.request.approval_request_sha256 or 'pending'}"
    run_agent_succeeded = has_succeeded_effect(
        runtime.side_effects,
        round_id=round_id,
        action="run_agent",
        approval_decision_sha256=decision_sha256,
    )
    push_succeeded = has_succeeded_effect(
        runtime.side_effects,
        round_id=round_id,
        action="push_update",
        approval_decision_sha256=decision_sha256,
        commit_sha=existing_commit_sha,
    )
    comment_succeeded = has_succeeded_effect(
        runtime.side_effects,
        round_id=round_id,
        action="post_comment",
        approval_decision_sha256=decision_sha256,
        comment_marker=comment_marker,
    )
    built = _build_execute_round(
        runtime,
        collected=collected,
        github_client=github_client,
        run_agent_approved=run_agent_approved,
        push_approved=push_approved,
        comment_approved=comment_approved,
        existing_commit_sha=existing_commit_sha,
        comment_marker=comment_marker,
        run_agent_succeeded=run_agent_succeeded,
        push_succeeded=push_succeeded,
        comment_succeeded=comment_succeeded,
    )
    if built is None:
        return None
    execute_ref, execute_manifest, execute_snapshots = built

    if execute_ref.status == "blocked":
        _record_blocked_execute_effects(
            runtime,
            collected=collected,
            execute_ref=execute_ref,
            decision_sha256=decision_sha256,
            run_agent_approved=run_agent_approved,
            push_approved=push_approved,
            comment_approved=comment_approved,
            comment_marker=comment_marker,
        )
        return ExecutionRoundResult(execute_ref, execute_manifest, execute_snapshots)

    _record_execute_round_effects(
        runtime,
        collected=collected,
        execute_ref=execute_ref,
        execute_manifest=execute_manifest or collected.manifest,
        execute_snapshots=execute_snapshots,
        decision_sha256=decision_sha256,
        run_agent_approved=run_agent_approved,
        push_approved=push_approved,
        comment_approved=comment_approved,
        run_agent_succeeded=run_agent_succeeded,
        push_succeeded=push_succeeded,
        comment_succeeded=comment_succeeded,
        comment_marker=comment_marker,
    )
    runtime.state = upsert_round(runtime.state, execute_ref)
    return ExecutionRoundResult(execute_ref, execute_manifest, execute_snapshots)


def _finalize_post_pr_artifacts(
    runtime: PostPRRuntime,
    *,
    post_pr_action_template: bool,
    write_workflow: bool = True,
) -> PostPRAutomationResult:
    """统一落盘 state、ledger、report 和 manifest，避免各退出分支产生差异。"""

    write_post_pr_state(runtime.state, runtime.state_path, overwrite=True)
    atomic_write_json(runtime.side_effects, runtime.side_effects_path, overwrite=True)
    if write_workflow and post_pr_action_template:
        runtime.workflow_path = write_post_pr_automation_workflow_template(
            runtime.post_pr_dir / "post_pr_automation_workflow.yml",
            overwrite=True,
        )
    result = _result_from_state(
        runtime=runtime,
        manifest_path=runtime.post_pr_dir / "post_pr_automation_manifest.json",
        report_path=runtime.post_pr_dir / "post_pr_automation_report.md",
    )
    report_path = write_post_pr_automation_report(
        result,
        result.report_path,
        state=runtime.state,
        side_effects=runtime.side_effects,
        overwrite=True,
    )
    result = replace(result, report_path=report_path)
    return replace(
        result,
        manifest_path=_write_post_pr_automation_manifest(
            result=result,
            auto_pr_manifest_path=runtime.automation_input.auto_pr_manifest_path,
            state=runtime.state,
            side_effects=runtime.side_effects,
        ),
    )


def run_post_pr_automation(
    *,
    run_dir: str | Path,
    auto_pr_manifest_path: str | Path | None = None,
    dry_run: bool = True,
    execute: bool = False,
    max_rounds: int = 2,
    wait_ci: bool = False,
    poll_interval_seconds: int = 30,
    timeout_seconds: int = 900,
    token_env: str = "GITHUB_TOKEN",
    include_logs: bool = True,
    include_success_logs: bool = False,
    max_log_bytes: int = 200_000,
    max_feedback_items: int = 20,
    stop_on_repeated_feedback: bool = True,
    approve_run_agent: bool = False,
    approve_push_update: bool = False,
    approve_comment: bool = False,
    approval_file: str | Path | None = None,
    resume: bool = False,
    overwrite: bool = False,
    post_pr_action_template: bool = True,
    github_client: PRFeedbackGitHubClientProtocol | None = None,
) -> PostPRAutomationResult:
    validate_max_rounds(max_rounds)
    run_dir_path = Path(run_dir).expanduser().resolve()
    if not run_dir_path.exists() or not run_dir_path.is_dir():
        raise FileNotFoundError(run_dir_path)
    manifest_path = Path(auto_pr_manifest_path).expanduser().resolve() if auto_pr_manifest_path else run_dir_path / "auto_pr_manifest.json"
    post_pr_dir = resolve_post_pr_dir(run_dir_path)
    post_pr_dir.mkdir(parents=True, exist_ok=True)
    state_path = resolve_state_path(run_dir_path)
    side_effects_path = resolve_side_effects_path(run_dir_path)
    if not resume and state_path.exists() and not overwrite:
        raise FileExistsError(state_path)

    # 先用 run_dir 名称建立最小安全运行时；manifest 解析成功后再替换为其正式 run_id。
    # 这样 manifest 内容损坏时仍可在状态锁保护下生成 blocked 状态、报告和 manifest。
    config = PostPRAutomationInput(
        run_id=run_dir_path.name,
        run_dir=run_dir_path,
        auto_pr_manifest_path=manifest_path,
        dry_run=dry_run,
        execute=execute,
        max_rounds=max_rounds,
        wait_ci=wait_ci,
        poll_interval_seconds=poll_interval_seconds,
        timeout_seconds=timeout_seconds,
        token_env=token_env,
        include_logs=include_logs,
        include_success_logs=include_success_logs,
        max_log_bytes=max_log_bytes,
        max_feedback_items=max_feedback_items,
        stop_on_repeated_feedback=stop_on_repeated_feedback,
        approve_run_agent=approve_run_agent,
        approve_push_update=approve_push_update,
        approve_comment=approve_comment,
        approval_file=None,
        resume=resume,
        overwrite=overwrite,
    )
    # 异常处理可能在加载持久化状态前触发，因此所有恢复所需对象都在 try 前初始化。
    runtime = PostPRRuntime(
        automation_input=config,
        post_pr_dir=post_pr_dir,
        state_path=state_path,
        side_effects_path=side_effects_path,
        state=initial_post_pr_state(run_id=config.run_id, run_dir=run_dir_path, max_rounds=max_rounds),
        side_effects=initial_side_effects(config.run_id),
        approval_request_path=None,
        approval_decision_path=None,
        workflow_path=None,
    )
    lock: StateLock | None = None
    try:
        auto_pr_manifest = _load_json_object(manifest_path)
    except FileNotFoundError:
        raise
    except ValueError as exc:
        try:
            lock = acquire_state_lock(post_pr_dir)
        except RuntimeError as lock_exc:
            runtime.state = mark_terminal(runtime.state, status="blocked", terminal_reason="state_locked", blockers=[str(lock_exc)])
        else:
            runtime.state = mark_terminal(runtime.state, status="blocked", terminal_reason="manifest_invalid", blockers=[str(exc)])
        try:
            return _finalize_post_pr_artifacts(runtime, post_pr_action_template=post_pr_action_template, write_workflow=False)
        finally:
            if lock is not None:
                release_state_lock(lock)

    run_id = str(auto_pr_manifest.get("run_id") or run_dir_path.name)
    approval_file_path = Path(approval_file).expanduser().resolve() if approval_file is not None else None
    if approval_file_path is not None and post_pr_dir.resolve() not in approval_file_path.parents and approval_file_path != post_pr_dir.resolve():
        raise ValueError("approval_file must be inside run_dir/post_pr")
    if approval_file_path is not None:
        if approval_file_path.exists() and not approval_file_path.is_file():
            raise ValueError("approval_file must be a JSON file")
        if approval_file_path.suffix.lower() != ".json":
            raise ValueError("approval_file must be a JSON file")
    config = replace(config, run_id=run_id, approval_file=approval_file_path)
    runtime = replace(
        runtime,
        automation_input=config,
        state=initial_post_pr_state(run_id=run_id, run_dir=run_dir_path, max_rounds=max_rounds),
        side_effects=initial_side_effects(run_id),
    )
    try:
        lock = acquire_state_lock(post_pr_dir)
    except RuntimeError as exc:
        runtime.state = mark_terminal(runtime.state, status="blocked", terminal_reason="state_locked", blockers=[str(exc)])
        return _finalize_post_pr_artifacts(runtime, post_pr_action_template=post_pr_action_template, write_workflow=False)

    if overwrite and not resume:
        clear_post_pr_dir(post_pr_dir)
    try:
        runtime = _load_or_initialize_runtime(runtime)
        if _is_hard_terminal_state(runtime.state):
            _restore_existing_artifact_paths(runtime)
            return _finalize_post_pr_artifacts(runtime, post_pr_action_template=post_pr_action_template, write_workflow=False)

        pending_round = _find_pending_approval_round(runtime.state) if resume else None
        if pending_round is not None and not (execute and not dry_run):
            _restore_existing_artifact_paths(runtime)
            return _finalize_post_pr_artifacts(runtime, post_pr_action_template=post_pr_action_template)

        next_round_index = pending_round.round_index if pending_round is not None else len(runtime.state.rounds) + 1
        for round_index in range(next_round_index, max_rounds + 1):
            collected = _collect_round(
                runtime,
                round_index=round_index,
                pending_round=pending_round,
                github_client=github_client,
            )
            collect_terminal = _collect_terminal_reason(
                collected,
                stop_on_repeated_feedback=stop_on_repeated_feedback,
            )
            if collect_terminal is not None:
                status, reason, new_blockers = collect_terminal
                runtime.state = mark_terminal(
                    runtime.state,
                    status=status,
                    terminal_reason=reason,
                    blockers=list(runtime.state.blockers) + new_blockers,
                )
                break

            approval = _prepare_approval(runtime, collected)
            if approval is None or approval.decision is None or approval.decision.status != "approved":
                break
            executed = _execute_round(runtime, collected=collected, approval=approval, github_client=github_client)
            if executed is None:
                runtime.state = mark_terminal(
                    runtime.state,
                    status="awaiting_approval",
                    terminal_reason="awaiting_approval",
                    blockers=list(runtime.state.blockers),
                )
                break
            if runtime.state.status == "blocked":
                break
            if executed.round_ref.push_update_executed:
                if round_index >= max_rounds:
                    runtime.state = mark_terminal(
                        runtime.state,
                        status="max_rounds_reached",
                        terminal_reason="max_rounds_reached",
                        blockers=list(runtime.state.blockers),
                    )
                    break
                pending_round = None
                continue
            if executed.round_ref.commit_created:
                runtime.state = mark_terminal(
                    runtime.state,
                    status="patch_ready",
                    terminal_reason="awaiting_approval",
                    blockers=list(runtime.state.blockers),
                )
                break
            if executed.round_ref.agent_ran:
                runtime.state = mark_terminal(
                    runtime.state,
                    status="agent_ran",
                    terminal_reason="awaiting_approval",
                    blockers=list(runtime.state.blockers),
                )
                break
            runtime.state = mark_terminal(
                runtime.state,
                status="awaiting_approval",
                terminal_reason="awaiting_approval",
                blockers=list(runtime.state.blockers),
            )
            break
        if runtime.state.terminal_reason == "none":
            runtime.state = mark_terminal(
                runtime.state,
                status="awaiting_approval",
                terminal_reason="awaiting_approval",
                blockers=list(runtime.state.blockers),
            )
        return _finalize_post_pr_artifacts(runtime, post_pr_action_template=post_pr_action_template)
    except FileNotFoundError:
        raise
    except ValueError as exc:
        runtime.state = mark_terminal(runtime.state, status="blocked", terminal_reason="manifest_invalid", blockers=[str(exc)])
        return _finalize_post_pr_artifacts(runtime, post_pr_action_template=post_pr_action_template, write_workflow=False)
    finally:
        if lock is not None:
            release_state_lock(lock)
