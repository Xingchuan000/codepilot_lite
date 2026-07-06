from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from codepilot.pr_feedback.github_client import FakePRFeedbackGitHubClient
from codepilot.pr_feedback.models import PRFeedbackResult, PRRef
from codepilot.pr_feedback.workflow import _comment_body, run_pr_feedback_loop
from codepilot.repo.git_utils import sha256_file


def _init_repo(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    subprocess.run(["git", "config", "user.email", "demo@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Demo"], cwd=repo, check=True)
    (repo / "src").mkdir()
    (repo / "src" / "calc.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    commit_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    ).stdout.strip()
    return repo, commit_sha


def _write_manifest_bundle(tmp_path: Path, *, manifest_head_sha: str, current_head_sha: str) -> Path:
    repo, _ = _init_repo(tmp_path)
    run_dir = tmp_path / "runs" / "issue-test"
    run_dir.mkdir(parents=True)
    for name, text in [
        ("issue.json", "{\"title\":\"Fix add bug\"}\n"),
        ("pr_summary.md", "# summary\n"),
        ("restore_plan.md", "# restore\n"),
        ("report.md", "# report\n"),
        ("changes.patch", "diff --git a/src/calc.py b/src/calc.py\n"),
    ]:
        (run_dir / name).write_text(text, encoding="utf-8")
    artifact_manifest = {
        "schema_version": "codepilot.artifact_manifest.v1",
        "run_id": "issue-test",
        "status": "success",
        "success": True,
        "repo_path": str(repo),
        "effective_repo_path": str(repo),
        "used_worktree": False,
        "safety_decision": "allow",
        "safety_reason": None,
        "safety_warnings": [],
        "safety_summary": {"baseline_dirty": False, "protected_after_files": []},
        "before": {},
        "after": {},
        "original_after": {},
        "patch": {
            "changed_files": ["src/calc.py"],
            "is_empty": False,
            "size_bytes": (run_dir / "changes.patch").stat().st_size,
            "sha256": sha256_file(run_dir / "changes.patch"),
            "protected_changed_files": [],
            "untracked_files": [],
            "untracked_files_omitted": [],
            "protected_after_files": [],
        },
        "cleanup": None,
        "artifacts": [],
    }
    for name, filename in [
        ("issue_json", "issue.json"),
        ("pr_summary", "pr_summary.md"),
        ("restore_plan", "restore_plan.md"),
        ("patch", "changes.patch"),
        ("report_md", "report.md"),
    ]:
        path = run_dir / filename
        artifact_manifest["artifacts"].append(
            {
                "name": name,
                "path": filename,
                "exists": True,
                "size_bytes": path.stat().st_size,
                "sha256": None if name == "artifact_manifest" else sha256_file(path),
            }
        )
    (run_dir / "artifact_manifest.json").write_text(json.dumps(artifact_manifest, indent=2), encoding="utf-8")
    artifact_manifest["artifacts"].append(
        {
            "name": "artifact_manifest",
            "path": "artifact_manifest.json",
            "exists": True,
            "size_bytes": None,
            "sha256": None,
        }
    )
    (run_dir / "artifact_manifest.json").write_text(json.dumps(artifact_manifest, indent=2), encoding="utf-8")
    pr_assist_manifest = {
        "schema_version": "codepilot.pr_assist_manifest.v1",
        "source_artifact_manifest": "artifact_manifest.json",
        "source_artifact_manifest_sha256": sha256_file(run_dir / "artifact_manifest.json"),
    }
    (run_dir / "pr_assist_manifest.json").write_text(json.dumps(pr_assist_manifest, indent=2), encoding="utf-8")
    auto_pr_manifest = {
        "schema_version": "codepilot.auto_pr_manifest.v1",
        "run_id": "issue-test",
        "status": "success",
        "side_effects": {"pr_created": True},
        "pr_request": {
            "repo": {"owner": "o", "repo": "r"},
            "base_branch": "main",
            "head_branch": "codepilot/test",
            "title": "Fix add bug",
        },
        "pr_result": {
            "url": "https://github.com/o/r/pull/1",
            "number": 1,
        },
        "branch_push_plan": {
            "remote_branch": "codepilot/test",
            "base_branch": "main",
            "commit_sha": manifest_head_sha,
        },
        "source_pr_assist_manifest": "pr_assist_manifest.json",
        "source_pr_assist_manifest_sha256": sha256_file(run_dir / "pr_assist_manifest.json"),
        "source_artifact_manifest": "artifact_manifest.json",
        "source_artifact_manifest_sha256": sha256_file(run_dir / "artifact_manifest.json"),
    }
    (run_dir / "auto_pr_manifest.json").write_text(json.dumps(auto_pr_manifest, indent=2), encoding="utf-8")
    subprocess.run(["git", "switch", "-c", "codepilot/test"], cwd=repo, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return run_dir


def test_workflow_missing_manifest_is_blocked_before_token_check(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    run_dir = tmp_path / "runs" / "issue-test"
    run_dir.mkdir(parents=True)

    result = run_pr_feedback_loop(run_dir=run_dir, dry_run=True, execute=False, overwrite=True)

    assert result.status == "blocked"
    assert result.github_api_called is False
    assert result.blockers
    assert "auto_pr_manifest" in "\n".join(result.blockers)
    assert (run_dir / "ci_feedback_manifest.json").exists()


def test_workflow_valid_manifest_without_token_keeps_pr_context(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    run_dir = _write_manifest_bundle(tmp_path, manifest_head_sha="abc123", current_head_sha="abc123")

    result = run_pr_feedback_loop(run_dir=run_dir, dry_run=True, execute=False, overwrite=True)

    assert result.status == "feedback_unavailable"
    assert result.pr is not None
    assert result.github_api_called is False
    assert (run_dir / "ci_feedback_manifest.json").exists()
    manifest = json.loads((run_dir / "ci_feedback_manifest.json").read_text(encoding="utf-8"))
    assert manifest["pr"]["head_branch"] == "codepilot/test"


def test_workflow_injected_fake_client_does_not_require_token(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    run_dir = _write_manifest_bundle(tmp_path, manifest_head_sha="abc123", current_head_sha="abc123")
    client = FakePRFeedbackGitHubClient(
        pull_request={"head": {"ref": "codepilot/test", "sha": "abc123"}, "updated_at": "2026-07-06T00:00:00Z"},
        check_runs=[
            {
                "name": "tests",
                "status": "completed",
                "conclusion": "failure",
                "html_url": "https://example.com/check",
                "head_sha": "abc123",
                "output": {"title": "failed", "summary": "line 1"},
            }
        ],
    )

    result = run_pr_feedback_loop(run_dir=run_dir, dry_run=True, execute=False, overwrite=True, github_client=client)

    assert result.status == "feedback_found"
    assert client.calls[0]["method"] == "get_pull_request"
    assert (run_dir / "ci_feedback_report.md").exists()


def test_execute_stale_head_writes_blocked_manifest_and_does_not_collect_feedback(tmp_path: Path) -> None:
    run_dir = _write_manifest_bundle(tmp_path, manifest_head_sha="old-sha", current_head_sha="new-sha")
    client = FakePRFeedbackGitHubClient(
        pull_request={"head": {"ref": "codepilot/test", "sha": "new-sha"}, "updated_at": "2026-07-06T00:00:00Z"},
        check_runs=[
            {
                "name": "tests",
                "status": "completed",
                "conclusion": "failure",
                "html_url": "https://example.com/check",
                "head_sha": "new-sha",
                "output": {"title": "failed", "summary": "line 1"},
            }
        ],
    )

    result = run_pr_feedback_loop(run_dir=run_dir, dry_run=False, execute=True, allow_run_agent=True, overwrite=True, github_client=client)

    assert result.status == "blocked"
    assert result.execute_blocked_by_stale_head is True
    assert [call["method"] for call in client.calls] == ["get_pull_request"]
    assert (run_dir / "ci_feedback_manifest.json").exists()


def test_dry_run_stale_head_generates_report_but_does_not_run_agent(tmp_path: Path) -> None:
    run_dir = _write_manifest_bundle(tmp_path, manifest_head_sha="old-sha", current_head_sha="new-sha")
    client = FakePRFeedbackGitHubClient(
        pull_request={"head": {"ref": "codepilot/test", "sha": "new-sha"}, "updated_at": "2026-07-06T00:00:00Z"},
        check_runs=[
            {
                "name": "tests",
                "status": "completed",
                "conclusion": "failure",
                "html_url": "https://example.com/check",
                "head_sha": "new-sha",
                "output": {"title": "failed", "summary": "line 1"},
            }
        ],
    )

    result = run_pr_feedback_loop(run_dir=run_dir, dry_run=True, execute=False, overwrite=True, github_client=client)

    assert result.feedback_freshness is not None
    assert result.feedback_freshness.is_stale is True
    assert result.agent_ran is False
    assert (run_dir / "ci_feedback_report.md").exists()


def test_update_plan_uses_input_flags_in_dry_run(tmp_path: Path) -> None:
    run_dir = _write_manifest_bundle(tmp_path, manifest_head_sha="abc123", current_head_sha="abc123")
    client = FakePRFeedbackGitHubClient(
        pull_request={"head": {"ref": "codepilot/test", "sha": "abc123"}, "updated_at": "2026-07-06T00:00:00Z"},
        check_runs=[
            {
                "name": "tests",
                "status": "completed",
                "conclusion": "failure",
                "html_url": "https://example.com/check",
                "head_sha": "abc123",
                "output": {"title": "failed", "summary": "line 1"},
            }
        ],
    )

    result = run_pr_feedback_loop(run_dir=run_dir, dry_run=True, execute=False, overwrite=True, github_client=client)

    text = (run_dir / "pr_update_plan.md").read_text(encoding="utf-8")
    assert "Mode: dry-run" in text
    assert "Dry run: yes" in text
    assert "allow_run_agent: no" in text
    assert result.dry_run is True


def test_allow_comment_uses_final_result_state_in_comment_body() -> None:
    body = _comment_body(
        PRFeedbackResult(
            run_id="run-1",
            run_dir=Path("runs/run-1"),
            status="branch_updated",
            agent_ran=True,
            patch_generated=True,
            commit_created=True,
            push_update_executed=True,
        )
    )

    assert "Status: branch_updated" in body
    assert "Agent ran: yes" in body
    assert "PR branch updated: yes" in body
