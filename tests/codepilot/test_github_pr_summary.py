from __future__ import annotations

from pathlib import Path

from codepilot.github.issue_models import IssueRef, IssueTask
from codepilot.github.pr_summary import render_pr_summary, write_pr_summary
from codepilot.repo.models import PatchMetadata


def _issue() -> IssueTask:
    return IssueTask(
        title="Fix add bug",
        body="body",
        ref=IssueRef(source="github", url="https://github.com/openai/codex/issues/1"),
    )


def test_render_pr_summary_contains_required_sections() -> None:
    content = render_pr_summary(
        _issue(),
        {
            "run_id": "issue-1",
            "final_summary": "Implemented the fix.",
            "changed_files": ["src/calc.py"],
            "tests": {"status": "passed", "command": "python -m pytest -q", "summary": "1 passed"},
            "policy": {"violations": []},
        },
        patch_path="changes.patch",
        report_path="report.md",
    )

    assert "# PR Summary" in content
    assert "https://github.com/openai/codex/issues/1" in content
    assert "Implemented the fix." in content
    assert "- src/calc.py" in content
    assert "- Status: passed" in content
    assert "- Command: `python -m pytest -q`" in content
    assert "- Report: `report.md`" in content
    assert "- Patch: `changes.patch`" in content
    assert "No commit was created automatically." in content
    assert "No push was performed." in content
    assert "No pull request was created automatically." in content


def test_render_pr_summary_handles_unknown_tests_and_no_changed_files() -> None:
    content = render_pr_summary(
        _issue(),
        {"run_id": "issue-1", "status": "partial", "changed_files": [], "tests": {}, "policy": {"violations": []}},
    )

    assert "- Status: unknown" in content
    assert "- No changed files reported." in content


def test_render_pr_summary_adds_notes_when_policy_violations_exist() -> None:
    content = render_pr_summary(
        _issue(),
        {
            "run_id": "issue-1",
            "status": "partial",
            "tests": {},
            "policy": {"violations": [{"decision": "deny", "reason": "read only"}]},
        },
    )

    assert "## Notes" in content


def test_write_pr_summary_creates_parent_dir_and_writes_file(tmp_path: Path) -> None:
    output_path = tmp_path / "nested" / "pr_summary.md"

    path = write_pr_summary(
        _issue(),
        {"run_id": "issue-1", "status": "success", "tests": {}, "policy": {"violations": []}},
        output_path,
    )

    assert path == output_path
    assert output_path.exists()


def test_render_pr_summary_with_patch_metadata_contains_new_sections() -> None:
    content = render_pr_summary(
        _issue(),
        {"run_id": "issue-1", "status": "success", "tests": {}, "policy": {"violations": []}},
        manifest_path="artifact_manifest.json",
        restore_plan_path="restore_plan.md",
        used_worktree=True,
        baseline_dirty=True,
        contains_preexisting_changes=True,
        patch_metadata=PatchMetadata(
            patch_path=Path("changes.patch"),
            is_empty=False,
            size_bytes=12,
            sha256="hash",
            changed_files=["src/calc.py"],
            generated_from_repo=Path("/tmp/repo"),
        ),
    )

    assert "## Patch Metadata" in content
    assert "artifact_manifest.json" in content
    assert "restore_plan.md" in content
    assert "may include changes that existed before CodePilot started" in content
    assert "isolated worktree" in content
