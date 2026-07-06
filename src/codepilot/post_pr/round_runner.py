from __future__ import annotations

"""第十五步 Post-PR automation 的单轮执行与 artifact snapshot。"""

import json
import shutil
from pathlib import Path
from typing import Any

from codepilot.post_pr.feedback_delta import extract_feedback_fingerprints, load_ci_feedback_manifest
from codepilot.post_pr.models import ArtifactSnapshotEntry, PostPRRoundPhase, PostPRRoundRef
from codepilot.post_pr.state_store import atomic_write_json
from codepilot.pr_feedback.github_client import PRFeedbackGitHubClientProtocol
from codepilot.pr_feedback.models import PRFeedbackResult, PRRef
from codepilot.pr_feedback.workflow import PR_FEEDBACK_ARTIFACT_NAMES, run_pr_feedback_loop
from codepilot.repo.git_utils import sha256_file


SNAPSHOT_ALLOWED_NAMES = [
    "ci_status.json",
    "review_feedback.json",
    "ci_feedback_report.md",
    "followup_task.md",
    "pr_update_plan.md",
    "ci_feedback_manifest.json",
    "pr_feedback_workflow.yml",
]


def snapshot_pr_feedback_artifacts(
    *,
    source_run_dir: str | Path,
    round_dir: str | Path,
    phase: PostPRRoundPhase,
    overwrite: bool = False,
) -> list[ArtifactSnapshotEntry]:
    source_dir = Path(source_run_dir).expanduser().resolve()
    target_dir = Path(round_dir).expanduser().resolve() / phase / "pr_feedback_snapshot"
    target_dir.mkdir(parents=True, exist_ok=True)
    snapshots: list[ArtifactSnapshotEntry] = []
    for name in SNAPSHOT_ALLOWED_NAMES:
        source_path = source_dir / name
        snapshot_path = target_dir / name
        exists = source_path.exists()
        if snapshot_path.exists() and not overwrite:
            raise FileExistsError(snapshot_path)
        if exists:
            shutil.copy2(source_path, snapshot_path)
        snapshots.append(
            ArtifactSnapshotEntry(
                name=name,
                source_path=str(source_path),
                snapshot_path=str(snapshot_path),
                exists=exists,
                size_bytes=source_path.stat().st_size if exists else None,
                sha256=sha256_file(source_path) if exists else None,
                phase=phase,
            )
        )
    return snapshots


def run_feedback_round(
    *,
    run_dir: str | Path,
    auto_pr_manifest_path: str | Path,
    round_dir: str | Path,
    phase: PostPRRoundPhase,
    dry_run: bool,
    execute: bool,
    allow_run_agent: bool,
    allow_push_update: bool,
    allow_comment: bool,
    wait_ci: bool,
    poll_interval_seconds: int,
    timeout_seconds: int,
    token_env: str,
    include_logs: bool,
    include_success_logs: bool,
    max_log_bytes: int,
    max_feedback_items: int,
    overwrite: bool,
    github_client: PRFeedbackGitHubClientProtocol | None = None,
    comment_marker: str | None = None,
) -> tuple[PostPRRoundRef, dict[str, Any], list[ArtifactSnapshotEntry]]:
    result = run_pr_feedback_loop(
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
        max_followup_rounds=1,
        poll_interval_seconds=poll_interval_seconds,
        timeout_seconds=timeout_seconds,
        token_env=token_env,
        feedback_action_template=True,
        overwrite=True,
        github_client=github_client,
        comment_marker=comment_marker,
    )
    snapshots = snapshot_pr_feedback_artifacts(source_run_dir=run_dir, round_dir=round_dir, phase=phase, overwrite=overwrite)
    manifest_path = next(
        (
            Path(item.snapshot_path)
            for item in snapshots
            if item.name == "ci_feedback_manifest.json" and item.exists
        ),
        Path(run_dir).expanduser().resolve() / "ci_feedback_manifest.json",
    )
    manifest = load_ci_feedback_manifest(manifest_path)
    fingerprints = extract_feedback_fingerprints(manifest)
    round_ref = PostPRRoundRef(
        round_id=Path(round_dir).name,
        round_index=int(Path(round_dir).name.split("-", maxsplit=1)[1]),
        round_dir=Path(round_dir).expanduser().resolve(),
        collect_manifest_path=manifest_path if phase == "collect" else None,
        execute_manifest_path=manifest_path if phase == "execute" else None,
        latest_pr_feedback_manifest_path=manifest_path,
        feedback_fingerprints=fingerprints,
        status=result.status,
        terminal_reason="none",
        head_sha_before=getattr(result.feedback_freshness, "observed_head_sha", None),
        head_sha_after=getattr(result.feedback_freshness, "current_head_sha", None),
        agent_ran=result.agent_ran,
        patch_generated=result.patch_generated,
        commit_created=result.commit_created,
        new_commit_sha=result.new_commit_sha,
        push_update_executed=bool(result.push_update_executed and allow_push_update),
        comment_posted=bool(result.comment_posted and allow_comment),
        blockers=list(result.blockers),
        warnings=list(result.warnings),
    )
    return round_ref, manifest, snapshots


