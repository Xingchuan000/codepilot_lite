from __future__ import annotations

from pathlib import Path

import pytest

from codepilot.pr_feedback.github_action import render_pr_feedback_workflow_template, write_pr_feedback_workflow_template


def test_pr_feedback_github_action_workflow_dispatch_only() -> None:
    text = render_pr_feedback_workflow_template()

    assert "workflow_dispatch" in text
    assert "pull_request_target" not in text
    assert "issue_comment" not in text
    assert "permissions: {}" in text


def test_pr_feedback_github_action_feedback_plan_read_only() -> None:
    text = render_pr_feedback_workflow_template()

    assert "feedback-plan" in text
    assert "contents: read" in text
    assert "pull-requests: read" in text
    assert "checks: read" in text
    assert "actions: read" in text


def test_pr_feedback_github_action_execute_update_write_only() -> None:
    text = render_pr_feedback_workflow_template()

    assert "execute-update" in text
    assert "contents: write" in text
    assert "pull-requests: write" in text


def test_pr_feedback_github_action_upload_artifact_whitelist() -> None:
    text = render_pr_feedback_workflow_template()

    assert "ci_status.json" in text
    assert "review_feedback.json" in text
    assert "ci_feedback_report.md" in text
    assert "followup_task.md" in text
    assert "pr_update_plan.md" in text
    assert "ci_feedback_manifest.json" in text
    assert "pr_feedback_workflow.yml" in text
    assert "ci_logs/*.summary.md" in text


def test_write_pr_feedback_workflow_template_writes_file(tmp_path: Path) -> None:
    path = write_pr_feedback_workflow_template(tmp_path / "workflow.yml")

    assert path.exists()


def test_write_pr_feedback_workflow_template_honors_overwrite(tmp_path: Path) -> None:
    path = tmp_path / "workflow.yml"
    path.write_text("old\n", encoding="utf-8")

    with pytest.raises(FileExistsError):
        write_pr_feedback_workflow_template(path, overwrite=False)
