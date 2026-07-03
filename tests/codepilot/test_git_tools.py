import subprocess
from pathlib import Path

from codepilot.tools.git_tools import git_diff, git_status


def _init_git_repo(tmp_path: Path) -> Path:
    subprocess.run(["git", "init"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "demo@example.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Demo"], cwd=tmp_path, check=True)
    return tmp_path


def _commit_file(repo: Path, path: str, content: str) -> None:
    file_path = repo / path
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(content, encoding="utf-8")
    subprocess.run(["git", "add", path], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True)


def test_git_status_clean_repo(tmp_path: Path) -> None:
    repo = _init_git_repo(tmp_path)
    _commit_file(repo, "src/demo.py", "print('ok')\n")

    result = git_status(repo)

    assert result.success is True
    assert result.metadata["clean"] is True


def test_git_status_modified_file(tmp_path: Path) -> None:
    repo = _init_git_repo(tmp_path)
    _commit_file(repo, "src/demo.py", "print('ok')\n")
    (repo / "src" / "demo.py").write_text("print('new')\n", encoding="utf-8")

    result = git_status(repo)

    assert "src/demo.py" in result.metadata["changed_files"]
    assert "src/demo.py" in result.metadata["unstaged_files"]


def test_git_status_untracked_file(tmp_path: Path) -> None:
    repo = _init_git_repo(tmp_path)
    (repo / "new.py").write_text("print('x')\n", encoding="utf-8")

    result = git_status(repo)

    assert "new.py" in result.metadata["untracked_files"]


def test_git_status_staged_file(tmp_path: Path) -> None:
    repo = _init_git_repo(tmp_path)
    _commit_file(repo, "src/demo.py", "print('ok')\n")
    (repo / "src" / "demo.py").write_text("print('new')\n", encoding="utf-8")
    subprocess.run(["git", "add", "src/demo.py"], cwd=repo, check=True)

    result = git_status(repo)

    assert "src/demo.py" in result.metadata["staged_files"]


def test_git_status_deleted_file(tmp_path: Path) -> None:
    repo = _init_git_repo(tmp_path)
    _commit_file(repo, "src/demo.py", "print('ok')\n")
    (repo / "src" / "demo.py").unlink()

    result = git_status(repo)

    assert "src/demo.py" in result.metadata["deleted_files"]


def test_git_status_not_git_repo_returns_failure(tmp_path: Path) -> None:
    assert git_status(tmp_path).success is False


def test_git_status_missing_repo_returns_failure(tmp_path: Path) -> None:
    assert git_status(tmp_path / "missing").success is False


def test_git_status_max_entries_truncates(tmp_path: Path) -> None:
    repo = _init_git_repo(tmp_path)
    for i in range(3):
        (repo / f"file_{i}.txt").write_text("x\n", encoding="utf-8")

    result = git_status(repo, max_entries=2)

    assert result.metadata["truncated"] is True


def test_git_diff_summary_mode_returns_name_status_and_stat(tmp_path: Path) -> None:
    repo = _init_git_repo(tmp_path)
    _commit_file(repo, "src/demo.py", "old\n")
    (repo / "src" / "demo.py").write_text("new\n", encoding="utf-8")

    result = git_diff(repo)

    assert "Changed files:" in result.output
    assert "Diff stat:" in result.output


def test_git_diff_content_requires_path(tmp_path: Path) -> None:
    result = git_diff(_init_git_repo(tmp_path), include_content=True)

    assert result.success is False


def test_git_diff_content_for_safe_path(tmp_path: Path) -> None:
    repo = _init_git_repo(tmp_path)
    _commit_file(repo, "src/demo.py", "old\n")
    (repo / "src" / "demo.py").write_text("new\n", encoding="utf-8")

    result = git_diff(repo, path="src/demo.py", include_content=True)

    assert "-old" in result.output
    assert "+new" in result.output


def test_git_diff_staged_content(tmp_path: Path) -> None:
    repo = _init_git_repo(tmp_path)
    _commit_file(repo, "src/demo.py", "old\n")
    (repo / "src" / "demo.py").write_text("new\n", encoding="utf-8")
    subprocess.run(["git", "add", "src/demo.py"], cwd=repo, check=True)

    result = git_diff(repo, path="src/demo.py", include_content=True, staged=True)

    assert "+new" in result.output


def test_git_diff_max_lines_truncates(tmp_path: Path) -> None:
    repo = _init_git_repo(tmp_path)
    _commit_file(repo, "src/demo.py", "\n".join(f"old{i}" for i in range(20)) + "\n")
    (repo / "src" / "demo.py").write_text("\n".join(f"new{i}" for i in range(20)) + "\n", encoding="utf-8")

    result = git_diff(repo, path="src/demo.py", include_content=True, max_lines=5)

    assert result.metadata["line_truncated"] is True


def test_git_diff_max_chars_truncates(tmp_path: Path) -> None:
    repo = _init_git_repo(tmp_path)
    _commit_file(repo, "src/demo.py", "old\n")
    (repo / "src" / "demo.py").write_text("new-" + ("x" * 5000) + "\n", encoding="utf-8")

    result = git_diff(repo, path="src/demo.py", include_content=True, max_chars=50)

    assert result.metadata["char_truncated"] is True


def test_git_diff_not_git_repo_returns_failure(tmp_path: Path) -> None:
    assert git_diff(tmp_path).success is False


def test_git_diff_secret_like_content_is_redacted(tmp_path: Path) -> None:
    repo = _init_git_repo(tmp_path)
    _commit_file(repo, "src/config.py", "API_KEY=old\n")
    (repo / "src" / "config.py").write_text("API_KEY=sk-abcdefghijklmnopqrstuvwxyz123456\n", encoding="utf-8")

    result = git_diff(repo, path="src/config.py", include_content=True)

    assert "sk-abcdefghijklmnopqrstuvwxyz123456" not in result.output
    assert "[REDACTED]" in result.output
    assert result.metadata["has_secret_like_content"] is True


def test_git_diff_binary_diff_sets_metadata(tmp_path: Path) -> None:
    repo = _init_git_repo(tmp_path)
    file_path = repo / "image.bin"
    file_path.write_bytes(b"\x00\x01\x02")
    subprocess.run(["git", "add", "image.bin"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True)
    file_path.write_bytes(b"\x00\x01\x03")

    result = git_diff(repo, path="image.bin", include_content=True)

    assert isinstance(result.metadata["binary_diff"], bool)


def test_git_diff_large_generated_file_marked(tmp_path: Path) -> None:
    repo = _init_git_repo(tmp_path)
    _commit_file(repo, "package-lock.json", '{"a":1}\n')
    (repo / "package-lock.json").write_text('{"a":2}\n', encoding="utf-8")

    result = git_diff(repo, path="package-lock.json", include_content=True)

    assert "package-lock.json" in result.metadata["large_or_generated_files"]
