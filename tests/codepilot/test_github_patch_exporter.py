from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from codepilot.github.patch_exporter import export_patch, export_patch_with_metadata, patch_is_empty, remove_protected_patch_content


def _init_git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    subprocess.run(["git", "config", "user.email", "demo@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Demo"], cwd=repo, check=True)
    return repo


def _commit_calc(repo: Path, content: str) -> None:
    (repo / "src").mkdir(parents=True, exist_ok=True)
    (repo / "src" / "calc.py").write_text(content, encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def test_export_patch_writes_diff_with_old_and_new_lines(tmp_path: Path) -> None:
    repo = _init_git_repo(tmp_path)
    _commit_calc(repo, "def add(a, b):\n    return a - b\n")
    (repo / "src" / "calc.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")

    patch_path = export_patch(repo, tmp_path / "runs" / "changes.patch")
    patch_text = patch_path.read_text(encoding="utf-8")

    assert "diff --git" in patch_text
    assert "-    return a - b" in patch_text
    assert "+    return a + b" in patch_text


def test_export_patch_empty_diff_still_creates_file(tmp_path: Path) -> None:
    repo = _init_git_repo(tmp_path)
    _commit_calc(repo, "def add(a, b):\n    return a - b\n")

    patch_path = export_patch(repo, tmp_path / "runs" / "changes.patch")

    assert patch_path.exists()
    assert patch_is_empty(patch_path) is True


def test_export_patch_rejects_non_git_repo(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()

    with pytest.raises(ValueError):
        export_patch(repo, tmp_path / "runs" / "changes.patch")


def test_export_patch_rejects_missing_repo(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        export_patch(tmp_path / "missing", tmp_path / "runs" / "changes.patch")


def test_export_patch_never_calls_commit_or_push(tmp_path: Path, monkeypatch) -> None:
    repo = _init_git_repo(tmp_path)
    _commit_calc(repo, "def add(a, b):\n    return a - b\n")
    (repo / "src" / "calc.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    calls: list[list[str]] = []
    original_run = subprocess.run

    def fake_run(args, **kwargs):
        calls.append(list(args))
        return original_run(args, **kwargs)

    monkeypatch.setattr("codepilot.repo.git_utils.subprocess.run", fake_run)

    export_patch(repo, tmp_path / "runs" / "changes.patch")

    assert ["git", "commit"] not in [item[:2] for item in calls]
    assert ["git", "push"] not in [item[:2] for item in calls]


def test_export_patch_with_metadata_returns_path_and_metadata(tmp_path: Path) -> None:
    repo = _init_git_repo(tmp_path)
    _commit_calc(repo, "def add(a, b):\n    return a - b\n")
    (repo / "src" / "calc.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")

    patch_path, metadata = export_patch_with_metadata(repo, tmp_path / "runs" / "changes.patch")

    assert patch_path.exists()
    assert "src/calc.py" in metadata.changed_files


def test_remove_protected_patch_content_drops_protected_diff_block(tmp_path: Path) -> None:
    patch_path = tmp_path / "changes.patch"
    patch_path.write_text(
        "diff --git a/.env b/.env\n--- /dev/null\n+++ b/.env\n@@ -0,0 +1 @@\n+SECRET=1\n"
        "diff --git a/src/demo.py b/src/demo.py\n--- a/src/demo.py\n+++ b/src/demo.py\n@@ -1 +1 @@\n-old\n+new\n",
        encoding="utf-8",
    )

    remove_protected_patch_content(patch_path, excluded_paths=[".env"])

    patch_text = patch_path.read_text(encoding="utf-8")
    assert ".env" not in patch_text
    assert "src/demo.py" in patch_text
