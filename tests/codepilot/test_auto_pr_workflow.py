from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from codepilot.auto_pr.github_client import FakeGitHubClient
from codepilot.auto_pr.models import AutoPRGitHubError, AutoPRWorkflowInputError
from codepilot.auto_pr.workflow import run_auto_pr
from codepilot.repo.git_utils import sha256_file


def _init_repo(tmp_path: Path, *, with_remote: bool = True) -> tuple[Path, Path | None]:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    subprocess.run(["git", "config", "user.email", "demo@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Demo"], cwd=repo, check=True)
    (repo / "src").mkdir()
    (repo / "src" / "calc.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    remote = None
    if with_remote:
        remote = tmp_path / "remote.git"
        subprocess.run(["git", "init", "--bare", str(remote)], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        subprocess.run(["git", "remote", "add", "origin", str(remote)], cwd=repo, check=True)
    return repo, remote


def _write_run_dir(
    tmp_path: Path,
    *,
    repo: Path,
    redact_repo_path: bool = False,
    safety_status: str = "pass",
    source_decision: str = "allow",
    patch_empty: bool = False,
) -> Path:
    run_dir = tmp_path / "runs" / "issue-test"
    run_dir.mkdir(parents=True)
    (run_dir / "issue.json").write_text(
        json.dumps(
            {
                "title": "Fix add bug",
                "body": "body",
                "number": 123,
                "ref": {"source": "github", "url": "https://github.com/o/r/issues/123", "number": 123},
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "pr_body.md").write_text("# Fix add bug\n\nsummary\n", encoding="utf-8")
    (run_dir / "manual_pr_commands.md").write_text("# manual\n", encoding="utf-8")
    (run_dir / "review_checklist.md").write_text("# checklist\n", encoding="utf-8")
    (run_dir / "changes.patch").write_text("" if patch_empty else "diff --git a/src/calc.py b/src/calc.py\n", encoding="utf-8")
    (run_dir / "pr_summary.md").write_text("# summary\n", encoding="utf-8")
    (run_dir / "restore_plan.md").write_text("# restore\n", encoding="utf-8")
    repo_path = "[REDACTED_PATH]" if redact_repo_path else str(repo)
    source_manifest = {
        "schema_version": "codepilot.artifact_manifest.v1",
        "run_id": "issue-test",
        "status": "success" if source_decision == "allow" else "repo_safety_denied",
        "success": True,
        "repo_path": repo_path,
        "effective_repo_path": repo_path,
        "used_worktree": False,
        "safety_decision": source_decision,
        "safety_summary": {"baseline_dirty": False, "protected_after_files": []},
        "before": {"branch": "main"},
        "patch": {
            "changed_files": ["src/calc.py"],
            "is_empty": patch_empty,
            "sha256": sha256_file(run_dir / "changes.patch"),
            "protected_changed_files": [],
            "protected_after_files": [],
        },
        "artifacts": [],
    }
    for name, filename in [
        ("issue_json", "issue.json"),
        ("patch", "changes.patch"),
        ("pr_summary", "pr_summary.md"),
        ("restore_plan", "restore_plan.md"),
        ("artifact_manifest", "artifact_manifest.json"),
    ]:
        path = run_dir / filename
        source_manifest["artifacts"].append(
            {
                "name": name,
                "path": filename,
                "exists": True,
                "size_bytes": path.stat().st_size if path.exists() else None,
                "sha256": None if name == "artifact_manifest" else sha256_file(path),
            }
        )
    (run_dir / "artifact_manifest.json").write_text(json.dumps(source_manifest, indent=2), encoding="utf-8")
    pr_assist_manifest = {
        "schema_version": "codepilot.pr_assist_manifest.v1",
        "run_id": "issue-test",
        "source_artifact_manifest": "artifact_manifest.json",
        "source_artifact_manifest_sha256": sha256_file(run_dir / "artifact_manifest.json"),
        "safety_gate": {"status": safety_status, "reasons": [], "warnings": []},
        "generated_artifacts": [
            {
                "name": "pr_body",
                "path": "pr_body.md",
                "exists": True,
                "size_bytes": (run_dir / "pr_body.md").stat().st_size,
                "sha256": sha256_file(run_dir / "pr_body.md"),
            },
            {
                "name": "manual_pr_commands",
                "path": "manual_pr_commands.md",
                "exists": True,
                "size_bytes": (run_dir / "manual_pr_commands.md").stat().st_size,
                "sha256": sha256_file(run_dir / "manual_pr_commands.md"),
            },
            {
                "name": "review_checklist",
                "path": "review_checklist.md",
                "exists": True,
                "size_bytes": (run_dir / "review_checklist.md").stat().st_size,
                "sha256": sha256_file(run_dir / "review_checklist.md"),
            },
            {
                "name": "pr_assist_manifest",
                "path": "pr_assist_manifest.json",
                "exists": True,
                "size_bytes": None,
                "sha256": None,
            },
        ],
        "side_effects": {
            "branch_prepared": True,
            "commit_prepared": True,
            "push_executed": False,
            "pr_created": False,
            "github_api_called": False,
        },
        "branch_name": "codepilot/issue-test",
        "commit_sha": subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        ).stdout.strip(),
        "warnings": [],
    }
    (run_dir / "pr_assist_manifest.json").write_text(json.dumps(pr_assist_manifest, indent=2), encoding="utf-8")
    subprocess.run(["git", "switch", "-c", "codepilot/issue-test"], cwd=repo, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return run_dir


def test_run_auto_pr_dry_run_generates_plan_and_manifest(tmp_path: Path) -> None:
    repo, _ = _init_repo(tmp_path)
    run_dir = _write_run_dir(tmp_path, repo=repo)

    result = run_auto_pr(run_dir=run_dir, dry_run=True, overwrite=True, repo_slug="o/r")

    assert result.status == "planned"
    assert (run_dir / "auto_pr_plan.md").exists()
    assert (run_dir / "auto_pr_manifest.json").exists()


def test_run_auto_pr_invalid_head_branch_fails_before_writing_plan(tmp_path: Path) -> None:
    repo, _ = _init_repo(tmp_path)
    run_dir = _write_run_dir(tmp_path, repo=repo)

    with pytest.raises(AutoPRWorkflowInputError):
        run_auto_pr(
            run_dir=run_dir,
            dry_run=True,
            overwrite=True,
            repo_slug="o/r",
            head_branch="codepilot/bad branch",
        )

    assert not (run_dir / "auto_pr_plan.md").exists()
    assert not (run_dir / "auto_pr_manifest.json").exists()


def test_run_auto_pr_dry_run_manifest_side_effects_are_false(tmp_path: Path) -> None:
    repo, _ = _init_repo(tmp_path)
    run_dir = _write_run_dir(tmp_path, repo=repo)

    run_auto_pr(run_dir=run_dir, dry_run=True, overwrite=True, repo_slug="o/r")
    payload = json.loads((run_dir / "auto_pr_manifest.json").read_text(encoding="utf-8"))

    assert payload["side_effects"]["push_executed"] is False
    assert payload["side_effects"]["pr_created"] is False
    assert payload["side_effects"]["github_api_called"] is False
    assert payload["side_effects"]["comment_posted"] is False


def test_run_auto_pr_dry_run_does_not_call_fake_client(tmp_path: Path) -> None:
    repo, _ = _init_repo(tmp_path)
    run_dir = _write_run_dir(tmp_path, repo=repo)
    client = FakeGitHubClient()

    run_auto_pr(run_dir=run_dir, dry_run=True, overwrite=True, repo_slug="o/r", github_client=client)

    assert client.created_requests == []


def test_run_auto_pr_safety_fail_dry_run_generates_blocked_plan(tmp_path: Path) -> None:
    repo, _ = _init_repo(tmp_path)
    run_dir = _write_run_dir(tmp_path, repo=repo, safety_status="fail")

    result = run_auto_pr(run_dir=run_dir, dry_run=True, overwrite=True, repo_slug="o/r")

    assert result.status == "planned_with_blockers"


def test_run_auto_pr_manifest_invalid_writes_minimal_manifest(tmp_path: Path) -> None:
    repo, _ = _init_repo(tmp_path)
    run_dir = _write_run_dir(tmp_path, repo=repo)
    payload = json.loads((run_dir / "pr_assist_manifest.json").read_text(encoding="utf-8"))
    payload["schema_version"] = "bad"
    (run_dir / "pr_assist_manifest.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")

    result = run_auto_pr(run_dir=run_dir, dry_run=True, overwrite=True, repo_slug="o/r")

    assert result.status == "manifest_invalid"
    assert (run_dir / "auto_pr_plan.md").exists()
    assert (run_dir / "auto_pr_manifest.json").exists()


def test_run_auto_pr_overwrite_protects_existing_files(tmp_path: Path) -> None:
    repo, _ = _init_repo(tmp_path)
    run_dir = _write_run_dir(tmp_path, repo=repo)
    (run_dir / "auto_pr_plan.md").write_text("old\n", encoding="utf-8")

    with pytest.raises(FileExistsError):
        run_auto_pr(run_dir=run_dir, dry_run=True, overwrite=False, repo_slug="o/r")


def test_run_auto_pr_overwrite_only_replaces_step_13_artifacts(tmp_path: Path) -> None:
    repo, _ = _init_repo(tmp_path)
    run_dir = _write_run_dir(tmp_path, repo=repo)

    run_auto_pr(run_dir=run_dir, dry_run=True, overwrite=True, repo_slug="o/r")

    assert (run_dir / "pr_body.md").exists()


def test_run_auto_pr_dry_run_generates_workflow_template(tmp_path: Path) -> None:
    repo, _ = _init_repo(tmp_path)
    run_dir = _write_run_dir(tmp_path, repo=repo)

    run_auto_pr(run_dir=run_dir, dry_run=True, overwrite=True, repo_slug="o/r")

    assert (run_dir / "controlled_auto_pr_workflow.yml").exists()


def test_run_auto_pr_execute_safety_fail_returns_blocked(tmp_path: Path) -> None:
    repo, _ = _init_repo(tmp_path)
    run_dir = _write_run_dir(tmp_path, repo=repo, safety_status="fail")

    assert run_auto_pr(run_dir=run_dir, execute=True, allow_push=True, overwrite=True, repo_slug="o/r").status == "blocked_by_safety"


def test_run_auto_pr_execute_without_allow_push_fails_closed(tmp_path: Path) -> None:
    repo, _ = _init_repo(tmp_path)
    run_dir = _write_run_dir(tmp_path, repo=repo)

    assert run_auto_pr(run_dir=run_dir, execute=True, allow_push=False, overwrite=True, repo_slug="o/r").status == "failed"


def test_run_auto_pr_execute_pushes_to_local_bare_remote(tmp_path: Path) -> None:
    repo, _ = _init_repo(tmp_path)
    run_dir = _write_run_dir(tmp_path, repo=repo)

    result = run_auto_pr(run_dir=run_dir, execute=True, allow_push=True, overwrite=True, repo_slug="o/r")

    assert result.push_executed is True


def test_run_auto_pr_execute_with_fake_client_creates_pr(tmp_path: Path) -> None:
    repo, _ = _init_repo(tmp_path)
    run_dir = _write_run_dir(tmp_path, repo=repo)

    result = run_auto_pr(
        run_dir=run_dir,
        execute=True,
        allow_push=True,
        allow_create_pr=True,
        overwrite=True,
        repo_slug="o/r",
        github_client=FakeGitHubClient(),
    )

    assert result.pr_created is True
    assert result.github_api_called is True


def test_run_auto_pr_fake_client_create_failure_marks_failed(tmp_path: Path) -> None:
    repo, _ = _init_repo(tmp_path)
    run_dir = _write_run_dir(tmp_path, repo=repo)

    result = run_auto_pr(
        run_dir=run_dir,
        execute=True,
        allow_push=True,
        allow_create_pr=True,
        overwrite=True,
        repo_slug="o/r",
        github_client=FakeGitHubClient(fail_create=True),
    )

    assert result.push_executed is True
    assert result.pr_created is False


def test_run_auto_pr_allow_comment_false_does_not_comment(tmp_path: Path) -> None:
    repo, _ = _init_repo(tmp_path)
    run_dir = _write_run_dir(tmp_path, repo=repo)
    client = FakeGitHubClient()

    run_auto_pr(
        run_dir=run_dir,
        execute=True,
        allow_push=True,
        allow_create_pr=True,
        allow_comment=False,
        overwrite=True,
        repo_slug="o/r",
        github_client=client,
    )

    assert client.comments == []


def test_run_auto_pr_allow_comment_true_posts_comment(tmp_path: Path) -> None:
    repo, _ = _init_repo(tmp_path)
    run_dir = _write_run_dir(tmp_path, repo=repo)
    client = FakeGitHubClient()

    result = run_auto_pr(
        run_dir=run_dir,
        execute=True,
        allow_push=True,
        allow_create_pr=True,
        allow_comment=True,
        overwrite=True,
        repo_slug="o/r",
        github_client=client,
    )

    assert result.comment_posted is True


def test_run_auto_pr_comment_failure_only_adds_warning(tmp_path: Path) -> None:
    repo, _ = _init_repo(tmp_path)
    run_dir = _write_run_dir(tmp_path, repo=repo)

    result = run_auto_pr(
        run_dir=run_dir,
        execute=True,
        allow_push=True,
        allow_create_pr=True,
        allow_comment=True,
        overwrite=True,
        repo_slug="o/r",
        github_client=FakeGitHubClient(fail_comment=True),
    )

    assert result.pr_created is True
    assert result.comment_posted is False
    assert result.warnings


def test_run_auto_pr_redacted_repo_path_blocks_execute(tmp_path: Path) -> None:
    repo, _ = _init_repo(tmp_path)
    run_dir = _write_run_dir(tmp_path, repo=repo, redact_repo_path=True)

    assert run_auto_pr(run_dir=run_dir, execute=True, allow_push=True, overwrite=True, repo_slug="o/r").status == "failed"


def test_run_auto_pr_manifest_does_not_contain_token_like_text(tmp_path: Path) -> None:
    repo, _ = _init_repo(tmp_path)
    run_dir = _write_run_dir(tmp_path, repo=repo)

    run_auto_pr(run_dir=run_dir, dry_run=True, overwrite=True, repo_slug="o/r")

    assert "GITHUB_TOKEN" not in (run_dir / "auto_pr_manifest.json").read_text(encoding="utf-8")


def test_run_auto_pr_missing_token_blocks_before_push_and_writes_manifest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo, remote = _init_repo(tmp_path)
    run_dir = _write_run_dir(tmp_path, repo=repo)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)

    result = run_auto_pr(
        run_dir=run_dir,
        execute=True,
        allow_push=True,
        allow_create_pr=True,
        repo_slug="o/r",
        overwrite=True,
    )

    assert result.status == "failed"
    assert result.push_executed is False
    assert result.pr_created is False
    assert result.github_api_called is False
    assert (run_dir / "auto_pr_plan.md").exists()
    assert (run_dir / "auto_pr_manifest.json").exists()
    assert "GITHUB_TOKEN" not in (run_dir / "auto_pr_manifest.json").read_text(encoding="utf-8")
    remote_branches = subprocess.run(
        ["git", "ls-remote", "--heads", str(remote), "codepilot/issue-test"],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    ).stdout.strip()
    assert remote_branches == ""


def test_run_auto_pr_preserves_push_executed_when_rest_client_raises_after_push(tmp_path: Path) -> None:
    class RaisingGitHubClient:
        def create_pull_request(self, request):
            raise AutoPRGitHubError("api down")

        def post_issue_comment(self, repo, issue_number, body):
            return {"posted": False}

    repo, remote = _init_repo(tmp_path)
    run_dir = _write_run_dir(tmp_path, repo=repo)

    result = run_auto_pr(
        run_dir=run_dir,
        execute=True,
        allow_push=True,
        allow_create_pr=True,
        github_client=RaisingGitHubClient(),
        repo_slug="o/r",
        overwrite=True,
    )

    assert result.status == "failed"
    assert result.push_executed is True
    assert result.pr_created is False
    remote_branches = subprocess.run(
        ["git", "ls-remote", "--heads", str(remote), "codepilot/issue-test"],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    ).stdout.strip()
    assert remote_branches
    payload = json.loads((run_dir / "auto_pr_manifest.json").read_text(encoding="utf-8"))
    assert payload["side_effects"]["push_executed"] is True


def test_run_auto_pr_missing_source_artifact_manifest_writes_manifest_invalid_artifacts(tmp_path: Path) -> None:
    repo, _ = _init_repo(tmp_path)
    run_dir = _write_run_dir(tmp_path, repo=repo)
    (run_dir / "artifact_manifest.json").unlink()

    result = run_auto_pr(run_dir=run_dir, dry_run=True, overwrite=True, repo_slug="o/r")

    assert result.status == "manifest_invalid"
    assert (run_dir / "auto_pr_plan.md").exists()
    assert (run_dir / "auto_pr_manifest.json").exists()
    payload = json.loads((run_dir / "auto_pr_manifest.json").read_text(encoding="utf-8"))
    assert payload["side_effects"]["push_executed"] is False
    assert payload["side_effects"]["github_api_called"] is False


def test_run_auto_pr_source_artifact_hash_mismatch_writes_manifest_invalid_artifacts(tmp_path: Path) -> None:
    repo, _ = _init_repo(tmp_path)
    run_dir = _write_run_dir(tmp_path, repo=repo)
    payload = json.loads((run_dir / "pr_assist_manifest.json").read_text(encoding="utf-8"))
    payload["source_artifact_manifest_sha256"] = "bad"
    (run_dir / "pr_assist_manifest.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")

    result = run_auto_pr(run_dir=run_dir, dry_run=True, overwrite=True, repo_slug="o/r")

    assert result.status == "manifest_invalid"
    assert (run_dir / "auto_pr_plan.md").exists()
    assert (run_dir / "auto_pr_manifest.json").exists()
    manifest_payload = json.loads((run_dir / "auto_pr_manifest.json").read_text(encoding="utf-8"))
    assert manifest_payload["side_effects"]["push_executed"] is False
    assert manifest_payload["side_effects"]["github_api_called"] is False
