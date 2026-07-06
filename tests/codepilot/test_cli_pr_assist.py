from __future__ import annotations

import json
import subprocess
from pathlib import Path

from typer.testing import CliRunner

from codepilot.cli import app
from codepilot.repo.git_utils import sha256_file


runner = CliRunner()


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "tests").mkdir()
    (repo / "src" / "calc.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
    (repo / "tests" / "test_calc.py").write_text(
        "from src.calc import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "init"], cwd=repo, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    subprocess.run(["git", "config", "user.email", "demo@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Demo"], cwd=repo, check=True)
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return repo


def _write_run_dir(tmp_path: Path, *, repo: Path, safety_decision: str = "allow", status: str = "success") -> Path:
    run_dir = tmp_path / "runs" / "issue-test"
    run_dir.mkdir(parents=True)
    (run_dir / "issue.json").write_text(
        json.dumps({"title": "Fix add bug", "body": "body", "ref": {"source": "file", "file_path": "issue.md"}}),
        encoding="utf-8",
    )
    (run_dir / "report.md").write_text("# report\n", encoding="utf-8")
    (run_dir / "report.json").write_text(
        json.dumps({"run_id": "issue-test", "final_summary": "Implemented fix.", "changed_files": ["src/calc.py"], "tests": {"status": "passed"}}),
        encoding="utf-8",
    )
    (run_dir / "changes.patch").write_text("diff --git a/src/calc.py b/src/calc.py\n", encoding="utf-8")
    (run_dir / "pr_summary.md").write_text("# summary\n", encoding="utf-8")
    (run_dir / "restore_plan.md").write_text("# restore\n", encoding="utf-8")
    artifacts = []
    for name, filename in [
        ("issue_json", "issue.json"),
        ("report_md", "report.md"),
        ("report_json", "report.json"),
        ("patch", "changes.patch"),
        ("pr_summary", "pr_summary.md"),
        ("restore_plan", "restore_plan.md"),
        ("artifact_manifest", "artifact_manifest.json"),
    ]:
        path = run_dir / filename
        artifacts.append(
            {
                "name": name,
                "path": filename,
                "kind": name,
                "exists": path.exists(),
                "size_bytes": path.stat().st_size if path.exists() else None,
                "sha256": None if name == "artifact_manifest" else sha256_file(path),
            }
        )
    manifest = {
        "schema_version": "codepilot.artifact_manifest.v1",
        "run_id": "issue-test",
        "status": status,
        "success": safety_decision == "allow",
        "repo_path": str(repo),
        "effective_repo_path": str(repo),
        "used_worktree": False,
        "safety_decision": safety_decision,
        "safety_reason": "blocked" if safety_decision == "deny" else None,
        "safety_warnings": [],
        "safety_summary": {"baseline_dirty": False, "protected_after_files": []},
        "patch": {"changed_files": ["src/calc.py"], "is_empty": False, "sha256": sha256_file(run_dir / "changes.patch")},
        "artifacts": artifacts,
    }
    (run_dir / "artifact_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return run_dir


def test_cli_pr_assist_run_dir_success(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    run_dir = _write_run_dir(tmp_path, repo=repo)

    result = runner.invoke(app, ["pr-assist", "--run-dir", str(run_dir), "--overwrite"])

    assert result.exit_code == 0
    assert "PR body:" in result.stdout
    assert "Manual commands:" in result.stdout
    assert "Review checklist:" in result.stdout


def test_cli_pr_assist_run_id_success(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    _write_run_dir(tmp_path, repo=repo)

    result = runner.invoke(app, ["pr-assist", "--run-id", "issue-test", "--runs-dir", str(tmp_path / "runs"), "--overwrite"])

    assert result.exit_code == 0
    assert "Run ID: issue-test" in result.stdout


def test_cli_pr_assist_requires_exactly_one_locator(tmp_path: Path) -> None:
    missing = runner.invoke(app, ["pr-assist"])
    both = runner.invoke(app, ["pr-assist", "--run-dir", str(tmp_path), "--run-id", "issue-test"])

    assert missing.exit_code != 0
    assert both.exit_code != 0


def test_cli_pr_assist_missing_manifest_returns_non_zero(tmp_path: Path) -> None:
    run_dir = tmp_path / "runs" / "issue-test"
    run_dir.mkdir(parents=True)

    result = runner.invoke(app, ["pr-assist", "--run-dir", str(run_dir)])

    assert result.exit_code != 0
    assert "artifact_manifest.json" in result.stderr


def test_cli_pr_assist_safety_fail_returns_non_zero(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    run_dir = _write_run_dir(tmp_path, repo=repo, safety_decision="deny", status="repo_safety_denied")

    result = runner.invoke(app, ["pr-assist", "--run-dir", str(run_dir), "--overwrite"])

    assert result.exit_code == 1
    assert "blocked by safety gate" in result.stdout


def test_cli_pr_assist_no_strict_safety_returns_zero_for_review_only(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    run_dir = _write_run_dir(tmp_path, repo=repo, safety_decision="deny", status="repo_safety_denied")

    result = runner.invoke(app, ["pr-assist", "--run-dir", str(run_dir), "--no-strict-safety", "--overwrite"])

    assert result.exit_code == 0
    assert "Status: generated" in result.stdout
    assert "Push executed: no" in result.stdout
    assert "PR created: no" in result.stdout


def test_cli_pr_assist_include_gh_pr_command_is_comment_only(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    run_dir = _write_run_dir(tmp_path, repo=repo)

    result = runner.invoke(app, ["pr-assist", "--run-dir", str(run_dir), "--include-gh-pr-command", "--overwrite"])

    assert result.exit_code == 0
    assert "# gh pr create" in (run_dir / "manual_pr_commands.md").read_text(encoding="utf-8")


def test_cli_pr_assist_prepare_branch_does_not_push(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    run_dir = _write_run_dir(tmp_path, repo=repo)

    result = runner.invoke(app, ["pr-assist", "--run-dir", str(run_dir), "--prepare-branch", "--overwrite"])

    assert result.exit_code == 0
    assert "Local branch prepared: codepilot/issue-test" in result.stdout
    assert "Push executed: no" in result.stdout


def test_cli_pr_assist_commit_does_not_push(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    (repo / "src" / "calc.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    run_dir = _write_run_dir(tmp_path, repo=repo)

    result = runner.invoke(app, ["pr-assist", "--run-dir", str(run_dir), "--commit", "--overwrite"])

    assert result.exit_code == 0
    assert "Push executed: no" in result.stdout
    assert "PR created: no" in result.stdout


def test_cli_pr_assist_commit_warn_returns_non_zero(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    (repo / "src" / "calc.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    run_dir = _write_run_dir(tmp_path, repo=repo, safety_decision="warn", status="success")
    manifest = json.loads((run_dir / "artifact_manifest.json").read_text(encoding="utf-8"))
    manifest["safety_summary"]["baseline_dirty"] = True
    (run_dir / "artifact_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    result = runner.invoke(app, ["pr-assist", "--run-dir", str(run_dir), "--commit", "--overwrite"])

    assert result.exit_code == 1
    assert "local side-effect failure" in result.stdout
    assert "safety_gate=warn" in result.stderr


def test_cli_pr_assist_no_github_action_template(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    run_dir = _write_run_dir(tmp_path, repo=repo)

    result = runner.invoke(app, ["pr-assist", "--run-dir", str(run_dir), "--no-github-action-template", "--overwrite"])

    assert result.exit_code == 0
    assert not (run_dir / "github_action_template.yml").exists()


def test_cli_issue_overwrite_removes_old_pr_assist_artifacts(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    issue_file = tmp_path / "issue.md"
    issue_file.write_text("# Add bug\n\nPlease fix add().\n", encoding="utf-8")
    fixture = Path("tests/codepilot/fixtures/agent_actions_success.jsonl").resolve()
    run_dir = _write_run_dir(tmp_path, repo=repo)
    (run_dir / "pr_body.md").write_text("old\n", encoding="utf-8")
    (run_dir / "pr_assist_manifest.json").write_text("old\n", encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "issue",
            "--issue-file",
            str(issue_file),
            "--repo",
            str(repo),
            "--fake-actions",
            str(fixture),
            "--approve",
            "--runs-dir",
            str(tmp_path / "runs"),
            "--run-id",
            "issue-test",
            "--overwrite",
        ],
    )

    assert result.exit_code == 0
    assert not (run_dir / "pr_body.md").exists()
    assert not (run_dir / "pr_assist_manifest.json").exists()


def test_cli_pr_assist_manifest_invalid_message_is_clear(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    run_dir = _write_run_dir(tmp_path, repo=repo)
    manifest = json.loads((run_dir / "artifact_manifest.json").read_text(encoding="utf-8"))
    manifest.pop("schema_version")
    (run_dir / "artifact_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    result = runner.invoke(app, ["pr-assist", "--run-dir", str(run_dir), "--overwrite"])

    assert result.exit_code == 1
    assert "PR assist manifest invalid." in result.stdout
