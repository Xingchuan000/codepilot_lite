from __future__ import annotations

import json
from pathlib import Path

import pytest

from codepilot.post_pr.approval import (
    ApprovalDecision,
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


def _write_manifest(path: Path, payload: dict[str, object]) -> Path:
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def test_write_approval_request_and_synthesize(tmp_path: Path) -> None:
    auto_pr = _write_manifest(tmp_path / "auto_pr_manifest.json", {"schema_version": "codepilot.auto_pr_manifest.v1"})
    feedback = _write_manifest(
        tmp_path / "ci_feedback_manifest.json",
        {
            "schema_version": "codepilot.ci_feedback_manifest.v1",
            "pr": {"url": "https://example.com", "head_branch": "codepilot/test", "head_sha": "abc"},
            "feedback_freshness": {"current_head_sha": "abc"},
        },
    )
    request = build_approval_request(
        run_id="r",
        round_id="round-001",
        pr_feedback_manifest=json.loads(feedback.read_text(encoding="utf-8")),
        auto_pr_manifest_path=auto_pr,
        pr_feedback_manifest_path=feedback,
        requested_actions=["run_agent"],
        reason="reviewed",
    )
    md_path, json_path, updated = write_approval_request(request, output_md=tmp_path / "approval_request.md", output_json=tmp_path / "approval_request.json")
    assert md_path.exists() and json_path.exists() and updated.approval_request_sha256 is not None
    decision = synthesize_cli_approval_decision(request=updated, approve_run_agent=True, approve_push_update=False, approve_comment=False)
    assert decision is not None and is_action_approved(decision, "run_agent") is True


def test_write_approval_request_respects_overwrite_false(tmp_path: Path) -> None:
    auto_pr = _write_manifest(tmp_path / "auto_pr_manifest.json", {"schema_version": "codepilot.auto_pr_manifest.v1"})
    feedback = _write_manifest(
        tmp_path / "ci_feedback_manifest.json",
        {
            "schema_version": "codepilot.ci_feedback_manifest.v1",
            "pr": {"url": "https://example.com", "head_branch": "codepilot/test", "head_sha": "abc"},
            "feedback_freshness": {"current_head_sha": "abc"},
        },
    )
    request = build_approval_request(
        run_id="r",
        round_id="round-001",
        pr_feedback_manifest=json.loads(feedback.read_text(encoding="utf-8")),
        auto_pr_manifest_path=auto_pr,
        pr_feedback_manifest_path=feedback,
        requested_actions=["run_agent"],
        reason="reviewed",
    )
    write_approval_request(request, output_md=tmp_path / "approval_request.md", output_json=tmp_path / "approval_request.json", overwrite=True)
    with pytest.raises(FileExistsError):
        write_approval_request(request, output_md=tmp_path / "approval_request.md", output_json=tmp_path / "approval_request.json", overwrite=False)


def test_validate_approval_request_integrity_detects_tampering(tmp_path: Path) -> None:
    auto_pr = _write_manifest(tmp_path / "auto_pr_manifest.json", {"schema_version": "codepilot.auto_pr_manifest.v1"})
    feedback = _write_manifest(
        tmp_path / "ci_feedback_manifest.json",
        {
            "schema_version": "codepilot.ci_feedback_manifest.v1",
            "pr": {"url": "https://example.com", "head_branch": "codepilot/test", "head_sha": "abc"},
            "feedback_freshness": {"current_head_sha": "abc"},
        },
    )
    request = build_approval_request(
        run_id="r",
        round_id="round-001",
        pr_feedback_manifest=json.loads(feedback.read_text(encoding="utf-8")),
        auto_pr_manifest_path=auto_pr,
        pr_feedback_manifest_path=feedback,
        requested_actions=["run_agent", "post_comment"],
        reason="reviewed",
    )
    _, json_path, updated = write_approval_request(request, output_md=tmp_path / "approval_request.md", output_json=tmp_path / "approval_request.json")
    assert validate_approval_request_integrity(updated) == []
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    payload["requested_actions"] = ["run_agent"]
    json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tampered = load_approval_request(json_path)
    assert validate_approval_request_integrity(tampered) == [
        "stale_approval: approval_request scope hash mismatch"
    ]


def test_validate_approval_decision_rejects_stale_hashes(tmp_path: Path) -> None:
    auto_pr = _write_manifest(tmp_path / "auto.json", {"schema_version": "codepilot.auto_pr_manifest.v1"})
    feedback = _write_manifest(
        tmp_path / "feedback.json",
        {"schema_version": "codepilot.ci_feedback_manifest.v1", "pr": {"head_sha": "abc"}, "feedback_freshness": {"current_head_sha": "abc"}},
    )
    request = build_approval_request(
        run_id="r",
        round_id="round-001",
        pr_feedback_manifest={"pr": {"head_sha": "abc"}, "feedback_freshness": {"current_head_sha": "abc"}},
        auto_pr_manifest_path=auto_pr,
        pr_feedback_manifest_path=feedback,
        requested_actions=["run_agent"],
        reason="reviewed",
    )
    _, _, updated = write_approval_request(request, output_md=tmp_path / "approval_request.md", output_json=tmp_path / "approval_request.json")
    decision = synthesize_cli_approval_decision(request=updated, approve_run_agent=True, approve_push_update=False, approve_comment=False)
    assert decision is not None
    assert validate_approval_decision(decision, request=updated) == []
    assert approval_terminal_reason(["approval_expired"]) == "approval_expired"


def test_validate_approval_decision_requires_hashes_for_approved(tmp_path: Path) -> None:
    request = build_approval_request(
        run_id="r",
        round_id="round-001",
        pr_feedback_manifest={"pr": {"head_sha": "abc"}, "feedback_freshness": {"current_head_sha": "abc"}},
        auto_pr_manifest_path=tmp_path / "auto.json",
        pr_feedback_manifest_path=tmp_path / "feedback.json",
        requested_actions=["run_agent"],
        reason="reviewed",
    )
    decision = ApprovalDecision(run_id="r", round_id="round-001", status="approved", approved_actions=["run_agent"])
    errors = validate_approval_decision(decision, request=request)
    assert "missing head_sha" in errors
    assert "missing auto_pr_manifest_sha256" in errors
    assert "missing pr_feedback_manifest_sha256" in errors
    assert "missing approval_request_sha256" in errors


def test_write_and_load_decision(tmp_path: Path) -> None:
    request = build_approval_request(
        run_id="r",
        round_id="round-001",
        pr_feedback_manifest={"pr": {"head_sha": "abc"}, "feedback_freshness": {"current_head_sha": "abc"}},
        auto_pr_manifest_path=tmp_path / "auto.json",
        pr_feedback_manifest_path=tmp_path / "feedback.json",
        requested_actions=["run_agent"],
        reason="reviewed",
    )
    decision = synthesize_cli_approval_decision(request=request, approve_run_agent=True, approve_push_update=False, approve_comment=False)
    assert decision is not None
    path = write_approval_decision(decision, tmp_path / "approval_decision.json")
    assert load_approval_decision(path).status == "approved"


@pytest.mark.parametrize(
    ("approval_file_path",),
    [
        (Path("approval_decision.txt"),),
        (Path("approval_decision.json"),),
    ],
)
def test_approval_file_validation(tmp_path: Path, approval_file_path: Path, monkeypatch) -> None:
    from codepilot.post_pr.controller import run_post_pr_automation

    run_dir = tmp_path / "runs" / "issue-001"
    run_dir.mkdir(parents=True)
    _write_manifest(run_dir / "auto_pr_manifest.json", {"schema_version": "codepilot.auto_pr_manifest.v1", "run_id": "issue-001"})
    approval_path = tmp_path / approval_file_path
    if approval_path.suffix == ".txt":
        approval_path.write_text("{}", encoding="utf-8")
    else:
        approval_path.mkdir()

    with pytest.raises(ValueError):
        run_post_pr_automation(
            run_dir=run_dir,
            dry_run=False,
            execute=True,
            approval_file=approval_path,
            resume=True,
            overwrite=True,
            post_pr_action_template=False,
        )
