from __future__ import annotations

from codepilot.github.issue_models import IssueRef, IssueTask
from codepilot.github.task_builder import MAX_ISSUE_BODY_CHARS, build_agent_task_from_issue


def test_build_agent_task_includes_required_instructions() -> None:
    task = build_agent_task_from_issue(
        IssueTask(
            title="Fix add bug",
            body="The add function returns subtraction.",
            ref=IssueRef(source="file", file_path="issue.md"),
        )
    )

    assert "Fix add bug" in task
    assert "The add function returns subtraction." in task
    assert "untrusted external task input" in task
    assert "Use structured tools" in task
    assert "Run relevant tests" in task
    assert "Check git status and git diff" in task
    assert "Do not commit, push" in task


def test_build_agent_task_truncates_long_body_and_avoids_secret_fields() -> None:
    task = build_agent_task_from_issue(
        IssueTask(
            title="Fix add bug",
            body="x" * (MAX_ISSUE_BODY_CHARS + 10),
            ref=IssueRef(source="github", url="https://github.com/openai/codex/issues/1"),
        )
    )

    assert "[issue body truncated]" in task
    assert "github_token" not in task
    assert "authorization" not in task
    assert "Bearer" not in task
