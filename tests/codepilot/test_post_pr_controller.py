from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from codepilot.post_pr.approval import load_approval_request, synthesize_cli_approval_decision, write_approval_decision
from codepilot.post_pr.controller import run_post_pr_automation
from codepilot.post_pr.models import ArtifactSnapshotEntry, PostPRRoundRef


def _write_auto_pr_manifest(run_dir: Path) -> None:
    (run_dir / "auto_pr_manifest.json").write_text(
        json.dumps({"schema_version": "codepilot.auto_pr_manifest.v1", "run_id": "issue-001"}, indent=2),
        encoding="utf-8",
    )


def test_run_post_pr_automation_dry_run_writes_request(tmp_path: Path, monkeypatch) -> None:
    run_dir = tmp_path / "runs" / "issue-001"
    run_dir.mkdir(parents=True)
    _write_auto_pr_manifest(run_dir)

    def fake_round(**kwargs):
        round_ref = PostPRRoundRef(
            round_id="round-001",
            round_index=1,
            round_dir=run_dir / "post_pr/round-001",
            latest_pr_feedback_manifest_path=run_dir / "ci_feedback_manifest.json",
            feedback_fingerprints=["fp-1"],
            status="feedback_found",
        )
        manifest = {
            "schema_version": "codepilot.ci_feedback_manifest.v1",
            "pr": {"url": "https://example.com", "head_branch": "codepilot/test", "head_sha": "abc"},
            "feedback_freshness": {"current_head_sha": "abc"},
            "safe_summary": {"feedback_items": [{"severity": "blocking", "kind": "ci_failure", "fingerprint": "fp-1"}]},
            "summary": {"checks_total": 0},
        }
        snapshots = [ArtifactSnapshotEntry(name="ci_feedback_manifest.json", source_path="x", snapshot_path="y", exists=True)]
        (run_dir / "ci_feedback_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        return round_ref, manifest, snapshots

    monkeypatch.setattr("codepilot.post_pr.controller.run_feedback_round", fake_round)
    result = run_post_pr_automation(run_dir=run_dir, dry_run=True, execute=False, overwrite=True, post_pr_action_template=False)
    assert result.status == "awaiting_approval"
    assert result.approval_request_path is not None
    assert result.report_path is not None
    assert result.manifest_path is not None
    assert result.rounds[0].round_id == "round-001"
    assert result.rounds[0].round_index == 1
    assert not (run_dir / "post_pr" / "round-000").exists()
    request_payload = json.loads((run_dir / "post_pr" / "approval_request.json").read_text(encoding="utf-8"))
    assert "push_update" in request_payload["requested_actions"]


def test_python_api_dry_run_execute_does_not_run_execute_phase(tmp_path: Path, monkeypatch) -> None:
    run_dir = tmp_path / "runs" / "issue-001"
    run_dir.mkdir(parents=True)
    _write_auto_pr_manifest(run_dir)
    phases: list[str] = []

    def fake_round(**kwargs):
        phases.append(kwargs["phase"])
        if kwargs["phase"] == "execute":
            raise AssertionError("execute phase must not run when dry_run=True")
        round_ref = PostPRRoundRef(
            round_id="round-001",
            round_index=1,
            round_dir=run_dir / "post_pr/round-001",
            latest_pr_feedback_manifest_path=run_dir / "ci_feedback_manifest.json",
            feedback_fingerprints=["fp-1"],
            status="feedback_found",
        )
        manifest = {
            "schema_version": "codepilot.ci_feedback_manifest.v1",
            "pr": {"url": "https://example.com", "head_branch": "codepilot/test", "head_sha": "abc"},
            "feedback_freshness": {"current_head_sha": "abc"},
            "safe_summary": {"feedback_items": [{"severity": "blocking", "kind": "ci_failure", "fingerprint": "fp-1"}]},
            "summary": {"checks_total": 0},
        }
        snapshots = [ArtifactSnapshotEntry(name="ci_feedback_manifest.json", source_path="x", snapshot_path="y", exists=True)]
        (run_dir / "ci_feedback_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        return round_ref, manifest, snapshots

    monkeypatch.setattr("codepilot.post_pr.controller.run_feedback_round", fake_round)
    result = run_post_pr_automation(
        run_dir=run_dir,
        dry_run=True,
        execute=True,
        approve_run_agent=True,
        overwrite=True,
        post_pr_action_template=False,
    )
    assert phases == ["collect"]
    assert result.status == "awaiting_approval"


def test_dry_run_approve_comment_requests_post_comment_action(tmp_path: Path, monkeypatch) -> None:
    run_dir = tmp_path / "runs" / "issue-001"
    run_dir.mkdir(parents=True)
    _write_auto_pr_manifest(run_dir)

    def fake_round(**kwargs):
        round_ref = PostPRRoundRef(
            round_id="round-001",
            round_index=1,
            round_dir=run_dir / "post_pr/round-001",
            latest_pr_feedback_manifest_path=run_dir / "ci_feedback_manifest.json",
            feedback_fingerprints=["fp-1"],
            status="feedback_found",
        )
        manifest = {
            "schema_version": "codepilot.ci_feedback_manifest.v1",
            "pr": {"url": "https://example.com", "head_branch": "codepilot/test", "head_sha": "abc"},
            "feedback_freshness": {"current_head_sha": "abc"},
            "safe_summary": {"feedback_items": [{"severity": "blocking", "kind": "ci_failure", "fingerprint": "fp-1"}]},
            "summary": {"checks_total": 0},
        }
        snapshots = [ArtifactSnapshotEntry(name="ci_feedback_manifest.json", source_path="x", snapshot_path="y", exists=True)]
        (run_dir / "ci_feedback_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        return round_ref, manifest, snapshots

    monkeypatch.setattr("codepilot.post_pr.controller.run_feedback_round", fake_round)
    run_post_pr_automation(run_dir=run_dir, dry_run=True, execute=False, overwrite=True, post_pr_action_template=False, approve_comment=True)
    request_payload = json.loads((run_dir / "post_pr" / "approval_request.json").read_text(encoding="utf-8"))
    assert "post_comment" in request_payload["requested_actions"]


def test_run_post_pr_automation_no_feedback(tmp_path: Path, monkeypatch) -> None:
    run_dir = tmp_path / "runs" / "issue-001"
    run_dir.mkdir(parents=True)
    _write_auto_pr_manifest(run_dir)

    monkeypatch.setattr(
        "codepilot.post_pr.controller.run_feedback_round",
        lambda **kwargs: (
            PostPRRoundRef(
                round_id="round-001",
                round_index=1,
                round_dir=run_dir / "post_pr/round-001",
                latest_pr_feedback_manifest_path=run_dir / "ci_feedback_manifest.json",
                feedback_fingerprints=[],
                status="no_feedback",
            ),
            {
                "schema_version": "codepilot.ci_feedback_manifest.v1",
                "pr": {"url": "https://example.com", "head_branch": "codepilot/test", "head_sha": "abc"},
                "feedback_freshness": {"current_head_sha": "abc"},
                "safe_summary": {"feedback_items": []},
                "summary": {"checks_total": 0},
            },
            [ArtifactSnapshotEntry(name="ci_feedback_manifest.json", source_path="x", snapshot_path="y", exists=True)],
        ),
    )
    result = run_post_pr_automation(run_dir=run_dir, dry_run=True, execute=False, overwrite=True, post_pr_action_template=False)
    assert result.status == "no_feedback"


def test_run_post_pr_automation_execute_runs_execute_phase(tmp_path: Path, monkeypatch) -> None:
    run_dir = tmp_path / "runs" / "issue-001"
    run_dir.mkdir(parents=True)
    _write_auto_pr_manifest(run_dir)
    phases: list[str] = []

    def fake_round(**kwargs):
        phases.append(kwargs["phase"])
        round_ref = PostPRRoundRef(
            round_id="round-001",
            round_index=1,
            round_dir=run_dir / "post_pr/round-001",
            latest_pr_feedback_manifest_path=run_dir / "ci_feedback_manifest.json",
            feedback_fingerprints=["fp-1"],
            status="feedback_found",
            commit_created=kwargs["phase"] == "execute",
            push_update_executed=False,
        )
        manifest = {
            "schema_version": "codepilot.ci_feedback_manifest.v1",
            "pr": {"url": "https://example.com", "head_branch": "codepilot/test", "head_sha": "abc"},
            "feedback_freshness": {"current_head_sha": "abc"},
            "safe_summary": {"feedback_items": [{"severity": "blocking", "kind": "ci_failure", "fingerprint": "fp-1"}]},
            "summary": {"checks_total": 0},
        }
        snapshots = [ArtifactSnapshotEntry(name="ci_feedback_manifest.json", source_path="x", snapshot_path="y", exists=True)]
        (run_dir / "ci_feedback_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        return round_ref, manifest, snapshots

    monkeypatch.setattr("codepilot.post_pr.controller.run_feedback_round", fake_round)
    result = run_post_pr_automation(
        run_dir=run_dir,
        dry_run=False,
        execute=True,
        approve_run_agent=True,
        overwrite=True,
        post_pr_action_template=False,
    )
    assert phases == ["collect", "execute"]
    assert result.rounds[0].round_id == "round-001"


def test_run_post_pr_automation_resume_approval_file_executes_same_round(tmp_path: Path, monkeypatch) -> None:
    run_dir = tmp_path / "runs" / "issue-001"
    run_dir.mkdir(parents=True)
    _write_auto_pr_manifest(run_dir)
    phases: list[str] = []

    def fake_round(**kwargs):
        phases.append(kwargs["phase"])
        manifest = {
            "schema_version": "codepilot.ci_feedback_manifest.v1",
            "pr": {"url": "https://example.com", "head_branch": "codepilot/test", "head_sha": "abc"},
            "feedback_freshness": {"current_head_sha": "abc"},
            "safe_summary": {"feedback_items": [{"severity": "blocking", "kind": "ci_failure", "fingerprint": "fp-1"}]},
            "summary": {"checks_total": 0},
        }
        round_ref = PostPRRoundRef(
            round_id="round-001",
            round_index=1,
            round_dir=run_dir / "post_pr/round-001",
            latest_pr_feedback_manifest_path=run_dir / "ci_feedback_manifest.json",
            feedback_fingerprints=["fp-1"],
            status="feedback_found" if kwargs["phase"] == "collect" else "patch_ready",
            commit_created=kwargs["phase"] == "execute",
            new_commit_sha="abc" if kwargs["phase"] == "execute" else None,
        )
        (run_dir / "ci_feedback_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        snapshots = [ArtifactSnapshotEntry(name="ci_feedback_manifest.json", source_path="x", snapshot_path="y", exists=True)]
        return round_ref, manifest, snapshots

    monkeypatch.setattr("codepilot.post_pr.controller.run_feedback_round", fake_round)
    run_post_pr_automation(run_dir=run_dir, dry_run=True, execute=False, overwrite=True, post_pr_action_template=False)
    decision = synthesize_cli_approval_decision(
        request=load_approval_request(run_dir / "post_pr" / "approval_request.json"),
        approve_run_agent=True,
        approve_push_update=False,
        approve_comment=False,
    )
    assert decision is not None
    approval_file = write_approval_decision(decision, run_dir / "post_pr" / "approval_decision.json", overwrite=True)
    result = run_post_pr_automation(
        run_dir=run_dir,
        dry_run=False,
        execute=True,
        approval_file=approval_file,
        resume=True,
        overwrite=True,
        post_pr_action_template=False,
    )
    assert phases == ["collect", "execute"]
    assert result.rounds[0].round_id == "round-001"
    assert len(result.rounds) == 1
    assert not (run_dir / "post_pr" / "round-002").exists()


def test_run_post_pr_automation_state_locked_writes_report_and_manifest(tmp_path: Path) -> None:
    run_dir = tmp_path / "runs" / "issue-001"
    post_pr_dir = run_dir / "post_pr"
    post_pr_dir.mkdir(parents=True)
    _write_auto_pr_manifest(run_dir)
    kept_round_dir = post_pr_dir / "round-999"
    kept_round_dir.mkdir()
    (kept_round_dir / "marker.txt").write_text("keep me", encoding="utf-8")
    (post_pr_dir / ".lock").write_text("locked", encoding="utf-8")

    result = run_post_pr_automation(run_dir=run_dir, dry_run=True, execute=False, overwrite=True, post_pr_action_template=False)

    assert result.status == "blocked"
    assert result.terminal_reason == "state_locked"
    assert (kept_round_dir / "marker.txt").exists()
    assert (post_pr_dir / "state.json").exists()
    assert (post_pr_dir / "side_effects.json").exists()
    assert (post_pr_dir / "post_pr_automation_report.md").exists()
    assert (post_pr_dir / "post_pr_automation_manifest.json").exists()


def test_resume_tampered_approval_request_stops_as_stale_approval(tmp_path: Path, monkeypatch) -> None:
    run_dir = tmp_path / "runs" / "issue-001"
    run_dir.mkdir(parents=True)
    _write_auto_pr_manifest(run_dir)

    def fake_round(**kwargs):
        manifest = {
            "schema_version": "codepilot.ci_feedback_manifest.v1",
            "pr": {"url": "https://example.com", "head_branch": "codepilot/test", "head_sha": "abc"},
            "feedback_freshness": {"current_head_sha": "abc"},
            "safe_summary": {"feedback_items": [{"severity": "blocking", "kind": "ci_failure", "fingerprint": "fp-1"}]},
            "summary": {"checks_total": 0},
        }
        round_ref = PostPRRoundRef(
            round_id="round-001",
            round_index=1,
            round_dir=run_dir / "post_pr/round-001",
            latest_pr_feedback_manifest_path=run_dir / "ci_feedback_manifest.json",
            feedback_fingerprints=["fp-1"],
            status="feedback_found",
        )
        (run_dir / "ci_feedback_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        snapshots = [ArtifactSnapshotEntry(name="ci_feedback_manifest.json", source_path="x", snapshot_path="y", exists=True)]
        return round_ref, manifest, snapshots

    monkeypatch.setattr("codepilot.post_pr.controller.run_feedback_round", fake_round)
    run_post_pr_automation(run_dir=run_dir, dry_run=True, execute=False, overwrite=True, post_pr_action_template=False)
    approval_request_path = run_dir / "post_pr" / "approval_request.json"
    payload = json.loads(approval_request_path.read_text(encoding="utf-8"))
    payload["head_sha"] = "tampered"
    approval_request_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    result = run_post_pr_automation(run_dir=run_dir, dry_run=False, execute=True, resume=True, overwrite=True, post_pr_action_template=False)

    assert result.status == "blocked"
    assert result.terminal_reason == "stale_approval"
    assert not (run_dir / "post_pr" / "approval_decision.json").exists()


def test_value_error_path_writes_report_and_manifest(tmp_path: Path, monkeypatch) -> None:
    run_dir = tmp_path / "runs" / "issue-001"
    run_dir.mkdir(parents=True)
    _write_auto_pr_manifest(run_dir)

    def fake_round(**kwargs):
        raise ValueError("bad manifest")

    monkeypatch.setattr("codepilot.post_pr.controller.run_feedback_round", fake_round)
    result = run_post_pr_automation(run_dir=run_dir, dry_run=False, execute=True, overwrite=True, post_pr_action_template=False)

    assert result.status == "blocked"
    assert result.terminal_reason == "manifest_invalid"
    assert result.report_path is not None and result.report_path.exists()
    assert result.manifest_path is not None and result.manifest_path.exists()


def test_invalid_auto_pr_manifest_writes_report_and_manifest(tmp_path: Path) -> None:
    run_dir = tmp_path / "runs" / "issue-001"
    run_dir.mkdir(parents=True)
    (run_dir / "auto_pr_manifest.json").write_text("[]", encoding="utf-8")

    result = run_post_pr_automation(run_dir=run_dir, overwrite=True, post_pr_action_template=False)

    assert result.status == "blocked"
    assert result.terminal_reason == "manifest_invalid"
    assert result.report_path is not None and result.report_path.exists()
    assert result.manifest_path is not None and result.manifest_path.exists()


def test_approval_file_outside_post_pr_is_rejected(tmp_path: Path, monkeypatch) -> None:
    run_dir = tmp_path / "runs" / "issue-001"
    run_dir.mkdir(parents=True)
    _write_auto_pr_manifest(run_dir)

    def fake_round(**kwargs):
        manifest = {
            "schema_version": "codepilot.ci_feedback_manifest.v1",
            "pr": {"url": "https://example.com", "head_branch": "codepilot/test", "head_sha": "abc"},
            "feedback_freshness": {"current_head_sha": "abc"},
            "safe_summary": {"feedback_items": [{"severity": "blocking", "kind": "ci_failure", "fingerprint": "fp-1"}]},
            "summary": {"checks_total": 0},
        }
        round_ref = PostPRRoundRef(
            round_id="round-001",
            round_index=1,
            round_dir=run_dir / "post_pr/round-001",
            latest_pr_feedback_manifest_path=run_dir / "ci_feedback_manifest.json",
            feedback_fingerprints=["fp-1"],
            status="feedback_found",
        )
        (run_dir / "ci_feedback_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        snapshots = [ArtifactSnapshotEntry(name="ci_feedback_manifest.json", source_path="x", snapshot_path="y", exists=True)]
        return round_ref, manifest, snapshots

    monkeypatch.setattr("codepilot.post_pr.controller.run_feedback_round", fake_round)
    run_post_pr_automation(run_dir=run_dir, dry_run=True, execute=False, overwrite=True, post_pr_action_template=False)
    approval_file = tmp_path / "approval_decision.json"
    approval_file.write_text("{}", encoding="utf-8")

    with pytest.raises(ValueError):
        run_post_pr_automation(
            run_dir=run_dir,
            dry_run=False,
            execute=True,
            approval_file=approval_file,
            resume=True,
            overwrite=True,
            post_pr_action_template=False,
        )


def test_execute_blocked_push_failure_remains_blocked(tmp_path: Path, monkeypatch) -> None:
    run_dir = tmp_path / "runs" / "issue-001"
    run_dir.mkdir(parents=True)
    _write_auto_pr_manifest(run_dir)

    def fake_round(**kwargs):
        phase = kwargs["phase"]
        manifest = {
            "schema_version": "codepilot.ci_feedback_manifest.v1",
            "pr": {"url": "https://example.com", "head_branch": "codepilot/test", "head_sha": "abc"},
            "feedback_freshness": {"current_head_sha": "abc"},
            "safe_summary": {"feedback_items": [{"severity": "blocking", "kind": "ci_failure", "fingerprint": "fp-1"}]},
            "summary": {"checks_total": 0},
        }
        round_ref = PostPRRoundRef(
            round_id="round-001",
            round_index=1,
            round_dir=run_dir / "post_pr/round-001",
            latest_pr_feedback_manifest_path=run_dir / "ci_feedback_manifest.json",
            feedback_fingerprints=["fp-1"],
            status="feedback_found" if phase == "collect" else "blocked",
            commit_created=phase == "execute",
            new_commit_sha="abc" if phase == "execute" else None,
            blockers=["push update failed"] if phase == "execute" else [],
        )
        (run_dir / "ci_feedback_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        snapshots = [ArtifactSnapshotEntry(name="ci_feedback_manifest.json", source_path="x", snapshot_path="y", exists=True)]
        return round_ref, manifest, snapshots

    monkeypatch.setattr("codepilot.post_pr.controller.run_feedback_round", fake_round)
    result = run_post_pr_automation(
        run_dir=run_dir,
        dry_run=False,
        execute=True,
        approve_run_agent=True,
        approve_push_update=True,
        overwrite=True,
        post_pr_action_template=False,
    )

    assert result.status == "blocked"
    assert result.terminal_reason == "push_failed"


def test_side_effects_records_failed_comment(tmp_path: Path, monkeypatch) -> None:
    run_dir = tmp_path / "runs" / "issue-001"
    run_dir.mkdir(parents=True)
    _write_auto_pr_manifest(run_dir)

    def fake_round(**kwargs):
        phase = kwargs["phase"]
        manifest = {
            "schema_version": "codepilot.ci_feedback_manifest.v1",
            "pr": {"url": "https://example.com", "head_branch": "codepilot/test", "head_sha": "abc"},
            "feedback_freshness": {"current_head_sha": "abc"},
            "safe_summary": {"feedback_items": [{"severity": "blocking", "kind": "ci_failure", "fingerprint": "fp-1"}]},
            "summary": {"checks_total": 0},
        }
        round_ref = PostPRRoundRef(
            round_id="round-001",
            round_index=1,
            round_dir=run_dir / "post_pr/round-001",
            latest_pr_feedback_manifest_path=run_dir / "ci_feedback_manifest.json",
            feedback_fingerprints=["fp-1"],
            status="feedback_found" if phase == "collect" else "patch_ready",
            commit_created=phase == "execute",
            new_commit_sha="abc" if phase == "execute" else None,
            push_update_executed=False,
            comment_posted=False,
        )
        (run_dir / "ci_feedback_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        snapshots = [ArtifactSnapshotEntry(name="ci_feedback_manifest.json", source_path="x", snapshot_path="y", exists=True)]
        return round_ref, manifest, snapshots

    monkeypatch.setattr("codepilot.post_pr.controller.run_feedback_round", fake_round)
    run_post_pr_automation(
        run_dir=run_dir,
        dry_run=False,
        execute=True,
        approve_run_agent=True,
        approve_push_update=True,
        approve_comment=True,
        overwrite=True,
        post_pr_action_template=False,
    )
    ledger = json.loads((run_dir / "post_pr" / "side_effects.json").read_text(encoding="utf-8"))
    actions = {(item["action"], item["status"]) for item in ledger["effects"]}
    assert ("post_comment", "failed") in actions


def test_side_effects_records_skipped_push_when_not_executed(tmp_path: Path, monkeypatch) -> None:
    run_dir = tmp_path / "runs" / "issue-001"
    run_dir.mkdir(parents=True)
    _write_auto_pr_manifest(run_dir)

    def fake_round(**kwargs):
        phase = kwargs["phase"]
        manifest = {
            "schema_version": "codepilot.ci_feedback_manifest.v1",
            "pr": {"url": "https://example.com", "head_branch": "codepilot/test", "head_sha": "abc"},
            "feedback_freshness": {"current_head_sha": "abc"},
            "safe_summary": {"feedback_items": [{"severity": "blocking", "kind": "ci_failure", "fingerprint": "fp-1"}]},
            "summary": {"checks_total": 0},
        }
        round_ref = PostPRRoundRef(
            round_id="round-001",
            round_index=1,
            round_dir=run_dir / "post_pr/round-001",
            latest_pr_feedback_manifest_path=run_dir / "ci_feedback_manifest.json",
            feedback_fingerprints=["fp-1"],
            status="feedback_found" if phase == "collect" else "patch_ready",
            commit_created=phase == "execute",
            new_commit_sha="abc" if phase == "execute" else None,
            push_update_executed=False,
            comment_posted=False,
        )
        (run_dir / "ci_feedback_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        snapshots = [ArtifactSnapshotEntry(name="ci_feedback_manifest.json", source_path="x", snapshot_path="y", exists=True)]
        return round_ref, manifest, snapshots

    monkeypatch.setattr("codepilot.post_pr.controller.run_feedback_round", fake_round)
    run_post_pr_automation(
        run_dir=run_dir,
        dry_run=False,
        execute=True,
        approve_run_agent=True,
        approve_push_update=True,
        overwrite=True,
        post_pr_action_template=False,
    )
    ledger = json.loads((run_dir / "post_pr" / "side_effects.json").read_text(encoding="utf-8"))
    actions = {(item["action"], item["status"]) for item in ledger["effects"]}
    assert ("push_update", "failed") in actions
