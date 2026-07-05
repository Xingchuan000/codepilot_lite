from __future__ import annotations

from pathlib import Path

from codepilot.repo.models import CleanupResult, PatchMetadata
from codepilot.repo.restore import render_restore_plan, write_restore_plan


def test_restore_plan_contains_expected_sections(tmp_path: Path) -> None:
    metadata = PatchMetadata(
        patch_path=tmp_path / "changes.patch",
        is_empty=False,
        size_bytes=1,
        sha256="hash",
        changed_files=["src/calc.py"],
    )
    content = render_restore_plan(
        run_id="issue-1",
        repo_path=tmp_path / "repo",
        effective_repo_path=tmp_path / "repo-worktree",
        used_worktree=True,
        worktree_path=tmp_path / "repo-worktree",
        baseline_dirty=True,
        patch_metadata=metadata,
        cleanup_result=CleanupResult(requested=True, attempted=True, success=False, reason="busy"),
    )

    assert "issue-1" in content
    assert "Original repo" in content
    assert "Effective repo" in content
    assert "git worktree remove" in content
    assert "Cleanup requested" in content
    assert "Do not use commands that overwrite pre-existing user changes." in content
    assert "git reset --hard" not in content
    assert "git clean -fd" not in content
    assert "git checkout -- ." not in content
    assert "src/calc.py" in content


def test_write_restore_plan_writes_utf8_file(tmp_path: Path) -> None:
    metadata = PatchMetadata(
        patch_path=tmp_path / "changes.patch",
        is_empty=True,
        size_bytes=0,
        sha256="hash",
        changed_files=[],
    )

    path = write_restore_plan(
        run_id="issue-1",
        repo_path=tmp_path / "repo",
        effective_repo_path=tmp_path / "repo",
        used_worktree=False,
        worktree_path=None,
        baseline_dirty=False,
        patch_metadata=metadata,
        output_path=tmp_path / "restore_plan.md",
    )

    assert path.read_text(encoding="utf-8")


def test_restore_plan_redacts_absolute_paths_and_mentions_protected_after_files(tmp_path: Path) -> None:
    metadata = PatchMetadata(
        patch_path=tmp_path / "changes.patch",
        is_empty=False,
        size_bytes=1,
        sha256="hash",
        changed_files=["src/calc.py"],
        protected_after_files=[".env"],
    )

    content = render_restore_plan(
        run_id="issue-1",
        repo_path=tmp_path / "repo",
        effective_repo_path=tmp_path / "repo",
        used_worktree=False,
        worktree_path=None,
        baseline_dirty=False,
        patch_metadata=metadata,
        redact_absolute_paths=True,
    )

    assert "[REDACTED_PATH]" in content
    assert str(tmp_path) not in content
    assert "Protected dirty files were detected after the run." in content
