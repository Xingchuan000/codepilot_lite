from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from codepilot.pr_assist.commit import prepare_commit, render_commit_message
from codepilot.pr_assist.models import CommitPrepError
from codepilot.repo.git_utils import run_git


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "calc.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
    subprocess.run(["git", "init"], cwd=repo, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    subprocess.run(["git", "config", "user.email", "demo@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Demo"], cwd=repo, check=True)
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return repo


def test_render_commit_message_includes_run_id_and_redacts_tokens() -> None:
    message = render_commit_message(
        issue_title="Fix bug",
        changed_files=["src/calc.py"],
        tests_summary="GITHUB_TOKEN=secret",
        run_id="issue-test",
    )

    assert "Fix: Fix bug" in message
    assert "run issue-test" in message
    assert "[REDACTED]" in message


def test_prepare_commit_only_stages_changed_files_and_returns_sha(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    (repo / "src" / "calc.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    (repo / "README.md").write_text("untracked\n", encoding="utf-8")

    sha = prepare_commit(
        repo,
        message="Fix: add bug",
        changed_files=["src/calc.py"],
        run_id="issue-test",
    )

    assert sha == run_git(repo, ["rev-parse", "HEAD"])
    assert "src/calc.py" in run_git(repo, ["show", "--name-only", "--format=", "HEAD"]).splitlines()
    assert "README.md" not in run_git(repo, ["show", "--name-only", "--format=", "HEAD"]).splitlines()


def test_prepare_commit_rejects_empty_changed_files(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)

    with pytest.raises(CommitPrepError):
        prepare_commit(repo, message="Fix: none", changed_files=[], run_id="issue-test")


def test_prepare_commit_rejects_protected_path(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    (repo / ".env").write_text("SECRET=1\n", encoding="utf-8")

    with pytest.raises(CommitPrepError):
        prepare_commit(repo, message="Fix: env", changed_files=[".env"], run_id="issue-test")


def test_prepare_commit_rejects_runs_artifacts(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    (repo / "runs").mkdir()
    (repo / "runs" / "issue-test.txt").write_text("artifact\n", encoding="utf-8")

    with pytest.raises(CommitPrepError):
        prepare_commit(repo, message="Fix: bad", changed_files=["runs/issue-test.txt"], run_id="issue-test")
