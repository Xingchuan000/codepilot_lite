from __future__ import annotations

"""第十五步 Post-PR automation 的控制器。"""

import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from codepilot.post_pr.approval import (
    ApprovalDecision,
    ApprovalRequest,
    approval_terminal_reason,
    build_approval_request,
    is_action_approved,
    load_approval_request,
    load_approval_decision,
    synthesize_cli_approval_decision,
    validate_approval_request_integrity,
    validate_approval_decision,
    write_approval_decision,
    write_approval_request,
)
from codepilot.post_pr.feedback_delta import build_feedback_delta, classify_check_terminal_reason, extract_feedback_fingerprints, load_ci_feedback_manifest, should_stop_for_repeated_feedback
from codepilot.post_pr.github_action import write_post_pr_automation_workflow_template
from codepilot.post_pr.models import (
    ApprovalAction,
    PostPRAutomationInput,
    PostPRAutomationResult,
    PostPRRoundRef,
    PostPRTerminalReason,
    SideEffectAction,
    SideEffectEntry,
    SideEffectStatus,
    validate_max_rounds,
)
from codepilot.post_pr.report import write_post_pr_automation_report
from codepilot.post_pr.round_runner import push_existing_followup_commit_if_approved, run_feedback_round, write_round_manifest
from codepilot.post_pr.state_store import (
    acquire_state_lock,
    append_side_effect,
    clear_post_pr_dir,
    initial_post_pr_state,
    initial_side_effects,
    has_succeeded_effect,
    latest_successful_commit_for_round,
    load_post_pr_state,
    load_side_effects,
    mark_terminal,
    release_state_lock,
    resolve_post_pr_dir,
    resolve_side_effects_path,
    resolve_state_path,
    write_post_pr_state,
    atomic_write_json,
)
from codepilot.pr_feedback.github_client import PRFeedbackGitHubClientProtocol
from codepilot.repo.git_utils import sha256_file


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _load_json_object(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"JSON object required: {path}")
    return data


def _round_index(round_id: str) -> int:
    return int(round_id.split("-", maxsplit=1)[1])