def write_round_manifest(
    *,
    round_ref: PostPRRoundRef,
    collect_manifest: dict[str, Any] | None,
    execute_manifest: dict[str, Any] | None,
    feedback_delta: dict[str, Any] | None,
    snapshots: list[ArtifactSnapshotEntry],
    output_path: str | Path,
    overwrite: bool = False,
) -> Path:
    payload = {
        "schema_version": "codepilot.post_pr.round_manifest.v1",
        "round_id": round_ref.round_id,
        "status": round_ref.status,
        "terminal_reason": round_ref.terminal_reason,
        "round": round_ref,
        "collect_manifest": collect_manifest,
        "execute_manifest": execute_manifest,
        "feedback_delta": feedback_delta,
        "snapshots": snapshots,
    }
    return atomic_write_json(payload, output_path, overwrite=overwrite)


def push_existing_followup_commit_if_approved(
    *,
    run_dir: str | Path,
    pr_feedback_manifest: dict[str, Any],
    commit_sha: str,
    execute: bool,
    allow_push_update: bool,
) -> dict[str, Any]:
    from codepilot.pr_feedback.branch_update import push_pr_branch_update_if_allowed

    if not execute:
        return {"pushed": False, "reason": "execute=false"}
    if not allow_push_update:
        return {"pushed": False, "reason": "allow_push_update=false"}
    run_dir_path = Path(run_dir).expanduser().resolve()
    auto_pr_manifest_path = run_dir_path / "auto_pr_manifest.json"
    auto_pr_manifest = json.loads(auto_pr_manifest_path.read_text(encoding="utf-8"))
    source_artifact_manifest_path = run_dir_path / str(auto_pr_manifest.get("source_artifact_manifest") or "artifact_manifest.json")
    source_artifact_manifest = json.loads(source_artifact_manifest_path.read_text(encoding="utf-8"))
    repo_path = Path(
        source_artifact_manifest.get("effective_repo_path") or source_artifact_manifest.get("repo_path")
    ).expanduser().resolve()
    pr_data = pr_feedback_manifest.get("pr") or {}
    pr = PRRef(
        owner=str(pr_data.get("owner") or ""),
        repo=str(pr_data.get("repo") or ""),
        pull_number=int(pr_data.get("pull_number") or 0),
        url=str(pr_data.get("url") or ""),
        head_branch=str(pr_data.get("head_branch") or ""),
        base_branch=str(pr_data.get("base_branch") or ""),
        head_sha=pr_data.get("head_sha"),
        base_sha=pr_data.get("base_sha"),
    )
    freshness = pr_feedback_manifest.get("feedback_freshness") or {}
    expected_current_head_sha = freshness.get("current_head_sha") or pr.head_sha
    return push_pr_branch_update_if_allowed(
        repo_path=repo_path,
        pr=pr,
        new_commit_sha=commit_sha,
        expected_current_head_sha=str(expected_current_head_sha),
        execute=execute,
        allow_push_update=allow_push_update,
    )
