from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from codepilot.post_pr.models import ArtifactSnapshotEntry, PostPRRoundRef
from codepilot.post_pr.round_runner import run_feedback_round, snapshot_pr_feedback_artifacts, write_round_manifest


def test_snapshot_and_round_manifest(tmp_path: Path, monkeypatch) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    manifest = {
        "schema_version": "codepilot.ci_feedback_manifest.v1",
        "safe_summary": {"feedback_items": []},
        "summary": {"checks_total": 0},
    }
    (run_dir / "ci_feedback_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (run_dir / "ci_status.json").write_text("{}", encoding="utf-8")
    snapshots = snapshot_pr_feedback_artifacts(source_run_dir=run_dir, round_dir=tmp_path / "post_pr/round-001", phase="collect")
    assert any(item.name == "ci_feedback_manifest.json" and item.exists for item in snapshots)
    round_ref = PostPRRoundRef(round_id="round-001", round_index=1, round_dir=tmp_path / "post_pr/round-001")
    path = write_round_manifest(
        round_ref=round_ref,
        collect_manifest=manifest,
        execute_manifest=None,
        feedback_delta=None,
        snapshots=snapshots,
        output_path=tmp_path / "post_pr/round-001/collect/round_manifest.json",
        overwrite=True,
    )
    assert path.exists()


def test_run_feedback_round_uses_monkeypatched_workflow(tmp_path: Path, monkeypatch) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    manifest = {
        "schema_version": "codepilot.ci_feedback_manifest.v1",
        "safe_summary": {"feedback_items": [{"severity": "blocking", "kind": "ci_failure", "fingerprint": "fp"}]},
        "summary": {"checks_total": 0},
        "pr": {"owner": "o", "repo": "r", "pull_number": 1, "url": "https://example.com", "head_branch": "codepilot/test", "base_branch": "main", "head_sha": "abc"},
        "feedback_freshness": {"current_head_sha": "abc"},
    }
    for name in ["ci_status.json", "review_feedback.json", "ci_feedback_report.md", "followup_task.md", "pr_update_plan.md", "ci_feedback_manifest.json", "pr_feedback_workflow.yml"]:
        (run_dir / name).write_text(json.dumps(manifest) if name.endswith(".json") else "# x\n", encoding="utf-8")
    monkeypatch.setattr(
        "codepilot.post_pr.round_runner.run_pr_feedback_loop",
        lambda **kwargs: SimpleNamespace(
            status="feedback_found",
            agent_ran=False,
            patch_generated=False,
            commit_created=False,
            new_commit_sha=None,
            push_update_executed=False,
            comment_posted=False,
            feedback_freshness=SimpleNamespace(observed_head_sha="abc", current_head_sha="abc"),
            blockers=[],
            warnings=[],
        ),
    )
    round_ref, loaded_manifest, snapshots = run_feedback_round(
        run_dir=run_dir,
        auto_pr_manifest_path=tmp_path / "auto_pr_manifest.json",
        round_dir=tmp_path / "post_pr/round-001",
        phase="collect",
        dry_run=True,
        execute=False,
        allow_run_agent=False,
        allow_push_update=False,
        allow_comment=False,
        wait_ci=False,
        poll_interval_seconds=1,
        timeout_seconds=1,
        token_env="GITHUB_TOKEN",
        include_logs=True,
        include_success_logs=False,
        max_log_bytes=100,
        max_feedback_items=20,
        overwrite=True,
    )
    assert round_ref.round_id == "round-001"
    assert loaded_manifest["schema_version"] == "codepilot.ci_feedback_manifest.v1"
    assert snapshots

