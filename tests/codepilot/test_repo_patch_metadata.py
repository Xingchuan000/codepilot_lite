from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from codepilot.github.patch_exporter import export_patch
from codepilot.repo.patch_metadata import compute_patch_metadata


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


def test_empty_patch_metadata(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    patch_path = export_patch(repo, tmp_path / "changes.patch")

    metadata = compute_patch_metadata(repo, patch_path)

    assert metadata.is_empty is True
    assert metadata.changed_files == []
    assert metadata.sha256 is not None


def test_changed_patch_metadata_contains_expected_fields(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    (repo / "src" / "calc.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    patch_path = export_patch(repo, tmp_path / "changes.patch")

    metadata = compute_patch_metadata(
        repo,
        patch_path,
        baseline_dirty=True,
        contains_preexisting_changes=True,
    )

    assert metadata.is_empty is False
    assert "src/calc.py" in metadata.changed_files
    assert metadata.size_bytes > 0
    assert metadata.diff_stat is not None and "src/calc.py" in metadata.diff_stat
    assert metadata.generated_from_repo == repo.resolve()
    assert metadata.baseline_dirty is True
    assert metadata.contains_preexisting_changes is True
    assert "@@" not in (metadata.diff_stat or "")


def test_patch_metadata_marks_protected_changed_files(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    (repo / ".env").write_text("SECRET=0\n", encoding="utf-8")
    subprocess.run(["git", "add", ".env"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "add env"], cwd=repo, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    (repo / ".env").write_text("SECRET=1\n", encoding="utf-8")
    patch_path = export_patch(repo, tmp_path / "changes.patch")

    metadata = compute_patch_metadata(repo, patch_path, protected_paths=[".env", ".env.*"])

    assert ".env" in metadata.protected_changed_files


def test_compute_patch_metadata_missing_patch_raises(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)

    with pytest.raises(FileNotFoundError):
        compute_patch_metadata(repo, tmp_path / "missing.patch")


def test_untracked_file_is_recorded_in_patch_metadata(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    (repo / "src" / "new_file.py").write_text("print('hi')\n", encoding="utf-8")
    patch_path = export_patch(repo, tmp_path / "changes.patch")

    metadata = compute_patch_metadata(repo, patch_path)

    assert "src/new_file.py" in metadata.untracked_files
    assert "src/new_file.py" in metadata.changed_files
    assert metadata.is_empty is False


def test_untracked_protected_file_is_recorded_as_protected_changed(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    (repo / ".env").write_text("SECRET=1\n", encoding="utf-8")
    patch_path = export_patch(repo, tmp_path / "changes.patch")

    metadata = compute_patch_metadata(repo, patch_path, protected_paths=[".env", ".env.*"])

    assert ".env" in metadata.untracked_files
    assert ".env" in metadata.protected_changed_files


def test_omitted_untracked_file_is_recorded(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    (repo / "large.bin").write_bytes(b"\x00" * 10)
    patch_path = export_patch(repo, tmp_path / "changes.patch")

    metadata = compute_patch_metadata(repo, patch_path)

    assert "large.bin" in metadata.untracked_files
    assert "large.bin" in metadata.untracked_files_omitted