def _is_hard_terminal_state(state: dict[str, Any]) -> bool:
    status = state.get("status")
    terminal_reason = state.get("terminal_reason")
    return status in {"blocked", "failed", "no_feedback", "max_rounds_reached", "repeated_feedback"} or terminal_reason in {
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


def _find_pending_approval_round(state: dict[str, Any]) -> PostPRRoundRef | None:
    if state.get("status") not in {"awaiting_approval", "patch_ready", "agent_ran"}:
        return None
    if state.get("terminal_reason") != "awaiting_approval":
        return None
    rounds = [item for item in state.get("rounds") or [] if isinstance(item, dict)]
    if not rounds:
        return None
    latest = _round_ref_from_dict(rounds[-1])
    if latest.collect_manifest_path is None and latest.latest_pr_feedback_manifest_path is None:
        return None
    return latest


def _round_ref_to_dict(round_ref: PostPRRoundRef) -> dict[str, Any]:
    return {
        "round_id": round_ref.round_id,
        "round_index": round_ref.round_index,
        "round_dir": str(round_ref.round_dir),
        "collect_manifest_path": None if round_ref.collect_manifest_path is None else str(round_ref.collect_manifest_path),
        "execute_manifest_path": None if round_ref.execute_manifest_path is None else str(round_ref.execute_manifest_path),
        "latest_pr_feedback_manifest_path": None
        if round_ref.latest_pr_feedback_manifest_path is None
        else str(round_ref.latest_pr_feedback_manifest_path),
        "feedback_fingerprints": list(round_ref.feedback_fingerprints),
        "status": round_ref.status,
        "terminal_reason": round_ref.terminal_reason,
        "head_sha_before": round_ref.head_sha_before,
        "head_sha_after": round_ref.head_sha_after,
        "agent_ran": round_ref.agent_ran,
        "patch_generated": round_ref.patch_generated,
        "commit_created": round_ref.commit_created,
        "new_commit_sha": round_ref.new_commit_sha,
        "push_update_executed": round_ref.push_update_executed,
        "comment_posted": round_ref.comment_posted,
        "blockers": list(round_ref.blockers),
        "warnings": list(round_ref.warnings),
    }


def _round_ref_from_dict(item: dict[str, Any]) -> PostPRRoundRef:
    return PostPRRoundRef(
        round_id=str(item.get("round_id") or ""),
        round_index=int(item.get("round_index") or 0),
        round_dir=Path(str(item.get("round_dir") or ".")),
        collect_manifest_path=Path(item["collect_manifest_path"]) if item.get("collect_manifest_path") else None,
        execute_manifest_path=Path(item["execute_manifest_path"]) if item.get("execute_manifest_path") else None,
        latest_pr_feedback_manifest_path=Path(item["latest_pr_feedback_manifest_path"])
        if item.get("latest_pr_feedback_manifest_path")
        else None,
        feedback_fingerprints=[str(value) for value in item.get("feedback_fingerprints") or []],
        status=str(item.get("status") or "planned"),
        terminal_reason=str(item.get("terminal_reason") or "none"),
        head_sha_before=item.get("head_sha_before"),
        head_sha_after=item.get("head_sha_after"),
        agent_ran=bool(item.get("agent_ran")),
        patch_generated=bool(item.get("patch_generated")),
        commit_created=bool(item.get("commit_created")),
        new_commit_sha=item.get("new_commit_sha"),
        push_update_executed=bool(item.get("push_update_executed")),
        comment_posted=bool(item.get("comment_posted")),
        blockers=[str(value) for value in item.get("blockers") or []],
        warnings=[str(value) for value in item.get("warnings") or []],
    )


def _write_round_delta(round_dir: Path, phase: str, delta: dict[str, Any]) -> Path:
    path = round_dir / phase / "feedback_delta.json"
    return atomic_write_json(delta, path, overwrite=True)


def _round_manifest_path(round_dir: Path, phase: str) -> Path:
    return round_dir / phase / "round_manifest.json"


def _refresh_round_state(state: dict[str, Any], round_ref: PostPRRoundRef) -> dict[str, Any]:
    rounds = [item for item in state.get("rounds") or [] if isinstance(item, dict)]
    rounds = [item for item in rounds if item.get("round_id") != round_ref.round_id]
    rounds.append(_round_ref_to_dict(round_ref))
    updated = dict(state)
    updated["rounds"] = rounds
    updated["latest_round_id"] = round_ref.round_id
    updated["updated_at"] = _now_iso()
    return updated


def _result_from_state(
    *,
    run_id: str,
    run_dir: Path,
    post_pr_dir: Path,
    state: dict[str, Any],
    approval_request_path: Path | None,
    approval_decision_path: Path | None,
    side_effects_path: Path,
    manifest_path: Path | None,
    report_path: Path | None,
    workflow_path: Path | None,
) -> PostPRAutomationResult:
    return PostPRAutomationResult(
        run_id=run_id,
        run_dir=run_dir,
        post_pr_dir=post_pr_dir,
        status=state.get("status") or "planned",
        terminal_reason=state.get("terminal_reason") or "none",
        rounds=[_round_ref_from_dict(item) for item in state.get("rounds") or [] if isinstance(item, dict)],
        latest_round_id=state.get("latest_round_id"),
        approval_request_path=approval_request_path,
        approval_decision_path=approval_decision_path,
        side_effects_path=side_effects_path,
        state_path=resolve_state_path(run_dir),
        manifest_path=manifest_path,
        report_path=report_path,
        workflow_path=workflow_path,
        warnings=[str(item) for item in state.get("warnings") or []],
        blockers=[str(item) for item in state.get("blockers") or []],
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
    state: dict[str, Any],
    side_effects: dict[str, Any],
) -> Path:
    def _display(path: Path | None) -> str | None:
        if path is None:
            return None
        try:
            return str(path.relative_to(result.run_dir))
        except ValueError:
            return path.name

    latest_manifest_sha256 = None
    if result.rounds:
        latest_manifest = result.rounds[-1].latest_pr_feedback_manifest_path
        latest_manifest_sha256 = sha256_file(latest_manifest) if latest_manifest else None
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
        "rounds": [_round_ref_to_dict(item) for item in result.rounds],
        "manifest_hash_chain": {
            "auto_pr_manifest_sha256": sha256_file(auto_pr_manifest_path),
            "latest_ci_feedback_manifest_sha256": latest_manifest_sha256,
            "approval_request_sha256": sha256_file(result.approval_request_path) if result.approval_request_path else None,
            "approval_decision_sha256": sha256_file(result.approval_decision_path) if result.approval_decision_path else None,
        },
        "side_effects_summary": {
            "effects_total": len(side_effects.get("effects") or []),
            "last_action": (side_effects.get("effects") or [{}])[-1].get("action") if side_effects.get("effects") else None,
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
    ledger: dict[str, Any],
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
) -> dict[str, Any]:
    effect = SideEffectEntry(
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
    )
    return append_side_effect(ledger, effect)


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
    # execute=True 只在非 dry-run 模式下才真正进入执行阶段，
    # 这样 Python API 即使同时传入 dry_run=True 也不会误跑 agent。
    allow_execute = execute and not dry_run
    run_dir_path = Path(run_dir).expanduser().resolve()
    if not run_dir_path.exists() or not run_dir_path.is_dir():
        raise FileNotFoundError(run_dir_path)
    manifest_path = Path(auto_pr_manifest_path).expanduser().resolve() if auto_pr_manifest_path else run_dir_path / "auto_pr_manifest.json"
    auto_pr_manifest = _load_json_object(manifest_path)
    run_id = str(auto_pr_manifest.get("run_id") or run_dir_path.name)
    post_pr_dir = resolve_post_pr_dir(run_dir_path)
    approval_file_path = Path(approval_file).expanduser().resolve() if approval_file is not None else None
    if approval_file_path is not None and post_pr_dir.resolve() not in approval_file_path.parents and approval_file_path != post_pr_dir.resolve():
        raise ValueError("approval_file must be inside run_dir/post_pr")
    if approval_file_path is not None:
        if approval_file_path.exists() and not approval_file_path.is_file():
            raise ValueError("approval_file must be a JSON file")
        if approval_file_path.suffix.lower() != ".json":
            raise ValueError("approval_file must be a JSON file")
    post_pr_dir.mkdir(parents=True, exist_ok=True)
    state_path = resolve_state_path(run_dir_path)
    side_effects_path = resolve_side_effects_path(run_dir_path)
    if not resume and state_path.exists() and not overwrite:
        raise FileExistsError(state_path)

    state = initial_post_pr_state(run_id=run_id, run_dir=run_dir_path, max_rounds=max_rounds)
    side_effects = initial_side_effects(run_id)
    lock: Any | None = None
    try:
        lock = acquire_state_lock(post_pr_dir)
    except RuntimeError as exc:
        # 锁已经被别的进程占用时，直接把这次运行标成 blocked，
        # 同时把 report / manifest 也写出来，保证产物链路完整可追踪。
        state = initial_post_pr_state(run_id=run_id, run_dir=run_dir_path, max_rounds=max_rounds)
        state = mark_terminal(state, status="blocked", terminal_reason="state_locked", blockers=[str(exc)])
        write_post_pr_state(state, state_path, overwrite=True)
        side_effects = initial_side_effects(run_id)
        atomic_write_json(side_effects, side_effects_path, overwrite=True)
        result = _result_from_state(
            run_id=run_id,
            run_dir=run_dir_path,
            post_pr_dir=post_pr_dir,
            state=state,
            approval_request_path=None,
            approval_decision_path=None,
            side_effects_path=side_effects_path,
            manifest_path=post_pr_dir / "post_pr_automation_manifest.json",
            report_path=post_pr_dir / "post_pr_automation_report.md",
            workflow_path=None,
        )
        report_path = write_post_pr_automation_report(result, result.report_path, state=state, side_effects=side_effects, overwrite=True)
        result = replace(
            result,
            report_path=report_path,
            manifest_path=_write_post_pr_automation_manifest(result=result, auto_pr_manifest_path=manifest_path, state=state, side_effects=side_effects),
        )
        return result
    if overwrite and not resume:
        clear_post_pr_dir(post_pr_dir)
    try:
        state = load_post_pr_state(state_path) if resume else None
        state = state or initial_post_pr_state(run_id=run_id, run_dir=run_dir_path, max_rounds=max_rounds)
        side_effects = load_side_effects(side_effects_path, run_id=run_id) if resume else initial_side_effects(run_id)
        if _is_hard_terminal_state(state):
            result = _result_from_state(
                run_id=run_id,
                run_dir=run_dir_path,
                post_pr_dir=post_pr_dir,
                state=state,
                approval_request_path=post_pr_dir / "approval_request.md" if (post_pr_dir / "approval_request.md").exists() else None,
                approval_decision_path=post_pr_dir / "approval_decision.json" if (post_pr_dir / "approval_decision.json").exists() else None,
                side_effects_path=side_effects_path,
                manifest_path=post_pr_dir / "post_pr_automation_manifest.json",
                report_path=post_pr_dir / "post_pr_automation_report.md",
                workflow_path=post_pr_dir / "post_pr_automation_workflow.yml" if (post_pr_dir / "post_pr_automation_workflow.yml").exists() else None,
            )
            report_path = write_post_pr_automation_report(result, result.report_path, state=state, side_effects=side_effects, overwrite=True)
            result = replace(result, report_path=report_path, manifest_path=_write_post_pr_automation_manifest(result=result, auto_pr_manifest_path=manifest_path, state=state, side_effects=side_effects))
            write_post_pr_state(state, state_path, overwrite=True)
            atomic_write_json(side_effects, side_effects_path, overwrite=True)
            return result

        approval_request_path: Path | None = None
        approval_decision_path: Path | None = None
        workflow_path: Path | None = None
        current_rounds = [item for item in state.get("rounds") or [] if isinstance(item, dict)]
        result_rounds: list[PostPRRoundRef] = [_round_ref_from_dict(item) for item in current_rounds]
        previous_fingerprints = [str(item) for item in result_rounds[-1].feedback_fingerprints] if result_rounds else []
        terminal_reason: PostPRTerminalReason = "none"
        status: str = "planned"
        blockers: list[str] = list(state.get("blockers") or [])
        warnings: list[str] = list(state.get("warnings") or [])
        approval_request: ApprovalRequest | None = None
        decision: ApprovalDecision | None = None
        pending_round = _find_pending_approval_round(state) if resume else None
        if pending_round is not None and not allow_execute:
            approval_request_path = post_pr_dir / "approval_request.md" if (post_pr_dir / "approval_request.md").exists() else None
            approval_decision_path = post_pr_dir / "approval_decision.json" if (post_pr_dir / "approval_decision.json").exists() else None
            workflow_path = post_pr_dir / "post_pr_automation_workflow.yml" if (post_pr_dir / "post_pr_automation_workflow.yml").exists() else None
            result = _result_from_state(
                run_id=run_id,
                run_dir=run_dir_path,
                post_pr_dir=post_pr_dir,
                state=state,
                approval_request_path=approval_request_path,
                approval_decision_path=approval_decision_path,
                side_effects_path=side_effects_path,
                manifest_path=post_pr_dir / "post_pr_automation_manifest.json",
                report_path=post_pr_dir / "post_pr_automation_report.md",
                workflow_path=workflow_path,
            )
            report_path = write_post_pr_automation_report(result, result.report_path, state=state, side_effects=side_effects, overwrite=True)
            result = replace(result, report_path=report_path, manifest_path=_write_post_pr_automation_manifest(result=result, auto_pr_manifest_path=manifest_path, state=state, side_effects=side_effects))
            write_post_pr_state(state, state_path, overwrite=True)
            atomic_write_json(side_effects, side_effects_path, overwrite=True)
            if post_pr_action_template:
                workflow_path = write_post_pr_automation_workflow_template(post_pr_dir / "post_pr_automation_workflow.yml", overwrite=True)
                result = replace(result, workflow_path=workflow_path)
            return result

        next_round_index = pending_round.round_index if pending_round is not None else len(result_rounds) + 1
        for round_index in range(next_round_index, max_rounds + 1):
            round_id = f"round-{round_index:03d}"
            round_dir = post_pr_dir / round_id
            round_dir.mkdir(parents=True, exist_ok=True)
            resume_pending_round = pending_round is not None and round_index == pending_round.round_index
            if resume_pending_round:
                collect_round = pending_round
                collect_manifest_path = collect_round.collect_manifest_path or collect_round.latest_pr_feedback_manifest_path
                if collect_manifest_path is None:
                    raise ValueError("missing collect manifest path")
                collect_manifest = load_ci_feedback_manifest(collect_manifest_path)
                collect_snapshots: list[Any] = []
                approval_request_path = post_pr_dir / "approval_request.md"
                approval_request = load_approval_request(post_pr_dir / "approval_request.json")
                # resume 场景下必须先校验 approval_request 自身有没有被改动，
                # 校验失败时不再读取 approval decision，也不再继续执行任何 side effect。
                approval_integrity_errors = validate_approval_request_integrity(approval_request)
                if approval_integrity_errors:
                    terminal_reason = "stale_approval"
                    status = "blocked"
                    blockers.extend(approval_integrity_errors)
                    state = mark_terminal(state, status=status, terminal_reason=terminal_reason, blockers=blockers)
                    result_rounds.append(collect_round)
                    break
            else:
                collect_ref, collect_manifest, collect_snapshots = run_feedback_round(
                    run_dir=run_dir_path,
                    auto_pr_manifest_path=manifest_path,
                    round_dir=round_dir,
                    phase="collect",
                    dry_run=True,
                    execute=False,
                    allow_run_agent=False,
                    allow_push_update=False,
                    allow_comment=False,
                    wait_ci=wait_ci,
                    poll_interval_seconds=poll_interval_seconds,
                    timeout_seconds=timeout_seconds,
                    token_env=token_env,
                    include_logs=include_logs,
                    include_success_logs=include_success_logs,
                    max_log_bytes=max_log_bytes,
                    max_feedback_items=max_feedback_items,
                    overwrite=overwrite,
                    github_client=github_client,
                )
                collect_delta = build_feedback_delta(previous_fingerprints=previous_fingerprints, current_fingerprints=collect_ref.feedback_fingerprints)
                _write_round_delta(round_dir, "collect", collect_delta.__dict__)
                collect_manifest_path = collect_ref.latest_pr_feedback_manifest_path or (run_dir_path / "ci_feedback_manifest.json")
                collect_round = replace(
                    collect_ref,
                    collect_manifest_path=collect_manifest_path,
                    latest_pr_feedback_manifest_path=collect_manifest_path,
                    status=collect_ref.status,
                )
                round_manifest_path = _round_manifest_path(round_dir, "collect")
                write_round_manifest(
                    round_ref=collect_round,
                    collect_manifest=collect_manifest,
                    execute_manifest=None,
                    feedback_delta=collect_delta.__dict__,
                    snapshots=collect_snapshots,
                    output_path=round_manifest_path,
                    overwrite=True,
                )
                state = _refresh_round_state(state, collect_round)
                current_rounds = [item for item in state.get("rounds") or [] if isinstance(item, dict)]
            if not resume_pending_round and collect_ref.status == "blocked":
                terminal_reason = "manifest_invalid" if any("manifest" in blocker for blocker in collect_ref.blockers) else "unsafe_branch"
                status = "blocked"
                blockers.extend(collect_ref.blockers)
                state = mark_terminal(state, status=status, terminal_reason=terminal_reason, blockers=blockers)
                result_rounds.append(collect_round)
                break
            if not resume_pending_round:
                freshness = collect_manifest.get("feedback_freshness") or {}
                if isinstance(freshness, dict) and freshness.get("is_stale"):
                    terminal_reason = "stale_head"
                    status = "blocked"
                    blockers.append("stale_head")
                    state = mark_terminal(state, status=status, terminal_reason=terminal_reason, blockers=blockers)
                    result_rounds.append(collect_round)
                    break
                check_reason = classify_check_terminal_reason(collect_manifest)
                if check_reason is not None:
                    terminal_reason = check_reason  # type: ignore[assignment]
                    status = "blocked"
                    blockers.append(check_reason)
                    state = mark_terminal(state, status=status, terminal_reason=terminal_reason, blockers=blockers)
                    result_rounds.append(collect_round)
                    break
                current_fingerprints = extract_feedback_fingerprints(collect_manifest)
                if not current_fingerprints and not collect_ref.feedback_fingerprints:
                    terminal_reason = "no_feedback"
                    status = "no_feedback"
                    state = mark_terminal(state, status=status, terminal_reason=terminal_reason, blockers=blockers)
                    result_rounds.append(collect_round)
                    break
                if should_stop_for_repeated_feedback(collect_delta, stop_on_repeated_feedback=stop_on_repeated_feedback):
                    terminal_reason = "repeated_feedback"
                    status = "repeated_feedback"
                    blockers.append("repeated_feedback")
                    state = mark_terminal(state, status=status, terminal_reason=terminal_reason, blockers=blockers)
                    result_rounds.append(collect_round)
                    break
            if resume_pending_round:
                state = _refresh_round_state(state, collect_round)
            if not resume_pending_round:
                requested_actions = _action_list(approve_comment=approve_comment)
                if approval_request is None:
                    approval_request = build_approval_request(
                        run_id=run_id,
                        round_id=round_id,
                        pr_feedback_manifest=collect_manifest,
                        auto_pr_manifest_path=manifest_path,
                        pr_feedback_manifest_path=collect_manifest_path,
                        requested_actions=requested_actions,
                        reason="Actionable feedback found.",
                    )
                    approval_request_path, _, approval_request = write_approval_request(
                        approval_request,
                        output_md=post_pr_dir / "approval_request.md",
                        output_json=post_pr_dir / "approval_request.json",
                        overwrite=True,
                    )
                result_rounds.append(collect_round)
                state = _refresh_round_state(state, collect_round)
                state = mark_terminal(state, status="awaiting_approval", terminal_reason="awaiting_approval", blockers=blockers)
                write_post_pr_state(state, state_path, overwrite=True)
                atomic_write_json(side_effects, side_effects_path, overwrite=True)
                if not allow_execute:
                    break
            else:
                result_rounds.append(collect_round)
                state = mark_terminal(state, status="awaiting_approval", terminal_reason="awaiting_approval", blockers=blockers)
                write_post_pr_state(state, state_path, overwrite=True)
                atomic_write_json(side_effects, side_effects_path, overwrite=True)
                if not allow_execute:
                    break
            if approval_file_path is not None:
                decision = load_approval_decision(approval_file_path)
                approval_decision_path = approval_file_path
            else:
                decision = synthesize_cli_approval_decision(
                    request=approval_request,
                    approve_run_agent=approve_run_agent,
                    approve_push_update=approve_push_update,
                    approve_comment=approve_comment,
                )
                if decision is not None:
                    approval_decision_path = write_approval_decision(decision, post_pr_dir / "approval_decision.json", overwrite=True)
            if decision is None:
                break
            if decision.status == "pending":
                terminal_reason = "awaiting_approval"
                status = "awaiting_approval"
                state = mark_terminal(state, status=status, terminal_reason=terminal_reason, blockers=blockers)
                break
            if decision.status == "rejected":
                terminal_reason = "approval_rejected"
                status = "blocked"
                blockers.append("approval_rejected")
                state = mark_terminal(state, status=status, terminal_reason=terminal_reason, blockers=blockers)
                break
            validation_errors = validate_approval_decision(
                decision,
                request=approval_request,
                existing_commit_sha=latest_successful_commit_for_round(side_effects, round_id=round_id),
            )
            if validation_errors:
                terminal_reason = approval_terminal_reason(validation_errors)
                status = "blocked"
                blockers.extend(validation_errors)
                state = mark_terminal(state, status=status, terminal_reason=terminal_reason, blockers=blockers)
                break
            run_agent_approved = is_action_approved(decision, "run_agent")
            push_approved = is_action_approved(decision, "push_update")
            comment_approved = is_action_approved(decision, "post_comment")
            existing_commit_sha = latest_successful_commit_for_round(side_effects, round_id=round_id)
            approval_decision_sha256 = sha256_file(approval_decision_path) if approval_decision_path else None
            comment_marker = f"{run_id}:{round_id}:{approval_request.approval_request_sha256 or 'pending'}"
            execute_ref = collect_round
            execute_manifest: dict[str, Any] | None = None
            execute_snapshots: list[Any] = []
            run_agent_succeeded = has_succeeded_effect(
                side_effects,
                round_id=round_id,
                action="run_agent",
                approval_decision_sha256=approval_decision_sha256,
            )
            push_succeeded = has_succeeded_effect(
                side_effects,
                round_id=round_id,
                action="push_update",
                approval_decision_sha256=approval_decision_sha256,
                commit_sha=existing_commit_sha,
            )
            comment_succeeded = has_succeeded_effect(
                side_effects,
                round_id=round_id,
                action="post_comment",
                approval_decision_sha256=approval_decision_sha256,
                comment_marker=comment_marker,
            )
            if run_agent_approved and allow_execute:
                if not run_agent_succeeded:
                    execute_ref, execute_manifest, execute_snapshots = run_feedback_round(
                        run_dir=run_dir_path,
                        auto_pr_manifest_path=manifest_path,
                        round_dir=round_dir,
                        phase="execute",
                        dry_run=False,
                        execute=True,
                        allow_run_agent=True,
                        allow_push_update=push_approved and not push_succeeded,
                        allow_comment=comment_approved and not comment_succeeded,
                        wait_ci=wait_ci,
                        poll_interval_seconds=poll_interval_seconds,
                        timeout_seconds=timeout_seconds,
                        token_env=token_env,
                        include_logs=include_logs,
                        include_success_logs=include_success_logs,
                        max_log_bytes=max_log_bytes,
                        max_feedback_items=max_feedback_items,
                        overwrite=overwrite,
                        github_client=github_client,
                        comment_marker=comment_marker if comment_approved else None,
                    )
                else:
                    execute_ref = replace(
                        collect_round,
                        status=collect_round.status,
                        agent_ran=True,
                        commit_created=bool(existing_commit_sha or collect_round.commit_created),
                        new_commit_sha=existing_commit_sha or collect_round.new_commit_sha,
                        push_update_executed=push_succeeded or collect_round.push_update_executed,
                        comment_posted=comment_succeeded or collect_round.comment_posted,
                        execute_manifest_path=collect_manifest_path,
                    )
                    execute_manifest = collect_manifest
                    execute_snapshots = []
            elif existing_commit_sha and push_approved and not push_succeeded:
                push_result = push_existing_followup_commit_if_approved(
                    run_dir=run_dir_path,
                    pr_feedback_manifest=collect_manifest,
                    commit_sha=existing_commit_sha,
                    execute=True,
                    allow_push_update=True,
                )
                execute_ref = replace(
                    collect_round,
                    status="branch_updated" if push_result.get("pushed") else "patch_ready",
                    commit_created=True,
                    new_commit_sha=existing_commit_sha,
                    push_update_executed=bool(push_result.get("pushed")),
                    execute_manifest_path=collect_manifest_path,
                )
                execute_manifest = collect_manifest
            elif existing_commit_sha and push_approved:
                execute_ref = replace(
                    collect_round,
                    status="branch_updated",
                    commit_created=True,
                    new_commit_sha=existing_commit_sha,
                    push_update_executed=True,
                    execute_manifest_path=collect_manifest_path,
                )
                execute_manifest = collect_manifest
            else:
                terminal_reason = "awaiting_approval"
                status = "awaiting_approval"
                state = mark_terminal(state, status=status, terminal_reason=terminal_reason, blockers=blockers)
                break
            if execute_ref.status == "blocked":
                # execute 阶段一旦返回 blocked，就保留原始 blocker，
                # 并根据 blocker 类型映射到更具体的 terminal_reason。
                blockers.extend(execute_ref.blockers)
                if any("stale" in blocker for blocker in execute_ref.blockers):
                    terminal_reason = "stale_head"
                elif any("push" in blocker.lower() for blocker in execute_ref.blockers):
                    terminal_reason = "push_failed"
                else:
                    terminal_reason = "api_degraded" if any("api" in blocker.lower() for blocker in execute_ref.blockers) else "unsafe_branch"
                status = "blocked"
                state = mark_terminal(state, status=status, terminal_reason=terminal_reason, blockers=blockers)
                result_rounds[-1] = execute_ref
                if run_agent_approved and not execute_ref.agent_ran:
                    side_effects = _append_effect(
                        side_effects,
                        round_id=round_id,
                        action="run_agent",
                        status="failed",
                        approval_decision_sha256=approval_decision_sha256,
                        head_sha_before=execute_ref.head_sha_before,
                        head_sha_after=execute_ref.head_sha_after,
                        commit_sha=execute_ref.new_commit_sha,
                        error="run_agent not executed",
                        metadata={"phase": "execute", "terminal_reason": terminal_reason},
                    )
                if push_approved and not execute_ref.push_update_executed:
                    side_effects = _append_effect(
                        side_effects,
                        round_id=round_id,
                        action="push_update",
                        status="failed" if execute_ref.commit_created else "skipped",
                        approval_decision_sha256=approval_decision_sha256,
                        commit_sha=execute_ref.new_commit_sha,
                        remote_ref=str((collect_manifest.get("pr") or {}).get("head_branch") or ""),
                        error="push_update not executed",
                        metadata={"phase": "execute", "terminal_reason": terminal_reason},
                    )
                if comment_approved and not execute_ref.comment_posted:
                    side_effects = _append_effect(
                        side_effects,
                        round_id=round_id,
                        action="post_comment",
                        status="failed" if execute_ref.commit_created else "skipped",
                        approval_decision_sha256=approval_decision_sha256,
                        comment_marker=comment_marker,
                        error="post_comment not executed",
                        metadata={"phase": "execute", "terminal_reason": terminal_reason},
                    )
                break
            if execute_ref.agent_ran:
                if not run_agent_succeeded:
                    side_effects = _append_effect(
                        side_effects,
                        round_id=round_id,
                        action="run_agent",
                        status="succeeded" if execute_ref.agent_ran else "failed",
                        approval_decision_sha256=approval_decision_sha256,
                        head_sha_before=execute_ref.head_sha_before,
                        head_sha_after=execute_ref.head_sha_after,
                        commit_sha=execute_ref.new_commit_sha,
                        metadata={"phase": "execute"},
                    )
            if execute_ref.commit_created:
                if not has_succeeded_effect(side_effects, round_id=round_id, action="commit", commit_sha=execute_ref.new_commit_sha):
                    side_effects = _append_effect(
                        side_effects,
                        round_id=round_id,
                        action="commit",
                        status="succeeded",
                        approval_decision_sha256=approval_decision_sha256,
                        commit_sha=execute_ref.new_commit_sha,
                        metadata={"phase": "execute"},
                    )
            if execute_ref.push_update_executed:
                if not push_succeeded:
                    side_effects = _append_effect(
                        side_effects,
                        round_id=round_id,
                        action="push_update",
                        status="succeeded",
                        approval_decision_sha256=approval_decision_sha256,
                        commit_sha=execute_ref.new_commit_sha,
                        remote_ref=str((collect_manifest.get("pr") or {}).get("head_branch") or ""),
                        metadata={"phase": "execute"},
                    )
            if execute_ref.comment_posted:
                if not comment_succeeded:
                    side_effects = _append_effect(
                        side_effects,
                        round_id=round_id,
                        action="post_comment",
                        status="succeeded",
                        approval_decision_sha256=approval_decision_sha256,
                        comment_marker=comment_marker,
                        metadata={"phase": "execute"},
                    )
            # 执行阶段允许审批，但实际没有跑起来时，也要把失败事实记入 ledger。
            if run_agent_approved and not execute_ref.agent_ran:
                side_effects = _append_effect(
                    side_effects,
                    round_id=round_id,
                    action="run_agent",
                    status="failed",
                    approval_decision_sha256=approval_decision_sha256,
                    head_sha_before=execute_ref.head_sha_before,
                    head_sha_after=execute_ref.head_sha_after,
                    commit_sha=execute_ref.new_commit_sha,
                    error="run_agent not executed",
                    metadata={"phase": "execute"},
                )
            # push 只有在确实创建了 commit 但没有成功推送时，才记为 failed。
            if push_approved and not execute_ref.push_update_executed and execute_ref.commit_created:
                side_effects = _append_effect(
                    side_effects,
                    round_id=round_id,
                    action="push_update",
                    status="failed",
                    approval_decision_sha256=approval_decision_sha256,
                    commit_sha=execute_ref.new_commit_sha,
                    remote_ref=str((collect_manifest.get("pr") or {}).get("head_branch") or ""),
                    error="push_update not executed",
                    metadata={"phase": "execute"},
                )
            # 评论审批已给出，但实际没有发出评论时，记录为 failed，方便审计缺口。
            if comment_approved and not execute_ref.comment_posted:
                side_effects = _append_effect(
                    side_effects,
                    round_id=round_id,
                    action="post_comment",
                    status="failed",
                    approval_decision_sha256=approval_decision_sha256,
                    comment_marker=comment_marker,
                    error="post_comment not executed",
                    metadata={"phase": "execute"},
                )
            if execute_manifest is not None:
                execute_delta = build_feedback_delta(
                    previous_fingerprints=collect_round.feedback_fingerprints,
                    current_fingerprints=extract_feedback_fingerprints(execute_manifest),
                )
                _write_round_delta(round_dir, "execute", execute_delta.__dict__)
                write_round_manifest(
                    round_ref=execute_ref,
                    collect_manifest=collect_manifest,
                    execute_manifest=execute_manifest,
                    feedback_delta=execute_delta.__dict__,
                    snapshots=execute_snapshots,
                    output_path=_round_manifest_path(round_dir, "execute"),
                    overwrite=True,
                )
            result_rounds[-1] = execute_ref
            state = _refresh_round_state(state, result_rounds[-1])
            if execute_ref.push_update_executed:
                status = "branch_updated"
                if round_index >= max_rounds:
                    terminal_reason = "max_rounds_reached"
                    status = "max_rounds_reached"
                    state = mark_terminal(state, status=status, terminal_reason=terminal_reason, blockers=blockers)
                    break
                previous_fingerprints = collect_ref.feedback_fingerprints
                continue
            if execute_ref.commit_created:
                status = "patch_ready"
                terminal_reason = "awaiting_approval"
                state = mark_terminal(state, status=status, terminal_reason=terminal_reason, blockers=blockers)
                break
            if execute_ref.agent_ran:
                status = "agent_ran"
                terminal_reason = "awaiting_approval"
                state = mark_terminal(state, status=status, terminal_reason=terminal_reason, blockers=blockers)
                break
            terminal_reason = "awaiting_approval"
            status = "awaiting_approval"
            state = mark_terminal(state, status=status, terminal_reason=terminal_reason, blockers=blockers)
            break
        if terminal_reason == "none" and not result_rounds:
            terminal_reason = "awaiting_approval"
            status = "awaiting_approval"
            state = mark_terminal(state, status=status, terminal_reason=terminal_reason, blockers=blockers)
        if approval_request is None and state.get("terminal_reason") == "none":
            state = mark_terminal(state, status=status, terminal_reason=terminal_reason, blockers=blockers)
        write_post_pr_state(state, state_path, overwrite=True)
        atomic_write_json(side_effects, side_effects_path, overwrite=True)
        if post_pr_action_template:
            workflow_path = write_post_pr_automation_workflow_template(post_pr_dir / "post_pr_automation_workflow.yml", overwrite=True)
        result = _result_from_state(
            run_id=run_id,
            run_dir=run_dir_path,
            post_pr_dir=post_pr_dir,
            state=state,
            approval_request_path=approval_request_path,
            approval_decision_path=approval_decision_path,
            side_effects_path=side_effects_path,
            manifest_path=post_pr_dir / "post_pr_automation_manifest.json",
            report_path=post_pr_dir / "post_pr_automation_report.md",
            workflow_path=workflow_path,
        )
        report_path = write_post_pr_automation_report(result, result.report_path, state=state, side_effects=side_effects, overwrite=True)
        result = replace(result, report_path=report_path)
        manifest_written = _write_post_pr_automation_manifest(
            result=result,
            auto_pr_manifest_path=manifest_path,
            state=state,
            side_effects=side_effects,
        )
        result = replace(result, manifest_path=manifest_written)
        return result
    except FileNotFoundError:
        raise
    except ValueError as exc:
        state = mark_terminal(state, status="blocked", terminal_reason="manifest_invalid", blockers=[str(exc)]) if "state" in locals() else initial_post_pr_state(run_id=run_id, run_dir=run_dir_path, max_rounds=max_rounds)
        write_post_pr_state(state, state_path, overwrite=True)
        side_effects = side_effects if "side_effects" in locals() else initial_side_effects(run_id)
        atomic_write_json(side_effects, side_effects_path, overwrite=True)
        result = _result_from_state(
            run_id=run_id,
            run_dir=run_dir_path,
            post_pr_dir=post_pr_dir,
            state=state,
            approval_request_path=None,
            approval_decision_path=None,
            side_effects_path=side_effects_path,
            manifest_path=post_pr_dir / "post_pr_automation_manifest.json",
            report_path=post_pr_dir / "post_pr_automation_report.md",
            workflow_path=None,
        )
        report_path = write_post_pr_automation_report(result, result.report_path, state=state, side_effects=side_effects, overwrite=True)
        result = replace(
            result,
            report_path=report_path,
            manifest_path=_write_post_pr_automation_manifest(result=result, auto_pr_manifest_path=manifest_path, state=state, side_effects=side_effects),
        )
        return result
    finally:
        if lock is not None:
            release_state_lock(lock)
