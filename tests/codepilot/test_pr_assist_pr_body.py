from __future__ import annotations

from pathlib import Path

import pytest

from codepilot.pr_assist.models import PRAssistSafetyGate
from codepilot.pr_assist.pr_body import build_pr_body_data, read_json_if_exists, render_pr_body


def test_render_pr_body_contains_required_sections(tmp_path: Path) -> None:
    run_dir = tmp_path / "runs" / "issue-test"
    run_dir.mkdir(parents=True)
    data = build_pr_body_data(
        manifest={
            "run_id": "issue-test",
            "used_worktree": False,
            "patch": {"changed_files": ["src/calc.py"], "sha256": "abc", "is_empty": False},
            "safety_summary": {"baseline_dirty": False},
        },
        report_json={
            "run_id": "issue-test",
            "final_summary": "Implemented fix.",
            "changed_files": ["src/ignored.py"],
            "tests": {"status": "passed", "command": "pytest -q", "summary": "1 passed"},
        },
        issue_json={
            "title": "Fix ```calc``` bug",
            "body": "demo",
            "ref": {"source": "file", "file_path": str(run_dir / "issue.md")},
        },
        pr_summary_text="# summary",
        run_dir=run_dir,
        artifacts={"report_json": run_dir / "report.json", "patch": run_dir / "changes.patch", "restore_plan": run_dir / "restore_plan.md"},
        safety_gate=PRAssistSafetyGate(status="pass"),
    )

    content = render_pr_body(data, safety_gate=PRAssistSafetyGate(status="pass"))

    assert "## Summary" in content
    assert "## Changes" in content
    assert "## Tests" in content
    assert "## Safety and Repo State" in content
    assert "## Evidence" in content
    assert "`src/calc.py`" in content
    assert "`\u200b``" in content
    assert "diff --git" not in content
    assert str(tmp_path) not in content


def test_render_pr_body_unknown_tests_and_empty_patch() -> None:
    data = build_pr_body_data(
        manifest={
            "run_id": "issue-test",
            "used_worktree": False,
            "patch": {"changed_files": [], "sha256": "abc", "is_empty": True},
            "safety_summary": {"baseline_dirty": False},
        },
        report_json=None,
        issue_json=None,
        pr_summary_text="summary",
        run_dir="runs/issue-test",
        artifacts={},
        safety_gate=PRAssistSafetyGate(status="fail"),
    )

    content = render_pr_body(data, safety_gate=PRAssistSafetyGate(status="fail"))

    assert "> [!WARNING]" in content
    assert "- unknown" in content
    assert "No code changes were generated." in content
    assert "passed" not in content


def test_read_json_if_exists_returns_none_for_missing(tmp_path: Path) -> None:
    assert read_json_if_exists(tmp_path / "missing.json") is None


def test_read_json_if_exists_rejects_invalid_json(tmp_path: Path) -> None:
    path = tmp_path / "broken.json"
    path.write_text("{broken", encoding="utf-8")

    with pytest.raises(ValueError, match="Invalid JSON artifact"):
        read_json_if_exists(path)


def test_read_json_if_exists_rejects_non_object(tmp_path: Path) -> None:
    path = tmp_path / "list.json"
    path.write_text("[]", encoding="utf-8")

    with pytest.raises(ValueError, match="must contain an object"):
        read_json_if_exists(path)
