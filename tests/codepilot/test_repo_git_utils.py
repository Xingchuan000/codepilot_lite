from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from codepilot.repo.git_utils import GitCommandError, get_current_branch, get_git_root, get_head_sha, get_porcelain_status, is_git_repo, run_git


def _init_git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    subprocess.run(["git", "config", "user.email", "demo@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Demo"], cwd=repo, check=True)
    return repo


def _commit_file(repo: Path, rel: str, content: str) -> None:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    subprocess.run(["git", "add", rel], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", f"add {rel}"], cwd=repo, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def test_is_git_repo_for_git_repo(tmp_path: Path) -> None:
    repo = _init_git_repo(tmp_path)

    assert is_git_repo(repo) is True


def test_is_git_repo_for_non_git_repo(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()

    assert is_git_repo(repo) is False


def test_get_git_root_returns_repo_root(tmp_path: Path) -> None:
    repo = _init_git_repo(tmp_path)

    assert get_git_root(repo) == repo.resolve()


def test_get_head_sha_returns_commit_sha(tmp_path: Path) -> None:
    repo = _init_git_repo(tmp_path)
    _commit_file(repo, "src/calc.py", "print('x')\n")

    assert len(get_head_sha(repo) or "") == 40


def test_get_current_branch_returns_branch_or_none(tmp_path: Path) -> None:
    repo = _init_git_repo(tmp_path)
    _commit_file(repo, "src/calc.py", "print('x')\n")

    assert get_current_branch(repo) in {"main", "master", None}


def test_get_porcelain_status_recognizes_modified_file(tmp_path: Path) -> None:
    repo = _init_git_repo(tmp_path)
    _commit_file(repo, "src/calc.py", "print('x')\n")
    (repo / "src" / "calc.py").write_text("print('y')\n", encoding="utf-8")

    assert get_porcelain_status(repo)[0].unstaged is True


def test_get_porcelain_status_recognizes_untracked_file(tmp_path: Path) -> None:
    repo = _init_git_repo(tmp_path)
    (repo / "new.txt").write_text("hello\n", encoding="utf-8")

    status = get_porcelain_status(repo)[0]
    assert status.path == "new.txt"
    assert status.untracked is True


def test_get_porcelain_status_recognizes_staged_file(tmp_path: Path) -> None:
    repo = _init_git_repo(tmp_path)
    (repo / "src").mkdir()
    (repo / "src" / "calc.py").write_text("print('x')\n", encoding="utf-8")
    subprocess.run(["git", "add", "src/calc.py"], cwd=repo, check=True)

    assert get_porcelain_status(repo)[0].staged is True


def test_run_git_rejects_non_list_args(tmp_path: Path) -> None:
    repo = _init_git_repo(tmp_path)

    with pytest.raises(TypeError):
        run_git(repo, "status")  # type: ignore[arg-type]


def test_run_git_raises_git_command_error_on_failure(tmp_path: Path) -> None:
    repo = _init_git_repo(tmp_path)

    with pytest.raises(GitCommandError):
        run_git(repo, ["missing-subcommand"])


def test_run_git_uses_shell_false(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _init_git_repo(tmp_path)
    captured: dict[str, object] = {}

    def fake_run(args, **kwargs):
        captured["shell"] = kwargs["shell"]
        return subprocess.CompletedProcess(args, 0, stdout="ok\n", stderr="")

    monkeypatch.setattr("codepilot.repo.git_utils.subprocess.run", fake_run)

    assert run_git(repo, ["status"]) == "ok"
    assert captured["shell"] is False


def test_run_git_cleans_askpass_related_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _init_git_repo(tmp_path)
    captured: dict[str, object] = {}

    def fake_run(args, **kwargs):
        captured["env"] = kwargs["env"]
        return subprocess.CompletedProcess(args, 0, stdout="ok\n", stderr="")

    monkeypatch.setenv("GIT_ASKPASS", "x")
    monkeypatch.setenv("SSH_ASKPASS", "y")
    monkeypatch.setenv("GIT_SSH_COMMAND", "z")
    monkeypatch.setattr("codepilot.repo.git_utils.subprocess.run", fake_run)

    run_git(repo, ["status"])

    env = captured["env"]
    assert isinstance(env, dict)
    assert "GIT_ASKPASS" not in env
    assert "SSH_ASKPASS" not in env
    assert "GIT_SSH_COMMAND" not in env
