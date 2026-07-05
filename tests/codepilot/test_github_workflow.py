from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from codepilot.github.issue_loader import load_issue_from_file, parse_github_issue_url
from codepilot.github.issue_models import IssueRef, IssueTask
from codepilot.github.patch_exporter import export_patch, patch_is_empty
from codepilot.github.pr_summary import render_pr_summary
from codepilot.github.task_builder import MAX_ISSUE_BODY_CHARS, build_agent_task_from_issue
from codepilot.github.workflow import run_issue_workflow


def _write_bug_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "tests").mkdir()
    (repo / "src" / "__init__.py").write_text("", encoding="utf-8")
    (repo / "src" / "calc.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
    (repo / "tests" / "test_calc.py").write_text(
        "from src.calc import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "init"], cwd=repo, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    subprocess.run(["git", "config", "user.email", "demo@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Demo"], cwd=repo, check=True)
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return repo


def test_load_issue_from_file_extracts_title_and_body(tmp_path: Path) -> None:
    path = tmp_path / "issue.md"
    path.write_text("# Demo bug\n\nBody line 1\n\n## Details\nBody line 2\n", encoding="utf-8")

    issue = load_issue_from_file(path)

    assert issue.title == "Demo bug"
    assert issue.body == "Body line 1\n\n## Details\nBody line 2"
    assert issue.ref.source == "file"
    assert issue.ref.file_path == str(path)
    assert issue.metadata == {"format": "markdown"}


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://github.com/openai/codex/issues/123", ("openai", "codex", 123)),
        ("http://github.com/openai/codex/issues/123", ("openai", "codex", 123)),
        ("github.com/openai/codex/issues/123", ("openai", "codex", 123)),
    ],
)
def test_parse_github_issue_url_accepts_expected_formats(url: str, expected: tuple[str, str, int]) -> None:
    ref = parse_github_issue_url(url)

    assert (ref.owner, ref.repo, ref.number) == expected
    assert ref.source == "github"
    assert ref.url == url


@pytest.mark.parametrize(
    ("url",),
    [
        ("https://github.com/openai/codex/pull/123",),
        ("https://github.com/openai/codex/commit/abcdef",),
        ("https://example.com/openai/codex/issues/123",),
        ("https://github.com/openai/codex/issues/",),
    ],
)
def test_parse_github_issue_url_rejects_non_issue_urls(url: str) -> None:
    with pytest.raises(ValueError):
        parse_github_issue_url(url)


def test_build_agent_task_from_issue_truncates_body() -> None:
    issue = IssueTask(
        title="Demo",
        body="x" * (MAX_ISSUE_BODY_CHARS + 5),
        ref=IssueRef(source="file", file_path="issue.md"),
    )

    task = build_agent_task_from_issue(issue)

    assert "[issue body truncated]" in task
    assert "Issue source:\nissue.md" in task


