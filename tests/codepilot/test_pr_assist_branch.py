from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from codepilot.pr_assist.branch import prepare_local_branch, sanitize_branch_name
from codepilot.pr_assist.models import BranchPrepError
from codepilot.repo.git_utils import run_git


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "demo.txt").write_text("demo\n", encoding="utf-8")
    subprocess.run(["git", "init"], cwd=repo, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    subprocess.run(["git", "config", "user.email", "demo@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Demo"], cwd=repo, check=True)
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return repo


def test_sanitize_branch_name() -> None:
    assert sanitize_branch_name("issue test/with spaces", prefix="code pilot").startswith("code-pilot/")


def test_prepare_local_branch_creates_branch(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)

    branch_name = prepare_local_branch(repo, branch_name="codepilot/issue-test")

    assert branch_name == "codepilot/issue-test"
    assert run_git(repo, ["branch", "--show-current"]) == "codepilot/issue-test"
    assert run_git(repo, ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"], check=False) == ""


def test_prepare_local_branch_rejects_existing_branch(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    prepare_local_branch(repo, branch_name="codepilot/issue-test")

    with pytest.raises(BranchPrepError):
        prepare_local_branch(repo, branch_name="codepilot/issue-test")


def test_prepare_local_branch_rejects_dirty_repo(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    (repo / "demo.txt").write_text("dirty\n", encoding="utf-8")

    with pytest.raises(BranchPrepError):
        prepare_local_branch(repo, branch_name="codepilot/issue-test")
