from __future__ import annotations

import subprocess
from pathlib import Path

from codepilot.repo.models import RepoSafetyConfig
from codepilot.repo.safety import check_repo_safety


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    subprocess.run(["git", "config", "user.email", "demo@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Demo"], cwd=repo, check=True)
    (repo / "src").mkdir()
    (repo / "src" / "calc.py").write_text("print('x')\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return repo


def test_clean_repo_fail_policy_allows_with_tmp_path(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)

    result = check_repo_safety(repo, config=RepoSafetyConfig(dirty_policy="fail", worktree_mode="off"))

    assert result.decision == "allow"
    assert result.warnings == []


def test_dirty_repo_fail_policy_denies(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    (repo / "src" / "calc.py").write_text("print('y')\n", encoding="utf-8")

    result = check_repo_safety(repo, config=RepoSafetyConfig(dirty_policy="fail", worktree_mode="off"))

    assert result.decision == "deny"


def test_dirty_repo_warn_policy_warns_and_marks_preexisting_changes(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    (repo / "src" / "calc.py").write_text("print('y')\n", encoding="utf-8")

    result = check_repo_safety(repo, config=RepoSafetyConfig(dirty_policy="warn", worktree_mode="off"))

    assert result.decision == "warn"
    assert result.contains_preexisting_changes is True
    assert result.warnings and result.warnings[0].startswith("Dirty files:")


def test_dirty_repo_allow_policy_allows_and_marks_baseline_dirty(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    (repo / "src" / "calc.py").write_text("print('y')\n", encoding="utf-8")

    result = check_repo_safety(repo, config=RepoSafetyConfig(dirty_policy="allow", worktree_mode="off"))

    assert result.decision == "allow"
    assert result.baseline_dirty is True


def test_non_git_repo_denies(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()

    assert check_repo_safety(repo).decision == "deny"


def test_missing_repo_denies(tmp_path: Path) -> None:
    assert check_repo_safety(tmp_path / "missing").decision == "deny"


def test_dirty_env_denies_even_with_warn_policy(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    (repo / ".env").write_text("SECRET=1\n", encoding="utf-8")

    result = check_repo_safety(repo, config=RepoSafetyConfig(dirty_policy="warn", worktree_mode="off"))

    assert result.decision == "deny"


def test_dirty_github_workflow_denies(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    (repo / ".github" / "workflows").mkdir(parents=True)
    (repo / ".github" / "workflows" / "test.yml").write_text("name: test\n", encoding="utf-8")

    result = check_repo_safety(repo, config=RepoSafetyConfig(dirty_policy="allow", worktree_mode="off"))

    assert result.decision == "deny"


def test_dirty_repo_worktree_warns_when_clean_source_not_required(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    (repo / "src" / "calc.py").write_text("print('y')\n", encoding="utf-8")

    result = check_repo_safety(
        repo,
        config=RepoSafetyConfig(
            dirty_policy="fail",
            worktree_mode="create",
            require_clean_source_for_worktree=False,
        ),
    )

    assert result.decision == "warn"
    assert result.contains_preexisting_changes is False


def test_dirty_repo_worktree_denies_when_clean_source_required(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    (repo / "src" / "calc.py").write_text("print('y')\n", encoding="utf-8")

    result = check_repo_safety(
        repo,
        config=RepoSafetyConfig(
            dirty_policy="fail",
            worktree_mode="create",
            require_clean_source_for_worktree=True,
        ),
    )

    assert result.decision == "deny"


def test_safety_result_before_includes_git_metadata(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    (repo / "src" / "calc.py").write_text("print('y')\n", encoding="utf-8")

    result = check_repo_safety(repo, config=RepoSafetyConfig(dirty_policy="warn", worktree_mode="off"))

    assert result.before is not None
    assert result.before.head_sha is not None
    assert result.before.branch in {"main", "master", None}
    assert result.before.files


def test_protected_path_cannot_be_allowed_by_dirty_policy_allow(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    (repo / ".env").write_text("SECRET=1\n", encoding="utf-8")

    assert check_repo_safety(repo, config=RepoSafetyConfig(dirty_policy="allow", worktree_mode="off")).decision == "deny"
