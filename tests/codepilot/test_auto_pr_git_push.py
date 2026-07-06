from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from codepilot.auto_pr.git_push import push_branch, verify_remote_branch_points_to_commit
from codepilot.auto_pr.git_remote import build_push_plan
from codepilot.auto_pr.models import AutoPRGitError


def _init_repo_with_remote(tmp_path: Path) -> tuple[Path, Path]:
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", str(remote)], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    subprocess.run(["git", "config", "user.email", "demo@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Demo"], cwd=repo, check=True)
    (repo / "a.txt").write_text("x\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    subprocess.run(["git", "remote", "add", "origin", str(remote)], cwd=repo, check=True)
    subprocess.run(["git", "switch", "-c", "codepilot/issue-test"], cwd=repo, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return repo, remote


def _plan(repo: Path, base_branch: str = "main"):
    sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True).stdout.strip()
    return build_push_plan(
        repo_path=repo,
        remote_name="origin",
        local_branch="codepilot/issue-test",
        remote_branch="codepilot/issue-test",
        base_branch=base_branch,
        commit_sha=sha,
    )


def test_push_branch_dry_run_does_not_push(tmp_path: Path) -> None:
    repo, _ = _init_repo_with_remote(tmp_path)

    assert push_branch(repo, push_plan=_plan(repo), execute=False, allow_push=True).will_push is False


def test_push_branch_allow_push_false_does_not_push(tmp_path: Path) -> None:
    repo, _ = _init_repo_with_remote(tmp_path)

    assert push_branch(repo, push_plan=_plan(repo), execute=True, allow_push=False).will_push is False


def test_push_branch_execute_and_allow_push_pushes(tmp_path: Path) -> None:
    repo, _ = _init_repo_with_remote(tmp_path)

    result = push_branch(repo, push_plan=_plan(repo), execute=True, allow_push=True)

    assert result.will_push is True
    assert result.remote_ref_verified is True


def test_push_branch_blocks_existing_remote_branch(tmp_path: Path) -> None:
    repo, _ = _init_repo_with_remote(tmp_path)
    push_branch(repo, push_plan=_plan(repo), execute=True, allow_push=True)

    with pytest.raises(AutoPRGitError):
        push_branch(repo, push_plan=_plan(repo), execute=True, allow_push=True)


@pytest.mark.parametrize(("base_branch",), [("main",), ("master",), ("codepilot/issue-test",)])
def test_push_branch_rejects_protected_or_base_branch(tmp_path: Path, base_branch: str) -> None:
    repo, _ = _init_repo_with_remote(tmp_path)
    with pytest.raises(Exception):
        if base_branch == "codepilot/issue-test":
            build_push_plan(
                repo_path=repo,
                remote_name="origin",
                local_branch="codepilot/issue-test",
                remote_branch="codepilot/issue-test",
                base_branch=base_branch,
                commit_sha=_plan(repo).commit_sha,
            )
        else:
            build_push_plan(
                repo_path=repo,
                remote_name="origin",
                local_branch="codepilot/issue-test",
                remote_branch=base_branch,
                base_branch="develop",
                commit_sha=_plan(repo).commit_sha,
            )


def test_push_branch_requires_matching_current_branch(tmp_path: Path) -> None:
    repo, _ = _init_repo_with_remote(tmp_path)
    subprocess.run(["git", "switch", "-c", "other"], cwd=repo, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    with pytest.raises(AutoPRGitError):
        push_branch(repo, push_plan=_plan(repo), execute=True, allow_push=True)


def test_push_branch_requires_matching_head_sha(tmp_path: Path) -> None:
    repo, _ = _init_repo_with_remote(tmp_path)
    plan = _plan(repo)
    (repo / "b.txt").write_text("y\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "next"], cwd=repo, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    with pytest.raises(AutoPRGitError):
        push_branch(repo, push_plan=plan, execute=True, allow_push=True)


def test_push_branch_requires_clean_worktree(tmp_path: Path) -> None:
    repo, _ = _init_repo_with_remote(tmp_path)
    (repo / "dirty.txt").write_text("z\n", encoding="utf-8")

    with pytest.raises(AutoPRGitError):
        push_branch(repo, push_plan=_plan(repo), execute=True, allow_push=True)


def test_verify_remote_branch_points_to_commit_mismatch_raises(tmp_path: Path) -> None:
    repo, _ = _init_repo_with_remote(tmp_path)
    push_branch(repo, push_plan=_plan(repo), execute=True, allow_push=True)

    with pytest.raises(AutoPRGitError):
        verify_remote_branch_points_to_commit(
            repo,
            remote_name="origin",
            remote_branch="codepilot/issue-test",
            expected_commit_sha="0" * 40,
        )
