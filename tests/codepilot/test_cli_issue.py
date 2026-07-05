from __future__ import annotations

import subprocess
from pathlib import Path

from typer.testing import CliRunner

from codepilot.cli import app


runner = CliRunner()


def _write_bug_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "tests").mkdir()
    (repo / "src" / "__init__.py").write_text("", encoding="utf-8")
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


def test_cli_issue_success_outputs_artifact_paths(tmp_path: Path) -> None:
    repo = _write_bug_repo(tmp_path)
    issue_file = tmp_path / "issue.md"
    issue_file.write_text("# Add bug\n\nPlease fix add().\n", encoding="utf-8")
    fixture = Path("tests/codepilot/fixtures/agent_actions_success.jsonl").resolve()

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
    assert "Issue workflow completed." in result.stdout
    assert "Run ID: issue-test" in result.stdout
    assert "Success: true" in result.stdout
    assert "PR summary:" in result.stdout
    assert "Manifest:" in result.stdout
    assert "Restore plan:" in result.stdout


def test_cli_issue_unsuccessful_run_still_prints_artifacts_and_exits_non_zero(tmp_path: Path) -> None:
    repo = _write_bug_repo(tmp_path)
    issue_file = tmp_path / "issue.md"
    issue_file.write_text("# Add bug\n\nPlease investigate.\n", encoding="utf-8")
    fake_actions = tmp_path / "partial.jsonl"
    fake_actions.write_text('{"type":"finish","status":"partial","summary":"not fixed"}\n', encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "issue",
            "--issue-file",
            str(issue_file),
            "--repo",
            str(repo),
            "--fake-actions",
            str(fake_actions),
            "--runs-dir",
            str(tmp_path / "runs"),
            "--run-id",
            "issue-test",
            "--overwrite",
        ],
    )

    assert result.exit_code == 1
    assert "Issue workflow completed." in result.stdout
    assert "Success: false" in result.stdout
    assert "Patch:" in result.stdout


def test_cli_issue_requires_repo_option(tmp_path: Path) -> None:
    issue_file = tmp_path / "issue.md"
    issue_file.write_text("# Add bug\n\nPlease fix.\n", encoding="utf-8")

    result = runner.invoke(app, ["issue", "--issue-file", str(issue_file)])

    assert result.exit_code != 0


def test_cli_issue_requires_issue_source(tmp_path: Path) -> None:
    repo = _write_bug_repo(tmp_path)

    result = runner.invoke(app, ["issue", "--repo", str(repo)])

    assert result.exit_code != 0
    assert "Provide exactly one of issue_file or issue_url." in result.stderr


def test_cli_issue_rejects_invalid_github_url(tmp_path: Path) -> None:
    repo = _write_bug_repo(tmp_path)

    result = runner.invoke(app, ["issue", "https://github.com/openai/codex/pull/1", "--repo", str(repo)])

    assert result.exit_code != 0
    assert "Invalid GitHub issue URL" in result.stderr


def test_cli_issue_read_only_still_generates_artifacts_and_exits_non_zero(tmp_path: Path) -> None:
    repo = _write_bug_repo(tmp_path)
    issue_file = tmp_path / "issue.md"
    issue_file.write_text("# Add bug\n\nPlease fix add().\n", encoding="utf-8")
    fixture = Path("tests/codepilot/fixtures/agent_actions_success.jsonl").resolve()

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
            "--policy-mode",
            "read_only",
            "--runs-dir",
            str(tmp_path / "runs"),
            "--run-id",
            "issue-test",
            "--overwrite",
        ],
    )

    assert result.exit_code == 1
    assert (tmp_path / "runs" / "issue-test" / "report.md").exists()
    assert (tmp_path / "runs" / "issue-test" / "pr_summary.md").exists()
    assert (tmp_path / "runs" / "issue-test" / "artifact_manifest.json").exists()
    assert (tmp_path / "runs" / "issue-test" / "restore_plan.md").exists()


def test_cli_issue_local_workflow_artifacts_do_not_contain_github_token(tmp_path: Path, monkeypatch) -> None:
    repo = _write_bug_repo(tmp_path)
    issue_file = tmp_path / "issue.md"
    issue_file.write_text("# Add bug\n\nPlease fix add().\n", encoding="utf-8")
    fixture = Path("tests/codepilot/fixtures/agent_actions_success.jsonl").resolve()
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_test_secret")

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
    run_dir = tmp_path / "runs" / "issue-test"
    for path in run_dir.glob("*"):
        if path.is_file():
            assert "ghp_test_secret" not in path.read_text(encoding="utf-8", errors="ignore")