def test_export_patch_and_patch_is_empty(tmp_path: Path) -> None:
    repo = _write_bug_repo(tmp_path)
    patch_path = tmp_path / "runs" / "changes.patch"

    empty_patch = export_patch(repo, patch_path)

    assert empty_patch == patch_path
    assert patch_is_empty(patch_path) is True

    (repo / "src" / "calc.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    export_patch(repo, patch_path)

    assert patch_is_empty(patch_path) is False
    assert "diff --git" in patch_path.read_text(encoding="utf-8")


def test_render_pr_summary_uses_report_dict() -> None:
    issue = IssueTask(
        title="Fix add bug",
        body="body",
        ref=IssueRef(source="github", url="https://github.com/openai/codex/issues/1"),
    )

    summary = render_pr_summary(
        issue,
        {
            "run_id": "issue-test",
            "status": "success",
            "final_summary": "Implemented the fix.",
            "changed_files": ["src/calc.py"],
            "tests": {"status": "passed", "command": "python -m pytest -q", "summary": "1 passed"},
            "policy": {"violations": []},
        },
        patch_path="changes.patch",
        report_path="report.md",
    )

    assert "Fixes: https://github.com/openai/codex/issues/1" in summary
    assert "- src/calc.py" in summary
    assert "- Status: passed" in summary
    assert "- Report: `report.md`" in summary


def test_run_issue_workflow_from_file_creates_expected_artifacts(tmp_path: Path) -> None:
    repo = _write_bug_repo(tmp_path)
    issue_file = tmp_path / "issue.md"
    issue_file.write_text(
        "# Add function returns wrong result\n\nThe `add(a, b)` function returns subtraction.\n",
        encoding="utf-8",
    )
    fixture = Path("tests/codepilot/fixtures/agent_actions_success.jsonl").resolve()

    result = run_issue_workflow(
        issue_file=issue_file,
        repo=repo,
        run_id="issue-test",
        runs_dir=tmp_path / "runs",
        approve=True,
        fake_actions=fixture,
        overwrite=True,
    )

    assert result.success is True
    assert result.status == "success"
    assert result.issue_json_path.exists()
    assert result.trace_path.exists()
    assert result.report_path is not None and result.report_path.exists()
    assert result.report_json_path is not None and result.report_json_path.exists()
    assert result.patch_path is not None and result.patch_path.exists()
    assert result.pr_summary_path is not None and result.pr_summary_path.exists()
    assert result.manifest_path is not None and result.manifest_path.exists()
    assert result.restore_plan_path is not None and result.restore_plan_path.exists()
    assert json.loads(result.issue_json_path.read_text(encoding="utf-8"))["title"] == "Add function returns wrong result"
    assert "-    return a - b" in result.patch_path.read_text(encoding="utf-8")
    assert "+    return a + b" in result.patch_path.read_text(encoding="utf-8")
    pr_summary = result.pr_summary_path.read_text(encoding="utf-8")
    assert "## Issue" in pr_summary
    assert "## Summary" in pr_summary
    assert "## Tests" in pr_summary
    assert "## Evidence" in pr_summary
    assert "## Safety" in pr_summary


def test_run_issue_workflow_keeps_exporting_artifacts_when_agent_fails(tmp_path: Path) -> None:
    repo = _write_bug_repo(tmp_path)
    issue_file = tmp_path / "issue.md"
    issue_file.write_text("# Add bug\n\nPlease investigate.\n", encoding="utf-8")
    fake_actions = tmp_path / "partial.jsonl"
    fake_actions.write_text('{"type":"finish","status":"partial","summary":"not fixed"}\n', encoding="utf-8")

    result = run_issue_workflow(
        issue_file=issue_file,
        repo=repo,
        run_id="issue-partial",
        runs_dir=tmp_path / "runs",
        fake_actions=fake_actions,
        overwrite=True,
    )

    assert result.success is False
    assert result.report_path is not None and result.report_path.exists()
    assert result.patch_path is not None and result.patch_path.exists()
    assert result.pr_summary_path is not None and result.pr_summary_path.exists()
    assert result.manifest_path is not None and result.manifest_path.exists()
    assert result.restore_plan_path is not None and result.restore_plan_path.exists()


def test_run_issue_workflow_requires_exactly_one_issue_source(tmp_path: Path) -> None:
    repo = _write_bug_repo(tmp_path)
    issue_file = tmp_path / "issue.md"
    issue_file.write_text("# Add bug\n\nPlease fix.\n", encoding="utf-8")

    with pytest.raises(ValueError):
        run_issue_workflow(repo=repo)
    with pytest.raises(ValueError):
        run_issue_workflow(issue_file=issue_file, issue_url="https://github.com/openai/codex/issues/1", repo=repo)


def test_run_issue_workflow_rejects_existing_artifacts_without_overwrite(tmp_path: Path) -> None:
    repo = _write_bug_repo(tmp_path)
    issue_file = tmp_path / "issue.md"
    issue_file.write_text("# Add bug\n\nPlease fix.\n", encoding="utf-8")
    run_dir = tmp_path / "runs" / "issue-test"
    run_dir.mkdir(parents=True)
    (run_dir / "issue.json").write_text("exists", encoding="utf-8")

    with pytest.raises(FileExistsError):
        run_issue_workflow(
            issue_file=issue_file,
            repo=repo,
            run_id="issue-test",
            runs_dir=tmp_path / "runs",
        )


def test_run_issue_workflow_overwrite_true_allows_rerun_same_run_id(tmp_path: Path) -> None:
    repo = _write_bug_repo(tmp_path)
    issue_file = tmp_path / "issue.md"
    issue_file.write_text("# Add bug\n\nPlease fix.\n", encoding="utf-8")
    fixture = Path("tests/codepilot/fixtures/agent_actions_success.jsonl").resolve()

    first = run_issue_workflow(
        issue_file=issue_file,
        repo=repo,
        run_id="issue-test",
        runs_dir=tmp_path / "runs",
        approve=True,
        fake_actions=fixture,
        overwrite=True,
    )
    second = run_issue_workflow(
        issue_file=issue_file,
        repo=repo,
        run_id="issue-test",
        runs_dir=tmp_path / "runs",
        approve=True,
        fake_actions=fixture,
        dirty_policy="warn",
        overwrite=True,
    )

    assert first.run_id == second.run_id == "issue-test"
    assert second.report_path is not None and second.report_path.exists()


def test_run_issue_workflow_read_only_still_writes_artifacts(tmp_path: Path) -> None:
    repo = _write_bug_repo(tmp_path)
    issue_file = tmp_path / "issue.md"
    issue_file.write_text("# Add bug\n\nPlease fix.\n", encoding="utf-8")
    fixture = Path("tests/codepilot/fixtures/agent_actions_success.jsonl").resolve()

    result = run_issue_workflow(
        issue_file=issue_file,
        repo=repo,
        run_id="issue-read-only",
        runs_dir=tmp_path / "runs",
        fake_actions=fixture,
        policy_mode="read_only",
        overwrite=True,
    )

    assert result.success is False
    assert (repo / "src" / "calc.py").read_text(encoding="utf-8") == "def add(a, b):\n    return a - b\n"
    assert result.report_path is not None and result.report_path.exists()
    assert result.patch_path is not None and result.patch_path.exists()
    assert result.pr_summary_path is not None and result.pr_summary_path.exists()
