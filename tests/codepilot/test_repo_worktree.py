from __future__ import annotations

import subprocess
from pathlib import Path

from codepilot.repo.git_utils import get_head_sha, is_git_repo
from codepilot.repo.worktree import create_issue_worktree, remove_issue_worktree, sanitize_run_id_for_ref


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


def test_sanitize_run_id_for_ref_replaces_invalid_characters() -> None:
    assert sanitize_run_id_for_ref("issue 1/中文") == "issue-1"


def test_create_issue_worktree_creates_repo_and_preserves_head(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    base_dir = tmp_path / "worktrees"

    info = create_issue_worktree(repo, run_id="issue-1", base_dir=base_dir)

    assert info.worktree_path.exists()
    assert is_git_repo(info.worktree_path) is True
    assert get_head_sha(info.worktree_path) == get_head_sha(repo)


def test_worktree_changes_do_not_modify_original_worktree_file(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    info = create_issue_worktree(repo, run_id="issue-2", base_dir=tmp_path / "worktrees")
    (info.worktree_path / "src" / "calc.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")

    assert (repo / "src" / "calc.py").read_text(encoding="utf-8") == "def add(a, b):\n    return a - b\n"


def test_create_issue_worktree_rejects_existing_branch(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    create_issue_worktree(repo, run_id="issue-3", base_dir=tmp_path / "worktrees")

    try:
        create_issue_worktree(repo, run_id="issue-3", base_dir=tmp_path / "worktrees-2")
    except ValueError:
        assert True
    else:
        assert False


def test_create_issue_worktree_rejects_existing_path(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    base_dir = tmp_path / "worktrees"
    (base_dir / "issue-4").mkdir(parents=True)

    try:
        create_issue_worktree(repo, run_id="issue-4", base_dir=base_dir)
    except FileExistsError:
        assert True
    else:
        assert False


def test_create_issue_worktree_rejects_base_dir_inside_repo(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)

    try:
        create_issue_worktree(repo, run_id="issue-5", base_dir=repo / "nested")
    except ValueError:
        assert True
    else:
        assert False


def test_remove_issue_worktree_returns_success(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    info = create_issue_worktree(repo, run_id="issue-6", base_dir=tmp_path / "worktrees")

    result = remove_issue_worktree(info.worktree_path, original_repo=repo, branch_name=info.branch_name)

    assert result.success is True
    assert result.branch_left_in_place is True


def test_remove_issue_worktree_failure_returns_result_not_exception(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)

    result = remove_issue_worktree(tmp_path / "missing-worktree", original_repo=repo)

    assert result.success is None
