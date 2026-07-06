from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from codepilot.auto_pr.git_remote import (
    assert_branch_name_safe,
    build_push_plan,
    parse_github_remote_url,
    resolve_repo_ref,
    sanitize_remote_branch_name,
)
from codepilot.auto_pr.models import AutoPRRemoteError


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    subprocess.run(["git", "config", "user.email", "demo@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Demo"], cwd=repo, check=True)
    (repo / "a.txt").write_text("x\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return repo


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("https://github.com/owner/repo.git", ("owner", "repo")),
        ("https://github.com/owner/repo", ("owner", "repo")),
        ("git@github.com:owner/repo.git", ("owner", "repo")),
        ("ssh://git@github.com/owner/repo.git", ("owner", "repo")),
    ],
)
def test_parse_github_remote_url(value: str, expected: tuple[str, str]) -> None:
    parsed = parse_github_remote_url(value)

    assert (parsed.owner, parsed.repo) == expected


def test_parse_github_remote_url_rejects_non_github() -> None:
    with pytest.raises(AutoPRRemoteError):
        parse_github_remote_url("https://gitlab.com/owner/repo.git")


def test_resolve_repo_ref_prefers_repo_slug(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    subprocess.run(["git", "remote", "add", "origin", "https://github.com/x/y.git"], cwd=repo, check=True)

    value = resolve_repo_ref(repo, repo_slug="owner/repo")

    assert (value.owner, value.repo) == ("owner", "repo")


def test_resolve_repo_ref_rejects_invalid_repo_slug(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    subprocess.run(["git", "remote", "add", "origin", "https://github.com/x/y.git"], cwd=repo, check=True)

    with pytest.raises(Exception):
        resolve_repo_ref(repo, repo_slug="owner/repo/extra")


def test_sanitize_remote_branch_name_rewrites_run_id() -> None:
    assert sanitize_remote_branch_name("issue test") == "codepilot/issue-test"


@pytest.mark.parametrize(("branch",), [("main",), ("master",)])
def test_assert_branch_name_safe_rejects_protected_branch(branch: str) -> None:
    with pytest.raises(AutoPRRemoteError):
        assert_branch_name_safe(remote_branch=branch, base_branch="develop")


def test_assert_branch_name_safe_rejects_base_branch() -> None:
    with pytest.raises(AutoPRRemoteError):
        assert_branch_name_safe(remote_branch="codepilot/x", base_branch="codepilot/x")


def test_assert_branch_name_safe_rejects_non_codepilot_branch() -> None:
    with pytest.raises(AutoPRRemoteError):
        assert_branch_name_safe(remote_branch="feature/x", base_branch="main")


def test_build_push_plan_generates_controlled_refspec(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)

    plan = build_push_plan(
        repo_path=repo,
        remote_name="origin",
        local_branch="codepilot/issue-test",
        remote_branch="codepilot/issue-test",
        base_branch="main",
        commit_sha="abc123",
    )

    assert plan.push_refspec == "HEAD:refs/heads/codepilot/issue-test"
